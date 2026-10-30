"""Device registry access, link settings, and finding the hub's links."""

from collections.abc import Mapping
import re
from typing import Any

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceEntry,
    DeviceEntryType,
    DeviceRegistry,
    format_mac,
)

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
    MAC_PATTERN,
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


_BARE_MAC = re.compile(r"[0-9A-Fa-f]{12}")
_PARTIAL_MAC = re.compile(r"[0-9A-Fa-f]{1,2}(?:[:-][0-9A-Fa-f]{0,2})+")
# Longest shared ID accepted in place of a MAC address (a VIN is 17 characters).
MAX_SHARED_ID_LENGTH = 100


def is_service(device: DeviceEntry) -> bool:
    """Whether this is a service (an integration's own helper), not hardware.

    Daily Routine's routines, Ford Assistant's vehicle helper and Device
    Linker's own devices are services. Only devices are linked; services that
    belong with a device link themselves.
    """
    return device.entry_type is DeviceEntryType.SERVICE


def normalize_link_value(raw: object) -> str | None:
    """The value a link shares: a MAC address, or another shared ID.

    A MAC address (colons, hyphens or bare hex) is stored lower-case with
    colons and becomes a network connection on each device. Anything else, such
    as a serial number or a VIN, is stored as typed and becomes a
    ``(device_linker, value)`` identifier. Home Assistant shows devices that
    share any identifier or connection as linked devices.
    """
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > MAX_SHARED_ID_LENGTH:
        return None
    if MAC_PATTERN.fullmatch(value) or _BARE_MAC.fullmatch(value):
        return format_mac(value)
    if _PARTIAL_MAC.fullmatch(value):
        return None  # a MAC address with digits missing, not a shared ID
    return value


def is_mac(value: str) -> bool:
    """Whether a stored link value is a MAC address rather than a shared ID."""
    return bool(MAC_PATTERN.fullmatch(value)) and format_mac(value) == value


def link_item(value: str) -> tuple[str, tuple[str, str]]:
    """The registry field (``connections`` or ``identifiers``) and the entry it gets."""
    if is_mac(value):
        return "connections", (CONNECTION_NETWORK_MAC, value)
    return "identifiers", (DOMAIN, value)


def device_has_link(device: DeviceEntry, value: str) -> bool:
    """Whether the device already carries this link's MAC address or shared ID."""
    field, item = link_item(value)
    return item in getattr(device, field)


def already_linked(first: DeviceEntry, second: DeviceEntry) -> bool:
    """True when Home Assistant already shows these two devices as linked.

    Home Assistant links devices that share any identifier or connection,
    such as the Ford integration and Ford Assistant both using a VIN.
    """
    return bool(
        set(first.identifiers) & set(second.identifiers)
        or set(first.connections) & set(second.connections)
    )


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
