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


async def test_linked_devices_are_not_suggested(hass) -> None:
    """Two devices that share a MAC aren't paired with a third of the same name."""
    await _setup_hub(hass)
    await _disable_auto_link(hass)
    first = _register(
        hass,
        domain="unifi",
        name="Kitchen Speaker",
        area="Kitchen",
        connections={(dr.CONNECTION_NETWORK_MAC, SHARED)},
    )
    second = _register(
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
    assert frozenset((first.id, third.id)) not in suggested
    assert frozenset((second.id, third.id)) not in suggested
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
