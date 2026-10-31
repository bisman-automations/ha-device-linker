"""Devices of chosen integrations can be in more than one link."""

from custom_components.device_linker.const import (
    CONF_LINKED_DEVICE_IDS,
    CONF_MAIN_DEVICE_ID,
    CONF_SELECTED_MAC_ADDRESS,
    SUBENTRY_LINK,
)
from custom_components.device_linker.manager import (
    async_apply_suggestion_preferences,
    async_load_settings,
)
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.helpers import device_registry as dr
from tests.ha.helpers import links
from tests.ha.test_config_flow import _add_link, _devices, _offered, _setup_hub
from tests.ha.test_suggestions import _disable_auto_link

RIGHT_MAC = "c2:00:00:00:00:01"
LEFT_MAC = "ee:00:00:00:00:02"


async def _link(hass, hub, main, devices, mac):
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: main.id}
    )
    assert result["step_id"] == "devices"
    offered = _offered(result, CONF_LINKED_DEVICE_IDS)
    if not {d.id for d in devices} <= offered:
        return result
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={CONF_LINKED_DEVICE_IDS: [d.id for d in devices]},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_SELECTED_MAC_ADDRESS: mac}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    return result


async def _curtains(hass):
    hub = await _setup_hub(hass)
    await _disable_auto_link(hass)
    right, left, pair = _devices(
        hass,
        ("switchbot", "Right Curtain", RIGHT_MAC.upper()),
        ("switchbot", "Left Curtain", LEFT_MAC.upper()),
        ("matter", "Curtains", None),
    )
    await _link(hass, hub, right, [pair], RIGHT_MAC)
    return hub, right, left, pair


async def test_a_linked_device_is_not_offered_again_by_default(hass) -> None:
    hub, _right, left, pair = await _curtains(hass)
    _devices(hass, ("hue", "Desk Lamp", None))
    result = await _link(hass, hub, left, [pair], LEFT_MAC)
    assert result["step_id"] == "devices"
    assert pair.id not in _offered(result, CONF_LINKED_DEVICE_IDS)
    assert "Curtains — in" in result["description_placeholders"]["hidden"]


async def test_a_multi_link_device_joins_a_second_link(hass) -> None:
    hub, right, left, pair = await _curtains(hass)
    await async_apply_suggestion_preferences(
        hass, {"multi_link_integrations": ["Matter"]}
    )
    assert (await async_load_settings(hass)).multi_link_integrations == ("matter",)

    await _link(hass, hub, left, [pair], LEFT_MAC)
    assert len(links(hass)) == 2
    registry = dr.async_get(hass)
    macs = {
        value
        for kind, value in registry.async_get(pair.id).connections
        if kind == dr.CONNECTION_NETWORK_MAC
    }
    assert macs == {RIGHT_MAC, LEFT_MAC}

    # Editing the second link offers only its own address, not the first
    # link's address the shared device carries.
    second = next(
        link for link in links(hass) if link.settings[CONF_MAIN_DEVICE_ID] == left.id
    )
    result = await hass.config_entries.subentries.async_init(
        (hub.entry_id, SUBENTRY_LINK),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": second.entry_id},
    )
    assert _offered(result, CONF_SELECTED_MAC_ADDRESS) == {LEFT_MAC}

    # Deleting the second link takes only its own address off.
    hass.config_entries.async_remove_subentry(hub, second.entry_id)
    await hass.async_block_till_done()
    macs = {
        value
        for kind, value in registry.async_get(pair.id).connections
        if kind == dr.CONNECTION_NETWORK_MAC
    }
    assert macs == {RIGHT_MAC}
    assert [link.settings[CONF_MAIN_DEVICE_ID] for link in links(hass)] == [right.id]
