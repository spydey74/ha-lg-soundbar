"""Sound-processing toggle switches (Neural:X, DRC, Night mode, ...)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LGSoundbarConfigEntry
from .coordinator import LGSoundbarCoordinator
from .entity import LGSoundbarEntity
from .protocol import MSG_EQ, MSG_SETTING


@dataclass(frozen=True, kw_only=True)
class ToggleSpec:
    key: str
    translation_key: str
    icon: str | None = None
    # Which view-info message this field lives in. All the originally
    # supported toggles live in SETTING_VIEW_INFO; b_upmix_enabled is an
    # EQ_VIEW_INFO field (confirmed against a live H7 via lg_h7_control.py's
    # verified `upmix` subcommand), so it needs its own message target
    # rather than the hardcoded one the original class used.
    message: str = MSG_SETTING
    # Optional EQ-mode gating. Some toggles only apply to certain sound
    # modes -- confirmed on the H7: EQ_VIEW_INFO's ao_upmix_support_eq lists
    # exactly which i_curr_eq values support upmix ([0, 29, 31, 33, 36, 37]
    # -- notably NOT 34/AI Sound Pro), and it's a static capability table,
    # not something that changes with the current mode. When set, the
    # entity goes `unavailable` rather than just toggleable-but-inert
    # whenever the current mode isn't in this list. Confirmed separately:
    # the bar does NOT clear b_upmix_enabled when you switch into an
    # unsupported mode -- it just stops acting on it -- so this only gates
    # `available`, never forces a write or fakes `is_on`.
    supported_eq_list_key: str | None = None
    supported_eq_list_item_key: str = "i_eq_index"
    # Message/key for the CURRENT eq mode, checked against the list above.
    # Defaults to EQ_VIEW_INFO's i_curr_eq -- the canonical source per
    # media_player.py's sound_mode (see its comment: SETTING_VIEW_INFO
    # carries a second, independently-stale copy of i_curr_eq under the
    # same key name; confirmed diverging in practice, not just in theory).
    curr_eq_message: str = MSG_EQ
    curr_eq_key: str = "i_curr_eq"


# Note: the bar uses ``b_night_time`` (the upstream library's wrong
# ``b_night_mode`` key is deliberately avoided here).
TOGGLE_SPECS: tuple[ToggleSpec, ...] = (
    ToggleSpec(key="b_neuralx", translation_key="neuralx", icon="mdi:surround-sound"),
    ToggleSpec(key="b_drc", translation_key="drc", icon="mdi:arrow-collapse-vertical"),
    ToggleSpec(
        key="b_night_time", translation_key="night_mode", icon="mdi:weather-night"
    ),
    ToggleSpec(
        key="b_auto_vol", translation_key="auto_volume", icon="mdi:volume-equal"
    ),
    ToggleSpec(
        key="b_auto_power", translation_key="auto_power", icon="mdi:power-sleep"
    ),
    ToggleSpec(
        key="b_voice_feedback",
        translation_key="voice_feedback",
        icon="mdi:account-voice",
    ),
    # AI Upmix. Lives in EQ_VIEW_INFO, not SETTING_VIEW_INFO -- see the
    # `message` note above. Not present in the upstream repo's toggle list;
    # confirmed as a real, writable field against an LG H7 (not the S95TR
    # this integration was built/tested against) via lg_h7_control.py.
    # NOTE: same-packet caution carried over from that script's findings --
    # combining i_curr_eq=34 (ai_sound / "Mode 34") with b_upmix_enabled in
    # ONE EQ_VIEW_INFO write silently drops the whole packet on this H7.
    # This integration's coordinator.async_set_key() only ever writes one
    # key per call, so a switch flip here and a sound_mode change via the
    # select entity always go out as separate packets -- safe by
    # construction, but don't "optimize" this later by batching them.
    ToggleSpec(
        key="b_upmix_enabled",
        translation_key="ai_upmix",
        icon="mdi:upload-network",
        message=MSG_EQ,
        supported_eq_list_key="ao_upmix_support_eq",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LGSoundbarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a switch for each toggle the bar reports."""
    coordinator = entry.runtime_data
    async_add_entities(
        LGSoundbarToggle(coordinator, spec)
        for spec in TOGGLE_SPECS
        if coordinator.get(spec.message, spec.key) is not None
    )


class LGSoundbarToggle(LGSoundbarEntity, SwitchEntity):
    """A boolean sound-processing setting."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: LGSoundbarCoordinator, spec: ToggleSpec) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_icon = spec.icon
        self._attr_unique_id = f"{coordinator.unique_id}_{spec.key}"

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.get(self._spec.message, self._spec.key)
        return None if value is None else bool(value)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self._spec.supported_eq_list_key is None:
            return True
        supported = self.coordinator.get(
            self._spec.message, self._spec.supported_eq_list_key
        )
        curr_eq = self.coordinator.get(
            self._spec.curr_eq_message, self._spec.curr_eq_key
        )
        if supported is None or curr_eq is None:
            # One of these hasn't arrived from the bar yet (e.g. a startup
            # race) -- fail open rather than hide a working entity.
            return True
        indices = {
            item.get(self._spec.supported_eq_list_item_key)
            for item in supported
            if isinstance(item, dict)
        }
        return curr_eq in indices

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_key(self._spec.message, self._spec.key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_key(
            self._spec.message, self._spec.key, False
        )
