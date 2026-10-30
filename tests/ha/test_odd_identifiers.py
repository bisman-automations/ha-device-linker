"""Devices whose identifiers aren't two pieces of text don't break scanning."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.device_linker.const import DOMAIN, SERVICE_RESCAN
from homeassistant.helpers import device_registry as dr
from tests.ha.test_config_flow import _setup_hub


async def _odd_devices(hass):
    registry = dr.async_get(hass)
    for domain, identifiers in (
        ("numbers", {("numbers", 12345)}),
        ("three_parts", {("three_parts", "bridge", "light-1")}),
    ):
        config = MockConfigEntry(domain=domain)
        config.add_to_hass(hass)
        registry.async_get_or_create(
            config_entry_id=config.entry_id,
            identifiers=identifiers,
            connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:70")},
            name=f"Odd {domain}",
        )


async def test_rescan_and_settings_survive_odd_identifiers(hass) -> None:
    hub = await _setup_hub(hass)
    await _odd_devices(hass)
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(hub.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"match_hardware": True}
    )
    assert result["type"] == "create_entry"

    result = await hass.config_entries.options.async_init(hub.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "cleanup"}
    )
    assert result["type"] in ("form", "abort")
