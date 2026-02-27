"""Tests for fixture statistics importer stateful workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.phyn import fixture_statistics as fixture_stats
from custom_components.phyn.fixture_statistics import PhynFixtureStatisticsImporter


def _event(
    event_id: str,
    fixture_name: str,
    end_ms: int,
    total_flow: float,
) -> dict:
    """Create a minimal usage event payload."""
    return {
        "event_id": event_id,
        "close_edge_timestamp": end_ms,
        "total_flow": total_flow,
        "latest_suggested_fixtures_result": {
            "suggested_fixtures": [{"fixture_name": fixture_name}]
        },
    }


@pytest.mark.asyncio
async def test_preview_import_reports_fixture_corrections() -> None:
    """Preview should report corrections without mutating importer state."""
    importer = PhynFixtureStatisticsImporter(SimpleNamespace(), "DEVICE_1")
    importer._state.event_cache = {"evt_1": {"fixture": "Toilet", "end_ms": 1000}}

    result = await importer.async_preview_import_events(
        [_event("evt_1", "Shower", 2000, 1.0)],
        force_reimport=True,
    )

    assert result["corrections_detected"] == 1
    assert result["dry_run"] == 1
    assert importer._state.event_cache["evt_1"]["fixture"] == "Toilet"


@pytest.mark.asyncio
async def test_import_events_updates_event_cache_and_returns_cached_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import should persist event cache and expose cached_events diagnostics."""
    importer = PhynFixtureStatisticsImporter(SimpleNamespace(), "DEVICE_2")
    importer._store = SimpleNamespace(async_save=AsyncMock())
    monkeypatch.setattr(
        fixture_stats,
        "async_add_external_statistics",
        lambda *_args, **_kwargs: None,
    )

    recent_end_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    result = await importer.async_import_events(
        [_event("evt_2", "Kitchen Sink", recent_end_ms, 0.5)]
    )

    assert result["cached_events"] == 1
    assert "evt_2" in importer._state.event_cache
    assert importer._store.async_save.await_count == 1


@pytest.mark.asyncio
async def test_force_reimport_clears_existing_stats_then_reimports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force reimport should clear statistic ids and return clear diagnostics."""
    importer = PhynFixtureStatisticsImporter(SimpleNamespace(), "DEVICE_3")
    importer._store = SimpleNamespace(async_save=AsyncMock())
    importer._state.fixture_sums = {"Toilet": 5.0}
    importer._state.last_event_ms = 3000
    importer._state.event_cache = {"evt_old": {"fixture": "Toilet", "end_ms": 3000}}
    monkeypatch.setattr(
        fixture_stats,
        "async_add_external_statistics",
        lambda *_args, **_kwargs: None,
    )

    clear_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(fixture_stats, "async_clear_statistic_ids", clear_mock)

    result = await importer.async_force_reimport_events(
        [_event("evt_3", "Toilet", 5000, 0.75)]
    )

    assert clear_mock.await_count == 1
    assert result["force_reimport"] == 1
    assert result["cleared_statistic_ids"] >= 1
    assert result["checkpoint_after_ms"] == 5000
