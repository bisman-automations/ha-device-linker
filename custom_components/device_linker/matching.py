"""Suggest devices that should share a MAC connection.

This module does not import Home Assistant. The manager normalizes registry
entries into :class:`DeviceView` and asks :func:`suggest` for the ranked list.
:func:`plan_auto_links` turns the strong matches into one group per anchor.

Two tiers:

* **Hardware** (higher confidence). A MAC address or another hardware
  identifier shared by two devices from different config entries. MAC matches
  are ranked first and are allowed across areas, because a UniFi client and
  the Shelly, ESPHome, or TV integration for the same hardware often live in
  different areas. An identifier match (Zigbee IEEE, Bluetooth MAC stored as
  an identifier, or a serial number) is next.
* **Name** (lower confidence). Similar names, after case, punctuation, the
  area name, generic words (``light``, ``switch``, ``sensor``, ``plug``), and
  common brand names are removed. Area names are removed as whole words, so
  an area called Room does not change "bedroom". A missing area is compatible
  with every area: UniFi clients are often unassigned, and requiring both
  devices to share an area dropped those pairs. Two assigned areas still have
  to match. An exact folded name always scores 1, even when the
  distinctive-word pass would leave nothing. Fuzzy similarity is a token-set
  score from :mod:`difflib` (no extra dependency) and has to reach the
  configured threshold.

  Automatic linking is stricter than suggesting. A fuzzy name is linked only
  when the devices share at least two distinctive words, they are in the same
  area, and the score reaches the automatic threshold. One shared word
  ("North window" and "North porch") stays a suggestion. When either device
  has no area, only a multi-word exact name is linked automatically, so a
  bare "TV" is not attached to every TV. A MAC this integration copied onto
  a device is ignored when choosing the anchor. Fuzzy comparisons skip pairs
  where neither device has a network MAC, except two devices in the same
  area, which are still suggested.

Pairs are skipped when they are the same device, share a config entry, are
already linked, or were dismissed or unlinked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from .const import MAX_AUTO_LINK_GROUP, REASON_IDENTIFIER, REASON_MAC, REASON_NAME

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
        "sonos",
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

# Extra words that only say what kind of device it is, for
# :func:`names_contained`. Kept apart from _GENERIC_TOKENS so name scores do
# not change.
_FILLER_TOKENS = frozenset({"camera", "cam", "client", "wifi", "ethernet"})
# Words that say how a device is connected, not what it is. Integrations add
# them to tell their copies apart ("Air Purifier (BLE)", "Air Purifier
# (Cloud)"), so they are dropped before names are compared: two names that
# share only "BLE" are not the same device.
_CONNECTION_TOKENS = frozenset(
    {
        "ble",
        "bluetooth",
        "cloud",
        "ethernet",
        "lan",
        "matter",
        "thread",
        "wifi",
        "zigbee",
        "zwave",
    }
)
# Short forms that mean the same word in :func:`names_contained`.
_SHORT_FORMS = {"cam": "camera", "temp": "temperature", "tv": "television"}

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
    # Normalized MAC to add, when the match produced one. Name matches set
    # this only when exactly one device has exactly one network MAC, so that
    # device can be the anchor. Otherwise the confirm step asks for an address.
    mac: str | None
    # Device that already has the network MAC. None when the address is
    # ambiguous or neither device has it.
    anchor_device_id: str | None = None
    # True when the folded names are equal. Fuzzy matches leave this false
    # even when the score is 1 (one name's tokens are a subset of the other).
    exact: bool = False
    # Distinctive words shared by the two names after the area, brands, and
    # generic words are removed. Automatic linking of a fuzzy name needs two.
    shared_tokens: int = 0
    config_entry_id_a: str | None = None
    config_entry_id_b: str | None = None
    # Set when one device is already in a link: confirming adds the other
    # device (``join_device_id``) to that link instead of making a new one.
    join_link_id: str | None = None
    join_link_title: str | None = None
    join_device_id: str | None = None

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
            "exact": self.exact,
            "shared_tokens": self.shared_tokens,
            "area_mismatch": self.area_mismatch,
            "areas": [
                self.area_name_a or "Unassigned",
                self.area_name_b or "Unassigned",
            ],
            "joins_link": self.join_link_title,
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
    if not isinstance(value, str):
        return None
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


def fold_name(name: str) -> str:
    """Return a name folded for an exact comparison.

    Case and punctuation are folded and whitespace is collapsed. The area,
    generic words, and brand names stay in the string, so two devices that
    really have the same name still compare equal after those words are
    removed from the fuzzy score.
    """
    text = _PUNCTUATION.sub(" ", name.casefold().replace("_", " "))
    return " ".join(text.split())


def area_tokens(area_name: str | None) -> set[str]:
    """Words of an area name. Matching ignores these as whole tokens.

    "Room" removes the token ``room``. It does not rewrite ``bedroom`` or
    ``bathroom``, which a substring replace would damage.
    """
    if not area_name:
        return set()
    area = _PUNCTUATION.sub(" ", area_name.casefold().replace("_", " "))
    return {token for token in area.split() if token}


def normalize_name(name: str, area_name: str | None) -> str:
    """Return the distinctive words of a device name.

    Case and punctuation are folded, then area words, generic device words,
    brand names, and connection words (BLE, Cloud, Matter) are removed as
    whole tokens. An empty result means the
    name was only an area plus generic words. That is not enough for a
    *fuzzy* match. An exact :func:`fold_name` match still qualifies.
    """
    text = _PUNCTUATION.sub(" ", name.casefold().replace("_", " "))
    ignored = area_tokens(area_name)
    tokens = [
        token
        for token in text.split()
        if token not in _GENERIC_TOKENS
        and token not in _BRAND_TOKENS
        and token not in _CONNECTION_TOKENS
        and token not in ignored
        and not token.isdigit()
    ]
    return " ".join(tokens)


def shared_distinctive_tokens(name_a: str, name_b: str, area_name: str | None) -> int:
    """How many distinctive words the two names have in common."""
    left = set(normalize_name(name_a, area_name).split())
    right = set(normalize_name(name_b, area_name).split())
    if not left or not right:
        return 0
    return len(left & right)


def names_contained(name_a: str, name_b: str, area_name: str | None) -> bool:
    """Return whether one name is the other plus only filler words.

    Every word of the shorter name (area words removed) must appear in the
    longer one; a few common short forms count as the full word ("cam" and
    "camera"). The words left over must be filler: generic device words
    ("light", "camera"), numbers, or model codes that mix letters and digits
    ("g4"). "Deck Camera" and "G4 Deck Camera" qualify.
    "Garage Door" and "Garage Camera" do not, and neither do "Living Room TV"
    and "Living Room Apple TV": a leftover brand or place word can mean a
    different device.
    """
    ignored = area_tokens(area_name)
    left = [token for token in fold_name(name_a).split() if token not in ignored]
    right = [token for token in fold_name(name_b).split() if token not in ignored]
    short, long = sorted((left, right), key=len)
    if not short:
        return False
    remaining = list(long)
    for token in short:
        found = next(
            (other for other in remaining if _same_word(token, other)),
            None,
        )
        if found is None:
            return False
        remaining.remove(found)
    return all(_filler_word(token) for token in remaining)


def _same_word(left: str, right: str) -> bool:
    return _SHORT_FORMS.get(left, left) == _SHORT_FORMS.get(right, right)


def _filler_word(token: str) -> bool:
    if token in _GENERIC_TOKENS or token in _FILLER_TOKENS or token.isdigit():
        return True
    return any(char.isdigit() for char in token) and any(
        char.isalpha() for char in token
    )


def name_similarity(name_a: str, name_b: str, area_name: str | None) -> float:
    """Score two names from 0 to 1.

    Equal folded names score 1. Otherwise the score is the best of a plain
    ratio and a token-set ratio of :func:`normalize_name`. Token-set treats
    "ceiling tablet" and "tablet ceiling speaker" as a strong match because
    the shared words dominate and the extra word only trims the score. A
    folded name that survives as nothing on either side scores 0 unless the
    folded names were equal.
    """
    score, _exact = _name_score(name_a, name_b, area_name)
    return score


def _name_score(name_a: str, name_b: str, area_name: str | None) -> tuple[float, bool]:
    """Return ``(score, exact)`` for two device names."""
    folded_a = fold_name(name_a)
    folded_b = fold_name(name_b)
    if folded_a and folded_a == folded_b:
        return 1.0, True
    # Two different brands are two different devices: "Apple TV" and
    # "TV (Google Cast)". One name with a brand and one without can still match.
    brands_a = set(folded_a.split()) & _BRAND_TOKENS
    brands_b = set(folded_b.split()) & _BRAND_TOKENS
    if brands_a and brands_b and not brands_a & brands_b:
        return 0.0, False
    left = normalize_name(name_a, area_name)
    right = normalize_name(name_b, area_name)
    if not left or not right:
        return 0.0, False
    return max(_ratio(left, right), _token_set_ratio(left, right)), False


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
    copied_macs: set[tuple[str, str]] | None = None,
    preferred_integrations: Sequence[str] = (),
) -> list[Suggestion]:
    """Return suggestions, best first.

    Hardware matches outrank name matches. Within a tier, a higher score wins.

    ``copied_macs`` is the set of ``(device id, normalized mac)`` pairs that
    Device Linker itself added. Those connections still count as a link the
    devices already share, but they are not an anchor for a new match.
    """
    copied = copied_macs or set()
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
                        _suggestion(
                            left,
                            right,
                            reason=reason,
                            score=1.0,
                            mac=mac,
                            anchor_device_id=_hardware_anchor(left, right, mac, copied),
                        ),
                    )

    if match_names:
        _match_names(
            eligible,
            best,
            linked_pairs=linked_pairs,
            dismissed=dismissed,
            threshold=threshold,
            copied_macs=copied,
            preferred=tuple(preferred_integrations),
        )

    return sorted(best.values(), key=_sort_key)


def _match_names(
    devices: Sequence[DeviceView],
    best: dict[str, Suggestion],
    *,
    linked_pairs: set[str],
    dismissed: set[str],
    threshold: float,
    copied_macs: set[tuple[str, str]],
    preferred: tuple[str, ...] = (),
) -> None:
    """Add name suggestions.

    Exact folded names are grouped first, which is the UniFi case: the client
    and the television integration share a name, and the client often has no
    area. Fuzzy pairs are limited to devices that share a distinctive token
    so a large registry is not a full pairwise comparison. A fuzzy pair where
    neither device has a network MAC is compared only when both are in the
    same area. That keeps "ceiling light" and "ceiling" in one room as a
    suggestion, and skips the cross product of every unaddressed device.
    """
    by_fold: dict[str, list[DeviceView]] = defaultdict(list)
    for device in devices:
        folded = fold_name(device.name)
        if folded:
            by_fold[folded].append(device)
    for group in by_fold.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                _consider_name(
                    best,
                    left,
                    right,
                    linked_pairs=linked_pairs,
                    dismissed=dismissed,
                    threshold=threshold,
                    known_exact=True,
                    copied_macs=copied_macs,
                    preferred=preferred,
                )

    has_mac = {
        device.device_id for device in devices if _network_macs(device.connections)
    }
    token_index: dict[str, list[DeviceView]] = defaultdict(list)
    for device in devices:
        # Index the words before the area is removed so a device with no area
        # still meets one whose area name is part of the title.
        for token in normalize_name(device.name, None).split():
            token_index[token].append(device)
    seen: set[str] = set()
    for holders in token_index.values():
        if len(holders) < 2:
            continue
        for index, left in enumerate(holders):
            for right in holders[index + 1 :]:
                key = pair_key(left.device_id, right.device_id)
                if key in seen or key in best:
                    continue
                seen.add(key)
                # Neither side has an address to copy. Still suggest a pair
                # that shares an area; skip every other combination.
                if left.device_id not in has_mac and right.device_id not in has_mac:
                    if not left.area_id or left.area_id != right.area_id:
                        continue
                _consider_name(
                    best,
                    left,
                    right,
                    linked_pairs=linked_pairs,
                    dismissed=dismissed,
                    threshold=threshold,
                    known_exact=False,
                    copied_macs=copied_macs,
                    preferred=preferred,
                )


def _consider_name(
    best: dict[str, Suggestion],
    left: DeviceView,
    right: DeviceView,
    *,
    linked_pairs: set[str],
    dismissed: set[str],
    threshold: float,
    known_exact: bool,
    copied_macs: set[tuple[str, str]],
    preferred: tuple[str, ...] = (),
) -> None:
    """Score one area-compatible name pair and keep it when it qualifies."""
    if not _areas_compatible(left, right):
        return
    if not _pair_allowed(left, right, linked_pairs, dismissed):
        return
    if pair_key(left.device_id, right.device_id) in best:
        return
    if _already_share_connection(left, right):
        return
    area_name = _shared_area_name(left, right)
    if known_exact:
        score, exact = 1.0, True
    else:
        score, exact = _name_score(left.name, right.name, area_name)
        if score < threshold:
            return
    anchor_id, mac = _name_anchor(left, right, copied_macs, preferred)
    _consider(
        best,
        _suggestion(
            left,
            right,
            reason=REASON_NAME,
            score=score,
            mac=mac,
            anchor_device_id=anchor_id,
            exact=exact,
            shared_tokens=shared_distinctive_tokens(left.name, right.name, area_name),
        ),
    )


@dataclass(frozen=True, slots=True)
class AutoLinkPlan:
    """Several devices that should share one anchor's MAC on one entry."""

    anchor_device_id: str
    anchor_name: str
    anchor_integration: str
    anchor_config_entry_id: str | None
    mac: str
    linked_device_ids: tuple[str, ...]
    linked_names: tuple[str, ...]
    linked_integrations: tuple[str, ...]
    reason: str
    score: float
    exact: bool


def plan_auto_links(
    suggestions: Sequence[Suggestion],
    *,
    enabled: bool,
    threshold: float,
    similar_names: bool = False,
) -> list[AutoLinkPlan]:
    """Group matches that should be linked without asking.

    Hardware matches are included whenever ``enabled`` is set and the anchor
    MAC is unambiguous. An exact name is included too, except a one-word
    exact name when either device has no area (a bare "TV"). A fuzzy name is
    included when the devices are both assigned to the same area, they share
    at least two distinctive words, and the score reaches ``threshold``.
    With ``similar_names``, a fuzzy name is also included when one name is
    the other plus only filler words (:func:`names_contained`), the score
    reaches ``threshold``, and the areas are compatible (a missing area is
    compatible). Each plan is one anchor device plus the MAC already on it. A device is
    attached to at most one plan, and at most :data:`MAX_AUTO_LINK_GROUP`
    devices, so the rest stay suggestions.
    """
    if not enabled:
        return []
    buckets: dict[tuple[str, str], list[Suggestion]] = defaultdict(list)
    for item in suggestions:
        if (
            item.anchor_device_id
            and item.mac
            and _auto_eligible(item, threshold, similar_names=similar_names)
        ):
            buckets[(item.anchor_device_id, item.mac)].append(item)

    claimed: set[str] = set()
    plans: list[AutoLinkPlan] = []
    for anchor_id, mac in sorted(buckets, key=lambda key: _bucket_rank(buckets[key])):
        items = sorted(buckets[(anchor_id, mac)], key=_sort_key)
        anchor_name = ""
        anchor_integration = ""
        anchor_entry: str | None = None
        linked_ids: list[str] = []
        linked_names: list[str] = []
        linked_integrations: list[str] = []
        used_entries: set[str | None] = set()
        best_item = items[0]
        for item in items:
            other = _linked_end(item, anchor_id)
            if other is None:
                continue
            other_id, other_name, other_integration, other_entry = other
            if not anchor_name:
                anchor_name, anchor_integration, anchor_entry = _anchor_end(
                    item, anchor_id
                )
                if anchor_entry is not None:
                    used_entries.add(anchor_entry)
            if other_id in claimed or other_id == anchor_id:
                continue
            if other_entry is None or other_entry in used_entries:
                continue
            if len(linked_ids) >= MAX_AUTO_LINK_GROUP:
                continue
            used_entries.add(other_entry)
            claimed.add(other_id)
            linked_ids.append(other_id)
            linked_names.append(other_name)
            linked_integrations.append(other_integration)
        if not linked_ids:
            continue
        plans.append(
            AutoLinkPlan(
                anchor_device_id=anchor_id,
                anchor_name=anchor_name,
                anchor_integration=anchor_integration,
                anchor_config_entry_id=anchor_entry,
                mac=mac,
                linked_device_ids=tuple(linked_ids),
                linked_names=tuple(linked_names),
                linked_integrations=tuple(linked_integrations),
                reason=best_item.reason,
                score=best_item.score,
                exact=any(
                    item.exact for item in items if _links_device(item, linked_ids)
                ),
            )
        )
    return plans


def pairs_among(main_device_id: str | None, device_ids: Sequence[str]) -> set[str]:
    """Every unordered pair inside a link entry."""
    ids = [device_id for device_id in device_ids if device_id]
    pairs: set[str] = set()
    if main_device_id:
        for device_id in ids:
            if device_id != main_device_id:
                pairs.add(pair_key(main_device_id, device_id))
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            pairs.add(pair_key(left, right))
    return pairs


def pairs_touching(removed_ids: Sequence[str], other_ids: Sequence[str]) -> set[str]:
    """Pairs between devices that were removed and the devices they left."""
    pairs: set[str] = set()
    for removed in removed_ids:
        for other in other_ids:
            if other and other != removed:
                pairs.add(pair_key(removed, other))
    return pairs


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
    a_has = mac in _network_macs(connections_a)
    b_has = mac in _network_macs(connections_b)
    if a_has and not b_has:
        return device_a_id, [device_b_id]
    if b_has and not a_has:
        return device_b_id, [device_a_id]
    return None, [device_a_id, device_b_id]


def _areas_compatible(left: DeviceView, right: DeviceView) -> bool:
    """Return whether these devices may be matched on name.

    A missing area matches any area, including another missing area. UniFi
    often leaves the client unassigned while the other integration files the
    same device in a room. Two different assigned areas do not match.
    """
    if left.area_id is None or right.area_id is None:
        return True
    return left.area_id == right.area_id


def _shared_area_name(left: DeviceView, right: DeviceView) -> str | None:
    """Area name to strip, when exactly one area or one shared area is known."""
    if left.area_id and right.area_id and left.area_id == right.area_id:
        return left.area_name or right.area_name
    if left.area_id and not right.area_id:
        return left.area_name
    if right.area_id and not left.area_id:
        return right.area_name
    return None


def _hardware_anchor(
    left: DeviceView,
    right: DeviceView,
    mac: str | None,
    copied_macs: set[tuple[str, str]],
) -> str | None:
    """Return the device that already had ``mac``, ignoring copied addresses.

    A connection Device Linker added is recorded in ``copied_macs`` and does
    not make that device the anchor of a later match.
    """
    if not mac:
        return None
    left_has = mac in _native_network_macs(left, copied_macs)
    right_has = mac in _native_network_macs(right, copied_macs)
    if left_has and not right_has:
        return left.device_id
    if right_has and not left_has:
        return right.device_id
    return None


def _name_anchor(
    left: DeviceView,
    right: DeviceView,
    copied_macs: set[tuple[str, str]],
    preferred: Sequence[str] = (),
) -> tuple[str | None, str | None]:
    """Return ``(device id, mac)`` for the device whose address to share.

    Exactly one side with one network MAC is the anchor. When both have one,
    the device from the integration earlier in ``preferred`` (the "Preferred
    MAC address sources" setting, such as UniFi) is the anchor. MACs this
    integration copied on are ignored, so the device that originally had the
    address stays the anchor. Anything else is left for the user to confirm.
    """
    left_macs = _native_network_macs(left, copied_macs)
    right_macs = _native_network_macs(right, copied_macs)
    if len(left_macs) == 1 and not right_macs:
        return left.device_id, next(iter(left_macs))
    if len(right_macs) == 1 and not left_macs:
        return right.device_id, next(iter(right_macs))
    if len(left_macs) == 1 and len(right_macs) == 1:
        left_rank = integration_rank(left.integration, preferred)
        right_rank = integration_rank(right.integration, preferred)
        if left_rank < right_rank:
            return left.device_id, next(iter(left_macs))
        if right_rank < left_rank:
            return right.device_id, next(iter(right_macs))
    return None, None


def integration_rank(integration: str | None, preferred: Sequence[str]) -> int:
    """Position in the preferred MAC sources; unlisted integrations come last."""
    try:
        return list(preferred).index(integration or "")
    except ValueError:
        return len(preferred)


def _native_network_macs(
    device: DeviceView, copied_macs: set[tuple[str, str]]
) -> set[str]:
    """Network MACs on ``device`` that Device Linker did not copy there."""
    return {
        mac
        for mac in _network_macs(device.connections)
        if (device.device_id, mac) not in copied_macs
    }


def _network_macs(connections: Iterable[tuple[str, str]]) -> set[str]:
    found: set[str] = set()
    for connection_type, value in connections:
        if connection_type != CONNECTION_NETWORK_MAC:
            continue
        mac = normalize_mac(value)
        if mac is not None:
            found.add(mac)
    return found


def _auto_eligible(
    item: Suggestion, threshold: float, *, similar_names: bool = False
) -> bool:
    """Return whether this match is strong enough to link on its own.

    Hardware matches are included. An exact name is included, but a one-word
    exact name is not when either device has no area. A fuzzy name needs two
    shared distinctive words, both devices assigned to the same area, and a
    score at the automatic threshold. One shared word stays a suggestion.
    """
    if item.reason in {REASON_MAC, REASON_IDENTIFIER}:
        return True
    if item.reason != REASON_NAME:
        return False
    unassigned = item.area_name_a is None or item.area_name_b is None
    if item.exact:
        if unassigned and len(fold_name(item.name_a).split()) < 2:
            return False
        return True
    if item.score < threshold:
        return False
    if similar_names and item.shared_tokens >= 1:
        area = item.area_name_a or item.area_name_b
        if (
            item.area_name_a
            and item.area_name_b
            and item.area_name_a != item.area_name_b
        ):
            area = None
        if names_contained(item.name_a, item.name_b, area):
            return True
    return not unassigned and item.shared_tokens >= 2


def _bucket_rank(items: Sequence[Suggestion]) -> tuple[int, float, str, str]:
    return _sort_key(min(items, key=_sort_key))


def _anchor_end(item: Suggestion, anchor_id: str) -> tuple[str, str, str | None]:
    if item.device_id_a == anchor_id:
        return item.name_a, item.integration_a, item.config_entry_id_a
    return item.name_b, item.integration_b, item.config_entry_id_b


def _linked_end(
    item: Suggestion, anchor_id: str
) -> tuple[str, str, str, str | None] | None:
    if item.anchor_device_id != anchor_id:
        return None
    if item.device_id_a == anchor_id:
        return (
            item.device_id_b,
            item.name_b,
            item.integration_b,
            item.config_entry_id_b,
        )
    if item.device_id_b == anchor_id:
        return (
            item.device_id_a,
            item.name_a,
            item.integration_a,
            item.config_entry_id_a,
        )
    return None


def _links_device(item: Suggestion, linked_ids: Sequence[str]) -> bool:
    return item.device_id_a in linked_ids or item.device_id_b in linked_ids


def _colon_join(bare_hex: str) -> str:
    text = bare_hex.lower()
    return ":".join(text[index : index + 2] for index in range(0, 12, 2))


def _add_hardware_value(found: dict[tuple[str, str], str | None], value: str) -> None:
    if not isinstance(value, str):
        return
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
    scores = [_ratio(joined_left, joined_right)]
    # One name inside the other counts as a full match when they share two
    # or more words ("Red bicycle" and "Red bicycle stand"), or when the
    # words left over are filler (a model code like "G4"). One shared word
    # with a real word left over is not: "Hub" inside "Family Hub", where
    # "Family" says which hub. Left inside right: right's extras decide.
    several = len(tokens_left & tokens_right) >= 2
    if several or all(_filler_word(token) for token in tokens_left - tokens_right):
        scores.append(_ratio(shared, joined_right))
    if several or all(_filler_word(token) for token in tokens_right - tokens_left):
        scores.append(_ratio(shared, joined_left))
    return max(scores)


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

    Home Assistant links devices that share any identifier or any connection,
    exactly as stored: the Ford integration and Ford Assistant, for example,
    both use ``("fordpass", VIN)``. A network-MAC or Bluetooth connection is
    also compared after normalizing the address. The same value stored only
    as an identifier on one side and a connection on the other is not a link
    yet; Device Linker copies it onto a connection.
    """
    if set(left.identifiers) & set(right.identifiers):
        return True
    if set(left.connections) & set(right.connections):
        return True
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
    anchor_device_id: str | None = None,
    exact: bool = False,
    shared_tokens: int = 0,
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
        anchor_device_id=anchor_device_id,
        exact=exact,
        shared_tokens=shared_tokens,
        config_entry_id_a=first.config_entry_id,
        config_entry_id_b=second.config_entry_id,
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
