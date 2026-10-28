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
