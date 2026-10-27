"""Wind turbine kit: matching settings for every printed part of a rotor,
from one set of settings (the flat parameter dict front ends use), plus the
hub's drilling template and plain assembly notes.

    blade       Wind Turbine Blade (Z), printed once per blade
    hub_disc    Wind Turbine Hub, printed twice (in front of and behind the roots)
    nose_cone   Conical Dome (Z) over the front disc, covering the screw heads

Every part keeps the material, machine and speed settings it is given, so
the parts fit each other: the hub's seats and screws come from the blade's
root, the cone's size from the hub's screws. Bought: screws, nuts and
washers, a flange shaft coupler, the shaft and generator.
"""
from __future__ import annotations

from pathlib import Path

from .config import ARCHIMEDEAN, BLADE, HUB
from .geometry.blade import root_label, root_size
from .geometry.hub import hub_layout, hub_template_svg
from .legacy import from_legacy_dict
from .project import save_project

CONE = "Conical Dome (Z)"
CONE_HEIGHT_MM = 40.0
PARTS = {"blade": "blade", "hub_disc": "hub disc", "nose_cone": "nose cone"}


def kit_parts(params: dict) -> dict[str, dict]:
    """{part: its settings}, each a copy of `params` set for that part.
    Raises ValueError (plain words) for settings no hub fits."""
    p = dict(params)
    p.pop("hold_points", None)                  # heights differ per part: set them on each
    blade = dict(p, shape=BLADE, infill_type=ARCHIMEDEAN, hold_points=params.get("hold_points", ""))
    hub = dict(p, shape=HUB, infill_type=ARCHIMEDEAN)
    lay = hub_layout(from_legacy_dict(hub).geometry)
    cone = dict(p, shape=CONE, infill_type=ARCHIMEDEAN, base_radius=round(lay.cone_radius, 1),
                stem_height=CONE_HEIGHT_MM, tracks_per_layer=min(int(p.get("tracks_per_layer", 3)), 3))
    return {"blade": blade, "hub_disc": hub, "nose_cone": cone}


def assembly_notes(params: dict) -> str:
    """Plain-text parts list and assembly steps for the kit."""
    parts = kit_parts(params)
    g = from_legacy_dict(parts["hub_disc"]).geometry
    lay = hub_layout(g)
    _w, t = root_size(g)
    root = root_label(g)
    n, disc = lay.blades, float(g.hub_thickness_mm)
    screws = len(lay.screws)
    blade_len = float(parts["blade"].get("stem_height", g.stem_height_mm))
    lines = [
        f"Wind turbine rotor: {n} blades, about {2 * (lay.start + lay.seat_length + blade_len):.0f} mm across",
        "",
        "PRINT (open each project in CFRP Slicer, check it, Slice, Save G-code)",
        f"  blade.cfrpproj      x{n:<3} root {root}, {g.blade_root_length_mm:g} mm long, then a {blade_len:g} mm blade",
        f"  hub_disc.cfrpproj   x2   disc {2 * lay.radius:.1f} mm across, {disc:g} mm thick, {lay.shaft:g} mm shaft hole",
        f"  nose_cone.cfrpproj  x1   cone {lay.cone_radius:.0f} mm radius, {CONE_HEIGHT_MM:g} mm tall",
        "",
        "BUY",
        f"  {screws} x M{lay.screw:g} x {lay.screw_length(disc)} mm screws, {screws} nuts, {2 * screws} washers",
        f"  1 x flange shaft coupler for your shaft ({lay.shaft:g} mm hole in the discs), with its screws",
        "  generator with shaft; epoxy",
        "",
        "AFTER CURING",
        "  1. Trim each part's lead-in anchor tail (the short start on the bed).",
        "  2. Print hub_drilling_template.svg at 100 % (check its 100 mm bar). Clamp both discs together,",
        f"     tape the template on, drill the {screws} crosses at {lay.drill:g} mm.",
        "  3. Drill the coupler's flange holes through one disc: that is the back disc.",
        "  4. Weigh the blades; sand the heaviest until they match.",
        "  5. Lay the back disc down. Put each blade's root on its dashed seat, the airfoil's leading edge",
        "     (its round nose) the way the arrow points and its flatter side facing up, towards the front",
        "     (the wind). All blades turn anticlockwise, seen from the front.",
        f"  6. Front disc on top ({t:g} mm above, resting on the roots), a screw through each pair of holes",
        "     with a washer each side; tighten evenly, then run epoxy along each root's edges.",
        "  7. Bolt the coupler to the back disc and fit it on the shaft. Glue the nose cone over the front disc.",
        "  8. Spin it by hand: it should stop anywhere. If one side keeps coming down, sand that blade's tip.",
    ]
    return "\n".join(lines) + "\n"


def write_kit(folder, params: dict, app: str = "") -> list[Path]:
    """Write the kit's projects, drilling template and notes into `folder`
    (made if missing); returns the files written."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    parts = kit_parts(params)
    written = [save_project(folder / f"{name}.cfrpproj", p, app=app) for name, p in parts.items()]
    lay = hub_layout(from_legacy_dict(parts["hub_disc"]).geometry)
    svg = folder / "hub_drilling_template.svg"
    svg.write_text(hub_template_svg(lay), encoding="utf-8")
    notes = folder / "assembly.txt"
    notes.write_text(assembly_notes(params), encoding="utf-8")
    return written + [svg, notes]
