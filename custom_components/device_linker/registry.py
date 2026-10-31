"""Device registry access, link settings, and finding the hub's links."""

from collections.abc import Iterable, Mapping, Sequence
import re
from typing import Any

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceEntry,
    DeviceEntryType,
    DeviceRegistry,
    async_get as async_get_device_registry,
    format_mac,
)
from homeassistant.loader import LoaderError, async_get_loaded_integration

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
from .matching import integration_rank


def text_pairs(items: Iterable[Any]) -> tuple[tuple[str, str], ...]:
    """Identifiers or connections as ``(str, str)`` pairs.

    Home Assistant stores whatever an integration registers, and some use a
    number as the value or a tuple with more than two parts. Those are turned
    into text (the extra parts joined with ``:``) so matching can compare them;
    anything shorter than a pair is skipped.
    """
    pairs: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        pairs.append((str(item[0]), ":".join(str(part) for part in item[1:])))
    return tuple(pairs)


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


def is_service(
    hass: HomeAssistant,
    device: DeviceEntry,
    *,
    include_services: bool = False,
) -> bool:
    """Whether this is a service (software), not hardware.

    That is a device marked as a service (Daily Routine's routines, Ford
    Assistant's vehicle helper, Device Linker's own device), or any device of
    an integration that is itself a service, such as Music Assistant, whose
    players are registered as ordinary devices. Only hardware is linked;
    services that belong with a device link themselves.

    With ``include_services`` (the Include services setting, off by default)
    nothing counts as a service, so every device can be linked. Device
    Linker's own device is never a candidate either way.
    """
    if include_services:
        return False
    if device.entry_type is DeviceEntryType.SERVICE:
        return True
    entry = hass.config_entries.async_get_entry(device.config_entry_id)
    if entry is None:
        return False
    try:
        integration = async_get_loaded_integration(hass, entry.domain)
    except LoaderError:
        return False
    return integration.integration_type == "service"


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


def link_title(name: str, count: int) -> str:
    """A link's title: ``Boy's Bedroom Hub (4 Devices Linked)``.

    ``count`` is every device in the link. :func:`retitle_links` gives every
    link this title, named after its best device.
    """
    noun = "Device" if count == 1 else "Devices"
    return f"{name} ({count} {noun} Linked)"


# A connection word in brackets at the end of a name: "Curtain (BLE)".
_CONNECTION_SUFFIX = re.compile(
    r"\s*\((?:ble|bluetooth|cloud|matter|zigbee|z-?wave|wi-?fi|ethernet|lan|thread)\)\s*$",
    re.IGNORECASE,
)


def link_name(
    hass: HomeAssistant, settings: Mapping[str, Any], preferred: Sequence[str]
) -> tuple[str, int] | None:
    """The name a link is shown with, and how many devices it has.

    The name is the device from the most trusted MAC source (the preferred
    MAC sources setting), else the main device (the one the address came
    from), else the first device, without a connection tag like "(BLE)".
    """
    registry = async_get_device_registry(hass)
    main_id = settings.get(CONF_MAIN_DEVICE_ID)
    ids = [main_id] if isinstance(main_id, str) else []
    ids += [
        d
        for d in settings.get(CONF_LINKED_DEVICE_IDS) or []
        if isinstance(d, str) and d not in ids
    ]
    members = [device for device in (get_device(registry, d) for d in ids) if device]
    if not members:
        return None

    def rank(item: tuple[int, DeviceEntry]) -> tuple[int, int, int]:
        index, device = item
        entry = hass.config_entries.async_get_entry(device.config_entry_id)
        domain = entry.domain if entry else ""
        return (integration_rank(domain, preferred), device.id != main_id, index)

    _index, best = min(enumerate(members), key=rank)
    name = best.name_by_user or best.name or best.id
    return _CONNECTION_SUFFIX.sub("", name).strip() or name, len(members)


def retitle_links(hass: HomeAssistant, preferred: Sequence[str]) -> None:
    """Give every link its current title. Changes only titles that differ."""
    hub = hub_entry(hass)
    if hub is None:
        return
    for subentry_id, settings in hub_links(hub).items():
        found = link_name(hass, settings, preferred)
        subentry = hub.subentries.get(subentry_id)
        if found is None or subentry is None:
            continue
        title = link_title(*found)
        if subentry.title != title:
            hass.config_entries.async_update_subentry(hub, subentry, title=title)


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


def devices_in_links(
    hass: HomeAssistant, *, skip_subentry_id: str | None = None
) -> set[str]:
    """Ids of every device already in a link (main or linked).

    ``skip_subentry_id`` leaves out one link, the one being edited.
    """
    ids: set[str] = set()
    for _hub, subentry, settings in iter_links(hass):
        if subentry.subentry_id == skip_subentry_id:
            continue
        main_id = settings.get(CONF_MAIN_DEVICE_ID)
        if isinstance(main_id, str):
            ids.add(main_id)
        ids.update(
            d for d in settings.get(CONF_LINKED_DEVICE_IDS) or [] if isinstance(d, str)
        )
    return ids


def _all_devices(registry: DeviceRegistry) -> Iterable[DeviceEntry]:
    """Every device: ``registry.devices`` is a mapping on 2026.8, iterable later."""
    devices = registry.devices
    for item in devices:
        yield devices[item] if isinstance(item, str) else item


def linked_device_ids(
    hass: HomeAssistant, *, skip_subentry_id: str | None = None
) -> set[str]:
    """Ids of every device that is already linked to another device.

    That is every device in a link, and every device Home Assistant already
    shows as linked because it shares an identifier or connection with
    another device. Services (a TV Mgmt profile, a Daily Routine routine)
    link themselves to their device; that doesn't count, or a TV with a
    profile could never be linked to its network device.
    ``skip_subentry_id`` leaves out one link, the one being edited.
    """
    linked = devices_in_links(hass, skip_subentry_id=skip_subentry_id)
    holders: dict[tuple[str, str, str], set[str]] = {}
    for device in _all_devices(async_get_device_registry(hass)):
        if is_service(hass, device):
            continue
        for kind, pairs in (
            ("identifier", text_pairs(device.identifiers)),
            ("connection", text_pairs(device.connections)),
        ):
            for first, second in pairs:
                holders.setdefault((kind, first, second), set()).add(device.id)
    for ids in holders.values():
        if len(ids) > 1:
            linked |= ids
    return linked
