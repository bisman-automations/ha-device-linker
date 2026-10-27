"""Config flow for Device Linker."""

from __future__ import annotations

import re

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr, selector

from .const import (
    CONF_ADDED_CONNECTION,
    CONF_DEVICE_ID,
    CONF_MAC_ADDRESS,
    CONF_PEER_DEVICE_ID,
    CONF_SELECTED_MAC_ADDRESS,
    DOMAIN,
)

MAC_PATTERN = re.compile(
    r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
    r"|^(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}$"
)


class DeviceLinkerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Link a target device to a peer device using a shared MAC address."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state."""
        self._target_device_id: str | None = None
        self._peer_device_id: str | None = None

    async def async_step_user(self, user_input=None):
        """Choose the device to link and the peer that may know its MAC."""
        errors = {}
        registry = dr.async_get(self.hass)

        if user_input is not None:
            target_id = user_input[CONF_DEVICE_ID]
            peer_id = user_input[CONF_PEER_DEVICE_ID]
            target = registry.async_get(target_id, include_composite_devices=False)
            peer = registry.async_get(peer_id, include_composite_devices=False)

            if target is None or peer is None:
                errors["base"] = "device_not_found"
            elif target.id == peer.id:
                errors["base"] = "same_device"
            elif target.config_entry_id == peer.config_entry_id:
                errors["base"] = "same_config_entry"
            elif (
                target.identifiers & peer.identifiers
                or target.connections & peer.connections
            ):
                return self.async_abort(reason="already_linked")
            else:
                self._target_device_id = target_id
                self._peer_device_id = peer_id
                return await self.async_step_mac()

        device_options = self._device_options()
        if not self._has_devices_from_different_entries():
            return self.async_abort(reason="not_enough_devices")

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=device_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_PEER_DEVICE_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=device_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_mac(self, user_input=None):
        """Use a MAC from the selected peer, or accept a manually entered one."""
        registry = dr.async_get(self.hass)
        target = (
            registry.async_get(
                self._target_device_id, include_composite_devices=False
            )
            if self._target_device_id
            else None
        )
        peer = (
            registry.async_get(self._peer_device_id, include_composite_devices=False)
            if self._peer_device_id
            else None
        )
        if target is None or peer is None:
            return self.async_abort(reason="device_not_found")
        if target.config_entry_id == peer.config_entry_id:
            return self.async_abort(reason="same_config_entry")
        if (
            target.identifiers & peer.identifiers
            or target.connections & peer.connections
        ):
            return self.async_abort(reason="already_linked")

        discovered_macs = sorted(
            {
                dr.format_mac(value)
                for connection_type, value in peer.connections
                if connection_type == dr.CONNECTION_NETWORK_MAC
            }
        )
        errors = {}

        if user_input is not None:
            custom_mac = user_input.get(CONF_MAC_ADDRESS, "").strip()
            selected_mac = user_input.get(CONF_SELECTED_MAC_ADDRESS, "")
            raw_mac = custom_mac or selected_mac

            if not raw_mac or not MAC_PATTERN.fullmatch(raw_mac):
                errors["base"] = "invalid_mac"
            else:
                mac_address = dr.format_mac(raw_mac)
                connection = (dr.CONNECTION_NETWORK_MAC, mac_address)
                conflict = any(
                    other.id != target.id
                    and other.config_entry_id == target.config_entry_id
                    and connection in other.connections
                    for other in registry.devices.values()
                )
                if conflict:
                    errors["base"] = "connection_conflict"
                else:
                    await self.async_set_unique_id(f"{target.id}_{mac_address}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Link {target.name_by_user or target.name or target.id}",
                        data={
                            CONF_DEVICE_ID: target.id,
                            CONF_PEER_DEVICE_ID: peer.id,
                            CONF_MAC_ADDRESS: mac_address,
                            CONF_ADDED_CONNECTION: connection not in target.connections,
                        },
                    )

        schema: dict[vol.Marker, object] = {}
        if discovered_macs:
            options = [
                selector.SelectOptionDict(value=mac, label=mac)
                for mac in discovered_macs
            ]
            if len(options) == 1:
                discovered_field = vol.Optional(
                    CONF_SELECTED_MAC_ADDRESS, default=discovered_macs[0]
                )
            else:
                discovered_field = vol.Optional(CONF_SELECTED_MAC_ADDRESS)
            schema[discovered_field] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema[vol.Optional(CONF_MAC_ADDRESS)] = str
        else:
            schema[vol.Required(CONF_MAC_ADDRESS)] = str

        return self.async_show_form(
            step_id="mac",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "target_name": target.name_by_user or target.name or target.id,
                "peer_name": peer.name_by_user or peer.name or peer.id,
            },
        )

    def _has_devices_from_different_entries(self) -> bool:
        """Check that at least two selectable config entries have devices."""
        registry = dr.async_get(self.hass)
        entry_ids = {
            device.config_entry_id
            for device in registry.devices.values()
            if device.config_entry_id
            and self.hass.config_entries.async_get_entry(device.config_entry_id)
            is not None
        }
        return len(entry_ids) > 1

    def _device_options(self) -> list[selector.SelectOptionDict]:
        """Return selectable main devices with their integration name."""
        registry = dr.async_get(self.hass)
        options = []
        for device in registry.devices.values():
            if not device.config_entry_id:
                continue
            config_entry = self.hass.config_entries.async_get_entry(
                device.config_entry_id
            )
            if config_entry is None:
                continue
            display_name = (
                device.name_by_user or device.name or f"Unnamed {device.id[:8]}"
            )
            options.append(
                selector.SelectOptionDict(
                    value=device.id,
                    label=f"{display_name} ({config_entry.domain})",
                )
            )
        return sorted(options, key=lambda option: option["label"].casefold())
