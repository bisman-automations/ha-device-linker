"""Setup, unload, and removal of the shared MAC connection."""

import logging

import pytest
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


@pytest.mark.skipif(
    not hasattr(dr.DeviceRegistry, "async_get_or_create_child"),
    reason="Child devices arrived in Home Assistant 2026.11",
)
async def test_a_linked_device_that_becomes_a_child_device_is_left_alone(
    hass, caplog
) -> None:
    """An integration turning a linked device into a child device isn't an error.

    A child device has no connections, and only the main device API can
    change them, so Device Linker skips it instead of trying to put the MAC
    back (which Home Assistant refuses).
    """
    registry, main, target = _devices(hass)
    entry = _entry(hass, main.id, target.id, added=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert CONNECTION in registry.async_get(target.id).connections

    caplog.clear()
    # The integration converts a device it registered on an earlier run;
    # forget that this run registered it, as after a restart.
    registry._live_device_ids.pop(target.config_entry_id, None)  # noqa: SLF001
    parent = registry.async_get_or_create(
        config_entry_id=target.config_entry_id,
        identifiers={("target_integration", "hub")},
        name="Target hub",
    )
    child = registry.async_get_or_create_child(
        config_entry_id=target.config_entry_id,
        identifiers={("target_integration", "target")},
        parent_device_id=parent.id,
    )
    await hass.async_block_till_done()
    assert child.id == target.id

    # Reconciling, rescanning and removing the link all skip the child.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    problems = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and "custom integration" not in record.getMessage().lower()
        or "ChildDeviceEntry" in record.getMessage()
    ]
    assert problems == []
