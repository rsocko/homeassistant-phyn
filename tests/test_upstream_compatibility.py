"""Regression coverage for merging fixture usage with current upstream."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, create_autospec

from aiophyn.device import Device
import pytest
from homeassistant.helpers.target import TargetSelection

from custom_components.phyn import services
from custom_components.phyn.const import CLIENT, DOMAIN
from custom_components.phyn.devices.pp import PhynPlusDevice


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dry_run", "force_reimport", "handler"),
    [
        (False, False, "async_import_events"),
        (False, True, "async_force_reimport_events"),
        (True, True, "async_preview_import_events"),
    ],
)
async def test_fixture_import_uses_millisecond_api(
    hass, dry_run, force_reimport, handler
):
    """All import modes use the published timestamp API without changing events."""
    events = [{"id": "event_1", "total_flow": 1.5}]
    api_device = create_autospec(Device, instance=True)
    fetch = api_device.get_water_usage_events
    fetch.return_value = events
    coordinator = SimpleNamespace(
        hass=hass,
        api_client=SimpleNamespace(device=api_device),
    )
    device = PhynPlusDevice(coordinator, "home_1", "device_1", "PP1")
    importer = SimpleNamespace(
        async_initialize=AsyncMock(),
        async_import_events=AsyncMock(return_value={"imported_rows": 1}),
        async_force_reimport_events=AsyncMock(return_value={"imported_rows": 1}),
        async_preview_import_events=AsyncMock(return_value={"imported_rows": 1}),
    )
    device._fixture_stats_importer = importer
    start = datetime(2026, 9, 1, tzinfo=timezone(timedelta(hours=-4)))
    end = start + timedelta(days=1)

    result = await device.async_import_fixture_statistics(
        from_datetime=start,
        to_datetime=end,
        dry_run=dry_run,
        force_reimport=force_reimport,
    )

    fetch.assert_awaited_once_with(
        "device_1",
        from_ts=1788235200000,
        to_ts=1788321600000,
    )
    if dry_run:
        importer.async_preview_import_events.assert_awaited_once_with(
            events, force_reimport=force_reimport
        )
    else:
        getattr(importer, handler).assert_awaited_once_with(events)
    for other in vars(importer):
        if other not in (handler, "async_initialize"):
            getattr(importer, other).assert_not_awaited()
    importer.async_initialize.assert_awaited_once()
    assert result == {"imported_rows": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("extended", [False, True])
async def test_leak_test_preserves_boolean_and_current_target_helper(
    monkeypatch, extended
):
    """The merge retains extended tests and the new Home Assistant target API."""
    run_leak_test = AsyncMock(return_value={"code": "success"})
    hass = SimpleNamespace(
        data={DOMAIN: {CLIENT: SimpleNamespace(
            device=SimpleNamespace(run_leak_test=run_leak_test)
        )}}
    )
    service = SimpleNamespace(
        hass=hass, data={"entity_id": "valve.phyn", "extended": extended}
    )
    extract = Mock(return_value=SimpleNamespace(referenced={"valve.phyn"}))
    monkeypatch.setattr(services, "async_extract_referenced_entity_ids", extract)
    monkeypatch.setattr(
        services.er,
        "async_get",
        Mock(return_value=SimpleNamespace(
            async_get=Mock(return_value=SimpleNamespace(device_id="ha_device"))
        )),
    )
    monkeypatch.setattr(
        services.dr,
        "async_get",
        Mock(return_value=SimpleNamespace(
            async_get=Mock(return_value=SimpleNamespace(
                identifiers={(DOMAIN, "phyn_device")}
            ))
        )),
    )
    monkeypatch.setattr(services, "async_add_logbook_entry", AsyncMock())

    await services.phyn_leak_test(service)

    assert isinstance(extract.call_args.args[1], TargetSelection)
    run_leak_test.assert_awaited_once_with("phyn_device", extended)
    assert run_leak_test.await_args.args[1] is extended


@pytest.mark.asyncio
async def test_fixture_services_remain_registered(hass):
    """Both fixture services survive the conflict with upstream leak-test setup."""
    await services.phyn_leak_test_service_setup(hass)

    for name in ("leak_test", "import_fixture_statistics", "reload_fixture_statistics"):
        assert hass.services.has_service(DOMAIN, name)


@pytest.mark.parametrize("value", [{"v": 123.456, "ts": 1}, 123.456, None])
def test_lifetime_consumption_retains_upstream_fix(value):
    """The lifetime reading handles REST and MQTT state even without RT consumption."""
    device = object.__new__(PhynPlusDevice)
    device._device_state = {"consumption": value}
    device._rt_device_state = {}

    assert device.consumption == (123.45 if value is not None else None)


@pytest.mark.asyncio
async def test_autoshutoff_uses_upstream_method_name(hass):
    """The paired library retains the upstream misspelled method, without an alias."""
    api_device = create_autospec(Device, instance=True)
    api_device.get_autoshuftoff_status.return_value = {"auto_shutoff_enable": True}
    device = PhynPlusDevice(
        SimpleNamespace(hass=hass, api_client=SimpleNamespace(device=api_device)),
        "home_1", "device_1", "PP1",
    )

    await device._update_autoshutoff()

    api_device.get_autoshuftoff_status.assert_awaited_once_with("device_1")
    assert device.autoshutoff_enabled is True
