"""Device Linker: one entry, the hub, with one subentry per link.

The hub puts each link's shared MAC on the devices that link owns, keeps it
there when another integration rewrites a device, and takes it off again
when a link is changed or deleted. Links added by hand, from a suggestion,
or automatically all go through the same reconcile step in
:class:`LinkRuntime`.

Before 2.0.0 every link was its own config entry. Migration folds those into
the hub (see :func:`async_migrate_entry`) without touching any connection.
"""

from __future__ import annotations

import asyncio
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry, ConfigSubentry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_HUB,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_MERGED_INTO,
    CONF_ORIGIN,
    CONFIG_VERSION,
    DATA_MANAGER,
    DOMAIN,
    HUB_TITLE,
    HUB_UNIQUE_ID,
    LINKS_STORAGE_KEY,
    LINKS_STORAGE_VERSION,
    ORIGIN_AUTO,
    ORIGIN_MANUAL,
    SUBENTRY_LINK,
)
from .manager import (
    SuggestionManager,
    async_load_settings,
    async_remember_unlinked,
    preferred_mac_sources,
)
from .matching import pairs_among, pairs_touching
from .registry import (
    get_device,
    hub_entry,
    hub_links,
    is_hub,
    is_service,
    link_item,
    link_settings,
    link_title,
    link_unique_id,
    retitle_links,
    text_pairs,
)

_LOGGER = logging.getLogger(__name__)

# Links used to have a device of their own, identified by
# ("device_linker", "link_<subentry id>"); they're removed on start.
LINK_DEVICE_PREFIX = "link_"

PLATFORMS = ["sensor"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type DeviceLinkerConfigEntry = ConfigEntry[LinkRuntime | None]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Nothing to set up before the hub; links live in its subentries."""
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: DeviceLinkerConfigEntry
) -> bool:
    """Set up the hub. Old entries folded into it are removed here."""
    entry.runtime_data = None
    if entry.source == SOURCE_IGNORE:
        # An ignored suggestion is stored as a config entry so Home Assistant
        # remembers it. It does not link devices.
        return True
    if entry.data.get(CONF_MERGED_INTO):
        # Migration moved this link into the hub. Remove the empty entry once
        # its setup has finished; async_remove_entry leaves the MAC alone.
        hass.async_create_task(_async_remove_merged(hass, entry.entry_id))
        return True
    if not is_hub(entry):
        _LOGGER.warning("Config entry %s is not a Device Linker hub", entry.entry_id)
        return False

    runtime = LinkRuntime(hass, entry)
    await runtime.async_start()
    entry.runtime_data = runtime

    manager = SuggestionManager(hass)
    hass.data.setdefault(DOMAIN, {})[DATA_MANAGER] = manager
    await manager.async_start()
    runtime.manager = manager

    entry.async_on_unload(entry.add_update_listener(_async_hub_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Scan after this setup returns. Scanning inline would abort a discovery
    # flow that is in the middle of creating this entry.
    entry.async_create_background_task(
        hass, _scan_after_setup(manager), "device_linker scan after setup"
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: DeviceLinkerConfigEntry
) -> bool:
    """Stop the hub. Links stay on the devices until a link is deleted."""
    runtime = entry.runtime_data
    if runtime is None:
        return True
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    runtime.async_stop()
    if runtime.manager is not None:
        await runtime.manager.async_stop()
    hass.data.get(DOMAIN, {}).pop(DATA_MANAGER, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deleting the hub unlinks every link it made, and remembers each pair."""
    if not is_hub(entry):
        # An ignored suggestion, or an old entry migration already moved
        # into the hub: its connections belong to the hub now.
        return
    registry = dr.async_get(hass)
    pairs: set[str] = set()
    for settings in hub_links(entry).values():
        pairs |= _pairs_of(settings)
        _remove_owned(registry, settings, keep=None)
    await async_remember_unlinked(hass, pairs)
    await Store(hass, LINKS_STORAGE_VERSION, LINKS_STORAGE_KEY).async_remove()


async def _async_hub_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A link was added, changed, or deleted, or the settings were saved."""
    runtime = entry.runtime_data
    if isinstance(runtime, LinkRuntime):
        await runtime.async_reconcile()


class LinkRuntime:
    """Applies every link of the hub to the device registry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Prepare the store of links last applied."""
        self.hass = hass
        self.entry = entry
        self.manager: SuggestionManager | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, LINKS_STORAGE_VERSION, LINKS_STORAGE_KEY
        )
        self._applied: dict[str, dict[str, Any]] = {}
        self._unsub: Any = None

    async def async_start(self) -> None:
        """Undo links deleted while stopped, apply the rest, and keep them."""
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("links"), dict):
            self._applied = {
                subentry_id: link_settings(data)
                for subentry_id, data in stored["links"].items()
                if isinstance(data, dict) and CONF_MAC_ADDRESS in data
            }
        if not (await async_load_settings(self.hass)).include_services:
            _drop_services_from_automatic_links(self.hass, self.entry)
        await self.async_reconcile(scan=False)
        self._unsub = self.hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, self._restore_owned_links
        )

    @callback
    def async_stop(self) -> None:
        """Stop restoring connections. The links stay on the devices."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def async_reconcile(self, *, scan: bool = True) -> None:
        """Make the registry match the hub's links.

        A deleted link takes its MAC off the devices it added it to, and its
        pairs are remembered so automatic linking leaves them apart. A changed
        link does the same for devices it dropped or a MAC it replaced. Every
        link then adds its MAC where it owns it and it is missing.
        """
        registry = dr.async_get(self.hass)
        current = hub_links(self.entry)
        unlinked: set[str] = set()
        for subentry_id, old in self._applied.items():
            new = current.get(subentry_id)
            if new is None:
                unlinked |= _pairs_of(old)
                _remove_owned(registry, old, keep=None)
                continue
            old_ids = [d for d in old[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)]
            new_ids = [d for d in new[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)]
            dropped = [d for d in old_ids if d not in new_ids]
            if dropped:
                others = list(old_ids)
                main = old[CONF_MAIN_DEVICE_ID]
                if isinstance(main, str):
                    others.append(main)
                unlinked |= pairs_touching(dropped, others)
            _remove_owned(registry, old, keep=new)
        if unlinked:
            await async_remember_unlinked(self.hass, unlinked)
        for settings in current.values():
            _add_owned(registry, settings)
        _remove_link_devices(registry, self.entry)
        if current != self._applied:
            self._applied = current
            await self._store.async_save(
                {
                    "links": {
                        subentry_id: _storable(settings)
                        for subentry_id, settings in current.items()
                    }
                }
            )
        # Every link is named after its best device and counts its devices.
        retitle_links(
            self.hass,
            preferred_mac_sources(
                self.manager.settings
                if self.manager is not None
                else await async_load_settings(self.hass)
            ),
        )
        if scan and self.manager is not None:
            await self.manager.async_scan()

    @callback
    def _restore_owned_links(self, event: Event) -> None:
        """Put a link's MAC back when another integration rewrites the device.

        Only connections Device Linker added are restored. The hub is read on
        each event, so a link added later is covered too.
        """
        device_id = event.data.get("device_id")
        if not isinstance(device_id, str) or event.data.get("action") == "remove":
            return
        registry = dr.async_get(self.hass)
        for settings in hub_links(self.entry).values():
            if device_id in settings[CONF_LINKED_DEVICE_IDS] and dict(
                settings[CONF_ADDED_CONNECTIONS]
            ).get(device_id, False):
                _add_owned(registry, settings, only=device_id)


def _pairs_of(settings: dict[str, Any]) -> set[str]:
    main = settings[CONF_MAIN_DEVICE_ID]
    return pairs_among(
        main if isinstance(main, str) else None,
        [d for d in settings[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)],
    )


def _remove_owned(
    registry: dr.DeviceRegistry,
    old: dict[str, Any],
    *,
    keep: dict[str, Any] | None,
) -> None:
    """Take ``old``'s MAC or shared ID off the devices it added it to.

    Devices that ``keep`` still adds the same value to are left alone.
    """
    value = str(old[CONF_MAC_ADDRESS])
    field, item = link_item(value)
    still: set[str] = set()
    if keep is not None and keep[CONF_MAC_ADDRESS] == value:
        still = {
            device_id
            for device_id, flag in dict(keep[CONF_ADDED_CONNECTIONS]).items()
            if flag and device_id in keep[CONF_LINKED_DEVICE_IDS]
        }
    for device_id, flag in dict(old[CONF_ADDED_CONNECTIONS]).items():
        if not flag or device_id in still:
            continue
        device = get_device(registry, device_id)
        if device is not None and item in getattr(device, field):
            registry.async_update_device(
                device.id, **{f"new_{field}": set(getattr(device, field)) - {item}}
            )


def _add_owned(
    registry: dr.DeviceRegistry, settings: dict[str, Any], only: str | None = None
) -> None:
    """Add the link's MAC or shared ID to each device it owns that is missing it."""
    value = str(settings[CONF_MAC_ADDRESS])
    field, item = link_item(value)
    added = dict(settings[CONF_ADDED_CONNECTIONS])
    for device_id in settings[CONF_LINKED_DEVICE_IDS]:
        if only is not None and device_id != only:
            continue
        if not added.get(device_id, False):
            continue
        device = get_device(registry, device_id)
        if device is None or item in getattr(device, field):
            continue
        try:
            registry.async_update_device(
                device.id, **{f"new_{field}": set(getattr(device, field)) | {item}}
            )
        except dr.DeviceCollisionError:
            _LOGGER.warning(
                "Could not add %s to device %s because another device from that "
                "integration already has it",
                value,
                device_id,
            )


@callback
def _drop_services_from_automatic_links(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Take service devices out of links Device Linker made on its own.

    Before 2.3.0, automatic linking could pair a device with an integration's
    service (a Daily Routine routine named after the curtains it opens). Only
    automatic links are changed; a link made by hand is left as it is. The
    reconcile that follows takes the shared value off the removed devices.
    """
    registry = dr.async_get(hass)
    for subentry_id, settings in hub_links(entry).items():
        if settings[CONF_ORIGIN] != ORIGIN_AUTO:
            continue
        subentry = entry.subentries[subentry_id]
        main_id = settings[CONF_MAIN_DEVICE_ID]
        main = get_device(registry, main_id) if isinstance(main_id, str) else None
        linked = [d for d in settings[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)]
        keep = [
            d
            for d in linked
            if (device := get_device(registry, d)) is None
            or not is_service(hass, device)
        ]
        if (main is not None and is_service(hass, main)) or not keep:
            hass.config_entries.async_remove_subentry(entry, subentry_id)
            continue
        if keep == linked:
            continue
        added = dict(settings[CONF_ADDED_CONNECTIONS])
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            title=link_title(
                (main.name_by_user or main.name or main.id)
                if main is not None
                else subentry.title.removeprefix("Link ").rsplit(" to ", 1)[0],
                len(keep) + bool(main),
            ),
            data={
                **settings,
                CONF_LINKED_DEVICE_IDS: keep,
                CONF_ADDED_CONNECTIONS: {d: bool(added.get(d, False)) for d in keep},
            },
        )


def _remove_link_devices(registry: dr.DeviceRegistry, entry: ConfigEntry) -> None:
    """Remove the per-link devices that 2.2.0 to 2.4.0 created.

    Each link had a service device carrying its value, so it showed up under
    Linked devices on every linked device. That was extra clutter, so links
    no longer have devices; the link itself is enough.
    """
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if any(
            domain == DOMAIN and value.startswith(LINK_DEVICE_PREFIX)
            for domain, value in text_pairs(device.identifiers)
        ):
            registry.async_remove_device(device.id)


def _storable(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_MAIN_DEVICE_ID: settings[CONF_MAIN_DEVICE_ID],
        CONF_LINKED_DEVICE_IDS: list(settings[CONF_LINKED_DEVICE_IDS]),
        CONF_MAC_ADDRESS: settings[CONF_MAC_ADDRESS],
        CONF_ADDED_CONNECTIONS: dict(settings[CONF_ADDED_CONNECTIONS]),
        CONF_ORIGIN: settings[CONF_ORIGIN],
    }


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Bring old entries up to version 3, where links are hub subentries.

    Version 1 gains ``origin``. In version 3 the first link entry to migrate
    becomes the hub, holding its own link as a subentry; every other link
    entry adds its link to the hub and is removed after setup. Nothing here
    awaits, so two entries migrating at once cannot both become the hub.
    The connections on the devices are not touched.
    """
    if config_entry.version >= CONFIG_VERSION:
        return True
    if config_entry.source == SOURCE_IGNORE:
        hass.config_entries.async_update_entry(config_entry, version=CONFIG_VERSION)
        return True

    data = dict(config_entry.data)
    if config_entry.version == 1 and CONF_ORIGIN not in data:
        data[CONF_ORIGIN] = ORIGIN_MANUAL
    try:
        settings = link_settings({**data, **config_entry.options})
    except KeyError:
        _LOGGER.error("Config entry %s has no link to migrate", config_entry.entry_id)
        return False
    payload = _storable(settings)
    main = settings[CONF_MAIN_DEVICE_ID]
    unique_id = link_unique_id(
        main if isinstance(main, str) else None, str(settings[CONF_MAC_ADDRESS])
    )

    title = config_entry.title
    hub = hub_entry(hass)
    if hub is None:
        hass.config_entries.async_update_entry(
            config_entry,
            data={CONF_HUB: True},
            options={},
            title=HUB_TITLE,
            unique_id=HUB_UNIQUE_ID,
            version=CONFIG_VERSION,
            minor_version=1,
        )
        _add_migrated_link(hass, config_entry, payload, title, unique_id)
        _LOGGER.info("Device Linker now keeps its links under one entry")
        return True

    _add_migrated_link(hass, hub, payload, title, unique_id)
    hass.config_entries.async_update_entry(
        config_entry,
        data={CONF_MERGED_INTO: hub.entry_id},
        options={},
        version=CONFIG_VERSION,
        minor_version=1,
    )
    return True


def _add_migrated_link(
    hass: HomeAssistant,
    hub: ConfigEntry,
    payload: dict[str, Any],
    title: str,
    unique_id: str,
) -> None:
    """Add a migrated link to the hub, merging into a link with the same anchor."""
    for subentry in hub.subentries.values():
        if subentry.unique_id != unique_id:
            continue
        current = link_settings(subentry.data)
        linked = list(current[CONF_LINKED_DEVICE_IDS])
        added = dict(current[CONF_ADDED_CONNECTIONS])
        for device_id in payload[CONF_LINKED_DEVICE_IDS]:
            if device_id not in linked:
                linked.append(device_id)
                added[device_id] = bool(
                    payload[CONF_ADDED_CONNECTIONS].get(device_id, False)
                )
        hass.config_entries.async_update_subentry(
            hub,
            subentry,
            data={
                **_storable(current),
                CONF_LINKED_DEVICE_IDS: linked,
                CONF_ADDED_CONNECTIONS: added,
            },
        )
        return
    hass.config_entries.async_add_subentry(
        hub,
        ConfigSubentry(
            data=MappingProxyType(payload),
            subentry_type=SUBENTRY_LINK,
            title=title,
            unique_id=unique_id,
        ),
    )


async def _async_remove_merged(hass: HomeAssistant, entry_id: str) -> None:
    """Remove an old link entry once migration has moved its link to the hub."""
    await asyncio.sleep(0)
    if hass.config_entries.async_get_entry(entry_id) is not None:
        await hass.config_entries.async_remove(entry_id)


async def _scan_after_setup(manager: SuggestionManager) -> None:
    """Run one scan after the config flow that created the hub has finished.

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
    """Return the manager while the hub is loaded."""
    manager = hass.data.get(DOMAIN, {}).get(DATA_MANAGER)
    return manager if isinstance(manager, SuggestionManager) else None
