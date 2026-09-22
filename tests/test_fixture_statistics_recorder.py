"""Real in-memory Recorder contracts used by fixture reconciliation.

The HA plugin's recorder_mock fixture runs a Recorder against a test database;
the import, clear, and query APIs below are not replaced with mocks.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    StatisticMetaData,
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.util.unit_conversion import VolumeConverter

from custom_components.phyn.fixture_statistics import (
    PhynFixtureStatisticsImporter,
    fixture_statistic_id,
)


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
    await recorder.async_block_till_done()
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
    await recorder_mock.async_block_till_done()

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
    await recorder_mock.async_block_till_done()
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
    await recorder_mock.async_block_till_done()
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
