"""Offline inventory discovery, failure isolation, and entity semantics."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, create_autospec

from aiophyn.home_inventory import HomeInventory
from aiophyn.errors import AuthenticationError, RequestError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er

from custom_components.phyn.const import DOMAIN
from custom_components.phyn.devices.pp import PhynPlusDevice
from custom_components.phyn.inventory import (
    FixtureInventory,
    PhynInventoryCoordinator,
    PhynInventoryCountSensor,
    async_setup_inventory,
    parse_inventory,
)
from custom_components.phyn.sensor import async_setup_entry


def row(category_id=8, name="Toilet", count=2):
    return {"home_inventory_type_id": category_id, "name": name, "count": count}


@pytest.fixture
def inventory_device(hass):
    api = create_autospec(HomeInventory, instance=True)
    api.get_device_inventory.return_value = {"list": [row(), row(7, "Sink", 0)]}
    device = PhynPlusDevice(
        SimpleNamespace(
            hass=hass,
            api_client=SimpleNamespace(home_inventory=api),
        ),
        "home_one", "device_one", "PP1",
    )
    return device, api


@pytest.fixture
async def entry(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    yield entry
    await entry._async_process_on_unload(hass)


@pytest.mark.parametrize("payload", [
    None, [], {}, {"list": None}, {"list": [None]},
    {"list": [row(category_id=True)]},
    {"list": [row(category_id=-1)]},
    {"list": [row(category_id="8")]},
    {"list": [row(name=" ")]},
    {"list": [row(name=None)]},
    {"list": [row(count=None)]},
    {"list": [row(count=-1)]},
    {"list": [row(count=True)]},
    {"list": [row(count=1.5)]},
    {"list": [row(count="2")]},
    {"list": [row(), row(name="Duplicate")]},
])
def test_invalid_inventory_rejects_whole_snapshot(payload):
    with pytest.raises(ValueError):
        parse_inventory(payload)


def test_parser_keeps_zero_and_drops_unneeded_metadata():
    payload = {"list": [
        {**row(0, " Other ", 0), "sub_fixtures": [{"name": "Private"}]},
        row(),
    ]}
    assert parse_inventory(payload) == {
        0: FixtureInventory("Other", 0), 8: FixtureInventory("Toilet", 2),
    }
    assert parse_inventory({"list": []}) == {}


async def test_counts_are_not_water_or_statistics_entities(hass, entry, inventory_device):
    device, api = inventory_device
    coordinator = PhynInventoryCoordinator(hass, entry, device)
    await coordinator.async_refresh()
    sensor = PhynInventoryCountSensor(coordinator, 8)
    zero = PhynInventoryCountSensor(coordinator, 7)

    api.get_device_inventory.assert_awaited_once_with("device_one")
    api.update_device_inventory.assert_not_called()
    assert coordinator.update_interval == timedelta(hours=1)
    assert sensor.unique_id == "device_one_configured_fixture_count_8"
    assert sensor.name == "Configured Toilet count"
    assert sensor.device_info["identifiers"] == {(DOMAIN, "device_one")}
    assert sensor.native_value == 2
    assert zero.native_value == 0
    assert sensor.available
    assert not sensor.entity_registry_enabled_default
    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement is None
    assert sensor.state_class is None
    assert sensor.extra_state_attributes is None
    # An inventory read must not initialize or mutate the usage ledger.
    assert device._fixture_stats_importer.current_checkpoint_ms() == 0


async def test_failures_missing_categories_and_recovery(hass, entry, inventory_device):
    device, api = inventory_device
    coordinator = PhynInventoryCoordinator(hass, entry, device)
    await coordinator.async_refresh()
    sensor = PhynInventoryCountSensor(coordinator, 8)
    original = coordinator.data
    for failure in (RequestError("offline"), TimeoutError(), {"list": [row(count=-1)]}):
        if isinstance(failure, Exception):
            api.get_device_inventory.side_effect = failure
        else:
            api.get_device_inventory.side_effect = None
            api.get_device_inventory.return_value = failure
        await coordinator.async_refresh()
        assert not sensor.available
        assert sensor.native_value is None
        assert coordinator.data is original

    api.get_device_inventory.return_value = {"list": []}
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert not sensor.available
    assert sensor.native_value is None

    api.get_device_inventory.return_value = {"list": [row(name="Renamed toilet", count=0)]}
    await coordinator.async_refresh()
    assert sensor.available
    assert sensor.native_value == 0
    assert sensor.name == "Configured Renamed toilet count"
    assert sensor.unique_id == "device_one_configured_fixture_count_8"


async def test_authentication_failure_uses_reauth(hass, entry, inventory_device):
    device, api = inventory_device
    api.get_device_inventory.side_effect = AuthenticationError("expired")
    coordinator = PhynInventoryCoordinator(hass, entry, device)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_discovery_recovers_and_unsubscribes(
    hass, entry, inventory_device, monkeypatch
):
    device, api = inventory_device
    added = []
    coordinator = PhynInventoryCoordinator(hass, entry, device)
    monkeypatch.setattr(
        "custom_components.phyn.inventory.PhynInventoryCoordinator",
        Mock(return_value=coordinator),
    )
    api.get_device_inventory.side_effect = RequestError("offline")
    await async_setup_inventory(hass, entry, device, added.extend)
    assert not added
    api.get_device_inventory.side_effect = None
    await coordinator.async_refresh()
    assert len(added) == 2
    api.get_device_inventory.return_value = {"list": [
        row(name="Renamed"), row(7, "Sink", 0), row(5, "Shower", 1),
    ]}
    await coordinator.async_refresh()
    assert len(added) == 3
    await coordinator.async_refresh()
    assert len(added) == 3
    assert added[-1].unique_id == "device_one_configured_fixture_count_5"
    assert not added[-1].entity_registry_enabled_default
    await entry._async_process_on_unload(hass)
    assert not coordinator._listeners


async def test_platform_keeps_existing_sensors_and_skips_other_models(
    hass, entry, inventory_device, monkeypatch
):
    device, _api = inventory_device
    existing_sensor = device.entities[12]
    other = SimpleNamespace(entities=[])
    hass.data[DOMAIN] = {"coordinator": SimpleNamespace(devices=[device, other])}
    setup_inventory = AsyncMock()
    monkeypatch.setattr("custom_components.phyn.sensor.async_setup_inventory", setup_inventory)
    added = []
    await async_setup_entry(hass, entry, added.extend)
    assert existing_sensor in added
    setup_inventory.assert_awaited_once_with(hass, entry, device, added.extend)


async def test_device_scoped_identity(hass, entry, inventory_device):
    first, api = inventory_device
    second = PhynPlusDevice(first.coordinator, "home_two", "device_two", "PP2")
    coordinators = [
        PhynInventoryCoordinator(hass, entry, device) for device in (first, second)
    ]
    sensors = []
    for index, coordinator in enumerate(coordinators):
        api.get_device_inventory.return_value = {"list": [row(count=index + 1)]}
        await coordinator.async_refresh()
        sensors.append(PhynInventoryCountSensor(coordinator, 8))
    assert sensors[0].unique_id != sensors[1].unique_id
    assert [sensor.native_value for sensor in sensors] == [1, 2]


async def test_real_registry_defaults_and_enabled_count_state(
    hass, entry, inventory_device
):
    """Use HA's sensor platform to verify registration and ordinary state."""
    device, _api = inventory_device
    device.entities = []
    hass.data[DOMAIN] = {"coordinator": SimpleNamespace(devices=[device])}
    # Pre-enable one category, as if the user had enabled it on a previous load.
    registry = er.async_get(hass)
    enabled = registry.async_get_or_create(
        "sensor", DOMAIN, "device_one_configured_fixture_count_8",
        config_entry=entry, suggested_object_id="configured_toilet_count",
        disabled_by=None,
    )
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    await hass.async_block_till_done()

    state = hass.states.get(enabled.entity_id)
    assert state is not None
    assert state.state == "2"
    assert "state_class" not in state.attributes
    assert "device_class" not in state.attributes
    assert "unit_of_measurement" not in state.attributes
    zero_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "device_one_configured_fixture_count_7"
    )
    assert zero_id is not None
    assert registry.async_get(zero_id).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(zero_id) is None
    assert registry.async_get(enabled.entity_id).device_id == registry.async_get(zero_id).device_id
    await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    await entry._async_process_on_unload(hass)
