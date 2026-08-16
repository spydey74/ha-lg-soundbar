f"""Per-channel speaker level and EQ tone controls."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LGSoundbarConfigEntry
from .coordinator import LGSoundbarCoordinator
from .entity import LGSoundbarEntity
from .protocol import MSG_EQ, MSG_SETTING


@dataclass(frozen=True, kw_only=True)
class LevelSpec:
    """Describes one numeric control backed by a soundbar field."""

    key: str  # e.g. "i_woofer_level"
    translation_key: str  # entity translation key
    message: str  # which view-info message the field lives in
    fallback_min: float = -6
    fallback_max: float = 6
    step: float = 1
    unit: str | None = None
    # Display-to-wire scale. The displayed value is `raw * scale + offset`; the
    # wire value is `(displayed - offset) / scale`. Levels are 1:1 (scale 1); AV
    # sync is stored as 10 ms steps on the wire but shown in ms (scale 10).
    scale: float = 1
    # Override: use these instead of reading {base}_min/{base}_max live from
    # the bar. Needed for i_woofer_level on the H7 -- confirmed by a 3-point
    # calibration (app -15dB/0dB/+12dB against raw -9/6/18) that the bar's
    # own reported bounds (-12/12) don't match the field's real usable range
    # at all, let alone the app's displayed range.
    fixed_min: float | None = None
    fixed_max: float | None = None
    # Override: the constant added to raw to get the displayed value.
    # Defaults to native_min_value, which holds for every other channel, but
    # NOT i_woofer_level: its confirmed real offset is -6, which equals
    # neither its reported bounds (-12) nor its fixed display bounds (-15).
    raw_offset: float | None = None

    @property
    def base(self) -> str:
        """Field prefix used for the _min/_max bound keys."""
        return self.key[:-6] if self.key.endswith("_level") else self.key


# Speaker channel levels (SETTING_VIEW_INFO). Bounds are read live from the bar.
LEVEL_SPECS: tuple[LevelSpec, ...] = (
    LevelSpec(
        key="i_woofer_level",
        translation_key="woofer_level",
        message=MSG_SETTING,
        fixed_min=-15,
        fixed_max=12,
        raw_offset=-6,
    ),
    LevelSpec(
        key="i_center_level", translation_key="center_level", message=MSG_SETTING
    ),
    LevelSpec(key="i_side_level", translation_key="side_level", message=MSG_SETTING),
    LevelSpec(key="i_top_level", translation_key="top_level", message=MSG_SETTING),
    LevelSpec(key="i_rear_level", translation_key="rear_level", message=MSG_SETTING),
    LevelSpec(
        key="i_rear_side_level",
        translation_key="rear_side_level",
        message=MSG_SETTING,
    ),
    LevelSpec(
        key="i_rear_top_level",
        translation_key="rear_top_level",
        message=MSG_SETTING,
    ),
    LevelSpec(
        key="i_dialog_level", translation_key="dialog_level", message=MSG_SETTING
    ),
)

# EQ tone (EQ_VIEW_INFO).
TONE_SPECS: tuple[LevelSpec, ...] = (
    LevelSpec(key="i_bass", translation_key="bass", message=MSG_EQ),
    LevelSpec(key="i_middle", translation_key="middle", message=MSG_EQ),
    LevelSpec(key="i_treble", translation_key="treble", message=MSG_EQ),
)

# Other adjustable settings (SETTING_VIEW_INFO) that aren't dB levels. AV sync
# is an audio delay shown in ms; the bar stores it as 1-30 steps of 10 ms each
# (so the wire value is ms / 10) and reports no bounds, hence the fixed range.
OTHER_SPECS: tuple[LevelSpec, ...] = (
    LevelSpec(
        key="i_av_sync",
        translation_key="av_sync",
        message=MSG_SETTING,
        fallback_min=0,
        fallback_max=300,
        step=10,
        unit="ms",
        scale=10,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LGSoundbarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a control for each level/tone field the bar actually reports."""
    coordinator = entry.runtime_data
    entities = [
        LGSoundbarLevel(coordinator, spec)
        for spec in (*LEVEL_SPECS, *TONE_SPECS, *OTHER_SPECS)
        if coordinator.get(spec.message, spec.key) is not None
    ]
    async_add_entities(entities)


class LGSoundbarLevel(LGSoundbarEntity, NumberEntity):
    """A single adjustable level/tone value, bounded by the bar's own limits."""

    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: LGSoundbarCoordinator, spec: LevelSpec) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_unique_id = f"{coordinator.unique_id}_{spec.key}"

    @property
    def native_min_value(self) -> float:
        if self._spec.fixed_min is not None:
            return self._spec.fixed_min
        # Bound keys are looked up in the SAME message namespace as the value
        # itself (self._spec.message), never a flat merge -- the bar reuses
        # e.g. "i_bass_min" for two unrelated ranges across EQ_VIEW_INFO and
        # SETTING_VIEW_INFO, so crossing namespaces here silently produces a
        # wrong-but-plausible bound. See coordinator.py's class docstring.
        return float(
            self.coordinator.get(
                self._spec.message, f"{self._spec.base}_min", self._spec.fallback_min
            )
        )

    @property
    def native_max_value(self) -> float:
        if self._spec.fixed_max is not None:
            return self._spec.fixed_max
        return float(
            self.coordinator.get(
                self._spec.message, f"{self._spec.base}_max", self._spec.fallback_max
            )
        )

    @property
    def _offset(self) -> float:
        # Defaults to native_min_value (the "0-based from min" convention
        # that holds for every channel confirmed so far), but i_woofer_level
        # overrides this: its real offset (-6) doesn't equal either its
        # reported bounds (-12, wrong) or its fixed display bounds (-15).
        if self._spec.raw_offset is not None:
            return self._spec.raw_offset
        return self.native_min_value

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator.get(self._spec.message, self._spec.key)
        if raw is None:
            return None
        # displayed = raw * scale + offset. For levels scale is 1. AV sync
        # uses scale 10 (10 ms wire steps shown in ms) with an offset of 0.
        low, high = self.native_min_value, self.native_max_value
        value = float(raw) * self._spec.scale + self._offset
        return max(low, min(high, value))

    async def async_set_native_value(self, value: float) -> None:
        # Reverse of the read transform: wire = (displayed - offset) / scale.
        raw = round((value - self._offset) / self._spec.scale)
        await self.coordinator.async_set_key(self._spec.message, self._spec.key, raw)
