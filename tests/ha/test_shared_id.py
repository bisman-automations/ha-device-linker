"""Links that share an ID other than a MAC, already-linked devices, and link devices."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.const import (
    CONF_ADDED_CONNECTIONS,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    CONF_SELECTED_MAC_ADDRESS,
    DOMAIN,
    SERVICE_RESCAN,
)
from custom_components.device_linker.manager import async_get_manager
from custom_components.device_linker.registry import normalize_link_value
from homeassistant.helpers import device_registry as dr
from tests.ha.helpers import links, remove_link
from tests.ha.test_config_flow import _add_link, _devices, _setup_hub

VIN = "1FTEST00000000001"
MAC = "aa:bb:cc:dd:ee:50"


def test_normalize_link_value() -> None:
    """MAC addresses are normalized, other IDs kept, half a MAC refused."""
    assert normalize_link_value("AA-BB-CC-DD-EE-50") == MAC
    assert normalize_link_value("aabbccddee50") == MAC
    assert normalize_link_value(f"  {VIN} ") == VIN
    assert normalize_link_value("aa:bb:cc") is None
    assert normalize_link_value("") is None
    assert normalize_link_value("x" * 101) is None


async def test_a_vin_links_two_devices_without_a_mac(hass) -> None:
    """Typing an ID that isn't a MAC adds it to every device, the main one included."""
    car, assistant = _devices(
        hass,
        ("car_integration", "Family Car", None),
        ("helper_integration", "Family Car Helper", None),
    )
    hub = await _setup_hub(hass)
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: car.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [assistant.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAC_ADDRESS: VIN}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Link Family Car to 2 devices"
    await hass.async_block_till_done()

    (link,) = links(hass)
    assert link.settings[CONF_MAIN_DEVICE_ID] is None
    assert link.settings[CONF_LINKED_DEVICE_IDS] == [car.id, assistant.id]
    assert link.settings[CONF_MAC_ADDRESS] == VIN
    assert link.settings[CONF_ADDED_CONNECTIONS] == {car.id: True, assistant.id: True}
    registry = dr.async_get(hass)
    for device in (car, assistant):
        assert (DOMAIN, VIN) in registry.async_get(device.id).identifiers
    # Home Assistant now shows them as linked devices.
    linked = {
        device.id for device in registry.async_get_devices(identifiers={(DOMAIN, VIN)})
    }
    assert {car.id, assistant.id} <= linked

    await remove_link(hass, link)
    for device in (car, assistant):
        assert (DOMAIN, VIN) not in registry.async_get(device.id).identifiers


async def test_a_half_typed_mac_is_refused(hass) -> None:
    car, assistant = _devices(
        hass,
        ("car_integration", "Family Car", None),
        ("helper_integration", "Family Car Helper", None),
    )
    hub = await _setup_hub(hass)
    result = await _add_link(hass, hub)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAIN_DEVICE_ID: car.id}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_LINKED_DEVICE_IDS: [assistant.id]}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={CONF_MAC_ADDRESS: "aa:bb:cc"}
    )
    assert result["errors"] == {"base": "invalid_mac"}


async def test_devices_sharing_an_identifier_are_not_suggested(hass) -> None:
    """The Ford case: both integrations use the VIN, so they're linked already."""
    await _setup_hub(hass)
    car_config = MockConfigEntry(domain="car_integration")
    car_config.add_to_hass(hass)
    helper_config = MockConfigEntry(domain="helper_integration")
    helper_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    car = registry.async_get_or_create(
        config_entry_id=car_config.entry_id,
        identifiers={("car_integration", VIN)},
        name="2022 Explorer",
    )
    helper = registry.async_get_or_create(
        config_entry_id=helper_config.entry_id,
        identifiers={("helper_integration", VIN), ("car_integration", VIN)},
        name="2022 Explorer",
    )
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()
    manager = async_get_manager(hass)
    assert not any(
        {item.device_id_a, item.device_id_b} == {car.id, helper.id}
        for item in manager.suggestions
    )
    assert links(hass) == []


async def test_each_link_has_a_device_named_after_its_main_device(hass) -> None:
    """The link's own device sits under the link and is linked to its devices."""
    main, target = _devices(
        hass,
        ("main_integration", "Water Heater Leak Sensor", MAC),
        ("target_integration", "Leak Sensor (matter)", None),
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
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    (link,) = links(hass)
    registry = dr.async_get(hass)
    (device,) = [
        device
        for device in dr.async_entries_for_config_entry(registry, hub.entry_id)
        if device.config_subentry_id == link.entry_id
    ]
    assert device.name == "Water Heater Leak Sensor"
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert (dr.CONNECTION_NETWORK_MAC, MAC) in device.connections
    shown = {
        other.id
        for other in registry.async_get_devices(connections=device.connections)
        if other.id != device.id
    }
    assert shown == {main.id, target.id}

    # Renaming the main device renames the link's device.
    registry.async_update_device(main.id, name_by_user="Basement Leak Sensor")
    await hass.async_block_till_done()
    assert registry.async_get(device.id).name == "Basement Leak Sensor"

    await remove_link(hass, link)
    assert registry.async_get(device.id) is None
