"""Diagnostics for a Device Linker config entry and its suggestions."""

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
from .manager import (
    async_get_manager,
    async_load_settings,
    auto_linked_summaries,
    entry_is_link,
)
from .registry import get_entry_settings

TO_REDACT = {CONF_MAC_ADDRESS, "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return link settings and the current suggestions.

    The shared MAC address is redacted. Device names are included so a report
    shows which devices were suggested; review the file before sharing it.
    """
    if not entry_is_link(entry):
        return async_redact_data({"ignored_suggestion": True}, TO_REDACT)

    settings = get_entry_settings(entry)
    added = settings[CONF_ADDED_CONNECTIONS]
    owned = added.values() if isinstance(added, dict) else []
    manager = async_get_manager(hass)
    preferences = (
        manager.settings if manager is not None else await async_load_settings(hass)
    )
    suggestions = manager.listed_suggestions() if manager is not None else []
    return async_redact_data(
        {
            "entry": {
                "main_device_set": bool(settings[CONF_MAIN_DEVICE_ID]),
                "linked_device_count": len(settings[CONF_LINKED_DEVICE_IDS]),
                "owned_connection_count": sum(1 for flag in owned if flag),
                CONF_ORIGIN: settings[CONF_ORIGIN],
                CONF_MAC_ADDRESS: settings[CONF_MAC_ADDRESS],
            },
            "suggestions": {
                "threshold": preferences.threshold,
                "match_hardware": preferences.match_hardware,
                "match_names": preferences.match_names,
                "auto_link": preferences.auto_link,
                "auto_link_threshold": preferences.auto_link_threshold,
                "dismissed_count": len(preferences.dismissed),
                "unlinked_count": len(preferences.unlinked),
                "count": len(manager.suggestions) if manager is not None else 0,
                "last_scan": manager.last_scan if manager is not None else None,
                "items": suggestions,
                "auto_linked": auto_linked_summaries(hass),
            },
        },
        TO_REDACT,
    )
