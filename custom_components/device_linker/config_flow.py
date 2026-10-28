"""Config and options flows for Device Linker."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, selector

from .const import (
    CONF_ADDED_CONNECTIONS,
    CONF_AUTO_LINK,
    CONF_AUTO_LINK_THRESHOLD,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_MATCH_HARDWARE,
    CONF_MATCH_NAMES,
    CONF_ORIGIN,
    CONF_SELECTED_MAC_ADDRESS,
    CONF_SUGGESTION_THRESHOLD,
    DOMAIN,
    MAC_PATTERN,
    ORIGIN_MANUAL,
    REASON_IDENTIFIER,
    REASON_MAC,
    REASON_NAME,
    SUGGESTION_THRESHOLD_MAX,
    SUGGESTION_THRESHOLD_MIN,
    SUGGESTION_THRESHOLD_STEP,
)
from .manager import (
    async_apply_suggestion_preferences,
    async_get_manager,
    async_load_settings,
    async_remember_unlinked,
    plan_entry_data,
)
from .matching import pairs_touching
from .registry import get_device, get_entry_settings, link_title, link_unique_id

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


def _suggestion_schema(
    threshold: float,
    match_hardware: bool,
    match_names: bool,
    auto_link: bool,
    auto_link_threshold: float,
) -> dict[vol.Marker, object]:
    """Options fields for suggestions and automatic linking.

    The same values apply to every entry. Exact names and hardware matches
    are linked whenever automatic linking is on. The automatic threshold
    applies only to fuzzy names.
    """
    return {
        vol.Optional(CONF_AUTO_LINK, default=auto_link): selector.BooleanSelector(),
        vol.Optional(
            CONF_AUTO_LINK_THRESHOLD, default=auto_link_threshold
        ): _threshold_selector(),
        vol.Optional(
            CONF_SUGGESTION_THRESHOLD, default=threshold
        ): _threshold_selector(),
        vol.Optional(
            CONF_MATCH_HARDWARE, default=match_hardware
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MATCH_NAMES, default=match_names): selector.BooleanSelector(),
    }


class DeviceLinkerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set a main device and link multiple related devices to its MAC."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize flow state."""
        self._main_device_id: str | None = None
        self._linked_device_ids: list[str] = []
        self._discovery: dict[str, Any] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
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
            selector.SelectOptionDict(value=MANUAL_ENTRY, label="Manual MAC entry")
        ]
        options.extend(self._device_options(include_mac=True))
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

    async def async_step_devices(self, user_input: dict[str, Any] | None = None):
        """Choose multiple devices to link to the main device."""
        registry = dr.async_get(self.hass)
        main = (
            get_device(registry, self._main_device_id) if self._main_device_id else None
        )
        if self._main_device_id and main is None:
            return self.async_abort(reason="device_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            linked_ids = list(dict.fromkeys(user_input[CONF_LINKED_DEVICE_IDS]))
            if not linked_ids:
                errors["base"] = "no_linked_devices"
            else:
                devices = [get_device(registry, device_id) for device_id in linked_ids]
                if any(device is None for device in devices):
                    errors["base"] = "device_not_found"
                elif main and any(
                    device.id == main.id
                    or device.config_entry_id == main.config_entry_id
                    for device in devices
                    if device is not None
                ):
                    errors["base"] = "same_config_entry"
                else:
                    self._linked_device_ids = linked_ids
                    return await self.async_step_confirm()

        options = self._device_options(
            exclude_device_id=self._main_device_id,
            exclude_config_entry_id=main.config_entry_id if main else None,
        )
        if not options:
            return self.async_abort(reason="no_devices")
        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINKED_DEVICE_IDS): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "main_name": self._display_name(main) if main else "Manual MAC entry"
            },
        )

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        """Confirm a MAC address and create the group entry."""
        registry = dr.async_get(self.hass)
        main = (
            get_device(registry, self._main_device_id) if self._main_device_id else None
        )
        linked_devices = [
            get_device(registry, device_id) for device_id in self._linked_device_ids
        ]
        if main is None and self._main_device_id:
            return self.async_abort(reason="device_not_found")
        if not linked_devices or any(device is None for device in linked_devices):
            return self.async_abort(reason="device_not_found")

        discovered_macs = sorted(
            {
                dr.format_mac(value)
                for connection_type, value in (main.connections if main else set())
                if connection_type == dr.CONNECTION_NETWORK_MAC
            }
        )
        errors: dict[str, str] = {}
        conflicting_devices: list[str] = []
        if user_input is not None:
            mac_address = self._submitted_mac(user_input)
            if not mac_address or not MAC_PATTERN.fullmatch(mac_address):
                errors["base"] = "invalid_mac"
            else:
                mac_address = dr.format_mac(mac_address)
                connection = (dr.CONNECTION_NETWORK_MAC, mac_address)
                conflicting_devices = self._connection_conflicts(
                    registry, linked_devices, connection
                )
                if conflicting_devices:
                    errors["base"] = "connection_conflict"
                else:
                    await self.async_set_unique_id(
                        link_unique_id(self._main_device_id, mac_address)
                    )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=self._entry_title(main, linked_devices),
                        data={
                            CONF_MAIN_DEVICE_ID: self._main_device_id,
                            CONF_LINKED_DEVICE_IDS: self._linked_device_ids,
                            CONF_MAC_ADDRESS: mac_address,
                            CONF_ADDED_CONNECTIONS: {
                                device.id: connection not in device.connections
                                for device in linked_devices
                                if device is not None
                            },
                            CONF_ORIGIN: ORIGIN_MANUAL,
                        },
                    )

        return self.async_show_form(
            step_id="confirm",
            data_schema=self._mac_schema(discovered_macs),
            errors=errors,
            description_placeholders={
                "main_name": (
                    DeviceLinkerConfigFlow._display_name(main)
                    if main
                    else "Manual entry"
                ),
                "device_count": str(len(linked_devices)),
                "linked_device_names": ", ".join(
                    self._device_labels(self.hass, linked_devices)
                ),
                "conflicting_devices": ", ".join(conflicting_devices) or "none",
            },
        )

    @staticmethod
    def _submitted_mac(user_input: dict[str, Any]) -> str:
        """Return the manually entered MAC or selected suggestion."""
        custom_mac = user_input.get(CONF_MAC_ADDRESS, "").strip()
        return custom_mac or user_input.get(CONF_SELECTED_MAC_ADDRESS, "")

    @staticmethod
    def _mac_schema(discovered_macs: list[str]) -> vol.Schema:
        """Create a schema with discovered choices and a manual override."""
        schema: dict[vol.Marker, object] = {}
        if discovered_macs:
            options = [
                selector.SelectOptionDict(value=mac, label=mac)
                for mac in discovered_macs
            ]
            selected = vol.Optional(CONF_SELECTED_MAC_ADDRESS)
            if len(options) == 1:
                selected = vol.Optional(
                    CONF_SELECTED_MAC_ADDRESS, default=discovered_macs[0]
                )
            schema[selected] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema[vol.Optional(CONF_MAC_ADDRESS)] = str
        else:
            schema[vol.Required(CONF_MAC_ADDRESS)] = str
        return vol.Schema(schema)

    def _connection_conflicts(self, registry, devices, connection) -> list[str]:
        """Return the exact devices causing MAC uniqueness conflicts."""
        conflicts: set[str] = set()
        selected_by_config_entry = {}
        for device in devices:
            if device is None:
                continue
            prior_selected = selected_by_config_entry.get(device.config_entry_id)
            if prior_selected is not None:
                conflicts.update(
                    self._device_labels(self.hass, [prior_selected, device])
                )
            selected_by_config_entry[device.config_entry_id] = device
            for other in dr.async_entries_for_config_entry(
                registry, device.config_entry_id
            ):
                if other.id != device.id and connection in other.connections:
                    conflicts.update(self._device_labels(self.hass, [other]))
        return sorted(conflicts, key=str.casefold)

    @classmethod
    def _device_labels(cls, hass, devices) -> list[str]:
        """Return device names with their integration domains."""
        labels = []
        for device in devices:
            if device is None:
                continue
            config_entry = hass.config_entries.async_get_entry(device.config_entry_id)
            integration_name = config_entry.domain if config_entry else "unknown"
            labels.append(cls._device_label(device, integration_name, False))
        return labels

    @staticmethod
    def _entry_title(main, linked_devices) -> str:
        """Build a readable title for the integration entry."""
        main_name = main.name_by_user or main.name or main.id if main else "Manual MAC"
        return link_title(main_name, len(linked_devices))

    @staticmethod
    def _display_name(device) -> str:
        """Get a readable device name."""
        return device.name_by_user or device.name or device.id

    def _device_options(
        self,
        *,
        exclude_device_id: str | None = None,
        exclude_config_entry_id: str | None = None,
        include_mac: bool = False,
    ) -> list[selector.SelectOptionDict]:
        """Return devices from loaded config entries, with integration labels."""
        registry = dr.async_get(self.hass)
        devices_by_id = {
            device.id: device
            for entry in self.hass.config_entries.async_entries()
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        }
        options = []
        for device in devices_by_id.values():
            if (
                device.id == exclude_device_id
                or device.config_entry_id == exclude_config_entry_id
            ):
                continue
            config_entry = self.hass.config_entries.async_get_entry(
                device.config_entry_id
            )
            if config_entry is None:
                continue
            options.append(
                selector.SelectOptionDict(
                    value=device.id,
                    label=DeviceLinkerConfigFlow._device_label(
                        device, config_entry.domain, include_mac
                    ),
                )
            )
        return sorted(options, key=lambda option: option["label"].casefold())

    @classmethod
    def _device_label(cls, device, integration_name: str, include_mac: bool) -> str:
        """Label devices and optionally show their registered MAC address."""
        label = f"{cls._display_name(device)} ({integration_name})"
        if not include_mac:
            return label
        macs = sorted(
            dr.format_mac(value)
            for connection_type, value in device.connections
            if connection_type == dr.CONNECTION_NETWORK_MAC
        )
        return f"{label} — {', '.join(macs) if macs else 'no registered MAC'}"

    async def async_step_discovery(self, discovery_info: dict[str, Any]):
        """Offer a discovered link and remember it so Ignore can find it."""
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
        """Confirm a same-area name match."""
        return await self._async_discovery_confirm(REASON_NAME, user_input)

    async def async_step_ignore(self, user_input: dict[str, Any]):
        """Persist a dismissed suggestion, then store Home Assistant's ignore entry.

        The integrations page calls this when the user chooses Ignore. The
        unique id is kept in the suggestion store so the pair does not come
        back even if that ignore entry is removed later.
        """
        unique_id = user_input["unique_id"]
        manager = async_get_manager(self.hass)
        if manager is not None:
            await manager.async_dismiss(unique_id)
        await self.async_set_unique_id(unique_id, raise_on_progress=False)
        return self.async_create_entry(title=user_input["title"], data={})

    async def _async_discovery_confirm(
        self, reason: str, user_input: dict[str, Any] | None
    ):
        """Create the link, or show the MAC confirmation form."""
        discovery = self._discovery or {}
        registry = dr.async_get(self.hass)
        device_a = get_device(registry, discovery.get("device_id_a", ""))
        device_b = get_device(registry, discovery.get("device_id_b", ""))
        if device_a is None or device_b is None:
            return self.async_abort(reason="device_not_found")

        discovered = self._discovery_macs(
            device_a, device_b, discovery.get("mac") or None
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            mac_address = self._submitted_mac(user_input)
            if not mac_address or not MAC_PATTERN.fullmatch(mac_address):
                errors["base"] = "invalid_mac"
            else:
                mac_address = dr.format_mac(mac_address)
                connection = (dr.CONNECTION_NETWORK_MAC, mac_address)
                conflicts = self._connection_conflicts(
                    registry, [device_a, device_b], connection
                )
                if conflicts:
                    errors["base"] = "connection_conflict"
                else:
                    data = plan_entry_data(
                        device_a.id,
                        device_b.id,
                        mac_address,
                        set(device_a.connections),
                        set(device_b.connections),
                    )
                    return self.async_create_entry(
                        title=f"Link {self._display_name(device_a)} with {self._display_name(device_b)}",
                        data=data,
                    )

        return self.async_show_form(
            step_id=f"discovery_{reason}",
            data_schema=self._mac_schema(discovered),
            errors=errors,
            description_placeholders=self._discovery_placeholders(discovery),
        )

    @staticmethod
    def _discovery_macs(device_a, device_b, suggested: str | None) -> list[str]:
        """MAC addresses already on either device, with the suggested one first."""
        found = {
            dr.format_mac(value)
            for device in (device_a, device_b)
            for connection_type, value in device.connections
            if connection_type == dr.CONNECTION_NETWORK_MAC
        }
        if suggested:
            found.add(dr.format_mac(suggested))
        return sorted(found)

    @staticmethod
    def _discovery_placeholders(discovery: dict[str, Any]) -> dict[str, str]:
        return {
            "device_a": str(discovery.get("name_a", "")),
            "device_b": str(discovery.get("name_b", "")),
            "integration_a": str(discovery.get("integration_a", "")),
            "integration_b": str(discovery.get("integration_b", "")),
            "area_a": str(discovery.get("area_a", "Unassigned")),
            "area_b": str(discovery.get("area_b", "Unassigned")),
            "score": str(discovery.get("score", "")),
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """Return the options flow for an existing device group."""
        return DeviceLinkerOptionsFlow()


class DeviceLinkerOptionsFlow(config_entries.OptionsFlowWithReload):
    """Edit the anchor, linked devices, and MAC for an existing group."""

    def __init__(self) -> None:
        """Initialize option flow state."""
        self._main_device_id: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Open settings for the current main device and linked devices."""
        settings = get_entry_settings(self.config_entry)
        main_id = settings[CONF_MAIN_DEVICE_ID]
        self._main_device_id = (
            main_id
            if main_id and get_device(dr.async_get(self.hass), main_id) is not None
            else None
        )
        return await self.async_step_configure(user_input)

    async def async_step_configure(self, user_input: dict[str, Any] | None = None):
        """Update the group and safely add or remove registry connections."""
        entry = self.config_entry
        old = get_entry_settings(entry)
        registry = dr.async_get(self.hass)
        main = (
            get_device(registry, self._main_device_id) if self._main_device_id else None
        )
        all_options = DeviceLinkerConfigFlow._device_options(
            self,
            exclude_device_id=self._main_device_id,
            exclude_config_entry_id=main.config_entry_id if main else None,
        )
        available_ids = {option["value"] for option in all_options}
        old_linked_ids = [
            device_id
            for device_id in old[CONF_LINKED_DEVICE_IDS]
            if device_id in available_ids
        ]
        errors: dict[str, str] = {}
        conflicting_devices: list[str] = []
        if user_input is not None:
            # Save the sliders even when the device form still has an error,
            # but do not scan until removed devices are remembered. A scan
            # before that would link them again.
            await async_apply_suggestion_preferences(self.hass, user_input, scan=False)
            for preference_key in (
                CONF_SUGGESTION_THRESHOLD,
                CONF_MATCH_HARDWARE,
                CONF_MATCH_NAMES,
                CONF_AUTO_LINK,
                CONF_AUTO_LINK_THRESHOLD,
            ):
                user_input.pop(preference_key, None)
            linked_ids = list(dict.fromkeys(user_input[CONF_LINKED_DEVICE_IDS]))
            mac_address = DeviceLinkerConfigFlow._submitted_mac(user_input)
            devices = [get_device(registry, device_id) for device_id in linked_ids]
            if any(device is None for device in devices):
                errors["base"] = "device_not_found"
            elif main and any(
                device.id == main.id or device.config_entry_id == main.config_entry_id
                for device in devices
                if device is not None
            ):
                errors["base"] = "same_config_entry"
            elif not mac_address or not MAC_PATTERN.fullmatch(mac_address):
                errors["base"] = "invalid_mac"
            else:
                mac_address = dr.format_mac(mac_address)
                connection = (dr.CONNECTION_NETWORK_MAC, mac_address)
                conflicting_devices = self._conflicts_in_edit(
                    registry, devices, connection, old_linked_ids, old[CONF_MAC_ADDRESS]
                )
                if conflicting_devices:
                    errors["base"] = "connection_conflict"
                else:
                    removed = [
                        device_id
                        for device_id in old[CONF_LINKED_DEVICE_IDS]
                        if device_id not in linked_ids
                    ]
                    others = list(old[CONF_LINKED_DEVICE_IDS])
                    if self._main_device_id:
                        others.append(self._main_device_id)
                    await async_remember_unlinked(
                        self.hass, pairs_touching(removed, others)
                    )
                    added_connections = self._reconcile_registry(
                        registry,
                        old,
                        linked_ids,
                        devices,
                        mac_address,
                    )
                    main_name = (
                        DeviceLinkerConfigFlow._display_name(main)
                        if main
                        else "Manual MAC"
                    )
                    self.hass.config_entries.async_update_entry(
                        entry, title=link_title(main_name, len(linked_ids))
                    )
                    manager = async_get_manager(self.hass)
                    if manager is not None:
                        await manager.async_scan()
                    return self.async_create_entry(
                        title="",
                        data={
                            CONF_MAIN_DEVICE_ID: self._main_device_id,
                            CONF_LINKED_DEVICE_IDS: linked_ids,
                            CONF_MAC_ADDRESS: mac_address,
                            CONF_ADDED_CONNECTIONS: added_connections,
                            CONF_ORIGIN: old[CONF_ORIGIN],
                        },
                    )

        discovered_macs = sorted(
            {
                dr.format_mac(value)
                for connection_type, value in (main.connections if main else set())
                if connection_type == dr.CONNECTION_NETWORK_MAC
            }
        )
        current_mac = old[CONF_MAC_ADDRESS]
        device_options = DeviceLinkerConfigFlow._device_options(
            self,
            exclude_device_id=self._main_device_id,
            exclude_config_entry_id=main.config_entry_id if main else None,
        )
        available_ids = {option["value"] for option in device_options}
        old_linked_ids = [
            device_id for device_id in old_linked_ids if device_id in available_ids
        ]
        schema: dict[vol.Marker, object] = {
            vol.Optional(
                CONF_LINKED_DEVICE_IDS, default=old_linked_ids
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=device_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
        if discovered_macs:
            if current_mac not in discovered_macs:
                discovered_macs.append(current_mac)
                discovered_macs.sort()
            options = [
                selector.SelectOptionDict(value=mac, label=mac)
                for mac in discovered_macs
            ]
            default_mac = current_mac
            schema[vol.Optional(CONF_SELECTED_MAC_ADDRESS, default=default_mac)] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            )
            schema[vol.Optional(CONF_MAC_ADDRESS)] = str
        else:
            schema[vol.Required(CONF_MAC_ADDRESS, default=current_mac)] = str
        settings = await async_load_settings(self.hass)
        schema.update(
            _suggestion_schema(
                settings.threshold,
                settings.match_hardware,
                settings.match_names,
                settings.auto_link,
                settings.auto_link_threshold,
            )
        )
        if user_input is not None and errors:
            manager = async_get_manager(self.hass)
            if manager is not None:
                await manager.async_scan()
        return self.async_show_form(
            step_id="configure",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "main_name": (
                    DeviceLinkerConfigFlow._display_name(main)
                    if main
                    else "Manual entry"
                ),
                "linked_device_names": ", ".join(
                    DeviceLinkerConfigFlow._device_labels(
                        self.hass,
                        [
                            get_device(registry, device_id)
                            for device_id in old_linked_ids
                        ],
                    )
                ),
                "conflicting_devices": ", ".join(conflicting_devices) or "none",
            },
        )

    def _conflicts_in_edit(
        self, registry, devices, connection, old_linked_ids, old_mac
    ) -> list[str]:
        """Find conflicts while allowing this entry's existing assignments."""
        selected_ids = {device.id for device in devices if device is not None}
        if len(selected_ids) != len(devices):
            return ["A selected device is no longer available"]
        conflicts: set[str] = set()
        selected_by_config_entry = {}
        for device in devices:
            if device is None:
                continue
            prior_selected = selected_by_config_entry.get(device.config_entry_id)
            if prior_selected is not None:
                conflicts.update(
                    DeviceLinkerConfigFlow._device_labels(
                        self.hass, [prior_selected, device]
                    )
                )
            selected_by_config_entry[device.config_entry_id] = device
            for other in dr.async_entries_for_config_entry(
                registry, device.config_entry_id
            ):
                if (
                    other.id != device.id
                    and connection in other.connections
                    and not (other.id in old_linked_ids and old_mac == connection[1])
                ):
                    conflicts.update(
                        DeviceLinkerConfigFlow._device_labels(self.hass, [other])
                    )
        return sorted(conflicts, key=str.casefold)

    def _reconcile_registry(
        self, registry, old, linked_ids, devices, mac_address
    ) -> dict[str, bool]:
        """Remove old owned connections and apply the edited group."""
        old_ids = list(old[CONF_LINKED_DEVICE_IDS])
        old_mac = old[CONF_MAC_ADDRESS]
        old_added = old[CONF_ADDED_CONNECTIONS]
        new_connection = (dr.CONNECTION_NETWORK_MAC, mac_address)

        # Stop the old reload listener so an intentional unlink is not restored.
        unsubscribe = self.hass.data.get(DOMAIN, {}).pop(
            self.config_entry.entry_id, None
        )
        if unsubscribe:
            unsubscribe()

        for device_id in old_ids:
            if device_id in linked_ids and old_mac == mac_address:
                continue
            device = get_device(registry, device_id)
            old_connection = (dr.CONNECTION_NETWORK_MAC, old_mac)
            if (
                device is not None
                and old_added.get(device_id, False)
                and old_connection in device.connections
            ):
                registry.async_update_device(
                    device.id,
                    new_connections=device.connections - {old_connection},
                )

        added_connections: dict[str, bool] = {}
        for device_id in linked_ids:
            device = get_device(registry, device_id)
            if device is None:
                continue
            previously_owned = (
                old_added.get(device.id, False)
                if device.id in old_ids and old_mac == mac_address
                else False
            )
            if new_connection in device.connections:
                added_connections[device.id] = previously_owned
                continue
            registry.async_update_device(
                device.id,
                new_connections=device.connections | {new_connection},
            )
            added_connections[device.id] = True
        return added_connections
