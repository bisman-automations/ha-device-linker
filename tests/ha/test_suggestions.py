"""Suggestion scans, discovery, dismissal, and the options that control them."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.device_linker.const import (
    CONF_MATCH_HARDWARE,
    CONF_MATCH_NAMES,
    CONF_SUGGESTION_THRESHOLD,
    DOMAIN,
    SERVICE_RESCAN,
)
from custom_components.device_linker.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.device_linker.manager import async_get_manager
from custom_components.device_linker.matching import normalize_mac, suggestion_unique_id
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.util import dt as dt_util

MAC = "aa:bb:cc:dd:ee:ff"


async def _anchor(hass):
    """Set up one real Device Linker entry so the suggestion manager is running.

    The two anchor devices end up sharing a MAC, so they are already linked and
    are not themselves a suggestion.
    """
    main_config = MockConfigEntry(domain="anchor_main")
    main_config.add_to_hass(hass)
    linked_config = MockConfigEntry(domain="anchor_linked")
    linked_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    main = registry.async_get_or_create(
        config_entry_id=main_config.entry_id,
        identifiers={("anchor_main", "main")},
        connections={(dr.CONNECTION_NETWORK_MAC, "11:22:33:44:55:66")},
        name="Anchor main",
    )
    linked = registry.async_get_or_create(
        config_entry_id=linked_config.entry_id,
        identifiers={("anchor_linked", "linked")},
        name="Anchor linked",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "main_device_id": main.id,
            "linked_device_ids": [linked.id],
            "mac_address": "11:22:33:44:55:66",
            "added_connections": {linked.id: True},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _register(
    hass, *, domain: str, name: str, area: str, connections=None, identifiers=None
):
    config = MockConfigEntry(domain=domain)
    config.add_to_hass(hass)
    area_entry = ar.async_get(hass).async_get_or_create(area)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id,
        identifiers=identifiers or {(domain, name)},
        connections=connections or set(),
        name=name,
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area_entry.id)
    return dr.async_get(hass).async_get(device.id)


async def test_format_mac_and_normalize_mac_agree(hass) -> None:
    """The pure normalizer stays aligned with Home Assistant's format_mac."""
    for raw in (
        "AA:BB:CC:DD:EE:FF",
        "aa-bb-cc-dd-ee-ff",
        "aabbccddeeff",
        "aabb.ccdd.eeff",
    ):
        assert normalize_mac(raw) == format_mac(raw)


async def test_unifi_mac_suggests_a_device_in_another_area(hass) -> None:
    """A UniFi client and a Shelly with the same MAC are suggested across areas."""
    entry = await _anchor(hass)
    unifi = _register(
        hass,
        domain="unifi",
        name="Living room tablet",
        area="Downstairs",
        connections={(dr.CONNECTION_NETWORK_MAC, "AA:BB:CC:DD:EE:FF")},
        identifiers={("unifi", "tablet")},
    )
    shelly = _register(
        hass,
        domain="shelly",
        name="Tablet relay",
        area="Living room",
        identifiers={("shelly", MAC)},
    )
    manager = async_get_manager(hass)
    assert manager is not None
    suggestions = await manager.async_scan()

    match = next(
        item for item in suggestions if item.device_id_a in {unifi.id, shelly.id}
    )
    assert match.reason == "mac"
    assert match.area_mismatch is True
    assert match.mac == MAC
    assert {match.integration_a, match.integration_b} == {"shelly", "unifi"}

    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("unique_id") == suggestion_unique_id(unifi.id, shelly.id)
    ]
    assert len(flows) == 1
    assert flows[0]["step_id"] == "discovery_mac"

    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"],
        user_input={"selected_mac_address": MAC},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    updated = dr.async_get(hass).async_get(shelly.id)
    assert (dr.CONNECTION_NETWORK_MAC, MAC) in updated.connections

    state_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "device_linker_suggestions"
    )
    assert state_id is not None
    attributes = hass.states.get(state_id).attributes["suggestions"]
    assert (
        any(item["reason"] == "mac" for item in attributes)
        or hass.states.get(state_id).state
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"]["mac_address"] == "**REDACTED**"
    assert "match_hardware" in diagnostics["suggestions"]


async def test_disabled_device_is_not_suggested(hass) -> None:
    """A disabled device is left out of the scan."""
    await _anchor(hass)
    unifi = _register(
        hass,
        domain="unifi",
        name="Office camera",
        area="Office",
        connections={(dr.CONNECTION_NETWORK_MAC, "22:33:44:55:66:77")},
    )
    other = _register(
        hass,
        domain="esphome",
        name="Office camera unit",
        area="Office",
        identifiers={("esphome", "22:33:44:55:66:77")},
    )
    dr.async_get(hass).async_update_device(
        other.id, disabled_by=dr.DeviceEntryDisabler.USER
    )
    manager = async_get_manager(hass)
    suggestions = await manager.async_scan()
    assert all(
        unifi.id not in {item.device_id_a, item.device_id_b} for item in suggestions
    )


async def test_dismissal_survives_another_scan(hass) -> None:
    """Ignore stores the pair, and a later scan does not offer it again."""
    await _anchor(hass)
    unifi = _register(
        hass,
        domain="unifi",
        name="Hall tablet",
        area="Hall",
        connections={(dr.CONNECTION_NETWORK_MAC, "33:44:55:66:77:88")},
    )
    other = _register(
        hass,
        domain="esphome",
        name="Hall tablet plug",
        area="Study",
        identifiers={("esphome", "33:44:55:66:77:88")},
    )
    manager = async_get_manager(hass)
    await manager.async_scan()
    unique_id = suggestion_unique_id(unifi.id, other.id)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "ignore"},
        data={"unique_id": unique_id, "title": "Hall tablet"},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    suggestions = await manager.async_scan()
    assert unique_id not in {
        suggestion_unique_id(item.device_id_a, item.device_id_b) for item in suggestions
    }
    assert manager.is_dismissed(unique_id)


async def test_registry_update_rescans_after_the_debounce(hass) -> None:
    """A device registry change waits out the debounce, then the suggestion appears."""
    await _anchor(hass)
    area = ar.async_get(hass).async_get_or_create("Kitchen")
    main_config = MockConfigEntry(domain="kitchen_a")
    main_config.add_to_hass(hass)
    other_config = MockConfigEntry(domain="kitchen_b")
    other_config.add_to_hass(hass)
    registry = dr.async_get(hass)
    first = registry.async_get_or_create(
        config_entry_id=main_config.entry_id,
        identifiers={("kitchen_a", "ceiling")},
        name="North window",
    )
    second = registry.async_get_or_create(
        config_entry_id=other_config.entry_id,
        identifiers={("kitchen_b", "ceiling")},
        name="South door",
    )
    registry.async_update_device(first.id, area_id=area.id)
    registry.async_update_device(second.id, area_id=area.id)
    manager = async_get_manager(hass)
    await manager.async_scan()
    assert not any(
        first.id in {item.device_id_a, item.device_id_b} for item in manager.suggestions
    )

    registry.async_update_device(first.id, name_by_user="Kitchen ceiling light")
    registry.async_update_device(second.id, name_by_user="Kitchen ceiling")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()

    assert any(
        {item.device_id_a, item.device_id_b} == {first.id, second.id}
        and item.reason == "name"
        for item in manager.suggestions
    )


async def test_rescan_action_and_option_switches(hass) -> None:
    """The rescan action runs a scan, and options can turn signals off."""
    entry = await _anchor(hass)
    _register(
        hass,
        domain="unifi",
        name="Loft speaker",
        area="Loft",
        connections={(dr.CONNECTION_NETWORK_MAC, "44:55:66:77:88:99")},
    )
    _register(
        hass,
        domain="media_player",
        name="Speaker unit",
        area="Den",
        identifiers={("cast", "44:55:66:77:88:99")},
    )
    await hass.services.async_call(DOMAIN, SERVICE_RESCAN, {}, blocking=True)
    manager = async_get_manager(hass)
    assert any(item.reason == "mac" for item in manager.suggestions)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "configure"
    submitted = {
        "linked_device_ids": entry.data["linked_device_ids"],
        "selected_mac_address": "11:22:33:44:55:66",
        CONF_SUGGESTION_THRESHOLD: 0.9,
        CONF_MATCH_HARDWARE: False,
        CONF_MATCH_NAMES: True,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=submitted
    )
    await hass.async_block_till_done()
    manager = async_get_manager(hass)
    assert manager.settings.threshold == 0.9
    assert manager.settings.match_hardware is False
    assert not any(item.reason == "mac" for item in manager.suggestions)
