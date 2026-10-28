"""Keep link suggestions up to date and show them as discoveries.

A single manager runs for the whole integration, started by the first real
config entry and stopped when the last one unloads. Preferences and dismissed
pairs live in a Store so they survive a restart. Device and area registry
updates schedule one scan after a short debounce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import SOURCE_DISCOVERY, SOURCE_IGNORE, ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_MATCH_HARDWARE,
    CONF_MATCH_NAMES,
    CONF_SUGGESTION_THRESHOLD,
    DATA_MANAGER,
    DEFAULT_SUGGESTION_THRESHOLD,
    DOMAIN,
    MAX_DISCOVERY_FLOWS,
    MAX_LISTED_SUGGESTIONS,
    SERVICE_RESCAN,
    STORAGE_KEY,
    STORAGE_VERSION,
    SUGGESTION_DEBOUNCE_SECONDS,
    SUGGESTION_THRESHOLD_MAX,
    SUGGESTION_THRESHOLD_MIN,
)
from .matching import (
    DeviceView,
    Suggestion,
    choose_link_ends,
    pair_key,
    suggest,
    suggestion_unique_id,
)
from .registry import get_entry_settings

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SuggestionSettings:
    """Integration-wide suggestion preferences."""

    threshold: float = DEFAULT_SUGGESTION_THRESHOLD
    match_hardware: bool = True
    match_names: bool = True
    dismissed: frozenset[str] = frozenset()


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
        self._unsub_device: Any = None
        self._unsub_area: Any = None
        self._unsub_debounce: Any = None
        self._sensor: Any = None
        self._sensor_entry_id: str | None = None
        self._platform_adders: dict[str, Any] = {}

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
            dr.EVENT_DEVICE_REGISTRY_UPDATED, self._schedule_scan
        )
        self._unsub_area = self.hass.bus.async_listen(
            ar.EVENT_AREA_REGISTRY_UPDATED, self._schedule_scan
        )
        self.hass.services.async_register(DOMAIN, SERVICE_RESCAN, self._async_rescan)

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
        if self.hass.services.has_service(DOMAIN, SERVICE_RESCAN):
            self.hass.services.async_remove(DOMAIN, SERVICE_RESCAN)
        self._sensor = None
        self._sensor_entry_id = None
        self._platform_adders.clear()

    async def async_scan(self) -> list[Suggestion]:
        """Rebuild suggestions now and reconcile discovery flows."""
        self._suggestions = suggest(
            _device_views(self.hass),
            linked_pairs=_linked_pairs(self.hass),
            dismissed=set(self._settings.dismissed),
            threshold=self._settings.threshold,
            match_hardware=self._settings.match_hardware,
            match_names=self._settings.match_names,
        )
        _LOGGER.debug(
            "Suggestion scan found %s candidate(s) (threshold %s, hardware %s, names %s)",
            len(self._suggestions),
            self._settings.threshold,
            self._settings.match_hardware,
            self._settings.match_names,
        )
        await self._async_sync_flows()
        self._publish_sensor()
        return self._suggestions

    async def async_update_settings(
        self,
        *,
        threshold: float,
        match_hardware: bool,
        match_names: bool,
    ) -> None:
        """Store new preferences and scan again."""
        self._settings = SuggestionSettings(
            threshold=_clamp_threshold(threshold),
            match_hardware=match_hardware,
            match_names=match_names,
            dismissed=self._settings.dismissed,
        )
        await _save_settings(self._store, self._settings)
        await self.async_scan()

    async def async_dismiss(self, unique_id: str) -> None:
        """Remember a suggestion so later scans do not offer it again."""
        if unique_id in self._settings.dismissed:
            return
        self._settings = SuggestionSettings(
            threshold=self._settings.threshold,
            match_hardware=self._settings.match_hardware,
            match_names=self._settings.match_names,
            dismissed=self._settings.dismissed | {unique_id},
        )
        await _save_settings(self._store, self._settings)
        self._suggestions = [
            item
            for item in self._suggestions
            if suggestion_unique_id(item.device_id_a, item.device_id_b) != unique_id
        ]
        self._publish_sensor()

    def is_dismissed(self, unique_id: str) -> bool:
        """Return whether this suggestion unique id was ignored."""
        return unique_id in self._settings.dismissed

    def async_add_sensor(self, entry_id: str, async_add_entities: Any) -> None:
        """Create the suggestions sensor once, on the first real entry."""
        self._platform_adders[entry_id] = async_add_entities
        if self._sensor is not None:
            return
        from .sensor import SuggestionSensor

        self._sensor = SuggestionSensor(self)
        self._sensor_entry_id = entry_id
        async_add_entities([self._sensor])

    def async_release_sensor(self, entry_id: str) -> None:
        """Forget the sensor when its config entry unloads, then adopt another."""
        self._platform_adders.pop(entry_id, None)
        if self._sensor_entry_id != entry_id:
            return
        self._sensor = None
        self._sensor_entry_id = None
        if not self._platform_adders:
            return
        next_entry_id, async_add_entities = next(iter(self._platform_adders.items()))
        from .sensor import SuggestionSensor

        self._sensor = SuggestionSensor(self)
        self._sensor_entry_id = next_entry_id
        async_add_entities([self._sensor])

    def listed_suggestions(self) -> list[dict[str, object]]:
        """Attributes for the sensor and diagnostics, without MAC addresses."""
        return [
            item.as_attribute() for item in self._suggestions[:MAX_LISTED_SUGGESTIONS]
        ]

    @callback
    def _schedule_scan(self, _event: Event) -> None:
        """Collapse a burst of registry events into one scan.

        Tracked on Home Assistant's clock so a test can fire the time change,
        and so unload cancels it instead of leaving a timer behind.
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
        "area_a": item.area_name_a or "Unassigned",
        "area_b": item.area_name_b or "Unassigned",
    }


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


def _device_views(hass: HomeAssistant) -> list[DeviceView]:
    """Snapshot registry devices that can take part in a suggestion."""
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)
    views: list[DeviceView] = []
    for device in _iter_devices(registry):
        if device.disabled_by is not None or getattr(
            device, "is_composite_device", False
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
                connections=tuple(device.connections),
                identifiers=tuple(device.identifiers),
            )
        )
    return views


def _linked_pairs(hass: HomeAssistant) -> set[str]:
    """Pairs already covered by a Device Linker entry."""
    pairs: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.source == SOURCE_IGNORE:
            continue
        try:
            settings = get_entry_settings(entry)
        except KeyError:
            continue
        linked = [
            device_id
            for device_id in settings[CONF_LINKED_DEVICE_IDS]
            if isinstance(device_id, str)
        ]
        main = settings[CONF_MAIN_DEVICE_ID]
        ids = list(linked)
        if isinstance(main, str):
            for device_id in linked:
                pairs.add(pair_key(main, device_id))
        for index, left in enumerate(ids):
            for right in ids[index + 1 :]:
                pairs.add(pair_key(left, right))
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
    return {
        CONF_MAIN_DEVICE_ID: main_id,
        CONF_LINKED_DEVICE_IDS: linked_ids,
        CONF_MAC_ADDRESS: mac,
        CONF_ADDED_CONNECTIONS: added,
    }


async def async_load_settings(hass: HomeAssistant) -> SuggestionSettings:
    """Return preferences from the running manager or from storage."""
    manager = async_get_manager(hass)
    if manager is not None:
        return manager.settings
    return await _load_settings(Store(hass, STORAGE_VERSION, STORAGE_KEY))


async def async_apply_suggestion_preferences(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> None:
    """Save suggestion fields submitted from an options form."""
    threshold = _clamp_threshold(
        user_input.get(CONF_SUGGESTION_THRESHOLD, DEFAULT_SUGGESTION_THRESHOLD)
    )
    match_hardware = bool(user_input.get(CONF_MATCH_HARDWARE, True))
    match_names = bool(user_input.get(CONF_MATCH_NAMES, True))
    manager = async_get_manager(hass)
    if manager is not None:
        await manager.async_update_settings(
            threshold=threshold,
            match_hardware=match_hardware,
            match_names=match_names,
        )
        return
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    current = await _load_settings(store)
    await _save_settings(
        store,
        SuggestionSettings(
            threshold=threshold,
            match_hardware=match_hardware,
            match_names=match_names,
            dismissed=current.dismissed,
        ),
    )


def _clamp_threshold(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        number = DEFAULT_SUGGESTION_THRESHOLD
    return min(SUGGESTION_THRESHOLD_MAX, max(SUGGESTION_THRESHOLD_MIN, number))


async def _load_settings(store: Store[dict[str, Any]]) -> SuggestionSettings:
    stored = await store.async_load()
    if not isinstance(stored, dict):
        return SuggestionSettings()
    dismissed = stored.get("dismissed", [])
    if not isinstance(dismissed, list):
        dismissed = []
    return SuggestionSettings(
        threshold=_clamp_threshold(
            stored.get("threshold", DEFAULT_SUGGESTION_THRESHOLD)
        ),
        match_hardware=bool(stored.get("match_hardware", True)),
        match_names=bool(stored.get("match_names", True)),
        dismissed=frozenset(item for item in dismissed if isinstance(item, str)),
    )


async def _save_settings(
    store: Store[dict[str, Any]], settings: SuggestionSettings
) -> None:
    await store.async_save(
        {
            "threshold": settings.threshold,
            "match_hardware": settings.match_hardware,
            "match_names": settings.match_names,
            "dismissed": sorted(settings.dismissed),
        }
    )


def entry_is_link(entry: ConfigEntry) -> bool:
    """Return False for ignored discoveries, which store no link."""
    return entry.source != SOURCE_IGNORE
