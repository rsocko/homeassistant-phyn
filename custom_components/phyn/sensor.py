"""Support for Phyn Water Monitor sensors."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN as PHYN_DOMAIN
from .devices.pp import PhynPlusDevice
from .inventory import async_setup_inventory
from .backfill import BACKFILL_STATES, PhynBackfillEntity


class PhynHistoryBackfillStatus(PhynBackfillEntity, SensorEntity):
    """Outcome of button-requested imports, not a water statistics source."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "history_backfill_status"
    _attr_options = BACKFILL_STATES
    _attr_icon = "mdi:database-clock"

    def __init__(self, device: PhynPlusDevice) -> None:
        super().__init__("history_backfill_status", "History backfill status", device)

    @property
    def native_value(self) -> str:
        return self.backfill.data["status"]

    @property
    def extra_state_attributes(self) -> dict:
        return {
            key: value for key, value in self.backfill.data.items()
            if key not in ("days", "status")
        }

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Phyn sensors from config entry."""
    coordinator = hass.data[PHYN_DOMAIN]["coordinator"]
    entities = []
    for device in coordinator.devices:
        if isinstance(device, PhynPlusDevice):
            entities.append(PhynHistoryBackfillStatus(device))
        entities.extend([
            entity
            for entity in device.entities
            if isinstance(entity, SensorEntity)
        ])
    async_add_entities(entities)
    for device in coordinator.devices:
        if isinstance(device, PhynPlusDevice):
            await async_setup_inventory(hass, config_entry, device, async_add_entities)
