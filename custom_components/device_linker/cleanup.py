"""Finding and removing addresses left behind by links that no longer exist.

Deleting a link takes its MAC address or shared ID off the devices Device
Linker added it to. Links made by the multi-select build before 1.3.0 didn't
record which devices those were, so deleting one can leave the address
behind, and Home Assistant keeps showing those devices as linked.

A leftover is a value on a device that no current link uses for that device:
- any ``("device_linker", value)`` identifier, which only Device Linker adds;
- a network MAC connection on a device that was part of a link you removed
  (it appears in the remembered unlinked pairs) and that another device
  still shares, so Home Assistant still shows them as linked.

Removing a MAC that the device's own integration registered is harmless: the
integration adds it again on its next refresh.

Not offered:
- a value that is all the device has: Home Assistant refuses to leave a
  device with no identifiers and no connections, and such a MAC is the
  device's own (ESPHome and UniFi register nothing else);
- a MAC that only services share (a Daily Routine routine or a TV Mgmt
  profile linking itself to its device), since that link is wanted.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import CONF_LINKED_DEVICE_IDS, CONF_MAC_ADDRESS, CONF_MAIN_DEVICE_ID, DOMAIN
from .registry import hub_links, is_service, link_item, text_pairs

_LOGGER = logging.getLogger(__name__)

# Separates the device id and the value in a form option's value.
SEPARATOR = "|"


@dataclass(frozen=True)
class Leftover:
    """One value on one device that no link uses any more."""

    device: dr.DeviceEntry
    integration: str
    value: str
    # Names of other devices that still share it (why it shows as linked).
    shared_with: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.device.id}{SEPARATOR}{self.value}"


def _name(device: dr.DeviceEntry) -> str:
    return device.name_by_user or device.name or device.id


def _in_use(entry: ConfigEntry) -> set[tuple[str, str]]:
    """(device id, value) for every device of every current link."""
    used: set[tuple[str, str]] = set()
    for settings in hub_links(entry).values():
        value = str(settings[CONF_MAC_ADDRESS])
        main = settings[CONF_MAIN_DEVICE_ID]
        members = [main] if isinstance(main, str) else []
        members += [d for d in settings[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)]
        used |= {(device_id, value) for device_id in members}
    return used


def _devices(registry: dr.DeviceRegistry) -> Iterable[dr.DeviceEntry]:
    """Every device, on Home Assistant 2026.8 and newer.

    Through 2026.9 ``registry.devices`` is a mapping, so iterating it yields
    ids; later releases yield the entries. Checking for ``.values()`` would
    itself trip the deprecation warning on newer releases.
    """
    devices = registry.devices
    for item in devices:
        yield devices[item] if isinstance(item, str) else item


def find_leftovers(
    hass: HomeAssistant, entry: ConfigEntry, unlinked_pairs: Iterable[str]
) -> list[Leftover]:
    """Every leftover value, sorted by device name."""
    registry = dr.async_get(hass)
    used = _in_use(entry)
    unlinked_devices = {
        device_id for pair in unlinked_pairs for device_id in pair.split(":")
    }
    domains: dict[str, str] = {}
    devices: list[dr.DeviceEntry] = []
    for device in _devices(registry):
        config = hass.config_entries.async_get_entry(device.config_entry_id)
        if config is not None and config.domain != DOMAIN:
            domains[device.id] = config.domain
            devices.append(device)
    leftovers: list[Leftover] = []
    for device in devices:
        values = [
            value
            for domain, value in text_pairs(device.identifiers)
            if domain == DOMAIN and (device.id, value) not in used
        ]
        # A service keeps its device's MAC on purpose, to link to it.
        if device.id in unlinked_devices and not is_service(hass, device):
            values += [
                dr.format_mac(value)
                for kind, value in text_pairs(device.connections)
                if kind == dr.CONNECTION_NETWORK_MAC
                and (device.id, dr.format_mac(value)) not in used
            ]
        for value in values:
            field, item = link_item(value)
            # Home Assistant won't leave a device with nothing to identify it.
            if not (set(device.identifiers) | set(device.connections)) - {item}:
                continue
            sharers = [
                other
                for other in devices
                if other.id != device.id and item in getattr(other, field)
            ]
            if field == "connections":
                # A service linking itself to its device is meant to stay.
                sharers = [other for other in sharers if not is_service(hass, other)]
                # A MAC no other device shares isn't linking anything.
                if not sharers:
                    continue
            shared_with = tuple(
                sorted(f"{_name(other)} ({domains[other.id]})" for other in sharers)
            )
            leftovers.append(Leftover(device, domains[device.id], value, shared_with))
    return sorted(
        leftovers, key=lambda item: (_name(item.device).casefold(), item.value)
    )


def leftover_label(leftover: Leftover) -> str:
    others = ", ".join(leftover.shared_with) or "no other device"
    return (
        f"{_name(leftover.device)} ({leftover.integration}): {leftover.value}"
        f" — also on {others}"
    )


def remove_leftovers(hass: HomeAssistant, keys: Iterable[str]) -> tuple[int, list[str]]:
    """Take the chosen values off their devices.

    Returns how many were removed and the names of devices Home Assistant
    wouldn't change. One refusal doesn't stop the rest.
    """
    registry = dr.async_get(hass)
    removed = 0
    failed: list[str] = []
    for key in keys:
        device_id, _, value = key.partition(SEPARATOR)
        device = registry.async_get(device_id)
        if device is None or not value:
            continue
        field, item = link_item(value)
        current = set(getattr(device, field))
        if item not in current:
            continue
        try:
            registry.async_update_device(
                device.id, **{f"new_{field}": current - {item}}
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Couldn't remove %s from %s: Home Assistant refused the change (%s)",
                value,
                _name(device),
                err,
            )
            failed.append(_name(device))
            continue
        _LOGGER.info("Removed leftover %s from %s", value, _name(device))
        removed += 1
    return removed, failed
