"""A diagnostic sensor that lists current link suggestions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .manager import (
    SuggestionManager,
    async_get_manager,
    auto_linked_summaries,
    entry_is_link,
)

_DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, "suggestions")},
    name="Device Linker",
    manufacturer="bisman-automations",
    model="Device Linker",
    entry_type=DeviceEntryType.SERVICE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the suggestions sensor for the first real config entry."""
    if not entry_is_link(entry):
        return
    manager = async_get_manager(hass)
    if manager is None:
        return
    manager.async_add_sensor(entry.entry_id, async_add_entities)


class SuggestionSensor(SensorEntity):
    """How many device links Device Linker is currently recommending."""

    _attr_has_entity_name = True
    _attr_translation_key = "suggestions"
    _attr_unique_id = "device_linker_suggestions"
    _attr_icon = "mdi:link-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_device_info = _DEVICE_INFO

    def __init__(self, manager: SuggestionManager) -> None:
        """Read live suggestions from the manager."""
        self._manager = manager

    @property
    def native_value(self) -> int:
        """Number of suggestions from the latest scan."""
        return len(self._manager.suggestions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Suggestions still waiting, plus what was linked automatically.

        ``suggestions`` lists pairs that were not linked on their own. The
        count can stay at 0 after a scan when every match was linked, which
        is why ``last_scan`` is included: Home Assistant does not move
        "last changed" when the count stays 0. ``auto_linked`` lists
        automatic entries. MAC addresses are left out. The reason is
        ``mac``, ``identifier``, or ``name``.
        """
        return {
            "suggestions": self._manager.listed_suggestions(),
            "auto_linked": auto_linked_summaries(self._manager.hass),
            "last_scan": self._manager.last_scan,
        }
