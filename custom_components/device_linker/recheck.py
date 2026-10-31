"""Rechecking automatic links against the current matching rules.

Matching rules get stricter over time, and settings change, but a link made
by an earlier scan stays. This finds the devices in automatic links that the
current rules and settings would no longer link to the link's main device,
so they can be taken out.

Only automatic links that you haven't confirmed are checked. A link you made
yourself, or one you ticked as confirmed, is never listed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_CONFIRMED,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_ORIGIN,
    ORIGIN_AUTO,
)
from .matching import DeviceView, plan_auto_links, suggest
from .registry import hub_links, link_item

_LOGGER = logging.getLogger(__name__)

# Separates the link and the device in a form option's value.
SEPARATOR = "|"


@dataclass(frozen=True)
class Mismatch:
    """A device in an automatic link that the current rules wouldn't link."""

    subentry_id: str
    link_title: str
    device_id: str
    device_label: str

    @property
    def key(self) -> str:
        return f"{self.subentry_id}{SEPARATOR}{self.device_id}"

    @property
    def label(self) -> str:
        return f"{self.device_label} — in {self.link_title}"


def _without_link_value(view: DeviceView, value: str) -> DeviceView:
    """The device as it was before the link copied its address onto it."""
    field, item = link_item(value)
    pair = (str(item[0]), str(item[1]))
    if field == "connections":
        return replace(
            view, connections=tuple(c for c in view.connections if c != pair)
        )
    return replace(view, identifiers=tuple(i for i in view.identifiers if i != pair))


def find_mismatches(
    hass: HomeAssistant,
    entry: ConfigEntry,
    views: Iterable[DeviceView],
    settings,
    preferred: tuple[str, ...],
) -> list[Mismatch]:
    """Devices in unconfirmed automatic links that wouldn't be linked now.

    For each device, the address the link copied onto it is set aside, and
    the device and the link's main device are matched with the current rules
    and settings. When automatic linking wouldn't link the two, it's listed.
    """
    by_id = {view.device_id: view for view in views}
    found: list[Mismatch] = []
    for subentry_id, link in hub_links(entry).items():
        if link[CONF_ORIGIN] != ORIGIN_AUTO or link[CONF_CONFIRMED]:
            continue
        main_id = link[CONF_MAIN_DEVICE_ID]
        main = by_id.get(main_id) if isinstance(main_id, str) else None
        if main is None:
            continue
        value = str(link[CONF_MAC_ADDRESS])
        added = dict(link[CONF_ADDED_CONNECTIONS])
        subentry = entry.subentries[subentry_id]
        for device_id in link[CONF_LINKED_DEVICE_IDS]:
            view = by_id.get(device_id) if isinstance(device_id, str) else None
            if view is None:
                continue
            if added.get(device_id):
                view = _without_link_value(view, value)
            matches = suggest(
                [main, view],
                linked_pairs=set(),
                dismissed=set(),
                threshold=settings.threshold,
                match_hardware=settings.match_hardware,
                match_names=settings.match_names,
                preferred_integrations=preferred,
            )
            plans = plan_auto_links(
                matches,
                enabled=True,
                threshold=settings.auto_link_threshold,
                similar_names=settings.auto_link_similar,
            )
            if any(device_id in plan.linked_device_ids for plan in plans) or any(
                plan.anchor_device_id == device_id for plan in plans
            ):
                continue
            found.append(
                Mismatch(
                    subentry_id,
                    subentry.title,
                    device_id,
                    f"{view.name} ({view.integration})",
                )
            )
    return sorted(found, key=lambda item: (item.link_title.casefold(), item.label))


def unlink_mismatches(
    hass: HomeAssistant, entry: ConfigEntry, keys: Iterable[str]
) -> int:
    """Take the chosen devices out of their links. Returns how many.

    A link left with no devices besides its main device is deleted. The hub's
    reconcile takes the copied address off and remembers each pair as
    unlinked, so automatic linking won't link them again.
    """
    chosen: dict[str, set[str]] = {}
    for key in keys:
        subentry_id, _, device_id = key.partition(SEPARATOR)
        if subentry_id and device_id:
            chosen.setdefault(subentry_id, set()).add(device_id)
    links = hub_links(entry)
    removed = 0
    for subentry_id, device_ids in chosen.items():
        link = links.get(subentry_id)
        subentry = entry.subentries.get(subentry_id)
        if link is None or subentry is None or link[CONF_CONFIRMED]:
            continue
        old_ids = [d for d in link[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)]
        keep = [d for d in old_ids if d not in device_ids]
        removed += len(old_ids) - len(keep)
        if not keep:
            hass.config_entries.async_remove_subentry(entry, subentry_id)
            continue
        data = dict(subentry.data)
        data[CONF_LINKED_DEVICE_IDS] = keep
        data[CONF_ADDED_CONNECTIONS] = {
            d: bool(flag)
            for d, flag in dict(link[CONF_ADDED_CONNECTIONS]).items()
            if d in keep
        }
        hass.config_entries.async_update_subentry(entry, subentry, data=data)
    _LOGGER.info("Recheck took %s device(s) out of automatic links", removed)
    return removed
