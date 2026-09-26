"""Native Phyn history backfill controls."""
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .backfill import PhynBackfillEntity
from .const import DOMAIN
from .devices.pp import PhynPlusDevice


class PhynHistoryBackfillButton(PhynBackfillEntity, ButtonEntity):
    """Start a device-scoped import without holding the UI action open."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "history_backfill"
    _attr_icon = "mdi:database-import"

    def __init__(self, device: PhynPlusDevice) -> None:
        super().__init__("history_backfill", "Backfill category history", device)

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.backfill.running
            and not self.backfill.device.fixture_import_running
        )

    async def async_press(self) -> None:
        await self.backfill.async_start()


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities([
        PhynHistoryBackfillButton(device)
        for device in coordinator.devices if isinstance(device, PhynPlusDevice)
    ])
