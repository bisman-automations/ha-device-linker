"""Device Linker integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ADDED_CONNECTION,
    CONF_DEVICE_ID,
    CONF_MAC_ADDRESS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Attach the selected MAC connection to the selected device."""
    registry = dr.async_get(hass)
    device_id = entry.data[CONF_DEVICE_ID]
    connection = (dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC_ADDRESS])
    device = registry.async_get(device_id, include_composite_devices=False)
    if device is None:
        raise ConfigEntryNotReady("The selected Home Assistant device no longer exists")

    if connection not in device.connections:
        try:
            registry.async_update_device(
                device.id,
                new_connections=device.connections | {connection},
            )
        except dr.DeviceConnectionCollisionError as err:
            raise ConfigEntryNotReady(
                "The MAC address conflicts with another device from the same integration"
            ) from err

    @callback
    def _restore_link_if_owner_refreshes(event: Event) -> None:
        """Keep the shared connection after the owning integration refreshes its device."""
        if event.data.get("device_id") != device_id or not entry.data[CONF_ADDED_CONNECTION]:
            return
        current = registry.async_get(device_id, include_composite_devices=False)
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
                entry.data[CONF_MAC_ADDRESS],
                device_id,
            )

    unsubscribe = hass.bus.async_listen(
        dr.EVENT_DEVICE_REGISTRY_UPDATED, _restore_link_if_owner_refreshes
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
    """Remove the connection only when the user removes this config entry."""
    if not entry.data.get(CONF_ADDED_CONNECTION):
        return

    registry = dr.async_get(hass)
    device = registry.async_get(
        entry.data[CONF_DEVICE_ID], include_composite_devices=False
    )
    connection = (dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC_ADDRESS])
    if device is not None and connection in device.connections:
        registry.async_update_device(
            device.id,
            new_connections=device.connections - {connection},
        )
