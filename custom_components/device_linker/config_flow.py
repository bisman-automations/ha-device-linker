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
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_SELECTED_MAC_ADDRESS,
    DOMAIN,
    MAC_PATTERN,
)
from .registry import get_device, get_entry_settings

MANUAL_ENTRY = "__manual_entry__"


class DeviceLinkerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set a main device and link multiple related devices to its MAC."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._main_device_id: str | None = None
        self._linked_device_ids: list[str] = []

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
        if user_input is not None:
            mac_address = self._submitted_mac(user_input)
            if not mac_address or not MAC_PATTERN.fullmatch(mac_address):
                errors["base"] = "invalid_mac"
            else:
                mac_address = dr.format_mac(mac_address)
                connection = (dr.CONNECTION_NETWORK_MAC, mac_address)
                if self._has_connection_conflict(registry, linked_devices, connection):
                    errors["base"] = "connection_conflict"
                else:
                    await self.async_set_unique_id(
                        f"{self._main_device_id or 'manual'}_{mac_address}"
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
                        },
                    )

        return self.async_show_form(
            step_id="confirm",
            data_schema=self._mac_schema(discovered_macs),
            errors=errors,
            description_placeholders={
                "main_name": self._display_name(main) if main else "Manual entry",
                "device_count": str(len(linked_devices)),
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

    def _has_connection_conflict(self, registry, devices, connection) -> bool:
        """Check conflicts within each integration and across chosen devices."""
        seen_config_entries: set[str] = set()
        for device in devices:
            if device is None:
                return True
            if device.config_entry_id in seen_config_entries:
                return True
            seen_config_entries.add(device.config_entry_id)
            if any(
                other.id != device.id and connection in other.connections
                for other in dr.async_entries_for_config_entry(
                    registry, device.config_entry_id
                )
            ):
                return True
        return False

    @staticmethod
    def _entry_title(main, linked_devices) -> str:
        """Build a readable title for the integration entry."""
        main_name = main.name_by_user or main.name or main.id if main else "Manual MAC"
        count = len(linked_devices)
        return f"Link {main_name} to {count} device{'s' if count != 1 else ''}"

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
                    label=self._device_label(device, config_entry.domain, include_mac),
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
        if user_input is not None:
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
                if self._conflict_in_edit(
                    registry, devices, connection, old_linked_ids, old[CONF_MAC_ADDRESS]
                ):
                    errors["base"] = "connection_conflict"
                else:
                    added_connections = self._reconcile_registry(
                        registry,
                        old,
                        linked_ids,
                        devices,
                        mac_address,
                    )
                    return self.async_create_entry(
                        title="",
                        data={
                            CONF_MAIN_DEVICE_ID: self._main_device_id,
                            CONF_LINKED_DEVICE_IDS: linked_ids,
                            CONF_MAC_ADDRESS: mac_address,
                            CONF_ADDED_CONNECTIONS: added_connections,
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
        return self.async_show_form(
            step_id="configure",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "main_name": self._display_name(main) if main else "Manual entry"
            },
        )

    def _conflict_in_edit(
        self, registry, devices, connection, old_linked_ids, old_mac
    ) -> bool:
        """Check conflicts while allowing this entry's existing assignments."""
        selected_ids = {device.id for device in devices if device is not None}
        if len(selected_ids) != len(devices):
            return True
        config_entry_ids: set[str] = set()
        for device in devices:
            if device is None or device.config_entry_id in config_entry_ids:
                return True
            config_entry_ids.add(device.config_entry_id)
            if any(
                other.id != device.id
                and connection in other.connections
                and not (other.id in old_linked_ids and old_mac == connection[1])
                for other in dr.async_entries_for_config_entry(
                    registry, device.config_entry_id
                )
            ):
                return True
        return False

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
