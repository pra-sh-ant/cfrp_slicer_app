"""DIW (direct ink writing) process settings for structural electronics.

Lengths mm, speeds mm/s, delays ms. Defaults: a 22 G (0.41 mm) needle with
silver-flake conductive ink and a UV-curable dielectric, dispensed
pneumatically (valve on/off codes)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ..config import ConfigError


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

    @field_validator("nozzle_mm", "line_width_mm", "layer_height_mm", "speed_conductor_mm_s",
                     "speed_dielectric_mm_s", "travel_speed_mm_s", "resistivity_ohm_mm",
                     "cured_thickness_ratio", "e_per_mm3")
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
                     "on_delay_ms", "off_delay_ms")
    @classmethod
    def _non_negative(cls, v, info):
        if v < 0:
            raise ValueError(f"DIW {info.field_name.replace('_', ' ')} cannot be negative.")
        return v


def build_diw_config(data: dict | None = None) -> DiwConfig:
    try:
        return DiwConfig.model_validate(data or {})
    except ValidationError as exc:
        raise ConfigError.from_validation_error(exc) from exc
