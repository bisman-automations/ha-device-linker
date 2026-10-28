"""Reading settings saved by older Device Linker versions."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.const import (
    CONF_ADDED_CONNECTION,
    CONF_ADDED_CONNECTIONS,
    CONF_DEVICE_ID,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_PEER_DEVICE_ID,
    CONF_TARGET_DEVICES,
    DOMAIN,
)
from custom_components.device_linker.registry import get_entry_settings


def test_legacy_pair_reads_the_peer_as_the_main_device(hass) -> None:
    """1.0.0 stored the MAC's device as the peer and the linked device separately."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_ID: "target-device",
            CONF_PEER_DEVICE_ID: "peer-device",
            CONF_MAC_ADDRESS: "aa:bb:cc:dd:ee:ff",
            CONF_ADDED_CONNECTION: True,
        },
    )

    settings = get_entry_settings(entry)

    assert settings[CONF_MAIN_DEVICE_ID] == "peer-device"
    assert settings[CONF_LINKED_DEVICE_IDS] == ["target-device"]
    assert settings[CONF_MAC_ADDRESS] == "aa:bb:cc:dd:ee:ff"
    assert settings[CONF_ADDED_CONNECTIONS] == {"target-device": True}
    assert settings["origin"] == "manual"


def test_legacy_multiselect_does_not_claim_connections_it_did_not_track(hass) -> None:
    """The earlier multi-select build did not record which MACs it added."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_ID: "main-device",
            CONF_TARGET_DEVICES: ["first", "second"],
            CONF_MAC_ADDRESS: "aa:bb:cc:dd:ee:ff",
        },
    )

    settings = get_entry_settings(entry)

    assert settings[CONF_MAIN_DEVICE_ID] == "main-device"
    assert settings[CONF_LINKED_DEVICE_IDS] == ["first", "second"]
    assert settings[CONF_ADDED_CONNECTIONS] == {"first": False, "second": False}
