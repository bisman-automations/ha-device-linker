"""Automatic linking, and the UniFi same-name case that produced no suggestions."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.device_linker.const import (
    CONF_ORIGIN,
    DOMAIN,
    ISSUE_AUTO_LINKED,
    SERVICE_RESCAN,
    SERVICE_RESET,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from custom_components.device_linker.manager import async_get_manager
from custom_components.device_linker.matching import pair_key
from custom_components.device_linker.registry import hub_entry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.issue_registry import async_get as async_get_issues
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from tests.ha.helpers import (
    get_entry_settings,
    link_for as _link_for,
    links,
    refresh,
    remove_link,
)
from tests.ha.unifi_fixture import async_setup_anchor, async_unifi_name_case

TV_MAC = "aa:bb:cc:dd:ee:10"


def _links(hass):
    """Automatic links."""
    return links(hass, origin="auto")


def _entry_for(hass, device_id: str):
    return _link_for(hass, device_id, origin="auto")


async def test_unifi_same_name_links_when_the_client_has_no_area(hass) -> None:
    """The reported install: one MAC, the same name, area missing on UniFi.

    Before 1.3.0 the name tier required both devices to have the same area, so
    this registry produced no suggestions and the sensor stayed at 0.
    """
    await async_setup_anchor(hass)
    case = await async_unifi_name_case(hass)
    manager = async_get_manager(hass)
    assert manager is not None
    await manager.async_scan()
    await hass.async_block_till_done()

    tv = _entry_for(hass, case.apple_tv.id)
    assert tv is not None
    settings = get_entry_settings(tv)
    assert settings["main_device_id"] == case.unifi_tv.id
    assert settings["mac_address"] == TV_MAC
    assert settings[CONF_ORIGIN] == "auto"
    assert set(settings["linked_device_ids"]) == {
        case.apple_tv.id,
        case.sonos.id,
        case.shelly.id,
        case.kitchen_same_name.id,
    }
    assert tv.title == "Living Room TV (5 Devices Linked)"
    # Near name shares one distinctive word and one side has no area.
    assert case.near_name.id not in settings["linked_device_ids"]
    # A one-word name with no area is not linked to every TV.
    assert _entry_for(hass, case.bare_tv_other.id) is None
    # Area "Room" must not make "bedroom tablet" the same name as "bed tablet".
    assert _entry_for(hass, case.bedroom_tablet.id) is None
    registry = dr.async_get(hass)
    for device_id in settings["linked_device_ids"]:
        assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) in registry.async_get(
            device_id
        ).connections

    # Same UniFi entry as the anchor: Home Assistant cannot share the MAC.
    assert case.same_entry_duplicate.id not in settings["linked_device_ids"]
    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) not in registry.async_get(
        case.same_entry_duplicate.id
    ).connections
    assert all(
        case.unifi_tv.id not in {item.device_id_a, item.device_id_b}
        or case.same_entry_duplicate.id not in {item.device_id_a, item.device_id_b}
        for item in manager.suggestions
    )

    # Disabled, and the integration's own service device, are not candidates.
    assert case.disabled.id not in settings["linked_device_ids"]
    linker_ids = {
        device.id
        for device in dr.async_entries_for_config_entry(
            registry, case.anchor_entry.entry_id
        )
    }
    assert linker_ids
    assert linker_ids.isdisjoint(set(settings["linked_device_ids"]))

    # Both areas assigned, and different: not a name match.
    assert _entry_for(hass, case.office_speaker.id) is None
    assert all(
        case.bedroom_unifi.id not in {item.device_id_a, item.device_id_b}
        or case.office_speaker.id not in {item.device_id_a, item.device_id_b}
        for item in manager.suggestions
    )

    # Both missing an area: still a match, on its own entry.
    hall = _entry_for(hass, case.hall_esphome.id)
    assert hall is not None
    assert get_entry_settings(hall)["main_device_id"] == case.hall_unifi.id
    assert hall.title == "Hall Tablet (2 Devices Linked)"

    # Exact name whose distinctive words are empty ("Hall" + "Light").
    light = _entry_for(hass, case.hall_light.id)
    assert light is not None
    assert get_entry_settings(light)["main_device_id"] == case.hall_light_anchor.id

    issue = async_get_issues(hass).async_get_issue(DOMAIN, ISSUE_AUTO_LINKED)
    assert issue is not None
    assert issue.translation_key == ISSUE_AUTO_LINKED
    assert "Living Room TV" in issue.translation_placeholders["summary"]

    state_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "device_linker_suggestions"
    )
    state = hass.states.get(state_id)
    assert state is not None
    assert state.attributes["last_scan"]
    assert any(
        item["title"] == "Living Room TV (5 Devices Linked)"
        for item in state.attributes["auto_linked"]
    )


async def test_exact_pair_leaves_the_suggestion_count_at_zero(hass) -> None:
    """A single exact pair is linked, so the sensor stays at 0 and records the scan."""
    await async_setup_anchor(hass)
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    apple_config = MockConfigEntry(domain="apple_tv")
    apple_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "tv")},
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
        name="Living Room TV",
    )
    apple = registry.async_get_or_create(
        config_entry_id=apple_config.entry_id,
        identifiers={("apple_tv", "tv")},
        name="Living Room TV",
    )
    manager = async_get_manager(hass)
    await manager.async_scan()
    await hass.async_block_till_done()

    entry = _entry_for(hass, apple.id)
    assert entry is not None
    assert entry.title == "Living Room TV (2 Devices Linked)"
    assert get_entry_settings(entry)["main_device_id"] == unifi.id
    assert manager.suggestions == []
    state_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "device_linker_suggestions"
    )
    state = hass.states.get(state_id)
    assert state.state == "0"
    assert state.attributes["last_scan"]
    assert state.attributes["auto_linked"]


async def test_deleting_an_entry_is_not_linked_again_until_reset(hass) -> None:
    """Unlink is sticky. Reset dismissed and unlinked is what brings the pair back."""
    await async_setup_anchor(hass)
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    apple_config = MockConfigEntry(domain="apple_tv")
    apple_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "tv")},
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
        name="Living Room TV",
    )
    apple = registry.async_get_or_create(
        config_entry_id=apple_config.entry_id,
        identifiers={("apple_tv", "tv")},
        name="Living Room TV",
    )
    manager = async_get_manager(hass)
    await manager.async_scan()
    await hass.async_block_till_done()
    created = _entry_for(hass, apple.id)
    assert created is not None

    await remove_link(hass, created)
    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) not in registry.async_get(
        apple.id
    ).connections
    assert pair_key(unifi.id, apple.id) in manager.settings.unlinked

    await manager.async_scan()
    assert _entry_for(hass, apple.id) is None

    await hass.services.async_call(DOMAIN, SERVICE_RESET, {}, blocking=True)
    await hass.async_block_till_done()
    assert pair_key(unifi.id, apple.id) not in manager.settings.unlinked
    restored = _entry_for(hass, apple.id)
    assert restored is not None
    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) in registry.async_get(
        apple.id
    ).connections


async def test_edit_link_removes_one_device_and_does_not_add_it_back(hass) -> None:
    """Removing a device with Edit link is an unlink, not a pause."""
    await async_setup_anchor(hass)
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    apple_config = MockConfigEntry(domain="apple_tv")
    apple_config.add_to_hass(hass)
    sonos_config = MockConfigEntry(domain="sonos")
    sonos_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "tv")},
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
        name="Living Room TV",
    )
    apple = registry.async_get_or_create(
        config_entry_id=apple_config.entry_id,
        identifiers={("apple_tv", "tv")},
        name="Living Room TV",
    )
    sonos = registry.async_get_or_create(
        config_entry_id=sonos_config.entry_id,
        identifiers={("sonos", "tv")},
        name="Living Room TV",
    )
    manager = async_get_manager(hass)
    await manager.async_scan()
    await hass.async_block_till_done()
    created = _entry_for(hass, apple.id)
    assert created is not None
    assert set(get_entry_settings(created)["linked_device_ids"]) == {apple.id, sonos.id}

    hub = hub_entry(hass)
    result = await hub.start_subentry_reconfigure_flow(hass, created.entry_id)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "linked_device_ids": [unifi.id, apple.id],
            "selected_mac_address": TV_MAC,
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) not in registry.async_get(
        sonos.id
    ).connections
    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) in registry.async_get(
        apple.id
    ).connections
    await manager.async_scan()
    kept = _entry_for(hass, apple.id)
    assert kept is not None
    assert get_entry_settings(kept)["linked_device_ids"] == [apple.id]
    assert get_entry_settings(kept)["main_device_id"] == unifi.id
    assert sonos.id not in get_entry_settings(kept)["linked_device_ids"]


async def test_manual_entry_gains_another_device_without_changing_origin(hass) -> None:
    """An existing link does not block a different device, and stays manual."""
    anchor = await async_setup_anchor(hass)
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    shelly_config = MockConfigEntry(domain="shelly")
    shelly_config.add_to_hass(hass)
    esphome_config = MockConfigEntry(domain="esphome")
    esphome_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "garage")},
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:20")},
        name="Garage Door",
    )
    shelly = registry.async_get_or_create(
        config_entry_id=shelly_config.entry_id,
        identifiers={("shelly", "garage")},
        name="Garage Door",
    )
    manual = MockConfigEntry(
        domain=DOMAIN,
        title="Link Garage Door to 1 device",
        unique_id=f"{unifi.id}_aa:bb:cc:dd:ee:20",
        data={
            "main_device_id": unifi.id,
            "linked_device_ids": [shelly.id],
            "mac_address": "aa:bb:cc:dd:ee:20",
            "added_connections": {shelly.id: True},
            "origin": "manual",
        },
        version=1,
    )
    manual.add_to_hass(hass)
    assert await hass.config_entries.async_setup(manual.entry_id)
    await hass.async_block_till_done()
    esphome = registry.async_get_or_create(
        config_entry_id=esphome_config.entry_id,
        identifiers={("esphome", "garage")},
        name="Garage Door",
    )
    manager = async_get_manager(hass)
    await manager.async_scan()
    await hass.async_block_till_done()

    updated = _link_for(hass, shelly.id)
    settings = get_entry_settings(updated)
    assert settings[CONF_ORIGIN] == "manual"
    assert set(settings["linked_device_ids"]) == {shelly.id, esphome.id}
    assert updated.title == "✅ Garage Door (3 Devices Linked)"
    # Both old entries were folded into one hub; the second one is gone.
    hub = hub_entry(hass)
    assert hub.version == 3
    assert hub.entry_id == anchor.entry_id
    assert hass.config_entries.async_get_entry(manual.entry_id) is None
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.source != "ignore"
    ]
    assert entries == [hub]
    # The original anchor link was migrated and was not relabeled.
    assert {link.settings[CONF_ORIGIN] for link in links(hass)} == {"manual"}


async def test_v1_store_keeps_dismissals_and_turns_auto_link_on(hass) -> None:
    """A 1.2.x suggestion store gains the new keys without dropping what it had."""
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    await store.async_save(
        {
            "threshold": 0.7,
            "match_hardware": False,
            "match_names": True,
            "dismissed": ["suggestion:left:right"],
        }
    )
    await async_setup_anchor(hass)
    manager = async_get_manager(hass)
    assert manager.settings.threshold == 0.7
    assert manager.settings.match_hardware is False
    assert manager.settings.auto_link is True
    assert manager.settings.auto_link_threshold == 0.95
    assert "suggestion:left:right" in manager.settings.dismissed
    assert manager.settings.unlinked == frozenset()


async def test_homeassistant_started_schedules_a_rescan(hass) -> None:
    """Devices that appear during startup are scanned when Home Assistant is up.

    The device-registry listener is removed first so this assertion is about
    the started event, not about the create event the registration also fires.
    """
    await async_setup_anchor(hass)
    manager = async_get_manager(hass)
    await manager.async_update_settings(
        threshold=0.8,
        match_hardware=True,
        match_names=True,
        auto_link=False,
        auto_link_threshold=0.95,
    )
    manager._unsub_device()
    manager._unsub_device = None
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    apple_config = MockConfigEntry(domain="apple_tv")
    apple_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "late")},
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:30")},
        name="Late TV",
    )
    apple = registry.async_get_or_create(
        config_entry_id=apple_config.entry_id,
        identifiers={("apple_tv", "late")},
        name="Late TV",
    )
    manager._suggestions = []
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert any(
        {item.device_id_a, item.device_id_b} == {unifi.id, apple.id} and item.exact
        for item in manager.suggestions
    )


async def test_device_create_rescans_after_the_debounce(hass) -> None:
    """A device created after startup is picked up without a manual rescan."""
    await async_setup_anchor(hass)
    manager = async_get_manager(hass)
    await manager.async_update_settings(
        threshold=0.8,
        match_hardware=True,
        match_names=True,
        auto_link=False,
        auto_link_threshold=0.95,
    )
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    apple_config = MockConfigEntry(domain="apple_tv")
    apple_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "created")},
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:31")},
        name="Created TV",
    )
    apple = registry.async_get_or_create(
        config_entry_id=apple_config.entry_id,
        identifiers={("apple_tv", "created")},
        name="Created TV",
    )
    assert not any(
        unifi.id in {item.device_id_a, item.device_id_b} for item in manager.suggestions
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert any(
        {item.device_id_a, item.device_id_b} == {unifi.id, apple.id}
        for item in manager.suggestions
    )


async def test_auto_link_off_still_discovers_the_unifi_pair(hass) -> None:
    """Turning automatic linking off keeps the confirmation flow, area fix included."""
    await async_setup_anchor(hass)
    manager = async_get_manager(hass)
    await manager.async_update_settings(
        threshold=0.8,
        match_hardware=True,
        match_names=True,
        auto_link=False,
        auto_link_threshold=0.95,
    )
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    apple_config = MockConfigEntry(domain="apple_tv")
    apple_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "discover")},
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
        name="Living Room TV",
    )
    apple = registry.async_get_or_create(
        config_entry_id=apple_config.entry_id,
        identifiers={("apple_tv", "discover")},
        name="Living Room TV",
    )
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    assert _links(hass) == []
    assert any(
        {item.device_id_a, item.device_id_b} == {unifi.id, apple.id}
        and item.exact
        and item.anchor_device_id == unifi.id
        for item in manager.suggestions
    )
    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("unique_id", "").startswith("suggestion:")
    ]
    assert flows
    assert flows[0]["step_id"] == "discovery_name"


async def test_copied_mac_does_not_anchor_the_next_device(hass) -> None:
    """The Apple TV that received the UniFi MAC does not anchor a later match."""
    await async_setup_anchor(hass)
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    apple_config = MockConfigEntry(domain="apple_tv")
    apple_config.add_to_hass(hass)
    extra_config = MockConfigEntry(domain="esphome")
    extra_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "tv")},
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
        name="Living Room TV",
    )
    apple = registry.async_get_or_create(
        config_entry_id=apple_config.entry_id,
        identifiers={("apple_tv", "tv")},
        name="Living Room TV",
    )
    manager = async_get_manager(hass)
    await manager.async_scan()
    await hass.async_block_till_done()
    created = _entry_for(hass, apple.id)
    assert created is not None
    assert get_entry_settings(created)["added_connections"][apple.id] is True
    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) in registry.async_get(
        apple.id
    ).connections

    extra = registry.async_get_or_create(
        config_entry_id=extra_config.entry_id,
        identifiers={("esphome", "tv")},
        name="Living Room TV",
    )
    await manager.async_scan()
    await hass.async_block_till_done()

    updated = refresh(hass, created)
    settings = get_entry_settings(updated)
    assert settings["main_device_id"] == unifi.id
    assert set(settings["linked_device_ids"]) == {apple.id, extra.id}
    assert updated.title == "Living Room TV (3 Devices Linked)"
    assert all(
        get_entry_settings(entry)["main_device_id"] != apple.id
        for entry in _links(hass)
    )
    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) in registry.async_get(
        extra.id
    ).connections


async def _camera_pair(hass):
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    protect_config = MockConfigEntry(domain="unifiprotect")
    protect_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "driveway")},
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:30")},
        name="Front Driveway",
    )
    camera = registry.async_get_or_create(
        config_entry_id=protect_config.entry_id,
        identifiers={("unifiprotect", "driveway")},
        name="Front Driveway Camera",
    )
    return unifi, camera


async def _set_similar(hass, enabled: bool) -> None:
    manager = async_get_manager(hass)
    await manager.async_update_settings(
        threshold=0.8,
        match_hardware=True,
        match_names=True,
        auto_link=True,
        auto_link_threshold=0.95,
        auto_link_similar=enabled,
        scan=False,
    )


async def test_similar_names_are_linked_when_the_setting_is_on(hass) -> None:
    """Front Driveway (UniFi) and Front Driveway Camera become one link."""
    await async_setup_anchor(hass)
    await _set_similar(hass, True)
    unifi, camera = await _camera_pair(hass)
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()

    link = _entry_for(hass, camera.id)
    assert link is not None
    settings = get_entry_settings(link)
    assert settings["main_device_id"] == unifi.id
    assert settings["linked_device_ids"] == [camera.id]
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:30") in dr.async_get(
        hass
    ).async_get(camera.id).connections


async def test_similar_names_stay_suggestions_when_the_setting_is_off(hass) -> None:
    """With the setting off, the same pair is only suggested."""
    await async_setup_anchor(hass)
    await _set_similar(hass, False)
    unifi, camera = await _camera_pair(hass)
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()

    assert _entry_for(hass, camera.id) is None
    manager = async_get_manager(hass)
    assert any(
        {item.device_id_a, item.device_id_b} == {unifi.id, camera.id}
        for item in manager.suggestions
    )


async def test_edit_link_lists_the_main_device_and_where_each_mac_came_from(
    hass,
) -> None:
    """The form shows every device and names the source of each MAC address."""
    await async_setup_anchor(hass)
    unifi_config = MockConfigEntry(domain="unifi")
    unifi_config.add_to_hass(hass)
    shelly_config = MockConfigEntry(domain="shelly")
    shelly_config.add_to_hass(hass)
    matter_config = MockConfigEntry(domain="matter")
    matter_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    wifi_mac = "aa:bb:cc:dd:ee:40"
    ble_mac = "aa:bb:cc:dd:ee:41"
    unifi = registry.async_get_or_create(
        config_entry_id=unifi_config.entry_id,
        identifiers={("unifi", "leak")},
        connections={(dr.CONNECTION_NETWORK_MAC, wifi_mac)},
        name="Leak Sensor",
    )
    shelly = registry.async_get_or_create(
        config_entry_id=shelly_config.entry_id,
        identifiers={("shelly", "leak")},
        connections={(dr.CONNECTION_NETWORK_MAC, ble_mac)},
        name="Leak Sensor Shelly",
    )
    matter = registry.async_get_or_create(
        config_entry_id=matter_config.entry_id,
        identifiers={("matter", "leak")},
        name="Leak Sensor Matter",
    )
    hub = hub_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (hub.entry_id, "link"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={"main_device_id": unifi.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={"linked_device_ids": [matter.id, shelly.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={"selected_mac_address": wifi_mac}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    link = _link_for(hass, matter.id)
    assert link is not None

    result = await hub.start_subentry_reconfigure_flow(hass, link.entry_id)
    assert result["step_id"] == "reconfigure"
    schema = result["data_schema"].schema
    devices_key = next(k for k in schema if str(k) == "linked_device_ids")
    assert set(devices_key.default()) == {unifi.id, matter.id, shelly.id}
    mac_key = next(k for k in schema if str(k) == "selected_mac_address")
    labels = {
        option["value"]: option["label"] for option in schema[mac_key].config["options"]
    }
    assert labels == {
        wifi_mac: f"{wifi_mac} — Leak Sensor (unifi)",
        ble_mac: f"{ble_mac} — Leak Sensor Shelly (shelly)",
    }

    # Switching the address of a confirmed link needs Confirmed unticked.
    locked = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "linked_device_ids": [unifi.id, matter.id, shelly.id],
            "selected_mac_address": ble_mac,
        },
    )
    assert locked["errors"] == {"base": "confirmed_locked"}
    # Choosing the Shelly's address makes the Shelly the main device.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "linked_device_ids": [unifi.id, matter.id, shelly.id],
            "selected_mac_address": ble_mac,
            "confirmed": False,
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    settings = get_entry_settings(refresh(hass, link))
    assert settings["main_device_id"] == shelly.id
    assert set(settings["linked_device_ids"]) == {unifi.id, matter.id}
    assert settings["mac_address"] == ble_mac
    matter_connections = registry.async_get(matter.id).connections
    assert (dr.CONNECTION_NETWORK_MAC, ble_mac) in matter_connections
    assert (dr.CONNECTION_NETWORK_MAC, wifi_mac) not in matter_connections
    assert (dr.CONNECTION_NETWORK_MAC, wifi_mac) in registry.async_get(
        unifi.id
    ).connections
