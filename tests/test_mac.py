"""MAC address validation, without importing the integration package.

``const.py`` has no Home Assistant imports. Load it directly so this test does
not start Home Assistant.
"""

import importlib.util
from pathlib import Path

import pytest

_CONST = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "device_linker"
    / "const.py"
)
_spec = importlib.util.spec_from_file_location("device_linker_const", _CONST)
assert _spec is not None and _spec.loader is not None
_const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_const)
MAC_PATTERN = _const.MAC_PATTERN


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
