"""Name and hardware matching, loaded without the integration package.

``matching.py`` has no Home Assistant imports. Load it directly so this file
does not depend on starting Home Assistant.
"""

import importlib.util
from pathlib import Path
import sys
import types

import pytest

_SRC = Path(__file__).resolve().parents[1] / "custom_components" / "device_linker"
_PKG = "device_linker_pure"
_package = types.ModuleType(_PKG)
_package.__path__ = [str(_SRC)]
sys.modules[_PKG] = _package


def _load(name: str):
    """Load a helper without running the integration package, which imports HA."""
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _SRC / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.{name}"] = module
    spec.loader.exec_module(module)
    return module


_load("const")
matching = _load("matching")

DeviceView = matching.DeviceView


def _device(
    device_id: str,
    *,
    name: str = "Device",
    area_id: str | None = "area",
    area_name: str | None = "Kitchen",
    config_entry_id: str | None = None,
    integration: str = "test",
    connections: tuple[tuple[str, str], ...] = (),
    identifiers: tuple[tuple[str, str], ...] = (),
) -> DeviceView:
    return DeviceView(
        device_id=device_id,
        name=name,
        area_id=area_id,
        area_name=area_name,
        config_entry_id=config_entry_id or f"entry-{device_id}",
        integration=integration,
        connections=connections,
        identifiers=identifiers,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("aa-bb-cc-dd-ee-ff", "aa:bb:cc:dd:ee:ff"),
        ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
        ("aabb.ccdd.eeff", "aa:bb:cc:dd:ee:ff"),
    ],
)
def test_normalize_mac_accepts_the_same_forms_as_a_nic(raw: str, expected: str) -> None:
    """Colon, hyphen, bare hex, and Cisco dotted addresses collapse to one form."""
    assert matching.normalize_mac(raw) == expected


@pytest.mark.parametrize("raw", ["", "not-a-mac", "AA:BB:CC:DD:EE", "12345"])
def test_normalize_mac_rejects_non_addresses(raw: str) -> None:
    """Serial numbers and partial addresses are not MAC keys."""
    assert matching.normalize_mac(raw) is None


def test_eui64_with_fffe_also_matches_the_embedded_mac() -> None:
    """A Zigbee IEEE that was built by inserting fffe into a MAC shares that MAC."""
    keys = matching.hardware_keys((), (("zha", "00:17:88:ff:fe:12:34:56"),))
    assert ("mac", "00:17:88:12:34:56") in keys
    assert ("eui", "001788fffe123456") in keys


def test_serial_numbers_match_across_integrations_and_short_values_do_not() -> None:
    """A long serial is a hardware key. A short or word-only value is not."""
    keys = matching.hardware_keys((), (("shelly", "SH-44129981"),))
    assert ("serial", "sh-44129981") in keys
    assert matching.hardware_keys((), (("other", "light"),)) == {}
    assert matching.hardware_keys((), (("other", "1234"),)) == {}


def test_normalize_name_strips_area_brand_and_generic_words() -> None:
    """The distinctive word is what name matching compares."""
    assert (
        matching.normalize_name("Shelly Kitchen ceiling light", "Kitchen") == "ceiling"
    )
    assert matching.normalize_name("Kitchen ceiling", "Kitchen") == "ceiling"


def test_name_similarity_is_high_for_the_same_distinctive_words() -> None:
    """Token order and a leftover generic word do not hide a shared name."""
    score = matching.name_similarity(
        "Kitchen ceiling light", "Shelly ceiling kitchen", "Kitchen"
    )
    assert score >= 0.8


def test_name_similarity_is_low_when_nothing_distinctive_is_shared() -> None:
    """Different words in the same area stay below the default threshold."""
    score = matching.name_similarity("North hall sensor", "South porch plug", "Hall")
    assert score < 0.8


def test_same_area_name_match_respects_the_threshold() -> None:
    """A weak overlap is a suggestion only when the threshold allows it."""
    kitchen = "kitchen"
    left = _device(
        "a",
        name="Kitchen ceiling light",
        area_id=kitchen,
        area_name="Kitchen",
    )
    right = _device(
        "b",
        name="Kitchen ceiling",
        area_id=kitchen,
        area_name="Kitchen",
    )
    high = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert len(high) == 1
    assert high[0].reason == "name"
    assert high[0].score >= 0.8

    none = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=1.0,
        match_hardware=False,
    )
    # Identical distinctive words score 1.0, so raise the bar with a weaker pair.
    weak_left = _device("a", name="North hall", area_id=kitchen, area_name="Kitchen")
    weak_right = _device("b", name="South porch", area_id=kitchen, area_name="Kitchen")
    weak = matching.suggest(
        [weak_left, weak_right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert weak == []
    assert none  # the ceiling pair still matches at 1.0


def test_name_matches_stay_inside_one_area() -> None:
    """The same names in different areas are not a name suggestion."""
    left = _device(
        "a",
        name="Ceiling light",
        area_id="kitchen",
        area_name="Kitchen",
    )
    right = _device(
        "b",
        name="Ceiling light",
        area_id="garage",
        area_name="Garage",
    )
    assert (
        matching.suggest(
            [left, right],
            linked_pairs=set(),
            dismissed=set(),
            threshold=0.5,
            match_hardware=False,
        )
        == []
    )


def test_mac_match_is_allowed_across_areas_and_ranks_above_a_name() -> None:
    """A UniFi client and another integration's device share a MAC first."""
    unifi = _device(
        "unifi-1",
        name="Living room tablet",
        area_id="downstairs",
        area_name="Downstairs",
        integration="unifi",
        connections=(("mac", "AA:BB:CC:DD:EE:FF"),),
    )
    shelly = _device(
        "shelly-1",
        name="Tablet plug",
        area_id="living",
        area_name="Living room",
        integration="shelly",
        identifiers=(("shelly", "aa-bb-cc-dd-ee-ff"),),
    )
    name_left = _device(
        "name-1",
        name="Kitchen ceiling light",
        area_id="kitchen",
        area_name="Kitchen",
    )
    name_right = _device(
        "name-2",
        name="Kitchen ceiling",
        area_id="kitchen",
        area_name="Kitchen",
    )
    suggestions = matching.suggest(
        [name_left, name_right, shelly, unifi],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
    )
    assert suggestions[0].reason == "mac"
    assert suggestions[0].key == matching.pair_key("shelly-1", "unifi-1")
    assert suggestions[0].area_mismatch is True
    assert suggestions[0].mac == "aa:bb:cc:dd:ee:ff"
    assert suggestions[1].reason == "name"
    assert suggestions[0].score >= suggestions[1].score


def test_same_config_entry_self_match_and_dismissal_are_excluded() -> None:
    """Do not suggest a device against itself, its own entry, or a dismissed pair."""
    mac = (("mac", "aa:bb:cc:dd:ee:11"),)
    left = _device("a", connections=mac, config_entry_id="same")
    right = _device("b", connections=mac, config_entry_id="same")
    assert (
        matching.suggest(
            [left, right], linked_pairs=set(), dismissed=set(), threshold=0.8
        )
        == []
    )

    other = _device("c", identifiers=(("shelly", "aa:bb:cc:dd:ee:11"),))
    dismissed = {matching.suggestion_unique_id("a", "c")}
    # left is in "same"; give it its own entry so only dismissal removes the pair.
    left = _device("a", connections=mac, config_entry_id="unifi")
    assert (
        matching.suggest(
            [left, other],
            linked_pairs=set(),
            dismissed=dismissed,
            threshold=0.8,
        )
        == []
    )
    assert (
        matching.suggest(
            [left, other],
            linked_pairs={matching.pair_key("a", "c")},
            dismissed=set(),
            threshold=0.8,
        )
        == []
    )


def test_devices_that_already_share_a_mac_connection_are_not_suggested() -> None:
    """Home Assistant already links devices that share a network MAC connection."""
    mac = (("mac", "aa:bb:cc:dd:ee:22"),)
    left = _device("a", connections=mac)
    right = _device("b", connections=mac)
    assert (
        matching.suggest(
            [left, right], linked_pairs=set(), dismissed=set(), threshold=0.8
        )
        == []
    )


def test_choose_link_ends_prefers_the_device_that_already_has_the_mac() -> None:
    """The device with the MAC connection stays the main device."""
    mac = "aa:bb:cc:dd:ee:ff"
    connection = {("mac", mac)}
    assert matching.choose_link_ends("a", "b", mac, connection, set()) == ("a", ["b"])
    assert matching.choose_link_ends("a", "b", mac, set(), connection) == ("b", ["a"])
    assert matching.choose_link_ends("a", "b", mac, set(), set()) == (None, ["a", "b"])
    # Registry values are not always pre-normalized. The anchor check folds them.
    assert matching.choose_link_ends(
        "a", "b", mac, {("mac", "AA:BB:CC:DD:EE:FF")}, set()
    ) == ("a", ["b"])


def test_living_room_tv_normalization_keeps_the_name() -> None:
    """The reported names are not emptied by brand, area, or generic stripping.

    "Living Room TV" keeps "tv" once the area is removed, and the full folded
    name when there is no area. The default 0.8 threshold is not what drops
    an exact pair: the score is 1.
    """
    assert matching.normalize_name("Living Room TV", "Living Room") == "tv"
    assert matching.normalize_name("Living Room TV", None) == "living room tv"
    assert matching.normalize_name("UniFi Living Room Apple TV", "Living Room") == "tv"
    assert matching.fold_name("Living Room TV") == matching.fold_name("living-room tv")
    assert matching.name_similarity("Living Room TV", "Living Room TV", None) == 1.0
    assert (
        matching.name_similarity(
            "Living Room TV", "Living Room Apple TV", "Living Room"
        )
        == 1.0
    )


def test_exact_names_match_when_distinctive_words_are_stripped() -> None:
    """Two devices both called "Kitchen Light" match even though nothing distinctive remains."""
    left = _device("a", name="Kitchen Light", area_id="kitchen", area_name="Kitchen")
    right = _device("b", name="Kitchen Light", area_id="kitchen", area_name="Kitchen")
    suggestions = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert len(suggestions) == 1
    assert suggestions[0].exact is True
    assert suggestions[0].score == 1.0


def test_unifi_without_an_area_matches_the_same_name() -> None:
    """A UniFi client with no area matches the same name in a room.

    Name matching used to require both devices to have the same area id, so
    every unassigned UniFi client was invisible to the name tier. That is the
    case that left the Suggestions sensor at 0.
    """
    unifi = _device(
        "unifi-tv",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        integration="unifi",
        connections=(("mac", "AA:BB:CC:DD:EE:01"),),
    )
    apple = _device(
        "apple-tv",
        name="Living Room TV",
        area_id="living",
        area_name="Living Room",
        integration="apple_tv",
    )
    sonos = _device(
        "sonos-tv",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        integration="sonos",
    )
    suggestions = matching.suggest(
        [unifi, apple, sonos],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    keys = {item.key for item in suggestions}
    assert matching.pair_key("unifi-tv", "apple-tv") in keys
    assert matching.pair_key("unifi-tv", "sonos-tv") in keys
    apple_match = next(
        item
        for item in suggestions
        if item.key == matching.pair_key("unifi-tv", "apple-tv")
    )
    assert apple_match.exact is True
    assert apple_match.score == 1.0
    assert apple_match.reason == "name"
    assert apple_match.anchor_device_id == "unifi-tv"
    assert apple_match.mac == "aa:bb:cc:dd:ee:01"
    assert apple_match.area_mismatch is True


def test_name_match_rejects_two_different_assigned_areas() -> None:
    """A missing area is compatible. Two different areas are not."""
    bedroom = _device(
        "a",
        name="Bedroom Speaker",
        area_id="bedroom",
        area_name="Bedroom",
        connections=(("mac", "aa:bb:cc:dd:ee:02"),),
    )
    office = _device(
        "b",
        name="Bedroom Speaker",
        area_id="office",
        area_name="Office",
    )
    assert (
        matching.suggest(
            [bedroom, office],
            linked_pairs=set(),
            dismissed=set(),
            threshold=0.5,
            match_hardware=False,
        )
        == []
    )


def test_auto_link_groups_every_same_name_device_on_the_anchor() -> None:
    """One UniFi MAC becomes one plan, not one plan per pair."""
    unifi = _device(
        "unifi-tv",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        integration="unifi",
        connections=(("mac", "aa:bb:cc:dd:ee:01"),),
    )
    apple = _device(
        "apple-tv",
        name="Living Room TV",
        area_id="living",
        area_name="Living Room",
        integration="apple_tv",
    )
    shelly = _device(
        "shelly-tv",
        name="Living Room TV",
        area_id="living",
        area_name="Living Room",
        integration="shelly",
    )
    suggestions = matching.suggest(
        [unifi, apple, shelly],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
    )
    plans = matching.plan_auto_links(suggestions, enabled=True, threshold=0.95)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.anchor_device_id == "unifi-tv"
    assert plan.mac == "aa:bb:cc:dd:ee:01"
    assert set(plan.linked_device_ids) == {"apple-tv", "shelly-tv"}
    assert plan.exact is True

    held = matching.plan_auto_links(suggestions, enabled=False, threshold=0.95)
    assert held == []


def test_same_config_entry_is_not_grouped_onto_the_anchor() -> None:
    """A second UniFi device from the same entry cannot take the MAC."""
    unifi = _device(
        "unifi-tv",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        integration="unifi",
        config_entry_id="unifi-entry",
        connections=(("mac", "aa:bb:cc:dd:ee:01"),),
    )
    duplicate = _device(
        "unifi-dup",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        integration="unifi",
        config_entry_id="unifi-entry",
    )
    apple = _device(
        "apple-tv",
        name="Living Room TV",
        area_id="living",
        area_name="Living Room",
        integration="apple_tv",
    )
    suggestions = matching.suggest(
        [unifi, duplicate, apple],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert all(
        matching.pair_key("unifi-tv", "unifi-dup") != item.key for item in suggestions
    )
    plans = matching.plan_auto_links(suggestions, enabled=True, threshold=0.95)
    assert len(plans) == 1
    assert plans[0].linked_device_ids == ("apple-tv",)


def test_two_macs_are_not_an_automatic_name_link() -> None:
    """Do not pick one of two addresses just because the names match."""
    left = _device(
        "a",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        connections=(("mac", "aa:bb:cc:dd:ee:01"),),
    )
    right = _device(
        "b",
        name="Living Room TV",
        area_id="living",
        area_name="Living Room",
        connections=(("mac", "aa:bb:cc:dd:ee:02"),),
    )
    suggestions = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert len(suggestions) == 1
    assert suggestions[0].anchor_device_id is None
    assert matching.plan_auto_links(suggestions, enabled=True, threshold=0.5) == []


def test_one_shared_word_is_a_suggestion_and_not_an_automatic_link() -> None:
    """North window and North porch share one word, so they are not linked."""
    left = _device(
        "a",
        name="North window",
        area_id="room",
        area_name="Room",
        connections=(("mac", "aa:bb:cc:dd:ee:03"),),
    )
    right = _device("b", name="North porch", area_id="room", area_name="Room")
    suggestions = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.5,
        match_hardware=False,
    )
    assert len(suggestions) == 1
    assert suggestions[0].exact is False
    assert suggestions[0].shared_tokens == 1
    assert suggestions[0].score >= 0.5
    assert matching.plan_auto_links(suggestions, enabled=True, threshold=0.5) == []


def test_two_shared_distinctive_words_can_auto_link_in_one_area() -> None:
    """A fuzzy name with two shared words in the same area can be linked."""
    left = _device(
        "a",
        name="Red bicycle",
        area_id="kitchen",
        area_name="Kitchen",
        connections=(("mac", "aa:bb:cc:dd:ee:04"),),
    )
    right = _device(
        "b", name="Red bicycle stand", area_id="kitchen", area_name="Kitchen"
    )
    suggestions = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert len(suggestions) == 1
    assert suggestions[0].exact is False
    assert suggestions[0].shared_tokens >= 2
    plans = matching.plan_auto_links(suggestions, enabled=True, threshold=0.95)
    assert len(plans) == 1
    assert plans[0].anchor_device_id == "a"
    assert plans[0].linked_device_ids == ("b",)


def test_area_names_are_stripped_as_whole_tokens() -> None:
    """An area called Room must not rewrite bedroom or bathroom."""
    assert matching.normalize_name("bedroom tablet", "Room") == "bedroom tablet"
    assert matching.normalize_name("bathroom light", "Room") == "bathroom"
    assert "bedroom" in matching.normalize_name("bedroom speaker", "Room").split()
    left = _device(
        "bed",
        name="bedroom tablet",
        area_id="room",
        area_name="Room",
        connections=(("mac", "aa:bb:cc:dd:ee:05"),),
    )
    right = _device("short", name="bed tablet", area_id="room", area_name="Room")
    suggestions = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.5,
        match_hardware=False,
    )
    assert matching.plan_auto_links(suggestions, enabled=True, threshold=0.5) == []


def test_unassigned_one_word_name_is_not_auto_linked() -> None:
    """A bare TV with no area is a suggestion, not a link to every TV."""
    unifi = _device(
        "unifi",
        name="TV",
        area_id=None,
        area_name=None,
        integration="unifi",
        connections=(("mac", "aa:bb:cc:dd:ee:06"),),
    )
    other = _device(
        "other",
        name="TV",
        area_id=None,
        area_name=None,
        integration="apple_tv",
    )
    suggestions = matching.suggest(
        [unifi, other],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert len(suggestions) == 1
    assert suggestions[0].exact is True
    assert matching.plan_auto_links(suggestions, enabled=True, threshold=0.8) == []

    named = _device(
        "named",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        connections=(("mac", "aa:bb:cc:dd:ee:07"),),
    )
    peer = _device(
        "peer", name="Living Room TV", area_id="living", area_name="Living Room"
    )
    planned = matching.plan_auto_links(
        matching.suggest(
            [named, peer],
            linked_pairs=set(),
            dismissed=set(),
            threshold=0.8,
            match_hardware=False,
        ),
        enabled=True,
        threshold=0.95,
    )
    assert len(planned) == 1
    assert planned[0].anchor_device_id == "named"


def test_copied_mac_does_not_become_the_anchor() -> None:
    """An address Device Linker copied on is ignored when picking the anchor."""
    mac = "aa:bb:cc:dd:ee:01"
    unifi = _device(
        "unifi",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        integration="unifi",
        connections=(("mac", mac),),
    )
    apple = _device(
        "apple",
        name="Living Room TV",
        area_id="living",
        area_name="Living Room",
        integration="apple_tv",
        connections=(("mac", mac),),
    )
    extra = _device(
        "extra",
        name="Living Room TV",
        area_id=None,
        area_name=None,
        integration="esphome",
    )
    suggestions = matching.suggest(
        [unifi, apple, extra],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
        copied_macs={("apple", mac)},
    )
    plans = matching.plan_auto_links(suggestions, enabled=True, threshold=0.95)
    assert len(plans) == 1
    assert plans[0].anchor_device_id == "unifi"
    assert plans[0].linked_device_ids == ("extra",)
    assert all(item.anchor_device_id != "apple" for item in suggestions)

    only_copied = matching.suggest(
        [apple, extra],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
        copied_macs={("apple", mac)},
    )
    assert only_copied
    assert only_copied[0].anchor_device_id is None
    assert matching.plan_auto_links(only_copied, enabled=True, threshold=0.5) == []


def test_same_area_suggestions_remain_when_neither_device_has_a_mac() -> None:
    """Devices in one room are still suggested when neither has an address."""
    left = _device(
        "a", name="Kitchen ceiling light", area_id="kitchen", area_name="Kitchen"
    )
    right = _device("b", name="Kitchen ceiling", area_id="kitchen", area_name="Kitchen")
    suggestions = matching.suggest(
        [left, right],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.8,
        match_hardware=False,
    )
    assert len(suggestions) == 1
    assert suggestions[0].reason == "name"
    assert matching.plan_auto_links(suggestions, enabled=True, threshold=0.5) == []


def test_fuzzy_names_without_a_mac_are_not_compared_across_areas() -> None:
    """Unaddressed devices are not fuzzy-matched to the whole registry.

    An exact name is still suggested. A fuzzy name is suggested when the
    devices share an area, or when one of them has a MAC.
    """
    left = _device("a", name="Patio heater", area_id=None, area_name=None)
    right = _device("b", name="Patio heater remote", area_id=None, area_name=None)
    assert (
        matching.suggest(
            [left, right],
            linked_pairs=set(),
            dismissed=set(),
            threshold=0.5,
            match_hardware=False,
        )
        == []
    )
    same_room = [
        _device(
            "a",
            name="Patio heater",
            area_id="patio",
            area_name="Patio",
        ),
        _device(
            "b",
            name="Patio heater remote",
            area_id="patio",
            area_name="Patio",
        ),
    ]
    assert matching.suggest(
        same_room,
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.5,
        match_hardware=False,
    )


@pytest.mark.parametrize(
    ("name_a", "name_b", "area"),
    [
        ("Front Driveway", "Front Driveway Camera", None),
        ("Deck", "G4 Deck Camera", None),
        ("Garage Cam", "Garage Camera", "Garage"),
        ("Mamaroo", "4moms Mamaroo", None),
        ("Front Entrance Doorbell", "Front Entrance Doorbell Camera", None),
    ],
)
def test_names_contained_allows_filler_words(name_a, name_b, area) -> None:
    """A name plus generic words, numbers or model codes is the same device."""
    assert matching.names_contained(name_a, name_b, area)
    assert matching.names_contained(name_b, name_a, area)


@pytest.mark.parametrize(
    ("name_a", "name_b", "area"),
    [
        ("Living Room TV", "Living Room Apple TV", "Living Room"),
        ("Garage Door", "Garage Camera", "Garage"),
        ("Upstairs Hallway", "Upstairs Hallway Thermostat Sensor Kitchen", None),
    ],
)
def test_names_contained_rejects_other_devices(name_a, name_b, area) -> None:
    """A leftover brand, place or device word can mean a different device."""
    assert not matching.names_contained(name_a, name_b, area)


def _similar_pair(name_a: str, name_b: str):
    unifi = _device(
        "unifi",
        name=name_a,
        area_id=None,
        area_name=None,
        integration="unifi",
        connections=(("mac", "aa:bb:cc:dd:ee:20"),),
    )
    camera = _device(
        "camera",
        name=name_b,
        area_id=None,
        area_name=None,
        integration="unifiprotect",
    )
    return matching.suggest(
        [unifi, camera],
        linked_pairs=set(),
        dismissed=set(),
        threshold=0.5,
        match_hardware=False,
    )


def test_similar_names_link_only_when_the_setting_is_on() -> None:
    """Front Driveway and Front Driveway Camera link with the setting on."""
    suggestions = _similar_pair("Front Driveway", "Front Driveway Camera")
    assert suggestions
    assert matching.plan_auto_links(suggestions, enabled=True, threshold=0.5) == []
    plans = matching.plan_auto_links(
        suggestions, enabled=True, threshold=0.5, similar_names=True
    )
    assert len(plans) == 1
    assert plans[0].anchor_device_id == "unifi"
    assert plans[0].linked_device_ids == ("camera",)


def test_similar_names_never_link_a_different_device() -> None:
    """Garage Door is not linked to Garage Camera, even with the setting on."""
    suggestions = _similar_pair("Garage Door", "Garage Camera")
    assert (
        matching.plan_auto_links(
            suggestions, enabled=True, threshold=0.5, similar_names=True
        )
        == []
    )


def test_identifiers_that_are_not_text_are_ignored() -> None:
    """Some integrations register a number as an identifier value."""
    assert matching.normalize_mac(12345) is None
    assert matching.hardware_keys((), (("numbers", 12345),)) == {}


def test_a_connection_word_alone_is_not_a_match() -> None:
    """Two devices that share only "BLE" aren't the same device."""
    score, exact = matching._name_score(
        "Family Room Air Purifier (BLE)", "Family Room Lights (BLE)", "Family Room"
    )
    assert (score, exact) == (0.0, False)
    # The same device under two connections still matches.
    score, _ = matching._name_score(
        "Family Room Air Purifier (BLE)",
        "Family Room Air Purifier (Cloud)",
        "Family Room",
    )
    assert score == 1.0


def test_a_real_leftover_word_is_not_a_full_match() -> None:
    """ "Kitchen Hub" in the Kitchen is "hub"; "Family Hub" says which hub."""
    score, _ = matching._name_score("Family Hub", "Kitchen Hub", "Kitchen")
    assert score < 0.8
    # A model code left over still counts: the same camera.
    score, _ = matching._name_score("Deck", "G4 Deck", None)
    assert score == 1.0


def test_two_different_brands_are_not_a_name_match() -> None:
    """An Apple TV and a TV with Google Cast are different devices."""
    for area in ("Family Room", None):
        score, _ = matching._name_score(
            "Family Room TV (Google Cast)", "Family Room Apple TV", area
        )
        assert score == 0.0
    # The same brand on both, or a brand on only one, can still match.
    score, _ = matching._name_score("Apple TV Den", "Den Apple TV", "Den")
    assert score == 1.0
    score, _ = matching._name_score("Sonos Kitchen Speaker", "Kitchen Speaker", None)
    assert score >= 0.8
