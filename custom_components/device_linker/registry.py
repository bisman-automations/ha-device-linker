"""Device registry access, link settings, and finding the hub's links."""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry, DeviceRegistry

from .const import (
    CONF_ADDED_CONNECTION,
    CONF_ADDED_CONNECTIONS,
    CONF_DEVICE_ID,
    CONF_HUB,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_ORIGIN,
    CONF_PEER_DEVICE_ID,
    CONF_TARGET_DEVICES,
    DOMAIN,
    ORIGIN_MANUAL,
    SUBENTRY_LINK,
)


def get_device(registry: DeviceRegistry, device_id: str) -> DeviceEntry | None:
    """Get a stored device, excluding synthetic composite devices on newer HA."""
    device = registry.async_get(device_id)
    if device is not None and getattr(device, "is_composite_device", False):
        return None
    return device


def get_entry_settings(entry: ConfigEntry) -> dict[str, object]:
    """Settings of a pre-2.0.0 link entry, which stored the link itself."""
    return link_settings({**entry.data, **entry.options})


def link_settings(data: Mapping[str, Any]) -> dict[str, object]:
    """Return normalized link settings, including compatibility with old data."""
    settings = dict(data)
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

    origin = settings.get(CONF_ORIGIN, ORIGIN_MANUAL)
    if origin not in {"manual", "auto"}:
        origin = ORIGIN_MANUAL
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
        # Entries saved before 1.3.0 have no origin. They were created by a
        # person, and automatic linking must not relabel them.
        CONF_ORIGIN: origin,
    }


def link_unique_id(main_device_id: str | None, mac: str) -> str:
    """Unique id for the one entry that shares this anchor and MAC."""
    return f"{main_device_id or 'manual'}_{mac}"


def link_title(main_name: str, count: int) -> str:
    """Title used for manual and automatic entries, such as ``Link TV to 2 devices``."""
    noun = "device" if count == 1 else "devices"
    return f"Link {main_name} to {count} {noun}"


def is_hub(entry: ConfigEntry) -> bool:
    """Whether this is the Device Linker entry that holds every link."""
    return entry.source != SOURCE_IGNORE and entry.data.get(CONF_HUB) is True


def hub_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The hub entry, if Device Linker is set up."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if is_hub(entry):
            return entry
    return None


def hub_links(entry: ConfigEntry) -> dict[str, dict[str, object]]:
    """Normalized settings of each link subentry, by subentry id."""
    links: dict[str, dict[str, object]] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_LINK:
            continue
        try:
            links[subentry_id] = link_settings(subentry.data)
        except KeyError:
            continue
    return links


def iter_links(
    hass: HomeAssistant,
) -> list[tuple[ConfigEntry, ConfigSubentry, dict[str, object]]]:
    """Every link: the hub, the subentry, and its normalized settings."""
    hub = hub_entry(hass)
    if hub is None:
        return []
    links = hub_links(hub)
    return [
        (hub, hub.subentries[subentry_id], settings)
        for subentry_id, settings in links.items()
    ]
