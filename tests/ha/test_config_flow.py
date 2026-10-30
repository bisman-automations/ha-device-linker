"""Setting up the hub, Add link and Edit link, and the hub's settings."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.config_flow import MANUAL_ENTRY
from custom_components.device_linker.const import (
    CONF_ADDED_CONNECTIONS,
    CONF_AUTO_LINK_SIMILAR,
    CONF_DEVICE_ID,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_SELECTED_MAC_ADDRESS,
    CONF_TARGET_DEVICES,
    DOMAIN,
    SUBENTRY_LINK,
)
from custom_components.device_linker.manager import async_get_manager
from custom_components.device_linker.registry import hub_entry
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import device_registry as dr
from tests.ha.helpers import links

MAC = "aa:bb:cc:dd:ee:ff"


async def _setup_hub(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["title"] == "Device Linker"
    await hass.async_block_till_done()
    return hub_entry(hass)


async def _add_link(hass, hub):
    return await hass.config_entries.subentries.async_init(
        (hub.entry_id, SUBENTRY_LINK), context={"source": SOURCE_USER}
    )


def _devices(hass, *specs):
    registry = dr.async_get(hass)
    devices = []
    for domain, name, mac in specs:
        config = MockConfigEntry(domain=domain)
        config.add_to_hass(hass)
        devices.append(
            registry.async_get_or_create(
                config_entry_id=config.entry_id,
                identifiers={(domain, name)},
                connections={(dr.CONNECTION_NETWORK_MAC, mac)} if mac else set(),
                name=name,
            )
        )
    return devices


async def test_device_linker_is_set_up_once(hass) -> None:
    hub = await _setup_hub(hass)
    assert hub is not None
    assert hub.subentries == {}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_add_link_links_several_devices_to_the_main_mac(hass) -> None:
    """Add link: main device, several devices, then its registered MAC."""
    main, target, second = _devices(
        hass,
        ("main_integration", "Main device", "AA:BB:CC:DD:EE:FF"),
        ("target_integration", "Target device", None),
        ("second_target_integration", "Second target device", None),
    )
    hub = await _setup_hub(hass)

    result = await _add_link(hass, hub)
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: main.id}
    )
    assert result["step_id"] == "devices"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [target.id, second.id]}
    )
    assert result["step_id"] == "confirm"
    names = result["description_placeholders"]["linked_device_names"]
    assert "Target device (target_integration)" in names
    assert "Second target device (second_target_integration)" in names
    assert result["data_schema"]({})[CONF_SELECTED_MAC_ADDRESS] == MAC

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_SELECTED_MAC_ADDRESS: MAC}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Link Main device to 2 devices"
    await hass.async_block_till_done()

    (link,) = links(hass)
    assert link.settings[CONF_MAIN_DEVICE_ID] == main.id
    assert link.settings[CONF_LINKED_DEVICE_IDS] == [target.id, second.id]
    assert link.settings[CONF_MAC_ADDRESS] == MAC
    registry = dr.async_get(hass)
    for device in (target, second):
        assert (dr.CONNECTION_NETWORK_MAC, MAC) in registry.async_get(
            device.id
        ).connections

    # A device that is already linked to the main device is refused, by name.
    (third,) = _devices(hass, ("third_integration", "Third device", None))
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: main.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [target.id]}
    )
    assert result["step_id"] == "devices"
    assert result["errors"] == {"base": "already_linked"}
    assert result["description_placeholders"]["already_linked"] == (
        "Target device (target_integration)"
    )

    # The same main device and MAC can't be added twice.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [third.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_SELECTED_MAC_ADDRESS: MAC}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_manual_mac_entry_links_a_device(hass) -> None:
    """Manual MAC entry stays available when no device has the address yet."""
    (target,) = _devices(hass, ("target_integration", "Target device", None))
    hub = await _setup_hub(hass)

    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: MANUAL_ENTRY}
    )
    assert result["step_id"] == "devices"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [target.id]}
    )
    assert result["step_id"] == "confirm"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    (link,) = links(hass)
    assert link.settings[CONF_MAIN_DEVICE_ID] is None
    assert link.settings[CONF_LINKED_DEVICE_IDS] == [target.id]
    assert link.settings[CONF_MAC_ADDRESS] == MAC
    assert link.settings[CONF_ADDED_CONNECTIONS] == {target.id: True}
    assert link.settings["origin"] == "manual"


async def test_legacy_multiselect_entry_becomes_an_editable_link(hass) -> None:
    """An entry from the earlier multi-select build moves into the hub."""
    main, target = _devices(
        hass,
        ("main_integration", "Main device", "AA:BB:CC:DD:EE:FF"),
        ("target_integration", "Target device", None),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy device group",
        data={
            CONF_DEVICE_ID: main.id,
            CONF_TARGET_DEVICES: [target.id],
            CONF_MAC_ADDRESS: MAC,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hub = hub_entry(hass)
    assert hub.entry_id == entry.entry_id
    assert hub.title == "Device Linker"
    (link,) = links(hass)
    assert link.title == "Legacy device group"
    assert link.settings[CONF_MAIN_DEVICE_ID] == main.id
    # That build didn't record which MAC it added, so none is owned.
    assert link.settings[CONF_ADDED_CONNECTIONS] == {target.id: False}

    result = await hub.start_subentry_reconfigure_flow(hass, link.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"


async def test_mac_conflict_names_the_device_already_using_it(hass) -> None:
    """Name the other device of that integration that already has the MAC."""
    main, target = _devices(
        hass,
        ("main_integration", "Main device", "AA:BB:CC:DD:EE:FF"),
        ("target_integration", "Selected target", None),
    )
    dr.async_get(hass).async_get_or_create(
        config_entry_id=target.config_entry_id,
        identifiers={("target_integration", "already_linked")},
        connections={(dr.CONNECTION_NETWORK_MAC, "AA:BB:CC:DD:EE:FF")},
        name="Existing MAC device",
    )
    hub = await _setup_hub(hass)

    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: main.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [target.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_SELECTED_MAC_ADDRESS: MAC}
    )
    assert result["errors"]["base"] == "connection_conflict"
    assert (
        "Existing MAC device (target_integration)"
        in result["description_placeholders"]["conflicting_devices"]
    )


async def test_settings_are_on_the_hub(hass) -> None:
    """Configure holds the settings, including similar names."""
    hub = await _setup_hub(hass)
    manager = async_get_manager(hass)
    assert manager.settings.auto_link_similar is True

    result = await hass.config_entries.options.async_init(hub.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["settings", "cleanup"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_AUTO_LINK_SIMILAR: False}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert manager.settings.auto_link_similar is False
