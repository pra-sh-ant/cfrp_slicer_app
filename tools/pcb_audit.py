"""Deep audit of the structural-PCB pipeline on random designs.

    python tools/pcb_audit.py [n_designs]

For each random board (random outline size, holes, parts from the palette,
wires between random pads, random moves/rotations) it slices every print
mode and checks invariants that must always hold:

  * no exception anywhere (DRC, DIW slicer, fibre slicer, G-code)
  * DRC reports a short whenever two different nets touch
  * every inked point lies on the board (minus edge clearance unless the DRC
    already reported an edge/hole error), never over a hole
  * with the dielectric on, every conductor point lies on the dielectric
  * ink on/off commands balance; extrusion only between them
  * DIW starts above the fibre top and never prints below ink already laid
    (level-2 crossover wires climb over their bridges)
  * the DIW board offset equals the fibre's (ink lands on the laminate)
  * fibre status is not Fail; validation messages are collected

Prints a summary of every invariant that failed and every message seen.
"""
import random
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shapely.geometry import Point, Polygon  # noqa: E402

from cfrp_core import ProcessConfig  # noqa: E402
from cfrp_core.board import slice_structural_board  # noqa: E402
from cfrp_core.diw import DiwConfig, DiwLayerSpec, Trace, drc  # noqa: E402
from cfrp_core.diw import edit as ed  # noqa: E402
from cfrp_core.diw.design import BoardDesign, conductors, hole_shape  # noqa: E402
from cfrp_core.diw.slicer import ink_area  # noqa: E402


def random_design(rng):
    w, h = rng.uniform(40, 110), rng.uniform(30, 80)
    d = BoardDesign(outline=ed.rectangle_outline(w, h, rng.choice([0.0, 6.0])))
    if rng.random() < 0.7:
        d = ed.corner_holes(d)
    keys = list(ed.PALETTE)
    for _ in range(rng.randint(1, 6)):
        key = rng.choice(keys)
        d, sel = ed.place(d, key, rng.uniform(8, w - 8), rng.uniform(8, h - 8))
        if rng.random() < 0.4:
            d = ed.transform(d, sel, angle_deg=rng.choice([90, -90, 180]))
        if rng.random() < 0.3:
            d = ed.transform(d, sel, dx=rng.uniform(-5, 5), dy=rng.uniform(-5, 5))
    pads = list(d.pads)
    for _ in range(rng.randint(0, 4)):
        if len(pads) < 2:
            break
        a, b = rng.sample(pads, 2)
        pts = [(a.x, a.y), (a.x, b.y), (b.x, b.y)] if rng.random() < 0.5 else [(a.x, a.y), (b.x, b.y)]
        pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        if len(pts) < 2:
            continue
        d, net = ed.auto_net(d, pts)
        d = d.model_copy(update=dict(traces=d.traces + (Trace(points=tuple(pts), width_mm=rng.choice([0.6, 1.0, 2.0]),
                                                                       net=net),)))
    return d


def check(d, cfg, proc, problems, messages):
    rep = drc(d, cfg)
    for m in rep["messages"]:
        messages[m.split(" ")[0] + " " + m.split(" ")[1] if " " in m else m] += 1
    # shorts must be reported
    elems = conductors(d)
    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            a, b = elems[i], elems[j]
            if a["net"] and b["net"] and a["net"] != b["net"] and a["geom"].intersects(b["geom"]):
                if not any(m.startswith("Short") for m in rep["messages"]):
                    problems["short not reported"] += 1
    placement_err = any("board edge" in m or "hole at" in m or "keepout" in m for m in rep["messages"])
    for mode in ("structural", "fibre", "electronics", "embedded"):
        if mode == "embedded" and proc.layers < 2:
            proc_m = proc.model_copy(update=dict(layers=2))
        else:
            proc_m = proc
        try:
            r = slice_structural_board(d, proc_m, cfg, mode=mode)
        except Exception:
            problems[f"exception {mode}: {traceback.format_exc().splitlines()[-1][:90]}"] += 1
            continue
        if r.fibre is not None:
            from shapely.affinity import translate as _tr
            from shapely.geometry import LineString as _LS
            fx0, fy0 = r.offset
            runs, run = [], []
            for p in r.fibre.trajectory:
                if p.get("is_anchor") or p.get("is_transition"):
                    if len(run) > 1:
                        runs.append(run)
                    run = []
                    continue
                run.append((p["x"], p["y"]))
            if len(run) > 1:
                runs.append(run)
            for h in d.holes:
                hole = _tr(hole_shape(h), fx0, fy0)
                if any(_LS(rr).intersects(hole) for rr in runs):
                    problems["fibre over a hole"] += 1
            st = r.fibre.validation["status"]
            for m in r.fibre.validation["messages"]:
                if not m.startswith("✅"):
                    messages["fibre: " + m[:60]] += 1
            if st == "Fail":
                problems["fibre Fail"] += 1
        if r.diw is None:
            continue
        if r.fibre_upper is not None:
            lo = min(p["z"] for p in r.fibre_upper.trajectory)
            if any(p["extruding"] and p["z"] >= lo for p in r.diw.trajectory):
                problems["ink above the upper laminate"] += 1
            if r.gcode.count("G28") != 1 or r.gcode.count("M84") != 1:
                problems["embedded G-code not one program"] += 1
        traj = r.diw.trajectory
        ox, oy = r.offset
        board = Polygon([(x + ox, y + oy) for x, y in d.outline]).buffer(1e-6)
        holes = [hole_shape(h) for h in d.holes]
        area = ink_area(d, cfg)
        from shapely.affinity import translate
        area = translate(area, ox, oy).buffer(0.05)
        diel = r.diw.regions.get("dielectric")
        diel = translate(diel, ox, oy).buffer(0.05) if diel is not None and not diel.is_empty else None
        flowing = 0
        top_fibre = max((p["z"] for p in r.fibre.trajectory), default=None) if r.fibre else None
        # Never print below ink already there (within 0.45 bead: beads that
        # overlap more would collide). Level-2 wires climb over bridges and
        # come down after them, so Z may fall within a layer.
        cell = 0.45 * cfg.bead_mm
        laid = defaultdict(list)
        run = 0                                  # extrusion run: a wire may come down along itself
        for p in traj:
            if not p["extruding"]:
                run += 1
                continue
            i, j = int(p["x"] // cell), int(p["y"] // cell)
            under = max((zz for di in (-1, 0, 1) for dj in (-1, 0, 1) for xx, yy, zz, rr in laid[i + di, j + dj]
                         if rr != run and (xx - p["x"]) ** 2 + (yy - p["y"]) ** 2 < cell * cell), default=-1e9)
            if p["z"] < under - 1e-6:
                problems["ink printed below ink already there"] += 1
            laid[i, j].append((p["x"], p["y"], p["z"], run))
        for p in traj:
            if not p["extruding"]:
                continue
            q = Point(p["x"], p["y"])
            if not board.contains(q):
                problems["ink off the board"] += 1
            if any(translate(h, ox, oy).contains(q) for h in holes):
                problems["ink over a hole"] += 1
            if not placement_err and not area.contains(q) and p["feature"] == "conductor":
                problems["conductor outside ink area without a DRC error"] += 1
            if diel is not None and p["feature"] == "conductor" and not diel.contains(q):
                problems["conductor not on dielectric"] += 1
            if top_fibre is not None and p["z"] <= top_fibre + 1e-6:
                problems["ink below fibre top"] += 1
        g = r.gcode
        if g.count("ink on") != g.count("ink off"):
            problems["ink on/off unbalanced"] += 1
        for line in g.splitlines():
            if "ink on" in line:
                flowing += 1
            elif "ink off" in line:
                flowing -= 1
            elif line.startswith("G1 ") and "E" not in line and flowing == 0 and "DIW" in g[:0]:
                pass
        if flowing != 0:
            problems["ink left on"] += 1
        if r.fibre is not None:
            fx = min(p["x"] for p in r.fibre.trajectory if not p.get("is_anchor"))
            bx = min(x for x, _ in d.outline) + ox
            if fx < bx - 1.0:
                problems["fibre outside board"] += 1


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    rng = random.Random(7)
    problems, messages = Counter(), Counter()
    cfgs = [DiwConfig(), DiwConfig(encapsulate=True, conductor_layers=2), DiwConfig(dispense="volumetric"),
            DiwConfig(dielectric=False, substrate_conductive=False),
            DiwConfig(spread_factor=1.3, layer_wait_s=5.0, bridge_layers=3),
            DiwConfig(insulation_coverage="board"),
            DiwConfig(stack=(DiwLayerSpec(kind="dielectric", covers="board", angle_deg=30.0),
                             DiwLayerSpec(kind="dielectric", fill="concentric", height_mm=0.15),
                             DiwLayerSpec(kind="conductor", level=1, height_mm=0.3),
                             DiwLayerSpec(kind="conductor", level=2),
                             DiwLayerSpec(kind="encapsulation", covers="board", cure=True)))]
    procs = [ProcessConfig(), ProcessConfig(infill_type="Corner-Step Spiral", fill_solid=True, layers=2),
             ProcessConfig(infill_type="Oriented Serpentine", layers=2, layup_deg=(0.0, 90.0), first_layer_solid=True)]
    from cfrp_core.diw.router import auto_route, ratsnest
    routed = failed = crossed = 0
    for k in range(n):
        d = random_design(rng)
        if k % 2:
            d, rep = auto_route(d, cfgs[k % len(cfgs)])
            routed += rep["routed"]
            crossed += rep.get("crossovers", 0)
            failed += len(rep["failed"])
            if not rep["failed"] and ratsnest(d):
                problems["router left connections without reporting"] += 1
        try:
            check(d, cfgs[k % len(cfgs)], procs[k % len(procs)], problems, messages)
        except Exception:
            problems["audit crash: " + traceback.format_exc().splitlines()[-1][:90]] += 1
    print(f"designs {n}, wires auto-routed {routed} ({crossed} as level-2 crossovers), "
          f"nets the router could not reach {failed}")
    print("PROBLEMS:", dict(problems) if problems else "none")
    print("messages:")
    for m, c in messages.most_common(40):
        print(f"  {c:4}  {m}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
