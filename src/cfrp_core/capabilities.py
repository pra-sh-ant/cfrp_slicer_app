"""Which settings and infill patterns apply to a setup, and why not.

Front ends grey out what does not apply and show the reason; the rules are
read off the slicer itself (which builder reads which field) and unit-tested
against it. Input is the flat parameter dict front ends already produce
(legacy keys, see `legacy._FIELD_MAP`); missing keys take model defaults.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import (
    ARCHIMEDEAN,
    AUTO,
    CORNER_STEP,
    DIAGONAL_45,
    FERMAT,
    FLAT_SPECIALTY_SHAPES,
    ORIENTED_SERPENTINE,
    REVOLVE_SHAPES,
)

INFILL_PATTERNS = (ARCHIMEDEAN, FERMAT, ORIENTED_SERPENTINE, DIAGONAL_45, CORNER_STEP, AUTO)
SPIRALS = (ARCHIMEDEAN, FERMAT, CORNER_STEP)
ZIGZAGS = (ORIENTED_SERPENTINE, DIAGONAL_45)
RECIPE_CORES = (*ZIGZAGS, FERMAT)        # layers the layer recipe shapes (layers/recipe.py)

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


def _u_turn_caution(p: dict, pattern: str = "") -> str:
    """Why a pattern that turns back between adjacent tracks will show
    curvature warnings (or, with Strict Bend Radius, what it does instead),
    or '' when the gap allows a turn at r_min."""
    gap, r = _num(p, "track_gap", 1.2), _num(p, "min_radius", 3.0)
    if r > 0 and gap < 2.0 * r and p.get("strict_bend_radius", False):
        if pattern == FERMAT:
            return (f"Strict Bend Radius: the turn between the spiral's two arms ({gap / 2:g} mm radius) "
                    f"becomes a loop at the {r:g} mm min bend radius, or the spiral turns a ring earlier.")
        k = max(1, math.ceil(2.0 * r / gap - 1e-9))
        return (f"Strict Bend Radius: zigzag lines are {k * gap:g} mm apart so their turns keep the "
                f"{r:g} mm min bend radius; {k} layers at one angle fill the area once.")
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
    strict = bool(p.get("strict_bend_radius", False))
    states = {}
    for name in INFILL_PATTERNS:
        if name == AUTO:
            states[name] = (_off("Z shells are concentric rings only (Archimedean or Fermat).")
                            if family == "revolve" else ON)
            continue
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
        if name in (FERMAT, *ZIGZAGS) and caution:
            why = _u_turn_caution(p, name) if strict else caution
            states[name] = OptionState(True, why, caution=True)
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
    elif (_recipe_layers(p, family) == "all" and int(_num(p, "edge_rings", 0)) > 0
          and not (shape == CUSTOM and p.get("stl_islands") == "all")):    # (pieces: no recipe)
        s["include_boundary"] = _off("Edge Rings already run along the outline.")
    else:
        s["include_boundary"] = ON
    stl_only = _off("Only for STL Import.")
    for key in ("stl_path", "stl_scale"):
        s[key] = ON if family == "stl" else stl_only
    # A Custom Outline can be split into pieces by its holes, like a slice.
    s["stl_islands"] = (ON if family == "stl" or shape == CUSTOM
                        else _off("Only for STL Import and the Custom Outline (pieces split by holes)."))
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
            elif infill == AUTO:
                s["fill_solid"] = _off("Auto (Best Fit) fills every layer: each pattern it tries is solid.")
                s["tracks_per_layer"] = _off("Auto (Best Fit) fills every layer; the track count is not used.")
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
        for key in ("first_layer_solid", "first_layer_infill", "first_layer_min_coverage",
                    "solid_bottom_layers", "solid_top_layers"):
            s[key] = _off(why)
    else:
        s["first_layer_solid"] = ON
        s["first_layer_infill"] = ON
        s["first_layer_min_coverage"] = ON
        s["solid_bottom_layers"] = ON
        s["solid_top_layers"] = ON
    rings = (infill in (ARCHIMEDEAN, CORNER_STEP, AUTO)
             or p.get("first_layer_infill") in (ARCHIMEDEAN, CORNER_STEP, AUTO))
    s["fill_centre"] = (ON if rings and family in ("flat", "stl")
                        else _off("Only for Archimedean and Corner-Step on flat and STL parts (the rings "
                                  "leave the middle open; zigzags fill it anyway)."))
    zz = (infill in (*ZIGZAGS, AUTO) or p.get("first_layer_infill") in (*ZIGZAGS, AUTO)) and family in ("flat", "stl")
    if p.get("strict_bend_radius", False) and family in ("flat", "stl"):
        s["zigzag_respect_radius"] = _off("Strict Bend Radius already keeps every zigzag turn at the min "
                                          "bend radius.")
    else:
        s["zigzag_respect_radius"] = (ON if zz else _off("Only for zigzag patterns (Oriented Serpentine, "
                                                          "45° Diagonal Hatch) on flat and STL parts."))
    auto = infill == AUTO or p.get("first_layer_infill") == AUTO
    s["fill_pockets"] = (ON if auto and family == "flat" and layers > 1
                         else _off("Only for Auto (Best Fit) on flat parts with more than one layer: it "
                                   "chooses each layer's pattern by what the layers below left empty."))
    # --- layer recipe (Phase 44) ---------------------------------------------
    recipe = _recipe_layers(p, family)
    s["edge_rings"] = (ON if recipe else _off("Only for flat parts with a zigzag (Oriented Serpentine, 45° "
                                               "Diagonal Hatch) or Fermat pattern, on this or the first "
                                               "layer."))
    wrap = bool(p.get("wrap_holes", False))
    if not recipe:
        s["wrap_holes"] = s["edge_rings"]
    elif shape != CUSTOM:
        s["wrap_holes"] = _off("Only for the Custom Outline: other flat shapes have no holes.")
    elif layers <= 1:
        s["wrap_holes"] = _off("A wrap takes two layers: one spirals into the hole, the next out of it.")
    else:
        s["wrap_holes"] = ON
    for key in ("hole_rings", "bolt_holes"):
        if not s["wrap_holes"].enabled:
            s[key] = s["wrap_holes"]
        else:
            s[key] = ON if wrap else _off("Only with Wrap Holes.")
    s["drill_holes"] = ON if shape == CUSTOM else _off("Only for the Custom Outline (its holes are numbered "
                                                       "in drawing order).")
    s["strict_bend_radius"] = (ON if family in ("flat", "stl")
                               else _off("Only for flat and STL parts: Z shells and the Mushroom have "
                                         "their own fixed paths."))
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
    elif family == "flat" and infill in (ARCHIMEDEAN, AUTO) and layers > 1:
        s["seam_stagger"] = ON
    elif family == "flat" and infill in (ARCHIMEDEAN, AUTO):
        s["seam_stagger"] = _off("Only one layer: there are no layer seams to stagger.")
    else:
        s["seam_stagger"] = _off("Only for round Z shells and flat Archimedean parts (it twists the "
                                 "spiral so each layer's seam moves round).")
    for key in ("v_z", "v_overhang", "dwell"):
        s[key] = ON if family == "mushroom" else mushroom_only
    return s


def _recipe_layers(p: dict, family: str) -> str:
    """Which layers the layer recipe can shape: "all", "some" (the first
    layer / skins or the main pattern only) or "" (none): flat parts, zigzag
    or Fermat layers."""
    if family != "flat":
        return ""
    main = p.get("infill_type", ARCHIMEDEAN) in RECIPE_CORES
    first = p.get("first_layer_infill")
    skin = main if first in (None, "", "Same as other layers") else first in RECIPE_CORES
    return "all" if main and skin else ("some" if main or skin else "")


def first_enabled(states: dict[str, OptionState], preferred: str) -> str:
    """`preferred` if enabled, else the first enabled option (or `preferred`)."""
    if states.get(preferred, ON).enabled:
        return preferred
    return next((k for k, v in states.items() if v.enabled), preferred)
