"""Tests for fixture statistics service workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.phyn.const import DOMAIN
from custom_components.phyn.services import (
    phyn_import_fixture_statistics,
    phyn_reload_fixture_statistics,
)


class _FakeDevice:
    """Minimal fake device supporting fixture statistic imports."""

    def __init__(self, device_id: str, result: dict[str, int]) -> None:
        self.id = device_id
        self.async_import_fixture_statistics = AsyncMock(return_value=result)


def _make_service(data: dict, devices: list[_FakeDevice]) -> SimpleNamespace:
    """Create a fake Home Assistant service call object."""
    coordinator = SimpleNamespace(devices=devices)
    hass = SimpleNamespace(data={DOMAIN: {"coordinator": coordinator}})
    return SimpleNamespace(hass=hass, data=data)


@pytest.mark.asyncio
async def test_reload_fixture_statistics_defaults_force_reimport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reload service should always force re-import using a default 7-day window."""
    fake_device = _FakeDevice(
        "device_1",
        {
            "imported_rows": 3,
            "events_fetched": 5,
            "events_newer_than_checkpoint": 5,
            "checkpoint_before_ms": 1,
            "checkpoint_after_ms": 2,
            "cleared_statistic_ids": 2,
            "corrections_detected": 1,
            "cached_events": 10,
        },
    )
    service = _make_service({}, [fake_device])
    monkeypatch.setattr(
        "custom_components.phyn.services.async_add_logbook_entry",
        AsyncMock(),
    )

    response = await phyn_reload_fixture_statistics(service)

    fake_device.async_import_fixture_statistics.assert_awaited_once()
    call = fake_device.async_import_fixture_statistics.await_args
    assert call.kwargs["force_reimport"] is True
    assert call.kwargs["dry_run"] is False
    assert isinstance(call.kwargs["from_datetime"], datetime)
    assert isinstance(call.kwargs["to_datetime"], datetime)
    assert call.kwargs["from_datetime"].tzinfo == timezone.utc
    assert call.kwargs["to_datetime"].tzinfo == timezone.utc
    assert timedelta(days=6, hours=23, minutes=59) <= (
        call.kwargs["to_datetime"] - call.kwargs["from_datetime"]
    ) <= timedelta(days=7, minutes=1)
    assert response["force_reimport"] is True
    assert response["total_corrections_detected"] == 1


@pytest.mark.asyncio
async def test_import_fixture_statistics_force_requires_explicit_timeframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import service should reject force_reimport without explicit days/range."""
    fake_device = _FakeDevice("device_1", {"imported_rows": 0})
    service = _make_service({"force_reimport": True}, [fake_device])
    monkeypatch.setattr(
        "custom_components.phyn.services.async_add_logbook_entry",
        AsyncMock(),
    )

    with pytest.raises(HomeAssistantError):
        await phyn_import_fixture_statistics(service)


@pytest.mark.asyncio
async def test_import_fixture_statistics_returns_aggregated_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import service should aggregate diagnostics from all target devices."""
    device_1 = _FakeDevice(
        "device_1",
        {
            "imported_rows": 2,
            "events_fetched": 4,
            "events_newer_than_checkpoint": 2,
            "checkpoint_before_ms": 10,
            "checkpoint_after_ms": 20,
            "cleared_statistic_ids": 0,
            "corrections_detected": 0,
            "cached_events": 7,
        },
    )
    device_2 = _FakeDevice(
        "device_2",
        {
            "imported_rows": 3,
            "events_fetched": 6,
            "events_newer_than_checkpoint": 3,
            "checkpoint_before_ms": 30,
            "checkpoint_after_ms": 40,
            "cleared_statistic_ids": 1,
            "corrections_detected": 2,
            "cached_events": 11,
        },
    )
    service = _make_service({"days": 2, "dry_run": True}, [device_1, device_2])
    monkeypatch.setattr(
        "custom_components.phyn.services.async_add_logbook_entry",
        AsyncMock(),
    )

    response = await phyn_import_fixture_statistics(service)

    assert response["dry_run"] is True
    assert response["force_reimport"] is False
    assert response["total_rows"] == 5
    assert response["total_events_fetched"] == 10
    assert response["total_events_newer_than_checkpoint"] == 5
    assert response["total_cleared_statistic_ids"] == 1
    assert response["total_corrections_detected"] == 2
    assert len(response["devices"]) == 2