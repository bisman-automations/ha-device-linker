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
# Automatic linking. Stored with the suggestion preferences, for the whole
# integration. ``origin`` on a config entry is ``manual`` or ``auto``.
CONF_AUTO_LINK = "auto_link"
CONF_AUTO_LINK_THRESHOLD = "auto_link_threshold"
# Also link fuzzy names automatically when one name is the other plus only
# filler words ("Deck Camera" and "G4 Deck Camera"). Added in 2.0.0.
CONF_AUTO_LINK_SIMILAR = "auto_link_similar"
CONF_ORIGIN = "origin"
ORIGIN_MANUAL = "manual"
ORIGIN_AUTO = "auto"

# 0.8 keeps a shared distinctive word ("ceiling", "tablet") and drops weak
# partial overlaps. Lower it from the options flow to see more name matches.
DEFAULT_SUGGESTION_THRESHOLD = 0.8
# Fuzzy names at or above this score are linked automatically. Exact names
# and hardware matches do not use this value. 0.95 leaves ordinary partial
# overlaps as suggestions.
DEFAULT_AUTO_LINK_THRESHOLD = 0.95
SUGGESTION_THRESHOLD_MIN = 0.5
SUGGESTION_THRESHOLD_MAX = 1.0
SUGGESTION_THRESHOLD_STEP = 0.05
# One anchor can collect several integrations. Past this, further devices
# stay suggestions so a generic name cannot attach the whole registry.
MAX_AUTO_LINK_GROUP = 10

# Registry updates arrive in bursts (a UniFi poll rewrites many clients).
# Wait briefly so one scan sees the whole burst.
SUGGESTION_DEBOUNCE_SECONDS = 2.0
# The integrations page lists discovered flows. Cap them so a large network
# does not fill the page; the sensor and diagnostics still list the rest.
MAX_DISCOVERY_FLOWS = 10
MAX_LISTED_SUGGESTIONS = 25

# 2.0.0 keeps every link as a subentry of one Device Linker entry, the hub.
# The hub's data is ``{"hub": True}``. An old link entry that migration folded
# into the hub keeps ``{"merged_into": <hub entry id>}`` until it is removed.
CONF_HUB = "hub"
CONF_MERGED_INTO = "merged_into"
HUB_UNIQUE_ID = DOMAIN
HUB_TITLE = "Device Linker"
SUBENTRY_LINK = "link"
CONFIG_VERSION = 3
# The links the hub last applied, by subentry id, so a link deleted while
# Device Linker was not running is still undone at the next start.
LINKS_STORAGE_KEY = "device_linker.links"
LINKS_STORAGE_VERSION = 1

DATA_MANAGER = "manager"
SERVICE_RESCAN = "rescan_suggestions"
SERVICE_RESET = "reset_dismissed"
ISSUE_AUTO_LINKED = "auto_linked"
STORAGE_KEY = "device_linker.suggestions"
# Version 1 is still the on-disk shape. 1.3.0 adds keys (``auto_link``,
# ``auto_link_threshold``, ``unlinked``, ``announced``). Missing keys load
# as the defaults below, so a 1.2 store keeps its threshold and dismissals.
STORAGE_VERSION = 1

REASON_MAC = "mac"
REASON_IDENTIFIER = "identifier"
REASON_NAME = "name"
