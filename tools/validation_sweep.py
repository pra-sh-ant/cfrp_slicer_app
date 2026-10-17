"""Slice every shape x infill x key setting and group validation messages.

    python tools/validation_sweep.py [results.json]

Prints each warning/error category with how many cases hit it and on which
shapes/infills; writes every case's status, messages and notes to the JSON.
Base values are the GUI defaults. Takes a few minutes (8 processes).
"""
import itertools
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

BASE = {  # GUI defaults (controls_panel get_params)
    "shape": "Square/Rectangle", "width": 135.0, "height": 25.0, "include_boundary": False,
    "stl_path": None, "base_radius": 20.0, "disc_radius": 15.0, "stem_height": 25.0,
    "wall_thickness": 0.0, "start_x": 26.0, "start_y": 40.0, "track_gap": 1.2, "z_offset": 0.2,
    "layers": 1, "layup_seq": "0", "infill_type": "Archimedean (Concentric Spiral)",
    "tracks_per_layer": 7, "layer_height": 0.2, "needle_dia": 0.8, "e_mult": 1.6,
    "density": 0.00115, "weight": 0.55 / (3.141592653589793 * 39), "v_max": 10.0, "v_min": 5.0,
    "accel": 25.0, "decel": 15.0, "p_advance": 3.0, "pump_stall_floor": 5.0,
    "transition_angle": 30.0, "seam_trim_length": 3.0, "anchor_length": 15.0, "min_radius": 3.0,
    "v_z": 2.0, "v_overhang": 5.0, "dwell": 1.5, "uv": 225, "purge_e": 15.0, "purge_f": 60.0,
    "travel_f": 600.0,
}
INFILLS = ["Archimedean (Concentric Spiral)", "Fermat Spiral (Connected Double Spiral)",
           "Oriented Serpentine", "45° Diagonal Hatch", "Corner-Step Spiral"]
FLAT = ["Square/Rectangle", "Triangle", "Hexagon", "Circle"]
REV = ["Hollow Cylinder (Z)", "Hollow Square Tube (Z)", "Hollow Sphere (Z)", "Dome (Z)",
       "Tapered Cylinder (Z)", "Conical Dome (Z)"]

def cases():
    for shape, inf, bnd, layers, layup, tracks, (w, h) in itertools.product(
            FLAT, INFILLS, (False, True), (1, 4), ("0", "0, 90", "0, 45, 90, -45"), (3, 7, 15),
            ((135.0, 25.0), (60.0, 60.0))):
        if layers == 1 and layup != "0":
            continue
        yield dict(shape=shape, infill_type=inf, include_boundary=bnd, layers=layers,
                   layup_seq=layup, tracks_per_layer=tracks, width=w, height=h)
    for shape, inf, tracks, lh, r in itertools.product(
            REV, INFILLS[:2], (1, 3, 7), (0.2, 0.5, 1.0), (10.0, 20.0)):
        yield dict(shape=shape, infill_type=inf, tracks_per_layer=tracks, layer_height=lh, base_radius=r)
    for disc, stem in itertools.product((10.0, 15.0, 30.0), (10.0, 25.0)):
        yield dict(shape="3D Overhang (Mushroom)", infill_type="N/A", disc_radius=disc, stem_height=stem,
                   base_radius=10.0)


def norm(msg):
    m = re.sub(r"[-+]?\d+(\.\d+)?", "#", msg)
    m = re.sub(r"\[[^\]]*\]", "[..]", m)
    return m[:110]


def run(over):
    from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part
    cfg = dict(BASE)
    cfg.update(over)
    layup = [float(x) for x in cfg["layup_seq"].split(",")]
    t = time.time()
    try:
        res = slice_part(from_legacy_dict(cfg, layup=layup), DEFAULT_MACHINE)
        return dict(case=over, status=res.validation["status"], msgs=res.validation["messages"],
                    notes=list(res.constraint_notes), cov=res.stats.get("coverage"), t=time.time() - t)
    except Exception as e:
        return dict(case=over, status="EXC", msgs=[f"{type(e).__name__}: {e}"], notes=[], cov=None, t=time.time() - t)


if __name__ == "__main__":
    cs = list(cases())
    with ProcessPoolExecutor(8) as ex:
        results = list(ex.map(run, cs, chunksize=4))
    out = sys.argv[1] if len(sys.argv) > 1 else "validation_sweep.json"
    Path(out).write_text(json.dumps(results, default=str), encoding="utf-8")
    print("cases", len(results), Counter(r["status"] for r in results))
    cat = defaultdict(list)
    for r in results:
        for m in r["msgs"]:
            if m.startswith("✅"):
                continue
            cat[(r["status"] if r["status"] in ("Fail", "EXC") else "Warn", norm(m))].append(r["case"])
    for (st, k), v in sorted(cat.items(), key=lambda kv: -len(kv[1])):
        shapes = Counter(c["shape"] for c in v)
        infs = Counter(c.get("infill_type", "")[:10] for c in v)
        print(f"\n[{st}] x{len(v)}  {k}\n    shapes={dict(shapes)}\n    infills={dict(infs)}")
    print("\nslowest", sorted(((round(r['t'],1), r['case']['shape']) for r in results), reverse=True)[:5])
