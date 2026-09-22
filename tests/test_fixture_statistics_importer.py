"""Pure observed-event reconciliation and state ownership regressions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.phyn.fixture_statistics import (
    FixtureStatisticsState,
    HOUR_MS,
    PhynFixtureStatisticsImporter,
    _plan,
)


def event(key, hour, volume=1.0, label="Kitchen"):
    return {
        "id": key, "close_edge_timestamp": hour * HOUR_MS + 1000,
        "total_flow": volume, "user_fixture_label": label,
    }


def accept(state, events, force=False):
    plan, _ = _plan(state, events, "device", force)
    return FixtureStatisticsState(
        state.events | plan.updates, plan.fixture_ids, state.rows | plan.rows
    )


def test_duplicates_and_identical_replay_are_noops():
    state = accept(FixtureStatisticsState(), [event("one", 1), event("one", 1)])
    assert len(state.events) == 1
    assert state.rows["Kitchen"][HOUR_MS] == 1
    plan, corrections = _plan(state, [event("one", 1)], "device", False)
    assert not plan.updates
    assert not plan.rows
    assert corrections == 0


def test_late_and_same_timestamp_ids_are_counted_once():
    state = accept(FixtureStatisticsState(), [event("first", 10, 5)])
    state = accept(state, [event("late", 1, 2), event("same-time", 10, 3)])
    assert len(state.events) == 3
    assert state.rows["Kitchen"][0] == 0
    assert state.rows["Kitchen"][HOUR_MS] == 2
    assert state.rows["Kitchen"][10 * HOUR_MS] == 10


def test_correction_updates_both_labels_and_keeps_absent_events():
    state = accept(FixtureStatisticsState(), [
        event("old", 1, 10), event("change", 2, 5), event("later", 3, 2),
    ])
    state = accept(state, [event("change", 2, 3, "Bath")])
    assert len(state.events) == 3
    assert state.rows["Kitchen"][HOUR_MS] == 10
    assert state.rows["Kitchen"][2 * HOUR_MS] == 10
    assert state.rows["Kitchen"][3 * HOUR_MS] == 12
    assert state.rows["Bath"][HOUR_MS] == 0
    assert state.rows["Bath"][2 * HOUR_MS] == 3


def test_volume_and_time_correction_zeroes_former_hour():
    state = accept(FixtureStatisticsState(), [event("one", 2, 10)])
    state = accept(state, [event("one", 3, 4)])
    assert state.rows["Kitchen"][2 * HOUR_MS] == 0
    assert state.rows["Kitchen"][3 * HOUR_MS] == 4


def test_empty_force_retains_all_history():
    state = accept(FixtureStatisticsState(), [event("one", 1)])
    before = deepcopy(state)
    plan, _ = _plan(state, [], "device", True)
    assert not plan.updates and not plan.rows
    assert state == before


@pytest.mark.parametrize("volume", [-1, float("nan"), float("inf"), True, "3"])
def test_invalid_volume_rejected(volume):
    with pytest.raises(ValueError, match="finite nonnegative"):
        _plan(FixtureStatisticsState(), [event("one", 1, volume)], "device", False)


def test_contradictory_same_response_is_rejected_without_mutation():
    state = FixtureStatisticsState()
    with pytest.raises(ValueError, match="Conflicting observations"):
        _plan(state, [event("one", 1, 1), event("one", 1, 2)], "device", False)
    assert not state.events


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "force", "preview"])
async def test_legacy_state_blocks_all_import_modes(hass, mode):
    original = {"fixture_sums": {"Kitchen": 100}, "last_event_ms": 999}
    importer = PhynFixtureStatisticsImporter(hass, "device")
    importer._store = SimpleNamespace(
        async_load=AsyncMock(return_value=deepcopy(original)), async_save=AsyncMock(),
    )
    await importer.async_initialize()
    method = {
        "normal": importer.async_import_events,
        "force": importer.async_force_reimport_events,
        "preview": importer.async_preview_import_events,
    }[mode]
    with pytest.raises(HomeAssistantError, match="paused"):
        await method([event("one", 1)])
    importer._store.async_save.assert_not_awaited()
    assert importer._store.async_load.return_value == original
