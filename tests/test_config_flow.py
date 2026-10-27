"""Tests for MAC address validation."""

import pytest

from custom_components.device_linker.config_flow import MAC_PATTERN


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
