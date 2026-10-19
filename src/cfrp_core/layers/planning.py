"""Per-layer pattern planning shared by the flat and STL layer loops: which
pattern, how many tracks and which spiral direction each layer gets."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import ARCHIMEDEAN, CORNER_STEP, DIAGONAL_45, FERMAT, ORIENTED_SERPENTINE, ProcessConfig
from ..geometry.corner_step import is_rectangle
from ..geometry.laminate import max_track_count

_SPIRAL_INFILLS = (ARCHIMEDEAN, FERMAT, CORNER_STEP)
_CENTRE_SPIRALS = (ARCHIMEDEAN, CORNER_STEP)
_ZIGZAGS = (ORIENTED_SERPENTINE, DIAGONAL_45)
_POLY_FILL_TRACKS = 100000   # Corner-Step on a polygon stops by itself when rings run out


@dataclass(frozen=True)
class LayerPlan:
    """What one layer prints. `index` is the layer index handed to the
    generators: its parity sets a spiral's direction (even = outside-in,
    odd = inside-out) and alternates zigzags. `lead_in`: anchor length to
    lay on the innermost ring (an inside-out first layer), else 0."""
    pattern: str
    solid: bool
    index: int
    lead_in: float = 0.0
    skin: bool = False
    note: str = ""


def plan_layers(config: ProcessConfig, n: int, skins=None, offset: int = 0) -> list[LayerPlan]:
    """Pattern, fill and direction of each of `n` layers (numbered from
    offset + 1 in notes).

    Skins -- the first Solid Bottom Layers and last Solid Top Layers, or
    `skins[i]` when given (exposed STL slices), plus the first layer with
    First Layer Solid -- are filled solid. They and the first layer use the
    skin pattern (First Layer Pattern; default the main pattern).

    Spiral directions (Archimedean, Corner-Step): a run of spiral layers
    that hands over to a zigzag (or Fermat) layer ends inside-out, on the
    perimeter where that layer starts (ending
    in the centre, the next layer had to cut across its own lines to get
    out: up to 19 crossings), and one that follows a zigzag starts
    outside-in, from the perimeter where the zigzag ended (inside-out, the
    climb ran in to the centre and every ring of that layer crossed it).
    Within a run directions alternate. A run of an odd number of spiral
    layers with zigzags on both sides cannot do both; its last layer becomes
    a Fermat spiral, which enters and leaves on the perimeter."""
    if n <= 0:
        return []
    bottom, top = max(int(config.solid_bottom_layers), 0), max(int(config.solid_top_layers), 0)
    skins = [i < bottom or i >= n - top for i in range(n)] if skins is None else [bool(s) for s in skins]
    if config.first_layer_solid:
        skins[0] = True
    skin_pattern = config.first_layer_infill or config.infill_type
    patterns = [skin_pattern if (skins[i] or i == 0) else config.infill_type for i in range(n)]
    solid = [bool(config.fill_solid or skins[i]) for i in range(n)]
    index = list(range(n))
    notes = [""] * n
    # Spirals that end in the centre (or start there). Fermat enters and
    # leaves on the perimeter either way (its direction only reverses it),
    # so like a zigzag it needs no planning.
    spiral = [p in _CENTRE_SPIRALS for p in patterns]
    if any(spiral) and not all(spiral):
        i = 0
        while i < n:
            if not spiral[i]:
                i += 1
                continue
            a = i
            while i < n and spiral[i]:
                i += 1
            b = i - 1                                   # spiral run a..b
            after_zig = a > 0
            before_zig = b + 1 < n
            if before_zig and not after_zig:
                dirs = ["out" if (b - k) % 2 == 0 else "in" for k in range(a, b + 1)]
            else:
                dirs = ["in" if (k - a) % 2 == 0 else "out" for k in range(a, b + 1)]
            if after_zig and before_zig and dirs[-1] == "in":
                if patterns[b] != FERMAT:
                    notes[b] = (f"Layer {b + 1 + offset} ({patterns[b]}) lies between zigzag layers; it is "
                                f"printed as a Fermat spiral so that it starts and ends on the perimeter.")
                patterns[b] = FERMAT
            for k, d in zip(range(a, b + 1), dirs, strict=True):
                index[k] = k if (k % 2 == 0) == (d == "in") else k + 1
    plans = []
    for i in range(n):
        lead = 0.0
        if i == 0 and index[0] % 2 == 1 and patterns[0] in (ARCHIMEDEAN, CORNER_STEP) \
                and not config.geometry.include_boundary:
            lead = float(config.anchor_length_mm)
        plans.append(LayerPlan(patterns[i], solid[i], index[i], lead, skins[i], notes[i]))
    return plans


def skin_summary(plans, offset: int = 0) -> str:
    """'Solid skins: layer(s) 1-2, 9-10 are filled solid (...).' or '' when
    there is at most one (First Layer Solid says that already)."""
    layers = [k + 1 + offset for k, p in enumerate(plans) if p.skin]
    if len(layers) <= 1:
        return ""
    spans, start = [], layers[0]
    for prev, cur in zip(layers, [*layers[1:], None], strict=True):
        if cur != prev + 1:
            spans.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = cur
    pattern = next(p.pattern for p in plans if p.skin)
    return f"Solid skins: layer(s) {', '.join(spans)} are filled solid ({pattern})."


def stl_skins(regions, bottom: int, top: int, share: float = 0.2) -> list[bool]:
    """Skin flags for STL slices. A slice is a top skin when it is one of
    the last `top` slices, or when more than `share` of its area is not
    covered by all of the next `top` slices (the top of a solid base under
    thinner walls, a step, a roof); bottom skins likewise with the previous
    `bottom` slices (the first slices, the underside of an overhang). Empty
    slices (None) are never skins."""
    n = len(regions)
    out = []
    for i, reg in enumerate(regions):
        if reg is None or reg.is_empty or reg.area <= 0:
            out.append(False)
            continue
        skin = False
        for count, step in ((top, 1), (bottom, -1)):
            if count <= 0:
                continue
            others = []
            k = i + step
            while 0 <= k < n and len(others) < count:
                if regions[k] is not None and not regions[k].is_empty:
                    others.append(regions[k])
                k += step
            if len(others) < count:
                skin = True                     # within `count` slices of the first / last one
                break
            covered = others[0]
            for o in others[1:]:
                covered = covered.intersection(o)
            if reg.difference(covered).area > share * reg.area:
                skin = True
                break
        out.append(skin)
    return out


def _points_within(path, length):
    """How many leading points of `path` lie within `length` mm of travel."""
    run = 0.0
    for k in range(1, len(path)):
        run += math.dist(path[k - 1], path[k])
        if run > length:
            return k
    return len(path)


def _fill_track_count(poly, config: ProcessConfig, pattern):
    """Track count for Fill Solid: None (as many as fit) for the spirals that
    stop by themselves; the exact fit for the analytic Corner-Step rectangle
    (0 if none); zigzags fill regardless."""
    if pattern == CORNER_STEP:
        if is_rectangle(poly):
            return max_track_count(poly, config.track_gap_mm, config.min_radius_mm, CORNER_STEP,
                                   config.geometry.include_boundary) or 0
        return _POLY_FILL_TRACKS
    if pattern in (ARCHIMEDEAN, FERMAT):
        return None
    return config.tracks_per_layer
