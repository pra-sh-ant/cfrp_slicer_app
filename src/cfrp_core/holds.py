"""Hold points (ProcessConfig.hold_points): where the stroke stops for a
while, and may pause so the fibre can be cut there; and cleaning pauses at
intervals of path or time (place_clean_pauses).

The path stays one stroke. A hold marks the point where the stroke first
reaches its height (mm above the first layer; not on a layer climb or the
lead-in anchor): the G-code writer dwells there with the UV on, so the tow
cures where it lies, and for a cut turns the UV off and pauses (M0) until the
operator resumes, then tacks the tow again (gcode/writer.py).
"""
from __future__ import annotations

import numpy as np

from .config import ProcessConfig


def place_holds(trajectory: list[dict], config: ProcessConfig) -> list[str]:
    """Mark each hold on its point (`hold_s`: seconds to dwell, `hold_cut`:
    pause for a cut; holds landing on one point add up) and return a note
    per hold. A hold above the part's top is left out (noted)."""
    holds = sorted(config.hold_points, key=lambda h: h.at_mm)
    if not holds or not trajectory:
        return []
    z = np.array([p['z'] for p in trajectory], dtype=np.float64)
    usable = np.array([not (p.get('is_transition') or p.get('is_anchor')) for p in trajectory], dtype=bool)
    base, top = float(z.min()), float(z.max())
    notes = []
    for h in holds:
        hits = np.nonzero(usable & (z >= base + h.at_mm - 1e-6))[0]
        if not len(hits):
            notes.append(f"Hold at {h.at_mm:g} mm is above the part's top ({top - base:.1f} mm above the first "
                         f"layer): left out.")
            continue
        pt = trajectory[int(hits[0])]
        if h.seconds > 0:
            pt['hold_s'] = float(pt.get('hold_s', 0.0)) + float(h.seconds)
        if h.cut:
            pt['hold_cut'] = True
        what = " and ".join(([f"holds {h.seconds:g} s"] if h.seconds > 0 else [])
                            + (["pauses so the fibre can be cut"] if h.cut else []))
        notes.append(f"Hold at {h.at_mm:g} mm (layer {pt.get('layer', 1)}, Z {pt['z']:.2f} mm): the stroke {what} "
                     f"there.")
    return notes


def place_clean_pauses(trajectory: list[dict], config: ProcessConfig) -> list[str]:
    """Mark the cleaning pauses (`clean_s` on a point): every
    `clean_every_m` metres of path and / or every `clean_every_min` minutes
    of printing (each move's length / its speed), at the first point there
    not on a climb or the anchor. None near the very end (the print stops
    there anyway). Returns a note."""
    if not trajectory or not (config.clean_by_distance or config.clean_by_time):
        return []
    xyz = np.array([(p['x'], p['y'], p['z']) for p in trajectory], dtype=np.float64)
    step = np.concatenate([[0.0], np.linalg.norm(np.diff(xyz, axis=0), axis=1)])
    speed = np.array([max(float(p.get('v', 1.0)), 1e-6) for p in trajectory])
    usable = np.array([not (p.get('is_transition') or p.get('is_anchor')) for p in trajectory], dtype=bool)
    marks = set()
    rules = []
    for on, every, run, unit in ((config.clean_by_distance, config.clean_every_m * 1000.0, np.cumsum(step),
                                  f"{config.clean_every_m:g} m of path"),
                                 (config.clean_by_time, config.clean_every_min * 60.0, np.cumsum(step / speed),
                                  f"{config.clean_every_min:g} min of printing")):
        if not on:
            continue
        rules.append(unit)
        k = 1
        while k * every < run[-1] - 0.5 * every:
            hits = np.nonzero(usable & (run >= k * every))[0]
            if not len(hits):
                break
            marks.add(int(hits[0]))
            k += 1
    for i in marks:
        trajectory[i]['clean_s'] = float(config.clean_pause_s)
    if not marks:
        return [f"Cleaning pauses: none (the print is shorter than {' or '.join(rules)} away from its end)."]
    return [f"Cleaning pauses: {len(marks)} (every {' and every '.join(rules)}): the UV goes off and the head "
            f"stays put {config.clean_pause_s:g} s each to clean the nozzle, then the UV comes back on."]
