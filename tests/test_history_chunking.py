"""Chunked fetch equivalence using actual accepted evidence and HA Recorder."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_recorder_block_till_done,
)

from custom_components.phyn.devices.pp import PhynPlusDevice
from custom_components.phyn.fixture_statistics import PhynFixtureStatisticsImporter
from custom_components.phyn.history_import import CHUNK_MS, history_windows

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
DAY_MS = 24 * 60 * 60 * 1000
END_MS = START_MS + 31 * DAY_MS


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url):
    """Configure Recorder before hass, independent of fixture argument order."""


@pytest.fixture(autouse=True)
def chunk_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.phyn.history_import.sleep", sleep)
    return sleep


def event(identifier, offset, volume=1.0, label="Toilet", *, open_offset=None):
    return {
        "id": identifier,
        "open_edge_timestamp": START_MS + (offset - 1000 if open_offset is None else open_offset),
        "close_edge_timestamp": START_MS + offset,
        "total_flow": volume,
        "latest_suggested_fixtures_result": {
            "suggested_fixtures": [{
                "fixture_id": {"Toilet": 8, "Sink": 9, "Other": 10}[label],
                "fixture_name": label, "confidence_score": 1.0,
            }]
        },
    }


def make_device(hass, fetch, identifier="chunk_monitor"):
    return PhynPlusDevice(
        SimpleNamespace(
            hass=hass,
            api_client=SimpleNamespace(device=SimpleNamespace(get_water_usage_events=fetch)),
        ),
        "home", identifier, "PP1",
    )


async def recorder_rows(hass, recorder, importer):
    await async_recorder_block_till_done(hass)
    rows = await recorder.async_add_executor_job(
        statistics_during_period, hass, START - timedelta(days=2), None,
        set(importer._state.fixture_ids.values()), "hour", None, {"state", "sum"},
    )
    return {
        label: [(row["start"], row["state"], row["sum"]) for row in rows.get(identifier, [])]
        for label, identifier in importer._state.fixture_ids.items()
    }


@pytest.mark.parametrize("days", [1, 7, 8, 14, 31, 365, 366, 730])
def test_windows_cover_whole_range_with_only_one_ms_internal_overlap(days):
    end = START_MS + days * DAY_MS
    windows = list(history_windows(START_MS, end))
    assert windows[0][0] == START_MS
    assert windows[-1][1] == end
    assert len(windows) == (days + 6) // 7
    for index, (left, right) in enumerate(windows):
        assert left < right
        assert right - left <= CHUNK_MS + (1 if index else 0)
        if index:
            assert left == windows[index - 1][1] - 1
    assert sum(right - left for left, right in windows) - (len(windows) - 1) == end - START_MS


def test_windows_use_elapsed_utc_across_dst():
    local_start = datetime(2026, 3, 1, tzinfo=ZoneInfo("America/New_York"))
    local_end = datetime(2026, 3, 16, tzinfo=ZoneInfo("America/New_York"))
    start, end = int(local_start.timestamp() * 1000), int(local_end.timestamp() * 1000)
    windows = list(history_windows(start, end))
    assert len(windows) == 3
    assert windows[0][1] - start == CHUNK_MS
    assert end - start == 15 * DAY_MS - 60 * 60 * 1000
    assert windows[-1][1] == end


@pytest.mark.parametrize("offset", [0, -1])
def test_empty_or_reversed_ms_range_is_rejected(offset):
    with pytest.raises(HomeAssistantError, match="positive millisecond"):
        list(history_windows(START_MS, START_MS + offset))


@pytest.mark.parametrize("selection", ["close_inclusive", "close_exclusive", "open", "overlap"])
async def test_chunked_matches_whole_ids_category_totals_and_every_recorder_row(
    hass, recorder_mock, selection, chunk_sleep
):
    """Synthetic responses force boundary duplication, long events and empty chunks."""
    events = [
        event("start", 0, 0.1, open_offset=0),
        event("before_boundary", CHUNK_MS - 1, 0.2),
        event("on_boundary", CHUNK_MS, 0.3),
        event("after_boundary", CHUNK_MS + 1, 0.4),
        event("same_timestamp_other_id", CHUNK_MS, 1.5, "Sink"),
        event("long_cross_boundary", 23 * DAY_MS, 6.2, "Other", open_offset=6 * DAY_MS),
        event("zero_volume", DAY_MS, 0.0, "Other"),
        event("last", 30 * DAY_MS, 2.1, "Sink"),
        event("overall_end", 31 * DAY_MS, 0.9, open_offset=31 * DAY_MS),
    ]
    calls = []

    async def fetch(device_id, *, from_ts, to_ts):
        calls.append((from_ts, to_ts))
        def included(item):
            opened, closed = item["open_edge_timestamp"], item["close_edge_timestamp"]
            if selection == "close_inclusive":
                return from_ts <= closed <= to_ts
            if selection == "close_exclusive":
                return from_ts < closed < to_ts
            if selection == "open":
                return from_ts <= opened < to_ts
            return opened < to_ts and closed >= from_ts
        found = [deepcopy(item) for item in reversed(events) if included(item)]
        # Identical duplicates within a response must also count only once.
        return found + found[:1]

    whole = await fetch("reference", from_ts=START_MS, to_ts=END_MS)
    calls.clear()
    reference = PhynFixtureStatisticsImporter(hass, "reference")
    await reference.async_import_events(whole)
    device = make_device(hass, fetch)
    progress = AsyncMock()
    result = await device.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=31),
        progress_callback=progress,
    )
    assert calls == list(history_windows(START_MS, END_MS))
    assert chunk_sleep.await_count == 4
    assert all(call.args == (1,) for call in chunk_sleep.await_args_list)
    assert result["chunks_completed"] == result["chunks_total"] == 5
    assert result["events_unique"] == len({item["id"] for item in whole})
    assert result["duplicate_events"] == result["events_fetched"] - result["events_unique"] > 0
    assert device._fixture_stats_importer._state.events == reference._state.events
    assert device._fixture_stats_importer._state.rows == reference._state.rows
    actual_rows = await recorder_rows(hass, recorder_mock, device._fixture_stats_importer)
    assert actual_rows == await recorder_rows(hass, recorder_mock, reference)
    for label, rows in actual_rows.items():
        volume = sum(
            Decimal(str(value["volume"])) for value in reference._state.events.values()
            if value["fixture"] == label
        )
        assert Decimal(str(rows[-1][2])) == volume
    assert progress.await_args_list[-1].args[0]["remaining_start_ms"] == END_MS

    repeated = await device.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=31),
    )
    assert repeated["imported_rows"] == 0
    assert await recorder_rows(hass, recorder_mock, device._fixture_stats_importer) == actual_rows
    forced = await device.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=31), force_reimport=True,
    )
    assert forced["force_reimport"] == 1
    assert forced["cleared_statistic_ids"] == 0
    assert await recorder_rows(hass, recorder_mock, device._fixture_stats_importer) == actual_rows
    await device.async_shutdown()


async def test_combined_dry_run_is_write_free_and_keeps_last_cross_chunk_observation(
    hass, recorder_mock, monkeypatch
):
    older = event("revised", DAY_MS, 9, "Sink")
    early = event("revised", DAY_MS, 2, "Toilet")
    latest = event("revised", 9 * DAY_MS, 3, "Other")
    unchanged = event("untouched", 0, 0.5, "Sink", open_offset=0)
    device = make_device(hass, AsyncMock(side_effect=[[early], [latest]]))
    importer = device._fixture_stats_importer
    await importer.async_import_events([older, unchanged])
    before = deepcopy(importer._state)
    before_store = await importer._store.async_load()
    before_rows = await recorder_rows(hass, recorder_mock, importer)
    expected = await importer.async_preview_import_events([latest])
    callback = AsyncMock()
    result = await device.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=14),
        dry_run=True, progress_callback=callback,
    )
    assert result["events_fetched"] == 2
    assert result["events_unique"] == 1
    assert result["duplicate_events"] == 1
    for key in ("imported_rows", "corrections_detected", "checkpoint_after_ms", "cached_events"):
        assert result[key] == expected[key]
    assert importer._state == before
    assert await importer._store.async_load() == before_store
    assert await recorder_rows(hass, recorder_mock, importer) == before_rows
    callback.assert_not_awaited()

    # Applying exactly the same observations revises rather than adds volume.
    device.coordinator.api_client.device.get_water_usage_events.side_effect = [[early], [latest]]
    await device.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=14),
    )
    assert importer._state.events["revised"] == {
        "fixture": "Other", "volume": 3.0, "end_ms": START_MS + 9 * DAY_MS,
    }
    assert importer._state.events["untouched"] == before.events["untouched"]
    rows = await recorder_rows(hass, recorder_mock, importer)
    assert rows["Other"][-1][2] == 3
    assert rows["Toilet"][-1][2] == 0
    assert rows["Sink"][-1][2] == 0.5
    await device.async_shutdown()


@pytest.mark.parametrize("dry_run", [False, True])
async def test_conflicting_duplicate_within_response_fails_without_next_fetch(
    hass, recorder_mock, dry_run
):
    response = [event("same", DAY_MS, 1), event("same", DAY_MS, 2)]
    fetch = AsyncMock(return_value=response)
    device = make_device(hass, fetch)
    with pytest.raises(HomeAssistantError, match="stopped after 0/3 chunks"):
        await device.async_import_fixture_statistics(
            from_datetime=START, to_datetime=START + timedelta(days=21), dry_run=dry_run,
        )
    assert fetch.await_count == 1
    assert not device._fixture_stats_importer._state.events
    assert not device._fixture_stats_importer._state.rows
    await device.async_shutdown()


async def test_failure_retains_verified_chunks_and_retry_recovers_pending_without_double_counting(
    hass, recorder_mock, monkeypatch
):
    responses = [
        [event("first", DAY_MS, 1)],
        [event("second", 9 * DAY_MS, 2)],
        [event("third", 17 * DAY_MS, 3)],
    ]
    fetch = AsyncMock(side_effect=deepcopy(responses))
    device = make_device(hass, fetch)
    importer = device._fixture_stats_importer
    save = importer._save

    async def fail_second_commit(state, pending):
        if pending is None and "second" in state.events:
            raise OSError("simulated commit failure after Recorder write")
        await save(state, pending)

    monkeypatch.setattr(importer, "_save", fail_second_commit)
    progress = AsyncMock()
    with pytest.raises(HomeAssistantError, match="stopped after 1/3 chunks"):
        await device.async_import_fixture_statistics(
            from_datetime=START, to_datetime=START + timedelta(days=21),
            progress_callback=progress,
        )
    assert fetch.await_count == 2
    assert set(importer._state.events) == {"first"}
    assert importer._pending is not None
    stored = await importer._store.async_load()
    assert stored["pending"] is not None
    assert progress.await_args_list[-1].args[0]["remaining_start_ms"] == START_MS + CHUNK_MS - 1
    await device.async_shutdown()

    retry_fetch = AsyncMock(side_effect=deepcopy(responses))
    restored = make_device(hass, retry_fetch)
    retry = await restored.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=21),
    )
    assert retry["chunks_completed"] == 3
    assert set(restored._fixture_stats_importer._state.events) == {"first", "second", "third"}
    assert restored._fixture_stats_importer._pending is None
    rows = await recorder_rows(hass, recorder_mock, restored._fixture_stats_importer)
    assert rows["Toilet"][-1][2] == 6
    assert (await restored._fixture_stats_importer._store.async_load())["pending"] is None
    await restored.async_shutdown()


async def test_empty_chunks_do_not_delete_existing_history(hass, recorder_mock):
    device = make_device(hass, AsyncMock(return_value=[]))
    importer = device._fixture_stats_importer
    await importer.async_import_events([event("old", DAY_MS, 5)])
    before = await recorder_rows(hass, recorder_mock, importer)
    result = await device.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=21), force_reimport=True,
    )
    assert result["chunks_completed"] == 3
    assert result["events_unique"] == result["events_fetched"] == result["imported_rows"] == 0
    assert await recorder_rows(hass, recorder_mock, importer) == before
    await device.async_shutdown()


async def test_cancellation_between_chunks_keeps_verified_progress(hass, recorder_mock):
    entered = asyncio.Event()
    calls = 0

    async def fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [event("first", DAY_MS, 2)]
        entered.set()
        await asyncio.Event().wait()

    device = make_device(hass, fetch)
    callback = AsyncMock()
    task = asyncio.create_task(device.async_import_fixture_statistics(
        from_datetime=START, to_datetime=START + timedelta(days=21),
        progress_callback=callback,
    ))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 2
    assert callback.await_args_list[-1].args[0]["chunks_completed"] == 1
    assert set(device._fixture_stats_importer._state.events) == {"first"}
    assert not device.fixture_import_running
    await device.async_shutdown()


async def test_progress_save_error_after_final_commit_does_not_report_empty_retry_range(
    hass, recorder_mock
):
    device = make_device(hass, AsyncMock(return_value=[event("first", DAY_MS, 2)]))

    async def report(progress):
        if progress["chunks_completed"] == 1:
            raise OSError("progress storage failed")

    with pytest.raises(HomeAssistantError, match="All data chunks were processed"):
        await device.async_import_fixture_statistics(
            from_datetime=START, to_datetime=START + timedelta(days=7),
            progress_callback=report,
        )
    assert set(device._fixture_stats_importer._state.events) == {"first"}
    assert device._fixture_stats_importer._pending is None
    await device.async_shutdown()
