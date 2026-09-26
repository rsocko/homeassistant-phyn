"""Read-only configured fixture counts, independent of usage statistics."""
from __future__ import annotations

from asyncio import timeout
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import ClientError
from aiophyn.errors import AuthenticationError, RequestError

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER
from .entities.base import PhynEntity

if TYPE_CHECKING:
    from .devices.pp import PhynPlusDevice


@dataclass(frozen=True)
class FixtureInventory:
    """One configured category, not an individual physical fixture."""

    name: str
    count: int


def parse_inventory(payload: object) -> dict[int, FixtureInventory]:
    """Validate a whole inventory snapshot without fabricating missing counts."""
    if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
        raise ValueError("Inventory response must contain a list")
    inventory: dict[int, FixtureInventory] = {}
    for row in payload["list"]:
        if not isinstance(row, dict):
            raise ValueError("Inventory category must be an object")
        category_id = row.get("home_inventory_type_id")
        name = row.get("name")
        count = row.get("count")
        if type(category_id) is not int or category_id < 0:
            raise ValueError("Inventory category ID must be a nonnegative integer")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Inventory category name must be nonempty")
        if type(count) is not int or count < 0:
            raise ValueError("Inventory count must be a nonnegative integer")
        if category_id in inventory:
            raise ValueError("Inventory contains a duplicate category ID")
        inventory[category_id] = FixtureInventory(name.strip(), count)
    return inventory


class PhynInventoryCoordinator(DataUpdateCoordinator[dict[int, FixtureInventory]]):
    """Refresh cloud inventory without blocking monitor or history refreshes."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, device: PhynPlusDevice
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=f"Phyn inventory {device.id}",
            config_entry=entry,
            update_interval=timedelta(hours=1),
        )
        self.device = device

    async def _async_update_data(self) -> dict[int, FixtureInventory]:
        try:
            async with timeout(10):
                payload = await (
                    self.device.coordinator.api_client.home_inventory.get_device_inventory(
                        self.device.id
                    )
                )
            return parse_inventory(payload)
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except (RequestError, ClientError, TimeoutError) as err:
            raise UpdateFailed("Could not fetch configured fixture counts") from err
        except ValueError as err:
            raise UpdateFailed(f"Invalid configured fixture counts: {err}") from err


class PhynInventoryCountSensor(PhynEntity, SensorEntity):
    """An optional inventory count; never a consumption statistics source."""

    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:counter"
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: PhynInventoryCoordinator, category_id: int
    ) -> None:
        self._inventory = coordinator
        self._category_id = category_id
        self._category_name = coordinator.data[category_id].name
        super().__init__(
            f"configured_fixture_count_{category_id}",
            f"Configured {self._category_name} count",
            coordinator.device,
        )

    @property
    def name(self) -> str:
        """Keep identity stable if Phyn changes a category's display name."""
        if self._inventory.data and self._category_id in self._inventory.data:
            self._category_name = self._inventory.data[self._category_id].name
        return f"Configured {self._category_name} count"

    @property
    def available(self) -> bool:
        return (
            self._inventory.last_update_success
            and self._inventory.data is not None
            and self._category_id in self._inventory.data
        )

    @property
    def native_value(self) -> int | None:
        if not self.available:
            return None
        return self._inventory.data[self._category_id].count

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._inventory.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self) -> None:
        await self._inventory.async_request_refresh()


async def async_setup_inventory(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: PhynPlusDevice,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover categories now and after later inventory refreshes."""
    coordinator = PhynInventoryCoordinator(hass, entry, device)
    known_ids: set[int] = set()

    @callback
    def add_new_categories() -> None:
        if not coordinator.last_update_success or coordinator.data is None:
            return
        new_ids = coordinator.data.keys() - known_ids
        if new_ids:
            async_add_entities([
                PhynInventoryCountSensor(coordinator, category_id)
                for category_id in sorted(new_ids)
            ])
            known_ids.update(new_ids)

    # Keep discovery active even when every count entity is disabled.
    entry.async_on_unload(coordinator.async_add_listener(add_new_categories))
    await coordinator.async_refresh()
