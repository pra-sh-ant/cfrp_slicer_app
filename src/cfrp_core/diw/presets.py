"""Ink material presets: typical values to start from (tune with test prints).

Each preset is a partial DiwConfig update. Resistivities are of the cured
ink in Ω·mm (1 µΩ·cm = 1e-5 Ω·mm)."""
from __future__ import annotations

from .config import DiwConfig

INK_PRESETS: dict[str, dict] = {
    "Silver flake paste": dict(
        resistivity_ohm_mm=4.0e-5, line_width_mm=0.5, layer_height_mm=0.25, nozzle_mm=0.41,
        speed_conductor_mm_s=8.0, cured_thickness_ratio=0.5),
    "Silver nanoparticle ink (fine lines)": dict(
        resistivity_ohm_mm=2.0e-5, line_width_mm=0.25, layer_height_mm=0.08, nozzle_mm=0.20,
        speed_conductor_mm_s=5.0, cured_thickness_ratio=0.3, conductor_layers=2),
    "Carbon / graphene paste": dict(
        resistivity_ohm_mm=0.3, line_width_mm=0.6, layer_height_mm=0.3, nozzle_mm=0.51,
        speed_conductor_mm_s=10.0, cured_thickness_ratio=0.6),
    "Liquid metal (EGaIn)": dict(
        resistivity_ohm_mm=2.94e-4, line_width_mm=0.4, layer_height_mm=0.2, nozzle_mm=0.26,
        speed_conductor_mm_s=4.0, cured_thickness_ratio=1.0, encapsulate=True, encapsulation_layers=2),
    "Copper paste": dict(
        resistivity_ohm_mm=1.0e-4, line_width_mm=0.5, layer_height_mm=0.25, nozzle_mm=0.41,
        speed_conductor_mm_s=7.0, cured_thickness_ratio=0.5),
}

DIELECTRIC_PRESETS: dict[str, dict] = {
    "UV-curable dielectric": dict(speed_dielectric_mm_s=12.0, uv_cure_between_layers=True, uv_cure_s=3.0),
    "Epoxy dielectric (thermal cure)": dict(speed_dielectric_mm_s=8.0, uv_cure_between_layers=False),
    "Silicone / PDMS": dict(speed_dielectric_mm_s=6.0, uv_cure_between_layers=False),
}


def apply_preset(cfg: DiwConfig, ink: str | None = None, dielectric: str | None = None) -> DiwConfig:
    upd = {}
    if ink:
        upd.update(INK_PRESETS[ink])
    if dielectric:
        upd.update(DIELECTRIC_PRESETS[dielectric])
    return cfg.model_copy(update=upd)
