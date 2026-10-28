"""Constants for Device Linker."""

import re

MAC_PATTERN = re.compile(
    r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
    r"|^(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}$"
)

DOMAIN = "device_linker"
# Current group-based configuration.
CONF_MAIN_DEVICE_ID = "main_device_id"
CONF_LINKED_DEVICE_IDS = "linked_device_ids"
CONF_ADDED_CONNECTIONS = "added_connections"
# Legacy keys retained so existing 1.0.0 entries remain usable and editable.
CONF_DEVICE_ID = "device_id"
CONF_PEER_DEVICE_ID = "peer_device_id"
CONF_MAC_ADDRESS = "mac_address"
CONF_SELECTED_MAC_ADDRESS = "selected_mac_address"
CONF_ADDED_CONNECTION = "added_connection"
CONF_TARGET_DEVICES = "target_devices"
