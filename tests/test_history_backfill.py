"""Native device backfill controls and owned background-job lifecycle."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phyn.backfill import PhynHistoryBackfill
from custom_components.phyn.button import PhynHistoryBackfillButton
from custom_components.phyn.const import DOMAIN
from custom_components.phyn.devices.pp import PhynPlusDevice
from custom_components.phyn.number import PhynHistoryBackfillDays
from custom_components.phyn.sensor import PhynHistoryBackfillStatus


RESULT = {"imported_rows": 4, "events_fetched": 0, "corrections_detected": 1}


@pytest.fixture(autouse=True)
def no_chunk_wait(monkeypatch):
    monkeypatch.setattr("custom_components.phyn.history_import.sleep", AsyncMock())


def make_device(hass, device_id="monitor_a"):
    device = PhynPlusDevice(
        SimpleNamespace(
            hass=hass,
            api_client=SimpleNamespace(device=SimpleNamespace(
                get_water_usage_events=AsyncMock(return_value=[])
            )),
        ),
        "home", device_id, "PP1",
    )
    device._fixture_stats_importer = SimpleNamespace(
        async_initialize=AsyncMock(),
        async_import_events=AsyncMock(return_value=RESULT),
    )
    return device


@pytest.fixture
async def device(hass, hass_storage):
    device = make_device(hass)
    yield device
    await device.async_shutdown()


async def test_days_setting_persists_without_fetch_or_import(device):
    number = PhynHistoryBackfillDays(device)
    assert number.native_value == 7
    assert number.native_min_value == 1
    assert number.native_max_value == 365
    assert number.native_step == 1
    await number.async_set_native_value(30)
    assert number.native_value == 30
    device.coordinator.api_client.device.get_water_usage_events.assert_not_awaited()
    device._fixture_stats_importer.async_import_events.assert_not_awaited()

    restored = PhynHistoryBackfill(device)
    await restored.async_initialize()
    assert restored.data == {"days": 30, "status": "idle"}


@pytest.mark.parametrize("days", [0, -1, 366, 1.5, float("nan"), float("inf"), True])
async def test_invalid_days_do_not_modify_settings(device, days):
    with pytest.raises(HomeAssistantError, match="whole number"):
        await device.history_backfill.async_set_days(days)
    assert device.history_backfill.data == {"days": 7, "status": "idle"}


@pytest.mark.parametrize("days", [1, 365])
async def test_day_limits_are_accepted(device, days):
    await device.history_backfill.async_set_days(days)
    assert device.history_backfill.data["days"] == days


async def test_backfill_uses_exact_elapsed_utc_range_and_persists_success(device):
    backfill = device.history_backfill
    await backfill.async_set_days(7)
    before = datetime.now(timezone.utc)
    await PhynHistoryBackfillButton(device).async_press()
    task = backfill._task
    assert task is not None
    assert backfill.data["status"] == "running"
    await task

    data = backfill.data
    assert data["status"] == "completed"
    assert data["requested_days"] == 7
    end = datetime.fromisoformat(data["end_datetime"])
    start = datetime.fromisoformat(data["start_datetime"])
    assert before <= end <= datetime.now(timezone.utc)
    assert start == end - timedelta(days=7)
    assert end.utcoffset() == timedelta(0)
    device.coordinator.api_client.device.get_water_usage_events.assert_awaited_once_with(
        device.id, from_ts=int(start.timestamp() * 1000), to_ts=int(end.timestamp() * 1000)
    )
    device._fixture_stats_importer.async_import_events.assert_awaited_once_with([])
    assert data["last_successful_at"] == data["finished_at"]
    for key, value in RESULT.items():
        assert data[key] == value
    assert backfill.running is False

    restored = PhynHistoryBackfill(device)
    await restored.async_initialize()
    assert restored.data == data


async def test_empty_import_completes_without_claiming_available_history(device):
    device._fixture_stats_importer.async_import_events.return_value = {
        "imported_rows": 0, "events_fetched": 0, "corrections_detected": 0,
    }
    await device.history_backfill.async_start()
    await device.history_backfill._task
    data = device.history_backfill.data
    assert data["status"] == "completed"
    assert data["events_fetched"] == 0
    assert "available_days" not in data


async def test_failure_preserves_last_success_and_can_retry(device, caplog):
    backfill = device.history_backfill
    await backfill.async_start()
    await backfill._task
    last_success = backfill.data["last_successful_at"]
    fetch = device.coordinator.api_client.device.get_water_usage_events
    fetch.side_effect = HomeAssistantError("API unavailable")
    await backfill.async_start()
    await backfill._task
    assert backfill.data["status"] == "failed"
    assert backfill.data["last_successful_at"] == last_success
    assert backfill.data["imported_rows"] == 0
    assert "API unavailable" in caplog.text
    assert backfill.running is False
    fetch.side_effect = None
    await backfill.async_start()
    await backfill._task
    assert backfill.data["status"] == "completed"
    assert backfill.data["error"] is None


async def test_duplicate_presses_rejected_and_days_snapshot_is_fixed(device):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await release.wait()
        return []

    device.coordinator.api_client.device.get_water_usage_events.side_effect = fetch
    backfill = device.history_backfill
    button = PhynHistoryBackfillButton(device)
    await button.async_press()
    task = backfill._task
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not button.available
    with pytest.raises(HomeAssistantError, match="already running"):
        await button.async_press()
    await backfill.async_set_days(14)
    assert backfill.data["requested_days"] == 7
    assert backfill.data["days"] == 14
    release.set()
    await task
    assert button.available
    assert device.coordinator.api_client.device.get_water_usage_events.await_count == 1


async def test_press_during_request_save_is_rejected_and_unload_stops_it(device):
    backfill = device.history_backfill
    await backfill.async_initialize()
    save = backfill._store.async_save
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_save(data):
        entered.set()
        await release.wait()
        await save(data)

    backfill._store.async_save = blocked_save
    press = asyncio.create_task(backfill.async_start())
    await asyncio.wait_for(entered.wait(), timeout=1)
    with pytest.raises(HomeAssistantError, match="already running"):
        await backfill.async_start()
    shutdown = asyncio.create_task(device.async_shutdown())
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(HomeAssistantError, match="stopping"):
        await press
    await shutdown
    assert backfill.data["status"] == "interrupted"
    device.coordinator.api_client.device.get_water_usage_events.assert_not_awaited()


async def test_manual_import_blocks_button_without_changing_backfill_outcome(device):
    async with device._fixture_import_lock:
        assert not PhynHistoryBackfillButton(device).available
        with pytest.raises(HomeAssistantError, match="already running"):
            await device.history_backfill.async_start()
    assert device.history_backfill.data["status"] == "idle"
    assert PhynHistoryBackfillButton(device).available


async def test_unload_cancels_job_and_restores_interrupted_state(device):
    entered = asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    device.coordinator.api_client.device.get_water_usage_events.side_effect = fetch
    await device.history_backfill.async_start()
    await asyncio.wait_for(entered.wait(), timeout=1)
    await device.async_shutdown()
    assert device.history_backfill._task is None
    assert not device.fixture_import_running
    assert device.history_backfill.data["status"] == "interrupted"
    assert not PhynHistoryBackfillButton(device).available
    assert not PhynHistoryBackfillDays(device).available
    with pytest.raises(HomeAssistantError, match="stopping"):
        await device.history_backfill.async_start()
    device._fixture_stats_importer.async_import_events.assert_not_awaited()
    restored = PhynHistoryBackfill(device)
    await restored.async_initialize()
    assert restored.data["status"] == "interrupted"
    assert "last_successful_at" not in restored.data


async def test_cancel_before_background_task_starts_is_interrupted(device):
    await device.history_backfill.async_start()
    await device.async_shutdown()
    assert device.history_backfill.data["status"] == "interrupted"


async def test_restore_running_job_does_not_auto_import(device):
    await device.history_backfill._store.async_save({
        "days": 90, "status": "running", "last_successful_at": "2026-09-01T00:00:00+00:00",
    })
    await device.history_backfill.async_initialize()
    assert device.history_backfill.data["status"] == "interrupted"
    assert device.history_backfill.data["days"] == 90
    assert device.history_backfill.data["last_successful_at"] == "2026-09-01T00:00:00+00:00"
    assert device.history_backfill._task is None
    device.coordinator.api_client.device.get_water_usage_events.assert_not_awaited()


@pytest.mark.parametrize("stored", [
    {"days": True, "status": "idle"},
    {"days": 366, "status": "idle"},
    {"days": 7, "status": "unexpected"},
    {},
])
async def test_invalid_saved_settings_fail_explicitly(device, stored):
    await device.history_backfill._store.async_save(stored)
    with pytest.raises(HomeAssistantError, match="Invalid saved"):
        await device.history_backfill.async_initialize()
    assert device.history_backfill._initialized is False


async def test_save_failure_does_not_start_import_or_change_days(device, monkeypatch):
    await device.history_backfill.async_initialize()
    monkeypatch.setattr(
        device.history_backfill._store, "async_save", AsyncMock(side_effect=OSError("disk full"))
    )
    with pytest.raises(HomeAssistantError, match="save Phyn backfill days"):
        await device.history_backfill.async_set_days(60)
    with pytest.raises(HomeAssistantError, match="save Phyn backfill request"):
        await device.history_backfill.async_start()
    assert device.history_backfill.data == {"days": 7, "status": "idle"}
    device.coordinator.api_client.device.get_water_usage_events.assert_not_awaited()


async def test_devices_have_independent_jobs_and_persisted_settings(hass, device):
    other = make_device(hass, "monitor_b")
    await device.history_backfill.async_set_days(14)
    await other.history_backfill.async_set_days(30)
    await device.history_backfill.async_start()
    await other.history_backfill.async_start()
    await asyncio.gather(device.history_backfill._task, other.history_backfill._task)
    assert device.history_backfill.data["requested_days"] == 14
    assert other.history_backfill.data["requested_days"] == 30
    restored = PhynHistoryBackfill(other)
    await restored.async_initialize()
    assert restored.data["days"] == 30
    await other.async_shutdown()


async def test_native_platforms_device_link_and_actions(hass, device, monkeypatch):
    """Real entity services, registry, state updates, and listener unload."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    device.entities = []
    hass.data[DOMAIN] = {"coordinator": SimpleNamespace(
        devices=[device, SimpleNamespace(entities=[])]
    )}
    monkeypatch.setattr("custom_components.phyn.sensor.async_setup_inventory", AsyncMock())
    await hass.config_entries.async_forward_entry_setups(entry, ["button", "number", "sensor"])
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    number_id = registry.async_get_entity_id("number", DOMAIN, "monitor_a_history_backfill_days")
    button_id = registry.async_get_entity_id("button", DOMAIN, "monitor_a_history_backfill")
    status_id = registry.async_get_entity_id("sensor", DOMAIN, "monitor_a_history_backfill_status")
    assert number_id and button_id and status_id
    entries = [registry.async_get(entity_id) for entity_id in (number_id, button_id, status_id)]
    assert len({entry.device_id for entry in entries}) == 1
    assert entries[0].device_id is not None
    assert all(entry.disabled_by is None for entry in entries)
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 3
    assert hass.states.get(status_id).state == "idle"
    assert "state_class" not in hass.states.get(status_id).attributes
    assert hass.states.get(number_id).state == "7"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 14}, blocking=True
    )
    device.coordinator.api_client.device.get_water_usage_events.assert_not_awaited()
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await release.wait()
        return []

    device.coordinator.api_client.device.get_water_usage_events.side_effect = fetch
    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    task = device.history_backfill._task
    await asyncio.wait_for(entered.wait(), timeout=1)
    await hass.async_block_till_done()
    assert hass.states.get(status_id).state == "running"
    assert hass.states.get(button_id).state == "unavailable"
    release.set()
    await task
    await hass.async_block_till_done()
    state = hass.states.get(status_id)
    assert state.state == "completed"
    assert state.attributes["requested_days"] == 14
    assert state.attributes["events_fetched"] == 0
    assert state.attributes["chunks_completed"] == state.attributes["chunks_total"] == 2
    assert "unit_of_measurement" not in state.attributes
    assert hass.states.get(button_id).state != "unavailable"
    await hass.config_entries.async_unload_platforms(entry, ["button", "number", "sensor"])
    await entry._async_process_on_unload(hass)
    unloaded = hass.states.get(status_id)
    assert unloaded.state == "unavailable"
    assert unloaded.attributes["restored"] is True


async def test_status_is_not_meter_and_controls_ignore_monitor_online_state(device):
    assert not device.available
    status = PhynHistoryBackfillStatus(device)
    assert status.available
    assert status.state_class is None
    assert status.native_unit_of_measurement is None
    assert PhynHistoryBackfillButton(device).available
    assert PhynHistoryBackfillDays(device).available
