"""Tests for fixture statistics processing helpers."""
from datetime import datetime, timezone

from custom_components.phyn.fixture_statistics import (
    build_hourly_fixture_totals,
    fixture_statistic_id,
    resolve_fixture_name,
)


def test_resolve_fixture_name_prefers_user_feedback_label():
    """User feedback label takes precedence over model suggestion."""
    event = {
        "latest_user_feedback": {"tell_us": "Kitchen Sink"},
        "latest_suggested_fixtures_result": {
            "suggested_fixtures": [{"fixture_name": "Toilet"}]
        },
    }

    assert resolve_fixture_name(event) == "Kitchen Sink"


def test_resolve_fixture_name_falls_back_to_suggested_fixture():
    """Suggested fixture name is used when no user label is present."""
    event = {
        "latest_suggested_fixtures_result": {
            "suggested_fixtures": [{"fixture_name": "Shower Only"}]
        }
    }

    assert resolve_fixture_name(event) == "Shower Only"


def test_build_hourly_fixture_totals_aggregates_and_filters_old_events():
    """Only new events should be aggregated into UTC hourly buckets."""
    base_ms = int(datetime(2026, 2, 24, 12, 15, tzinfo=timezone.utc).timestamp() * 1000)

    events = [
        {
            "close_edge_timestamp": base_ms,
            "total_flow": 1.25,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Toilet"}]
            },
        },
        {
            "close_edge_timestamp": base_ms + 10_000,
            "total_flow": 0.75,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Toilet"}]
            },
        },
        {
            "close_edge_timestamp": base_ms - 1,
            "total_flow": 99,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Sink"}]
            },
        },
    ]

    totals, newest = build_hourly_fixture_totals(events, last_event_ms=base_ms - 5)

    assert newest == base_ms + 10_000
    hour_bucket = datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc)
    assert totals["Toilet"][hour_bucket] == 2.0
    assert "Sink" not in totals


def test_fixture_statistic_id_is_stable_and_sanitized():
    """Statistic IDs should be deterministic and URL-safe."""
    statistic_id = fixture_statistic_id("28F53741CBBA", "Master Bath Toilet")
    assert statistic_id == "phyn:28f53741cbba_master_bath_toilet_water"
