"""Layer recipe in the flat layer loop (Phase 44): which hole each layer
wraps, the way the wrap turns (the next layer spirals out of the same hole
the same way round), and the notes. Geometry in geometry/zones.py."""
from __future__ import annotations

import math
from collections import Counter

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


class Recipe:
    """Builds recipe layers in turn for one flat part (`part`: its outline
    with holes, as placed)."""

    def __init__(self, config: ProcessConfig, part):
        g = config.geometry
        self.part = part
        self.gap, self.r_min = float(config.track_gap_mm), float(config.min_radius_mm)
        self.edge = int(config.edge_rings)
        self.core_pattern = config.infill_type
        self.holes = drawn_holes(g)
        self.wrap = bool(config.wrap_holes) and bool(self.holes)
        count = max(int(config.hole_rings), 1)
        self.bands = ({k: zones.hole_band(k, h, self.gap, self.r_min, count) for k, h in self.holes.items()}
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
        self._last = (None, None)        # (layer, its 'no room' note) of the last layer built
        # Nothing asked of it (no edge rings, no hole to wrap): the layers
        # are laid as without the recipe.
        self.active = self.edge > 0 or self.wrap
        if self.active and g.include_boundary:
            self.edge = max(self.edge, 1)    # the boundary pass is one edge ring

    def layer(self, layer_num, plan_index, wrap_ok, last_pt, last_dir, core, plain):
        """The 2D path of layer `layer_num` (1-based). `core(poly, last_pt,
        last_dir, layer_idx)` lays the core pattern in `poly`; `plain()` the
        layer without the recipe, laid instead when it crosses itself less.
        `wrap_ok`: the next layer is a recipe layer too (it spirals out of
        the hole)."""
        inward = plan_index % 2 == 0
        start = None if inward else self.band
        crossings = None

        def build(band):
            return zones.recipe_layer(self.part, self.holes, self.gap, self.r_min, self.edge, core, plan_index,
                                      last_pt=last_pt, last_dir=last_dir, wrap_end=band, wrap_start=start,
                                      start_ccw=self.ccw,
                                      start_near=band.centre if band is not None and last_pt is None else None)
        if self.wrap and inward and wrap_ok and self.eligible:
            # Holes wrapped least so far first; of those, where the core ends: a
            # zigzag ends across the part (the hole farthest from where the
            # layer enters), a Fermat spiral next to where it entered (the
            # nearest). The first layer that crosses none of its own fibre is
            # kept; failing that, the layer wraps no hole (and failing that,
            # the fewest crossings).
            at = last_pt if last_pt is not None else self.part.centroid.coords[0]
            sign = 1.0 if self.core_pattern == FERMAT else -1.0
            order = sorted(self.eligible, key=lambda h: (self.count[h], sign * math.dist(at, self.bands[h].centre), h))
            least = self.count[order[0]]
            tried = []
            lay = None
            for k in [h for h in order if self.count[h] == least] + [None]:
                cand = build(self.bands[k] if k is not None else None)
                crossings = zones.joins_cross(cand.path)
                tried.append((crossings, k is None, cand))
                if crossings == 0:
                    lay = cand
                    break
            if lay is None:
                crossings, _, lay = min(tried, key=lambda t: t[:2])
            if lay.wrapped is None:
                self.skipped += 1
        else:
            lay = build(None)
            crossings = zones.joins_cross(lay.path)
        if crossings:
            # Its pieces cross (a join over the core, a core into a band):
            # the layer without the recipe, if that crosses less.
            alt = plain()
            if zones.joins_cross(alt) < crossings:
                self.plain_layers.append(layer_num)
                self.band = None
                return alt
        self._last = (layer_num, lay.notes[0] if lay.notes else None)
        if lay.notes:
            self.unjoined[lay.notes[0]] += 1
        if inward and lay.wrapped is not None:
            self.count[lay.wrapped] += 1
            self.band, self.ccw = self.bands[lay.wrapped], lay.ccw
            self.wraps.append((layer_num, lay.wrapped))
        else:
            self.band = None
        return lay.path

    def drop(self, layer_num):
        """Layer `layer_num`, the last one built, is laid without the recipe
        after all: undo what it counted."""
        if getattr(self, '_last', (None,))[0] != layer_num:
            return
        why = self._last[1]
        if why:
            self.unjoined[why] -= 1
            if not self.unjoined[why]:
                del self.unjoined[why]
        if self.wraps and self.wraps[-1][0] == layer_num:
            self.count[self.wraps.pop()[1]] -= 1
        self.band = None
        self.plain_layers.append(layer_num)
        self._last = (None, None)

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
            out.append(f"Hole wraps ({max(int(config.hole_rings), 1)} ring(s), full laps): " + ", ".join(parts) + ".")
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
                       f"its pieces crossed each other there.")
        return out


__all__ = ["RECIPE_CORES", "Recipe", "hole_notes", "recipe_wanted"]
