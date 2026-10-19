"""DIW (direct ink writing) process settings for structural electronics.

Lengths mm, speeds mm/s, delays ms. Defaults: a 22 G (0.41 mm) needle with
silver-flake conductive ink and a UV-curable dielectric, dispensed
pneumatically (valve on/off codes)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ..config import ConfigError


class DiwLayerSpec(BaseModel):
    """One printed ink layer of a user-set stack (DiwConfig.stack), bottom
    first. `covers` (insulation / cover layers): under the wires only, or
    the whole board; `level` (ink layers): wire level 1 or 2 (crossovers).
    `height_mm` / `speed_mm_s` 0 = the usual value; `cure`: UV cure right
    after this layer; `enabled` False = skipped."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["dielectric", "conductor", "encapsulation"] = "dielectric"
    covers: Literal["wires", "board"] = "wires"
    level: int = 1
    fill: Literal["raster", "concentric"] = "raster"
    angle_deg: float = 0.0
    height_mm: float = 0.0
    speed_mm_s: float = 0.0
    cure: bool = False
    enabled: bool = True

    @field_validator("level")
    @classmethod
    def _level(cls, v):
        if v not in (1, 2):
            raise ValueError("An ink layer's wire level is 1 or 2.")
        return v

    @field_validator("height_mm", "speed_mm_s")
    @classmethod
    def _nonneg(cls, v, info):
        if v < 0:
            raise ValueError(f"A layer's {info.field_name.split('_')[0]} cannot be negative.")
        return v


class DiwConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nozzle_mm: float = 0.41
    line_width_mm: float = 0.5           # printed bead width (~1.2 x nozzle)
    layer_height_mm: float = 0.25
    conductor_layers: int = 1
    dielectric: bool = True              # insulate the ink from the (conductive) carbon fibre
    dielectric_layers: int = 2
    dielectric_margin_mm: float = 1.0    # dielectric extends this far beyond the conductors
    encapsulate: bool = False            # cover the traces (not the pads) afterwards
    encapsulation_layers: int = 1
    speed_conductor_mm_s: float = 8.0
    speed_dielectric_mm_s: float = 12.0
    travel_speed_mm_s: float = 40.0
    z_hop_mm: float = 1.0
    dispense: Literal["pneumatic", "volumetric"] = "pneumatic"
    dispense_on: str = "M42 P4 S255"      # valve open (pneumatic)
    dispense_off: str = "M42 P4 S0"
    on_delay_ms: int = 120               # wait after opening before moving
    off_delay_ms: int = 60
    e_per_mm3: float = 1.0               # volumetric: E units per mm³ of ink
    tool_select: str = "T1"
    tool_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    pause_before: bool = True            # cut the fibre tow and swap heads first
    clearance_mm: float = 0.5            # minimum gap between different nets
    edge_clearance_mm: float = 1.0       # ink to board edge / hole
    resistivity_ohm_mm: float = 4.0e-5   # cured ink (silver flake ~4 µΩ·cm)
    cured_thickness_ratio: float = 0.5   # cured film / wet layer height
    substrate_conductive: bool = True    # carbon fibre conducts
    surface_z_mm: float = 0.0            # electronics-only prints: top of the part printed on
    # Flow start/stop compensation (calibrate with a test print):
    stop_early_mm: float = 0.0           # pneumatic: close the valve this far before a line ends
    prime_e: float = 0.0                 # volumetric: extra push at each line start (E units)
    retract_e: float = 0.0               # volumetric: pull back at each line end (E units)
    # Cure between ink layers (UV LED on the head):
    uv_cure_between_layers: bool = False
    uv_cure_on: str = "M1995 P47 S255"
    uv_cure_off: str = "M1995 P47 S0"
    uv_cure_s: float = 3.0
    # Embedded electronics: print them after this fibre layer, then lay the
    # remaining fibre layers on top (0 = on top of the whole laminate).
    embed_after_layer: int = 0
    # The bead wets out after it is laid: printed width = line width x this
    # (1 = no spread). Fills are inset so the spread edge lands on the drawn
    # edge; checks use the spread width.
    spread_factor: float = 1.0
    # Conductive ink must stay this far inside the insulation's edge (ink
    # that spreads past it touches the carbon fibre).
    min_insulation_overlap_mm: float = 0.5
    # Current a cured trace may carry per mm² of cross-section (sets each
    # wire's current capacity; ~20 A/mm² keeps printed silver warm, not hot).
    max_current_density_a_mm2: float = 20.0
    # Wait after each ink layer (solvent inks drying), seconds.
    layer_wait_s: float = 0.0
    # Crossovers: a level-2 wire crossing a level-1 wire of another net is
    # insulated by this many dielectric layers, reaching this far past it.
    bridge_layers: int = 2
    bridge_margin_mm: float = 0.6
    # Insulation under the wires only (grown by the margin), or over the
    # whole top surface of the board (minus holes and the edge).
    insulation_coverage: Literal["wires", "board"] = "wires"
    # The ink layers printed, bottom first, set one by one (empty = the
    # automatic stack from the layer counts above; see slicer.layer_stack).
    stack: tuple[DiwLayerSpec, ...] = ()

    @field_validator("nozzle_mm", "line_width_mm", "layer_height_mm", "speed_conductor_mm_s",
                     "speed_dielectric_mm_s", "travel_speed_mm_s", "resistivity_ohm_mm",
                     "cured_thickness_ratio", "e_per_mm3", "max_current_density_a_mm2")
    @classmethod
    def _positive(cls, v, info):
        if not v > 0:
            raise ValueError(f"DIW {info.field_name.replace('_', ' ')} must be greater than 0 (got {v:g}).")
        return v

    @field_validator("conductor_layers", "dielectric_layers", "encapsulation_layers")
    @classmethod
    def _layers(cls, v, info):
        if v < 0 or (info.field_name == "conductor_layers" and v < 1):
            raise ValueError(f"DIW {info.field_name.replace('_', ' ')} out of range (got {v}).")
        return v

    @field_validator("z_hop_mm", "dielectric_margin_mm", "clearance_mm", "edge_clearance_mm", "surface_z_mm",
                     "on_delay_ms", "off_delay_ms", "stop_early_mm", "prime_e", "retract_e", "uv_cure_s",
                     "embed_after_layer", "min_insulation_overlap_mm", "layer_wait_s", "bridge_margin_mm")
    @classmethod
    def _non_negative(cls, v, info):
        if v < 0:
            raise ValueError(f"DIW {info.field_name.replace('_', ' ')} cannot be negative.")
        return v

    @field_validator("spread_factor")
    @classmethod
    def _spread(cls, v):
        if not 1.0 <= v <= 3.0:
            raise ValueError(f"DIW bead spread must be between 1 and 3 (got {v:g}).")
        return v

    @field_validator("bridge_layers")
    @classmethod
    def _bridge(cls, v):
        if v < 1:
            raise ValueError("A crossover bridge needs at least one insulation layer.")
        return v

    @property
    def bead_mm(self) -> float:
        """Printed bead width after it spreads."""
        return self.line_width_mm * self.spread_factor


def build_diw_config(data: dict | None = None) -> DiwConfig:
    try:
        return DiwConfig.model_validate(data or {})
    except ValidationError as exc:
        raise ConfigError.from_validation_error(exc) from exc
