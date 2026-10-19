"""Bridge from the legacy flat `sys_config` dict (the Streamlit sidebar's
output, and the golden test cases) to `ProcessConfig`.

Keys missing from the dict take the model defaults; keys the core does not
use (e.g. `constraint_notes`, `is_3d`, `layup_sequence`) are ignored.
"""
from __future__ import annotations

from pydantic import ValidationError

from .config import ConfigError, ProcessConfig

# legacy key -> (section or None for top level, field name)
_FIELD_MAP = {
    # geometry
    "shape": ("geometry", "shape"),
    "width": ("geometry", "width_mm"),
    "height": ("geometry", "height_mm"),
    "include_boundary": ("geometry", "include_boundary"),
    "base_radius": ("geometry", "base_radius_mm"),
    "disc_radius": ("geometry", "disc_radius_mm"),
    "stem_height": ("geometry", "stem_height_mm"),
    "wall_thickness": ("geometry", "wall_thickness_mm"),
    "start_x": ("geometry", "start_x_mm"),
    "start_y": ("geometry", "start_y_mm"),
    "stl_path": ("geometry", "stl_path"),
    "stl_islands": ("geometry", "stl_islands"),
    "stl_closing_radius": ("geometry", "stl_closing_radius_mm"),
    "stl_scale": ("geometry", "stl_scale"),
    "stl_stitch": ("geometry", "stl_stitch_mm"),
    "custom_outline": ("geometry", "custom_outline"),
    "custom_holes": ("geometry", "custom_holes"),
    # material
    "needle_dia": ("material", "needle_dia_mm"),
    "e_mult": ("material", "e_multiplier"),
    "density": ("material", "density_g_per_mm3"),
    "weight": ("material", "fiber_weight_g_per_mm"),
    "e_const": ("material", "e_const_override"),
    # kinematics
    "v_max": ("kinematics", "v_max_mm_s"),
    "v_min": ("kinematics", "v_min_mm_s"),
    "accel": ("kinematics", "accel_mm_s2"),
    "decel": ("kinematics", "decel_mm_s2"),
    "p_advance": ("kinematics", "pressure_advance_mm"),
    "pump_stall_floor": ("kinematics", "pump_stall_floor_mm_s"),
    "pa_table": ("kinematics", "pressure_advance_table"),
    "v_z": ("kinematics", "v_z_mm_s"),
    "v_overhang": ("kinematics", "v_overhang_mm_s"),
    "dwell": ("kinematics", "dwell_s"),
    # G-code start sequence
    "uv": ("gcode", "uv_pwm"),
    "purge_e": ("gcode", "purge_e"),
    "purge_f": ("gcode", "purge_f_mm_min"),
    "travel_f": ("gcode", "travel_f_mm_min"),
    # process (top level)
    "layers": (None, "layers"),
    "layup_seq": (None, "layup_text"),
    "infill_type": (None, "infill_type"),
    "tracks_per_layer": (None, "tracks_per_layer"),
    "track_gap": (None, "track_gap_mm"),
    "min_radius": (None, "min_radius_mm"),
    "layer_height": (None, "layer_height_mm"),
    "z_offset": (None, "z_offset_mm"),
    "transition_angle": (None, "transition_angle_deg"),
    "seam_trim_length": (None, "seam_trim_mm"),
    "anchor_length": (None, "anchor_length_mm"),
    "seam_stagger": (None, "seam_stagger_deg"),
    "fill_solid": (None, "fill_solid"),
    "first_layer_solid": (None, "first_layer_solid"),
    "first_layer_infill": (None, "first_layer_infill"),
    "first_layer_min_coverage": (None, "first_layer_min_coverage"),
    "solid_bottom_layers": (None, "solid_bottom_layers"),
    "solid_top_layers": (None, "solid_top_layers"),
}


_KEY_FOR = {(section, field): key for key, (section, field) in _FIELD_MAP.items()}


def legacy_key(path: str) -> str | None:
    """The flat (GUI) setting key for a config field path such as
    'geometry.width_mm' or 'track_gap_mm' (as error messages name them),
    or None."""
    parts = [p for p in str(path).split(".") if p]
    if not parts:
        return None
    if len(parts) == 1:
        return _KEY_FOR.get((None, parts[0]))
    return _KEY_FOR.get((parts[-2], parts[-1])) or _KEY_FOR.get((None, parts[-1]))


def parse_layup(text: str) -> tuple[float, ...]:
    """Comma-separated angles, as the legacy sidebar parsed them: empty or
    unparseable input falls back to (0.0,)."""
    try:
        angles = tuple(float(x.strip()) for x in str(text).split(",") if x.strip())
    except ValueError:
        return (0.0,)
    return angles or (0.0,)


def parse_pa_table(text) -> tuple[tuple[float, float], ...]:
    """'5:1.0, 10:3.0' -> ((5.0, 1.0), (10.0, 3.0)); empty -> ().
    Raises ValueError naming the bad entry."""
    if not isinstance(text, str):
        return tuple((float(v), float(a)) for v, a in (text or ()))
    pairs = []
    for item in text.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            v, a = (float(x) for x in item.split(":"))
        except ValueError:
            raise ValueError(f"PA Table entry {item!r} must be speed:advance, e.g. 10:3.0") from None
        pairs.append((v, a))
    return tuple(pairs)


def to_nested(d: dict, layup=None) -> dict:
    """Legacy flat dict -> nested dict ready for ProcessConfig validation."""
    nested: dict = {"geometry": {}, "material": {}, "kinematics": {}, "gcode": {}}
    for key, value in d.items():
        if key not in _FIELD_MAP:
            continue
        section, field = _FIELD_MAP[key]
        if field == "pressure_advance_table":
            value = parse_pa_table(value)
        if field == "first_layer_infill" and value in ("", "Same as other layers", "N/A"):
            value = None
        (nested[section] if section else nested)[field] = value
    # The legacy G-code header printed layup_seq with a "0" default.
    nested.setdefault("layup_text", "0")
    if layup is None:
        layup = parse_layup(d.get("layup_seq", "0"))
    nested["layup_deg"] = tuple(float(a) for a in layup) or (0.0,)
    return nested


def from_legacy_dict(d: dict, layup=None) -> ProcessConfig:
    """Build a ProcessConfig from a legacy flat config dict.

    `layup` is the parsed angle sequence the legacy pipeline received as a
    separate argument; if omitted it is parsed from `layup_seq`.
    Raises ConfigError with plain-language messages.
    """
    try:
        return ProcessConfig.model_validate(to_nested(d, layup))
    except ValidationError as exc:
        raise ConfigError.from_validation_error(exc) from exc
