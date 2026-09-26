"""Energy coverage is opt-in, home-scoped, and never changes preferences."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.components.energy.data import async_get_manager
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.phyn.config_flow import PhynOptionsFlow
from custom_components.phyn.const import (
    CONF_ENERGY_COVERAGE,
    CONF_ENERGY_COVERAGE_EXCLUDED,
    CONF_EXCLUDED_ALERT_TYPES,
    DOMAIN,
)
from custom_components.phyn.devices.pp import PhynPlusDevice
from custom_components.phyn.energy_coverage import PhynEnergyCoverage
from custom_components.phyn.fixture_statistics import FixtureStatisticsState


@pytest.fixture
async def coverage(hass):
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_ENERGY_COVERAGE: True})
    entry.add_to_hass(hass)
    devices = []
    for index, home in enumerate(("Cape", "NTK")):
        device = PhynPlusDevice(
            SimpleNamespace(hass=hass), f"home_{index}", f"device_{index}", "PP1",
            statistics_home_name=home,
        )
        device._fixture_stats_importer._state = FixtureStatisticsState(
            events={
                "event": {"fixture": "Toilet", "volume": 2.0, "end_ms": 1000},
            },
            fixture_ids={"Toilet": f"phyn:device_{index}_toilet"},
            rows={},
        )
        devices.append(device)
    checker = PhynEnergyCoverage(hass, entry, devices)
    yield checker
    checker.async_stop()
    await entry._async_process_on_unload(hass)


async def test_missing_usage_is_aggregated_per_home_without_changing_energy(hass, coverage):
    manager = await async_get_manager(hass)
    manager.data = {
        "energy_sources": [],
        "device_consumption": [{"stat_consumption": "phyn:device_0_toilet"}],
        "device_consumption_water": [
            {"stat_consumption": "sensor.whole_house", "name": "My meter"},
        ],
    }
    original = deepcopy(manager.data)
    await coverage.async_check()
    registry = ir.async_get(hass)
    for index, home in enumerate(("Cape", "NTK")):
        issue = registry.async_get_issue(DOMAIN, coverage._issue_ids[f"home_{index}"])
        assert issue is not None
        assert issue.severity == ir.IssueSeverity.WARNING
        assert issue.translation_placeholders["home"] == home
        assert f"phyn:device_{index}_toilet" in issue.translation_placeholders["statistics"]
        assert f"phyn:device_{1-index}_toilet" not in issue.translation_placeholders["statistics"]
    assert manager.data == original

    manager.data["device_consumption_water"].append({
        "stat_consumption": "phyn:device_0_toilet",
        "name": "Custom toilet name", "included_in_stat": "sensor.whole_house",
    })
    updated = deepcopy(manager.data)
    await coverage.async_check()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_0"]) is None
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_1"]) is not None
    assert manager.data == updated


async def test_exclusions_option_disable_and_corrected_usage_clear(hass, coverage):
    await coverage.async_check()
    registry = ir.async_get(hass)
    hass.config_entries.async_update_entry(coverage.entry, options={
        CONF_ENERGY_COVERAGE: True,
        CONF_ENERGY_COVERAGE_EXCLUDED: ["phyn:device_0_toilet"],
    })
    await coverage.async_check()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_0"]) is None
    coverage.devices[1]._fixture_stats_importer._state.events["event"]["volume"] = 0
    await coverage.async_check()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_1"]) is None
    coverage.devices[1]._fixture_stats_importer._state.events["event"]["volume"] = 1
    await coverage.async_check()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_1"]) is not None
    hass.config_entries.async_update_entry(coverage.entry, options={})
    await coverage.async_check()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_1"]) is None


async def test_inventory_only_and_pending_evidence_do_not_warn(hass, coverage):
    for device in coverage.devices:
        device._fixture_stats_importer._state.events.clear()
        device._fixture_stats_importer._pending = SimpleNamespace(
            updates={"pending": {"fixture": "Toilet", "volume": 5, "end_ms": 2000}}
        )
    await coverage.async_check()
    registry = ir.async_get(hass)
    assert all(
        registry.async_get_issue(DOMAIN, issue_id) is None
        for issue_id in coverage._issue_ids.values()
    )


async def test_polling_options_listener_and_unload(hass, coverage):
    hass.config_entries.async_update_entry(coverage.entry, options={})
    await coverage.async_setup()
    registry = ir.async_get(hass)
    hass.config_entries.async_update_entry(
        coverage.entry, options={CONF_ENERGY_COVERAGE: True}
    )
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_0"]) is not None
    manager = await async_get_manager(hass)
    manager.data = {
        "device_consumption_water": [{"stat_consumption": "phyn:device_0_toilet"}],
    }
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_0"]) is None
    await coverage.entry._async_process_on_unload(hass)
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_1"]) is None
    await coverage.async_check()
    assert registry.async_get_issue(DOMAIN, coverage._issue_ids["home_1"]) is None


async def test_disabled_never_reads_energy_and_failed_read_does_not_guess(
    hass, coverage, monkeypatch, caplog
):
    getter = AsyncMock(side_effect=OSError("unreadable preferences"))
    monkeypatch.setattr("custom_components.phyn.energy_coverage.async_get_manager", getter)
    hass.config_entries.async_update_entry(coverage.entry, options={})
    await coverage.async_check()
    getter.assert_not_awaited()
    hass.config_entries.async_update_entry(
        coverage.entry, options={CONF_ENERGY_COVERAGE: True}
    )
    await coverage.async_check()
    assert "Could not read Energy preferences" in caplog.text
    registry = ir.async_get(hass)
    assert all(
        registry.async_get_issue(DOMAIN, issue_id) is None
        for issue_id in coverage._issue_ids.values()
    )


async def test_options_show_usage_and_preserve_existing_settings(hass, coverage):
    hass.config_entries.async_update_entry(coverage.entry, options={
        CONF_EXCLUDED_ALERT_TYPES: ["battery"],
        CONF_ENERGY_COVERAGE_EXCLUDED: ["phyn:previously_omitted"],
        "future_option": True,
    })
    hass.data[DOMAIN] = {"coordinator": SimpleNamespace(devices=coverage.devices)}
    flow = PhynOptionsFlow(coverage.entry)
    flow.hass = hass
    form = await flow.async_step_init()
    schema = form["data_schema"]
    defaults = schema({})
    assert defaults[CONF_ENERGY_COVERAGE] is False
    assert defaults[CONF_EXCLUDED_ALERT_TYPES] == ["battery"]
    submitted = schema({
        CONF_ENERGY_COVERAGE: True,
        CONF_ENERGY_COVERAGE_EXCLUDED: [
            "phyn:previously_omitted", "phyn:device_0_toilet",
        ],
    })
    result = await flow.async_step_init(submitted)
    assert result["data"]["future_option"] is True
    assert result["data"][CONF_EXCLUDED_ALERT_TYPES] == ["battery"]
    assert result["data"][CONF_ENERGY_COVERAGE] is True
