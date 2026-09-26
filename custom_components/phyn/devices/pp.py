"""Support for Phyn Plus Water Monitor sensors."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from aiophyn.errors import RequestError
from asyncio import CancelledError, Lock, Task, timeout

from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
import homeassistant.util.dt as dt_util

from ..const import LOGGER
from ..backfill import PhynHistoryBackfill
from ..fixture_statistics import PhynFixtureStatisticsImporter
from ..logbook_helpers import async_add_logbook_entry
from ..entities.base import (
    PhynAlertEvent,
    PhynAlertSensor,
    PhynDailyUsageSensor,
    PhynFirmwareUpdateAvailableSensor,
    PhynFirmwareUpdateEntity,
    PhynPressureSensor,
    PhynTemperatureSensor,
)
from ..entities.pp import (
    PhynAutoShutoffModeSwitch,
    PhynAwayModeSwitch,
    PhynConsumptionSensor,
    PhynCurrentFlowRateSensor,
    PhynFlowState,
    PhynLeakTestLeakDetected,
    PhynLeakTestSensor,
    PhynLeakTestWarning,
    PhynScheduledLeakTestEnabledSwitch,
    PhynValve,
)
from .base import PhynDevice

import math
import time

if TYPE_CHECKING:
    from ..update_coordinator import PhynDataUpdateCoordinator

NAME_WATER_TEMPERATURE = "Current water temperature"
NAME_WATER_PRESSURE = "Current water pressure"

class PhynPlusDevice(PhynDevice):
    """Phyn device object."""

    ALERT_EVENT_TYPES: list[str] = [
        "battery",
        "freeze_warn",
        "high_pressure",
        "leak",
        "offline_leak",
        "periodic_leak",
        "pinhole_leak",
        "temperature",
    ]

    def __init__(
        self,
        coordinator: PhynDataUpdateCoordinator,
        home_id: str,
        device_id: str,
        product_code: str,
        home_name: str = "",
        *,
        statistics_home_name: str = "",
        statistics_display_context: str | None = None,
    ) -> None:
        """Initialize the device."""
        super().__init__(coordinator, home_id, device_id, product_code, home_name)
        self._device_state: dict[str, Any] = {
            "flow_state": {
                "v": 0.0,
                "ts": 0,
            }
        }
        self._auto_shutoff: dict[str, Any] = {}
        self._water_usage: dict[str, Any] = {}
        self._last_known_valve_state: bool = True
        self._latest_health_test: dict[str, Any] | None = None
        self._rt_device_state: dict[str, Any] = {}
        self._state_lock: Lock = Lock()
        self._fixture_import_lock: Lock = Lock()
        self._fixture_task: Task[None] | None = None
        self._fixture_stopping = False
        self.history_backfill = PhynHistoryBackfill(self)
        self.configured_fixture_categories: set[str] = set()
        self._fixture_stats_importer = PhynFixtureStatisticsImporter(
            coordinator.hass,
            self._phyn_device_id,
            home_name=statistics_home_name or home_name,
            display_context=statistics_display_context,
        )

        self.entities = [
            PhynAlertEvent(self),
            PhynAlertSensor(self, "alert_battery", "Battery Alert", "alert_battery"),
            PhynAlertSensor(self, "alert_freeze_warn", "Freeze Warning Alert", "alert_freeze_warn"),
            PhynAlertSensor(self, "alert_high_pressure", "High Pressure Alert", "alert_high_pressure"),
            PhynAlertSensor(self, "alert_leak", "Leak Alert", "alert_leak"),
            PhynAlertSensor(self, "alert_offline_leak", "Offline Leak Shutoff Alert", "alert_offline_leak"),
            PhynAlertSensor(self, "alert_periodic_leak", "Recurring Flow Alert", "alert_periodic_leak"),
            PhynAlertSensor(self, "alert_pinhole_leak", "Pinhole Leak Alert", "alert_pinhole_leak"),
            PhynAlertSensor(self, "alert_temperature", "Temperature Alert", "alert_temperature"),
            PhynAutoShutoffModeSwitch(self),
            PhynAwayModeSwitch(self),
            PhynFlowState(self),
            PhynDailyUsageSensor(self),
            PhynCurrentFlowRateSensor(self),
            PhynConsumptionSensor(self),
            PhynFirmwareUpdateAvailableSensor(self),
            PhynFirmwareUpdateEntity(self),
            PhynLeakTestLeakDetected(self),
            PhynLeakTestSensor(self),
            PhynLeakTestWarning(self),
            PhynScheduledLeakTestEnabledSwitch(self),
            PhynTemperatureSensor(self, "temperature", NAME_WATER_TEMPERATURE),
            PhynPressureSensor(self, "pressure", NAME_WATER_PRESSURE),
            PhynValve(self),
        ]

    async def async_update_data(self):
        """Update data via library."""
        try:
            async with timeout(20):
                await self._update_device_state()
                await self._update_alerts()
                await self._update_alert_events()
                await self._update_autoshutoff()
                await self._update_device_preferences()
                await self._update_consumption_data()
                #Update every 10 minutes
                if self._update_count % 10 == 0:
                    await self._update_device_health_tests()

                #Update every hour
                if (self._update_count % 60 == 0):
                    await self._update_firmware_information()
                
                if not self._fixture_stopping and not self.history_backfill.running and self._update_count % 15 == 0 and (
                    self._fixture_task is None or self._fixture_task.done()
                ):
                    self._fixture_task = self._coordinator.hass.async_create_background_task(
                        self._update_fixture_statistics(), f"Phyn fixture import {self.id}"
                    )
                self._update_count += 1
        except (RequestError) as error:
            raise UpdateFailed(error) from error

    @property
    def consumption(self) -> float | None:
        """Return the lifetime meter reading in gallons."""
        value = self._device_state.get("consumption")
        # REST get_state() stores {"v": ..., "ts": ...}; on_device_update stores
        # a floored scalar. Accept both so a realtime message that lacks
        # "consumption" cannot drop the sensor to unknown.
        if isinstance(value, dict):
            value = value.get("v")
        if value is None:
            return None
        return math.floor(value * 100) / 100

    @property
    def consumption_today(self) -> float | None:
        """Return the current consumption for today in gallons."""
        return self._water_usage.get("water_consumption")

    @property
    def current_flow_rate(self) -> float | None:
        """Return current flow rate in gpm."""
        flow = self._device_state.get("flow", {})
        if "v" not in flow:
            return None
        return round(flow["v"], 3)

    @property
    def current_psi(self) -> float:
        """Return the current pressure in psi."""
        pressure = self._device_state.get("pressure", {})
        if "v" in pressure:
            return round(pressure["v"], 2)
        return round(pressure.get("mean", 0), 2)

    @property
    def leak_test_running(self) -> bool:
        """Check if a leak test is running"""
        sov_status = self._device_state.get("sov_status", {})
        return sov_status.get("v") == "LeakExp"

    @property
    def temperature(self) -> float:
        """Return the current temperature in degrees F."""
        temp = self._device_state.get("temperature", {})
        if "v" in temp:
            return round(temp["v"], 2)
        return round(temp.get("mean", 0), 2)

    @property
    def scheduled_leak_test_enabled(self) -> bool | None:
        """Return if the scheduled leak test is enabled"""
        if "scheduler_enable" not in self._device_preferences:
            return None
        scheduler = self._device_preferences.get("scheduler_enable", {})
        return scheduler.get("value") == "true"


    @property
    def alert_battery(self) -> bool:
        return self.has_active_alert("battery")

    @property
    def alert_freeze_warn(self) -> bool:
        return self.has_active_alert("freeze_warn")

    @property
    def alert_high_pressure(self) -> bool:
        return self.has_active_alert("high_pressure")

    @property
    def alert_leak(self) -> bool:
        return self.has_active_alert("leak")

    @property
    def alert_offline_leak(self) -> bool:
        return self.has_active_alert("offline_leak")

    @property
    def alert_periodic_leak(self) -> bool:
        return self.has_active_alert("periodic_leak")

    @property
    def alert_pinhole_leak(self) -> bool:
        return self.has_active_alert("pinhole_leak")

    @property
    def alert_temperature(self) -> bool:
        return self.has_active_alert("temperature")

    @property
    def valve_open(self) -> bool:
        """Return the valve state for the device."""
        if self.valve_changing:
            return self._last_known_valve_state
        sov_status = self._device_state.get("sov_status", {})
        return sov_status.get("v") == "Open"

    @property
    def valve_changing(self) -> bool:
        """Return the valve changing status"""
        sov_status = self._device_state.get("sov_status", {})
        return sov_status.get("v") == "Partial"

    async def async_setup(self) -> str:  # type: ignore[override]
        """Setup a new device coordinator"""
        LOGGER.debug("Setting up coordinator")

        await self._fixture_stats_importer.async_initialize()
        await self._coordinator.api_client.mqtt.add_event_handler("update", self.on_device_update)
        await self._coordinator.api_client.mqtt.subscribe(f"prd/app_subscriptions/{self._phyn_device_id}")
        return self._device_state["sov_status"]["v"]

    async def async_import_fixture_statistics(
        self,
        from_datetime: datetime | None = None,
        to_datetime: datetime | None = None,
        force_reimport: bool = False,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Restore state and serialize manual and recurring fixture imports."""
        async with self._fixture_import_lock:
            async_dispatcher_send(self._coordinator.hass, self.history_backfill.signal)
            try:
                if self._fixture_stopping:
                    raise HomeAssistantError("Phyn fixture imports are stopping")
                await self._fixture_stats_importer.async_initialize()
                return await self._async_import_fixture_statistics(
                    from_datetime, to_datetime, force_reimport, dry_run
                )
            finally:
                self._coordinator.hass.loop.call_soon(
                    async_dispatcher_send, self._coordinator.hass, self.history_backfill.signal
                )

    @property
    def fixture_import_running(self) -> bool:
        return self._fixture_import_lock.locked()

    async def _async_import_fixture_statistics(
        self,
        from_datetime: datetime | None,
        to_datetime: datetime | None,
        force_reimport: bool,
        dry_run: bool,
    ) -> dict[str, int]:
        """Import fixture events for a given time window.

        If ``from_datetime`` is omitted, importer checkpoint state determines
        the next fetch start.
        """
        now_utc = dt_util.now(timezone.utc)
        to_dt = to_datetime or now_utc
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)

        from_dt = from_datetime or self._fixture_stats_importer.next_fetch_start(to_dt)
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)

        if from_dt >= to_dt:
            raise HomeAssistantError("Fixture import start must be before its end")

        if not dry_run and self.configured_fixture_categories:
            await self._fixture_stats_importer.async_register_categories(
                self.configured_fixture_categories
            )

        events = await self._coordinator.api_client.device.get_water_usage_events(
            self._phyn_device_id,
            from_ts=int(from_dt.timestamp() * 1000),
            to_ts=int(to_dt.timestamp() * 1000),
        )

        if dry_run:
            return await self._fixture_stats_importer.async_preview_import_events(
                events,
                force_reimport=force_reimport,
            )
        if force_reimport:
            return await self._fixture_stats_importer.async_force_reimport_events(events)
        return await self._fixture_stats_importer.async_import_events(events)

    async def _update_fixture_statistics(self) -> None:
        """Fetch fixture usage events and import into HA long-term statistics."""
        try:
            result = await self.async_import_fixture_statistics()

            imported = int(result.get("imported_rows", 0))
            events_fetched = int(result.get("events_fetched", 0))
            newer_events = int(result.get("events_newer_than_checkpoint", 0))
            checkpoint_before = int(result.get("checkpoint_before_ms", 0))
            checkpoint_after = int(result.get("checkpoint_after_ms", 0))
            corrections_detected = int(result.get("corrections_detected", 0))

            if imported > 0:
                LOGGER.info(
                    "Recurring fixture import for device %s: rows=%s, events=%s, newer=%s, checkpoint_before=%s, checkpoint_after=%s, corrections=%s",
                    self._phyn_device_id,
                    imported,
                    events_fetched,
                    newer_events,
                    checkpoint_before,
                    checkpoint_after,
                    corrections_detected,
                )
            else:
                LOGGER.debug(
                    "Recurring fixture import (no new rows) for device %s: rows=%s, events=%s, newer=%s, checkpoint_before=%s, checkpoint_after=%s, corrections=%s",
                    self._phyn_device_id,
                    imported,
                    events_fetched,
                    newer_events,
                    checkpoint_before,
                    checkpoint_after,
                    corrections_detected,
                )

            if imported > 0:
                await async_add_logbook_entry(
                    self._coordinator.hass,
                    (
                        f"Recurring fixture import for {self._phyn_device_id}: "
                        f"rows={imported}, events={events_fetched}, newer={newer_events}, corrections={corrections_detected}"
                    ),
                )
        except Exception as err:
            LOGGER.exception(
                "Recurring fixture import failed for device %s: %s",
                self._phyn_device_id,
                err,
            )
            await async_add_logbook_entry(
                self._coordinator.hass,
                f"Recurring fixture import failed for {self._phyn_device_id}: {err}",
            )

    async def async_shutdown(self) -> None:
        """Stop history work without discarding any persisted pending import."""
        self._fixture_stopping = True
        await self.history_backfill.async_shutdown()
        if self._fixture_task is not None:
            self._fixture_task.cancel()
            try:
                await self._fixture_task
            except CancelledError:
                pass
            self._fixture_task = None
        async with self._fixture_import_lock:
            pass
    
    @property
    def autoshutoff_enabled(self) -> bool | None:
        """Return True if auto shutoff enabled"""
        if "auto_shutoff_enable" not in self._auto_shutoff:
            return None
        return self._auto_shutoff["auto_shutoff_enable"] == True
    
    async def set_autoshutoff_enabled(self, state: bool) -> None:
        LOGGER.debug("Setting auto shutoff state: %s" % state)
        await self._coordinator.api_client.device.set_autoshutoff_enabled(self._phyn_device_id, state)
        self._auto_shutoff["auto_shutoff_enable"] = state

    @property
    def away_mode(self) -> bool | None:
        """Return True if device is in away mode."""
        if "leak_sensitivity_away_mode" not in self._device_preferences:
            return None
        return self._device_preferences["leak_sensitivity_away_mode"]["value"] == "true"

    async def set_device_preference(self, name: str, val: str) -> None:
        """Set Device Preference.

        :param name: Preference name (leak_sensitivity_away_mode or scheduler_enable)
        :param val: Preference value as string ("true" or "false")
        """
        if name not in ["leak_sensitivity_away_mode", "scheduler_enable"]:
            LOGGER.debug("Tried setting preference for %s but not available", name)
            return None
        if val not in ["true", "false"]:
            return None
        params = [{
            "device_id": self._phyn_device_id,
            "name": name,
            "value": val
        }]
        LOGGER.debug("Setting preference '%s' to '%s'", name, val)
        await self._coordinator.api_client.device.set_device_preferences(self._phyn_device_id, params)
        if name not in self._device_preferences:
            self._device_preferences[name] = {}
        self._device_preferences[name]["value"] = val
    
    async def set_away_mode(self, state: bool) -> None:
        """Manually set away mode value"""
        key = "leak_sensitivity_away_mode"
        val = "true" if state else "false"
        params = [{
            "device_id": self._phyn_device_id,
            "name": key,
            "value": val
        }]
        await self._coordinator.api_client.device.set_device_preferences(self._phyn_device_id, params)
        self._device_preferences[key]["value"] = val

    async def set_scheduler_enabled(self, state: bool) -> None:
        """Manually set the scheduler enabled mode"""
        key = "scheduler_enable"
        val = "true" if state else "false"
        params = [{
            "device_id": self._phyn_device_id,
            "name": key,
            "value": val
        }]
        await self._coordinator.api_client.device.set_device_preferences(self._phyn_device_id, params)
        self._device_preferences[key]["value"] = val
    
    async def _update_autoshutoff(self, *_) -> None:
        """Update auto shutoff status"""
        data = await self._coordinator.api_client.device.get_autoshuftoff_status(self._phyn_device_id)
        LOGGER.debug("Autoshutoff info: %s" % data)
        self._auto_shutoff.update(data)

    async def _update_device_preferences(self, *_) -> None:
        """Update the device preferences from the API"""
        data = await self._coordinator.api_client.device.get_device_preferences(self._phyn_device_id)
        for item in data:
            self._device_preferences.update({item['name']: item})
        #LOGGER.debug("Device Preferences: %s", self._device_preferences)

    async def _update_consumption_data(self, *_) -> None:
        """Update water consumption data from the API."""
        today = dt_util.now().date()
        duration = today.strftime("%Y/%m/%d")
        self._water_usage = await self._coordinator.api_client.device.get_consumption(
            self._phyn_device_id, duration
        )
        LOGGER.debug("Updated Phyn consumption data: %s", self._water_usage)
    
    async def _update_device_health_tests(self, *_) -> None:
        """Update the latest health test"""
        try: 
            data = await self._coordinator.api_client.device.get_health_tests(self._phyn_device_id)
        except Exception as error:
            LOGGER.error("Error getting health tests: %s" % error)
            self._latest_health_test = None
            return
        latest_test = None
        LOGGER.debug("Health data: %s" % data)
        for test in data['data']:
            if latest_test is None or latest_test['end_time'] < test['end_time']:
                latest_test = test
        
        self._latest_health_test = latest_test        

    def _update_last_known_valve_state(self) -> None:
        """Update last known valve state from device state. Must be called within _state_lock."""
        sov_status = self._device_state.get("sov_status", {})
        if sov_status.get("v") != "Partial":
            self._last_known_valve_state = sov_status.get("v") == "Open"

    async def _update_device_state(self, *_) -> None:
        """Update the device state from the API."""
        async with self._state_lock:
            if 'last_updated' not in self._device_state or self._device_state['last_updated'] <= (math.floor(time.time()) - 60):
                state_data = await self._coordinator.api_client.device.get_state(
                    self._phyn_device_id
                )
                self._device_state.update(state_data)
                self._device_state['last_updated'] = math.floor(time.time())
                self._update_last_known_valve_state()

    async def on_device_update(self, device_id, data):
        if device_id == self._phyn_device_id:
            async with self._state_lock:
                self._rt_device_state = data

                update_data = {}
                if "consumption" in data:
                    # Round consumption down to 2 decimal points.
                    update_data.update({"consumption": math.floor(data["consumption"]["v"] * 100) / 100})
                if "flow" in data:
                    update_data.update({"flow": data["flow"]})
                if "flow_state" in data:
                    update_data.update({"flow_state": data["flow_state"]})
                if "sov_state" in data:
                    update_data.update({"sov_status":{"v": data["sov_state"]}})
                if "sensor_data" in data:
                    if "pressure" in data["sensor_data"]:
                        update_data.update({"pressure": data["sensor_data"]["pressure"]})
                    if "temperature" in data["sensor_data"]:
                        update_data.update({"temperature": data["sensor_data"]["temperature"]})
                self._device_state.update(update_data)
                self._device_state['last_updated'] = math.floor(time.time())
                self._update_last_known_valve_state()
                LOGGER.debug("Updating device %s Device State: %s", self._phyn_device_id, self._device_state)

            for entity in self.entities:
                # Skip entities that aren't fully initialized yet
                if getattr(entity, "hass", None) is None:
                    continue
                entity.async_write_ha_state()
