"""Find links in the hub the way the tests read them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from custom_components.device_linker.const import CONF_ORIGIN
from custom_components.device_linker.registry import hub_entry, iter_links


@dataclass
class Link:
    """One link subentry: its title, id, and normalized settings."""

    entry_id: str
    title: str
    settings: dict[str, Any]
    unique_id: str | None


def links(hass, *, origin: str | None = None) -> list[Link]:
    """Every link, or only automatic or manual ones."""
    found = []
    for _hub, subentry, settings in iter_links(hass):
        if origin is not None and settings[CONF_ORIGIN] != origin:
            continue
        found.append(
            Link(subentry.subentry_id, subentry.title, settings, subentry.unique_id)
        )
    return found


def link_for(hass, device_id: str, *, origin: str | None = None) -> Link | None:
    """The link that includes ``device_id`` as a linked device."""
    for link in links(hass, origin=origin):
        if device_id in link.settings["linked_device_ids"]:
            return link
    return None


def refresh(hass, link: Link) -> Link | None:
    """The same link as it is now."""
    return next((item for item in links(hass) if item.entry_id == link.entry_id), None)


def get_entry_settings(link: Link) -> dict[str, Any]:
    """Settings of a link (named like the old helper the tests used)."""
    return link.settings


async def remove_link(hass, link: Link) -> None:
    """Delete a link the way the trash button does."""
    hub = hub_entry(hass)
    assert hub is not None
    assert hass.config_entries.async_remove_subentry(hub, link.entry_id)
    await hass.async_block_till_done()
