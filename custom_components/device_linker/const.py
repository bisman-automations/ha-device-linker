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
# Suggestion preferences. Stored once for the whole integration, not on a
# single link, and edited from every entry's options flow.
CONF_SUGGESTION_THRESHOLD = "suggestion_threshold"
CONF_MATCH_HARDWARE = "match_hardware"
CONF_MATCH_NAMES = "match_names"

# 0.8 keeps a shared distinctive word ("ceiling", "tablet") and drops weak
# partial overlaps. Lower it from the options flow to see more name matches.
DEFAULT_SUGGESTION_THRESHOLD = 0.8
SUGGESTION_THRESHOLD_MIN = 0.5
SUGGESTION_THRESHOLD_MAX = 1.0
SUGGESTION_THRESHOLD_STEP = 0.05

# Registry updates arrive in bursts (a UniFi poll rewrites many clients).
# Wait briefly so one scan sees the whole burst.
SUGGESTION_DEBOUNCE_SECONDS = 2.0
# The integrations page lists discovered flows. Cap them so a large network
# does not fill the page; the sensor and diagnostics still list the rest.
MAX_DISCOVERY_FLOWS = 10
MAX_LISTED_SUGGESTIONS = 25

DATA_MANAGER = "manager"
SERVICE_RESCAN = "rescan_suggestions"
STORAGE_KEY = "device_linker.suggestions"
STORAGE_VERSION = 1

REASON_MAC = "mac"
REASON_IDENTIFIER = "identifier"
REASON_NAME = "name"
