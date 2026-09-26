"""Non-destructive, observed-event fixture statistics."""
from __future__ import annotations

from asyncio import CancelledError, Lock, shield, sleep, timeout
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import partial
from hashlib import sha256
import logging
import math
from typing import Any, Literal, TypedDict

from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_add_external_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import VolumeConverter

from .const import DOMAIN

FIXTURE_STATS_STORE_VERSION = 1
FIXTURE_STATE_SCHEMA = 2
DEFAULT_INITIAL_LOOKBACK_DAYS = 7
HOUR_MS = 3_600_000
VERIFY_TIMEOUT_SECONDS = 20
_LOGGER = logging.getLogger(__name__)


class EventContribution(TypedDict):
    """Accepted observation, not an authoritative cloud revision."""

    end_ms: int
    volume: float
    fixture: str


Rows = dict[str, dict[int, float]]


@dataclass
class FixtureStatisticsState:
    """Compact evidence retained for all accepted history."""

    events: dict[str, EventContribution] = field(default_factory=dict)
    fixture_ids: dict[str, str] = field(default_factory=dict)
    rows: Rows = field(default_factory=dict)


@dataclass
class PendingImport:
    """Replayable absolute writes; committed evidence stays unchanged until verified."""

    updates: dict[str, EventContribution]
    fixture_ids: dict[str, str]
    rows: Rows


def extract_event_id(event: dict[str, Any]) -> str | None:
    """Accept both published and historical event identifier fields."""
    value = event.get("event_id") or event.get("id")
    return value.strip() or None if isinstance(value, str) else None


@dataclass(frozen=True)
class FixturePrediction:
    """Validated category suggestion, not a household fixture."""

    fixture_id: int | None
    name: str | None
    confidence: float


@dataclass(frozen=True)
class FixtureAttribution:
    """Local attribution diagnostics, never an authoritative server revision."""

    label: str
    fixture_id: int | None
    source: Literal["user_feedback", "prediction", "unknown"]
    confidence: float | None
    top_prediction: FixturePrediction | None
    confidence_gap: float | None
    tied_confidence: bool
    feedback_conflict: bool
    review_reasons: tuple[str, ...]


def _category_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str):
        digits = value.strip()
        if digits and digits.isascii() and digits.isdigit():
            try:
                return int(digits)
            except ValueError:
                pass
    raise ValueError("Invalid fixture_id: expected a nonnegative integer or ASCII digit string")


def _fixture_predictions(event: dict[str, Any]) -> list[FixturePrediction]:
    prediction = event.get("latest_suggested_fixtures_result")
    if prediction is None:
        return []
    if not isinstance(prediction, dict):
        raise ValueError("Invalid latest_suggested_fixtures_result")
    suggestions = prediction.get("suggested_fixtures")
    if suggestions is None:
        return []
    if not isinstance(suggestions, list):
        raise ValueError("Invalid suggested_fixtures")
    result = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            raise ValueError("Invalid suggested_fixtures entry")
        category = _category_id(suggestion.get("fixture_id"))
        name = suggestion.get("fixture_name")
        if name is not None and not isinstance(name, str):
            raise ValueError("Invalid fixture_name")
        score = suggestion.get("confidence_score")
        if isinstance(score, bool) or not isinstance(score, (int, float, str)):
            raise ValueError("Invalid confidence_score")
        try:
            confidence = float(score)
        except (ValueError, OverflowError) as err:
            raise ValueError("Invalid confidence_score") from err
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Invalid confidence_score: expected a finite value in [0, 1]")
        result.append(FixturePrediction(
            category, name.strip() or None if name else None, confidence
        ))
    return result


def resolve_fixture_attribution(event: dict[str, Any]) -> FixtureAttribution:
    """Prefer explicit user category selection, otherwise the first maximum score."""
    feedback = event.get("latest_user_feedback")
    if feedback is None:
        feedback = {}
    if not isinstance(feedback, dict):
        raise ValueError("Invalid latest_user_feedback")
    selected_id = _category_id(feedback.get("fixture_id"))
    reasons = []
    if feedback and selected_id is None:
        reasons.append("feedback_without_fixture_selection")
    try:
        predictions = _fixture_predictions(event)
    except ValueError:
        if selected_id is None:
            raise
        predictions = []
        reasons.append("invalid_prediction_metadata")

    ranked = sorted(predictions, key=lambda candidate: candidate.confidence, reverse=True)
    top = ranked[0] if ranked else None
    gap = ranked[0].confidence - ranked[1].confidence if len(ranked) > 1 else None
    tied = gap == 0
    conflict = selected_id is not None and top is not None and (
        top.fixture_id is not None and top.fixture_id != selected_id
    )
    category = selected_id if selected_id is not None else top.fixture_id if top else None
    source: Literal["user_feedback", "prediction", "unknown"] = (
        "user_feedback" if selected_id is not None else "prediction" if top else "unknown"
    )
    if category is not None:
        names = {
            candidate.name for candidate in predictions
            if candidate.fixture_id == category and candidate.name
        }
        if len(names) > 1:
            reasons.append("conflicting_fixture_names")
        label = next(iter(names)) if len(names) == 1 else f"Fixture type {category}"
    else:
        label = top.name if top and top.name else "Unknown"
    if selected_id is None:
        if tied:
            reasons.append("tied_confidence")
        if category is None and (top is None or not top.name):
            source = "unknown"
            reasons.append("no_attributable_prediction")
    return FixtureAttribution(
        label=label,
        fixture_id=category,
        source=source,
        confidence=top.confidence if source == "prediction" and top else None,
        top_prediction=top,
        confidence_gap=gap,
        tied_confidence=tied,
        feedback_conflict=conflict,
        review_reasons=tuple(reasons),
    )


def resolve_fixture_name(event: dict[str, Any]) -> str:
    """Resolve a category label and report uncertainty without private payloads."""
    attribution = resolve_fixture_attribution(event)
    if attribution.review_reasons:
        _LOGGER.warning(
            "Phyn fixture attribution needs review in the Phyn app: %s",
            ", ".join(attribution.review_reasons),
        )
    return attribution.label


def event_end_timestamp_ms(event: dict[str, Any]) -> int | None:
    """Only closed events have a stable contribution time."""
    value = event.get("close_edge_timestamp")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0 or int(value) != value:
        return None
    return int(value)


def _contribution_values(end: Any, volume: Any) -> tuple[int, float]:
    end_ms = event_end_timestamp_ms({"close_edge_timestamp": end})
    if end_ms is None:
        raise ValueError("Water usage event requires a valid close timestamp")
    if (
        isinstance(volume, bool) or not isinstance(volume, (int, float))
        or not math.isfinite(volume) or volume < 0
    ):
        raise ValueError("Water usage event requires a finite nonnegative volume")
    datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    return end_ms, float(volume)


def _normalize_events(events: list[dict[str, Any]]) -> dict[str, EventContribution]:
    if not isinstance(events, list):
        raise ValueError("Expected a list of water usage events")
    normalized: dict[str, EventContribution] = {}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid water usage event")
        identifier = extract_event_id(event)
        if identifier is None:
            raise ValueError("Water usage event requires an ID")
        if event.get("id") and event.get("event_id") and event["id"] != event["event_id"]:
            raise ValueError("Conflicting water usage event identifiers")
        end_ms, volume = _contribution_values(
            event.get("close_edge_timestamp"), event.get("total_flow")
        )
        contribution = EventContribution(
            end_ms=end_ms, volume=volume, fixture=resolve_fixture_name(event)
        )
        if identifier in normalized and normalized[identifier] != contribution:
            raise ValueError(f"Conflicting observations for event {identifier}")
        normalized[identifier] = contribution
    return normalized


def build_hourly_fixture_totals(
    events: list[dict[str, Any]], last_event_ms: int
) -> tuple[dict[str, dict[datetime, float]], int]:
    """Aggregate a selected batch; importer reconciliation never uses a timestamp gate."""
    totals: dict[str, dict[datetime, float]] = defaultdict(lambda: defaultdict(float))
    newest = last_event_ms
    for event in events:
        end = event_end_timestamp_ms(event)
        if end is None or end <= last_event_ms:
            continue
        hour = datetime.fromtimestamp(end / 1000, timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        totals[resolve_fixture_name(event)][hour] += float(event["total_flow"])
        newest = max(newest, end)
    return totals, newest


def detect_fixture_corrections(
    events: list[dict[str, Any]], cached_events: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """Report label changes independently of prediction timestamps."""
    result = []
    for event in events:
        identifier = extract_event_id(event)
        previous = cached_events.get(identifier) if identifier else None
        label = resolve_fixture_name(event)
        if identifier and previous and previous.get("fixture") != label:
            result.append({
                "event_id": identifier, "old_fixture": previous["fixture"],
                "new_fixture": label,
            })
    return result


def _device_slug(device_id: str) -> str:
    return "".join(c for c in device_id.lower() if c.isascii() and c.isalnum()) or "device"


def fixture_statistic_id(device_id: str, fixture_name: str) -> str:
    """New label-series IDs include both original keys in a checked digest."""
    slug = "".join(
        c for c in "_".join(fixture_name.lower().split())
        if c.isascii() and (c.isalnum() or c == "_")
    )[:40] or "fixture"
    digest = sha256(f"{device_id}\0{fixture_name}".encode()).hexdigest()[:16]
    return f"{DOMAIN}:{_device_slug(device_id)}_{slug}_{digest}_water"


def _plan(
    state: FixtureStatisticsState, events: list[dict[str, Any]],
    device_id: str, force: bool,
) -> tuple[PendingImport, int]:
    observations = _normalize_events(events)
    updates = {
        key: value for key, value in observations.items()
        if state.events.get(key) != value
    }
    affected = {value["fixture"] for value in updates.values()}
    affected.update(state.events[key]["fixture"] for key in updates if key in state.events)
    if force:
        affected.update(value["fixture"] for value in observations.values())
    merged = state.events | updates
    mapping = dict(state.fixture_ids)
    rows: Rows = {}
    for label in sorted(affected):
        if label not in mapping:
            identifier = fixture_statistic_id(device_id, label)
            if identifier in mapping.values():
                raise ValueError("Fixture statistic ID collision; no data was changed")
            mapping[label] = identifier
        hourly: dict[int, list[float]] = defaultdict(list)
        for value in merged.values():
            if value["fixture"] == label:
                hourly[value["end_ms"] // HOUR_MS * HOUR_MS].append(value["volume"])
        old_hours = state.rows.get(label, {})
        hours = set(hourly) | set(old_hours)
        if hourly:
            hours.add(min(hourly) - HOUR_MS)
        total = 0.0
        planned = {}
        for hour in sorted(hours):
            total = math.fsum((total, math.fsum(hourly.get(hour, []))))
            planned[hour] = round(total, 6)
        rows[label] = planned
    return PendingImport(updates, mapping, rows), sum(key in state.events for key in updates)


def _encode_rows(rows: Rows) -> dict[str, dict[str, float]]:
    return {label: {str(hour): value for hour, value in values.items()} for label, values in rows.items()}


def _decode_rows(raw: Any) -> Rows:
    if not isinstance(raw, dict):
        raise ValueError("Invalid stored statistic rows")
    rows: Rows = {}
    for label, values in raw.items():
        if not isinstance(label, str) or not isinstance(values, dict):
            raise ValueError("Invalid stored fixture")
        rows[label] = {}
        for hour, value in values.items():
            timestamp = int(hour)
            if (
                timestamp % HOUR_MS
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
            ):
                raise ValueError("Invalid stored cumulative row")
            datetime.fromtimestamp(timestamp / 1000, timezone.utc)
            rows[label][timestamp] = float(value)
    return rows


def _decode_events(raw: Any) -> dict[str, EventContribution]:
    if not isinstance(raw, dict):
        raise ValueError("Invalid stored event evidence")
    observations: dict[str, EventContribution] = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str) or not key or key != key.strip()
            or not isinstance(value, dict)
        ):
            raise ValueError("Invalid stored event")
        if not isinstance(value.get("fixture"), str) or not value["fixture"].strip():
            raise ValueError("Invalid stored event label")
        end_ms, volume = _contribution_values(value.get("end_ms"), value.get("volume"))
        observations[key] = EventContribution(
            end_ms=end_ms, volume=volume, fixture=value["fixture"]
        )
    return observations


def _decode_mapping(raw: Any, device_id: str) -> dict[str, str]:
    if not isinstance(raw, dict) or any(
        not isinstance(label, str) or value != fixture_statistic_id(device_id, label)
        for label, value in raw.items()
    ):
        raise ValueError("Invalid stored fixture identity")
    if len(set(raw.values())) != len(raw):
        raise ValueError("Stored fixture ID collision")
    return dict(raw)


def _validate_evidence(state: FixtureStatisticsState) -> None:
    """Stored absolute rows must be explainable by retained contributions."""
    if state.rows.keys() != state.fixture_ids.keys():
        raise ValueError("Missing stored fixture rows")
    grouped: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for event in state.events.values():
        grouped[event["fixture"]][event["end_ms"] // HOUR_MS * HOUR_MS].append(event["volume"])
    for label, rows in state.rows.items():
        hourly = grouped[label]
        required = set(hourly)
        if required:
            required.add(min(required) - HOUR_MS)
        if not required <= rows.keys():
            raise ValueError("Stored rows omit accepted events or their baseline")
        total = 0.0
        for hour in sorted(rows):
            total = math.fsum((total, math.fsum(hourly.get(hour, []))))
            if round(total, 6) != rows[hour]:
                raise ValueError("Stored cumulative rows do not match event evidence")


class PhynFixtureStatisticsImporter:
    """Keep local accepted evidence and verify absolute Recorder writes before commit."""

    def __init__(
        self, hass: HomeAssistant, device_id: str, *, home_name: str = ""
    ) -> None:
        self._hass = hass
        self._device_id = device_id
        self._home_name = home_name.strip() or device_id
        self._store: Store[dict[str, Any]] = Store(
            hass, FIXTURE_STATS_STORE_VERSION, f"{DOMAIN}_fixture_stats_{device_id.lower()}"
        )
        self._state = FixtureStatisticsState()
        self._pending: PendingImport | None = None
        self._initialized = False
        self._initialize_lock = Lock()
        self._operation_lock = Lock()
        self._blocked_reason: str | None = None

    def _notice(self, reason: str) -> None:
        async_create(
            self._hass,
            f"Fixture statistics for Phyn {self._device_id} are paused: {reason}. "
            "Existing history is preserved. Normal sensors and controls are unaffected. "
            "Do not delete stored history to bypass this warning.",
            title="Phyn fixture statistics paused",
            notification_id=f"phyn_fixture_stats_{self._device_id.lower()}",
        )

    async def async_initialize(self) -> None:
        async with self._initialize_lock:
            if self._initialized:
                return
            data = await self._store.async_load()
            if data is not None:
                try:
                    self._state, self._pending = await self._hass.async_add_executor_job(
                        self._decode, data
                    )
                except (ValueError, TypeError, KeyError, OverflowError) as err:
                    self._blocked_reason = f"unsupported or ambiguous saved state ({err})"
                    self._notice(self._blocked_reason)
            self._initialized = True

    def _decode(self, data: dict[str, Any]) -> tuple[FixtureStatisticsState, PendingImport | None]:
        if not isinstance(data, dict) or data.get("schema") != FIXTURE_STATE_SCHEMA:
            raise ValueError("older state has no complete event evidence")
        state = FixtureStatisticsState(
            _decode_events(data["events"]),
            _decode_mapping(data["fixture_ids"], self._device_id),
            _decode_rows(data["rows"]),
        )
        if any(value["fixture"] not in state.fixture_ids for value in state.events.values()):
            raise ValueError("Missing fixture identity")
        if not state.rows.keys() <= state.fixture_ids.keys():
            raise ValueError("Unidentified stored rows")
        _validate_evidence(state)
        pending = None
        if (raw := data.get("pending")) is not None:
            if not isinstance(raw, dict):
                raise ValueError("Invalid pending import")
            pending = PendingImport(
                _decode_events(raw["updates"]),
                _decode_mapping(raw["fixture_ids"], self._device_id),
                _decode_rows(raw["rows"]),
            )
            if any(pending.fixture_ids.get(key) != value for key, value in state.fixture_ids.items()):
                raise ValueError("Pending import reassigns fixture identities")
            if not pending.rows.keys() <= pending.fixture_ids.keys():
                raise ValueError("Unidentified pending rows")
            if any(value["fixture"] not in pending.rows for value in pending.updates.values()):
                raise ValueError("Pending event has no row plan")
            _validate_evidence(FixtureStatisticsState(
                state.events | pending.updates, pending.fixture_ids, state.rows | pending.rows
            ))
        return state, pending

    async def _save(self, state: FixtureStatisticsState, pending: PendingImport | None) -> None:
        def payload() -> dict[str, Any]:
            return {
                "schema": FIXTURE_STATE_SCHEMA, "events": state.events,
                "fixture_ids": state.fixture_ids, "rows": _encode_rows(state.rows),
                "pending": None if pending is None else {
                    "updates": pending.updates, "fixture_ids": pending.fixture_ids,
                    "rows": _encode_rows(pending.rows),
                },
            }
        data = await self._hass.async_add_executor_job(payload)
        write = self._hass.async_create_task(self._store.async_save(data))
        try:
            await shield(write)
        except CancelledError:
            self._initialized = False
            await write
            raise
        except OSError:
            self._initialized = False
            raise

    def current_checkpoint_ms(self) -> int:
        """Newest committed observed event; not a deduplication cutoff."""
        return max((value["end_ms"] for value in self._state.events.values()), default=0)

    def next_fetch_start(self, now: datetime) -> datetime:
        overlap = now - timedelta(days=DEFAULT_INITIAL_LOOKBACK_DAYS)
        checkpoint = self.current_checkpoint_ms()
        return min(overlap, datetime.fromtimestamp(checkpoint / 1000, timezone.utc)) if checkpoint else overlap

    async def _prepare(self) -> None:
        await self.async_initialize()
        if self._blocked_reason:
            raise HomeAssistantError(f"Fixture statistics paused: {self._blocked_reason}")
        recorder = get_instance(self._hass)
        metadata = await recorder.async_add_executor_job(
            partial(get_metadata, self._hass, statistic_source=DOMAIN)
        )
        prefix = f"{DOMAIN}:{_device_slug(self._device_id)}_"
        existing = {key for key in metadata if key.startswith(prefix) and key.endswith("_water")}
        owned = set(self._state.fixture_ids.values())
        if self._pending:
            owned.update(self._pending.fixture_ids.values())
        if existing - owned:
            self._blocked_reason = "existing fixture statistics have no verified event evidence"
            self._notice(self._blocked_reason)
            raise HomeAssistantError(self._blocked_reason)

    def _metadata(self, label: str, identifier: str) -> StatisticMetaData:
        return StatisticMetaData(
            mean_type=StatisticMeanType.NONE, has_sum=True,
            name=f"Phyn {self._home_name} - {label} Water",
            source=DOMAIN, statistic_id=identifier,
            unit_class=VolumeConverter.UNIT_CLASS, unit_of_measurement=UnitOfVolume.GALLONS,
        )

    async def _matches(self, rows: Rows, mapping: dict[str, str]) -> bool:
        if not rows:
            return True
        recorder = get_instance(self._hass)
        metadata = await recorder.async_add_executor_job(
            partial(get_metadata, self._hass, statistic_ids={mapping[label] for label in rows})
        )
        for label, expected in rows.items():
            identifier = mapping[label]
            meta = metadata.get(identifier)
            if meta is None:
                return False
            actual_metadata = meta[1]
            wanted = self._metadata(label, identifier)
            # Display names may change without changing accepted usage evidence.
            if any(actual_metadata.get(key) != wanted[key] for key in wanted if key != "name"):
                return False
            result = await recorder.async_add_executor_job(
                statistics_during_period, self._hass,
                datetime.fromtimestamp(min(expected, default=0) / 1000, timezone.utc),
                None, {identifier}, "hour", None, {"state", "sum"},
            )
            actual = {
                round(row["start"] * 1000): (row.get("sum"), row.get("state"))
                for row in result.get(identifier, [])
            }
            if actual.keys() != expected.keys() or any(
                actual.get(hour) != (value, value) for hour, value in expected.items()
            ):
                return False
        return True

    def usage_statistics(self) -> dict[str, str]:
        """Committed positive-usage series, excluding inventory-only entries."""
        labels = {
            event["fixture"] for event in self._state.events.values()
            if event["volume"] > 0
        }
        return {
            self._state.fixture_ids[label]: f"Phyn {self._home_name} - {label} Water"
            for label in sorted(labels)
        }

    @property
    def home_name(self) -> str:
        """Phyn home name used in statistics and coverage warnings."""
        return self._home_name

    async def async_register_categories(self, labels: set[str]) -> None:
        """Journal metadata-only series without inventing usage or a baseline."""
        if any(not isinstance(label, str) or not label.strip() for label in labels):
            raise HomeAssistantError("Configured fixture category names must be nonempty")
        async with self._operation_lock:
            await self._prepare()
            if self._pending:
                await self._finish_pending()
            if not await self._matches(self._state.rows, self._state.fixture_ids):
                self._blocked_reason = "stored Recorder values differ from accepted event evidence"
                self._notice(self._blocked_reason)
                raise HomeAssistantError("Fixture statistics differ from saved evidence; no automatic repair")
            mapping = dict(self._state.fixture_ids)
            rows: Rows = {}
            for label in sorted(labels - mapping.keys()):
                identifier = fixture_statistic_id(self._device_id, label)
                if identifier in mapping.values():
                    raise HomeAssistantError("Configured fixture statistic ID collision")
                mapping[label] = identifier
                rows[label] = {}
            if rows:
                plan = PendingImport({}, mapping, rows)
                await self._save(self._state, plan)
                self._pending = plan
                await self._finish_pending()
            await self._refresh_names()

    async def _refresh_names(self) -> None:
        """Update owned display metadata without writing consumption rows."""
        if not self._state.fixture_ids:
            return
        recorder = get_instance(self._hass)
        wanted = {
            identifier: self._metadata(label, identifier)
            for label, identifier in self._state.fixture_ids.items()
        }

        async def mismatches() -> set[str]:
            metadata = await recorder.async_add_executor_job(
                partial(get_metadata, self._hass, statistic_ids=set(wanted))
            )
            return {
                identifier for identifier, expected in wanted.items()
                if identifier not in metadata
                or metadata[identifier][1].get("name") != expected["name"]
            }

        for identifier in await mismatches():
            # HA upserts metadata even with no statistic rows. IDs stay unchanged.
            async_add_external_statistics(self._hass, wanted[identifier], [])
        try:
            async with timeout(VERIFY_TIMEOUT_SECONDS):
                while await mismatches():
                    await sleep(0.05)
        except TimeoutError as err:
            raise HomeAssistantError(
                "Fixture statistic display names could not be verified; retry the import"
            ) from err

    async def _finish_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return
        if not await self._matches(pending.rows, pending.fixture_ids):
            for label, rows in pending.rows.items():
                async_add_external_statistics(
                    self._hass, self._metadata(label, pending.fixture_ids[label]),
                    [
                        StatisticData(
                            start=datetime.fromtimestamp(hour / 1000, timezone.utc),
                            sum=value, state=value,
                        ) for hour, value in sorted(rows.items())
                    ],
                )
            try:
                async with timeout(VERIFY_TIMEOUT_SECONDS):
                    while not await self._matches(pending.rows, pending.fixture_ids):
                        await sleep(0.05)
            except TimeoutError as err:
                self._notice("Recorder writes could not be verified; the pending import is retained")
                raise HomeAssistantError("Fixture import pending Recorder verification") from err
        next_state = FixtureStatisticsState(
            self._state.events | pending.updates,
            pending.fixture_ids,
            self._state.rows | pending.rows,
        )
        await self._save(next_state, None)
        self._state = next_state
        self._pending = None
        async_dismiss(self._hass, f"phyn_fixture_stats_{self._device_id.lower()}")

    async def async_preview_import_events(
        self, events: list[dict[str, Any]], *, force_reimport: bool = False
    ) -> dict[str, int]:
        return await self._execute(events, force_reimport=force_reimport, dry_run=True)

    async def async_import_events(
        self, events: list[dict[str, Any]], *, force_reimport: bool = False
    ) -> dict[str, int]:
        return await self._execute(events, force_reimport=force_reimport, dry_run=False)

    async def async_force_reimport_events(self, events: list[dict[str, Any]]) -> dict[str, int]:
        """Reconcile observed events without deleting or resetting historical series."""
        return await self.async_import_events(events, force_reimport=True)

    async def _execute(
        self, events: list[dict[str, Any]], *, force_reimport: bool, dry_run: bool,
    ) -> dict[str, int]:
        async with self._operation_lock:
            await self._prepare()
            if self._pending:
                if dry_run:
                    raise HomeAssistantError("An earlier fixture import is pending; preview cannot modify it")
                await self._finish_pending()
            if not await self._matches(self._state.rows, self._state.fixture_ids):
                self._blocked_reason = "stored Recorder values differ from accepted event evidence"
                self._notice(self._blocked_reason)
                raise HomeAssistantError("Fixture statistics differ from saved evidence; no automatic repair")
            try:
                plan, corrections = await self._hass.async_add_executor_job(
                    _plan, self._state, events, self._device_id, force_reimport
                )
            except (ValueError, TypeError, OverflowError) as err:
                raise HomeAssistantError(f"Invalid fixture observations: {err}") from err
            if not dry_run:
                await self._refresh_names()
            before = self.current_checkpoint_ms()
            merged = self._state.events | plan.updates
            after = max((value["end_ms"] for value in merged.values()), default=0)
            result = {
                "imported_rows": sum(len(rows) for rows in plan.rows.values()),
                "events_fetched": len(events),
                "events_newer_than_checkpoint": sum(
                    value["end_ms"] > before for value in plan.updates.values()
                ),
                "checkpoint_before_ms": before, "checkpoint_after_ms": after,
                "cleared_statistic_ids": 0, "corrections_detected": corrections,
                "cached_events": len(merged),
                "force_reimport": int(force_reimport), "dry_run": int(dry_run),
            }
            if not dry_run and (plan.updates or plan.rows):
                await self._save(self._state, plan)
                self._pending = plan
                await self._finish_pending()
                result["checkpoint_after_ms"] = self.current_checkpoint_ms()
            return result
