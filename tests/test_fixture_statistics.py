"""Tests for fixture statistics processing helpers."""
from datetime import datetime, timezone

from custom_components.phyn.fixture_statistics import (
    build_hourly_fixture_totals,
    detect_fixture_corrections,
    extract_event_id,
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
            "close_edge_timestamp": base_ms - 10,
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


def test_extract_event_id_accepts_event_id_or_id_fields():
    """Event ID extraction should support both event_id and id keys."""
    assert extract_event_id({"event_id": "evt_123"}) == "evt_123"
    assert extract_event_id({"id": "evt_456"}) == "evt_456"
    assert extract_event_id({"event_id": "  evt_789  "}) == "evt_789"
    assert extract_event_id({"event_id": 1}) is None


def test_detect_fixture_corrections_reports_fixture_changes():
    """Cached event fixture mismatch should be reported as a correction."""
    cached_events = {
        "evt_1": {"fixture": "Toilet", "end_ms": 1772000000000},
        "evt_2": {"fixture": "Sink", "end_ms": 1772000001000},
    }
    events = [
        {
            "event_id": "evt_1",
            "total_flow": 1.0,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Shower Only"}]
            },
        },
        {
            "event_id": "evt_2",
            "total_flow": 0.5,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Sink"}]
            },
        },
        {
            "event_id": "evt_3",
            "user_fixture_label": "Kitchen Sink",
            "total_flow": 0.3,
        },
    ]

    corrections = detect_fixture_corrections(events, cached_events)

    assert len(corrections) == 1
    assert corrections[0] == {
        "event_id": "evt_1",
        "old_fixture": "Toilet",
        "new_fixture": "Shower Only",
    }
