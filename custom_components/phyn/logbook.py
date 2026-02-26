"""Logbook helper utilities for the Phyn integration."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN

try:
    from homeassistant.components.logbook.const import EVENT_LOGBOOK_ENTRY
except ImportError:
    from homeassistant.components.logbook import EVENT_LOGBOOK_ENTRY  # type: ignore


async def async_add_logbook_entry(
    hass: HomeAssistant,
    message: str,
    *,
    name: str = "Phyn",
    entity_id: str | None = None,
) -> None:
    """Add a message to Home Assistant Logbook."""
    data: dict[str, str] = {
        "name": name,
        "message": message,
        "domain": DOMAIN,
    }
    if entity_id:
        data["entity_id"] = entity_id
    hass.bus.async_fire(EVENT_LOGBOOK_ENTRY, data)
