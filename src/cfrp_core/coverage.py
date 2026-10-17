"""Per-layer fibre coverage: how much of each layer's footprint the tow
actually covers (void %) and how much tow lies on tow (overlap %).

The tow footprint is the path buffered by half a track gap. The layer's
part region is its printable boundary (`target_shapes`, which sits gap/2
inside the outline) grown back by gap/2. Ramps (`is_transition`) and island
bridges are excluded: they are not part of the layer's laminate.
"""
from __future__ import annotations

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
        strips = [LineString(run).buffer(half, cap_style='flat', join_style=1) for run in _runs(pts)]
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
    return {
        "coverage": cov, "overlap": ovl,
        "worst_layer": worst, "worst_coverage": per_layer[worst]["coverage"],
    }
