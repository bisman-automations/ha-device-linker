"""Device Linker integration setup and registry connection management."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    DATA_MANAGER,
    DOMAIN,
)
from .manager import SuggestionManager, entry_is_link
from .registry import get_device, get_entry_settings

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Attach the shared MAC connection and keep suggestions running."""
    if not entry_is_link(entry):
        # An ignored discovery is stored as a config entry so Home Assistant
        # remembers it. It does not link devices.
        return True

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
    # Count before any further await so two entries unloading together can
    # tell which one is the last and must stop the suggestion manager.
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["link_count"] = domain_data.get("link_count", 0) + 1
    domain_data[entry.entry_id] = unsubscribe

    manager = domain_data.get(DATA_MANAGER)
    if not isinstance(manager, SuggestionManager):
        manager = SuggestionManager(hass)
        domain_data[DATA_MANAGER] = manager
        await manager.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Scan after this setup returns. Scanning inline would abort the discovery
    # flow that is in the middle of creating this entry.
    hass.async_create_task(_scan_after_setup(manager))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop listening while the config entry is unloaded."""
    if not entry_is_link(entry):
        return True

    domain_data = hass.data.get(DOMAIN, {})
    domain_data["link_count"] = domain_data.get("link_count", 1) - 1
    last_link = domain_data["link_count"] <= 0
    unsubscribe = domain_data.pop(entry.entry_id, None)
    if unsubscribe:
        unsubscribe()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    manager = domain_data.get(DATA_MANAGER)
    if isinstance(manager, SuggestionManager):
        manager.async_release_sensor(entry.entry_id)
        if last_link:
            await manager.async_stop()
            domain_data.pop(DATA_MANAGER, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove only MAC connections this entry added to linked devices."""
    if not entry_is_link(entry):
        return

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

    manager = async_get_manager_if_running(hass)
    if manager is not None:
        await manager.async_scan()


async def _scan_after_setup(manager: SuggestionManager) -> None:
    """Run one scan after the config flow that created this entry has finished.

    Yielding once lets the flow manager remove the discovery flow before the
    scan looks at in-progress flows. Aborting that flow while it is still
    returning its new entry raises UnknownFlow.
    """
    await asyncio.sleep(0)
    try:
        await manager.async_scan()
    except Exception:
        _LOGGER.exception("Suggestion scan failed during setup")


def async_get_manager_if_running(hass: HomeAssistant) -> SuggestionManager | None:
    """Return the manager without importing it again at call sites that have it."""
    domain_data = hass.data.get(DOMAIN, {})
    manager = domain_data.get(DATA_MANAGER)
    if isinstance(manager, SuggestionManager):
        return manager
    return None
