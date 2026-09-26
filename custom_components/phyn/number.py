"""Local duration setting for Phyn history backfills."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .backfill import BACKFILL_MAX_DAYS, PhynBackfillEntity
from .const import DOMAIN
from .devices.pp import PhynPlusDevice


class PhynHistoryBackfillDays(PhynBackfillEntity, NumberEntity):
    """A persisted lookback, not a Phyn retention claim or cloud preference."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "history_backfill_days"
    _attr_icon = "mdi:calendar-range"
    _attr_native_min_value = 1
    _attr_native_max_value = BACKFILL_MAX_DAYS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_mode = NumberMode.BOX

    def __init__(self, device: PhynPlusDevice) -> None:
        super().__init__("history_backfill_days", "Backfill days", device)

    @property
    def native_value(self) -> float:
        return self.backfill.data["days"]

    async def async_set_native_value(self, value: float) -> None:
        await self.backfill.async_set_days(value)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities([
        PhynHistoryBackfillDays(device)
        for device in coordinator.devices if isinstance(device, PhynPlusDevice)
    ])
