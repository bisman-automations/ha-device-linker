"""Suggest devices that should share a MAC connection.

This module does not import Home Assistant. The manager normalizes registry
entries into :class:`DeviceView` and asks :func:`suggest` for the ranked list.

Two tiers:

* **Hardware** (higher confidence). A MAC address or another hardware
  identifier shared by two devices from different config entries. MAC matches
  are ranked first and are allowed across areas, because a UniFi client and
  the Shelly, ESPHome, or TV integration for the same hardware often live in
  different areas. An identifier match (Zigbee IEEE, Bluetooth MAC stored as
  an identifier, or a serial number) is next.
* **Name** (lower confidence). Devices in the *same* area whose names still
  look alike after case, punctuation, the area name, generic words (``light``,
  ``switch``, ``sensor``, ``plug``), and common brand names are removed.
  Similarity is a token-set score from :mod:`difflib` (no extra dependency)
  and has to reach the configured threshold.

Pairs are skipped when they are the same device, share a config entry, are
already linked, or were dismissed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from .const import REASON_IDENTIFIER, REASON_MAC, REASON_NAME

# Connection type strings. Duplicated here so this module stays free of
# Home Assistant imports; they match device_registry.CONNECTION_*.
CONNECTION_NETWORK_MAC = "mac"
CONNECTION_BLUETOOTH = "bluetooth"

_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$")
_PUNCTUATION = re.compile(r"[^\w\s]+", re.UNICODE)
_SEPARATORS = re.compile(r"[\s:.\-]+")

# Words that describe a kind of device rather than which device it is.
# Stripped from both names so "Kitchen ceiling light" and "Kitchen ceiling"
# can meet on "ceiling".
_GENERIC_TOKENS = frozenset(
    {
        "binary",
        "blind",
        "blinds",
        "bulb",
        "button",
        "contact",
        "cover",
        "device",
        "dimmer",
        "door",
        "energy",
        "fan",
        "humidity",
        "lamp",
        "led",
        "light",
        "lights",
        "lock",
        "media",
        "motion",
        "outlet",
        "outlets",
        "player",
        "plug",
        "plugs",
        "power",
        "relay",
        "remote",
        "sensor",
        "sensors",
        "shade",
        "socket",
        "speaker",
        "switch",
        "switches",
        "temperature",
        "tracker",
        "window",
    }
)

# Brand and integration words that show up in one copy of a device and not the
# other ("Shelly Kitchen" vs "Kitchen"). Short ambiguous tokens (lg, mi, tv)
# are left in place so they can still distinguish two devices.
_BRAND_TOKENS = frozenset(
    {
        "aqara",
        "apple",
        "amazon",
        "broadlink",
        "caseta",
        "ecobee",
        "esphome",
        "google",
        "govee",
        "hisense",
        "homekit",
        "hue",
        "ikea",
        "inovelli",
        "kasa",
        "lifx",
        "lutron",
        "meross",
        "nanoleaf",
        "nest",
        "philips",
        "roku",
        "samsung",
        "sengled",
        "shelly",
        "sonoff",
        "sony",
        "switchbot",
        "tapo",
        "tasmota",
        "tradfri",
        "tuya",
        "ubiquiti",
        "unifi",
        "vizio",
        "xiaomi",
        "yeelight",
    }
)

_REASON_RANK = {REASON_MAC: 0, REASON_IDENTIFIER: 1, REASON_NAME: 2}


@dataclass(frozen=True, slots=True)
class DeviceView:
    """One registry device, reduced to what matching needs."""

    device_id: str
    name: str
    area_id: str | None
    area_name: str | None
    config_entry_id: str | None
    integration: str
    connections: tuple[tuple[str, str], ...]
    identifiers: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A ranked recommendation to link two devices."""

    device_id_a: str
    device_id_b: str
    name_a: str
    name_b: str
    integration_a: str
    integration_b: str
    reason: str
    score: float
    area_name_a: str | None
    area_name_b: str | None
    # Normalized MAC to add, when the match produced one. Name and serial
    # matches leave this empty and the confirm step asks for an address.
    mac: str | None

    @property
    def key(self) -> str:
        """Stable id for this unordered pair. Used for dismissal and flows."""
        return pair_key(self.device_id_a, self.device_id_b)

    @property
    def area_mismatch(self) -> bool:
        """True when the devices are not in the same assigned area."""
        if self.area_name_a is None and self.area_name_b is None:
            return False
        return self.area_name_a != self.area_name_b

    def as_attribute(self) -> dict[str, object]:
        """Sensor and diagnostics view. MAC addresses are not included."""
        return {
            "devices": (
                f"{self.name_a} ({self.integration_a}), "
                f"{self.name_b} ({self.integration_b})"
            ),
            "reason": self.reason,
            "score": round(self.score, 2),
            "area_mismatch": self.area_mismatch,
            "areas": [
                self.area_name_a or "Unassigned",
                self.area_name_b or "Unassigned",
            ],
        }


def pair_key(device_id_a: str, device_id_b: str) -> str:
    """Return an order-independent id for two devices."""
    left, right = sorted((device_id_a, device_id_b))
    return f"{left}:{right}"


def suggestion_unique_id(device_id_a: str, device_id_b: str) -> str:
    """Config-flow unique id for a suggestion. Distinct from link unique ids."""
    return f"suggestion:{pair_key(device_id_a, device_id_b)}"


def normalize_mac(value: str) -> str | None:
    """Normalize a MAC the way ``device_registry.format_mac`` does.

    Accepts colon, hyphen, Cisco dotted, and bare 12-hex forms. Returns
    ``aa:bb:cc:dd:ee:ff`` or ``None`` when the text is not a 48-bit MAC.
    ``format_mac`` returns unrecognized input unchanged; this helper returns
    ``None`` so a serial number is not stored as a MAC key.
    """
    text = value.strip()
    if len(text) == 17 and text.count(":") == 5:
        formatted = text.lower()
    elif len(text) == 17 and text.count("-") == 5:
        formatted = _colon_join(text.replace("-", ""))
    elif len(text) == 14 and text.count(".") == 2:
        formatted = _colon_join(text.replace(".", ""))
    elif len(text) == 12 and _HEX_RE.fullmatch(text.lower()):
        formatted = _colon_join(text)
    else:
        return None
    if _MAC_RE.fullmatch(formatted):
        return formatted
    return None


def normalize_name(name: str, area_name: str | None) -> str:
    """Return the distinctive words of a device name.

    Case and punctuation are folded, then the area name, generic device
    words, and brand names are removed. An empty result means the name was
    only an area plus generic words, which is not enough to suggest a link.
    """
    text = _PUNCTUATION.sub(" ", name.casefold().replace("_", " "))
    area_tokens: set[str] = set()
    if area_name:
        area = _PUNCTUATION.sub(" ", area_name.casefold().replace("_", " "))
        area = " ".join(area.split())
        if area:
            text = text.replace(area, " ")
            area_tokens = set(area.split())
    tokens = [
        token
        for token in text.split()
        if token not in _GENERIC_TOKENS
        and token not in _BRAND_TOKENS
        and token not in area_tokens
        and not token.isdigit()
    ]
    return " ".join(tokens)


def name_similarity(name_a: str, name_b: str, area_name: str | None) -> float:
    """Score two names from 0 to 1 after :func:`normalize_name`.

    The score is the best of a plain ratio and a token-set ratio. Token-set
    treats "ceiling tablet" and "tablet ceiling speaker" as a strong match
    because the shared words dominate and the extra word only trims the score.
    """
    left = normalize_name(name_a, area_name)
    right = normalize_name(name_b, area_name)
    if not left or not right:
        return 0.0
    return max(_ratio(left, right), _token_set_ratio(left, right))


def hardware_keys(
    connections: Iterable[tuple[str, str]],
    identifiers: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], str | None]:
    """Index comparable hardware keys to the MAC they imply, if any.

    The key is ``(kind, value)`` where kind is ``mac``, ``eui``, or ``serial``.
    The mapped value is a normalized MAC when the key was a MAC or an EUI-64
    with ``fffe`` in the middle (the usual way a 48-bit MAC is extended to a
    Zigbee/IEEE address). Serial keys map to ``None``.
    """
    found: dict[tuple[str, str], str | None] = {}
    for connection_type, value in connections:
        if connection_type in {CONNECTION_NETWORK_MAC, CONNECTION_BLUETOOTH}:
            _add_hardware_value(found, value)
    for _domain, value in identifiers:
        _add_hardware_value(found, value)
    return found


def suggest(
    devices: Sequence[DeviceView],
    *,
    linked_pairs: set[str],
    dismissed: set[str],
    threshold: float,
    match_hardware: bool = True,
    match_names: bool = True,
) -> list[Suggestion]:
    """Return suggestions, best first.

    Hardware matches outrank name matches. Within a tier, a higher score wins.
    """
    eligible = [device for device in devices if device.config_entry_id]
    by_key: dict[tuple[str, str], list[tuple[DeviceView, str | None]]] = {}
    if match_hardware:
        for device in eligible:
            for key, mac in hardware_keys(
                device.connections, device.identifiers
            ).items():
                by_key.setdefault(key, []).append((device, mac))

    best: dict[str, Suggestion] = {}
    if match_hardware:
        for key, holders in by_key.items():
            kind, _value = key
            reason = REASON_MAC if kind == "mac" else REASON_IDENTIFIER
            for index, (left, left_mac) in enumerate(holders):
                for right, right_mac in holders[index + 1 :]:
                    if not _pair_allowed(left, right, linked_pairs, dismissed):
                        continue
                    if _already_share_connection(left, right):
                        continue
                    mac = left_mac or right_mac
                    _consider(
                        best,
                        _suggestion(left, right, reason=reason, score=1.0, mac=mac),
                    )

    if match_names:
        by_area: dict[str, list[DeviceView]] = {}
        for device in eligible:
            if device.area_id:
                by_area.setdefault(device.area_id, []).append(device)
        for group in by_area.values():
            area_name = group[0].area_name
            for index, left in enumerate(group):
                for right in group[index + 1 :]:
                    if not _pair_allowed(left, right, linked_pairs, dismissed):
                        continue
                    if pair_key(left.device_id, right.device_id) in best:
                        # A hardware match for this pair is already the better tier.
                        continue
                    if _already_share_connection(left, right):
                        continue
                    score = name_similarity(left.name, right.name, area_name)
                    if score < threshold:
                        continue
                    _consider(
                        best,
                        _suggestion(
                            left, right, reason=REASON_NAME, score=score, mac=None
                        ),
                    )

    return sorted(best.values(), key=_sort_key)


def choose_link_ends(
    device_a_id: str,
    device_b_id: str,
    mac: str,
    connections_a: Iterable[tuple[str, str]],
    connections_b: Iterable[tuple[str, str]],
) -> tuple[str | None, list[str]]:
    """Choose the main device and the devices that should receive ``mac``.

    The device that already has the MAC connection is the main device, matching
    the config flow. When neither has it (the address was only an identifier,
    or the user typed it), both devices are linked from a manual main so each
    of them gains the connection and Home Assistant can show the link.
    """
    wanted = (CONNECTION_NETWORK_MAC, mac)
    a_has = wanted in set(connections_a)
    b_has = wanted in set(connections_b)
    if a_has and not b_has:
        return device_a_id, [device_b_id]
    if b_has and not a_has:
        return device_b_id, [device_a_id]
    return None, [device_a_id, device_b_id]


def _colon_join(bare_hex: str) -> str:
    text = bare_hex.lower()
    return ":".join(text[index : index + 2] for index in range(0, 12, 2))


def _add_hardware_value(found: dict[tuple[str, str], str | None], value: str) -> None:
    mac = normalize_mac(value)
    if mac is not None:
        found[("mac", mac)] = mac
        return
    compact = _SEPARATORS.sub("", value).lower()
    if len(compact) == 16 and _HEX_RE.fullmatch(compact):
        found[("eui", compact)] = None
        # EUI-64 formed by inserting fffe into a MAC: 00:11:22:ff:fe:33:44:55.
        if compact[6:10] == "fffe":
            embedded = normalize_mac(compact[:6] + compact[10:])
            if embedded is not None:
                found[("mac", embedded)] = embedded
        return
    serial = _serial_key(value)
    if serial is not None:
        found[("serial", serial)] = None


def _serial_key(value: str) -> str | None:
    """Return a comparable serial, or None when the value is too weak to share.

    Short values and pure words collide across unrelated devices. A serial
    here is at least 8 characters, contains a digit, and is not itself a MAC.
    """
    text = value.strip().casefold()
    if len(text) < 8 or not any(character.isdigit() for character in text):
        return None
    if normalize_mac(text) is not None:
        return None
    compact = _SEPARATORS.sub("", text)
    if len(compact) == 16 and _HEX_RE.fullmatch(compact):
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    if any(character not in allowed for character in text):
        return None
    return text


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _token_set_ratio(left: str, right: str) -> float:
    tokens_left = set(left.split())
    tokens_right = set(right.split())
    if not tokens_left or not tokens_right:
        return 0.0
    shared = " ".join(sorted(tokens_left & tokens_right))
    only_left = " ".join(sorted(tokens_left - tokens_right))
    only_right = " ".join(sorted(tokens_right - tokens_left))
    joined_left = f"{shared} {only_left}".strip()
    joined_right = f"{shared} {only_right}".strip()
    return max(
        _ratio(shared, joined_left),
        _ratio(shared, joined_right),
        _ratio(joined_left, joined_right),
    )


def _pair_allowed(
    left: DeviceView,
    right: DeviceView,
    linked_pairs: set[str],
    dismissed: set[str],
) -> bool:
    if left.device_id == right.device_id:
        return False
    if left.config_entry_id and left.config_entry_id == right.config_entry_id:
        return False
    key = pair_key(left.device_id, right.device_id)
    if (
        key in linked_pairs
        or suggestion_unique_id(left.device_id, right.device_id) in dismissed
    ):
        return False
    if key in dismissed:
        return False
    return True


def _already_share_connection(left: DeviceView, right: DeviceView) -> bool:
    """True when Home Assistant would already show these as linked devices.

    A shared network-MAC or Bluetooth *connection* is already a link. The same
    value stored only as an identifier still needs Device Linker to copy it
    onto a connection.
    """
    left_keys = {
        (connection_type, normalize_mac(value) or value)
        for connection_type, value in left.connections
        if connection_type in {CONNECTION_NETWORK_MAC, CONNECTION_BLUETOOTH}
    }
    right_keys = {
        (connection_type, normalize_mac(value) or value)
        for connection_type, value in right.connections
        if connection_type in {CONNECTION_NETWORK_MAC, CONNECTION_BLUETOOTH}
    }
    return bool(left_keys & right_keys)


def _suggestion(
    left: DeviceView,
    right: DeviceView,
    *,
    reason: str,
    score: float,
    mac: str | None,
) -> Suggestion:
    ordered = tuple(sorted((left, right), key=lambda device: device.device_id))
    first, second = ordered
    return Suggestion(
        device_id_a=first.device_id,
        device_id_b=second.device_id,
        name_a=first.name,
        name_b=second.name,
        integration_a=first.integration,
        integration_b=second.integration,
        reason=reason,
        score=score,
        area_name_a=first.area_name,
        area_name_b=second.area_name,
        mac=mac,
    )


def _consider(best: dict[str, Suggestion], candidate: Suggestion) -> None:
    current = best.get(candidate.key)
    if current is None or _sort_key(candidate) < _sort_key(current):
        best[candidate.key] = candidate


def _sort_key(suggestion: Suggestion) -> tuple[int, float, str, str]:
    return (
        _REASON_RANK[suggestion.reason],
        -suggestion.score,
        suggestion.name_a.casefold(),
        suggestion.name_b.casefold(),
    )
