"""Diagnostic sensors (installed firmware version)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LGSoundbarConfigEntry
from .coordinator import LGSoundbarCoordinator
from .entity import LGSoundbarEntity
from .protocol import MSG_UPDATE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LGSoundbarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([LGSoundbarFirmwareSensor(entry.runtime_data)])


class LGSoundbarFirmwareSensor(LGSoundbarEntity, SensorEntity):
    """Installed firmware version (UPDATE_VIEW_INFO.o_system_ver.s_main)."""

    _attr_name = "Firmware version"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LGSoundbarCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_firmware_version"

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.get(MSG_UPDATE, "o_system_ver") or {}).get("s_main")
