"""Services for the phyn integration"""

import datetime
from datetime import timedelta, timezone
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.service import async_extract_referenced_entity_ids

from .const import CLIENT, DOMAIN, LOGGER
from .logbook import async_add_logbook_entry


def _resolve_device_id_from_entity(hass: HomeAssistant, entity_id: str) -> str:
    """Resolve a Phyn device_id from a Home Assistant entity_id."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    if entity_entry is None:
        raise HomeAssistantError(f"Unknown entity_id: {entity_id}")

    if entity_entry.device_id is None:
        raise HomeAssistantError(f"Entity {entity_id} is not linked to a device")

    device = device_registry.async_get(entity_entry.device_id)
    if device is None:
        raise HomeAssistantError(f"Device registry entry not found for {entity_id}")

    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier

    raise HomeAssistantError(f"Entity {entity_id} is not a {DOMAIN} device")

async def phyn_leak_test(service: ServiceCall):
    """Handle the service call."""
    try:
        ref = async_extract_referenced_entity_ids(service.hass, service)
        entity_registry = er.async_get(service.hass)
        device_registry = dr.async_get(service.hass)

        entity_id = ref.referenced.pop()
        valve = entity_registry.async_get(entity_id)
        if valve is None or valve.device_id is None:
            raise HomeAssistantError(f"Unable to resolve valve device for entity: {entity_id}")

        device = device_registry.async_get(valve.device_id)
        if device is None:
            raise HomeAssistantError(f"Device not found for entity: {entity_id}")

        device_id = None
        extended_test = bool(service.data.get('extended', False))
        for x in device.identifiers:
            if x[0] == DOMAIN:
                device_id = x[1]
                break

        if device_id is None:
            raise HomeAssistantError(f"Entity {entity_id} is not a {DOMAIN} device")

        client = service.hass.data[DOMAIN][CLIENT]
        LOGGER.info(
            "Service phyn.leak_test starting (device_id=%s, extended=%s)",
            device_id,
            extended_test,
        )
        result = await client.device.run_leak_test(device_id, extended_test)
        if result.get("code") != "success":
            raise HomeAssistantError(f"Leak test API did not return success: {result}")

        LOGGER.info(
            "Service phyn.leak_test completed (device_id=%s, extended=%s)",
            device_id,
            extended_test,
        )
        await async_add_logbook_entry(
            service.hass,
            f"Leak test started for device {device_id} (extended={extended_test})",
            entity_id=entity_id,
        )
    except Exception as err:
        LOGGER.exception("Service phyn.leak_test failed: %s", err)
        await async_add_logbook_entry(
            service.hass,
            f"Leak test failed: {err}",
        )
        raise


async def phyn_import_fixture_statistics(service: ServiceCall) -> ServiceResponse:
    """Manually import fixture statistics from water usage events."""
    try:
        coordinator = service.hass.data[DOMAIN]["coordinator"]

        entity_id = service.data.get("entity_id")
        target_device_id = service.data.get("device_id")
        days = int(service.data.get("days", 1))
        start_dt = service.data.get("start_datetime")
        end_dt = service.data.get("end_datetime")
        force_reimport = bool(service.data.get("force_reimport", False))
        dry_run = bool(service.data.get("dry_run", False))

        has_explicit_days = "days" in service.data
        has_explicit_range = start_dt is not None and end_dt is not None

        if (start_dt is None) ^ (end_dt is None):
            raise HomeAssistantError("Provide both start_datetime and end_datetime, or neither")

        if force_reimport and not (has_explicit_range or has_explicit_days):
            raise HomeAssistantError(
                "force_reimport requires an explicit timeframe. Provide days or start_datetime/end_datetime"
            )

        if start_dt is not None and end_dt is not None:
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if start_dt >= end_dt:
                raise HomeAssistantError("start_datetime must be before end_datetime")
        else:
            end_dt = datetime.datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(days=days)

        if entity_id:
            target_device_id = _resolve_device_id_from_entity(service.hass, entity_id)

        target_devices = []
        for device in coordinator.devices:
            importer = getattr(device, "async_import_fixture_statistics", None)
            if importer is None:
                continue
            if target_device_id and device.id != target_device_id:
                continue
            target_devices.append(device)

        if not target_devices:
            raise HomeAssistantError("No compatible Phyn Plus devices found for import")

        LOGGER.info(
            "Service phyn.import_fixture_statistics starting (devices=%s, range=%s..%s, force_reimport=%s, dry_run=%s)",
            len(target_devices),
            start_dt.isoformat(),
            end_dt.isoformat(),
            force_reimport,
            dry_run,
        )

        results: list[dict] = []
        total_rows = 0
        total_events_fetched = 0
        total_events_newer_than_checkpoint = 0
        total_cleared_statistic_ids = 0

        for device in target_devices:
            import_result = await device.async_import_fixture_statistics(
                from_datetime=start_dt,
                to_datetime=end_dt,
                force_reimport=force_reimport,
                dry_run=dry_run,
            )
            imported = int(import_result.get("imported_rows", 0))
            events_fetched = int(import_result.get("events_fetched", 0))
            newer_events = int(import_result.get("events_newer_than_checkpoint", 0))
            checkpoint_before = int(import_result.get("checkpoint_before_ms", 0))
            checkpoint_after = int(import_result.get("checkpoint_after_ms", 0))
            cleared_statistic_ids = int(import_result.get("cleared_statistic_ids", 0))

            total_rows += imported
            total_events_fetched += events_fetched
            total_events_newer_than_checkpoint += newer_events
            total_cleared_statistic_ids += cleared_statistic_ids
            results.append(
                {
                    "device_id": device.id,
                    "imported_rows": imported,
                    "events_fetched": events_fetched,
                    "events_newer_than_checkpoint": newer_events,
                    "checkpoint_before_ms": checkpoint_before,
                    "checkpoint_after_ms": checkpoint_after,
                    "cleared_statistic_ids": cleared_statistic_ids,
                    "force_reimport": 1 if force_reimport else 0,
                    "dry_run": 1 if dry_run else 0,
                }
            )

        LOGGER.info(
            "Service phyn.import_fixture_statistics complete (rows=%s, fetched_events=%s, newer_than_checkpoint=%s, cleared_statistic_ids=%s, devices=%s, force_reimport=%s, dry_run=%s)",
            total_rows,
            total_events_fetched,
            total_events_newer_than_checkpoint,
            total_cleared_statistic_ids,
            len(results),
            force_reimport,
            dry_run,
        )

        note = None
        if total_rows == 0 and total_events_fetched > 0 and total_events_newer_than_checkpoint == 0:
            note = "No rows imported because all events were at or before the importer checkpoint. Use a force-reimport workflow to rebuild historical ranges."
        if force_reimport and not dry_run:
            note = "Force reimport mode enabled: cleared existing fixture statistics for target devices and rebuilt from requested timeframe."
        elif force_reimport and dry_run:
            note = "Dry run: no data was modified. Response shows projected clears/imports for force reimport mode."
        elif dry_run:
            note = "Dry run: no data was modified. Response shows projected imports for incremental mode."

        await async_add_logbook_entry(
            service.hass,
            (
                f"import_fixture_statistics complete: rows={total_rows}, "
                f"events={total_events_fetched}, newer={total_events_newer_than_checkpoint}, "
                f"clears={total_cleared_statistic_ids}, force_reimport={force_reimport}, dry_run={dry_run}"
            ),
        )

        return {
            "start_datetime": start_dt.isoformat(),
            "end_datetime": end_dt.isoformat(),
            "force_reimport": force_reimport,
            "dry_run": dry_run,
            "devices": results,
            "total_rows": total_rows,
            "total_events_fetched": total_events_fetched,
            "total_events_newer_than_checkpoint": total_events_newer_than_checkpoint,
            "total_cleared_statistic_ids": total_cleared_statistic_ids,
            "note": note,
        }
    except Exception as err:
        LOGGER.exception("Service phyn.import_fixture_statistics failed: %s", err)
        await async_add_logbook_entry(
            service.hass,
            f"import_fixture_statistics failed: {err}",
        )
        raise

async def phyn_leak_test_service_setup(hass: HomeAssistant):
    """Setup services for phyn integration."""
    hass.services.async_register(
        DOMAIN,
        "leak_test",
        phyn_leak_test,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.entity_id,
            vol.Optional("extended"): bool
        }),
        supports_response=SupportsResponse.NONE
    )

    hass.services.async_register(
        DOMAIN,
        "import_fixture_statistics",
        phyn_import_fixture_statistics,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.entity_id,
                vol.Optional("device_id"): cv.string,
                vol.Optional("days", default=1): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=365)
                ),
                vol.Optional("start_datetime"): cv.datetime,
                vol.Optional("end_datetime"): cv.datetime,
                vol.Optional("force_reimport", default=False): cv.boolean,
                vol.Optional("dry_run", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
