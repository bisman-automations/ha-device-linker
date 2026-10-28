"""Tests for the Device Linker config flow."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.const import (
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_SELECTED_MAC_ADDRESS,
    DOMAIN,
    MAC_PATTERN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import device_registry as dr


async def test_three_step_flow_links_multiple_devices_to_main_mac(
    hass, enable_custom_integrations
) -> None:
    """Select a main device, several targets, and confirm its registered MAC."""
    main_config = MockConfigEntry(domain="main_integration")
    main_config.add_to_hass(hass)
    target_config = MockConfigEntry(domain="target_integration")
    target_config.add_to_hass(hass)
    second_target_config = MockConfigEntry(domain="second_target_integration")
    second_target_config.add_to_hass(hass)

    registry = dr.async_get(hass)
    main = registry.async_get_or_create(
        config_entry_id=main_config.entry_id,
        identifiers={("main_integration", "main")},
        connections={(dr.CONNECTION_NETWORK_MAC, "AA:BB:CC:DD:EE:FF")},
        name="Main device",
    )
    target = registry.async_get_or_create(
        config_entry_id=target_config.entry_id,
        identifiers={("target_integration", "target")},
        name="Target device",
    )
    second_target = registry.async_get_or_create(
        config_entry_id=second_target_config.entry_id,
        identifiers={("second_target_integration", "target")},
        name="Second target device",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: main.id}
    )
    assert result["step_id"] == "devices"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_LINKED_DEVICE_IDS: [target.id, second_target.id]},
    )
    assert result["step_id"] == "confirm"
    assert result["data_schema"]({})[CONF_SELECTED_MAC_ADDRESS] == "aa:bb:cc:dd:ee:ff"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_SELECTED_MAC_ADDRESS: "aa:bb:cc:dd:ee:ff"},
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_MAIN_DEVICE_ID] == main.id
    assert result["data"][CONF_LINKED_DEVICE_IDS] == [target.id, second_target.id]
    assert result["data"][CONF_MAC_ADDRESS] == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    "mac_address",
    ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "00:11:22:33:44:55"],
)
def test_valid_mac_addresses(mac_address: str) -> None:
    """Accept six hexadecimal octets with a consistent separator."""
    assert MAC_PATTERN.fullmatch(mac_address)


@pytest.mark.parametrize(
    "mac_address",
    ["", "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:FG", "AABBCCDDEEFF", "AA-BB:CC-DD:EE-FF"],
)
def test_invalid_mac_addresses(mac_address: str) -> None:
    """Reject incomplete, invalid, or inconsistently separated addresses."""
    assert MAC_PATTERN.fullmatch(mac_address) is None
