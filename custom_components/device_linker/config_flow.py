"""Config, subentry, and options flows for Device Linker.

The config flow creates the one Device Linker entry (the hub) and turns an
accepted suggestion into a link on it. Each link is a subentry: **Add link**
picks the main device, the devices to link, and the MAC; **Edit link**
changes the devices or the MAC. Deleting a link unlinks it. **Configure** on
the hub holds the settings for suggestions and automatic linking.
"""

from __future__ import annotations

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

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_AUTO_LINK,
    CONF_AUTO_LINK_SIMILAR,
    CONF_AUTO_LINK_THRESHOLD,
    CONF_HUB,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_MATCH_HARDWARE,
    CONF_MATCH_NAMES,
    CONF_ORIGIN,
    CONF_SELECTED_MAC_ADDRESS,
    CONF_SUGGESTION_THRESHOLD,
    CONFIG_VERSION,
    DOMAIN,
    HUB_TITLE,
    HUB_UNIQUE_ID,
    MAC_PATTERN,
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
    find_link,
    link_payload,
    plan_entry_data,
    save_link,
)
from .registry import (
    get_device,
    hub_entry,
    is_hub,
    link_settings,
    link_title,
    link_unique_id,
)

MANUAL_ENTRY = "__manual_entry__"


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
        for connection_type, value in device.connections
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
) -> list[selector.SelectOptionDict]:
    """Devices of other integrations, labelled with the integration."""
    registry = dr.async_get(hass)
    options = []
    for entry in hass.config_entries.async_entries():
        if entry.domain == DOMAIN:
            continue
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if device.id == exclude_device_id or (
                exclude_config_entry_id is not None
                and device.config_entry_id == exclude_config_entry_id
            ):
                continue
            if get_device(registry, device.id) is None:
                continue
            options.append(
                selector.SelectOptionDict(
                    value=device.id,
                    label=_device_label(device, entry.domain, include_mac),
                )
            )
    return sorted(options, key=lambda option: option["label"].casefold())


def _submitted_mac(user_input: dict[str, Any]) -> str:
    """Return the typed MAC, or else the selected one."""
    custom_mac = (user_input.get(CONF_MAC_ADDRESS) or "").strip()
    return custom_mac or user_input.get(CONF_SELECTED_MAC_ADDRESS, "")


def _mac_schema(discovered: list[str], current: str | None = None) -> dict:
    """MAC fields: discovered choices plus a manual override."""
    schema: dict[vol.Marker, object] = {}
    if current and discovered and current not in discovered:
        discovered = sorted([*discovered, current])
    if discovered:
        default = current or (discovered[0] if len(discovered) == 1 else None)
        key = (
            vol.Optional(CONF_SELECTED_MAC_ADDRESS, default=default)
            if default
            else vol.Optional(CONF_SELECTED_MAC_ADDRESS)
        )
        schema[key] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=mac, label=mac)
                    for mac in discovered
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        schema[vol.Optional(CONF_MAC_ADDRESS)] = str
    elif current:
        schema[vol.Required(CONF_MAC_ADDRESS, default=current)] = str
    else:
        schema[vol.Required(CONF_MAC_ADDRESS)] = str
    return schema


def _network_macs(*devices) -> list[str]:
    return sorted(
        {
            dr.format_mac(value)
            for device in devices
            if device is not None
            for connection_type, value in device.connections
            if connection_type == dr.CONNECTION_NETWORK_MAC
        }
    )


def _connection_conflicts(
    hass: HomeAssistant,
    devices,
    connection: tuple[str, str],
    *,
    allowed_ids: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[str]:
    """Devices that stop this MAC from being shared.

    Two selected devices from one integration, or another device of that
    integration that already has the MAC. ``allowed_ids`` are devices this
    link already put the MAC on.
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
                and connection in other.connections
            ):
                conflicts.update(_device_labels(hass, [other]))
    return sorted(conflicts, key=str.casefold)


def _suggestion_schema(settings) -> dict[vol.Marker, object]:
    """Settings for suggestions and automatic linking, for every link."""
    return {
        vol.Optional(CONF_AUTO_LINK, default=settings.auto_link): (
            selector.BooleanSelector()
        ),
        vol.Optional(CONF_AUTO_LINK_SIMILAR, default=settings.auto_link_similar): (
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
        self.context["title_placeholders"] = {
            "device_a": discovery_info["name_a"],
            "device_b": discovery_info["name_b"],
        }
        reason = discovery_info["reason"]
        if reason == REASON_MAC:
            return await self.async_step_discovery_mac()
        if reason == REASON_IDENTIFIER:
            return await self.async_step_discovery_identifier()
        return await self.async_step_discovery_name()

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

        discovered = _network_macs(device_a, device_b)
        if discovery.get("mac"):
            discovered = sorted({*discovered, dr.format_mac(discovery["mac"])})
        errors: dict[str, str] = {}
        if user_input is not None:
            mac = _submitted_mac(user_input)
            if not mac or not MAC_PATTERN.fullmatch(mac):
                errors["base"] = "invalid_mac"
            else:
                mac = dr.format_mac(mac)
                connection = (dr.CONNECTION_NETWORK_MAC, mac)
                if _connection_conflicts(self.hass, [device_a, device_b], connection):
                    errors["base"] = "connection_conflict"
                else:
                    _add_link_to_hub(
                        self.hass,
                        hub,
                        plan_entry_data(
                            device_a.id,
                            device_b.id,
                            mac,
                            set(device_a.connections),
                            set(device_b.connections),
                        ),
                    )
                    return self.async_abort(reason="linked")

        return self.async_show_form(
            step_id=f"discovery_{reason}",
            data_schema=vol.Schema(_mac_schema(discovered)),
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
    if existing is not None:
        current = existing[1]
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
        link_payload(main_id, linked, mac, added, origin),
        link_title(_display_name(main) if main else "Manual MAC", len(linked)),
        unique_id,
    )


class LinkSubentryFlow(ConfigSubentryFlow):
    """Add a link by hand, or edit one."""

    def __init__(self) -> None:
        """Initialize flow state."""
        self._main_device_id: str | None = None
        self._linked_device_ids: list[str] = []

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
            main = get_device(dr.async_get(self.hass), main_id)
            if main is None:
                errors["base"] = "device_not_found"
            else:
                self._main_device_id = main.id
                return await self.async_step_devices()

        options = [
            selector.SelectOptionDict(value=MANUAL_ENTRY, label="Manual MAC entry"),
            *_device_options(self.hass, include_mac=True),
        ]
        if len(options) == 1:
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
        if user_input is not None:
            linked_ids = list(dict.fromkeys(user_input[CONF_LINKED_DEVICE_IDS]))
            error = _check_devices(registry, main, linked_ids)
            if error:
                errors["base"] = error
            else:
                self._linked_device_ids = linked_ids
                return await self.async_step_confirm()

        options = _device_options(
            self.hass,
            exclude_device_id=self._main_device_id,
            exclude_config_entry_id=main.config_entry_id if main else None,
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
                "main_name": _display_name(main) if main else "Manual MAC entry"
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
            if not mac or not MAC_PATTERN.fullmatch(mac):
                errors["base"] = "invalid_mac"
            else:
                mac = dr.format_mac(mac)
                connection = (dr.CONNECTION_NETWORK_MAC, mac)
                conflicts = _connection_conflicts(self.hass, devices, connection)
                unique_id = link_unique_id(self._main_device_id, mac)
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
                            _display_name(main) if main else "Manual MAC", len(devices)
                        ),
                        data=link_payload(
                            self._main_device_id,
                            self._linked_device_ids,
                            mac,
                            {
                                device.id: connection not in device.connections
                                for device in devices
                                if device is not None
                            },
                            ORIGIN_MANUAL,
                        ),
                        unique_id=unique_id,
                    )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(_mac_schema(_network_macs(main))),
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
        """Change the linked devices or the MAC. The main device stays."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        old = link_settings(subentry.data)
        registry = dr.async_get(self.hass)
        main_id = (
            old[CONF_MAIN_DEVICE_ID]
            if isinstance(old[CONF_MAIN_DEVICE_ID], str)
            else None
        )
        main = get_device(registry, main_id) if main_id else None
        old_ids = [d for d in old[CONF_LINKED_DEVICE_IDS] if isinstance(d, str)]
        old_mac = str(old[CONF_MAC_ADDRESS])
        old_added = dict(old[CONF_ADDED_CONNECTIONS])
        options = _device_options(
            self.hass,
            exclude_device_id=main_id,
            exclude_config_entry_id=main.config_entry_id if main else None,
        )
        available = {option["value"] for option in options}

        errors: dict[str, str] = {}
        conflicts: list[str] = []
        if user_input is not None:
            linked_ids = list(dict.fromkeys(user_input.get(CONF_LINKED_DEVICE_IDS, [])))
            mac = _submitted_mac(user_input)
            error = _check_devices(registry, main, linked_ids)
            if error:
                errors["base"] = error
            elif not mac or not MAC_PATTERN.fullmatch(mac):
                errors["base"] = "invalid_mac"
            else:
                mac = dr.format_mac(mac)
                connection = (dr.CONNECTION_NETWORK_MAC, mac)
                devices = [get_device(registry, d) for d in linked_ids]
                owned_here = (
                    {d for d in old_ids if old_added.get(d)}
                    if mac == old_mac
                    else set()
                )
                conflicts = _connection_conflicts(
                    self.hass, devices, connection, allowed_ids=owned_here
                )
                if conflicts:
                    errors["base"] = "connection_conflict"
                else:
                    added = {}
                    for device in devices:
                        if device is None:
                            continue
                        if device.id in old_ids and mac == old_mac:
                            added[device.id] = bool(old_added.get(device.id, False))
                        else:
                            added[device.id] = connection not in device.connections
                    return self.async_update_and_abort(
                        entry,
                        subentry,
                        title=link_title(
                            _display_name(main) if main else "Manual MAC",
                            len(linked_ids),
                        ),
                        unique_id=link_unique_id(main_id, mac),
                        data=link_payload(
                            main_id, linked_ids, mac, added, str(old[CONF_ORIGIN])
                        ),
                    )

        current_ids = [d for d in old_ids if d in available]
        schema: dict[vol.Marker, object] = {
            vol.Required(CONF_LINKED_DEVICE_IDS, default=current_ids): (
                _devices_selector(options)
            ),
            **_mac_schema(_network_macs(main), current=old_mac),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "main_name": _display_name(main) if main else "Manual entry",
                "conflicting_devices": ", ".join(conflicts) or "none",
            },
        )


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


class DeviceLinkerOptionsFlow(config_entries.OptionsFlow):
    """Settings for suggestions and automatic linking."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the settings. They apply to every link."""
        if user_input is not None:
            await async_apply_suggestion_preferences(self.hass, user_input)
            return self.async_create_entry(data={})
        settings = await async_load_settings(self.hass)
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(_suggestion_schema(settings))
        )
