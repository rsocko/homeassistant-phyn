"""Opt-in, read-only checks of imported usage against Energy selections."""
from __future__ import annotations

from asyncio import Lock
from datetime import datetime, timedelta

from homeassistant.components.energy.data import async_get_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_ENERGY_COVERAGE,
    CONF_ENERGY_COVERAGE_EXCLUDED,
    DOMAIN,
    LOGGER,
)
from .devices.pp import PhynPlusDevice


class PhynEnergyCoverage:
    """Report omissions, never edit Energy preferences or consumption data."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, devices: list[PhynPlusDevice]
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.devices = devices
        self._lock = Lock()
        self._stopped = False
        self._issue_ids = {
            device.home_id: f"energy_coverage_{entry.entry_id}_{device.home_id}"
            for device in devices
        }

    async def async_check(self, _now: datetime | None = None) -> None:
        async with self._lock:
            if self._stopped:
                return
            if not self.entry.options.get(CONF_ENERGY_COVERAGE, False):
                self._clear()
                return
            try:
                manager = await async_get_manager(self.hass)
            except OSError:
                LOGGER.exception("Could not read Energy preferences for Phyn coverage")
                return
            if self._stopped:
                return
            preferences = manager.data or {}
            selected = {
                item["stat_consumption"]
                for item in preferences.get("device_consumption_water", [])
            }
            excluded = set(self.entry.options.get(CONF_ENERGY_COVERAGE_EXCLUDED, []))
            missing: dict[str, dict[str, str]] = {}
            names: dict[str, str] = {}
            for device in self.devices:
                importer = device._fixture_stats_importer
                names[device.home_id] = importer.home_name
                for identifier, name in importer.usage_statistics().items():
                    if identifier not in selected and identifier not in excluded:
                        missing.setdefault(device.home_id, {})[identifier] = name
            for home_id, issue_id in self._issue_ids.items():
                if not missing.get(home_id):
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                    continue
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    is_persistent=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="energy_coverage",
                    translation_placeholders={
                        "home": names[home_id],
                        "statistics": "\n".join(
                            f"- {name} (`{identifier}`)"
                            for identifier, name in sorted(missing[home_id].items())
                        ),
                    },
                    learn_more_url="https://www.home-assistant.io/docs/energy/individual-devices/",
                )

    @callback
    def _clear(self) -> None:
        for issue_id in self._issue_ids.values():
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        self._clear()

    async def async_options_updated(self, _hass: HomeAssistant, _entry: ConfigEntry) -> None:
        await self.async_check()

    async def async_setup(self) -> None:
        self.entry.async_on_unload(self.async_stop)
        self.entry.async_on_unload(self.entry.add_update_listener(self.async_options_updated))
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self.async_check, timedelta(minutes=5))
        )
        await self.async_check()
