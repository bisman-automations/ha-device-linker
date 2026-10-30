"""Cleaning up addresses left behind by links that no longer exist."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.const import (
    CONF_LINKED_DEVICE_IDS,
    CONF_MAIN_DEVICE_ID,
    CONF_SELECTED_MAC_ADDRESS,
    DOMAIN,
)
from custom_components.device_linker.manager import async_remember_unlinked
from custom_components.device_linker.matching import pair_key
from homeassistant.helpers import device_registry as dr
from tests.ha.test_config_flow import _add_link, _devices, _setup_hub

MAC = "aa:bb:cc:dd:ee:60"
VIN = "1FTEST00000000001"


def _device(hass, domain, name, *, mac=None, identifiers=()):
    config = MockConfigEntry(domain=domain)
    config.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id,
        identifiers={(domain, name), *identifiers},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        name=name,
    )


async def _open_cleanup(hass, hub):
    result = await hass.config_entries.options.async_init(hub.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "cleanup"}
    )


async def test_nothing_to_clean_up(hass) -> None:
    hub = await _setup_hub(hass)
    result = await _open_cleanup(hass, hub)
    assert result["type"] == "abort"
    assert result["reason"] == "no_leftovers"


async def test_a_mac_left_by_an_untracked_old_link_can_be_removed(hass) -> None:
    """The case from the multi-select build: deleted, but the MAC stayed."""
    hub = await _setup_hub(hass)
    unifi = _device(hass, "unifi", "Leak Sensor", mac=MAC)
    matter = _device(hass, "matter", "Leak Sensor (matter)", mac=MAC)
    await async_remember_unlinked(hass, {pair_key(unifi.id, matter.id)})

    result = await _open_cleanup(hass, hub)
    assert result["step_id"] == "cleanup"
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == "leftovers")
    labels = {o["value"]: o["label"] for o in schema[key].config["options"]}
    assert labels[f"{matter.id}|{MAC}"] == (
        f"Leak Sensor (matter) (matter): {MAC} — also on Leak Sensor"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"leftovers": [f"{matter.id}|{MAC}"]}
    )
    assert result["type"] == "create_entry"
    registry = dr.async_get(hass)
    assert (dr.CONNECTION_NETWORK_MAC, MAC) not in registry.async_get(
        matter.id
    ).connections
    # The device that registered it keeps it.
    assert (dr.CONNECTION_NETWORK_MAC, MAC) in registry.async_get(unifi.id).connections


async def test_a_left_over_shared_id_is_listed_without_any_history(hass) -> None:
    """Only Device Linker adds its own identifiers, so one with no link is a leftover."""
    hub = await _setup_hub(hass)
    car = _device(hass, "car", "Family Car", identifiers={(DOMAIN, VIN)})
    _device(hass, "helper", "Family Car Helper", identifiers={(DOMAIN, VIN)})
    result = await _open_cleanup(hass, hub)
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == "leftovers")
    values = {o["value"] for o in schema[key].config["options"]}
    assert f"{car.id}|{VIN}" in values
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"leftovers": [f"{car.id}|{VIN}"]}
    )
    assert (DOMAIN, VIN) not in dr.async_get(hass).async_get(car.id).identifiers


async def test_addresses_a_current_link_uses_are_not_listed(hass) -> None:
    main, target = _devices(
        hass,
        ("main_integration", "Main device", MAC),
        ("target_integration", "Target device", None),
    )
    hub = await _setup_hub(hass)
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: main.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [target.id]}
    )
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_SELECTED_MAC_ADDRESS: MAC}
    )
    await hass.async_block_till_done()
    await async_remember_unlinked(hass, {pair_key(main.id, target.id)})
    result = await _open_cleanup(hass, hub)
    assert result["reason"] == "no_leftovers"
