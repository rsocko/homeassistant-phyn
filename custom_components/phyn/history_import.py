"""Sequential history fetches with correction-safe per-chunk commits."""
from __future__ import annotations

from asyncio import CancelledError, sleep
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientError
from aiophyn.errors import AuthenticationError, RequestError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER
from .fixture_statistics import (
    PhynFixtureStatisticsImporter, extract_event_id, normalize_fixture_events,
)

CHUNK_MS = 7 * 24 * 60 * 60 * 1000
CHUNK_DELAY_SECONDS = 1
ImportProgressCallback = Callable[[dict[str, int]], Awaitable[None]]


def history_windows(start_ms: int, end_ms: int) -> Iterator[tuple[int, int]]:
    """Overlap internal boundaries by one API timestamp unit; never clip events."""
    if start_ms >= end_ms:
        raise HomeAssistantError("Fixture import needs a positive millisecond range")
    cursor = start_ms
    while cursor < end_ms:
        stop = min(cursor + CHUNK_MS, end_ms)
        yield cursor if cursor == start_ms else cursor - 1, stop
        cursor = stop


async def async_import_history(
    hass: HomeAssistant,
    importer: PhynFixtureStatisticsImporter,
    fetch: Callable[..., Awaitable[list[dict[str, Any]]]],
    start_ms: int,
    end_ms: int,
    *,
    force_reimport: bool = False,
    dry_run: bool = False,
    progress_callback: ImportProgressCallback | None = None,
) -> dict[str, int]:
    """Commit each verified chunk, or preview the last-observed union once."""
    if start_ms >= end_ms:
        raise HomeAssistantError("Fixture import needs a positive millisecond range")
    total = (end_ms - start_ms + CHUNK_MS - 1) // CHUNK_MS
    progress = {
        "chunks_total": total, "chunks_completed": 0,
        "events_fetched": 0, "events_unique": 0, "duplicate_events": 0,
        "imported_rows": 0, "corrections_detected": 0,
        "remaining_start_ms": start_ms, "remaining_end_ms": end_ms,
    }
    result = {
        "imported_rows": 0, "events_newer_than_checkpoint": 0,
        "checkpoint_before_ms": 0, "checkpoint_after_ms": 0,
        "cleared_statistic_ids": 0, "corrections_detected": 0, "cached_events": 0,
        "force_reimport": int(force_reimport), "dry_run": int(dry_run),
    }
    seen: set[str] = set()
    preview_events: dict[str, dict[str, Any]] = {}

    async def report() -> None:
        if progress_callback is not None and not dry_run:
            await progress_callback(dict(progress))

    try:
        for index, (query_start, query_end) in enumerate(history_windows(start_ms, end_ms)):
            if index:
                await sleep(CHUNK_DELAY_SECONDS)
            progress["remaining_start_ms"] = query_start
            await report()
            events = await fetch(from_ts=query_start, to_ts=query_end)
            if dry_run:
                # Reject contradictions within one response, but allow a later
                # response to revise an earlier observation of the same ID.
                await hass.async_add_executor_job(normalize_fixture_events, events)
            else:
                if force_reimport:
                    chunk = await importer.async_force_reimport_events(events)
                else:
                    chunk = await importer.async_import_events(events)
                if index == 0:
                    result["checkpoint_before_ms"] = chunk.get("checkpoint_before_ms", 0)
                for key in (
                    "imported_rows", "events_newer_than_checkpoint",
                    "cleared_statistic_ids", "corrections_detected",
                ):
                    result[key] += chunk.get(key, 0)
                for key in ("checkpoint_after_ms", "cached_events"):
                    result[key] = chunk.get(key, 0)
            for event in events:
                identifier = extract_event_id(event)
                assert identifier is not None  # Already validated by the importer.
                seen.add(identifier)
                if dry_run:
                    preview_events[identifier] = event
            progress.update(
                chunks_completed=index + 1,
                events_fetched=progress["events_fetched"] + len(events),
                events_unique=len(seen),
                imported_rows=result["imported_rows"],
                corrections_detected=result["corrections_detected"],
                remaining_start_ms=query_end if query_end == end_ms else query_end - 1,
            )
            progress["duplicate_events"] = progress["events_fetched"] - len(seen)
            await report()
        if dry_run:
            progress["remaining_start_ms"] = start_ms
            result = await importer.async_preview_import_events(
                list(preview_events.values()), force_reimport=force_reimport,
            )
            progress["remaining_start_ms"] = end_ms
            progress["imported_rows"] = result.get("imported_rows", 0)
            progress["corrections_detected"] = result.get("corrections_detected", 0)
        return {**result, **progress}
    except CancelledError:
        LOGGER.info(
            "History request interrupted after %s/%s chunks; remaining range %s..%s",
            progress["chunks_completed"], total, progress["remaining_start_ms"], end_ms,
        )
        raise
    except (
        HomeAssistantError, AuthenticationError, RequestError, ClientError,
        OSError, ValueError, TimeoutError,
    ) as err:
        remaining = datetime.fromtimestamp(
            progress["remaining_start_ms"] / 1000, timezone.utc
        ).isoformat()
        end = datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat()
        disposition = (
            "Dry run did not change statistics or saved evidence."
            if dry_run else
            "Earlier verified chunks are retained; the current chunk may need pending recovery."
        )
        raise HomeAssistantError(
            f"History import stopped after {progress['chunks_completed']}/{total} chunks. "
            f"Retry the original range or the remaining range {remaining} to {end}. {disposition}"
        ) from err
