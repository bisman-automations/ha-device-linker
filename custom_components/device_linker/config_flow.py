"""Config, subentry, and options flows for Device Linker.

The config flow creates the one Device Linker entry (the hub) and turns an
accepted suggestion into a link on it. Each link is a subentry: **Add link**
picks the main device, the devices to link, and the MAC; **Edit link**
changes the devices or the MAC. Deleting a link unlinks it. **Configure** on
the hub holds the settings for suggestions and automatic linking.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, selector
from homeassistant.loader import LoaderError, async_get_loaded_integration

from .cleanup import find_leftovers, leftover_label, remove_leftovers
from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_AUTO_LINK,
    CONF_AUTO_LINK_SIMILAR,
    CONF_AUTO_LINK_THRESHOLD,
    CONF_CONFIRMED,
    CONF_HUB,
    CONF_IGNORED_DEVICES,
    CONF_INCLUDE_SERVICES,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_MATCH_HARDWARE,
    CONF_MATCH_NAMES,
    CONF_NEVER_SUGGEST,
    CONF_ORIGIN,
    CONF_PREFERRED_MAC_SOURCES,
    CONF_SELECTED_MAC_ADDRESS,
    CONF_SUGGESTION_THRESHOLD,
    CONFIG_VERSION,
    DOMAIN,
    HUB_TITLE,
    HUB_UNIQUE_ID,
    ORIGIN_MANUAL,
    REASON_IDENTIFIER,
    REASON_MAC,
    REASON_NAME,
    SUBENTRY_LINK,
    SUGGESTION_THRESHOLD_MAX,
    SUGGESTION_THRESHOLD_MIN,
    SUGGESTION_THRESHOLD_STEP,
)
from .manager import (
    async_apply_suggestion_preferences,
    async_get_manager,
    async_load_settings,
    device_views,
    find_link,
    link_payload,
    may_join,
    may_link,
    plan_entry_data,
    preferred_mac_sources,
    save_link,
)
from .matching import integration_rank, normalize_mac
from .recheck import find_mismatches, unlink_mismatches
from .registry import (
    already_linked,
    device_has_link,
    devices_in_links,
    get_device,
    hub_entry,
    is_hub,
    is_mac,
    is_service,
    iter_links,
    link_settings,
    link_title,
    link_unique_id,
    linked_device_ids,
    normalize_link_value,
    text_pairs,
)

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY = "__manual_entry__"
# Add link: add devices to a link that already exists.
JOIN_LINK = "__join_link__"
CONF_LINK_ID = "link_id"


def _threshold_selector() -> selector.NumberSelector:
    """Slider from 0.5 to 1 in steps of 0.05."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=SUGGESTION_THRESHOLD_MIN,
            max=SUGGESTION_THRESHOLD_MAX,
            step=SUGGESTION_THRESHOLD_STEP,
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _display_name(device: dr.DeviceEntry) -> str:
    return device.name_by_user or device.name or device.id


def _device_label(device: dr.DeviceEntry, integration: str, include_mac: bool) -> str:
    """Label a device with its integration, and optionally its MAC addresses."""
    label = f"{_display_name(device)} ({integration})"
    if not include_mac:
        return label
    macs = sorted(
        dr.format_mac(value)
        for connection_type, value in text_pairs(device.connections)
        if connection_type == dr.CONNECTION_NETWORK_MAC
    )
    return f"{label} — {', '.join(macs) if macs else 'no registered MAC'}"


def _device_labels(hass: HomeAssistant, devices) -> list[str]:
    labels = []
    for device in devices:
        if device is None:
            continue
        config_entry = hass.config_entries.async_get_entry(device.config_entry_id)
        labels.append(
            _device_label(
                device, config_entry.domain if config_entry else "unknown", False
            )
        )
    return labels


def _device_options(
    hass: HomeAssistant,
    *,
    exclude_device_id: str | None = None,
    exclude_config_entry_id: str | None = None,
    include_mac: bool = False,
    include_services: bool = False,
    exclude_device_ids: set[str] | None = None,
) -> list[selector.SelectOptionDict]:
    """Devices of other integrations, labelled with the integration.

    ``exclude_device_ids`` leaves out devices that are already linked.
    """
    registry = dr.async_get(hass)
    options = []
    for entry in hass.config_entries.async_entries():
        if entry.domain == DOMAIN:
            continue
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                device.id == exclude_device_id
                or device.id in (exclude_device_ids or ())
                or (
                    exclude_config_entry_id is not None
                    and device.config_entry_id == exclude_config_entry_id
                )
            ):
                continue
            if get_device(registry, device.id) is None or is_service(
                hass, device, include_services=include_services
            ):
                continue
            options.append(
                selector.SelectOptionDict(
                    value=device.id,
                    label=_device_label(device, entry.domain, include_mac),
                )
            )
    return sorted(options, key=lambda option: option["label"].casefold())


def _submitted_mac(user_input: dict[str, Any]) -> str:
    """The typed value, or else the selected MAC, normalized; "" when not valid.

    A MAC address is returned lower-case with colons. Anything else, such as a
    serial number or VIN, is a shared ID (see ``normalize_link_value``).
    """
    custom = (user_input.get(CONF_MAC_ADDRESS) or "").strip()
    return (
        normalize_link_value(custom or user_input.get(CONF_SELECTED_MAC_ADDRESS, ""))
        or ""
    )


def _mac_origins(hass: HomeAssistant, *devices) -> dict[str, list[tuple[str, str]]]:
    """Each MAC address with the devices it came from: ``(label, domain)``.

    A MAC registered as a connection, or carried in an identifier (a Shelly
    or Zigbee device's own MAC), counts for that device.
    """
    origins: dict[str, list[tuple[str, str]]] = {}
    for device in devices:
        if device is None:
            continue
        entry = hass.config_entries.async_get_entry(device.config_entry_id)
        domain = entry.domain if entry else "unknown"
        label = _device_label(device, domain, False)
        values = {
            dr.format_mac(value)
            for kind, value in text_pairs(device.connections)
            if kind == dr.CONNECTION_NETWORK_MAC
        }
        values |= {
            mac
            for _domain, value in text_pairs(device.identifiers)
            if (mac := normalize_mac(value))
        }
        for mac in values:
            if (label, domain) not in origins.setdefault(mac, []):
                origins[mac].append((label, domain))
    return origins


def _mac_sources(hass: HomeAssistant, *devices) -> dict[str, str]:
    """Label each MAC address with the devices it came from.

    ``"Kitchen Speaker (unifi)"``, several joined with commas.
    """
    return {
        mac: ", ".join(label for label, _domain in pairs)
        for mac, pairs in _mac_origins(hass, *devices).items()
    }


def _rank_macs(
    hass: HomeAssistant, macs: list[str], devices, preferred: tuple[str, ...]
) -> tuple[list[str], str | None]:
    """Order addresses by the preferred MAC sources, most trusted first.

    Returns the ordered list and the address to preselect: the one from the
    most trusted integration, when it is on the list.
    """
    origins = _mac_origins(hass, *devices)

    def rank(mac: str) -> int:
        return min(
            (
                integration_rank(domain, preferred)
                for _l, domain in origins.get(mac, [])
            ),
            default=len(preferred),
        )

    ordered = sorted(macs, key=lambda mac: (rank(mac), mac))
    top = ordered[0] if ordered and rank(ordered[0]) < len(preferred) else None
    return ordered, top


def _mac_schema(
    discovered: list[str],
    current: str | None = None,
    sources: dict[str, str] | None = None,
    *,
    required: bool = True,
    preselect: str | None = None,
) -> dict:
    """MAC fields: discovered choices plus a manual override.

    ``sources`` labels each address with the device and integration it came
    from, so two addresses can be told apart.
    """
    sources = sources or {}
    schema: dict[vol.Marker, object] = {}
    if current and discovered and current not in discovered:
        discovered = [*discovered, current]
    if discovered:
        default = (
            current or preselect or (discovered[0] if len(discovered) == 1 else None)
        )
        key = (
            vol.Optional(CONF_SELECTED_MAC_ADDRESS, default=default)
            if default
            else vol.Optional(CONF_SELECTED_MAC_ADDRESS)
        )
        schema[key] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=mac,
                        label=f"{mac} — {sources[mac]}" if mac in sources else mac,
                    )
                    for mac in discovered
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        schema[vol.Optional(CONF_MAC_ADDRESS)] = str
    elif current:
        schema[vol.Required(CONF_MAC_ADDRESS, default=current)] = str
    elif required:
        schema[vol.Required(CONF_MAC_ADDRESS)] = str
    else:
        # A suggestion can be declined with "never suggest" and no address.
        schema[vol.Optional(CONF_MAC_ADDRESS)] = str
    return schema


def _never_suggest_schema(*devices) -> dict:
    """On a suggestion: instead of linking, never suggest these devices."""
    return {
        vol.Optional(CONF_NEVER_SUGGEST, default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=d.id, label=_display_name(d))
                    for d in devices
                    if d is not None
                ],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    }


def _network_macs(*devices) -> list[str]:
    return sorted(
        {
            dr.format_mac(value)
            for device in devices
            if device is not None
            for connection_type, value in text_pairs(device.connections)
            if connection_type == dr.CONNECTION_NETWORK_MAC
        }
    )


def _connection_conflicts(
    hass: HomeAssistant,
    devices,
    value: str,
    *,
    allowed_ids: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[str]:
    """Devices that stop this MAC address or shared ID from being shared.

    Two selected devices from one integration, or another device of that
    integration that already has it. ``allowed_ids`` are devices this link
    already put it on.
    """
    registry = dr.async_get(hass)
    conflicts: set[str] = set()
    selected_by_entry: dict[str, dr.DeviceEntry] = {}
    for device in devices:
        if device is None:
            continue
        prior = selected_by_entry.get(device.config_entry_id)
        if prior is not None:
            conflicts.update(_device_labels(hass, [prior, device]))
        selected_by_entry[device.config_entry_id] = device
        for other in dr.async_entries_for_config_entry(
            registry, device.config_entry_id
        ):
            if (
                other.id != device.id
                and other.id not in allowed_ids
                and device_has_link(other, value)
            ):
                conflicts.update(_device_labels(hass, [other]))
    return sorted(conflicts, key=str.casefold)


def _mac_source_options(
    hass: HomeAssistant, chosen: tuple[str, ...]
) -> list[selector.SelectOptionDict]:
    """Integrations that register MAC addresses, plus any already chosen."""
    registry = dr.async_get(hass)
    domains: set[str] = set(chosen)
    for entry in hass.config_entries.async_entries():
        if entry.domain == DOMAIN or entry.domain in domains:
            continue
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if any(
                kind == dr.CONNECTION_NETWORK_MAC
                for kind, _value in text_pairs(device.connections)
            ):
                domains.add(entry.domain)
                break

    def name(domain: str) -> str:
        try:
            return f"{async_get_loaded_integration(hass, domain).name} ({domain})"
        except LoaderError:
            return domain

    # Chosen ones first, in their order, so the list reads as the priority.
    rest = sorted(domains - set(chosen), key=lambda d: name(d).casefold())
    return [
        selector.SelectOptionDict(value=domain, label=name(domain))
        for domain in [*chosen, *rest]
    ]


def _ignorable_device_options(
    hass: HomeAssistant, settings
) -> list[selector.SelectOptionDict]:
    """Devices that can be put on the never-suggest list.

    The same devices suggestions can use: hardware, plus services only when
    Include services is on. Devices already on the list stay listed, so they
    can be taken off.
    """
    options = _device_options(hass, include_services=settings.include_services)
    listed = {option["value"] for option in options}
    registry = dr.async_get(hass)
    for device_id in sorted(settings.ignored_devices - listed):
        device = get_device(registry, device_id)
        options.append(
            selector.SelectOptionDict(
                value=device_id,
                label=_display_name(device) if device else "A removed device",
            )
        )
    return options


def _suggestion_schema(
    settings, hass: HomeAssistant | None = None
) -> dict[vol.Marker, object]:
    """Settings for suggestions and automatic linking, for every link."""
    preferred = preferred_mac_sources(settings)
    extra: dict[vol.Marker, object] = {}
    if hass is not None and settings.preferred_mac_sources is None:
        # The defaults: show only the integrations that are installed.
        installed = {entry.domain for entry in hass.config_entries.async_entries()}
        preferred = tuple(d for d in preferred if d in installed)
    if hass is not None:
        extra[vol.Optional(CONF_PREFERRED_MAC_SOURCES, default=list(preferred))] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_mac_source_options(hass, preferred),
                    multiple=True,
                    custom_value=True,
                    sort=False,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        )
    tail: dict[vol.Marker, object] = {}
    if hass is not None:
        tail[
            vol.Optional(CONF_IGNORED_DEVICES, default=sorted(settings.ignored_devices))
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_ignorable_device_options(hass, settings),
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    return {
        **extra,
        vol.Optional(CONF_AUTO_LINK, default=settings.auto_link): (
            selector.BooleanSelector()
        ),
        vol.Optional(CONF_AUTO_LINK_SIMILAR, default=settings.auto_link_similar): (
            selector.BooleanSelector()
        ),
        vol.Optional(CONF_INCLUDE_SERVICES, default=settings.include_services): (
            selector.BooleanSelector()
        ),
        vol.Optional(
            CONF_AUTO_LINK_THRESHOLD, default=settings.auto_link_threshold
        ): _threshold_selector(),
        vol.Optional(CONF_SUGGESTION_THRESHOLD, default=settings.threshold): (
            _threshold_selector()
        ),
        vol.Optional(CONF_MATCH_HARDWARE, default=settings.match_hardware): (
            selector.BooleanSelector()
        ),
        vol.Optional(CONF_MATCH_NAMES, default=settings.match_names): (
            selector.BooleanSelector()
        ),
        **tail,
    }


class DeviceLinkerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up the hub, and accept suggestions as links on it."""

    VERSION = CONFIG_VERSION

    def __init__(self) -> None:
        """Initialize flow state."""
        self._discovery: dict[str, Any] | None = None

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Links are subentries of the hub."""
        if not is_hub(config_entry):
            return {}
        return {SUBENTRY_LINK: LinkSubentryFlow}

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Only the hub has settings."""
        return is_hub(config_entry)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """Settings for suggestions and automatic linking."""
        return DeviceLinkerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up Device Linker once. Links are added from its entry."""
        if hub_entry(self.hass) is not None:
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            await self.async_set_unique_id(HUB_UNIQUE_ID)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=HUB_TITLE, data={CONF_HUB: True})
        return self.async_show_form(step_id="user")

    async def async_step_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Offer a suggestion, and remember it so Ignore can find it."""
        self._discovery = discovery_info
        await self.async_set_unique_id(discovery_info["unique_id"])
        self._abort_if_unique_id_configured()
        manager = async_get_manager(self.hass)
        if manager is not None and manager.is_dismissed(discovery_info["unique_id"]):
            return self.async_abort(reason="ignored")
        if discovery_info.get("join_link_id"):
            side = (
                "b"
                if discovery_info.get("join_device_id") == discovery_info["device_id_b"]
                else "a"
            )
            new_name = discovery_info[f"name_{side}"]
            integration = discovery_info.get(f"integration_{side}", "")
            self.context["title_placeholders"] = {
                "name": f"Add {new_name} ({integration}) to "
                f"{discovery_info['join_link_title']}"
            }
            return await self.async_step_discovery_join()
        self.context["title_placeholders"] = {
            "name": f"{discovery_info['name_a']} and {discovery_info['name_b']}",
            "device_a": discovery_info["name_a"],
            "device_b": discovery_info["name_b"],
        }
        reason = discovery_info["reason"]
        if reason == REASON_MAC:
            return await self.async_step_discovery_mac()
        if reason == REASON_IDENTIFIER:
            return await self.async_step_discovery_identifier()
        return await self.async_step_discovery_name()

    async def _async_never_suggest(
        self, chosen: list[str], devices: tuple
    ) -> ConfigFlowResult:
        """Add the chosen devices to the never-suggest list, without linking."""
        allowed = {d.id: d for d in devices if d is not None}
        ids = [d for d in chosen if d in allowed]
        manager = async_get_manager(self.hass)
        if manager is not None and ids:
            await manager.async_ignore_devices(ids)
        return self.async_abort(
            reason="devices_ignored",
            description_placeholders={
                "names": ", ".join(_display_name(allowed[d]) for d in ids)
            },
        )

    async def async_step_discovery_join(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a device to the link that a matching device is already in."""
        hub = hub_entry(self.hass)
        if hub is None:
            return self.async_abort(reason="not_set_up")
        discovery = self._discovery or {}
        link_id = discovery.get("join_link_id", "")
        subentry = hub.subentries.get(link_id)
        registry = dr.async_get(self.hass)
        new_id = discovery.get("join_device_id", "")
        new = get_device(registry, new_id)
        member_id = (
            discovery.get("device_id_a")
            if new_id == discovery.get("device_id_b")
            else discovery.get("device_id_b")
        )
        member = get_device(registry, member_id or "")
        if subentry is None or new is None or member is None:
            return self.async_abort(reason="device_not_found")
        settings = link_settings(subentry.data)
        main_id = settings[CONF_MAIN_DEVICE_ID]
        main_id = main_id if isinstance(main_id, str) else None
        linked_ids = [
            d for d in settings[CONF_LINKED_DEVICE_IDS] or [] if isinstance(d, str)
        ]
        if member.id not in {main_id, *linked_ids} or not may_join(
            self.hass, link_id, new.id
        ):
            return self.async_abort(reason="already_linked")
        value = str(settings[CONF_MAC_ADDRESS])
        main = get_device(registry, main_id) if main_id else None
        errors: dict[str, str] = {}
        placeholders = {
            "device": _display_name(new),
            "integration": str(
                discovery.get(
                    "integration_b"
                    if new.id == discovery.get("device_id_b")
                    else "integration_a",
                    "",
                )
            ),
            "member": _display_name(member),
            "link": subentry.title,
            "why": _match_reason(discovery),
            "conflicting_devices": "",
        }
        if user_input is not None and user_input.get(CONF_NEVER_SUGGEST):
            return await self._async_never_suggest(
                user_input[CONF_NEVER_SUGGEST], (new, member)
            )
        if user_input is not None:
            conflicts = _connection_conflicts(self.hass, [new], value)
            if _check_devices(registry, main, [*linked_ids, new.id]):
                return self.async_abort(reason="same_integration")
            if conflicts:
                errors["base"] = "connection_conflict"
                placeholders["conflicting_devices"] = ", ".join(conflicts)
            else:
                devices = [get_device(registry, d) for d in [*linked_ids, new.id]]
                save_link(
                    self.hass,
                    hub,
                    subentry,
                    link_payload(
                        main_id,
                        [*linked_ids, new.id],
                        value,
                        {**dict(settings[CONF_ADDED_CONNECTIONS]), new.id: True},
                        str(settings[CONF_ORIGIN]),
                        bool(settings[CONF_CONFIRMED]),
                    ),
                    link_title(_link_name(main, devices), len(devices) + bool(main)),
                    subentry.unique_id or link_unique_id(main_id, value),
                )
                return self.async_abort(reason="linked")
        return self.async_show_form(
            step_id="discovery_join",
            data_schema=vol.Schema(_never_suggest_schema(new)),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_discovery_mac(self, user_input: dict[str, Any] | None = None):
        """Confirm a MAC-address match."""
        return await self._async_discovery_confirm(REASON_MAC, user_input)

    async def async_step_discovery_identifier(
        self, user_input: dict[str, Any] | None = None
    ):
        """Confirm a hardware-identifier match."""
        return await self._async_discovery_confirm(REASON_IDENTIFIER, user_input)

    async def async_step_discovery_name(self, user_input: dict[str, Any] | None = None):
        """Confirm a name match."""
        return await self._async_discovery_confirm(REASON_NAME, user_input)

    async def async_step_ignore(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Remember a dismissed suggestion, then store Home Assistant's ignore entry."""
        unique_id = user_input["unique_id"]
        manager = async_get_manager(self.hass)
        if manager is not None:
            await manager.async_dismiss(unique_id)
        await self.async_set_unique_id(unique_id, raise_on_progress=False)
        return self.async_create_entry(title=user_input["title"], data={})

    async def _async_discovery_confirm(
        self, reason: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Add the suggested link to the hub, or show the MAC form."""
        hub = hub_entry(self.hass)
        if hub is None:
            return self.async_abort(reason="not_set_up")
        discovery = self._discovery or {}
        registry = dr.async_get(self.hass)
        device_a = get_device(registry, discovery.get("device_id_a", ""))
        device_b = get_device(registry, discovery.get("device_id_b", ""))
        if device_a is None or device_b is None:
            return self.async_abort(reason="device_not_found")
        # Another link may have taken one of them since this was suggested.
        if not may_link(
            self.hass,
            device_a.id,
            device_b.id,
            discovery.get("anchor_device_id") or None,
            discovery.get("mac") or None,
            linked_device_ids(self.hass),
        ):
            return self.async_abort(reason="already_linked")

        discovered = _network_macs(device_a, device_b)
        if discovery.get("mac"):
            discovered = sorted({*discovered, dr.format_mac(discovery["mac"])})
        errors: dict[str, str] = {}
        if user_input is not None and user_input.get(CONF_NEVER_SUGGEST):
            return await self._async_never_suggest(
                user_input[CONF_NEVER_SUGGEST], (device_a, device_b)
            )
        if user_input is not None:
            mac = _submitted_mac(user_input)
            if not mac:
                errors["base"] = "invalid_mac"
            elif _connection_conflicts(self.hass, [device_a, device_b], mac):
                errors["base"] = "connection_conflict"
            else:
                if is_mac(mac):
                    payload = plan_entry_data(
                        device_a.id,
                        device_b.id,
                        mac,
                        set(device_a.connections),
                        set(device_b.connections),
                    )
                else:
                    # A shared ID that neither device has: both get it.
                    payload = link_payload(
                        None,
                        [device_a.id, device_b.id],
                        mac,
                        {device_a.id: True, device_b.id: True},
                        ORIGIN_MANUAL,
                    )
                _add_link_to_hub(self.hass, hub, payload)
                return self.async_abort(reason="linked")

        ordered, preselect = _rank_macs(
            self.hass,
            discovered,
            (device_a, device_b),
            preferred_mac_sources(await async_load_settings(self.hass)),
        )
        return self.async_show_form(
            step_id=f"discovery_{reason}",
            data_schema=vol.Schema(
                {
                    **_mac_schema(
                        ordered,
                        sources=_mac_sources(self.hass, device_a, device_b),
                        required=False,
                        preselect=preselect,
                    ),
                    **_never_suggest_schema(device_a, device_b),
                }
            ),
            errors=errors,
            description_placeholders={
                "device_a": str(discovery.get("name_a", "")),
                "device_b": str(discovery.get("name_b", "")),
                "integration_a": str(discovery.get("integration_a", "")),
                "integration_b": str(discovery.get("integration_b", "")),
                "area_a": str(discovery.get("area_a", "Unassigned")),
                "area_b": str(discovery.get("area_b", "Unassigned")),
                "score": str(discovery.get("score", "")),
            },
        )


@callback
def _add_link_to_hub(
    hass: HomeAssistant, hub: ConfigEntry, payload: dict[str, Any]
) -> None:
    """Add an accepted suggestion, or extend the link that has the same anchor."""
    registry = dr.async_get(hass)
    main_id = payload[CONF_MAIN_DEVICE_ID]
    mac = payload[CONF_MAC_ADDRESS]
    unique_id = link_unique_id(main_id, mac)
    existing = find_link(hass, unique_id, main_id, mac)
    linked = list(payload[CONF_LINKED_DEVICE_IDS])
    added = dict(payload[CONF_ADDED_CONNECTIONS])
    origin = ORIGIN_MANUAL
    confirmed = True
    if existing is not None:
        current = existing[1]
        confirmed = bool(current[CONF_CONFIRMED])
        linked = [*current[CONF_LINKED_DEVICE_IDS]] + [
            device_id
            for device_id in linked
            if device_id not in current[CONF_LINKED_DEVICE_IDS]
        ]
        added = {**dict(current[CONF_ADDED_CONNECTIONS]), **added}
        origin = str(current[CONF_ORIGIN])
    main = get_device(registry, main_id) if main_id else None
    save_link(
        hass,
        hub,
        existing[0] if existing is not None else None,
        link_payload(main_id, linked, mac, added, origin, confirmed),
        link_title(
            _link_name(main, [get_device(registry, device_id) for device_id in linked]),
            len(linked) + bool(main),
        ),
        unique_id,
    )


class LinkSubentryFlow(ConfigSubentryFlow):
    """Add a link by hand, or edit one."""

    def __init__(self) -> None:
        """Initialize flow state."""
        self._main_device_id: str | None = None
        self._linked_device_ids: list[str] = []
        self._join_link_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the device that has the MAC address, or manual entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            main_id = user_input[CONF_MAIN_DEVICE_ID]
            if main_id == MANUAL_ENTRY:
                self._main_device_id = None
                return await self.async_step_devices()
            if main_id == JOIN_LINK:
                return await self.async_step_join()
            main = get_device(dr.async_get(self.hass), main_id)
            if main is None:
                errors["base"] = "device_not_found"
            else:
                self._main_device_id = main.id
                return await self.async_step_devices()

        joinable = (
            [
                selector.SelectOptionDict(
                    value=JOIN_LINK, label="Add devices to an existing link"
                )
            ]
            if self._get_entry().subentries
            else []
        )
        include_services = (await async_load_settings(self.hass)).include_services
        options = [
            selector.SelectOptionDict(value=MANUAL_ENTRY, label="Manual MAC entry"),
            *joinable,
            *_device_options(
                self.hass,
                include_mac=True,
                # A device linked only by Home Assistant can share its MAC
                # with more devices, so it can be the main device.
                exclude_device_ids=devices_in_links(self.hass),
                include_services=include_services,
            ),
        ]
        if len(options) == 1 + len(joinable):
            return self.async_abort(reason="no_devices")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAIN_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "hidden": _hidden_note(
                    self.hass, include_services, hidden=devices_in_links(self.hass)
                )
            },
        )

    async def async_step_join(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the link to add devices to."""
        entry = self._get_entry()
        links = {
            subentry_id: subentry
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_LINK
        }
        if not links:
            return self.async_abort(reason="no_links")
        if user_input is not None and user_input[CONF_LINK_ID] in links:
            self._join_link_id = user_input[CONF_LINK_ID]
            return await self.async_step_join_devices()
        options = sorted(
            (
                selector.SelectOptionDict(value=subentry_id, label=subentry.title)
                for subentry_id, subentry in links.items()
            ),
            key=lambda option: option["label"].casefold(),
        )
        return self.async_show_form(
            step_id="join",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINK_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_join_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose devices to add to the chosen link."""
        entry = self._get_entry()
        subentry = entry.subentries.get(self._join_link_id or "")
        if subentry is None:
            return self.async_abort(reason="no_links")
        registry = dr.async_get(self.hass)
        settings = link_settings(subentry.data)
        main_id = settings[CONF_MAIN_DEVICE_ID]
        main_id = main_id if isinstance(main_id, str) else None
        main = get_device(registry, main_id) if main_id else None
        old_ids = [
            d for d in settings[CONF_LINKED_DEVICE_IDS] or [] if isinstance(d, str)
        ]
        value = str(settings[CONF_MAC_ADDRESS])
        members = [
            device
            for device in (get_device(registry, d) for d in [main_id, *old_ids] if d)
            if device is not None
        ]
        member_entries = {device.config_entry_id for device in members}
        errors: dict[str, str] = {}
        conflicts: list[str] = []
        if user_input is not None:
            new_ids = [
                d
                for d in dict.fromkeys(user_input[CONF_LINKED_DEVICE_IDS])
                if d not in old_ids and d != main_id
            ]
            new_devices = [get_device(registry, d) for d in new_ids]
            linked_ids = [*old_ids, *new_ids]
            error = _check_devices(registry, main, linked_ids)
            if not new_ids:
                errors["base"] = "no_linked_devices"
            elif error:
                errors["base"] = error
            else:
                conflicts = _connection_conflicts(self.hass, new_devices, value)
                if conflicts:
                    errors["base"] = "connection_conflict"
                else:
                    added = dict(settings[CONF_ADDED_CONNECTIONS])
                    added.update(dict.fromkeys(new_ids, True))
                    save_link(
                        self.hass,
                        entry,
                        subentry,
                        link_payload(
                            main_id,
                            linked_ids,
                            value,
                            added,
                            str(settings[CONF_ORIGIN]),
                            bool(settings[CONF_CONFIRMED]),
                        ),
                        link_title(
                            _link_name(
                                main, [get_device(registry, d) for d in linked_ids]
                            ),
                            len(linked_ids) + bool(main),
                        ),
                        subentry.unique_id or link_unique_id(main_id, value),
                    )
                    return self.async_abort(reason="joined")

        # Devices not linked to anything, from integrations not in the link yet.
        options = [
            option
            for option in _device_options(
                self.hass,
                include_services=(
                    await async_load_settings(self.hass)
                ).include_services,
                exclude_device_ids=linked_device_ids(self.hass),
            )
            if (device := get_device(registry, option["value"])) is not None
            and device.config_entry_id not in member_entries
        ]
        if not options:
            return self.async_abort(reason="no_devices")
        return self.async_show_form(
            step_id="join_devices",
            data_schema=vol.Schema(
                {vol.Required(CONF_LINKED_DEVICE_IDS): _devices_selector(options)}
            ),
            errors=errors,
            description_placeholders={
                "link": subentry.title,
                "conflicting_devices": ", ".join(conflicts) or "none",
            },
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the devices to link to the main device."""
        registry = dr.async_get(self.hass)
        main = (
            get_device(registry, self._main_device_id) if self._main_device_id else None
        )
        if self._main_device_id and main is None:
            return self.async_abort(reason="device_not_found")
        errors: dict[str, str] = {}
        already_names = "none"
        if user_input is not None:
            linked_ids = list(dict.fromkeys(user_input[CONF_LINKED_DEVICE_IDS]))
            error = _check_devices(registry, main, linked_ids)
            already = [
                device
                for device in (get_device(registry, d) for d in linked_ids)
                if main is not None
                and device is not None
                and already_linked(main, device)
            ]
            if error:
                errors["base"] = error
            elif already:
                errors["base"] = "already_linked"
                already_names = ", ".join(_device_labels(self.hass, already))
            else:
                self._linked_device_ids = linked_ids
                return await self.async_step_confirm()

        options = _device_options(
            self.hass,
            include_services=(await async_load_settings(self.hass)).include_services,
            exclude_device_id=self._main_device_id,
            exclude_config_entry_id=main.config_entry_id if main else None,
            exclude_device_ids=linked_device_ids(self.hass),
        )
        if not options:
            return self.async_abort(reason="no_devices")
        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINKED_DEVICE_IDS): _devices_selector(options),
                }
            ),
            errors=errors,
            description_placeholders={
                "main_name": _display_name(main) if main else "Manual entry",
                "already_linked": already_names,
                "hidden": _hidden_note(
                    self.hass,
                    (await async_load_settings(self.hass)).include_services,
                    skip_device_id=self._main_device_id,
                ),
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm the MAC address and add the link."""
        registry = dr.async_get(self.hass)
        main = (
            get_device(registry, self._main_device_id) if self._main_device_id else None
        )
        devices = [get_device(registry, d) for d in self._linked_device_ids]
        if (self._main_device_id and main is None) or not devices or None in devices:
            return self.async_abort(reason="device_not_found")

        errors: dict[str, str] = {}
        conflicts: list[str] = []
        if user_input is not None:
            mac = _submitted_mac(user_input)
            if not mac:
                errors["base"] = "invalid_mac"
            else:
                main_id, linked = _ends_for(main, devices, mac)
                conflicts = _connection_conflicts(self.hass, linked, mac)
                unique_id = link_unique_id(main_id, mac)
                if conflicts:
                    errors["base"] = "connection_conflict"
                elif any(
                    sub.unique_id == unique_id
                    for sub in self._get_entry().subentries.values()
                ):
                    return self.async_abort(reason="already_configured")
                else:
                    return self.async_create_entry(
                        title=link_title(
                            _link_name(main if main_id else None, linked),
                            len(linked) + bool(main_id),
                        ),
                        data=link_payload(
                            main_id,
                            [device.id for device in linked],
                            mac,
                            {
                                device.id: not device_has_link(device, mac)
                                for device in linked
                            },
                            ORIGIN_MANUAL,
                        ),
                        unique_id=unique_id,
                    )

        ordered, preselect = _rank_macs(
            self.hass,
            _network_macs(main),
            (main,),
            preferred_mac_sources(await async_load_settings(self.hass)),
        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                _mac_schema(
                    ordered,
                    sources=_mac_sources(self.hass, main),
                    preselect=preselect,
                )
            ),
            errors=errors,
            description_placeholders={
                "main_name": _display_name(main) if main else "Manual entry",
                "linked_device_names": ", ".join(_device_labels(self.hass, devices)),
                "conflicting_devices": ", ".join(conflicts) or "none",
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the devices in a link, or the MAC address they share.

        The form lists every device in the link, the main device included, and
        offers the MAC addresses those devices registered themselves, each
        named after its device. The main device is the one that has the chosen
        address; a typed address that none of them has makes a manual link.
        """
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        old = link_settings(subentry.data)
        registry = dr.async_get(self.hass)
        old_main_id = (
            old[CONF_MAIN_DEVICE_ID]
            if isinstance(old[CONF_MAIN_DEVICE_ID], str)
            else None
        )
        old_ids = [d for d in old[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)]
        old_mac = str(old[CONF_MAC_ADDRESS])
        old_added = dict(old[CONF_ADDED_CONNECTIONS])
        copied = {d for d in old_ids if old_added.get(d)}
        options = _device_options(
            self.hass,
            include_services=(await async_load_settings(self.hass)).include_services,
            # Its own devices stay listed; devices linked elsewhere don't.
            exclude_device_ids=linked_device_ids(
                self.hass, skip_subentry_id=subentry.subentry_id
            )
            - {old_main_id, *old_ids},
        )
        available = {option["value"] for option in options}

        def own_macs(device: dr.DeviceEntry) -> list[str]:
            """MAC addresses a device registered itself (not ones this link copied)."""
            return [
                mac
                for mac in _network_macs(device)
                if not (device.id in copied and mac == old_mac)
            ]

        errors: dict[str, str] = {}
        conflicts: list[str] = []
        if user_input is not None:
            selected = list(dict.fromkeys(user_input.get(CONF_LINKED_DEVICE_IDS, [])))
            devices = [get_device(registry, d) for d in selected]
            mac = _submitted_mac(user_input)
            confirmed = bool(user_input.get(CONF_CONFIRMED, old[CONF_CONFIRMED]))
            kept = {old_main_id, *old_ids} - {None}
            # A confirmed link keeps its devices and address until you untick
            # Confirmed (in the same save is fine).
            locked = (
                bool(old[CONF_CONFIRMED])
                and confirmed
                and (not kept <= set(selected) or (mac and mac != old_mac))
            )
            if any(device is None for device in devices):
                errors["base"] = "device_not_found"
            elif not mac:
                errors["base"] = "invalid_mac"
            elif locked:
                errors["base"] = "confirmed_locked"
            else:
                owners = [
                    device.id
                    for device in devices
                    if device is not None and mac in own_macs(device)
                ]
                main_id = (
                    old_main_id
                    if old_main_id in owners
                    else (owners[0] if owners else None)
                )
                main = get_device(registry, main_id) if main_id else None
                linked_ids = [d for d in selected if d != main_id]
                error = _check_devices(registry, main, linked_ids)
                unique_id = link_unique_id(main_id, mac)
                if error:
                    errors["base"] = error
                elif any(
                    sub.unique_id == unique_id
                    and sub.subentry_id != subentry.subentry_id
                    for sub in entry.subentries.values()
                ):
                    return self.async_abort(reason="already_configured")
                else:
                    linked = [get_device(registry, d) for d in linked_ids]
                    conflicts = _connection_conflicts(
                        self.hass,
                        linked,
                        mac,
                        allowed_ids=copied if mac == old_mac else set(),
                    )
                    if conflicts:
                        errors["base"] = "connection_conflict"
                    else:
                        added = {}
                        for device in linked:
                            if device is None:
                                continue
                            if device.id in old_ids and mac == old_mac:
                                added[device.id] = bool(old_added.get(device.id, False))
                            else:
                                added[device.id] = not device_has_link(device, mac)
                        return self.async_update_and_abort(
                            entry,
                            subentry,
                            title=link_title(
                                _link_name(main, linked), len(linked_ids) + bool(main)
                            ),
                            unique_id=unique_id,
                            data=link_payload(
                                main_id,
                                linked_ids,
                                mac,
                                added,
                                str(old[CONF_ORIGIN]),
                                confirmed,
                            ),
                        )

        current_ids = [
            d
            for d in ([old_main_id] if old_main_id else []) + old_ids
            if d in available
        ]
        sources: dict[str, list[str]] = {}
        domains: dict[str, list[str]] = {}
        for device_id in current_ids:
            device = get_device(registry, device_id)
            if device is None:
                continue
            config = self.hass.config_entries.async_get_entry(device.config_entry_id)
            domain = config.domain if config else "unknown"
            for mac in own_macs(device):
                sources.setdefault(mac, []).append(_device_label(device, domain, False))
                domains.setdefault(mac, []).append(domain)
        if old_mac not in sources:
            sources[old_mac] = ["entered by hand"]
        preferred = preferred_mac_sources(await async_load_settings(self.hass))

        def mac_rank(mac: str) -> tuple[int, str]:
            ranks = [integration_rank(d, preferred) for d in domains.get(mac, [])]
            return (min(ranks, default=len(preferred)), mac)

        schema: dict[vol.Marker, object] = {
            vol.Required(CONF_LINKED_DEVICE_IDS, default=current_ids): (
                _devices_selector(options)
            ),
            vol.Optional(CONF_SELECTED_MAC_ADDRESS, default=old_mac): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=mac, label=f"{mac} — {', '.join(names)}"
                            )
                            for mac, names in sorted(
                                sources.items(), key=lambda item: mac_rank(item[0])
                            )
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            ),
            vol.Optional(CONF_MAC_ADDRESS): str,
            vol.Optional(CONF_CONFIRMED, default=bool(old[CONF_CONFIRMED])): (
                selector.BooleanSelector()
            ),
        }
        main = get_device(registry, old_main_id) if old_main_id else None
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "main_name": _display_name(main) if main else "Manual entry",
                "conflicting_devices": ", ".join(conflicts) or "none",
            },
        )


def _ends_for(main, devices, value: str):
    """The main device id and the devices that get ``value``.

    The main device keeps that role only when it already has the MAC address
    or shared ID. Otherwise (a shared ID, or a MAC typed by hand) it gets the
    value too, and the link has no main device.
    """
    linked = [device for device in devices if device is not None]
    if main is None:
        return None, linked
    if device_has_link(main, value):
        return main.id, linked
    return None, [main, *linked]


def _match_reason(discovery: dict[str, Any]) -> str:
    """Why two devices were matched, in a sentence."""
    reason = discovery.get("reason")
    if reason == REASON_MAC:
        return "They share a MAC address."
    if reason == REASON_IDENTIFIER:
        return (
            "They share a hardware identifier, such as a Bluetooth or Zigbee address."
        )
    return f"Their names are {discovery.get('score', '')} alike."


# How many hidden devices the note names before "and N more".
HIDDEN_NAMES = 5


def _hidden_note(
    hass: HomeAssistant,
    include_services: bool,
    *,
    skip_device_id: str | None = None,
    hidden: set[str] | None = None,
) -> str:
    """Name the devices left out because they're already linked.

    Each is named with the link it's in, or as linked by Home Assistant when
    it shares an identifier or connection with another device on its own.
    Empty when nothing is hidden.
    """
    hidden_ids = {
        option["value"]
        for option in _device_options(hass, include_services=include_services)
    } & (linked_device_ids(hass) if hidden is None else hidden)
    hidden_ids.discard(skip_device_id)
    if not hidden_ids:
        return ""
    link_of: dict[str, str] = {}
    for _hub, subentry, settings in iter_links(hass):
        main_id = settings[CONF_MAIN_DEVICE_ID]
        for device_id in [main_id, *(settings[CONF_LINKED_DEVICE_IDS] or [])]:
            if isinstance(device_id, str):
                link_of.setdefault(device_id, subentry.title)
    registry = dr.async_get(hass)
    named = sorted(
        (
            (_display_name(device), link_of.get(device.id))
            for device in (get_device(registry, d) for d in hidden_ids)
            if device is not None
        ),
        key=lambda pair: pair[0].casefold(),
    )
    parts = [
        f"{name} — in {link}" if link else f"{name} — linked by Home Assistant"
        for name, link in named[:HIDDEN_NAMES]
    ]
    if len(named) > HIDDEN_NAMES:
        parts.append(f"and {len(named) - HIDDEN_NAMES} more")
    count = len(named)
    noun = "device isn't" if count == 1 else "devices aren't"
    return f"{count} {noun} listed because they're already linked: {', '.join(parts)}."


def _link_name(main, devices) -> str:
    """The name in a link's title: the main device, else the first device."""
    if main is not None:
        return _display_name(main)
    for device in devices:
        if device is not None:
            return _display_name(device)
    return "Manual link"


def _devices_selector(options) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _check_devices(registry, main, linked_ids: list[str]) -> str | None:
    """An error key for the chosen devices, or None when they can be linked."""
    if not linked_ids:
        return "no_linked_devices"
    devices = [get_device(registry, device_id) for device_id in linked_ids]
    if any(device is None for device in devices):
        return "device_not_found"
    if main and any(
        device.id == main.id or device.config_entry_id == main.config_entry_id
        for device in devices
        if device is not None
    ):
        return "same_config_entry"
    return None


# The recheck form field holding the devices to take out of their links.
CONF_UNLINK = "unlink"

# The options form field holding the chosen leftovers.
CONF_LEFTOVERS = "leftovers"


class DeviceLinkerOptionsFlow(config_entries.OptionsFlow):
    """Settings for suggestions and automatic linking, and cleaning up leftovers."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to do."""
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "recheck", "cleanup"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the settings. They apply to every link."""
        if user_input is not None:
            await async_apply_suggestion_preferences(self.hass, user_input)
            return self.async_create_entry(data={})
        settings = await async_load_settings(self.hass)
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(_suggestion_schema(settings, self.hass)),
        )

    async def async_step_recheck(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List devices in automatic links the current rules wouldn't link."""
        settings = await async_load_settings(self.hass)
        try:
            mismatches = find_mismatches(
                self.hass,
                self.config_entry,
                device_views(self.hass, include_services=settings.include_services),
                settings,
                preferred_mac_sources(settings),
            )
        except Exception:
            _LOGGER.exception("Rechecking automatic links failed")
            return self.async_abort(reason="recheck_failed")
        if user_input is not None:
            chosen = set(user_input.get(CONF_UNLINK) or [])
            removed = unlink_mismatches(
                self.hass,
                self.config_entry,
                [item.key for item in mismatches if item.key in chosen],
            )
            return self.async_abort(
                reason="rechecked", description_placeholders={"removed": str(removed)}
            )
        if not mismatches:
            return self.async_abort(reason="nothing_to_recheck")
        return self.async_show_form(
            step_id="recheck",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_UNLINK, default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=item.key, label=item.label
                                )
                                for item in mismatches
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(mismatches))},
        )

    async def async_step_cleanup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List addresses left behind by removed links, and remove the chosen ones."""
        settings = await async_load_settings(self.hass)
        try:
            leftovers = find_leftovers(self.hass, self.config_entry, settings.unlinked)
        except Exception:
            # Logged with the traceback, so it shows in Settings > System > Logs.
            _LOGGER.exception("Looking for leftover addresses failed")
            return self.async_abort(reason="cleanup_failed")
        if user_input is not None:
            chosen = set(user_input.get(CONF_LEFTOVERS) or [])
            try:
                removed, failed = remove_leftovers(
                    self.hass, [item.key for item in leftovers if item.key in chosen]
                )
            except Exception:
                _LOGGER.exception("Removing leftover addresses failed")
                return self.async_abort(reason="cleanup_failed")
            if failed:
                return self.async_abort(
                    reason="cleaned_some",
                    description_placeholders={
                        "removed": str(removed),
                        "failed": ", ".join(sorted(set(failed))),
                    },
                )
            return self.async_abort(
                reason="cleaned", description_placeholders={"removed": str(removed)}
            )
        if not leftovers:
            return self.async_abort(reason="no_leftovers")
        return self.async_show_form(
            step_id="cleanup",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LEFTOVERS, default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=item.key, label=leftover_label(item)
                                )
                                for item in leftovers
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(leftovers))},
        )
