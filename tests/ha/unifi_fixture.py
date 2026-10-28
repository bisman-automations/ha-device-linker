"""Donavan's install: UniFi has the MAC, and often no area.

The other integration (Apple TV, Sonos, Shelly) has the same or a
near-identical name and no MAC. Name matching used to require both devices
to share an area, so this fixture produced no suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.const import DOMAIN
from homeassistant.helpers import area_registry as ar, device_registry as dr


@dataclass
class UnifiNameCase:
    """Devices for the same-name tier, one side with a MAC."""

    anchor_entry: MockConfigEntry
    unifi_tv: dr.DeviceEntry
    apple_tv: dr.DeviceEntry
    sonos: dr.DeviceEntry
    shelly: dr.DeviceEntry
    near_name: dr.DeviceEntry
    kitchen_same_name: dr.DeviceEntry
    same_entry_duplicate: dr.DeviceEntry
    disabled: dr.DeviceEntry
    bedroom_unifi: dr.DeviceEntry
    office_speaker: dr.DeviceEntry
    hall_unifi: dr.DeviceEntry
    hall_esphome: dr.DeviceEntry
    hall_light_anchor: dr.DeviceEntry
    hall_light: dr.DeviceEntry
    bare_tv: dr.DeviceEntry
    bare_tv_other: dr.DeviceEntry
    bedroom_tablet: dr.DeviceEntry
    bed_tablet: dr.DeviceEntry
    mac: str


def _entry(hass, domain: str) -> MockConfigEntry:
    config = MockConfigEntry(domain=domain)
    config.add_to_hass(hass)
    return config


def _device(
    hass,
    config: MockConfigEntry,
    name: str,
    *,
    area_id: str | None = None,
    connections: set[tuple[str, str]] | None = None,
    identifier: str | None = None,
) -> dr.DeviceEntry:
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=config.entry_id,
        identifiers={(config.domain, identifier or name)},
        connections=connections or set(),
        name=name,
    )
    if area_id is not None:
        registry.async_update_device(device.id, area_id=area_id)
    return registry.async_get(device.id)


async def async_setup_anchor(hass) -> MockConfigEntry:
    """One real Device Linker entry so the manager is running.

    The two anchor devices already share a MAC through this entry, so they
    are not themselves a suggestion. The service device this creates is the
    one Donavan sees in the Utility Room.
    """
    areas = ar.async_get(hass)
    utility = areas.async_get_or_create("Utility Room")
    main_config = _entry(hass, "anchor_main")
    linked_config = _entry(hass, "anchor_linked")
    registry = dr.async_get(hass)
    main = registry.async_get_or_create(
        config_entry_id=main_config.entry_id,
        identifiers={("anchor_main", "main")},
        connections={(dr.CONNECTION_NETWORK_MAC, "11:22:33:44:55:66")},
        name="Anchor main",
    )
    linked = registry.async_get_or_create(
        config_entry_id=linked_config.entry_id,
        identifiers={("anchor_linked", "linked")},
        name="Anchor linked",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Link Anchor main to 1 device",
        data={
            "main_device_id": main.id,
            "linked_device_ids": [linked.id],
            "mac_address": "11:22:33:44:55:66",
            "added_connections": {linked.id: True},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The service device follows the entity, which has no area of its own.
    # Give the area a real id so a scan that wrongly required areas still
    # has somewhere to put a bogus match. The device is excluded by domain.
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        registry.async_update_device(device.id, area_id=utility.id)
    return entry


async def async_unifi_name_case(hass) -> UnifiNameCase:
    """Build the registry Donavan described, then return every device.

    Call :func:`async_setup_anchor` first. This does not scan; the test
    decides when the manager runs.
    """
    areas = ar.async_get(hass)
    living = areas.async_get_or_create("Living Room")
    kitchen = areas.async_get_or_create("Kitchen")
    bedroom = areas.async_get_or_create("Bedroom")
    office = areas.async_get_or_create("Office")
    hall = areas.async_get_or_create("Hall")
    room = areas.async_get_or_create("Room")
    mac = "aa:bb:cc:dd:ee:10"
    unifi = _entry(hass, "unifi")
    apple = _entry(hass, "apple_tv")
    sonos = _entry(hass, "sonos")
    shelly = _entry(hass, "shelly")
    homekit = _entry(hass, "homekit")
    kitchen_apple = _entry(hass, "kitchen_apple_tv")
    disabled_entry = _entry(hass, "disabled_media")
    office_entry = _entry(hass, "office_media")
    esphome = _entry(hass, "esphome")
    light_a = _entry(hass, "light_a")
    light_b = _entry(hass, "light_b")
    bare_unifi = _entry(hass, "unifi_network")
    bare_media = _entry(hass, "bare_media")
    room_tablet = _entry(hass, "room_tablet")
    room_bed = _entry(hass, "room_bed")

    unifi_tv = _device(
        hass,
        unifi,
        "Living Room TV",
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        identifier="living-room-tv",
    )
    same_entry_duplicate = _device(
        hass,
        unifi,
        "Living Room TV",
        identifier="living-room-tv-duplicate",
    )
    apple_tv = _device(hass, apple, "Living Room TV", area_id=living.id)
    sonos_device = _device(hass, sonos, "Living Room TV")
    shelly_device = _device(hass, shelly, "Living Room TV", area_id=living.id)
    near_name = _device(hass, homekit, "Living Room Apple TV", area_id=living.id)
    kitchen_same_name = _device(
        hass, kitchen_apple, "Living Room TV", area_id=kitchen.id
    )
    disabled = _device(hass, disabled_entry, "Living Room TV", area_id=living.id)
    dr.async_get(hass).async_update_device(
        disabled.id, disabled_by=dr.DeviceEntryDisabler.USER
    )
    bedroom_unifi = _device(
        hass,
        unifi,
        "Bedroom Speaker",
        area_id=bedroom.id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:11")},
        identifier="bedroom-speaker",
    )
    office_speaker = _device(hass, office_entry, "Bedroom Speaker", area_id=office.id)
    hall_unifi = _device(
        hass,
        unifi,
        "Hall Tablet",
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:12")},
        identifier="hall-tablet",
    )
    hall_esphome = _device(hass, esphome, "Hall Tablet")
    hall_light_anchor = _device(
        hass,
        light_a,
        "Hall Light",
        area_id=hall.id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:13")},
    )
    hall_light = _device(hass, light_b, "Hall Light", area_id=hall.id)
    # One word and no area: suggested, not linked to every other TV.
    bare_tv = _device(
        hass,
        bare_unifi,
        "TV",
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:14")},
        identifier="bare-tv",
    )
    bare_tv_other = _device(hass, bare_media, "TV")
    # Area "Room" must not turn "bedroom tablet" into "bed tablet".
    bedroom_tablet = _device(hass, room_tablet, "bedroom tablet", area_id=room.id)
    bed_tablet = _device(
        hass,
        room_bed,
        "bed tablet",
        area_id=room.id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:15")},
    )
    anchor = hass.config_entries.async_entries(DOMAIN)[0]
    return UnifiNameCase(
        anchor_entry=anchor,
        unifi_tv=unifi_tv,
        apple_tv=apple_tv,
        sonos=sonos_device,
        shelly=shelly_device,
        near_name=near_name,
        kitchen_same_name=kitchen_same_name,
        same_entry_duplicate=same_entry_duplicate,
        disabled=disabled,
        bedroom_unifi=bedroom_unifi,
        office_speaker=office_speaker,
        hall_unifi=hall_unifi,
        hall_esphome=hall_esphome,
        hall_light_anchor=hall_light_anchor,
        hall_light=hall_light,
        bare_tv=bare_tv,
        bare_tv_other=bare_tv_other,
        bedroom_tablet=bedroom_tablet,
        bed_tablet=bed_tablet,
        mac=mac,
    )
