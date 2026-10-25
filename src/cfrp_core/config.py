"""Typed process configuration (pydantic v2).

Units: lengths mm, speeds mm/s, accelerations mm/s², angles degrees,
feedrates in the G-code section mm/min (named `*_mm_min`).

Models are frozen: a config is an immutable value, so it can be hashed
(caching), compared, and stored on an undo stack. Derive a changed copy with
`cfg.model_copy(update=...)` or build a new one.

Validation here is the pre-slice rule set from spec §4: field validators for
the unconditional rules, a model validator for the shape-dependent ones.
Error messages match the legacy hand-written checks. The *automatic
adjustments* of §4 (which produce info notes rather than errors) live in
`cfrp_core.constraints`.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

# --- Shapes and patterns -----------------------------------------------------

ShapeName = Literal[
    "Square/Rectangle", "Triangle", "Hexagon", "Circle",
    "Hollow Cylinder (Z)", "Hollow Square Tube (Z)",
    "Hollow Sphere (Z)", "Dome (Z)", "Tapered Cylinder (Z)",
    "Conical Dome (Z)", "3D Overhang (Mushroom)",
    "STL Import", "Custom Outline",
]
SHAPES: tuple[str, ...] = ShapeName.__args__

# Z-revolve shells: sliced into layers with a concentric multi-track wall per
# layer, alternating outside-in/inside-out, like the flat laminate pipeline.
REVOLVE_SHAPES = frozenset({
    "Hollow Cylinder (Z)", "Hollow Square Tube (Z)", "Hollow Sphere (Z)",
    "Dome (Z)", "Tapered Cylinder (Z)", "Conical Dome (Z)",
})
# Fixed-speed specialty generators: no per-layer stitching, no speed planner.
FLAT_SPECIALTY_SHAPES = frozenset({"3D Overhang (Mushroom)"})
SPECIALTY_SHAPES = REVOLVE_SHAPES | FLAT_SPECIALTY_SHAPES

ARCHIMEDEAN = "Archimedean (Concentric Spiral)"
FERMAT = "Fermat Spiral (Connected Double Spiral)"
ORIENTED_SERPENTINE = "Oriented Serpentine"
DIAGONAL_45 = "45° Diagonal Hatch"
CORNER_STEP = "Corner-Step Spiral"
AUTO = "Auto (Best Fit)"        # per layer, the pattern that keeps the fibre rules best (layers/auto.py)
InfillPattern = Literal[
    "Archimedean (Concentric Spiral)",
    "Fermat Spiral (Connected Double Spiral)",
    "Oriented Serpentine",
    "45° Diagonal Hatch",
    "Corner-Step Spiral",
    "Auto (Best Fit)",
    "N/A",  # specialty shapes: no infill pattern applies
]

# The legacy G-code header prints a few values with whatever numeric type the
# user supplied ("0" vs "0.0"). `int | float` keeps that type so G-code stays
# byte-identical to the legacy app; Phase 5 normalises number formatting.
Number = int | float


class ConfigError(ValueError):
    """Plain-language configuration error. `messages` lists every problem."""

    def __init__(self, messages: list[str] | tuple[str, ...]):
        self.messages = tuple(messages)
        super().__init__("\n".join(self.messages))

    @classmethod
    def from_validation_error(cls, exc: ValidationError) -> ConfigError:
        messages = []
        for err in exc.errors():
            msg = str(err.get("msg", ""))
            msg = msg.removeprefix("Value error, ")
            loc = ".".join(str(p) for p in err.get("loc", ()))
            # pydantic's own (type) errors carry no context; add the field path.
            messages.append(msg if err.get("type") == "value_error" else f"{loc}: {msg}")
        return cls(messages)


def _require_finite(value: float, label: str) -> float:
    if not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number (got {value}).")
    return value


def _require(value: float, label: str, minimum: float = 0.0, strict: bool = True, unit: str = "") -> float:
    """Legacy `_require`: value must be > minimum (strict) or >= minimum."""
    v = float(value)
    if not math.isfinite(v) or (v <= minimum if strict else v < minimum):
        relation = "greater than" if strict else "at least"
        raise ValueError(f"{label} must be {relation} {minimum:g}{unit} (got {v:g}).")
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Sections ----------------------------------------------------------------

class GeometryConfig(_Frozen):
    """Part shape, size and placement. Which fields apply depends on `shape`."""

    shape: ShapeName = "Square/Rectangle"
    width_mm: float = 135.0              # flat shapes (also hexagon/circle diameter)
    height_mm: float = 25.0              # flat shapes (XY height, not Z)
    include_boundary: bool = False       # flat shapes: add one perimeter pass
    base_radius_mm: float = 10.0         # revolve outer radius / mushroom base
    disc_radius_mm: float = 15.0         # mushroom top disc
    stem_height_mm: float = 25.0         # revolve height / mushroom stem top (absolute Z)
    wall_thickness_mm: float = 2.5
    start_x_mm: float = 26.0
    start_y_mm: float = 40.0
    stl_path: str | None = None
    # STL slicing (see docs/SLICER_BENCHMARK.md #1). "largest" keeps one
    # island per slice (legacy); "all" prints every island in one stroke,
    # bridging between them.
    stl_islands: Literal["largest", "all"] = "largest"
    # Gaps narrower than ~2x this in a slice are closed (Orca default).
    stl_closing_radius_mm: float = 0.049
    # Holes in a broken mesh leave slice contours open; gaps up to this are
    # bridged (Cura-style stitching). 0 = drop open contours.
    stl_stitch_mm: float = 5.0
    # Multiply the mesh by this on import: 25.4 for a model saved in inches,
    # 10 for cm, 1000 for metres.
    stl_scale: float = 1.0
    # "Custom Outline": the part outline and holes (mm, placed at start_x/y),
    # e.g. a structural PCB drawn in the editor.
    custom_outline: tuple[tuple[float, float], ...] = ()
    custom_holes: tuple[tuple[tuple[float, float], ...], ...] = ()
    # Layer recipe (Phase 44), by hole number (1 = the first hole drawn):
    # bolt holes are wrapped first at layer changes; drill-after holes are
    # printed over and drilled after curing (drilling cuts the fibre there).
    bolt_holes: tuple[int, ...] = ()
    drill_holes: tuple[int, ...] = ()
    # laps a wrap gives each hole (1 to 5), as drawn; 0 or none = Hole Rings
    hole_ring_counts: tuple[int, ...] = ()

    @field_validator("hole_ring_counts")
    @classmethod
    def _hole_ring_counts(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        for k in v:
            if not 0 <= k <= 5:
                raise ValueError(f"a hole's ring count must be 0 (the Hole Rings setting) to 5, not {k}")
        return v

    @field_validator("stl_scale")
    @classmethod
    def _scale(cls, v: float) -> float:
        return _require(v, "STL Scale")

    @field_validator("stl_stitch_mm")
    @classmethod
    def _stitch(cls, v: float) -> float:
        return _require(v, "STL Stitch Gap", strict=False, unit=" mm")

    @field_validator("stl_closing_radius_mm")
    @classmethod
    def _closing_radius(cls, v: float) -> float:
        return _require(v, "STL Closing Radius", strict=False, unit=" mm")

    @property
    def is_specialty(self) -> bool:
        """True for every Z shape (the legacy `is_3d`)."""
        return self.shape in SPECIALTY_SHAPES

    @property
    def is_revolve(self) -> bool:
        return self.shape in REVOLVE_SHAPES

    @property
    def is_flat_specialty(self) -> bool:
        return self.shape in FLAT_SPECIALTY_SHAPES


class MaterialConfig(_Frozen):
    """Inputs of the extrusion constant (see `ProcessConfig.e_const`)."""

    needle_dia_mm: float = 0.8
    e_multiplier: float = 1.6
    density_g_per_mm3: float = 0.00115
    fiber_weight_g_per_mm: float = 0.55 / (math.pi * 39)
    # Legacy configs stored the computed constant directly; when set it wins.
    e_const_override: float | None = None

    @field_validator("fiber_weight_g_per_mm")
    @classmethod
    def _weight_positive(cls, v: float) -> float:
        return _require(v, "Fiber Weight", unit=" g/mm")


class KinematicsConfig(_Frozen):
    """Speed-planner settings (flat + revolve) and specialty fixed speeds."""

    v_max_mm_s: float = 10.0
    v_min_mm_s: float = 5.0
    accel_mm_s2: float = 25.0
    decel_mm_s2: float = 15.0
    pressure_advance_mm: float = 3.0
    # UNMEASURED by default: equals v_min, i.e. no real stall protection.
    pump_stall_floor_mm_s: float = 5.0
    # Optional measured pressure advance: ((speed mm/s, advance mm), ...),
    # interpolated at each point's speed. Empty = the fixed value above.
    pressure_advance_table: tuple[tuple[float, float], ...] = ()
    # Specialty shapes only:
    v_z_mm_s: float = 2.0
    v_overhang_mm_s: float = 5.0
    dwell_s: float = 1.5

    @field_validator("v_max_mm_s")
    @classmethod
    def _v_max_positive(cls, v: float) -> float:
        if not (math.isfinite(v) and v > 0):
            raise ValueError("Print Speed (Max) must be greater than 0 mm/s.")
        return v

    @field_validator(
        "v_min_mm_s", "accel_mm_s2", "decel_mm_s2", "pressure_advance_mm",
        "pump_stall_floor_mm_s", "v_z_mm_s", "v_overhang_mm_s", "dwell_s",
    )
    @classmethod
    def _finite(cls, v: float, info) -> float:
        return _require_finite(v, info.field_name)

    @field_validator("pressure_advance_table")
    @classmethod
    def _pa_table(cls, v):
        for speed, adv in v:
            _require(speed, "Pressure advance table speed", strict=False, unit=" mm/s")
            _require(adv, "Pressure advance table advance", strict=False, unit=" mm")
        return v

    @property
    def pump_floor_unmeasured(self) -> bool:
        """True while the pump floor still sits at its placeholder (= v_min)."""
        return self.pump_stall_floor_mm_s == self.v_min_mm_s


class GcodeConfig(_Frozen):
    """Start-sequence settings. Feedrates are mm/min (G-code units)."""

    uv_pwm: int = 225
    purge_e: float = 15.0
    purge_f_mm_min: float = 60.0
    travel_f_mm_min: float = 600.0

    @field_validator("purge_f_mm_min")
    @classmethod
    def _purge_f(cls, v: float) -> float:
        return _require(v, "Purge Speed")

    @field_validator("travel_f_mm_min")
    @classmethod
    def _travel_f(cls, v: float) -> float:
        return _require(v, "Approach Speed")

    @field_validator("purge_e")
    @classmethod
    def _purge_e(cls, v: float) -> float:
        return _require(v, "Purge Amount", strict=False)


# --- Whole process -----------------------------------------------------------

class ProcessConfig(_Frozen):
    """Everything one slice needs, apart from the machine profile."""

    geometry: GeometryConfig = GeometryConfig()
    material: MaterialConfig = MaterialConfig()
    kinematics: KinematicsConfig = KinematicsConfig()
    gcode: GcodeConfig = GcodeConfig()

    layers: int = 1
    layup_deg: tuple[float, ...] = (0.0,)
    # The layup as the user typed it, for the G-code header. None = derive.
    layup_text: str | None = None
    infill_type: InfillPattern = ARCHIMEDEAN
    tracks_per_layer: int = 7
    track_gap_mm: float = 1.2
    min_radius_mm: Number = 3.0
    layer_height_mm: Number = 0.2
    z_offset_mm: float = 0.2
    transition_angle_deg: float = 30.0
    seam_trim_mm: float = 3.0
    anchor_length_mm: Number = 15.0
    # Round revolve shells: rotate each layer's seam by this much (0 = all
    # seams on one vertical line, as before). E.g. 137.5 spreads them evenly.
    seam_stagger_deg: float = 0.0
    # Flat parts and STL slices: lay as many spiral tracks as fit instead of
    # tracks_per_layer (zigzags always fill; revolve walls keep their count).
    fill_solid: bool = False
    # Lay the FIRST layer solid (as many tracks as fit) whatever the other
    # layers use: a closed base for the laminate. Off = like the others.
    first_layer_solid: bool = False
    # Pattern for that first layer (None = the same as the other layers). A
    # spiral reaches furthest into the outline (zigzag line ends stay r_min in).
    first_layer_infill: InfillPattern | None = None
    # Warn when the first layer's fibre coverage is below this (0..1, 0 = no check).
    first_layer_min_coverage: float = 0.0
    # Solid skins: this many layers at the bottom / top of a flat part are
    # filled solid with the First Layer Pattern, whatever the other layers
    # use; on an STL part, also every slice whose top or bottom is exposed
    # (the top of a base under thinner walls, a roof). 0 = off.
    solid_bottom_layers: int = 0
    solid_top_layers: int = 0
    # Zigzags (Oriented Serpentine, 45° Diagonal Hatch): keep every U-turn at
    # the min bend radius. Lines are then 2·r_min apart (a whole number of
    # gaps) and each layer at the same angle is shifted one gap, so that many
    # layers together fill the area. Off = lines one gap apart, turns tighter
    # than r_min (slowed, curvature warning).
    zigzag_respect_radius: bool = False
    # Archimedean / Corner-Step: fill the hollow the rings leave in the middle
    # (no ring fits inside twice the bend radius) with short back-and-forth
    # lines. Their turns are tighter than the bend radius (slowed, reported).
    fill_centre: bool = False
    # Strict bend radius (Phase 43): no fibre bend tighter than min_radius_mm
    # anywhere -- zigzag turns, joins, the Fermat centre turn, Fill Centre
    # lines and layer climbs. A bend that cannot be kept is replaced by a
    # curve at the radius, or that spot is left empty and reported; the audit
    # fails on any bend under 0.98·r_min. Off = tight bends are slowed and
    # warned. The GUI turns it on for new projects; saved files keep theirs.
    strict_bend_radius: bool = False
    # Fill Pockets (Phase 44): spots every layer below left empty are filled
    # first -- Auto (Best Fit) scores covering them double, so layers take
    # turns instead of all leaving the same spots (a cavity through the
    # laminate). Flat parts.
    fill_pockets: bool = False
    # Layer recipe (Phase 44), flat parts with a zigzag or Fermat core:
    # `edge_rings` rings along the outline (bending round the holes) before /
    # after the core; with `wrap_holes` an inward layer ends by spiralling
    # into a hole's `hole_rings` rings and the next layer starts by spiralling
    # out of them, so that hole gets full laps in two layers (bolt holes
    # first; every hole when none is marked). 0 / off = today's layers.
    edge_rings: int = 0
    hole_rings: int = 2
    wrap_holes: bool = False

    # -- unconditional field rules (legacy messages) --
    @field_validator("track_gap_mm")
    @classmethod
    def _gap(cls, v: float) -> float:
        if not (math.isfinite(v) and v > 0):
            raise ValueError("Track Gap must be greater than 0 mm.")
        return v

    @field_validator("min_radius_mm")
    @classmethod
    def _radius(cls, v: Number) -> Number:
        if not math.isfinite(v) or v < 0:
            raise ValueError("Min Bend Radius cannot be negative.")
        return v

    @field_validator("tracks_per_layer")
    @classmethod
    def _tracks(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Tracks Per Layer must be at least 1.")
        return v

    @field_validator("layers")
    @classmethod
    def _layers(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Total Layers must be at least 1.")
        return v

    @field_validator("layer_height_mm")
    @classmethod
    def _layer_height(cls, v: Number) -> Number:
        return _require(v, "Layer Height", unit=" mm")

    @field_validator("first_layer_min_coverage")
    @classmethod
    def _first_cov(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("First layer minimum coverage must be between 0 and 1 (0-100 %).")
        return v

    @field_validator("solid_bottom_layers", "solid_top_layers")
    @classmethod
    def _skins(cls, v: int) -> int:
        if not 0 <= v <= 1000:
            raise ValueError("Solid Bottom / Top Layers must be between 0 and 1000.")
        return v

    @field_validator("edge_rings", "hole_rings")
    @classmethod
    def _rings(cls, v: int) -> int:
        if not 0 <= v <= 5:
            raise ValueError("Edge Rings and Hole Rings must be between 0 and 5.")
        return v

    @field_validator("seam_stagger_deg")
    @classmethod
    def _stagger(cls, v: float) -> float:
        return _require_finite(v, "Seam Stagger")

    @field_validator("layup_deg")
    @classmethod
    def _layup(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        if not v:
            raise ValueError("Layup must contain at least one angle.")
        for angle in v:
            _require_finite(angle, "Layup angle")
        return v

    # -- shape-dependent rules --
    @model_validator(mode="after")
    def _shape_rules(self) -> ProcessConfig:
        _require(self.e_const, "Extrusion constant (check the Materials panel)")
        g, k = self.geometry, self.kinematics
        if g.is_flat_specialty:
            _require(k.v_z_mm_s, "Z Pull Speed", unit=" mm/s")
            if g.shape == "3D Overhang (Mushroom)":
                _require(k.v_overhang_mm_s, "Top Disc Overhang Speed", unit=" mm/s")
                _require(k.dwell_s, "Top Dwell", strict=False, unit=" s")
        else:
            # Every shape that runs the cornering-speed planner.
            _require(k.accel_mm_s2, "Acceleration")
            _require(k.decel_mm_s2, "Deceleration")
            _require(k.v_min_mm_s, "Min Bend Speed", strict=False, unit=" mm/s")
            _require(k.pump_stall_floor_mm_s, "Pump-Stall Floor", strict=False)
            _require(k.pressure_advance_mm, "Pressure Advance", strict=False)
            _require(self.anchor_length_mm, "Lead-in Anchor Length", strict=False, unit=" mm")
            _require(self.seam_trim_mm, "Seam Trim Length", strict=False, unit=" mm")

        if g.shape == "Custom Outline":
            from shapely.geometry import Polygon
            if len(g.custom_outline) < 3:
                raise ValueError("Custom Outline needs an outline of at least 3 points (draw one in the "
                                 "Structural PCB editor).")
            poly = Polygon(g.custom_outline, [h for h in g.custom_holes if len(h) >= 3]).buffer(0)
            if poly.is_empty or poly.area <= 0:
                raise ValueError("The custom outline encloses no area.")
            minx, miny, maxx, maxy = poly.bounds
            if self.min_radius_mm * 2.0 + self.track_gap_mm >= min(maxx - minx, maxy - miny):
                raise ValueError(
                    "The custom outline is too narrow for two bend radii plus one track gap. "
                    "Reduce Min Bend Radius or Track Gap, or enlarge the outline."
                )
        elif g.shape not in SPECIALTY_SHAPES and g.shape != "STL Import":
            width, height = float(g.width_mm), float(g.height_mm)
            if width <= 0 or height <= 0:
                raise ValueError("2D geometry width and height must be greater than 0 mm.")
            if self.min_radius_mm * 2.0 + self.track_gap_mm >= min(width, height):
                raise ValueError(
                    "This combination is geometrically impossible: Width/Height must "
                    "leave room for two bend radii plus one track gap. Reduce Min Bend "
                    "Radius or Track Gap, or enlarge the specimen."
                )
        if g.shape == "STL Import" and not g.stl_path:
            raise ValueError("STL Import requires a file path. Select an STL file.")
        return self

    # -- derived values --
    @property
    def e_const(self) -> float:
        """Extrusion constant: E per mm of travel at full speed.

        e_const = needle_dia · layer_height · E_multiplier · density / fiber_weight
        (dimensionless as written; see docs/ARCHITECTURE.md, mismatch 1).
        """
        m = self.material
        if m.e_const_override is not None:
            return m.e_const_override
        return (m.needle_dia_mm * self.layer_height_mm * m.e_multiplier * m.density_g_per_mm3) / m.fiber_weight_g_per_mm

    @property
    def layup_label(self) -> str:
        """Layup for display: the user's text if known, else derived."""
        if self.layup_text is not None:
            return self.layup_text
        return ", ".join(f"{a:g}" for a in self.layup_deg)


def build_config(data: dict) -> ProcessConfig:
    """Validate a nested dict into a ProcessConfig, raising ConfigError
    (plain-language messages) instead of pydantic's ValidationError."""
    try:
        return ProcessConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(exc) from exc
