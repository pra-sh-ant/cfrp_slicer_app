"""Which settings and infill patterns apply to a setup, and why not.

Front ends grey out what does not apply and show the reason; the rules are
read off the slicer itself (which builder reads which field) and unit-tested
against it. Input is the flat parameter dict front ends already produce
(legacy keys, see `legacy._FIELD_MAP`); missing keys take model defaults.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import (
    ARCHIMEDEAN,
    CORNER_STEP,
    DIAGONAL_45,
    FERMAT,
    FLAT_SPECIALTY_SHAPES,
    ORIENTED_SERPENTINE,
    REVOLVE_SHAPES,
)

INFILL_PATTERNS = (ARCHIMEDEAN, FERMAT, ORIENTED_SERPENTINE, DIAGONAL_45, CORNER_STEP)
SPIRALS = (ARCHIMEDEAN, FERMAT, CORNER_STEP)
ZIGZAGS = (ORIENTED_SERPENTINE, DIAGONAL_45)

STL = "STL Import"
CUSTOM = "Custom Outline"
FLAT_SHAPES = frozenset({"Square/Rectangle", "Triangle", "Hexagon", "Circle", CUSTOM})
SQUARE_TUBE = "Hollow Square Tube (Z)"
SPHERE = "Hollow Sphere (Z)"


@dataclass(frozen=True)
class OptionState:
    """`enabled` False: the setting has no effect here (`reason` says why).
    `caution`: it works but expect a validation warning (`reason` says why)."""
    enabled: bool = True
    reason: str = ""
    caution: bool = False


ON = OptionState()


def _off(reason: str) -> OptionState:
    return OptionState(False, reason)


def _family(shape: str) -> str:
    if shape in REVOLVE_SHAPES:
        return "revolve"
    if shape in FLAT_SPECIALTY_SHAPES:
        return "mushroom"
    if shape == STL:
        return "stl"
    return "flat"


def _num(p: dict, key: str, default: float) -> float:
    try:
        return float(p.get(key, default))
    except (TypeError, ValueError):
        return default


def _u_turn_caution(p: dict) -> str:
    """Why a pattern that turns back between adjacent tracks will show
    curvature warnings, or '' when the gap allows a turn at r_min."""
    gap, r = _num(p, "track_gap", 1.2), _num(p, "min_radius", 3.0)
    if r > 0 and gap < 2.0 * r:
        return (f"U-turns between tracks {gap:g} mm apart are at most {gap / 2:g} mm radius, below the "
                f"{r:g} mm min bend radius: they are slowed and reported as curvature warnings. "
                f"A Track Gap of {2 * r:g} mm or more avoids this.")
    return ""


def infill_states(p: dict) -> dict[str, OptionState]:
    """State of every infill pattern for the setup in `p`."""
    shape = p.get("shape", "Square/Rectangle")
    family = _family(shape)
    if family == "mushroom":
        return {name: _off("The Mushroom overhang test has a fixed path (spiral base, stem, disc).")
                for name in INFILL_PATTERNS}
    caution = _u_turn_caution(p)
    states = {}
    for name in INFILL_PATTERNS:
        if family == "revolve":
            if name == ARCHIMEDEAN:
                states[name] = ON
            elif name == FERMAT:
                states[name] = (_off("On shells the slicer replaces Fermat with Archimedean: Fermat "
                                     "turns back between adjacent tracks on every layer. " + caution)
                                if caution else ON)
            else:
                states[name] = _off("Z shells are concentric rings only (Archimedean or Fermat).")
            continue
        if name == FERMAT and shape == CUSTOM and p.get("custom_holes"):
            states[name] = OptionState(True, "The Fermat spiral stops at the first hole, leaving most of a "
                                             "holed outline empty; Archimedean and Corner-Step bend their "
                                             "rings round holes. " + caution, caution=True)
        elif name in (FERMAT, *ZIGZAGS) and caution:
            states[name] = OptionState(True, caution, caution=True)
        else:
            states[name] = ON
    return states


def field_states(p: dict) -> dict[str, OptionState]:
    """State of every setting (GUI keys, mostly the legacy dict keys) for the
    setup in `p`. Keys not listed apply everywhere."""
    shape = p.get("shape", "Square/Rectangle")
    family = _family(shape)
    infill = p.get("infill_type", ARCHIMEDEAN)
    layers = int(_num(p, "layers", 1))
    fill = bool(p.get("fill_solid", False))
    has_pa_table = bool(str(p.get("pa_table", "") or "").strip())
    s: dict[str, OptionState] = {}

    # --- part geometry -----------------------------------------------------
    size_reason = {
        "revolve": "Z shells are sized by Outer Radius and Height.",
        "mushroom": "The Mushroom is sized by its base and disc radii.",
        "stl": "The model sets the size (use STL Scale to convert units).",
        "flat": "",
    }[family]
    if family == "flat" and shape == CUSTOM:
        size_reason = "A custom outline takes its size from the outline (PCB editor)."
    s["width"] = _off(size_reason) if size_reason else ON
    if size_reason:
        s["height"] = _off(size_reason)
    elif shape in ("Hexagon", "Circle"):
        s["height"] = _off(f"A {shape.lower()} uses Width as its diameter.")
    else:
        s["height"] = ON
    if family in ("revolve", "mushroom"):
        s["include_boundary"] = _off("Z shells and the Mushroom have no separate boundary pass.")
    elif infill == CORNER_STEP:
        s["include_boundary"] = _off("Corner-Step's outer ring already runs along the boundary.")
    else:
        s["include_boundary"] = ON
    stl_only = _off("Only for STL Import.")
    for key in ("stl_path", "stl_islands", "stl_scale"):
        s[key] = ON if family == "stl" else stl_only
    s["base_radius"] = (ON if family in ("revolve", "mushroom")
                        else _off("Only for Z shells and the Mushroom."))
    if family == "revolve":
        s["stem_height"] = (_off("A sphere's height is twice its radius.") if shape == SPHERE else ON)
    else:
        s["stem_height"] = _off("Only for Z shells.")
    mushroom_only = _off("Only for the 3D Overhang (Mushroom) test.")
    for key in ("disc_radius", "stem_top"):
        s[key] = ON if family == "mushroom" else mushroom_only
    s["custom_outline"] = ON if shape == CUSTOM else _off("Only for the Custom Outline shape.")

    # --- pattern and layers ------------------------------------------------
    if family == "mushroom":
        fixed = _off("The Mushroom overhang test has a fixed path.")
        for key in ("infill_type", "tracks_per_layer", "fill_solid", "layers", "layup_seq"):
            s[key] = fixed
    else:
        s["infill_type"] = ON
        if family == "revolve":
            s["layers"] = _off("Layers follow the shell: Height / Layer Height.")
            s["fill_solid"] = _off("A shell's wall thickness is set by Tracks Per Layer.")
            s["tracks_per_layer"] = ON
            s["layup_seq"] = _off("Shell rings follow the wall; there is no fibre angle to set.")
        else:
            s["layers"] = (_off("Layers follow the model: its height / Layer Height.")
                           if family == "stl" else ON)
            if infill in ZIGZAGS:
                s["fill_solid"] = _off("Zigzag patterns always fill the whole layer.")
                s["tracks_per_layer"] = _off("Zigzag patterns fill the whole layer; the track count "
                                             "is not used.")
            else:
                s["fill_solid"] = ON
                s["tracks_per_layer"] = (_off("Fill Solid lays as many tracks as fit.") if fill else ON)
            if infill in SPIRALS:
                s["layup_seq"] = _off("Spiral patterns follow the outline; there is no fibre angle "
                                      "to set. Layup applies to Oriented Serpentine.")
            elif infill == DIAGONAL_45:
                s["layup_seq"] = _off("45° Diagonal Hatch alternates +45°/-45° by itself.")
            else:
                s["layup_seq"] = ON

    # --- first layer -------------------------------------------------------------
    if family in ("revolve", "mushroom"):
        why = ("Shells have no flat base layer." if family == "revolve"
               else "The Mushroom overhang test has a fixed path.")
        for key in ("first_layer_solid", "first_layer_infill", "first_layer_min_coverage"):
            s[key] = _off(why)
    else:
        s["first_layer_solid"] = ON
        s["first_layer_infill"] = ON
        s["first_layer_min_coverage"] = ON
    for key in ("stl_closing_radius", "stl_stitch"):
        s[key] = ON if family == "stl" else stl_only

    # --- placement -----------------------------------------------------------
    stl_xy = _off("STL parts keep the model's own XY and are auto-positioned on the bed.")
    s["start_x"] = stl_xy if family == "stl" else ON
    s["start_y"] = stl_xy if family == "stl" else ON

    # --- motion ----------------------------------------------------------------
    fixed_speed = _off("The Mushroom runs at fixed speeds (Print Speed, Z Pull, Overhang).")
    for key in ("v_min", "accel", "decel", "pump_stall_floor", "anchor_length", "pa_table"):
        s[key] = fixed_speed if family == "mushroom" else ON
    if family == "mushroom":
        s["p_advance"] = fixed_speed
    elif has_pa_table:
        s["p_advance"] = _off("The PA Table sets the advance per speed; this fixed value is not used.")
    else:
        s["p_advance"] = ON
    if family == "mushroom":
        seams = fixed_speed
    elif family == "revolve":
        seams = _off("Z shells climb in one continuous helix; there are no layer seams to trim or ramp.")
    elif family == "flat" and layers <= 1:
        seams = _off("Only one layer: there is no layer-to-layer transition.")
    else:
        seams = ON
    s["transition_angle"] = seams
    s["seam_trim_length"] = seams
    if family == "revolve" and shape != SQUARE_TUBE:
        s["seam_stagger"] = ON
    elif shape == SQUARE_TUBE:
        s["seam_stagger"] = _off("The square tube keeps its seam on a straight side.")
    else:
        s["seam_stagger"] = _off("Only for round Z shells (rotates each layer's seam).")
    for key in ("v_z", "v_overhang", "dwell"):
        s[key] = ON if family == "mushroom" else mushroom_only
    return s


def first_enabled(states: dict[str, OptionState], preferred: str) -> str:
    """`preferred` if enabled, else the first enabled option (or `preferred`)."""
    if states.get(preferred, ON).enabled:
        return preferred
    return next((k for k, v in states.items() if v.enabled), preferred)
