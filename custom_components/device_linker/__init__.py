"""Device Linker integration setup and registry connection management."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    DOMAIN,
)
from .registry import get_device, get_entry_settings

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Attach the shared MAC connection to all selected linked devices."""
    registry = dr.async_get(hass)
    settings = get_entry_settings(entry)
    linked_device_ids = list(settings[CONF_LINKED_DEVICE_IDS])
    added_connections = dict(settings[CONF_ADDED_CONNECTIONS])
    connection = (dr.CONNECTION_NETWORK_MAC, settings[CONF_MAC_ADDRESS])

    for device_id in linked_device_ids:
        device = get_device(registry, device_id)
        if device is None:
            raise ConfigEntryNotReady(
                f"Linked device {device_id} no longer exists in the device registry"
            )
        if (
            added_connections.get(device_id, False)
            and connection not in device.connections
        ):
            try:
                registry.async_update_device(
                    device.id,
                    new_connections=device.connections | {connection},
                )
            except dr.DeviceConnectionCollisionError as err:
                raise ConfigEntryNotReady(
                    "The shared MAC address conflicts with another device from the "
                    "same integration"
                ) from err

    @callback
    def _restore_owned_links(event: Event) -> None:
        """Restore only connections Device Linker originally added."""
        device_id = event.data.get("device_id")
        if device_id not in linked_device_ids or not added_connections.get(
            device_id, False
        ):
            return
        current = get_device(registry, device_id)
        if current is None or connection in current.connections:
            return
        try:
            registry.async_update_device(
                current.id,
                new_connections=current.connections | {connection},
            )
        except dr.DeviceConnectionCollisionError:
            _LOGGER.warning(
                "Could not restore linked MAC %s on device %s because it is already "
                "assigned to another device in the same integration",
                settings[CONF_MAC_ADDRESS],
                device_id,
            )

    unsubscribe = hass.bus.async_listen(
        dr.EVENT_DEVICE_REGISTRY_UPDATED, _restore_owned_links
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = unsubscribe
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop listening while the config entry is unloaded."""
    unsubscribe = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if unsubscribe:
        unsubscribe()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove only MAC connections this entry added to linked devices."""
    registry = dr.async_get(hass)
    settings = get_entry_settings(entry)
    connection = (dr.CONNECTION_NETWORK_MAC, settings[CONF_MAC_ADDRESS])
    added_connections = dict(settings[CONF_ADDED_CONNECTIONS])

    for device_id in settings[CONF_LINKED_DEVICE_IDS]:
        if not added_connections.get(device_id, False):
            continue
        device = get_device(registry, device_id)
        if device is not None and connection in device.connections:
            registry.async_update_device(
                device.id,
                new_connections=device.connections - {connection},
            )
