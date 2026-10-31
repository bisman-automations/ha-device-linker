"""A device already linked to another device isn't offered for a new link."""

from custom_components.device_linker.const import DOMAIN, SERVICE_RESCAN
from custom_components.device_linker.manager import (
    async_apply_suggestion_preferences,
    async_get_manager,
)
from custom_components.device_linker.matching import suggestion_unique_id
from homeassistant.config_entries import SOURCE_DISCOVERY
from homeassistant.helpers import device_registry as dr
from tests.ha.helpers import links
from tests.ha.test_config_flow import _setup_hub
from tests.ha.test_shared_id import _service_case
from tests.ha.test_suggestions import _disable_auto_link, _register

SHARED = "12:34:56:78:9a:bc"


async def test_a_group_linked_by_home_assistant_takes_new_devices_once(hass) -> None:
    """Two devices sharing a MAC are a group; a third of the same name can join it."""
    await _setup_hub(hass)
    await _disable_auto_link(hass)
    first = _register(
        hass,
        domain="unifi",
        name="Kitchen Speaker",
        area="Kitchen",
        connections={(dr.CONNECTION_NETWORK_MAC, SHARED)},
    )
    _register(
        hass,
        domain="apple_tv",
        name="Kitchen Speaker",
        area="Kitchen",
        connections={(dr.CONNECTION_NETWORK_MAC, SHARED)},
    )
    third = _register(hass, domain="sonos", name="Kitchen Speaker", area="Kitchen")
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()

    suggested = {
        frozenset((item.device_id_a, item.device_id_b))
        for item in async_get_manager(hass).suggestions
    }
    # The third joins the group once, through UniFi (the preferred source),
    # sharing the group's MAC; never through the Apple TV as well.
    assert suggested == {frozenset((first.id, third.id))}
    (item,) = async_get_manager(hass).suggestions
    assert item.anchor_device_id == first.id
    assert item.mac == SHARED
    assert links(hass) == []


async def test_turning_include_services_off_unlinks_them_now(hass) -> None:
    """Services leave automatic links when the setting is saved, not at restart."""
    await _setup_hub(hass)
    curtain, routine = await _service_case(hass)
    await async_apply_suggestion_preferences(hass, {"include_services": True})
    await hass.async_block_till_done()
    assert any(
        routine.id in link.settings["linked_device_ids"] for link in links(hass)
    ) or any(routine.id == link.settings["main_device_id"] for link in links(hass))

    await async_apply_suggestion_preferences(hass, {"include_services": False})
    await hass.async_block_till_done()
    assert not any(
        routine.id in link.settings["linked_device_ids"]
        or routine.id == link.settings["main_device_id"]
        for link in links(hass)
    )


async def test_confirming_a_suggestion_updates_the_others(hass) -> None:
    """Linking two devices from Discovered drops suggestions that use them."""
    await _setup_hub(hass)
    await _disable_auto_link(hass)
    speaker = _register(hass, domain="sonos", name="Kitchen Speaker", area="Kitchen")
    player = _register(hass, domain="apple_tv", name="Kitchen Speaker", area="Kitchen")
    other = _register(hass, domain="cast", name="Kitchen Speaker", area="Kitchen")
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()

    def flows():
        return {
            flow["context"]["unique_id"]: flow
            for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            if str(flow["context"].get("unique_id", "")).startswith("suggestion:")
        }

    first = suggestion_unique_id(speaker.id, player.id)
    stale = suggestion_unique_id(speaker.id, other.id)
    assert {first, stale} <= set(flows())
    stale_flow_id = flows()[stale]["flow_id"]

    result = await hass.config_entries.flow.async_configure(
        flows()[first]["flow_id"], user_input={"mac_address": "SN-0001"}
    )
    assert result["reason"] == "linked"
    await hass.async_block_till_done()

    # The scan after the new link offers the third speaker to join it, once.
    waiting = async_get_manager(hass).suggestions
    assert len(waiting) == 1
    join = waiting[0]
    assert join.join_device_id == other.id
    (link,) = links(hass)
    assert join.join_link_id == link.entry_id
    assert join.join_link_title == link.title
    join_flows = [
        flow for flow in flows().values() if flow["step_id"] == "discovery_join"
    ]
    assert len(join_flows) == 1
    assert len(flows()) == 1
    # The title names the integration, so two same-named devices differ.
    assert join_flows[0]["context"]["title_placeholders"]["name"] == (
        f"Add Kitchen Speaker (cast) to {link.title}"
    )
    shown = await hass.config_entries.flow.async_configure(join_flows[0]["flow_id"])
    assert shown["description_placeholders"]["why"] == "Their names are 100% alike."

    result = await hass.config_entries.flow.async_configure(
        join_flows[0]["flow_id"], user_input={}
    )
    assert result["reason"] == "linked"
    await hass.async_block_till_done()
    (link,) = links(hass)
    assert set(link.settings["linked_device_ids"]) == {speaker.id, player.id, other.id}
    assert link.settings["added_connections"][other.id] is True
    assert ("device_linker", "SN-0001") in dr.async_get(hass).async_get(
        other.id
    ).identifiers
    assert async_get_manager(hass).suggestions == []
    assert flows() == {}

    # A suggestion opened before the link was made stops: it no longer applies.
    assert stale_flow_id not in {flow["flow_id"] for flow in flows().values()}
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DISCOVERY},
        data={
            "unique_id": stale,
            "device_id_a": speaker.id,
            "device_id_b": other.id,
            "name_a": "Kitchen Speaker",
            "name_b": "Kitchen Speaker",
            "integration_a": "sonos",
            "integration_b": "cast",
            "reason": "name",
            "score": "100%",
            "mac": "",
            "anchor_device_id": "",
        },
    )
    if result["type"] == "form":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"mac_address": "SN-0002"}
        )
    assert result["reason"] == "already_linked"


async def test_no_join_from_an_integration_already_in_the_link(hass) -> None:
    """Two devices of one integration can't share a link's address."""
    await _setup_hub(hass)
    await _disable_auto_link(hass)
    speaker = _register(hass, domain="sonos", name="Den Speaker", area="Den")
    player = _register(hass, domain="apple_tv", name="Den Speaker", area="Den")
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()
    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("unique_id")
        == suggestion_unique_id(speaker.id, player.id)
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input={"mac_address": "SN-0003"}
    )
    assert result["reason"] == "linked"
    await hass.async_block_till_done()

    # A second Sonos device of the same config entry can't join.
    second = dr.async_get(hass).async_get_or_create(
        config_entry_id=speaker.config_entry_id,
        identifiers={("sonos", "den_speaker_2")},
        name="Den Speaker",
    )
    dr.async_get(hass).async_update_device(second.id, area_id=speaker.area_id)
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()
    assert not any(
        item.join_device_id == second.id for item in async_get_manager(hass).suggestions
    )


async def test_never_suggest_a_device_and_take_it_back(hass) -> None:
    """Decline a suggestion for good, then undo it from the settings."""
    hub = await _setup_hub(hass)
    await _disable_auto_link(hass)
    lamp = _register(hass, domain="shelly", name="Desk Lamp", area="Office")
    plug = _register(hass, domain="tplink", name="Desk Lamp", area="Office")
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()
    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("unique_id") == suggestion_unique_id(lamp.id, plug.id)
    )
    # No address needed to decline.
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input={"never_suggest": [plug.id]}
    )
    assert result["reason"] == "devices_ignored"
    assert result["description_placeholders"] == {"names": "Desk Lamp"}
    await hass.async_block_till_done()
    manager = async_get_manager(hass)
    assert manager.settings.ignored_devices == {plug.id}
    assert manager.suggestions == []
    assert links(hass) == []

    # The settings list shows it; removing it suggests the pair again.
    result = await hass.config_entries.options.async_init(hub.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    defaults = result["data_schema"]({})
    assert defaults["ignored_devices"] == [plug.id]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={**defaults, "ignored_devices": []}
    )
    await hass.async_block_till_done()
    assert manager.settings.ignored_devices == frozenset()
    assert any(
        {item.device_id_a, item.device_id_b} == {lamp.id, plug.id}
        for item in manager.suggestions
    )


async def test_same_named_devices_join_a_group_home_assistant_linked(hass) -> None:
    """UniFi and ESPHome share a MAC; Matter and Cloud lamps join that group."""
    await _setup_hub(hass)
    mac = "02:00:00:00:00:71"
    unifi = _register(
        hass,
        domain="unifi",
        name="Guest Bedroom Left Lamp",
        area="Guest Bedroom",
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
    )
    esphome = _register(
        hass,
        domain="esphome",
        name="Guest Bedroom Left Lamp (ESPHome)",
        area="Guest Bedroom",
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
    )
    matter = _register(
        hass, domain="matter", name="Guest Bedroom Left Lamp", area="Guest Bedroom"
    )
    cloud = _register(
        hass,
        domain="switchbot_cloud",
        name="Guest Bedroom Left Lamp (Cloud)",
        area="Guest Bedroom",
    )
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()

    # The same name links on its own, through UniFi's address.
    (link,) = links(hass)
    assert link.settings["main_device_id"] == unifi.id
    assert set(link.settings["linked_device_ids"]) == {matter.id, cloud.id}
    assert link.settings["mac_address"] == mac
    registry = dr.async_get(hass)
    for device in (matter, cloud):
        assert (dr.CONNECTION_NETWORK_MAC, mac) in registry.async_get(
            device.id
        ).connections
    # The ESPHome lamp already has the address; it isn't added to the link.
    assert esphome.id not in link.settings["linked_device_ids"]
    assert async_get_manager(hass).suggestions == []


async def test_same_names_with_no_address_link_through_a_shared_id(hass) -> None:
    """A fan behind a Bluetooth hub and its Cloud copy share an ID from the name."""
    await _setup_hub(hass)
    ble = _register(
        hass, domain="switchbot", name="Office Ceiling Fan (BLE)", area="Office"
    )
    cloud = _register(
        hass, domain="switchbot_cloud", name="Office Ceiling Fan (Cloud)", area="Office"
    )
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()
    (link,) = links(hass)
    assert link.settings["mac_address"] == "office-ceiling-fan"
    assert link.settings["main_device_id"] is None
    assert set(link.settings["linked_device_ids"]) == {ble.id, cloud.id}
    assert link.title == "Office Ceiling Fan (2 Devices Linked)"
    registry = dr.async_get(hass)
    for device in (ble, cloud):
        assert ("device_linker", "office-ceiling-fan") in registry.async_get(
            device.id
        ).identifiers


async def test_without_automatic_links_the_suggestion_fills_in_the_id(hass) -> None:
    await _setup_hub(hass)
    await _disable_auto_link(hass)
    ble = _register(
        hass, domain="switchbot", name="Office Ceiling Fan (BLE)", area="Office"
    )
    cloud = _register(
        hass, domain="switchbot_cloud", name="Office Ceiling Fan (Cloud)", area="Office"
    )
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()
    assert links(hass) == []
    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("unique_id") == suggestion_unique_id(ble.id, cloud.id)
    )
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["data_schema"]({})["mac_address"] == "office-ceiling-fan"
