"""Device updates Device Linker can't see don't make it scan or re-read links."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.device_linker as device_linker
from custom_components.device_linker.const import (
    CONF_ADDED_CONNECTIONS,
    CONF_LINKED_DEVICE_IDS,
    CONF_MAC_ADDRESS,
    CONF_MAIN_DEVICE_ID,
    DOMAIN,
)
from custom_components.device_linker.manager import (
    async_get_manager,
    device_change_matters,
)
from homeassistant.helpers import device_registry as dr

MAC = "aa:bb:cc:dd:ee:ff"
CONNECTION = (dr.CONNECTION_NETWORK_MAC, MAC)


async def _linked(hass):
    """A hub with one link that owns the MAC on its target device."""
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
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAIN_DEVICE_ID: main.id,
            CONF_LINKED_DEVICE_IDS: [target.id],
            CONF_MAC_ADDRESS: MAC,
            CONF_ADDED_CONNECTIONS: {target.id: True},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return registry, target


def test_which_device_changes_matter() -> None:
    """Only fields a scan reads, or a create or remove, schedule a scan."""
    assert device_change_matters({"action": "create", "device_id": "x"})
    assert device_change_matters({"action": "remove", "device_id": "x"})
    assert device_change_matters(
        {"action": "update", "device_id": "x", "changes": {"name_by_user": None}}
    )
    assert device_change_matters(
        {
            "action": "update",
            "device_id": "x",
            "changes": {"sw_version": "1", "area_id": None},
        }
    )
    # A field Home Assistant adds later still counts.
    assert device_change_matters(
        {"action": "update", "device_id": "x", "changes": {"something_new": 1}}
    )
    assert not device_change_matters(
        {
            "action": "update",
            "device_id": "x",
            "changes": {"sw_version": "1", "hw_version": "2", "labels": set()},
        }
    )
    assert not device_change_matters({"action": "other", "device_id": "x"})


async def test_a_firmware_update_does_not_schedule_a_scan(hass) -> None:
    """A new firmware version can't change a suggestion; a rename can."""
    registry, target = await _linked(hass)
    manager = async_get_manager(hass)
    assert manager is not None
    if manager._unsub_debounce is not None:  # noqa: SLF001
        manager._unsub_debounce()  # noqa: SLF001
        manager._unsub_debounce = None  # noqa: SLF001

    registry.async_update_device(target.id, sw_version="2.0.1", hw_version="B")
    await hass.async_block_till_done()
    assert manager._unsub_debounce is None  # noqa: SLF001

    registry.async_update_device(target.id, name_by_user="Den target")
    await hass.async_block_till_done()
    assert manager._unsub_debounce is not None  # noqa: SLF001
    await manager.async_stop()


async def test_restore_skips_updates_that_keep_the_connections(hass) -> None:
    """Links are re-read only when a device's connections or identifiers change."""
    registry, target = await _linked(hass)
    calls = 0
    real = device_linker.hub_links

    def counting(entry):
        nonlocal calls
        calls += 1
        return real(entry)

    with patch.object(device_linker, "hub_links", counting):
        registry.async_update_device(
            target.id, sw_version="2.0.1", name_by_user="Den target"
        )
        await hass.async_block_till_done()
        assert calls == 0

        registry.async_update_device(
            target.id,
            new_connections=registry.async_get(target.id).connections - {CONNECTION},
        )
        await hass.async_block_till_done()
        assert calls >= 1
    assert CONNECTION in registry.async_get(target.id).connections
