"""Keep link suggestions up to date, link the strong ones, and show the rest.

A single manager runs for the whole integration, started by the first real
config entry and stopped when the last one unloads. Preferences, dismissed
suggestions, and pairs the user unlinked live in a Store so they survive a
restart. Device and area registry updates, and Home Assistant finishing
startup, schedule one scan after a short debounce.

Startup order is why the scan listens for ``EVENT_HOMEASSISTANT_STARTED``.
The first config entry can be set up before other integrations have
registered their devices. A scan at that moment sees an empty registry.
Devices created or updated later schedule another scan; the started event
covers integrations that register during startup and then go quiet.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import SOURCE_DISCOVERY, ConfigEntry, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_AUTO_LINK,
    CONF_AUTO_LINK_SIMILAR,
    CONF_AUTO_LINK_THRESHOLD,
    CONF_IGNORED_DEVICES,
    CONF_INCLUDE_SERVICES,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_MATCH_HARDWARE,
    CONF_MATCH_NAMES,
    CONF_ORIGIN,
    CONF_SUGGESTION_THRESHOLD,
    DATA_MANAGER,
    DEFAULT_AUTO_LINK_THRESHOLD,
    DEFAULT_SUGGESTION_THRESHOLD,
    DOMAIN,
    ISSUE_AUTO_LINKED,
    MAX_DISCOVERY_FLOWS,
    MAX_LISTED_SUGGESTIONS,
    ORIGIN_AUTO,
    ORIGIN_MANUAL,
    SERVICE_RESCAN,
    SERVICE_RESET,
    STORAGE_KEY,
    STORAGE_VERSION,
    SUBENTRY_LINK,
    SUGGESTION_DEBOUNCE_SECONDS,
    SUGGESTION_THRESHOLD_MAX,
    SUGGESTION_THRESHOLD_MIN,
)
from .matching import (
    AutoLinkPlan,
    DeviceView,
    Suggestion,
    choose_link_ends,
    pair_key,
    pairs_among,
    plan_auto_links,
    suggest,
    suggestion_unique_id,
)
from .registry import (
    get_device,
    hub_entry,
    is_service,
    iter_links,
    link_title,
    link_unique_id,
    linked_device_ids,
    text_pairs,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SuggestionSettings:
    """Integration-wide suggestion and automatic-link preferences.

    ``dismissed`` holds suggestion unique ids the user ignored. ``unlinked``
    holds pair keys the user separated; those pairs are not linked again.
    ``announced`` holds pair keys already described by the repair, so a
    restart does not raise the same repair again.
    """

    threshold: float = DEFAULT_SUGGESTION_THRESHOLD
    match_hardware: bool = True
    match_names: bool = True
    auto_link: bool = True
    auto_link_threshold: float = DEFAULT_AUTO_LINK_THRESHOLD
    auto_link_similar: bool = True
    include_services: bool = False
    # Devices never suggested or linked automatically, until taken off the
    # list in the settings.
    ignored_devices: frozenset[str] = frozenset()
    dismissed: frozenset[str] = frozenset()
    unlinked: frozenset[str] = frozenset()
    announced: frozenset[str] = frozenset()


def async_get_manager(hass: HomeAssistant) -> SuggestionManager | None:
    """Return the running manager, if a real config entry has set one up."""
    data = hass.data.get(DOMAIN)
    if not isinstance(data, dict):
        return None
    manager = data.get(DATA_MANAGER)
    if isinstance(manager, SuggestionManager):
        return manager
    return None


class SuggestionManager:
    """Scan the device registry and publish suggestions."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Prepare storage. Call :meth:`async_start` before using the manager."""
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._settings = SuggestionSettings()
        self._suggestions: list[Suggestion] = []
        self._last_scan: datetime | None = None
        self._in_scan = False
        self._scan_again = False
        self._unsub_device: Any = None
        self._unsub_area: Any = None
        self._unsub_started: Any = None
        self._unsub_debounce: Any = None
        self._sensor: Any = None
        self._sensor_entry_id: str | None = None

    @property
    def settings(self) -> SuggestionSettings:
        """Current threshold, enabled signals, and dismissed ids."""
        return self._settings

    @property
    def suggestions(self) -> list[Suggestion]:
        """Suggestions from the latest scan, best first."""
        return self._suggestions

    @property
    def sensor_entry_id(self) -> str | None:
        """Config entry that currently owns the suggestions sensor."""
        return self._sensor_entry_id

    async def async_start(self) -> None:
        """Load preferences and listen for registry changes."""
        self._settings = await _load_settings(self._store)
        self._unsub_device = self.hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, self._schedule_device_scan
        )
        self._unsub_area = self.hass.bus.async_listen(
            ar.EVENT_AREA_REGISTRY_UPDATED, self._schedule_scan
        )
        # listen_once survives until the event or until stop. Integrations
        # that are set up before this one still deliver create events; this
        # covers the ones that register during startup and then sit still.
        self._unsub_started = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, self._schedule_scan
        )
        self.hass.services.async_register(DOMAIN, SERVICE_RESCAN, self._async_rescan)
        self.hass.services.async_register(
            DOMAIN, SERVICE_RESET, self._async_reset_dismissed
        )

    async def async_stop(self) -> None:
        """Stop listeners and the rescan action."""
        if self._unsub_debounce is not None:
            self._unsub_debounce()
            self._unsub_debounce = None
        if self._unsub_device is not None:
            self._unsub_device()
            self._unsub_device = None
        if self._unsub_area is not None:
            self._unsub_area()
            self._unsub_area = None
        if self._unsub_started is not None:
            self._unsub_started()
            self._unsub_started = None
        for service in (SERVICE_RESCAN, SERVICE_RESET):
            if self.hass.services.has_service(DOMAIN, service):
                self.hass.services.async_remove(DOMAIN, service)
        self._sensor = None
        self._sensor_entry_id = None

    async def async_scan(self) -> list[Suggestion]:
        """Rebuild suggestions, link strong matches, and reconcile discoveries.

        Re-entering while a scan is already applying entries (their setup
        schedules another scan) records one follow-up pass instead of
        running nested. Home Assistant runs this on a single loop.
        """
        if self._in_scan:
            self._scan_again = True
            return self._suggestions
        self._in_scan = True
        try:
            await self._async_scan_once()
            while self._scan_again:
                self._scan_again = False
                await self._async_scan_once()
        finally:
            self._in_scan = False
        return self._suggestions

    async def _async_scan_once(self) -> None:
        blocked = set(self._settings.dismissed) | set(self._settings.unlinked)
        matches = suggest(
            [
                view
                for view in _device_views(
                    self.hass,
                    include_services=(self._settings.include_services),
                )
                if view.device_id not in self._settings.ignored_devices
            ],
            linked_pairs=_linked_pairs(self.hass),
            dismissed=blocked,
            threshold=self._settings.threshold,
            match_hardware=self._settings.match_hardware,
            match_names=self._settings.match_names,
            copied_macs=_copied_macs(self.hass),
        )
        # A device already linked to another device isn't offered for a new
        # link. A device that isn't linked yet and matches one already in a
        # link is offered to join that link instead.
        matches, auto = _for_links(self.hass, matches)
        plans = plan_auto_links(
            auto,
            enabled=self._settings.auto_link,
            threshold=self._settings.auto_link_threshold,
            similar_names=self._settings.auto_link_similar,
        )
        linked_keys = await self._async_apply_plans(plans)
        self._suggestions = [item for item in matches if item.key not in linked_keys]
        self._last_scan = dt_util.utcnow()
        _LOGGER.debug(
            "Suggestion scan found %s candidate(s), linked %s, %s still waiting "
            "(threshold %s, auto %s at %s, hardware %s, names %s)",
            len(matches),
            len(linked_keys),
            len(self._suggestions),
            self._settings.threshold,
            self._settings.auto_link,
            self._settings.auto_link_threshold,
            self._settings.match_hardware,
            self._settings.match_names,
        )
        await self._async_sync_flows()
        self._publish_sensor()

    async def async_update_settings(
        self,
        *,
        threshold: float,
        match_hardware: bool,
        match_names: bool,
        auto_link: bool,
        auto_link_threshold: float,
        auto_link_similar: bool | None = None,
        include_services: bool | None = None,
        ignored_devices: Iterable[str] | None = None,
        scan: bool = True,
    ) -> None:
        """Store new preferences and, by default, scan again."""
        services_were_included = self._settings.include_services
        self._settings = replace(
            self._settings,
            threshold=_clamp_threshold(threshold),
            match_hardware=match_hardware,
            match_names=match_names,
            auto_link=bool(auto_link),
            auto_link_threshold=_clamp_threshold(auto_link_threshold),
            auto_link_similar=(
                self._settings.auto_link_similar
                if auto_link_similar is None
                else bool(auto_link_similar)
            ),
            include_services=(
                self._settings.include_services
                if include_services is None
                else bool(include_services)
            ),
            ignored_devices=(
                self._settings.ignored_devices
                if ignored_devices is None
                else frozenset(d for d in ignored_devices if isinstance(d, str) and d)
            ),
        )
        await _save_settings(self._store, self._settings)
        hub = hub_entry(self.hass)
        if services_were_included and not self._settings.include_services and hub:
            # Take services back out of automatic links now, not at the next start.
            from . import _drop_services_from_automatic_links  # noqa: PLC0415

            _drop_services_from_automatic_links(self.hass, hub)
        if scan:
            try:
                await self.async_scan()
            except Exception:
                # The settings are saved either way; a scan problem is logged,
                # not shown as an error on the settings form.
                _LOGGER.exception("Scan after saving the settings failed")

    async def async_remember_unlinked(self, pairs: set[str]) -> None:
        """Remember pairs the user separated so a later scan leaves them alone."""
        fresh = {pair for pair in pairs if pair} - self._settings.unlinked
        if not fresh and not (self._settings.announced & pairs):
            return
        self._settings = replace(
            self._settings,
            unlinked=self._settings.unlinked | pairs,
            announced=self._settings.announced - pairs,
        )
        await _save_settings(self._store, self._settings)

    async def async_dismiss(self, unique_id: str) -> None:
        """Remember a suggestion so later scans do not offer it again."""
        if unique_id in self._settings.dismissed:
            return
        self._settings = replace(
            self._settings, dismissed=self._settings.dismissed | {unique_id}
        )
        await _save_settings(self._store, self._settings)
        self._suggestions = [
            item
            for item in self._suggestions
            if suggestion_unique_id(item.device_id_a, item.device_id_b) != unique_id
        ]
        self._publish_sensor()

    async def async_ignore_devices(self, device_ids: Iterable[str]) -> None:
        """Never suggest these devices again, then look again without them."""
        fresh = {d for d in device_ids if d} - self._settings.ignored_devices
        if not fresh:
            return
        self._settings = replace(
            self._settings,
            ignored_devices=self._settings.ignored_devices | fresh,
        )
        await _save_settings(self._store, self._settings)
        await self.async_scan()

    def is_dismissed(self, unique_id: str) -> bool:
        """Return whether this suggestion unique id was ignored."""
        return unique_id in self._settings.dismissed

    def async_add_sensor(self, entry_id: str, async_add_entities: Any) -> None:
        """Create the suggestions sensor on the hub entry."""
        if self._sensor is not None:
            return
        from .sensor import SuggestionSensor

        self._sensor = SuggestionSensor(self)
        self._sensor_entry_id = entry_id
        async_add_entities([self._sensor])

    @property
    def last_scan(self) -> str | None:
        """ISO timestamp of the latest finished scan, if one has run."""
        if self._last_scan is None:
            return None
        return dt_util.as_local(self._last_scan).isoformat()

    def listed_suggestions(self) -> list[dict[str, object]]:
        """Attributes for the sensor and diagnostics, without MAC addresses."""
        return [
            item.as_attribute() for item in self._suggestions[:MAX_LISTED_SUGGESTIONS]
        ]

    @callback
    def _schedule_device_scan(self, event: Event) -> None:
        """Rescan after a device is created, updated, or removed."""
        action = event.data.get("action")
        if action not in {"create", "update", "remove"}:
            return
        self._schedule_scan(event)

    @callback
    def _schedule_scan(self, _event: Event) -> None:
        """Collapse a burst of registry events into one scan.

        Create and update events are how a UniFi poll, or another integration
        finishing its setup, gets a new scan. Tracked on Home Assistant's
        clock so a test can fire the time change, and so unload cancels it
        instead of leaving a timer behind.
        """
        if self._unsub_debounce is not None:
            self._unsub_debounce()
            self._unsub_debounce = None
        self._unsub_debounce = async_track_point_in_utc_time(
            self.hass,
            self._debounced_scan,
            dt_util.utcnow() + timedelta(seconds=SUGGESTION_DEBOUNCE_SECONDS),
        )

    async def _debounced_scan(self, _now: Any) -> None:
        self._unsub_debounce = None
        await self.async_scan()

    async def _async_rescan(self, _call: Any) -> None:
        """Handle the rescan action."""
        await self.async_scan()

    async def _async_reset_dismissed(self, _call: Any) -> None:
        """Forget ignored suggestions and unlinked pairs, then look again."""
        self._settings = replace(
            self._settings,
            dismissed=frozenset(),
            unlinked=frozenset(),
        )
        await _save_settings(self._store, self._settings)
        await self.async_scan()

    async def _async_sync_flows(self) -> None:
        """Open discovery flows for new suggestions and close stale ones."""
        wanted = {
            suggestion_unique_id(item.device_id_a, item.device_id_b): item
            for item in self._suggestions
        }
        progress = self.hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        active: set[str] = set()
        for flow in progress:
            unique_id = flow["context"].get("unique_id")
            if not isinstance(unique_id, str) or not unique_id.startswith(
                "suggestion:"
            ):
                continue
            if unique_id not in wanted:
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
                continue
            # A pair that now joins a link (or no longer does) is asked again.
            joins = wanted[unique_id].join_link_id is not None
            if joins != (flow.get("step_id") == "discovery_join"):
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
                continue
            active.add(unique_id)
        room = MAX_DISCOVERY_FLOWS - len(active)
        for unique_id, item in wanted.items():
            if room <= 0:
                break
            if unique_id in active or unique_id in self._settings.dismissed:
                continue
            try:
                await self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_DISCOVERY},
                    data=_discovery_data(unique_id, item),
                )
            except AbortFlow:
                _LOGGER.debug("Suggestion flow %s was already active", unique_id)
            else:
                room -= 1

    def _publish_sensor(self) -> None:
        sensor = self._sensor
        if sensor is None or sensor.hass is None:
            return
        sensor.async_write_ha_state()

    async def _async_apply_plans(self, plans: list[AutoLinkPlan]) -> set[str]:
        """Create or extend one entry per plan. Return the pair keys now linked."""
        linked: set[str] = set()
        notices: list[str] = []
        newly: set[str] = set()
        for plan in plans:
            try:
                keys, notice = await self._async_apply_plan(plan)
            except Exception:
                _LOGGER.exception("Could not automatically link %s", plan.anchor_name)
                continue
            linked |= keys
            newly |= keys
            if notice:
                notices.append(notice)
        newly -= self._settings.announced
        if newly:
            self._settings = replace(
                self._settings, announced=self._settings.announced | newly
            )
            await _save_settings(self._store, self._settings)
            _publish_auto_link_issue(self.hass, notices)
        return linked

    async def _async_apply_plan(
        self, plan: AutoLinkPlan
    ) -> tuple[set[str], str | None]:
        """Link ``plan`` onto the anchor's link. Return new pair keys and a notice.

        The link is a subentry of the hub. The hub's update listener puts the
        MAC on the devices, the same way it does for a link added by hand.
        """
        hub = hub_entry(self.hass)
        if hub is None:
            return set(), None
        registry = dr.async_get(self.hass)
        anchor = get_device(registry, plan.anchor_device_id)
        if anchor is None:
            return set(), None
        unique_id = link_unique_id(plan.anchor_device_id, plan.mac)
        existing = find_link(self.hass, unique_id, plan.anchor_device_id, plan.mac)
        connection = (dr.CONNECTION_NETWORK_MAC, plan.mac)
        already: set[str] = set()
        old_added: dict[str, bool] = {}
        origin = ORIGIN_AUTO
        if existing is not None:
            current = existing[1]
            already = {
                device_id
                for device_id in current[CONF_LINKED_DEVICE_IDS]
                if isinstance(device_id, str)
            }
            old_added = {
                device_id: bool(flag)
                for device_id, flag in dict(current[CONF_ADDED_CONNECTIONS]).items()
            }
            origin = (
                current[CONF_ORIGIN]
                if current[CONF_ORIGIN] == ORIGIN_MANUAL
                else ORIGIN_AUTO
            )
        # Keep devices the link already has. A plan only lists new matches,
        # and replacing the list would drop the ones a person already chose.
        chosen: list[str] = [device_id for device_id in already]
        chosen_names: list[str] = []
        used_entries = {anchor.config_entry_id}
        for device_id in already:
            device = get_device(registry, device_id)
            if device is not None and device.config_entry_id:
                used_entries.add(device.config_entry_id)
        skip_id = existing[0].subentry_id if existing is not None else None
        for device_id, device_name in zip(
            plan.linked_device_ids, plan.linked_names, strict=True
        ):
            if device_id in already:
                continue
            device = get_device(registry, device_id)
            if device is None or device.config_entry_id in used_entries:
                continue
            if linked_on_another_link(self.hass, device_id, skip_id):
                continue
            if _mac_conflicts(registry, device, connection, already | set(chosen)):
                _LOGGER.info(
                    "Skipped %s: %s is already used on another device from %s",
                    device_name,
                    plan.mac,
                    device.config_entry_id,
                )
                continue
            used_entries.add(device.config_entry_id)
            chosen.append(device_id)
            chosen_names.append(device_name)
        fresh = [device_id for device_id in chosen if device_id not in already]
        if not fresh:
            return set(), None
        added = dict(old_added)
        for device_id in fresh:
            device = get_device(registry, device_id)
            if device is not None:
                added[device_id] = connection not in device.connections
        payload = link_payload(plan.anchor_device_id, chosen, plan.mac, added, origin)
        anchor_name = anchor.name_by_user or anchor.name or plan.anchor_name
        save_link(
            self.hass,
            hub,
            existing[0] if existing is not None else None,
            payload,
            link_title(anchor_name, len(chosen)),
            unique_id,
        )
        keys = {pair_key(plan.anchor_device_id, device_id) for device_id in fresh}
        keys |= pairs_among(None, fresh)
        notice = f"{anchor_name} ({plan.anchor_integration}): {', '.join(chosen_names)}"
        _LOGGER.info(
            "Automatically linked %s to %s device(s): %s",
            anchor_name,
            len(fresh),
            ", ".join(chosen_names),
        )
        return keys, notice


def _discovery_data(unique_id: str, item: Suggestion) -> dict[str, Any]:
    return {
        "unique_id": unique_id,
        "device_id_a": item.device_id_a,
        "device_id_b": item.device_id_b,
        "name_a": item.name_a,
        "name_b": item.name_b,
        "integration_a": item.integration_a,
        "integration_b": item.integration_b,
        "reason": item.reason,
        "score": f"{item.score:.0%}",
        "mac": item.mac or "",
        "anchor_device_id": item.anchor_device_id or "",
        "join_link_id": item.join_link_id or "",
        "join_link_title": item.join_link_title or "",
        "join_device_id": item.join_device_id or "",
        "area_a": item.area_name_a or "Unassigned",
        "area_b": item.area_name_b or "Unassigned",
    }


@dataclass(frozen=True, slots=True)
class _Membership:
    """The link a device is in."""

    subentry_id: str
    title: str
    main_id: str | None
    value: str
    member_ids: frozenset[str]


def _link_membership(hass: HomeAssistant) -> dict[str, _Membership]:
    """For each device in a link, that link."""
    found: dict[str, _Membership] = {}
    registry = dr.async_get(hass)
    for _hub, subentry, settings in iter_links(hass):
        main_id = settings[CONF_MAIN_DEVICE_ID]
        main_id = main_id if isinstance(main_id, str) else None
        value = settings[CONF_MAC_ADDRESS]
        if not isinstance(value, str):
            continue
        members = {
            d for d in settings[CONF_LINKED_DEVICE_IDS] or [] if isinstance(d, str)
        }
        if main_id:
            members.add(main_id)
        info = _Membership(
            subentry.subentry_id, subentry.title, main_id, value, frozenset(members)
        )
        for device_id in members:
            if get_device(registry, device_id) is not None:
                found.setdefault(device_id, info)
    return found


def _for_links(
    hass: HomeAssistant, matches: list[Suggestion]
) -> tuple[list[Suggestion], list[Suggestion]]:
    """Fit suggestions to the existing links. Return (shown, automatic).

    Two devices that aren't linked yet stay a normal suggestion. When one of
    them is in a link and the other isn't linked at all, the suggestion joins
    that link, once per device and link, best match first. Anything else is
    dropped: both linked already, or one linked only by Home Assistant (no
    link to join). A join is also left out when the new device's integration
    already has a device in the link, since they can't share one address.

    Automatic linking keeps what it did before: a join only when the match is
    on the link's main device and MAC address.
    """
    linked = linked_device_ids(hass)
    membership = _link_membership(hass)
    registry = dr.async_get(hass)
    shown: list[Suggestion] = []
    auto: list[Suggestion] = []
    seen: set[tuple[str, str]] = set()
    for item in matches:
        a_linked = item.device_id_a in linked
        b_linked = item.device_id_b in linked
        if not a_linked and not b_linked:
            shown.append(item)
            auto.append(item)
            continue
        if a_linked and b_linked:
            continue
        member, new = (
            (item.device_id_a, item.device_id_b)
            if a_linked
            else (item.device_id_b, item.device_id_a)
        )
        info = membership.get(member)
        new_device = get_device(registry, new)
        if info is None or new_device is None or (new, info.subentry_id) in seen:
            continue
        member_entries = {
            device.config_entry_id
            for device in (get_device(registry, d) for d in info.member_ids)
            if device is not None
        }
        if new_device.config_entry_id in member_entries:
            continue
        seen.add((new, info.subentry_id))
        if item.anchor_device_id == info.main_id and item.mac == info.value:
            auto.append(item)
        shown.append(
            replace(
                item,
                join_link_id=info.subentry_id,
                join_link_title=info.title,
                join_device_id=new,
            )
        )
    return shown, auto


def may_join(hass: HomeAssistant, link_id: str, device_id: str) -> bool:
    """Whether a device may still join this link: in no link, and not linked."""
    info = next(
        (m for m in _link_membership(hass).values() if m.subentry_id == link_id),
        None,
    )
    return info is not None and device_id not in linked_device_ids(hass)


def may_link(
    hass: HomeAssistant,
    device_id_a: str,
    device_id_b: str,
    anchor_device_id: str | None,
    mac: str | None,
    linked: set[str],
) -> bool:
    """Whether two devices may be linked, given the devices already linked.

    Neither may be linked to another device already. The one exception
    extends a link: the linked device is the main device of an existing link
    on this MAC address, and the other device joins it.
    """
    a_linked = device_id_a in linked
    b_linked = device_id_b in linked
    if not a_linked and not b_linked:
        return True
    if a_linked and b_linked:
        return False
    if anchor_device_id is None or mac is None or anchor_device_id not in linked:
        return False
    return (
        find_link(hass, link_unique_id(anchor_device_id, mac), anchor_device_id, mac)
        is not None
    )


def find_link(
    hass: HomeAssistant, unique_id: str, main_id: str | None, mac: str
) -> tuple[ConfigSubentry, dict[str, object]] | None:
    """The link for this anchor and MAC, including ones saved before unique ids."""
    for _hub, subentry, settings in iter_links(hass):
        if subentry.unique_id == unique_id:
            return subentry, settings
    for _hub, subentry, settings in iter_links(hass):
        if (
            settings[CONF_MAIN_DEVICE_ID] == main_id
            and settings[CONF_MAC_ADDRESS] == mac
        ):
            return subentry, settings
    return None


def linked_on_another_link(
    hass: HomeAssistant, device_id: str, skip_subentry_id: str | None
) -> bool:
    """Return whether another link already includes this device.

    Extending the link that already has the anchor is handled by the caller.
    Putting the device on a second link would give it a second MAC.
    """
    for _hub, subentry, settings in iter_links(hass):
        if subentry.subentry_id == skip_subentry_id:
            continue
        if (
            device_id == settings[CONF_MAIN_DEVICE_ID]
            or device_id in settings[CONF_LINKED_DEVICE_IDS]
        ):
            return True
    return False


@callback
def save_link(
    hass: HomeAssistant,
    hub: ConfigEntry,
    subentry: ConfigSubentry | None,
    payload: dict[str, Any],
    title: str,
    unique_id: str,
) -> None:
    """Add a link to the hub, or update the one it extends."""
    if subentry is None:
        hass.config_entries.async_add_subentry(
            hub,
            ConfigSubentry(
                data=MappingProxyType(payload),
                subentry_type=SUBENTRY_LINK,
                title=title,
                unique_id=unique_id,
            ),
        )
        return
    hass.config_entries.async_update_subentry(
        hub, subentry, data=payload, title=title, unique_id=unique_id
    )


def _mac_conflicts(registry, device, connection, allowed_ids: set[str]) -> bool:
    """Return whether another device from the same config entry already has ``connection``."""
    if not device.config_entry_id:
        return True
    for other in dr.async_entries_for_config_entry(registry, device.config_entry_id):
        if other.id == device.id or other.id in allowed_ids:
            continue
        if connection in other.connections:
            return True
    return False


def link_payload(
    main_id: str | None,
    linked_ids: list[str],
    mac: str,
    added: dict[str, bool],
    origin: str,
) -> dict[str, Any]:
    return {
        CONF_MAIN_DEVICE_ID: main_id,
        CONF_LINKED_DEVICE_IDS: linked_ids,
        CONF_MAC_ADDRESS: mac,
        CONF_ADDED_CONNECTIONS: {
            device_id: bool(added.get(device_id, False)) for device_id in linked_ids
        },
        CONF_ORIGIN: origin,
    }


def _publish_auto_link_issue(hass: HomeAssistant, notices: list[str]) -> None:
    """Open or refresh the repair that lists what was linked automatically."""
    if not notices:
        return
    shown = notices[:MAX_LISTED_SUGGESTIONS]
    summary = "\n".join(shown)
    extra = len(notices) - len(shown)
    if extra > 0:
        summary = f"{summary}\nAnd {extra} more."
    count = 0
    for line in notices:
        _anchor, _sep, names = line.partition(": ")
        count += len([name for name in names.split(", ") if name])
    registry = ir.async_get(hass)
    current = registry.async_get_issue(DOMAIN, ISSUE_AUTO_LINKED)
    if current is not None and current.dismissed_version is not None:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_AUTO_LINKED)
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_AUTO_LINKED,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_AUTO_LINKED,
        translation_placeholders={"count": str(count), "summary": summary},
        learn_more_url="https://github.com/bisman-automations/ha-device-linker#how-auto-linking-works",
    )


def auto_linked_summaries(hass: HomeAssistant) -> list[dict[str, object]]:
    """Automatic links for the sensor and diagnostics. MAC addresses are omitted."""
    registry = dr.async_get(hass)
    items: list[dict[str, object]] = []
    for _hub, subentry, settings in iter_links(hass):
        if settings[CONF_ORIGIN] != ORIGIN_AUTO:
            continue
        main_id = settings[CONF_MAIN_DEVICE_ID]
        main = get_device(registry, main_id) if isinstance(main_id, str) else None
        main_name = (
            main.name_by_user or main.name or main_id
            if main is not None
            else "Manual MAC"
        )
        linked_names: list[str] = []
        for device_id in settings[CONF_LINKED_DEVICE_IDS]:
            if not isinstance(device_id, str):
                continue
            device = get_device(registry, device_id)
            if device is not None:
                linked_names.append(device.name_by_user or device.name or device_id)
        items.append(
            {"title": subentry.title, "main": main_name, "linked": linked_names}
        )
        if len(items) >= MAX_LISTED_SUGGESTIONS:
            break
    return items


def _iter_devices(registry: dr.DeviceRegistry):
    """Yield device entries on Home Assistant 2026.8 and newer.

    Through 2026.9 ``registry.devices`` is a mapping of id to entry, so
    iterating it yields ids. Later releases yield the entries directly.
    """
    devices = registry.devices
    for item in devices:
        if isinstance(item, str):
            yield devices[item]
        else:
            yield item


def _device_views(
    hass: HomeAssistant, *, include_services: bool = False
) -> list[DeviceView]:
    """Snapshot registry devices that can take part in a suggestion."""
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)
    views: list[DeviceView] = []
    for device in _iter_devices(registry):
        if (
            device.disabled_by is not None
            or getattr(device, "is_composite_device", False)
            or is_service(
                hass,
                device,
                include_services=include_services,
            )
        ):
            continue
        if not device.config_entry_id:
            continue
        config_entry = hass.config_entries.async_get_entry(device.config_entry_id)
        if config_entry is None or config_entry.domain == DOMAIN:
            continue
        area = areas.async_get_area(device.area_id) if device.area_id else None
        views.append(
            DeviceView(
                device_id=device.id,
                name=device.name_by_user or device.name or device.id,
                area_id=device.area_id,
                area_name=area.name if area else None,
                config_entry_id=device.config_entry_id,
                integration=config_entry.domain,
                connections=text_pairs(device.connections),
                identifiers=text_pairs(device.identifiers),
            )
        )
    return views


def _copied_macs(hass: HomeAssistant) -> set[tuple[str, str]]:
    """MAC connections this integration added, as ``(device id, mac)``.

    ``added_connections`` is true only for a device whose address Device
    Linker copied on. The main device, which already had the address, is not
    in that set. Anchor selection ignores these so a linked Apple TV does not
    become the anchor of the next match.
    """
    copied: set[tuple[str, str]] = set()
    for _hub, _subentry, settings in iter_links(hass):
        mac = settings[CONF_MAC_ADDRESS]
        added = settings[CONF_ADDED_CONNECTIONS]
        if not isinstance(mac, str) or not isinstance(added, dict):
            continue
        for device_id, flag in added.items():
            if flag and isinstance(device_id, str):
                copied.add((device_id, mac))
    return copied


def _linked_pairs(hass: HomeAssistant) -> set[str]:
    """Pairs already covered by a link."""
    pairs: set[str] = set()
    for _hub, _subentry, settings in iter_links(hass):
        main = settings[CONF_MAIN_DEVICE_ID]
        linked = [
            device_id
            for device_id in settings[CONF_LINKED_DEVICE_IDS]
            if isinstance(device_id, str)
        ]
        pairs |= pairs_among(main if isinstance(main, str) else None, linked)
    return pairs


def plan_entry_data(
    device_a_id: str,
    device_b_id: str,
    mac: str,
    connections_a: set[tuple[str, str]],
    connections_b: set[tuple[str, str]],
) -> dict[str, Any]:
    """Build config-entry data that links two devices with ``mac``."""
    main_id, linked_ids = choose_link_ends(
        device_a_id, device_b_id, mac, connections_a, connections_b
    )
    connection = (dr.CONNECTION_NETWORK_MAC, mac)
    added = {
        device_id: connection
        not in (connections_a if device_id == device_a_id else connections_b)
        for device_id in linked_ids
    }
    return link_payload(main_id, linked_ids, mac, added, ORIGIN_MANUAL)


async def async_load_settings(hass: HomeAssistant) -> SuggestionSettings:
    """Return preferences from the running manager or from storage."""
    manager = async_get_manager(hass)
    if manager is not None:
        return manager.settings
    return await _load_settings(Store(hass, STORAGE_VERSION, STORAGE_KEY))


async def async_apply_suggestion_preferences(
    hass: HomeAssistant, user_input: dict[str, Any], *, scan: bool = True
) -> None:
    """Save suggestion fields submitted from an options form."""
    manager = async_get_manager(hass)
    current = (
        manager.settings if manager is not None else await async_load_settings(hass)
    )
    threshold = _clamp_threshold(
        user_input.get(CONF_SUGGESTION_THRESHOLD, current.threshold)
    )
    match_hardware = bool(user_input.get(CONF_MATCH_HARDWARE, current.match_hardware))
    match_names = bool(user_input.get(CONF_MATCH_NAMES, current.match_names))
    auto_link = bool(user_input.get(CONF_AUTO_LINK, current.auto_link))
    auto_link_threshold = _clamp_threshold(
        user_input.get(CONF_AUTO_LINK_THRESHOLD, current.auto_link_threshold)
    )
    auto_link_similar = bool(
        user_input.get(CONF_AUTO_LINK_SIMILAR, current.auto_link_similar)
    )
    include_services = bool(
        user_input.get(CONF_INCLUDE_SERVICES, current.include_services)
    )
    ignored_devices = frozenset(
        d
        for d in user_input.get(CONF_IGNORED_DEVICES, current.ignored_devices) or []
        if isinstance(d, str) and d
    )
    if manager is not None:
        await manager.async_update_settings(
            threshold=threshold,
            match_hardware=match_hardware,
            match_names=match_names,
            auto_link=auto_link,
            auto_link_threshold=auto_link_threshold,
            auto_link_similar=auto_link_similar,
            include_services=include_services,
            ignored_devices=ignored_devices,
            scan=scan,
        )
        return
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    await _save_settings(
        store,
        replace(
            current,
            threshold=threshold,
            match_hardware=match_hardware,
            match_names=match_names,
            auto_link=auto_link,
            auto_link_threshold=auto_link_threshold,
            auto_link_similar=auto_link_similar,
            include_services=include_services,
            ignored_devices=ignored_devices,
        ),
    )


async def async_remember_unlinked(hass: HomeAssistant, pairs: set[str]) -> None:
    """Persist pairs the user unlinked, even if the manager has already stopped.

    Removing the last config entry unloads the manager before Home Assistant
    calls ``async_remove_entry``. The store write still has to happen, or the
    next setup will link those devices again.
    """
    pairs = {pair for pair in pairs if pair}
    if not pairs:
        return
    manager = async_get_manager(hass)
    if manager is not None:
        await manager.async_remember_unlinked(pairs)
        return
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    current = await _load_settings(store)
    await _save_settings(
        store,
        replace(
            current,
            unlinked=current.unlinked | pairs,
            announced=current.announced - pairs,
        ),
    )


def _clamp_threshold(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        number = DEFAULT_SUGGESTION_THRESHOLD
    return min(SUGGESTION_THRESHOLD_MAX, max(SUGGESTION_THRESHOLD_MIN, number))


def _string_set(stored: dict[str, Any], key: str) -> frozenset[str]:
    raw = stored.get(key, [])
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(item for item in raw if isinstance(item, str))


async def _load_settings(store: Store[dict[str, Any]]) -> SuggestionSettings:
    """Load preferences. Keys added in 1.3.0 default when a 1.2 store omits them."""
    stored = await store.async_load()
    if not isinstance(stored, dict):
        return SuggestionSettings()
    return SuggestionSettings(
        threshold=_clamp_threshold(
            stored.get("threshold", DEFAULT_SUGGESTION_THRESHOLD)
        ),
        match_hardware=bool(stored.get("match_hardware", True)),
        match_names=bool(stored.get("match_names", True)),
        auto_link=bool(stored.get("auto_link", True)),
        auto_link_threshold=_clamp_threshold(
            stored.get("auto_link_threshold", DEFAULT_AUTO_LINK_THRESHOLD)
        ),
        auto_link_similar=bool(stored.get("auto_link_similar", True)),
        include_services=bool(stored.get("include_services", False)),
        ignored_devices=_string_set(stored, "ignored_devices"),
        dismissed=_string_set(stored, "dismissed"),
        unlinked=_string_set(stored, "unlinked"),
        announced=_string_set(stored, "announced"),
    )


async def _save_settings(
    store: Store[dict[str, Any]], settings: SuggestionSettings
) -> None:
    await store.async_save(
        {
            "threshold": settings.threshold,
            "match_hardware": settings.match_hardware,
            "match_names": settings.match_names,
            "auto_link": settings.auto_link,
            "auto_link_threshold": settings.auto_link_threshold,
            "auto_link_similar": settings.auto_link_similar,
            "include_services": settings.include_services,
            "ignored_devices": sorted(settings.ignored_devices),
            "dismissed": sorted(settings.dismissed),
            "unlinked": sorted(settings.unlinked),
            "announced": sorted(settings.announced),
        }
    )
