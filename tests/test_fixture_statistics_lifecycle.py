"""Regression tests for restoring fixture state before first use."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.phyn.devices.pp import PhynPlusDevice
from custom_components.phyn.fixture_statistics import PhynFixtureStatisticsImporter
from custom_components.phyn import async_unload_entry
from custom_components.phyn.const import CLIENT, DOMAIN


@pytest.mark.asyncio
async def test_initialize_does_not_reload_over_current_state(hass):
    """Device setup must not reload a baseline already restored during refresh."""
    importer = PhynFixtureStatisticsImporter(hass, "device_1")
    importer._store = SimpleNamespace(
        async_load=AsyncMock(return_value={
            "schema": 2, "events": {}, "fixture_ids": {}, "rows": {}, "pending": None,
        })
    )

    await importer.async_initialize()
    importer._state.events["event"] = {"fixture": "Kitchen", "end_ms": 2000, "volume": 12.0}
    await importer.async_initialize()

    importer._store.async_load.assert_awaited_once()
    assert importer._state.events["event"]["volume"] == 12.0
    assert importer.current_checkpoint_ms() == 2000


@pytest.mark.asyncio
async def test_concurrent_initialization_loads_once(hass):
    """Concurrent first callers share one initialization."""
    importer = PhynFixtureStatisticsImporter(hass, "device_1")
    importer._store = SimpleNamespace(async_load=AsyncMock(return_value=None))

    await asyncio.gather(importer.async_initialize(), importer.async_initialize())

    importer._store.async_load.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_initialization_is_retryable(hass):
    """A failed storage read must never mark the importer initialized."""
    importer = PhynFixtureStatisticsImporter(hass, "device_1")
    importer._store = SimpleNamespace(
        async_load=AsyncMock(side_effect=[OSError("storage unavailable"), None])
    )

    with pytest.raises(OSError, match="storage unavailable"):
        await importer.async_initialize()
    await importer.async_initialize()

    assert importer._store.async_load.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_device_restores_before_computing_fetch_start(hass, dry_run):
    """The first refresh/manual preview must use the persisted checkpoint."""
    order = []
    restored = False
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)

    async def initialize():
        nonlocal restored
        order.append("restore")
        restored = True

    def next_fetch_start(_end):
        assert restored, "checkpoint consulted before storage restore"
        order.append("checkpoint")
        return start

    fetch = AsyncMock(return_value=[])
    device = PhynPlusDevice(
        SimpleNamespace(
            hass=hass,
            api_client=SimpleNamespace(device=SimpleNamespace(
                get_water_usage_events=fetch
            )),
        ),
        "home_1", "device_1", "PP1",
    )
    device._fixture_stats_importer = SimpleNamespace(
        async_initialize=AsyncMock(side_effect=initialize),
        next_fetch_start=next_fetch_start,
        async_import_events=AsyncMock(return_value={"imported_rows": 0}),
        async_preview_import_events=AsyncMock(return_value={"imported_rows": 0}),
    )

    await device.async_import_fixture_statistics(to_datetime=end, dry_run=dry_run)

    assert order == ["restore", "checkpoint"]
    fetch.assert_awaited_once_with(
        "device_1",
        from_ts=int(start.timestamp() * 1000),
        to_ts=int(end.timestamp() * 1000),
    )


@pytest.mark.asyncio
async def test_manual_imports_do_not_overlap(hass):
    """Serialize the entire fetch-and-import operation for each device."""
    active = 0
    peak = 0

    async def fetch(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return []

    device = PhynPlusDevice(
        SimpleNamespace(
            hass=hass,
            api_client=SimpleNamespace(device=SimpleNamespace(
                get_water_usage_events=fetch
            )),
        ),
        "home_1", "device_1", "PP1",
    )
    device._fixture_stats_importer = SimpleNamespace(
        async_initialize=AsyncMock(),
        async_import_events=AsyncMock(return_value={"imported_rows": 0}),
    )
    window = {
        "from_datetime": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "to_datetime": datetime(2026, 9, 2, tzinfo=timezone.utc),
    }

    await asyncio.gather(
        device.async_import_fixture_statistics(**window),
        device.async_import_fixture_statistics(**window),
    )

    assert peak == 1


@pytest.mark.asyncio
async def test_history_work_does_not_block_sensor_refresh_and_stops_on_unload(hass, monkeypatch):
    device = PhynPlusDevice(
        SimpleNamespace(hass=hass, api_client=SimpleNamespace()),
        "home_1", "device_1", "PP1",
    )
    for name in (
        "_update_device_state", "_update_alerts", "_update_alert_events",
        "_update_autoshutoff", "_update_device_preferences", "_update_consumption_data",
        "_update_device_health_tests", "_update_firmware_information",
    ):
        monkeypatch.setattr(device, name, AsyncMock())
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def history():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(device, "_update_fixture_statistics", history)
    await device.async_update_data()
    assert device._update_count == 1
    await asyncio.wait_for(started.wait(), timeout=1)
    await device.async_shutdown()
    assert stopped.is_set()
    assert device._fixture_task is None
    device._update_count = 15
    await device.async_update_data()
    assert device._fixture_task is None
    with pytest.raises(HomeAssistantError, match="stopping"):
        await device.async_import_fixture_statistics()


@pytest.mark.asyncio
@pytest.mark.parametrize("unload_ok", [False, True])
async def test_fixture_shutdown_only_after_successful_platform_unload(hass, monkeypatch, unload_ok):
    coordinator = SimpleNamespace(async_shutdown=AsyncMock())
    hass.data[DOMAIN] = {CLIENT: object(), "coordinator": coordinator}
    monkeypatch.setattr(
        "custom_components.phyn._async_disconnect_mqtt", AsyncMock()
    )
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=unload_ok)
    )

    assert await async_unload_entry(hass, SimpleNamespace(entry_id="test")) is unload_ok

    if unload_ok:
        coordinator.async_shutdown.assert_awaited_once()
        assert "coordinator" not in hass.data[DOMAIN]
    else:
        coordinator.async_shutdown.assert_not_awaited()
        assert hass.data[DOMAIN]["coordinator"] is coordinator
