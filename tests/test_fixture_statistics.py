"""Tests for fixture statistics processing helpers."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from custom_components.phyn.fixture_statistics import (
    FixtureStatisticsState,
    _decode_events,
    _plan,
    build_hourly_fixture_totals,
    detect_fixture_corrections,
    extract_event_id,
    fixture_statistic_id,
    resolve_fixture_attribution,
    resolve_fixture_name,
)


def test_resolve_fixture_name_prefers_user_category():
    """User category takes precedence; private text is not category identity."""
    event = {
        "latest_user_feedback": {"fixture_id": 7, "tell_us": "Kitchen Sink"},
        "latest_suggested_fixtures_result": {
            "suggested_fixtures": [
                {"fixture_id": 8, "fixture_name": "Toilet", "confidence_score": 0.9},
                {"fixture_id": 7, "fixture_name": "Sink", "confidence_score": 0.1},
            ]
        },
    }

    assert resolve_fixture_name(event) == "Sink"


def test_resolve_fixture_name_falls_back_to_suggested_fixture():
    """Suggested fixture name is used when no user label is present."""
    event = {
        "latest_suggested_fixtures_result": {
            "suggested_fixtures": [{"fixture_name": "Shower Only", "confidence_score": 0.9}]
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
                "suggested_fixtures": [{"fixture_name": "Toilet", "confidence_score": 0.9}]
            },
        },
        {
            "close_edge_timestamp": base_ms + 10_000,
            "total_flow": 0.75,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Toilet", "confidence_score": 0.9}]
            },
        },
        {
            "close_edge_timestamp": base_ms - 10,
            "total_flow": 99,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Sink", "confidence_score": 0.9}]
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
    assert statistic_id.startswith("phyn:28f53741cbba_master_bath_toilet_")
    assert statistic_id.endswith("_water")
    assert statistic_id == fixture_statistic_id("28F53741CBBA", "Master Bath Toilet")
    assert fixture_statistic_id("device", "Bath-Sink") != fixture_statistic_id("device", "BathSink")
    assert fixture_statistic_id("device", "浴室") != fixture_statistic_id("device", "!!!")
    assert fixture_statistic_id("device", "浴室").isascii()


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
                "suggested_fixtures": [{"fixture_name": "Shower Only", "confidence_score": 0.9}]
            },
        },
        {
            "event_id": "evt_2",
            "total_flow": 0.5,
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Sink", "confidence_score": 0.9}]
            },
        },
        {
            "event_id": "evt_3",
            "latest_user_feedback": {"fixture_id": 7},
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


ATTRIBUTION_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "attribution_cases.json").read_text()
)


@pytest.mark.parametrize(
    "case", [case for case in ATTRIBUTION_CASES if "catalog" not in case],
    ids=lambda case: case["name"],
)
def test_shared_raw_attribution_contract(case):
    """Shared SDK diagnostic vectors; catalog enrichment is not a consumer feature."""
    event = deepcopy(case["event"])
    if "expected_error" in case:
        with pytest.raises(ValueError, match=case["expected_error"]):
            resolve_fixture_name(event)
        return
    result = resolve_fixture_attribution(event)
    actual = {
        "attributed_fixture": result.label,
        "attributed_fixture_id": result.fixture_id,
        "attribution_source": result.source,
        "attributed_confidence": result.confidence,
        "top_confidence": result.top_prediction.confidence if result.top_prediction else None,
        "confidence_gap": result.confidence_gap,
        "tied_confidence": result.tied_confidence,
        "feedback_conflict": result.feedback_conflict,
        "needs_review": bool(result.review_reasons),
        "review_reasons": list(result.review_reasons),
        "invalid_prediction_metadata": "invalid_prediction_metadata" in result.review_reasons,
        "conflicting_fixture_names": "conflicting_fixture_names" in result.review_reasons,
    }
    for key, expected in case["expected"].items():
        if key == "algorithm":
            # The consumer leaves optional algorithm metadata in the original payload.
            assert event["latest_suggested_fixtures_result"]["suggested_fixtures"][0][
                "prediction_algorithm"
            ] == expected
        elif isinstance(expected, float):
            assert actual[key] == pytest.approx(expected)
        else:
            assert actual[key] == expected
    assert resolve_fixture_name(event) == result.label
    assert event == case["event"]


def test_catalog_vector_without_catalog_uses_matching_suggestion():
    case = next(case for case in ATTRIBUTION_CASES if "catalog" in case)
    result = resolve_fixture_attribution(case["event"])
    assert result.label == "Sink"
    assert result.fixture_id == 8
    assert result.source == "user_feedback"


@pytest.mark.parametrize("identifier", [False, True, -1, 1.0, "", " ", "-1", "1.0", "８", [], {}])
def test_invalid_explicit_category_never_falls_back(identifier):
    with pytest.raises(ValueError, match="fixture_id"):
        resolve_fixture_name({
            "latest_user_feedback": {"fixture_id": identifier},
            "latest_suggested_fixtures_result": {
                "suggested_fixtures": [{"fixture_name": "Sink", "confidence_score": 1}]
            },
        })


@pytest.mark.parametrize("identifier, expected", [(0, 0), (" 008 ", 8), ("0", 0), (8, 8)])
def test_category_id_normalization(identifier, expected):
    result = resolve_fixture_attribution({"latest_user_feedback": {"fixture_id": identifier}})
    assert result.fixture_id == expected
    assert result.label == f"Fixture type {expected}"


@pytest.mark.parametrize("score", [None, True, False, -0.1, 1.01, "bad", "", [], {},
                                      float("nan"), float("inf"), "-inf", "NaN"])
def test_invalid_candidate_is_not_skipped_even_after_perfect_score(score):
    prediction = {"suggested_fixtures": [
        {"fixture_id": 7, "fixture_name": "Sink", "confidence_score": 1},
        {"fixture_id": 8, "fixture_name": "Toilet", "confidence_score": score},
    ]}
    with pytest.raises(ValueError, match="confidence_score"):
        resolve_fixture_name({"latest_suggested_fixtures_result": prediction})
    result = resolve_fixture_attribution({
        "latest_user_feedback": {"fixture_id": 7},
        "latest_suggested_fixtures_result": prediction,
    })
    assert result.label == "Fixture type 7"
    assert result.confidence is None
    assert result.top_prediction is None
    assert result.review_reasons == ("invalid_prediction_metadata",)


@pytest.mark.parametrize("prediction, field", [
    ([], "latest_suggested_fixtures_result"),
    (False, "latest_suggested_fixtures_result"),
    ({"suggested_fixtures": {}}, "suggested_fixtures"),
    ({"suggested_fixtures": False}, "suggested_fixtures"),
    ({"suggested_fixtures": [None]}, "suggested_fixtures"),
    ({"suggested_fixtures": [{"fixture_name": [], "confidence_score": 1}]}, "fixture_name"),
    ({"suggested_fixtures": [{"fixture_id": -1, "confidence_score": 1}]}, "fixture_id"),
])
def test_malformed_models_fail_unless_human_selection_is_valid(prediction, field):
    with pytest.raises(ValueError, match=field):
        resolve_fixture_name({"latest_suggested_fixtures_result": prediction})
    result = resolve_fixture_attribution({
        "latest_user_feedback": {"fixture_id": 7},
        "latest_suggested_fixtures_result": prediction,
    })
    assert result.label == "Fixture type 7"
    assert result.review_reasons == ("invalid_prediction_metadata",)


@pytest.mark.parametrize("feedback", [[], "", False, 0, "comment"])
def test_malformed_feedback_container_fails(feedback):
    with pytest.raises(ValueError, match="latest_user_feedback"):
        resolve_fixture_name({"latest_user_feedback": feedback})


@pytest.mark.parametrize("feedback", [None, {}])
def test_empty_feedback_is_not_incomplete(feedback):
    result = resolve_fixture_attribution({
        "latest_user_feedback": feedback,
        "latest_suggested_fixtures_result": {
            "suggested_fixtures": [{"fixture_name": "Sink", "confidence_score": "0"}]
        },
    })
    assert not result.review_reasons


@pytest.mark.parametrize("candidate, label", [
    ({"fixture_id": "008", "confidence_score": 1}, "Fixture type 8"),
    ({"fixture_id": 8, "fixture_name": None, "confidence_score": 1}, "Fixture type 8"),
    ({"confidence_score": 1}, "Unknown"),
    ({"fixture_name": "  ", "confidence_score": 1}, "Unknown"),
])
def test_missing_model_names_have_explicit_outcome(candidate, label):
    assert resolve_fixture_name({
        "latest_suggested_fixtures_result": {"suggested_fixtures": [candidate]}
    }) == label


def test_literal_unknown_name_with_valid_id_is_still_a_category_prediction():
    result = resolve_fixture_attribution({
        "latest_suggested_fixtures_result": {"suggested_fixtures": [
            {"fixture_id": 0, "fixture_name": "Unknown", "confidence_score": 0.8}
        ]}
    })
    assert result.label == "Unknown"
    assert result.source == "prediction"
    assert result.fixture_id == 0
    assert result.confidence == 0.8
    assert "no_attributable_prediction" not in result.review_reasons


def test_first_among_maxima_not_first_candidate():
    result = resolve_fixture_attribution({
        "latest_suggested_fixtures_result": {"suggested_fixtures": [
            {"fixture_name": "Sink", "confidence_score": 0.1},
            {"fixture_name": "Toilet", "confidence_score": 0.9},
            {"fixture_name": "Shower", "confidence_score": 0.9},
        ]}
    })
    assert result.label == "Toilet"
    assert result.confidence_gap == 0
    assert result.review_reasons == ("tied_confidence",)


def test_human_source_and_private_subfixture_are_distinct(caplog):
    event = {
        "latest_user_feedback": {"fixture_id": 7, "sub_fixture_id": 12, "tell_us": "private"},
        "latest_suggested_fixtures_result": {"suggested_fixtures": [
            {"fixture_id": 8, "fixture_name": "Toilet", "confidence_score": 0.99},
        ]},
    }
    result = resolve_fixture_attribution(event)
    assert result.source == "user_feedback"
    assert result.confidence is None
    assert result.top_prediction is not None
    assert result.top_prediction.confidence == 0.99
    assert result.sub_fixture_id == 12
    assert result.label == "Fixture type 7"
    event["latest_suggested_fixtures_result"] = []
    assert resolve_fixture_name(event) == "Fixture type 7"
    assert "invalid_prediction_metadata" in caplog.text
    assert "private" not in caplog.text
    assert "sub_fixture_id" not in caplog.text


def test_saved_evidence_does_not_use_raw_attribution(monkeypatch):
    evidence = {"event": {"end_ms": 1000, "volume": 3.0, "fixture": "Private old label"}}

    def reject_raw(_event):
        raise AssertionError("Stored labels must not be re-resolved")

    monkeypatch.setattr(
        "custom_components.phyn.fixture_statistics.resolve_fixture_name", reject_raw
    )
    assert _decode_events(evidence) == evidence


@pytest.mark.parametrize("value", [
    {"end_ms": 1000, "volume": -1, "fixture": "Old"},
    {"end_ms": 1000, "volume": True, "fixture": "Old"},
    {"end_ms": 0, "volume": 1, "fixture": "Old"},
    {"end_ms": 1000, "volume": 1, "fixture": ""},
])
def test_stored_evidence_still_validated(value):
    with pytest.raises(ValueError):
        _decode_events({"event": value})


def test_human_selection_does_not_bypass_volume_validation():
    with pytest.raises(ValueError, match="finite nonnegative"):
        _plan(FixtureStatisticsState(), [{
            "id": "event", "close_edge_timestamp": 1000, "total_flow": -1,
            "latest_user_feedback": {"fixture_id": 7},
            "latest_suggested_fixtures_result": [],
        }], "device", False)
