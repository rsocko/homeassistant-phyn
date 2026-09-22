"""Real in-memory Recorder contracts used by fixture reconciliation.

The HA plugin's recorder_mock fixture runs a Recorder against a test database;
the import, clear, and query APIs below are not replaced with mocks.
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_recorder_block_till_done,
)
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    StatisticMetaData,
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_conversion import VolumeConverter

from custom_components.phyn.fixture_statistics import (
    PhynFixtureStatisticsImporter,
    fixture_statistic_id,
)
from custom_components.phyn import fixture_statistics as fixture_module


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url):
    """Configure the test database before hass, independent of argument order."""


def _metadata(statistic_id):
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name="Phyn test fixture",
        source="phyn",
        statistic_id=statistic_id,
        unit_class=VolumeConverter.UNIT_CLASS,
        unit_of_measurement=UnitOfVolume.GALLONS,
    )


async def _read_rows(hass, recorder, start, statistic_id):
    await async_recorder_block_till_done(hass)
    result = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return result.get(statistic_id, [])


@pytest.mark.asyncio
async def test_absolute_upsert_preserves_older_rows(hass, recorder_mock):
    """Rewriting a managed hour must not delete or add to older cumulative rows."""
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    later = start + timedelta(hours=1)
    statistic_id = "phyn:test_device_kitchen"
    metadata = _metadata(statistic_id)
    async_add_external_statistics(
        hass, metadata,
        [
            {"start": start, "sum": 10.0, "state": 10.0},
            {"start": later, "sum": 12.0, "state": 12.0},
        ],
    )
    await async_recorder_block_till_done(hass)

    replacement = [{"start": later, "sum": 13.0, "state": 13.0}]
    async_add_external_statistics(hass, metadata, replacement)
    first = await _read_rows(hass, recorder_mock, start, statistic_id)
    async_add_external_statistics(hass, metadata, replacement)
    second = await _read_rows(hass, recorder_mock, start, statistic_id)

    assert [row["sum"] for row in first] == [10.0, 13.0]
    assert [row["sum"] for row in second] == [10.0, 13.0]


@pytest.mark.asyncio
async def test_recorder_clear_is_callback_and_deletes_whole_series(hass, recorder_mock):
    """Document why this API cannot implement an ordinary short-window correction."""
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    statistic_id = "phyn:test_device_clear_contract"
    async_add_external_statistics(
        hass, _metadata(statistic_id),
        [
            {"start": start, "sum": 10.0, "state": 10.0},
            {"start": start + timedelta(days=1), "sum": 12.0, "state": 12.0},
        ],
    )
    assert len(await _read_rows(hass, recorder_mock, start, statistic_id)) == 2
    completed = asyncio.Event()

    result = recorder_mock.async_clear_statistics(
        [statistic_id],
        on_done=lambda: hass.loop.call_soon_threadsafe(completed.set),
    )
    await async_recorder_block_till_done(hass)
    await asyncio.wait_for(completed.wait(), timeout=5)

    assert result is None
    assert await _read_rows(hass, recorder_mock, start, statistic_id) == []


@pytest.mark.asyncio
async def test_importer_state_survives_restore_and_same_hour_update(hass, recorder_mock):
    """Exercise actual storage, Recorder metadata, and cumulative same-hour writes."""
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    timestamp = int(start.timestamp() * 1000)
    importer = PhynFixtureStatisticsImporter(hass, "test_device")
    await importer.async_initialize()
    await importer.async_import_events([{
        "id": "event_1",
        "close_edge_timestamp": timestamp + 1000,
        "total_flow": 1.0,
        "user_fixture_label": "Kitchen",
    }])
    await async_recorder_block_till_done(hass)
    restored = PhynFixtureStatisticsImporter(hass, "test_device")
    await restored.async_initialize()

    await restored.async_import_events([{
        "id": "event_2",
        "close_edge_timestamp": timestamp + 2000,
        "total_flow": 2.0,
        "user_fixture_label": "Kitchen",
    }])
    rows = await _read_rows(
        hass, recorder_mock, start, fixture_statistic_id("test_device", "Kitchen")
    )

    assert len(rows) == 1
    assert rows[0]["sum"] == 3.0
    assert restored.current_checkpoint_ms() == timestamp + 2000


def _event(key, time, volume, label="Kitchen"):
    return {
        "id": key,
        "close_edge_timestamp": int(time.timestamp() * 1000) + 1000,
        "total_flow": volume,
        "user_fixture_label": label,
    }


@pytest.mark.asyncio
async def test_reconciliation_preserves_year_old_history_and_zeroes_old_fixture(hass, recorder_mock):
    importer = PhynFixtureStatisticsImporter(hass, "device")
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    recent = datetime(2026, 9, 1, tzinfo=timezone.utc)
    await importer.async_import_events([
        _event("old", old, 10), _event("correct", recent, 5, "Bath"),
        _event("later", recent + timedelta(hours=1), 2),
    ])
    old_rows = await _read_rows(
        hass, recorder_mock, old - timedelta(hours=1), fixture_statistic_id("device", "Kitchen")
    )

    result = await importer.async_force_reimport_events([_event("correct", recent, 3)])

    kitchen = await _read_rows(
        hass, recorder_mock, old - timedelta(hours=1), fixture_statistic_id("device", "Kitchen")
    )
    bath = await _read_rows(
        hass, recorder_mock, recent - timedelta(hours=1), fixture_statistic_id("device", "Bath")
    )
    assert [row["sum"] for row in kitchen] == [0, 10, 13, 15]
    assert kitchen[:2] == old_rows[:2]
    assert [row["sum"] for row in bath] == [0, 0]
    assert result["cleared_statistic_ids"] == 0
    assert result["corrections_detected"] == 1
    assert len(importer._state.events) == 3


@pytest.mark.asyncio
async def test_earlier_backfill_and_duplicate_same_timestamp_ids(hass, recorder_mock):
    importer = PhynFixtureStatisticsImporter(hass, "device")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    await importer.async_import_events([_event("one", start, 3)])
    await importer.async_import_events([
        _event("earlier", start - timedelta(hours=2), 2),
        _event("two", start, 4), _event("two", start, 4),
    ])
    rows = await _read_rows(
        hass, recorder_mock, start - timedelta(hours=3), fixture_statistic_id("device", "Kitchen")
    )
    assert [row["sum"] for row in rows] == [0, 2, 2, 9]
    assert len(importer._state.events) == 3
    before = await importer._store.async_load()
    result = await importer.async_import_events([_event("two", start, 4)])
    assert result["imported_rows"] == 0
    assert await importer._store.async_load() == before


@pytest.mark.asyncio
async def test_preview_and_empty_force_do_not_change_history(hass, recorder_mock):
    importer = PhynFixtureStatisticsImporter(hass, "device")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    await importer.async_import_events([_event("one", start, 3)])
    before = deepcopy(await importer._store.async_load())
    result = await importer.async_preview_import_events(
        [_event("one", start, 10, "Bath")], force_reimport=True
    )
    assert result["corrections_detected"] == 1
    assert result["cleared_statistic_ids"] == 0
    assert await importer._store.async_load() == before
    await importer.async_force_reimport_events([])
    assert await importer._store.async_load() == before
    assert await _read_rows(
        hass, recorder_mock, start, fixture_statistic_id("device", "Bath")
    ) == []


@pytest.mark.asyncio
async def test_partial_recorder_failure_recovers_absolute_plan(hass, recorder_mock, monkeypatch):
    importer = PhynFixtureStatisticsImporter(hass, "device")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    original = fixture_module.async_add_external_statistics
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HomeAssistantError("injected second-series failure")
        return original(*args)

    monkeypatch.setattr(fixture_module, "async_add_external_statistics", fail_second)
    with pytest.raises(HomeAssistantError, match="injected"):
        await importer.async_import_events([
            _event("one", start, 3), _event("two", start, 4, "Bath"),
        ])
    assert importer.current_checkpoint_ms() == 0
    assert (await importer._store.async_load())["pending"] is not None
    await async_recorder_block_till_done(hass)
    monkeypatch.setattr(fixture_module, "async_add_external_statistics", original)

    restored = PhynFixtureStatisticsImporter(hass, "device")
    await restored.async_import_events([])

    for label, expected in (("Kitchen", 3), ("Bath", 4)):
        rows = await _read_rows(hass, recorder_mock, start, fixture_statistic_id("device", label))
        assert [row["sum"] for row in rows] == [expected]
    assert len(restored._state.events) == 2
    assert (await restored._store.async_load())["pending"] is None


@pytest.mark.asyncio
async def test_cancelled_write_plan_recovers_after_restart(hass, recorder_mock, monkeypatch):
    importer = PhynFixtureStatisticsImporter(hass, "device")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    original = importer._matches
    calls = 0

    async def cancel_verification(rows, mapping):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise asyncio.CancelledError
        return await original(rows, mapping)

    monkeypatch.setattr(importer, "_matches", cancel_verification)
    with pytest.raises(asyncio.CancelledError):
        await importer.async_import_events([_event("one", start, 3)])
    assert importer.current_checkpoint_ms() == 0
    assert (await importer._store.async_load())["pending"] is not None
    await async_recorder_block_till_done(hass)
    restored = PhynFixtureStatisticsImporter(hass, "device")
    await restored.async_import_events([])
    assert len(restored._state.events) == 1
    assert (await restored._store.async_load())["pending"] is None
    rows = await _read_rows(hass, recorder_mock, start, fixture_statistic_id("device", "Kitchen"))
    assert [row["sum"] for row in rows] == [3]


@pytest.mark.asyncio
async def test_readback_mismatch_does_not_advance_checkpoint(hass, recorder_mock, monkeypatch):
    importer = PhynFixtureStatisticsImporter(hass, "device")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)

    async def reject_nonempty(rows, mapping):
        return not rows

    monkeypatch.setattr(importer, "_matches", reject_nonempty)
    monkeypatch.setattr(fixture_module, "VERIFY_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HomeAssistantError, match="verification"):
        await importer.async_import_events([_event("one", start, 3)])
    assert importer.current_checkpoint_ms() == 0
    assert (await importer._store.async_load())["pending"] is not None
    await async_recorder_block_till_done(hass)
    restored = PhynFixtureStatisticsImporter(hass, "device")
    await restored.async_import_events([])
    assert restored.current_checkpoint_ms() > 0


@pytest.mark.asyncio
async def test_final_state_save_failure_keeps_recoverable_journal(hass, recorder_mock, monkeypatch):
    importer = PhynFixtureStatisticsImporter(hass, "device")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    original = importer._store.async_save
    calls = 0

    async def fail_final(data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected storage failure")
        await original(data)

    monkeypatch.setattr(importer._store, "async_save", fail_final)
    with pytest.raises(OSError, match="injected"):
        await importer.async_import_events([_event("one", start, 3)])
    assert importer.current_checkpoint_ms() == 0
    restored = PhynFixtureStatisticsImporter(hass, "device")
    await restored.async_import_events([])
    assert len(restored._state.events) == 1
    assert (await restored._store.async_load())["pending"] is None


@pytest.mark.asyncio
async def test_existing_statistics_without_evidence_are_not_adopted(hass, recorder_mock):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    legacy_id = "phyn:device_kitchen_water"
    async_add_external_statistics(
        hass, _metadata(legacy_id), [{"start": start, "sum": 100.0, "state": 100.0}]
    )
    await async_recorder_block_till_done(hass)
    importer = PhynFixtureStatisticsImporter(hass, "device")
    with pytest.raises(HomeAssistantError, match="no verified event evidence"):
        await importer.async_force_reimport_events([_event("one", start, 3)])
    assert await importer._store.async_load() is None
    rows = await _read_rows(hass, recorder_mock, start, legacy_id)
    assert [row["sum"] for row in rows] == [100]
