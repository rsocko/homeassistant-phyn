"""Regression tests for restoring fixture state before first use."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.phyn.devices.pp import PhynPlusDevice
from custom_components.phyn.fixture_statistics import PhynFixtureStatisticsImporter


@pytest.mark.asyncio
async def test_initialize_does_not_reload_over_current_state(hass):
    """Device setup must not reload a baseline already restored during refresh."""
    importer = PhynFixtureStatisticsImporter(hass, "device_1")
    importer._store = SimpleNamespace(
        async_load=AsyncMock(return_value={
            "fixture_sums": {"Kitchen": 10.0},
            "last_event_ms": 1000,
        })
    )

    await importer.async_initialize()
    importer._state.fixture_sums["Kitchen"] = 12.0
    importer._state.last_event_ms = 2000
    await importer.async_initialize()

    importer._store.async_load.assert_awaited_once()
    assert importer._state.fixture_sums == {"Kitchen": 12.0}
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
