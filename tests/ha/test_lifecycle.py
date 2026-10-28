"""Setup, unload, and removal of the shared MAC connection."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.const import (
    CONF_ADDED_CONNECTIONS,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    DOMAIN,
)
from homeassistant.helpers import device_registry as dr

MAC = "aa:bb:cc:dd:ee:ff"
CONNECTION = (dr.CONNECTION_NETWORK_MAC, MAC)


def _devices(hass):
    """Register a main device that has the MAC and a target that does not."""
    main_config = MockConfigEntry(domain="main_integration")
    main_config.add_to_hass(hass)
    target_config = MockConfigEntry(domain="target_integration")
    target_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    main = registry.async_get_or_create(
        config_entry_id=main_config.entry_id,
        identifiers={("main_integration", "main")},
        connections={CONNECTION},
        name="Main device",
    )
    target = registry.async_get_or_create(
        config_entry_id=target_config.entry_id,
        identifiers={("target_integration", "target")},
        name="Target device",
    )
    return registry, main, target


def _entry(hass, main_id, target_id, *, added: bool):
    """A current-format Device Linker entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAIN_DEVICE_ID: main_id,
            CONF_LINKED_DEVICE_IDS: [target_id],
            CONF_MAC_ADDRESS: MAC,
            CONF_ADDED_CONNECTIONS: {target_id: added},
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_adds_an_owned_connection_and_unload_keeps_it(hass) -> None:
    """Add the MAC on setup, and leave it in place when the entry unloads."""
    registry, main, target = _devices(hass)
    entry = _entry(hass, main.id, target.id, added=True)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert CONNECTION in registry.async_get(target.id).connections

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert CONNECTION in registry.async_get(target.id).connections


async def test_removal_deletes_only_a_connection_this_entry_added(hass) -> None:
    """Removing the entry drops a MAC it added, and leaves one it did not."""
    registry, main, target = _devices(hass)
    owned = _entry(hass, main.id, target.id, added=True)
    assert await hass.config_entries.async_setup(owned.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(owned.entry_id)
    await hass.async_block_till_done()
    assert CONNECTION not in registry.async_get(target.id).connections

    # The address was already on the device, and this entry does not own it.
    registry.async_update_device(
        target.id,
        new_connections=registry.async_get(target.id).connections | {CONNECTION},
    )
    preexisting = _entry(hass, main.id, target.id, added=False)
    assert await hass.config_entries.async_setup(preexisting.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_remove(preexisting.entry_id)
    await hass.async_block_till_done()
    assert CONNECTION in registry.async_get(target.id).connections


async def test_registry_refresh_restores_an_owned_connection(hass) -> None:
    """Put an owned MAC back when another integration rewrites the device."""
    registry, main, target = _devices(hass)
    entry = _entry(hass, main.id, target.id, added=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry.async_update_device(
        target.id,
        new_connections=registry.async_get(target.id).connections - {CONNECTION},
    )
    await hass.async_block_till_done()

    assert CONNECTION in registry.async_get(target.id).connections
