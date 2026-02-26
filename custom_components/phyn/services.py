"""Services for the phyn integration"""

import datetime
from datetime import timedelta, timezone
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.service import async_extract_referenced_entity_ids

from .const import CLIENT, DOMAIN, LOGGER


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
    entity = service.data['entity_id']

    ref = async_extract_referenced_entity_ids(service.hass, service)
    entity_registry = er.async_get(service.hass)
    device_registry = dr.async_get(service.hass)
    
    entity_id = ref.referenced.pop()
    valve = entity_registry.async_get(entity_id)
    device = device_registry.async_get(valve.device_id)
    
    device_id = None
    extended_test = bool(service.data.get('extended', False))
    for x in device.identifiers:
        if x[0] == "phyn":
            device_id = x[1]
            break
    assert device_id is not None
    
    client = service.hass.data[DOMAIN][CLIENT]
    LOGGER.debug("Running leak test for device_id: %s (extended: %s)", device_id, extended_test)
    result = await client.device.run_leak_test(device_id, extended_test)
    assert 'code' in result and result['code'] == 'success'


async def phyn_import_fixture_statistics(service: ServiceCall) -> ServiceResponse:
    """Manually import fixture statistics from water usage events."""
    coordinator = service.hass.data[DOMAIN]["coordinator"]

    entity_id = service.data.get("entity_id")
    target_device_id = service.data.get("device_id")
    days = int(service.data.get("days", 1))
    start_dt = service.data.get("start_datetime")
    end_dt = service.data.get("end_datetime")

    if (start_dt is None) ^ (end_dt is None):
        raise HomeAssistantError("Provide both start_datetime and end_datetime, or neither")

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

    results: list[dict] = []
    total_rows = 0

    for device in target_devices:
        imported = await device.async_import_fixture_statistics(
            from_datetime=start_dt,
            to_datetime=end_dt,
        )
        total_rows += imported
        results.append({"device_id": device.id, "imported_rows": imported})

    LOGGER.debug(
        "Manual fixture stats import complete: %s rows across %s device(s)",
        total_rows,
        len(results),
    )
    return {
        "start_datetime": start_dt.isoformat(),
        "end_datetime": end_dt.isoformat(),
        "devices": results,
        "total_rows": total_rows,
    }

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
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
