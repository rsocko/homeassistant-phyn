"""Helpers for importing Phyn fixture events into Home Assistant statistics."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_add_external_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

FIXTURE_STATS_STORE_VERSION = 1
DEFAULT_INITIAL_LOOKBACK_DAYS = 7
DEFAULT_EVENT_CACHE_RETENTION_DAYS = 30


@dataclass
class FixtureStatisticsState:
    """Persisted state for incremental fixture imports."""

    fixture_sums: dict[str, float] = field(default_factory=dict)
    last_event_ms: int = 0
    event_cache: dict[str, dict[str, Any]] = field(default_factory=dict)


def extract_event_id(event: dict[str, Any]) -> str | None:
    """Extract stable event identifier from event payload."""
    event_id = event.get("event_id") or event.get("id")
    if not isinstance(event_id, str):
        return None
    normalized = event_id.strip()
    return normalized or None


def resolve_fixture_name(event: dict[str, Any]) -> str:
    """Resolve fixture name from an event, preferring user feedback labels."""
    latest_feedback = event.get("latest_user_feedback") or {}
    if latest_feedback:
        tell_us = latest_feedback.get("tell_us")
        if isinstance(tell_us, str) and tell_us.strip():
            return tell_us.strip()

    user_label = event.get("user_fixture_label")
    if isinstance(user_label, str) and user_label.strip():
        return user_label.strip()

    suggested = (event.get("latest_suggested_fixtures_result") or {}).get(
        "suggested_fixtures", []
    )
    if suggested:
        fixture_name = suggested[0].get("fixture_name")
        if isinstance(fixture_name, str) and fixture_name.strip():
            return fixture_name.strip()

    return "Unknown"


def event_end_timestamp_ms(event: dict[str, Any]) -> int | None:
    """Extract event close timestamp in milliseconds."""
    end_ts = event.get("close_edge_timestamp") or event.get("open_edge_timestamp")
    if not isinstance(end_ts, (int, float)):
        return None
    end_ts_int = int(end_ts)
    if end_ts_int <= 0:
        return None
    return end_ts_int


def build_hourly_fixture_totals(
    events: list[dict[str, Any]],
    last_event_ms: int,
) -> tuple[dict[str, dict[datetime, float]], int]:
    """Aggregate events newer than last_event_ms into hourly per-fixture totals."""
    fixture_hourly_totals: dict[str, dict[datetime, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    newest_event_ms = last_event_ms

    for event in events:
        end_ms = event_end_timestamp_ms(event)
        if end_ms is None or end_ms <= last_event_ms:
            continue

        fixture_name = resolve_fixture_name(event)
        total_flow = event.get("total_flow")
        if not isinstance(total_flow, (int, float)):
            continue

        end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        hour_start = end_dt.replace(minute=0, second=0, microsecond=0)
        fixture_hourly_totals[fixture_name][hour_start] += float(total_flow)

        if end_ms > newest_event_ms:
            newest_event_ms = end_ms

    return fixture_hourly_totals, newest_event_ms


def detect_fixture_corrections(
    events: list[dict[str, Any]],
    cached_events: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Detect fixture assignment changes by comparing events to cached fixtures."""
    corrections: list[dict[str, str]] = []
    for event in events:
        event_id = extract_event_id(event)
        if event_id is None:
            continue

        current_fixture = resolve_fixture_name(event)
        cached = cached_events.get(event_id)
        if not isinstance(cached, dict):
            continue

        previous_fixture = cached.get("fixture")
        if isinstance(previous_fixture, str) and previous_fixture != current_fixture:
            corrections.append(
                {
                    "event_id": event_id,
                    "old_fixture": previous_fixture,
                    "new_fixture": current_fixture,
                }
            )

    return corrections


def fixture_statistic_id(device_id: str, fixture_name: str) -> str:
    """Build stable statistic_id for a fixture."""
    fixture_slug = "_".join(fixture_name.lower().split())
    fixture_slug = "".join(c for c in fixture_slug if c.isalnum() or c == "_")
    device_slug = "".join(c for c in device_id.lower() if c.isalnum())
    return f"{DOMAIN}:{device_slug}_{fixture_slug}_water"


async def async_clear_statistic_ids(hass: HomeAssistant, statistic_ids: list[str]) -> int:
    """Clear existing statistic ids when HA provides a compatible clear API."""
    if not statistic_ids:
        return 0

    from homeassistant.components.recorder import statistics as recorder_statistics

    clear_async = getattr(recorder_statistics, "async_clear_statistics", None)
    if not callable(clear_async):
        return 0

    try:
        await clear_async(hass, statistic_ids)
    except TypeError:
        await clear_async(hass, statistic_ids=statistic_ids)
    return len(statistic_ids)


class PhynFixtureStatisticsImporter:
    """Import incremental fixture event data to HA long-term statistics."""

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        """Initialize importer."""
        self._hass = hass
        self._device_id = device_id
        self._store: Store[dict[str, Any]] = Store(
            hass,
            FIXTURE_STATS_STORE_VERSION,
            f"{DOMAIN}_fixture_stats_{device_id.lower()}",
        )
        self._state = FixtureStatisticsState()

    async def async_initialize(self) -> None:
        """Load persisted importer state."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return

        fixture_sums = data.get("fixture_sums", {})
        if isinstance(fixture_sums, dict):
            self._state.fixture_sums = {
                str(name): float(value)
                for name, value in fixture_sums.items()
                if isinstance(value, (int, float))
            }

        last_event_ms = data.get("last_event_ms")
        if isinstance(last_event_ms, (int, float)):
            self._state.last_event_ms = int(last_event_ms)

        event_cache = data.get("event_cache", {})
        if isinstance(event_cache, dict):
            normalized: dict[str, dict[str, Any]] = {}
            for key, value in event_cache.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                fixture = value.get("fixture")
                end_ms = value.get("end_ms")
                if isinstance(fixture, str) and isinstance(end_ms, (int, float)):
                    normalized[key] = {"fixture": fixture, "end_ms": int(end_ms)}
            self._state.event_cache = normalized

    def current_checkpoint_ms(self) -> int:
        """Return current event checkpoint in milliseconds."""
        return self._state.last_event_ms

    def _build_import_diagnostics(
        self,
        events: list[dict[str, Any]],
        *,
        force_reimport: bool,
        dry_run: bool,
    ) -> tuple[dict[str, int], dict[str, dict[datetime, float]]]:
        """Build import diagnostics and hourly totals without mutating state."""
        checkpoint_before = -1 if force_reimport else self._state.last_event_ms
        events_fetched = len(events)
        events_newer_than_checkpoint = 0
        for event in events:
            end_ms = event_end_timestamp_ms(event)
            if end_ms is not None and end_ms > checkpoint_before:
                events_newer_than_checkpoint += 1

        fixture_hourly_totals, newest_event_ms = build_hourly_fixture_totals(
            events,
            checkpoint_before,
        )

        imported_rows = 0
        for hourly_totals in fixture_hourly_totals.values():
            imported_rows += len(hourly_totals)

        checkpoint_after = self._state.last_event_ms
        if newest_event_ms > checkpoint_after:
            checkpoint_after = newest_event_ms

        fixture_names: set[str] = set(self._state.fixture_sums.keys())
        for event in events:
            fixture_names.add(resolve_fixture_name(event))

        cleared_statistic_ids = len(fixture_names) if force_reimport else 0

        result = {
            "imported_rows": imported_rows,
            "events_fetched": events_fetched,
            "events_newer_than_checkpoint": events_newer_than_checkpoint,
            "checkpoint_before_ms": 0 if checkpoint_before < 0 else checkpoint_before,
            "checkpoint_after_ms": checkpoint_after,
            "cleared_statistic_ids": cleared_statistic_ids,
            "corrections_detected": len(
                detect_fixture_corrections(events, self._state.event_cache)
            ),
            "cached_events": len(self._state.event_cache),
            "force_reimport": 1 if force_reimport else 0,
            "dry_run": 1 if dry_run else 0,
        }
        return result, fixture_hourly_totals

    def _update_event_cache(self, events: list[dict[str, Any]]) -> bool:
        """Update persistent event->fixture cache and prune old entries."""
        cache_changed = False
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        cutoff_ms = now_ms - (DEFAULT_EVENT_CACHE_RETENTION_DAYS * 24 * 60 * 60 * 1000)

        for event in events:
            event_id = extract_event_id(event)
            end_ms = event_end_timestamp_ms(event)
            if event_id is None or end_ms is None:
                continue

            fixture = resolve_fixture_name(event)
            existing = self._state.event_cache.get(event_id)
            next_entry = {"fixture": fixture, "end_ms": end_ms}
            if existing != next_entry:
                self._state.event_cache[event_id] = next_entry
                cache_changed = True

        stale_event_ids = [
            event_id
            for event_id, payload in self._state.event_cache.items()
            if isinstance(payload, dict)
            and isinstance(payload.get("end_ms"), int)
            and payload["end_ms"] < cutoff_ms
        ]
        for event_id in stale_event_ids:
            del self._state.event_cache[event_id]
            cache_changed = True

        return cache_changed

    async def async_preview_import_events(
        self,
        events: list[dict[str, Any]],
        *,
        force_reimport: bool = False,
    ) -> dict[str, int]:
        """Preview import effects without mutating state or statistics."""
        result, _ = self._build_import_diagnostics(
            events,
            force_reimport=force_reimport,
            dry_run=True,
        )
        return result

    def next_fetch_start(self, now: datetime) -> datetime:
        """Return the next from_datetime for event fetching."""
        if self._state.last_event_ms > 0:
            return datetime.fromtimestamp(
                (self._state.last_event_ms + 1) / 1000,
                tz=timezone.utc,
            )
        return now - timedelta(days=DEFAULT_INITIAL_LOOKBACK_DAYS)

    async def async_import_events(
        self,
        events: list[dict[str, Any]],
        *,
        force_reimport: bool = False,
    ) -> dict[str, int]:
        """Import new event data into HA statistics.

        Returns import diagnostics:
            imported_rows: Number of statistic rows imported
            events_fetched: Number of events fetched from API
            events_newer_than_checkpoint: Events newer than last checkpoint
            checkpoint_before_ms: Last checkpoint before import
            checkpoint_after_ms: Last checkpoint after import
        """
        result, fixture_hourly_totals = self._build_import_diagnostics(
            events,
            force_reimport=force_reimport,
            dry_run=False,
        )
        newest_event_ms = result["checkpoint_after_ms"]

        imported_rows = 0

        for fixture_name, hourly_totals in fixture_hourly_totals.items():
            cumulative = self._state.fixture_sums.get(fixture_name, 0.0)
            statistic_id = fixture_statistic_id(self._device_id, fixture_name)

            metadata = StatisticMetaData(
                has_mean=False,
                has_sum=True,
                name=f"Phyn {fixture_name} Water",
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_of_measurement="gal",
            )

            stats: list[StatisticData] = []
            for hour_start in sorted(hourly_totals):
                cumulative += hourly_totals[hour_start]
                stats.append(
                    StatisticData(
                        start=hour_start,
                        sum=round(cumulative, 6),
                        state=round(cumulative, 6),
                    )
                )

            if stats:
                async_add_external_statistics(self._hass, metadata, stats)
                imported_rows += len(stats)
                self._state.fixture_sums[fixture_name] = cumulative

        checkpoint_changed = newest_event_ms > self._state.last_event_ms
        if checkpoint_changed:
            self._state.last_event_ms = newest_event_ms

        cache_changed = self._update_event_cache(events)

        if imported_rows > 0 or checkpoint_changed or cache_changed:
            await self._store.async_save(
                {
                    "fixture_sums": self._state.fixture_sums,
                    "last_event_ms": self._state.last_event_ms,
                    "event_cache": self._state.event_cache,
                }
            )

        result["imported_rows"] = imported_rows
        result["checkpoint_after_ms"] = self._state.last_event_ms
        result["cleared_statistic_ids"] = 0
        result["dry_run"] = 0
        result["cached_events"] = len(self._state.event_cache)
        return result

    async def async_force_reimport_events(self, events: list[dict[str, Any]]) -> dict[str, int]:
        """Force a rebuild by clearing existing stats for this device and re-importing.

        This bypasses checkpoint gating and resets cumulative baselines.
        """
        fixture_names: set[str] = set(self._state.fixture_sums.keys())
        for event in events:
            fixture_names.add(resolve_fixture_name(event))

        statistic_ids = [
            fixture_statistic_id(self._device_id, fixture_name)
            for fixture_name in sorted(fixture_names)
        ]

        cleared_statistic_ids = await async_clear_statistic_ids(self._hass, statistic_ids)

        self._state.fixture_sums = {}
        self._state.last_event_ms = 0
        self._state.event_cache = {}
        await self._store.async_save(
            {
                "fixture_sums": self._state.fixture_sums,
                "last_event_ms": self._state.last_event_ms,
                "event_cache": self._state.event_cache,
            }
        )

        result = await self.async_import_events(events, force_reimport=True)
        result["cleared_statistic_ids"] = cleared_statistic_ids
        result["force_reimport"] = 1
        return result