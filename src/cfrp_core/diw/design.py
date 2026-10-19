"""Structural PCB design: board outline, holes, conductive traces, pads and
zones (mm, board origin at bottom left). Saved as JSON (`.spcb.json`).

The board outline is the fibre laminate's Custom Outline; traces, pads and
conductor zones are printed on top of it by DIW, over a dielectric layer.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from shapely.affinity import rotate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

Pt = tuple[float, float]


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Trace(_M):
    """Conductive track along a polyline, `width_mm` wide, round ends.
    `level` 2 = printed after level 1 and allowed to cross other nets'
    level-1 wires over an insulating bridge (a crossover); it joins level-1
    pads and wires of its own net where it touches them. `current_a`: the
    current it must carry (0 = not checked)."""
    points: tuple[Pt, ...]
    width_mm: float = 0.8
    net: str = ""
    name: str = ""
    level: int = 1
    current_a: float = 0.0

    @field_validator("points")
    @classmethod
    def _two(cls, v):
        if len(v) < 2:
            raise ValueError("A trace needs at least two points.")
        return v

    @field_validator("width_mm")
    @classmethod
    def _w(cls, v):
        if not v > 0:
            raise ValueError("Trace width must be greater than 0 mm.")
        return v

    @field_validator("level")
    @classmethod
    def _level(cls, v):
        if v not in (1, 2):
            raise ValueError("A wire's level is 1 (on the insulation) or 2 (crossover).")
        return v

    @field_validator("current_a")
    @classmethod
    def _current(cls, v):
        if v < 0:
            raise ValueError("A wire's current cannot be negative.")
        return v

    def length(self) -> float:
        return LineString(self.points).length


class Pad(_M):
    """Solder/contact pad, rectangle or circle, centred at (x, y)."""
    x: float
    y: float
    w_mm: float = 1.6
    h_mm: float = 1.6
    shape: Literal["rect", "circle"] = "rect"
    angle_deg: float = 0.0
    net: str = ""
    name: str = ""


class Hole(_M):
    """Through hole in the laminate (mounting hole, via); fibre steers round it."""
    x: float
    y: float
    d_mm: float = 3.2


class Zone(_M):
    """Polygon area: conductor (copper-pour-like fill), dielectric (extra
    insulation) or keepout (no ink)."""
    points: tuple[Pt, ...]
    kind: Literal["conductor", "dielectric", "keepout"] = "conductor"
    net: str = ""


class BoardDesign(_M):
    name: str = "board"
    outline: tuple[Pt, ...] = ((0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0))
    holes: tuple[Hole, ...] = ()
    traces: tuple[Trace, ...] = ()
    pads: tuple[Pad, ...] = ()
    zones: tuple[Zone, ...] = ()
    version: int = 1

    @field_validator("outline")
    @classmethod
    def _outline(cls, v):
        if len(v) < 3:
            raise ValueError("The board outline needs at least 3 points.")
        return v


# --- geometry -------------------------------------------------------------------

def hole_shape(h: Hole, segments=32):
    return Point(h.x, h.y).buffer(h.d_mm / 2.0, quad_segs=max(4, segments // 4))


def hole_outlines(design: BoardDesign) -> list[list[Pt]]:
    """Holes as closed point lists (for the fibre slicer's custom_holes)."""
    return [list(hole_shape(h).exterior.coords)[:-1] for h in design.holes]


def board_polygon(design: BoardDesign) -> Polygon:
    poly = Polygon(design.outline).buffer(0)
    for h in design.holes:
        poly = poly.difference(hole_shape(h))
    return poly


def pad_shape(p: Pad):
    if p.shape == "circle":
        return Point(p.x, p.y).buffer(min(p.w_mm, p.h_mm) / 2.0, quad_segs=8)
    g = box(p.x - p.w_mm / 2.0, p.y - p.h_mm / 2.0, p.x + p.w_mm / 2.0, p.y + p.h_mm / 2.0)
    return rotate(g, p.angle_deg, origin=(p.x, p.y)) if p.angle_deg else g


def trace_shape(t: Trace):
    return LineString(t.points).buffer(t.width_mm / 2.0, quad_segs=8)


def conductors(design: BoardDesign) -> list[dict]:
    """Every conductive element: {kind, index, net, name, geom, level}."""
    out = []
    for i, t in enumerate(design.traces):
        out.append(dict(kind="trace", index=i, net=t.net, name=t.name or f"trace {i + 1}", geom=trace_shape(t),
                        level=t.level))
    for i, p in enumerate(design.pads):
        out.append(dict(kind="pad", index=i, net=p.net, name=p.name or f"pad {i + 1}", geom=pad_shape(p),
                        level=1))
    for i, z in enumerate(design.zones):
        if z.kind == "conductor":
            out.append(dict(kind="zone", index=i, net=z.net, name=f"zone {i + 1}",
                            geom=Polygon(z.points).buffer(0), level=1))
    return out


def joined(a: dict, b: dict) -> bool:
    """Do two conductive elements connect? Touching on the same level, or a
    level-2 wire touching its own net's level-1 element. A level-2 wire over
    another net's level-1 element is insulated by a bridge."""
    if not a["geom"].intersects(b["geom"]):
        return False
    return a.get("level", 1) == b.get("level", 1) or (bool(a["net"]) and a["net"] == b["net"])


def crossovers(design: BoardDesign, margin: float = 0.6) -> list[dict]:
    """Where a level-2 wire crosses another net's level-1 element:
    [{wire, under, geom}] with geom the bridge to print (the crossing grown
    by `margin`)."""
    elems = conductors(design)
    out = []
    for a in elems:
        if a.get("level", 1) != 2:
            continue
        for b in elems:
            if b.get("level", 1) != 1 or (a["net"] and a["net"] == b["net"]):
                continue
            if a["geom"].intersects(b["geom"]):
                hit = a["geom"].intersection(b["geom"])
                if not hit.is_empty and hit.area > 1e-6:
                    out.append(dict(wire=a["name"], under=b["name"], geom=hit.buffer(margin, quad_segs=8)))
    return out


def conductor_union(design: BoardDesign):
    return unary_union([c["geom"] for c in conductors(design)])


def bounds(design: BoardDesign):
    return Polygon(design.outline).bounds


# --- save / load ------------------------------------------------------------------

def save_design(design: BoardDesign, path) -> None:
    Path(path).write_text(design.model_dump_json(indent=2), encoding="utf-8")


def load_design(path) -> BoardDesign:
    return BoardDesign.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


# --- footprints ---------------------------------------------------------------------

# name -> [(dx, dy, w, h, shape)] pads relative to the footprint centre (mm)
FOOTPRINTS: dict[str, list[tuple[float, float, float, float, str]]] = {
    "LED / resistor 1206": [(-1.5, 0.0, 1.2, 1.8, "rect"), (1.5, 0.0, 1.2, 1.8, "rect")],
    "LED / resistor 0805": [(-1.0, 0.0, 1.0, 1.3, "rect"), (1.0, 0.0, 1.0, 1.3, "rect")],
    "SOT-23": [(-0.95, -1.1, 0.8, 1.0, "rect"), (0.95, -1.1, 0.8, 1.0, "rect"), (0.0, 1.1, 0.8, 1.0, "rect")],
    "Header 2-pin 2.54": [(-1.27, 0.0, 1.8, 1.8, "circle"), (1.27, 0.0, 1.8, 1.8, "circle")],
    "Header 3-pin 2.54": [(-2.54, 0.0, 1.8, 1.8, "circle"), (0.0, 0.0, 1.8, 1.8, "circle"),
                          (2.54, 0.0, 1.8, 1.8, "circle")],
    "JST-PH 2-pin": [(-1.0, 0.0, 1.0, 3.0, "rect"), (1.0, 0.0, 1.0, 3.0, "rect")],
    "Test pad": [(0.0, 0.0, 2.0, 2.0, "circle")],
}


def footprint(name: str, x: float, y: float, angle_deg: float = 0.0, ref: str = "U",
              nets: tuple[str, ...] = ()) -> list[Pad]:
    """Pads of a footprint placed at (x, y), rotated by angle_deg; pad k is
    named '<ref>.<k+1>' and gets nets[k] if given."""
    pads = []
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    for k, (dx, dy, w, h, shape) in enumerate(FOOTPRINTS[name]):
        pads.append(Pad(x=x + dx * ca - dy * sa, y=y + dx * sa + dy * ca, w_mm=w, h_mm=h, shape=shape,
                        angle_deg=angle_deg, net=nets[k] if k < len(nets) else "", name=f"{ref}.{k + 1}"))
    return pads


# --- parametric structural-electronics features ---------------------------------

def meander(x, y, length, pitch, turns, width=0.6, net="", name="meander") -> Trace:
    """Serpentine resistor: strain gauge or heater. `turns` legs of `length`
    along X, `pitch` apart along Y, starting at (x, y)."""
    pts = []
    for k in range(int(turns)):
        y_k = y + k * pitch
        a, b = (x, x + length) if k % 2 == 0 else (x + length, x)
        pts += [(a, y_k), (b, y_k)]
    return Trace(points=tuple(pts), width_mm=width, net=net, name=name)


def interdigitated(x, y, finger_len, fingers, pitch, width=0.5, nets=("A", "B")) -> list[Trace]:
    """Interdigitated electrodes (capacitive / humidity / strain sensing):
    two combs whose fingers alternate `pitch` apart."""
    out = []
    span = (fingers - 1) * pitch
    bus_a = Trace(points=((x, y), (x + span, y)), width_mm=width * 1.5, net=nets[0], name=f"{nets[0]} bus")
    bus_b = Trace(points=((x, y + finger_len + 1.5 * pitch), (x + span, y + finger_len + 1.5 * pitch)),
                  width_mm=width * 1.5, net=nets[1], name=f"{nets[1]} bus")
    out += [bus_a, bus_b]
    for k in range(int(fingers)):
        fx = x + k * pitch
        if k % 2 == 0:
            out.append(Trace(points=((fx, y), (fx, y + finger_len)), width_mm=width, net=nets[0]))
        else:
            out.append(Trace(points=((fx, y + 1.5 * pitch), (fx, y + finger_len + 1.5 * pitch)),
                             width_mm=width, net=nets[1]))
    return out


def spiral_coil(cx, cy, r_in, turns, pitch, width=0.6, net="", name="coil", step_deg=10.0) -> Trace:
    """Archimedean spiral coil (NFC antenna / inductor / heater)."""
    pts = []
    n = int(turns * 360.0 / step_deg) + 1
    for i in range(n):
        t = math.radians(i * step_deg)
        r = r_in + pitch * t / (2.0 * math.pi)
        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return Trace(points=tuple(pts), width_mm=width, net=net, name=name)


def example_board() -> BoardDesign:
    """60 x 40 mm structural panel: four M3 mounting holes, a 2-pin power
    header feeding an LED through a resistor, and a strain-gauge meander
    with its own test pads."""
    holes = tuple(Hole(x=x, y=y, d_mm=3.2) for x, y in ((5, 5), (55, 5), (55, 35), (5, 35)))
    pads = (footprint("Header 2-pin 2.54", 12, 20, 90, "J1", ("GND", "VCC"))
            + footprint("LED / resistor 1206", 30, 28, 0, "R1", ("VCC", "LED_A"))
            + footprint("LED / resistor 1206", 44, 28, 0, "D1", ("LED_A", "GND"))
            + footprint("Test pad", 14, 9, 0, "TP1", ("SG",))
            + footprint("Test pad", 14, 15.8, 0, "TP2", ("SG",)))
    traces = (
        Trace(points=((12, 21.27), (12, 28), (28.5, 28)), width_mm=1.0, net="VCC", name="VCC"),
        Trace(points=((31.5, 28), (42.5, 28)), width_mm=1.0, net="LED_A", name="LED"),
        Trace(points=((45.5, 28), (50, 28), (50, 18.73), (12, 18.73)), width_mm=1.0, net="GND", name="GND"),
        Trace(points=((14, 9), (20, 9), (20, 11)), width_mm=0.8, net="SG", name="gauge lead 1"),
        meander(20, 11, 20, 1.6, 4, width=0.6, net="SG", name="strain gauge"),
        Trace(points=((20, 15.8), (14, 15.8)), width_mm=0.8, net="SG", name="gauge lead 2"),
    )
    return BoardDesign(name="example structural panel", holes=holes, pads=tuple(pads), traces=traces)


def translated(design: BoardDesign, dx: float, dy: float) -> BoardDesign:
    """A copy moved by (dx, dy)."""
    def mv(pts):
        return tuple((x + dx, y + dy) for x, y in pts)
    return design.model_copy(update=dict(
        outline=mv(design.outline),
        holes=tuple(h.model_copy(update=dict(x=h.x + dx, y=h.y + dy)) for h in design.holes),
        traces=tuple(t.model_copy(update=dict(points=mv(t.points))) for t in design.traces),
        pads=tuple(p.model_copy(update=dict(x=p.x + dx, y=p.y + dy)) for p in design.pads),
        zones=tuple(z.model_copy(update=dict(points=mv(z.points))) for z in design.zones),
    ))


__all__ = [
    "FOOTPRINTS", "BoardDesign", "Hole", "Pad", "Trace", "Zone", "board_polygon", "conductor_union",
    "conductors", "crossovers", "example_board", "footprint", "hole_outlines", "interdigitated", "joined",
    "load_design", "meander", "pad_shape", "save_design", "spiral_coil", "trace_shape", "translated",
]
