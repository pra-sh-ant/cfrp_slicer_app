"""Per-layer fibre coverage: how much of each layer's footprint the tow
actually covers (void %) and how much tow lies on tow (overlap %).

The tow footprint is the path buffered by half a track gap. The layer's
part region is its printable boundary (`target_shapes`, which sits gap/2
inside the outline) grown back by gap/2. Ramps (`is_transition`) and island
bridges are excluded: they are not part of the layer's laminate.
"""
from __future__ import annotations

import shapely
from shapely.geometry import LineString
from shapely.ops import unary_union


def _runs(points):
    """Split a layer's points into runs of consecutive laminate points."""
    run = []
    for pt in points:
        if pt.get('is_transition') or pt.get('is_bridge'):
            if len(run) >= 2:
                yield run
            run = []
            continue
        run.append((pt['x'], pt['y']))
    if len(run) >= 2:
        yield run


def layer_coverage(trajectory, target_shapes, track_gap: float) -> dict[int, dict]:
    """{layer: {"coverage": 0..1, "overlap": 0..1, "area_mm2": float}}.

    coverage = area(tow ∩ region) / area(region)
    overlap  = (Σ tow area − area(tow union)) / area(region), the tow area
               laid on top of other tow in the same layer.
    """
    half = float(track_gap) / 2.0
    by_layer: dict[int, list] = {}
    for pt in trajectory:
        by_layer.setdefault(pt.get('layer', 1), []).append(pt)

    out = {}
    for _z, shape, layer in target_shapes:
        pts = by_layer.get(layer)
        if shape is None or shape.is_empty or not pts:
            continue
        region = shape.buffer(half, join_style=1)
        if region.area <= 0:
            continue
        # A flat-capped buffer of a path that doubles back can be invalid;
        # repair it so the union below cannot fail.
        strips = [shapely.make_valid(LineString(run).buffer(half, cap_style='flat', join_style=1))
                  for run in _runs(pts)]
        if not strips:
            out[layer] = {"coverage": 0.0, "overlap": 0.0, "area_mm2": region.area}
            continue
        tow = unary_union(strips)
        laid = sum(LineString(run).length for run in _runs(pts)) * 2.0 * half
        out[layer] = {
            "coverage": tow.intersection(region).area / region.area,
            "overlap": max(0.0, laid - tow.area) / region.area,
            "area_mm2": region.area,
        }
    return out


def coverage_summary(per_layer: dict[int, dict]) -> dict:
    """Area-weighted totals plus the worst layer."""
    if not per_layer:
        return {}
    area = sum(v["area_mm2"] for v in per_layer.values())
    cov = sum(v["coverage"] * v["area_mm2"] for v in per_layer.values()) / area
    ovl = sum(v["overlap"] * v["area_mm2"] for v in per_layer.values()) / area
    worst = min(per_layer, key=lambda k: per_layer[k]["coverage"])
    first = min(per_layer)
    return {
        "coverage": cov, "overlap": ovl,
        "worst_layer": worst, "worst_coverage": per_layer[worst]["coverage"],
        "first_layer": first, "first_coverage": per_layer[first]["coverage"],
    }


def _layer_tow(points, half):
    strips = [shapely.make_valid(LineString(run).buffer(half, cap_style='flat', join_style=1))
              for run in _runs(points)]
    return unary_union(strips) if strips else None


def cavity_map(trajectory, target_shapes, track_gap: float, run: int | None = None, holes=None) -> dict:
    """Spots of the part that no layer fills: empty in `run` consecutive
    layers (None: in every layer). Layers stack, so such a spot is a cavity
    through the laminate.

    Returns {"share": 0..1 of the part, "area_mm2", "layers": n judged,
    "spots": [{"area_mm2", "x", "y", "first", "last", "where"}]}, spots
    largest first; "where" is "next to hole k" (`holes`: polygons in the
    order the outline lists them; default the part's own), "at the edge" or
    "inside". Spots under one track width squared are left out (slivers
    between neighbouring tows)."""
    half = float(track_gap) / 2.0
    by_layer: dict[int, list] = {}
    for pt in trajectory:
        by_layer.setdefault(pt.get('layer', 1), []).append(pt)
    layers = []
    for _z, shape, layer in sorted(target_shapes, key=lambda t: t[2]):
        if shape is None or shape.is_empty:
            continue
        region = shape.buffer(half, join_style=1)
        tow = _layer_tow(by_layer.get(layer, []), half)
        empty = region if tow is None else region.difference(tow)
        layers.append((layer, region, empty))
    if not layers:
        return {"share": 0.0, "area_mm2": 0.0, "layers": 0, "spots": []}
    n = len(layers) if run is None else max(1, min(int(run), len(layers)))
    found = []                                  # (geometry, first layer, last layer)
    for start in range(len(layers) - n + 1):
        window = layers[start:start + n]
        common = window[0][2]
        for _, _, empty in window[1:]:
            common = common.intersection(empty)
            if common.is_empty:
                break
        if not common.is_empty:
            found.append((common, window[0][0], window[-1][0]))
    part = layers[0][1]
    if holes is None:
        holes = [shapely.geometry.Polygon(r) for g in getattr(part, 'geoms', [part]) for r in g.interiors]
    near_hole = max(3.0 * float(track_gap), 3.0)
    spots = []
    covered = None
    for geom, first, last in found:
        # tidy hairline slivers, then split into separate spots
        geom = geom.buffer(-0.05).buffer(0.05)
        for g in getattr(geom, 'geoms', [geom]):
            if g.geom_type != 'Polygon' or g.area < float(track_gap) ** 2:
                continue
            c = g.representative_point()
            dist = [h.exterior.distance(g) for h in holes]
            if dist and min(dist) <= near_hole:
                where = f"next to hole {dist.index(min(dist)) + 1}"
            elif part.exterior.distance(g) <= 2.0 * float(track_gap) if hasattr(part, 'exterior') else False:
                where = "at the edge"
            else:
                where = "inside"
            spots.append({"area_mm2": float(g.area), "x": float(c.x), "y": float(c.y),
                          "first": int(first), "last": int(last), "where": where})
            covered = g if covered is None else covered.union(g)
    spots.sort(key=lambda s: -s["area_mm2"])
    area = float(covered.area) if covered is not None else 0.0
    return {"share": area / max(part.area, 1e-9), "area_mm2": area, "layers": n, "spots": spots}


def cavity_note(m: dict) -> str:
    """One line for the notes from cavity_map's result."""
    n = m.get("layers", 0)
    if not m.get("spots"):
        return f"Cavities: no spot of the part is empty in all {n} layers."
    spots = m["spots"]
    shown = ", ".join(f"{s['area_mm2']:.0f} mm² {s['where']}" for s in spots[:3])
    more = f" and {len(spots) - 3} smaller" if len(spots) > 3 else ""
    return (f"Cavities: {100.0 * m['share']:.1f} % of the part ({m['area_mm2']:.0f} mm²) is empty in all {n} "
            f"layers, a void through the laminate: {shown}{more}. Marked in the 3D view (Cavities).")
