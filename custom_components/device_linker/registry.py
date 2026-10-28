"""Compatibility helpers for Home Assistant device registry access."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntry, DeviceRegistry

from .const import (
    CONF_ADDED_CONNECTION,
    CONF_ADDED_CONNECTIONS,
    CONF_DEVICE_ID,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_PEER_DEVICE_ID,
    CONF_TARGET_DEVICES,
)


def get_device(registry: DeviceRegistry, device_id: str) -> DeviceEntry | None:
    """Get a stored device, excluding synthetic composite devices on newer HA."""
    device = registry.async_get(device_id)
    if device is not None and getattr(device, "is_composite_device", False):
        return None
    return device


def get_entry_settings(entry: ConfigEntry) -> dict[str, object]:
    """Return normalized settings, including compatibility with old entries."""
    settings = {**entry.data, **entry.options}
    linked_device_ids = settings.get(CONF_LINKED_DEVICE_IDS)
    if linked_device_ids is None:
        if CONF_TARGET_DEVICES in settings:
            linked_device_ids = settings[CONF_TARGET_DEVICES]
        else:
            legacy_device_id = settings.get(CONF_DEVICE_ID)
            linked_device_ids = [legacy_device_id] if legacy_device_id else []

    added_connections = settings.get(CONF_ADDED_CONNECTIONS)
    if added_connections is None:
        if CONF_TARGET_DEVICES in settings:
            # The earlier multi-select build did not track which connections it
            # owned, so preserve existing connections rather than risk removing
            # one supplied by another integration.
            added_connections = {device_id: False for device_id in linked_device_ids}
        else:
            legacy_device_id = settings.get(CONF_DEVICE_ID)
            added_connections = (
                {legacy_device_id: settings.get(CONF_ADDED_CONNECTION, False)}
                if legacy_device_id
                else {}
            )

    return {
        CONF_MAIN_DEVICE_ID: settings.get(
            CONF_MAIN_DEVICE_ID,
            settings.get(
                CONF_DEVICE_ID
                if CONF_TARGET_DEVICES in settings
                else CONF_PEER_DEVICE_ID
            ),
        ),
        CONF_LINKED_DEVICE_IDS: linked_device_ids,
        CONF_MAC_ADDRESS: settings[CONF_MAC_ADDRESS],
        CONF_ADDED_CONNECTIONS: added_connections,
    }
