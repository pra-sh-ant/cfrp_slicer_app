"""Layer recipe in the flat layer loop (Phase 44): which hole each layer
wraps, the way the wrap turns (the next layer spirals out of the same hole
the same way round), and the notes. Geometry in geometry/zones.py."""
from __future__ import annotations

import math
from collections import Counter

import shapely
from shapely.geometry import LineString, Polygon

from ..config import DIAGONAL_45, FERMAT, ORIENTED_SERPENTINE, ProcessConfig
from ..geometry import zones
from ..geometry.outlines import drawn_holes

RECIPE_CORES = (ORIENTED_SERPENTINE, DIAGONAL_45, FERMAT)    # start and end on their zone's edge


def recipe_wanted(config: ProcessConfig) -> bool:
    """True when the config asks for edge rings or hole wraps."""
    return int(config.edge_rings) > 0 or bool(config.wrap_holes)


def hole_notes(config: ProcessConfig) -> list[str]:
    """Notes on the hole numbers (Bolt Holes, Drill After Curing) of a
    Custom Outline: holes printed over, numbers with no such hole."""
    g = config.geometry
    if g.shape != "Custom Outline":
        return []
    drawn = sum(1 for h in g.custom_holes if len(h) >= 3)
    out = []
    lists = [("Drill After Curing", g.drill_holes)]
    if config.wrap_holes:
        lists.append(("Bolt Holes", g.bolt_holes))
    for name, nums in lists:
        bad = sorted({k for k in nums if not 1 <= k <= drawn})
        if bad:
            out.append(f"{name}: no hole {', '.join(map(str, bad))} (the outline has {drawn} hole(s), "
                       f"numbered 1 to {drawn} in drawing order).")
    drill = sorted({k for k in g.drill_holes if 1 <= k <= drawn})
    if drill:
        out.append(f"Hole(s) {', '.join(map(str, drill))} are printed over: drill them after curing "
                   f"(drilling cuts the fibre round them).")
    both = sorted(set(drill) & set(g.bolt_holes)) if config.wrap_holes else []
    if both:
        out.append(f"Hole(s) {', '.join(map(str, both))} are listed as bolt holes and drill-after holes: "
                   f"they are printed over, not wrapped.")
    return out


def _length(path):
    return LineString(path).length if len(path) > 1 else 0.0


class Recipe:
    """Builds recipe layers in turn for one flat part (`part`: its outline
    with holes, as placed). `holes` ({number: Polygon}): the holes to bend
    round and wrap (default: a Custom Outline's, as drawn; an STL slice
    gives its own)."""

    def __init__(self, config: ProcessConfig, part, holes=None):
        g = config.geometry
        self.part = part
        self.gap, self.r_min = float(config.track_gap_mm), float(config.min_radius_mm)
        self.edge = int(config.edge_rings)
        self.core_pattern = config.infill_type
        self.holes = drawn_holes(g) if holes is None else dict(holes)
        self.wrap = bool(config.wrap_holes) and bool(self.holes)
        count = max(int(config.hole_rings), 1)
        each = g.hole_ring_counts          # per hole, as drawn (0 = Hole Rings)
        self.rings = {k: each[k - 1] if k <= len(each) and each[k - 1] > 0 else count for k in self.holes}
        self.bands = ({k: zones.hole_band(k, h, self.gap, self.r_min, self.rings[k]) for k, h in self.holes.items()}
                      if self.wrap else {})
        self.eligible = zones.wrap_order(self.holes, g.bolt_holes) if self.wrap else []
        self.count = Counter()
        self.band = None                 # the hole the last layer ended wrapped round
        self.ccw = True
        self.wraps = []                  # (first layer, hole)
        self.notes = []
        self.unjoined = Counter()        # layers whose core found no room
        self.skipped = 0                 # layers that could have wrapped a hole but would have crossed
        self.plain_layers = []           # layers laid without the recipe (it crossed itself there)
        # Nothing asked of it (no edge rings, no hole to wrap): the layers
        # are laid as without the recipe.
        self.active = self.edge > 0 or self.wrap
        # where fibre over a hole fails validation (the holes, a hair in)
        inner = [Polygon(r).buffer(-0.1) for r in part.interiors] if part.geom_type == 'Polygon' else []
        inner = [h for h in inner if not h.is_empty]
        self.hole_area = shapely.union_all(inner) if inner else None
        if self.active and g.include_boundary:
            self.edge = max(self.edge, 1)    # the boundary pass is one edge ring

    def layer(self, layer_num, plan_index, wrap_ok, last_pt, last_dir, core, plain, repair=None, record=True):
        """The 2D path of layer `layer_num` (1-based). `core(poly, last_pt,
        last_dir, layer_idx, exit_to)` lays the core pattern in `poly`;
        `plain()` the layer without the recipe, laid instead when it is
        clearly better.
        `wrap_ok`: the next layer is a recipe layer too (it spirals out of
        the hole). `repair(path)`: the layer's bend repair (Strict Bend
        Radius), or None; every candidate is judged as repaired, and the
        repaired path is returned. `record=False` (a candidate Auto (Best
        Fit) weighs): no wrap, nothing noted, and the recipe layer even when
        the layer without it would do better (Auto weighs that one too)."""
        inward = plan_index % 2 == 0
        start = None if inward or not record else self.band

        def build(band):
            return zones.recipe_layer(self.part, self.holes, self.gap, self.r_min, self.edge, core, plan_index,
                                      last_pt=last_pt, last_dir=last_dir, wrap_end=band, wrap_start=start,
                                      start_ccw=self.ccw,
                                      start_near=band.centre if band is not None and last_pt is None else None)

        def judge(path):
            """((fibre over a hole, crossings), the repair cut over 40 % of
            it away, the path as laid). Judged after the bend repair: a
            candidate that crossed nothing could cross once repaired (and was
            then laid without the recipe, its hole left unwrapped)."""
            laid = repair(path) if repair is not None else path
            lost = float(getattr(laid, 'lost', 0.0) or 0.0)
            over = len(laid) > 1 and self.hole_area is not None and LineString(laid).intersects(self.hole_area)
            return (bool(over), zones.joins_cross(laid)), lost > 0.4 * _length(path), laid

        wrapping = self.wrap and inward and wrap_ok and self.eligible and record
        holes = [None]
        if wrapping:
            # Holes wrapped least so far first; of those, where the core ends: a
            # zigzag ends across the part (the hole farthest from where the
            # layer enters), a Fermat spiral next to where it entered (the
            # nearest); then no hole.
            at = last_pt if last_pt is not None else self.part.centroid.coords[0]
            sign = 1.0 if self.core_pattern == FERMAT else -1.0
            order = sorted(self.eligible, key=lambda h: (self.count[h], sign * math.dist(at, self.bands[h].centre), h))
            holes = [h for h in order if self.count[h] == self.count[order[0]]] + [None]
        # The first candidate that crosses none of its own fibre and keeps
        # most of it is laid. Failing that, of the candidates (and the layer
        # without the recipe) that lay at least 60 % of the longest one, the
        # one with no fibre over a hole and the fewest crossings, the first
        # in that order. (Fewest crossings alone laid a strict Fermat layer
        # as 53 mm of its 1783.)
        tried = []
        for k in holes:
            cand = build(self.bands[k] if k is not None else None)
            faults, short, laid = judge(cand.path)
            tried.append((faults, laid, cand))
            if faults == (False, 0) and not short:
                break
        else:
            if not record:
                return min(tried, key=lambda t: t[0])[1]
            alt_faults, _, alt = judge(plain())
            tried.append((alt_faults, alt, None))
            longest = max(_length(t[1]) for t in tried)
            tried = [min((t for t in tried if _length(t[1]) >= 0.6 * longest), key=lambda t: t[0])]
        _, laid, lay = tried[-1]
        if not record:
            return laid
        if lay is None:
            self.plain_layers.append(layer_num)
            self.band = None
            return laid
        if wrapping and lay.wrapped is None:
            self.skipped += 1
        if lay.notes:
            self.unjoined[lay.notes[0]] += 1
        if inward and lay.wrapped is not None:
            self.count[lay.wrapped] += 1
            self.band, self.ccw = self.bands[lay.wrapped], lay.ccw
            self.wraps.append((layer_num, lay.wrapped))
        else:
            self.band = None
        return laid

    def note(self, config: ProcessConfig) -> list[str]:
        out = []
        if self.edge:
            out.append(f"Layer recipe: {self.edge} edge ring(s) round the outline, then the "
                       f"{config.infill_type} core.")
        if config.wrap_holes and not self.holes:
            out.append("Wrap Holes: this part has no holes to wrap.")
        if self.wraps:
            parts = [f"hole {h} at layer {n} (into it only)" if n + 1 in self.plain_layers
                     else f"hole {h} at layers {n}-{n + 1}" for n, h in self.wraps]
            counts = {self.rings[h] for _, h in self.wraps}
            rings = (f"{counts.pop()} ring(s)" if len(counts) == 1 else
                     "rings: " + ", ".join(f"{self.rings[h]} round hole {h}" for h in sorted({h for _, h in self.wraps})))
            out.append(f"Hole wraps ({rings}, full laps): " + ", ".join(parts) + ".")
        if self.skipped:
            out.append(f"{self.skipped} layer(s) wrap no hole: every hole's wrap there would have crossed "
                       f"that layer's own fibre.")
        missed = [h for h in self.eligible if not self.count[h]]
        if self.wrap and missed:
            need = 2 * len(self.eligible)
            why = (f"each wrap takes two layers, so {need} layers wrap every hole once"
                   if int(config.layers) < need else
                   "on the layers left for them, every wrap crossed that layer's own fibre")
            out.append(f"Not wrapped: hole(s) {', '.join(map(str, missed))}: {why}.")
        for why, n in self.unjoined.items():
            out.append(f"{n} layer(s): {why}.")
        if self.plain_layers:
            out.append(f"Layer(s) {', '.join(map(str, self.plain_layers))} laid without the layer recipe: "
                       f"its pieces crossed each other there, or the bend repair cut much of it away.")
        return out


__all__ = ["RECIPE_CORES", "Recipe", "hole_notes", "recipe_wanted"]
