"""Diagnostics for Device Linker: its links, settings, and suggestions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_ORIGIN,
)
from .manager import async_get_manager, async_load_settings, auto_linked_summaries
from .registry import hub_links, is_hub

TO_REDACT = {CONF_MAC_ADDRESS, "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return each link, the settings, and the current suggestions.

    MAC addresses are redacted. Device names are included so a report shows
    which devices were suggested; review the file before sharing it.
    """
    if not is_hub(entry):
        return {"ignored_suggestion": True}

    links = []
    for subentry_id, settings in hub_links(entry).items():
        added = settings[CONF_ADDED_CONNECTIONS]
        owned = added.values() if isinstance(added, dict) else []
        links.append(
            {
                "title": entry.subentries[subentry_id].title,
                "main_device_set": bool(settings[CONF_MAIN_DEVICE_ID]),
                "linked_device_count": len(settings[CONF_LINKED_DEVICE_IDS]),
                "owned_connection_count": sum(1 for flag in owned if flag),
                CONF_ORIGIN: settings[CONF_ORIGIN],
                CONF_MAC_ADDRESS: settings[CONF_MAC_ADDRESS],
            }
        )
    manager = async_get_manager(hass)
    preferences = (
        manager.settings if manager is not None else await async_load_settings(hass)
    )
    return async_redact_data(
        {
            "links": links,
            "suggestions": {
                "threshold": preferences.threshold,
                "match_hardware": preferences.match_hardware,
                "match_names": preferences.match_names,
                "auto_link": preferences.auto_link,
                "auto_link_similar": preferences.auto_link_similar,
                "auto_link_threshold": preferences.auto_link_threshold,
                "dismissed_count": len(preferences.dismissed),
                "unlinked_count": len(preferences.unlinked),
                "count": len(manager.suggestions) if manager is not None else 0,
                "last_scan": manager.last_scan if manager is not None else None,
                "items": manager.listed_suggestions() if manager is not None else [],
                "auto_linked": auto_linked_summaries(hass),
            },
        },
        TO_REDACT,
    )
