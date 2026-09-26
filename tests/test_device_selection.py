"""Home-aware labels and selection round trips without Phyn requests."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.phyn.config_flow import (
    _build_device_schema,
    _device_label,
    _extract_device_ids,
)
from custom_components.phyn import async_setup_entry, async_unload_entry
from custom_components.phyn.const import CLIENT, CONF_DEVICE_IDS, DOMAIN


HOMES = [
    {"id": "home_a", "name": "Cape", "devices": [
        {"device_id": "monitor_a", "product_code": "PP1", "name": "Main line"},
        {"device_id": "sensor_a", "product_code": "PW1"},
    ]},
    {"id": "home_b", "name": "NTK", "devices": [
        {"device_id": "monitor_b", "product_code": "PP2", "device_name": "Water main"},
    ]},
]


@pytest.mark.parametrize(("details", "expected"), [
    ({"device_name": "Nickname", "name": "Other"}, "Cape - Nickname (id)"),
    ({"name": "Main line"}, "Cape - Main line (id)"),
    ({"device_name": " ", "name": " Main line "}, "Cape - Main line (id)"),
    ({"name": None, "product_code": "PP1"}, "Cape - Phyn Plus (id)"),
    ({"product_code": "PP2"}, "Cape - Phyn Plus (2nd generation) (id)"),
    ({"product_code": "PC1"}, "Cape - Phyn Smart Water Assistant (id)"),
    ({"product_code": "PW1"}, "Cape - Phyn Smart Water Sensor (id)"),
    ({"product_code": "NEW"}, "Cape - NEW (id)"),
    ({}, "Cape - id"),
])
def test_device_labels_include_home_and_supported_names(details, expected):
    assert _device_label({"device_id": "id", **details}, "Cape") == expected


def test_schema_preserves_home_mapping_and_reconfigure_selections():
    initial = _build_device_schema(HOMES)({})
    assert initial == {"Cape": ["monitor_a", "sensor_a"], "NTK": ["monitor_b"]}
    selected = _build_device_schema(HOMES, ["monitor_a"])({})
    assert selected == {"Cape": ["monitor_a"], "NTK": []}
    assert _extract_device_ids(selected, HOMES) == ["monitor_a"]
    empty = _build_device_schema(HOMES, [])({})
    assert _extract_device_ids(empty, HOMES) == []
    schema = _build_device_schema(HOMES)
    choices = {str(key): validator.options for key, validator in schema.schema.items()}
    assert choices["Cape"]["monitor_a"] == "Cape - Main line (monitor_a)"
    assert choices["NTK"]["monitor_b"] == "NTK - Water main (monitor_b)"


def test_duplicate_and_missing_home_names_do_not_hide_devices():
    homes = deepcopy(HOMES)
    homes[1]["name"] = "Cape"
    selected = _build_device_schema(homes)({})
    assert set(selected) == {"Cape (home_a)", "Cape (home_b)"}
    assert _extract_device_ids(selected, homes) == ["monitor_a", "sensor_a", "monitor_b"]
    homes[0]["name"] = None
    homes[1]["name"] = " "
    selected = _build_device_schema(homes)({})
    assert set(selected) == {"home_a", "home_b"}
    assert _extract_device_ids(selected, homes) == ["monitor_a", "sensor_a", "monitor_b"]


async def test_initial_flow_presents_home_aware_options(hass, monkeypatch):
    lookup = AsyncMock(return_value=(None, deepcopy(HOMES)))
    monkeypatch.setattr("custom_components.phyn.config_flow._get_api_and_homes", lookup)
    monkeypatch.setattr("custom_components.phyn.async_setup_entry", AsyncMock(return_value=True))
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: "test@example.invalid", CONF_PASSWORD: "synthetic"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "device"
    choices = {
        str(key): validator.options for key, validator in result["data_schema"].schema.items()
    }
    assert choices["Cape"]["monitor_a"] == "Cape - Main line (monitor_a)"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"Cape": ["monitor_a"], "NTK": []}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_IDS] == ["monitor_a"]
    lookup.assert_awaited_once()
    await hass.async_block_till_done()


async def test_reconfigure_keeps_other_home_deselected(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="test@example.invalid", data={
            CONF_USERNAME: "test@example.invalid", CONF_PASSWORD: "synthetic",
            CONF_DEVICE_IDS: ["monitor_a"],
        },
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.phyn.config_flow._get_api_and_homes",
        AsyncMock(return_value=(None, deepcopy(HOMES))),
    )
    monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock(return_value=True))
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.FORM
    defaults = result["data_schema"]({})
    assert defaults == {"Cape": ["monitor_a"], "NTK": []}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=defaults
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE_IDS] == ["monitor_a"]
    await hass.async_block_till_done()


async def test_migrated_entry_without_selection_unloads_cleanly(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test@example.invalid",
        data={
            CONF_USERNAME: "test@example.invalid",
            CONF_PASSWORD: "synthetic",
            CONF_DEVICE_IDS: [],
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.home.get_homes.return_value = deepcopy(HOMES)
    monkeypatch.setattr(
        "custom_components.phyn.async_get_api", AsyncMock(return_value=client)
    )
    unload_platforms = AsyncMock(return_value=True)
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", unload_platforms
    )

    assert await async_setup_entry(hass, entry) is True
    client.mqtt.disconnect_and_wait.assert_awaited_once()
    assert CLIENT not in hass.data[DOMAIN]

    assert await async_unload_entry(hass, entry) is True
    unload_platforms.assert_not_awaited()
