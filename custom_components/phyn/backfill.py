"""Device-owned, user-requested history backfills."""
from __future__ import annotations

from asyncio import CancelledError, Lock, Task
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError
from aiophyn.errors import AuthenticationError, RequestError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER
from .entities.base import PhynEntity

if TYPE_CHECKING:
    from .devices.pp import PhynPlusDevice

BACKFILL_DEFAULT_DAYS = 7
BACKFILL_MAX_DAYS = 365
BACKFILL_STATES = ["idle", "running", "completed", "failed", "interrupted"]


class PhynHistoryBackfill:
    """Own a backfill task and its local settings/outcome, not usage evidence."""

    def __init__(self, device: PhynPlusDevice) -> None:
        self.device = device
        self.hass = device.coordinator.hass
        self.signal = f"{DOMAIN}_history_backfill_{device.id}"
        self.data: dict[str, Any] = {
            "days": BACKFILL_DEFAULT_DAYS,
            "status": "idle",
        }
        self._store = Store[dict[str, Any]](
            self.hass, 1, f"{DOMAIN}_history_backfill_{device.id}"
        )
        self._initialize_lock = Lock()
        self._state_lock = Lock()
        self._start_lock = Lock()
        self._initialized = False
        self._task: Task[None] | None = None
        self.stopping = False

    @property
    def running(self) -> bool:
        return self._start_lock.locked() or (
            self._task is not None and not self._task.done()
        )

    async def async_initialize(self) -> None:
        async with self._initialize_lock:
            if self._initialized:
                return
            stored = await self._store.async_load()
            if stored is not None:
                if (
                    not isinstance(stored, dict)
                    or type(stored.get("days")) is not int
                    or not 1 <= stored["days"] <= BACKFILL_MAX_DAYS
                    or stored.get("status") not in BACKFILL_STATES
                ):
                    raise HomeAssistantError("Invalid saved Phyn history backfill settings")
                self.data = stored
                if stored["status"] == "running":
                    await self._async_update(
                        status="interrupted",
                        error="Home Assistant stopped before completion was confirmed.",
                    )
            self._initialized = True

    async def _async_update(self, **changes: Any) -> None:
        async with self._state_lock:
            updated = {**self.data, **changes}
            await self._store.async_save(updated)
            self.data = updated
            async_dispatcher_send(self.hass, self.signal)

    async def async_set_days(self, value: float) -> None:
        if (
            type(value) not in (int, float)
            or not 1 <= value <= BACKFILL_MAX_DAYS
            or int(value) != value
        ):
            raise HomeAssistantError(
                f"Backfill days must be a whole number from 1 to {BACKFILL_MAX_DAYS}"
            )
        await self.async_initialize()
        if self.stopping:
            raise HomeAssistantError("Phyn history backfills are stopping")
        try:
            await self._async_update(days=int(value))
        except OSError as err:
            raise HomeAssistantError("Could not save Phyn backfill days") from err

    async def async_start(self) -> None:
        await self.async_initialize()
        if self.stopping:
            raise HomeAssistantError("Phyn history backfills are stopping")
        if self.running or self.device.fixture_import_running:
            raise HomeAssistantError("A Phyn history import is already running for this device")
        async with self._start_lock:
            end = dt_util.utcnow()
            days = self.data["days"]
            start = end - timedelta(days=days)
            try:
                await self._async_update(
                    status="running", requested_days=days,
                    start_datetime=start.isoformat(), end_datetime=end.isoformat(),
                    started_at=end.isoformat(), finished_at=None, error=None,
                    imported_rows=None, events_fetched=None, corrections_detected=None,
                    chunks_total=0, chunks_completed=0, events_unique=0, duplicate_events=0,
                    remaining_start_ms=int(start.timestamp() * 1000),
                    remaining_end_ms=int(end.timestamp() * 1000),
                    remaining_start_datetime=start.isoformat(),
                    remaining_end_datetime=end.isoformat(),
                )
            except OSError as err:
                raise HomeAssistantError("Could not save Phyn backfill request") from err
            # Unload may have started while the request was being saved.
            if self.stopping:
                await self._async_update(status="interrupted")
                raise HomeAssistantError("Phyn history backfills are stopping")
            self._task = self.hass.async_create_background_task(
                self._async_run(start, end), f"Phyn history backfill {self.device.id}",
                eager_start=False,
            )
        async_dispatcher_send(self.hass, self.signal)

    async def _async_run(self, start: datetime, end: datetime) -> None:
        LOGGER.info("History backfill starting for %s (%s..%s)", self.device.id, start, end)
        try:
            result = await self.device.async_import_fixture_statistics(
                from_datetime=start, to_datetime=end,
                progress_callback=self._async_report_progress,
            )
            finished = dt_util.utcnow().isoformat()
            await self._async_update(
                status="completed", finished_at=finished,
                last_successful_at=finished,
                imported_rows=result["imported_rows"],
                events_fetched=result["events_fetched"],
                corrections_detected=result["corrections_detected"],
            )
            LOGGER.info("History backfill completed for %s: %s", self.device.id, result)
        except CancelledError:
            await self._async_record_failure(
                "interrupted", "Backfill stopped before completion was confirmed."
            )
            raise
        except (
            HomeAssistantError, AuthenticationError, RequestError, ClientError,
            OSError, ValueError, TimeoutError,
        ):
            LOGGER.exception("History backfill failed for %s", self.device.id)
            await self._async_record_failure(
                "failed", "History backfill failed; see the Home Assistant logs."
            )
        finally:
            if self.data["status"] == "running":
                await self._async_record_failure(
                    "failed", "Backfill ended without a confirmed result; see the Home Assistant logs."
                )
            self._task = None
            async_dispatcher_send(self.hass, self.signal)

    async def _async_report_progress(self, progress: dict[str, int]) -> None:
        await self._async_update(
            **progress,
            remaining_start_datetime=datetime.fromtimestamp(
                progress["remaining_start_ms"] / 1000, dt_util.UTC
            ).isoformat(),
            remaining_end_datetime=datetime.fromtimestamp(
                progress["remaining_end_ms"] / 1000, dt_util.UTC
            ).isoformat(),
        )

    async def _async_record_failure(self, status: str, message: str) -> None:
        changes = {
            "status": status, "error": message,
            "finished_at": dt_util.utcnow().isoformat(),
        }
        try:
            await self._async_update(**changes)
        except OSError:
            LOGGER.exception("Could not save backfill failure for %s", self.device.id)
            self.data.update(changes)
            async_dispatcher_send(self.hass, self.signal)

    async def async_shutdown(self) -> None:
        self.stopping = True
        async with self._start_lock:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except CancelledError:
                    pass
                self._task = None
                if self.data["status"] == "running":
                    await self._async_record_failure(
                        "interrupted", "Backfill stopped before completion was confirmed."
                    )


class PhynBackfillEntity(PhynEntity):
    """Cloud-history controls remain usable when the physical monitor is offline."""

    def __init__(self, entity_type: str, name: str, device: PhynPlusDevice) -> None:
        super().__init__(entity_type, name, device)
        self.backfill = device.history_backfill

    @property
    def available(self) -> bool:
        return not self.backfill.stopping

    async def async_added_to_hass(self) -> None:
        await self.backfill.async_initialize()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.backfill.signal, self.async_write_ha_state)
        )
