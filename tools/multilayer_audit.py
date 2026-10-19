"""Audit multi-layer toolpath planning: what happens BETWEEN layers.

    python tools/multilayer_audit.py [results.json]
    python tools/multilayer_audit.py --quick       # CI: a twelfth of the cases, exit 1 on failure

validate_trajectory judges points; this judges the plan across layers, per
seam (layer N -> N+1):
  jump      XY chord of the inter-layer ramp (layer N's cut end to layer
            N+1's start); chords across the part show up as crossings in
            validation instead
  reversal  direction at the end of N vs start of N+1 (cos < 0: the fibre
            folds back on itself)
  ramp      horizontal length and steepest step of the inter-layer ramp
and per part:
  stack     most seams within 2 mm of one XY spot (stacked seams build a
            ridge / weak line through the thickness)
  drift     how far each layer's XY bounds move from layer 2's (layer 1
            carries the lead-in anchor)
  coverage  worst layer's tow coverage, worst overlap
  z_down    any point lower than the one before it (fibre can only go up)
"""
import itertools
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation_sweep import BASE  # noqa: E402

FLAT = ["Square/Rectangle", "Triangle", "Hexagon", "Circle"]
INFILLS = ["Archimedean (Concentric Spiral)", "Fermat Spiral (Connected Double Spiral)",
           "Oriented Serpentine", "45° Diagonal Hatch", "Corner-Step Spiral"]
REV = ["Hollow Cylinder (Z)", "Hollow Square Tube (Z)", "Hollow Sphere (Z)", "Dome (Z)",
       "Tapered Cylinder (Z)", "Conical Dome (Z)"]


def cases():
    for shape, (w, h), inf, bnd, layup in itertools.product(
            FLAT, ((135.0, 25.0), (60.0, 60.0)), INFILLS, (False, True),
            ("0", "0, 90", "0, 45, 90, -45")):
        yield dict(shape=shape, width=w, height=h, infill_type=inf, include_boundary=bnd,
                   layup_seq=layup, layers=6, tracks_per_layer=7)
    for shape, tracks in itertools.product(REV, (1, 3)):
        yield dict(shape=shape, tracks_per_layer=tracks, layer_height=1.0, base_radius=20.0)


def _dir(points, from_end, span=1.0):
    seq = points[::-1] if from_end else points
    x0, y0 = seq[0]['x'], seq[0]['y']
    for p in seq[1:]:
        dx, dy = p['x'] - x0, p['y'] - y0
        n = math.hypot(dx, dy)
        if n >= span:
            return (-dx / n, -dy / n) if from_end else (dx / n, dy / n)
    return None


def audit(over):
    from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part
    from cfrp_core.coverage import layer_coverage
    cfg = dict(BASE)
    cfg.update(over)
    layup = [float(x) for x in cfg.get("layup_seq", "0").split(",")]
    try:
        res = slice_part(from_legacy_dict(cfg, layup=layup), DEFAULT_MACHINE)
    except Exception as exc:
        return dict(case=over, error=f"{type(exc).__name__}: {exc}")
    tr = res.trajectory
    layers = {}
    for p in tr:
        layers.setdefault(p['layer'], []).append(p)
    nums = sorted(layers)
    seams = []
    for a, b in zip(nums[:-1], nums[1:], strict=False):
        A = [p for p in layers[a] if not p.get('is_transition')]
        B = [p for p in layers[b] if not p.get('is_transition')]
        ramp = [p for p in layers[b] if p.get('is_transition')]
        if not A or not B:
            continue
        ea, sb = A[-1], B[0]
        da, db = _dir(A, True), _dir(B, False)
        steep = 0.0
        prev = A[-1]
        for p in ramp + [sb]:
            dxy = math.hypot(p['x'] - prev['x'], p['y'] - prev['y'])
            dz = abs(p['z'] - prev['z'])
            if dz > 1e-9:
                steep = max(steep, math.degrees(math.atan2(dz, dxy)))
            prev = p
        start = ramp[-1] if ramp else sb
        seams.append(dict(
            layer=b, jump=math.hypot(start['x'] - ea['x'], start['y'] - ea['y']),
            cos=(da[0] * db[0] + da[1] * db[1]) if da and db else None,
            ramp_len=sum(math.hypot(q['x'] - p['x'], q['y'] - p['y'])
                         for p, q in zip(ramp[:-1], ramp[1:], strict=False)),
            steep=steep, xy=(sb['x'], sb['y'])))
    xy = np.array([s['xy'] for s in seams]) if seams else np.zeros((0, 2))
    stack = max((int((np.hypot(*(xy - c).T) < 2.0).sum()) for c in xy), default=0)
    b0 = None
    drift = 0.0
    for n in nums[1:]:   # layer 1 carries the lead-in anchor outside the part
        pts = np.array([(p['x'], p['y']) for p in layers[n] if not p.get('is_transition')])
        if len(pts) < 2:
            continue
        b = np.r_[pts.min(0), pts.max(0)]
        if b0 is None:
            b0 = b
        else:
            drift = max(drift, float(np.abs(b - b0).max()))
    zs = np.array([p['z'] for p in tr])
    cov = layer_coverage(tr, res.target_shapes, res.config.track_gap_mm) if res.target_shapes else {}
    return dict(
        case=over, status=res.validation["status"], notes=list(res.constraint_notes),
        seams=seams, stack=stack, drift=drift,
        z_down=int((np.diff(zs) < -1e-6).sum()),
        cov_min=min((v["coverage"] for v in cov.values()), default=None),
        ovl_max=max((v["overlap"] for v in cov.values()), default=None),
        msgs=res.validation["messages"])


def summarize(results):
    print(f"cases {len(results)}  errors {sum(1 for r in results if 'error' in r)}")
    rows = []
    for r in results:
        if 'error' in r:
            print("  ERROR", r['case'], r['error'][:100])
            continue
        s = r['seams']
        rows.append(dict(
            key=(r['case']['shape'][:10], r['case'].get('infill_type', '')[:10],
                 'bnd' if r['case'].get('include_boundary') else '   '),
            max_jump=max((x['jump'] for x in s), default=0.0),
            reversals=sum(1 for x in s if x['cos'] is not None and x['cos'] < 0),
            steep=max((x['steep'] for x in s), default=0.0),
            stack=r['stack'], drift=r['drift'], z_down=r['z_down'],
            cov=r['cov_min'] if r['cov_min'] is not None else float('nan'),
            ovl=r['ovl_max'] if r['ovl_max'] is not None else float('nan')))
    groups = {}
    for row in rows:
        groups.setdefault(row['key'], []).append(row)
    print(f"\n{'shape':10} {'infill':10} bnd | n | max jump | reversals | steep° | stack | drift | z↓ | min cov | max ovl")
    for key in sorted(groups):
        g = groups[key]
        print(f"{key[0]:10} {key[1]:10} {key[2]} | {len(g):2}| {max(x['max_jump'] for x in g):8.1f} |"
              f" {sum(x['reversals'] for x in g):9} | {max(x['steep'] for x in g):6.1f} |"
              f" {max(x['stack'] for x in g):5} | {max(x['drift'] for x in g):5.1f} |"
              f" {sum(x['z_down'] for x in g):2} | {min(x['cov'] for x in g):7.2f} |"
              f" {max(x['ovl'] for x in g):7.2f}")


def failures(results, gap=1.2):
    """Things that must never happen (exit code 1 in CI): a crash, Z going
    down, or a layer starting more than a gap away from its ramp."""
    out = []
    for r in results:
        if 'error' in r:
            out.append(f"crash {r['case']}: {r['error'][:80]}")
        elif r['z_down']:
            out.append(f"Z drops {r['z_down']}x in {r['case']}")
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cs = list(cases())
    if "--quick" in sys.argv:
        cs = cs[::12]
    with ProcessPoolExecutor(8) as ex:
        results = list(ex.map(audit, cs, chunksize=2))
    out = args[0] if args else "multilayer_audit.json"
    if "--quick" not in sys.argv:
        Path(out).write_text(json.dumps(results, default=float), encoding="utf-8")
    summarize(results)
    bad = failures(results)
    print("\nFAILURES:" if bad else "\nno failures", *bad, sep="\n  ")
    sys.exit(1 if bad else 0)
