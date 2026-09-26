"""Regression coverage for ordinary Phyn Logbook entries."""

from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import callback
from homeassistant.loader import async_get_integration
import pytest

from custom_components.phyn.logbook_helpers import async_add_logbook_entry


async def test_ha_loader_does_not_discover_a_phyn_logbook_platform(hass):
    """Use HA's actual discovery rather than importing the helper as a platform."""
    integration = await async_get_integration(hass, "phyn")

    assert integration.pkg_path == "custom_components.phyn"
    assert integration.platforms_exists(("sensor", "logbook")) == ["sensor"]


@pytest.mark.parametrize("entity_id", [None, "sensor.phyn_daily_water_usage"])
async def test_logbook_entry_uses_builtin_event(hass, entity_id):
    """Generic entries need no Phyn-specific Logbook platform."""
    events = []

    @callback
    def capture(event):
        events.append(event)

    unsubscribe = hass.bus.async_listen(EVENT_LOGBOOK_ENTRY, capture)
    try:
        await async_add_logbook_entry(
            hass, "Fixture import completed", entity_id=entity_id
        )
        await hass.async_block_till_done()
    finally:
        unsubscribe()

    expected = {
        "name": "Phyn",
        "message": "Fixture import completed",
        "domain": "phyn",
    }
    if entity_id is not None:
        expected["entity_id"] = entity_id
    assert len(events) == 1
    assert events[0].data == expected
