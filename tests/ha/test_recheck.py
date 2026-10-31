"""Rechecking automatic links, and links you've confirmed."""

from types import MappingProxyType

from custom_components.device_linker.const import SUBENTRY_LINK
from custom_components.device_linker.manager import async_get_manager, link_payload
from custom_components.device_linker.matching import pair_key
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigSubentry
from homeassistant.helpers import device_registry as dr
from tests.ha.helpers import links
from tests.ha.test_config_flow import _setup_hub
from tests.ha.test_suggestions import _disable_auto_link, _register

TV_MAC = "02:00:00:00:00:61"
SPEAKER_MAC = "02:00:00:00:00:62"
LAMP_MAC = "02:00:00:00:00:63"


def _auto_link(hass, hub, main, other, mac, *, confirmed=False):
    """An automatic link, as an earlier scan would have made it."""
    hass.config_entries.async_add_subentry(
        hub,
        ConfigSubentry(
            data=MappingProxyType(
                link_payload(
                    main.id, [other.id], mac, {other.id: True}, "auto", confirmed
                )
            ),
            subentry_type=SUBENTRY_LINK,
            title="Old title",
            unique_id=f"{main.id}_{mac}",
        ),
    )


async def _edit(hass, hub, subentry_id):
    return await hass.config_entries.subentries.async_init(
        (hub.entry_id, SUBENTRY_LINK),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )


async def _open_recheck(hass, hub):
    result = await hass.config_entries.options.async_init(hub.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "recheck"}
    )


async def test_recheck_lists_only_what_the_rules_would_not_link(hass) -> None:
    hub = await _setup_hub(hass)
    await _disable_auto_link(hass)
    # Linked by looser rules: a TV and an Apple TV are different devices.
    tv = _register(
        hass,
        domain="unifi",
        name="Garage TV",
        area="Garage",
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
    )
    apple = _register(hass, domain="apple_tv", name="Garage Apple TV", area="Garage")
    # Still the same name: stays.
    speaker = _register(
        hass,
        domain="unifi",
        name="Den Speaker",
        area="Den",
        connections={(dr.CONNECTION_NETWORK_MAC, SPEAKER_MAC)},
    )
    sonos = _register(hass, domain="sonos", name="Den Speaker", area="Den")
    # A mismatch you confirmed: never listed.
    lamp = _register(
        hass,
        domain="unifi",
        name="Office Lamp",
        area="Office",
        connections={(dr.CONNECTION_NETWORK_MAC, LAMP_MAC)},
    )
    media = _register(hass, domain="cast", name="Office Media", area="Office")
    _auto_link(hass, hub, tv, apple, TV_MAC)
    _auto_link(hass, hub, speaker, sonos, SPEAKER_MAC)
    _auto_link(hass, hub, lamp, media, LAMP_MAC, confirmed=True)
    await hass.async_block_till_done()
    assert len(links(hass)) == 3

    result = await _open_recheck(hass, hub)
    assert result["step_id"] == "recheck"
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == "unlink")
    options = schema[key].config["options"]
    assert [option["label"] for option in options] == [
        "Garage Apple TV (apple_tv) — in Garage TV (2 Devices Linked)"
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"unlink": [options[0]["value"]]}
    )
    assert result["reason"] == "rechecked"
    assert result["description_placeholders"] == {"removed": "1"}
    await hass.async_block_till_done()
    # The link had nothing else, so it's gone, with the address it copied.
    assert {link.settings["main_device_id"] for link in links(hass)} == {
        speaker.id,
        lamp.id,
    }
    assert (dr.CONNECTION_NETWORK_MAC, TV_MAC) not in dr.async_get(hass).async_get(
        apple.id
    ).connections
    # Remembered, so it isn't linked again.
    assert pair_key(tv.id, apple.id) in async_get_manager(hass).settings.unlinked

    result = await _open_recheck(hass, hub)
    assert result["reason"] == "nothing_to_recheck"


async def test_links_you_make_start_confirmed(hass) -> None:
    hub = await _setup_hub(hass)
    await _disable_auto_link(hass)
    tv = _register(
        hass,
        domain="unifi",
        name="Garage TV",
        area="Garage",
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
    )
    apple = _register(hass, domain="apple_tv", name="Garage Apple TV", area="Garage")
    _auto_link(hass, hub, tv, apple, TV_MAC)
    await hass.async_block_till_done()
    (link,) = links(hass)
    assert link.settings["confirmed"] is False

    # Ticking Confirmed in Edit link keeps it out of the recheck.
    result = await _edit(hass, hub, link.entry_id)
    defaults = result["data_schema"]({})
    assert defaults["confirmed"] is False
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "linked_device_ids": [tv.id, apple.id],
            "selected_mac_address": TV_MAC,
            "confirmed": True,
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    (link,) = links(hass)
    assert link.settings["confirmed"] is True
    result = await _open_recheck(hass, hub)
    assert result["reason"] == "nothing_to_recheck"

    # Removing a device from a confirmed link is refused.
    result = await _edit(hass, hub, link.entry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"linked_device_ids": [tv.id], "selected_mac_address": TV_MAC},
    )
    assert result["errors"] == {"base": "confirmed_locked"}
