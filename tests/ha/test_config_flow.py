"""Setting up the hub, Add link and Edit link, and the hub's settings."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.config_flow import (
    CONF_LINK_ID,
    JOIN_LINK,
    MANUAL_ENTRY,
)
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
from custom_components.device_linker.manager import (
    async_apply_suggestion_preferences,
    async_get_manager,
)
from custom_components.device_linker.registry import hub_entry
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
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


def _offered(result, field) -> set[str]:
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == field)
    return {option["value"] for option in schema[key].config["options"]}


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
    assert result["title"] == "Main device (3 Devices Linked)"
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

    # Devices already in a link aren't offered again when adding a link.
    result = await _add_link(hass, hub)
    assert result["reason"] == "no_devices"  # every device is in a link
    (sixth,) = _devices(hass, ("sixth_integration", "Sixth device", None))
    result = await _add_link(hass, hub)
    offered = _offered(result, CONF_MAIN_DEVICE_ID)
    assert MANUAL_ENTRY in offered
    assert not offered & {main.id, target.id, second.id}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: MANUAL_ENTRY}
    )
    assert result["step_id"] == "devices"
    assert not _offered(result, CONF_LINKED_DEVICE_IDS) & {
        main.id,
        target.id,
        second.id,
    }

    # Devices Home Assistant already links (a shared MAC) aren't offered either.
    fourth, fifth = _devices(
        hass,
        ("fourth_integration", "Fourth device", "11:22:33:44:55:66"),
        ("fifth_integration", "Fifth device", "11:22:33:44:55:66"),
    )
    result = await _add_link(hass, hub)
    # They can be the main device, sharing their MAC with more devices.
    assert {fourth.id, fifth.id} <= _offered(result, CONF_MAIN_DEVICE_ID)
    # The form names what it left out, and why: devices in a link.
    hidden = result["description_placeholders"]["hidden"]
    assert hidden.startswith("3 devices aren't listed because they're already linked:")
    assert "Main device — in Main device (3 Devices Linked)" in hidden
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: MANUAL_ENTRY}
    )
    # But not as a device to link: they're already linked.
    assert not _offered(result, CONF_LINKED_DEVICE_IDS) & {fourth.id, fifth.id}
    assert (
        "Fourth device — linked by Home Assistant"
        in (result["description_placeholders"]["hidden"])
    )

    # Editing a link still lists its own devices, but not another link's.
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: MANUAL_ENTRY}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [sixth.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAC_ADDRESS: "22:33:44:55:66:77"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    first = next(link for link in links(hass) if link.settings[CONF_MAIN_DEVICE_ID])
    result = await hass.config_entries.subentries.async_init(
        (hub.entry_id, SUBENTRY_LINK),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": first.entry_id},
    )
    assert result["step_id"] == "reconfigure"
    offered = _offered(result, CONF_LINKED_DEVICE_IDS)
    assert {main.id, target.id, second.id} <= offered
    assert sixth.id not in offered


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
    # Renamed to the current form after the move.
    assert link.title == "Main device (2 Devices Linked)"
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

    # The main device already shares that MAC, so it isn't offered; type it.
    result = await _add_link(hass, hub)
    assert main.id in _offered(result, CONF_MAIN_DEVICE_ID)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: MANUAL_ENTRY}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [target.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAC_ADDRESS: MAC}
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
    assert result["menu_options"] == ["settings", "recheck", "cleanup"]
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


async def test_add_devices_to_an_existing_link(hass) -> None:
    """Add link can add devices to a link that's already there."""
    main, target, newcomer = _devices(
        hass,
        ("main_integration", "Main device", "AA:BB:CC:DD:EE:FF"),
        ("target_integration", "Target device", None),
        ("new_integration", "New device", None),
    )
    hub = await _setup_hub(hass)

    # No links yet: the choice isn't offered.
    result = await _add_link(hass, hub)
    assert JOIN_LINK not in _offered(result, CONF_MAIN_DEVICE_ID)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: main.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [target.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_SELECTED_MAC_ADDRESS: MAC}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    (link,) = links(hass)

    # A second device of the target's integration can't share the address.
    same_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=target.config_entry_id,
        identifiers={("target_integration", "second")},
        name="Second target device",
    )

    result = await _add_link(hass, hub)
    assert JOIN_LINK in _offered(result, CONF_MAIN_DEVICE_ID)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: JOIN_LINK}
    )
    assert result["step_id"] == "join"
    assert _offered(result, CONF_LINK_ID) == {link.entry_id}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINK_ID: link.entry_id}
    )
    assert result["step_id"] == "join_devices"
    assert result["description_placeholders"]["link"] == link.title
    offered = _offered(result, CONF_LINKED_DEVICE_IDS)
    assert newcomer.id in offered
    assert not offered & {main.id, target.id, same_entry.id}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [newcomer.id]}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "joined"
    await hass.async_block_till_done()

    (link,) = links(hass)
    assert link.settings[CONF_MAIN_DEVICE_ID] == main.id
    assert link.settings[CONF_LINKED_DEVICE_IDS] == [target.id, newcomer.id]
    assert link.settings[CONF_ADDED_CONNECTIONS][newcomer.id] is True
    assert link.title == "Main device (3 Devices Linked)"
    assert (dr.CONNECTION_NETWORK_MAC, MAC) in dr.async_get(hass).async_get(
        newcomer.id
    ).connections


async def test_hidden_note_names_five_then_counts(hass) -> None:
    """Past five devices, the note says how many more."""
    hub = await _setup_hub(hass)
    for number in range(4):
        _devices(
            hass,
            (f"left_{number}", f"Device {number}A", f"02:00:00:00:00:0{number}"),
            (f"right_{number}", f"Device {number}B", f"02:00:00:00:00:0{number}"),
        )
    _devices(hass, ("free_integration", "Free device", None))
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: MANUAL_ENTRY}
    )
    hidden = result["description_placeholders"]["hidden"]
    assert hidden.startswith("8 devices aren't listed")
    assert hidden.endswith("and 3 more.")
    assert "Device 0A — linked by Home Assistant" in hidden


async def test_a_link_is_named_after_its_most_trusted_device(hass) -> None:
    """The preferred MAC source names the link; a "(BLE)" tag is dropped."""
    hub_device, client = _devices(
        hass,
        ("switchbot", "Den Hub (BLE)", "AA:BB:CC:DD:EE:70"),
        ("unifi", "Den Hub Client", None),
    )
    hub = await _setup_hub(hass)
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: hub_device.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [client.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_SELECTED_MAC_ADDRESS: "aa:bb:cc:dd:ee:70"}
    )
    await hass.async_block_till_done()
    # UniFi is trusted first out of the box, so its device names the link.
    (link,) = links(hass)
    assert link.title == "Den Hub Client (2 Devices Linked)"

    # Put SwitchBot first: the main device names it, without "(BLE)".
    await async_apply_suggestion_preferences(
        hass, {"preferred_mac_sources": ["switchbot"]}
    )
    await hass.async_block_till_done()
    (link,) = links(hass)
    assert link.title == "Den Hub (2 Devices Linked)"
