"""Wind Turbine Hub: a disc, printed twice, that the blades' roots are
clamped between (one disc in front of the roots, one behind), with screws
beside each root through both discs. It is laid as a short Z shell
(layers/revolve.py): rings from its edge in to the shaft hole, one helical
stroke, hub_thickness_mm tall, centred at (start_x, start_y).

Layout, mm from the disc's centre, seen from the front (upwind); blade 1
along +Y, the others every 360/n degrees anticlockwise:

    seats    each root's footprint: from `start` to start + root length out
             along its blade's line, root width across it
    screws   two per blade beside its seat, halfway along it: root width/2 +
             screw/2 + 1.5 mm to either side of the line
    start    the least that keeps neighbouring seats apart and clears the
             shaft coupler's screws round the middle,
             max((width/2 + 1) / tan(180°/n), 1.25·shaft + 8), raised until
             every screw is 2.5 x its size from the others and clear of
             every other seat
    radius   the farthest screw + 3 x its size (edge distance), or the seats'
             outer corners, whichever is further

The shaft hole is printed (the fibre runs round it); the screw holes are
not: the fibre runs over them, and they are drilled after curing from the
slice notes or the 1:1 drilling template (hub_template_svg).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.affinity import rotate
from shapely.geometry import Point, box

from .blade import root_size

SCREW_LENGTHS = (16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120)


@dataclass(frozen=True)
class HubLayout:
    blades: int
    radius: float               # disc radius
    shaft: float                # shaft hole diameter
    start: float                # seats begin this far out
    seat_length: float          # root length
    seat_width: float           # root width (across the blade's line)
    spacing: float              # gap between the discs = root thickness
    screw: float                # M size
    screws: tuple               # ((x, y), ...) from the centre, two per blade
    angles: tuple               # each blade's line, degrees (blade 1 = 90)

    @property
    def drill(self):
        """Clearance drill for the screws (M4: 4.5 mm)."""
        return self.screw + 0.5

    @property
    def cone_radius(self):
        """A nose cone (Conical Dome) this wide covers every screw head and
        washer (washer about 2.25 x the screw size across) and still sits on
        the disc."""
        reach = max(math.hypot(x, y) for x, y in self.screws) + 1.125 * self.screw + 2.0
        return min(reach, self.radius - 0.5)

    def screw_length(self, disc):
        """Shortest standard screw through both discs, the root between,
        two washers and the nut, with a thread or two to spare."""
        need = 2.0 * disc + self.spacing + 2.0 * 0.2 * self.screw + 0.8 * self.screw + 1.5
        return next((n for n in SCREW_LENGTHS if n >= need), math.ceil(need / 5.0) * 5)

    def seat(self, k):
        """Blade k's seat (a Polygon, from the centre)."""
        rect = box(self.start, -self.seat_width / 2.0, self.start + self.seat_length, self.seat_width / 2.0)
        return rotate(rect, self.angles[k], origin=(0.0, 0.0))


def _place(n, start, length, width, screw):
    angles = tuple(90.0 + 360.0 * k / n for k in range(n))
    side = width / 2.0 + screw / 2.0 + 1.5
    mid = start + length / 2.0
    pts = []
    for a in angles:
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        for sgn in (1.0, -1.0):
            pts.append((mid * ca - sgn * side * sa, mid * sa + sgn * side * ca))
    return angles, tuple(pts)


def hub_layout(geometry) -> HubLayout:
    """The hub that holds `hub_blades` of this blade root. Raises ValueError
    (plain words) when the screws cannot be placed."""
    g = geometry
    n = int(g.hub_blades)
    width, spacing = root_size(g)
    length = max(float(g.blade_root_length_mm), 1.0)
    shaft, screw = float(g.hub_shaft_mm), float(g.hub_screw_mm)
    start = max((width / 2.0 + 1.0) / math.tan(math.pi / n), 1.25 * shaft + 8.0)
    for _ in range(400):
        angles, pts = _place(n, start, length, width, screw)
        seats = [rotate(box(start, -width / 2.0, start + length, width / 2.0), a, origin=(0.0, 0.0)) for a in angles]
        apart = all(math.dist(p, q) >= 2.5 * screw for i, p in enumerate(pts) for q in pts[i + 1:])
        clear = all(Point(p).distance(s) >= screw / 2.0 + 1.0
                    for i, p in enumerate(pts) for k, s in enumerate(seats) if k != i // 2)
        if apart and clear:
            break
        start += 0.5
    else:
        raise ValueError(f"The hub has no room for {n} blades' screws at this Root Width and Screw Size. Use fewer "
                         f"blades, a narrower root or a smaller screw.")
    corners = math.hypot(start + length, width / 2.0)             # the seats' outer corners
    radius = max(max(math.hypot(x, y) for x, y in pts) + 3.0 * screw, corners)
    return HubLayout(n, radius, shaft, start, length, width, spacing, screw, pts, angles)


def hub_notes(lay: HubLayout, disc) -> list[str]:
    """What to drill and how the hub goes together, in plain words (`disc`:
    the disc's thickness, mm)."""
    spots = "; ".join(f"blade {k // 2 + 1}: ({x:.1f}, {y:.1f})" for k, (x, y) in enumerate(lay.screws))
    return [
        f"Wind Turbine Hub: a disc {2 * lay.radius:.1f} mm across with the {lay.shaft:g} mm shaft hole printed. Print "
        f"it twice: one disc goes in front of the blade roots, one behind, {lay.spacing:g} mm apart (the roots' "
        f"thickness).",
        f"Wind Turbine Hub: drill {len(lay.screws)} screw holes, {lay.drill:g} mm (M{lay.screw:g}), after curing "
        f"(the fibre runs over them), both discs clamped together. From the centre, blade 1 along +Y: {spots}. "
        f"Use M{lay.screw:g} x {lay.screw_length(disc)} mm screws with a washer either side.",
        f"Wind Turbine Hub: each root sits from {lay.start:.1f} to {lay.start + lay.seat_length:.1f} mm out, "
        f"{lay.seat_width:g} mm wide, the blades every {360 / lay.blades:g} degrees. Drill the shaft coupler's "
        f"own screw holes to its flange; a nose cone of {lay.cone_radius:.0f} mm radius covers the screw heads.",
    ]


def hub_template_svg(lay: HubLayout, title="Wind Turbine Hub") -> str:
    """A 1:1 drilling template (SVG, millimetres): the disc, the shaft hole,
    each blade's seat with its leading-edge direction, and every screw hole
    with its drill size. Print at 100 % and check the 100 mm bar."""
    margin = 14.0
    half = lay.radius + margin
    w, h = 2.0 * half, 2.0 * half + 26.0

    def p(x, y):                # disc coordinates (y up) -> SVG (y down)
        return f"{half + x:.2f}", f"{half - y:.2f}"

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" '
           f'viewBox="0 0 {w:.2f} {h:.2f}" font-family="Arial, Helvetica, sans-serif">',
           f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" fill="#ffffff"/>']
    cx, cy = p(0.0, 0.0)
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{lay.radius:.2f}" fill="none" stroke="#000" stroke-width="0.35"/>')
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{lay.shaft / 2:.2f}" fill="none" stroke="#000" stroke-width="0.3"/>')
    for k, a in enumerate(lay.angles):
        seat = lay.seat(k)
        pts = " ".join("{},{}".format(*p(x, y)) for x, y in list(seat.exterior.coords)[:-1])
        out.append(f'<polygon points="{pts}" fill="#eeeeee" stroke="#666" stroke-width="0.25" '
                   f'stroke-dasharray="1.5 1"/>')
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        mid = lay.start + lay.seat_length / 2.0
        lx, ly = p(mid * ca, mid * sa)
        rot = -a if ca >= -1e-9 else 180.0 - a         # along the blade's line, never upside down
        out.append(f'<text x="{lx}" y="{ly}" font-size="3.6" text-anchor="middle" dominant-baseline="middle" '
                   f'transform="rotate({rot:.1f} {lx} {ly})">blade {k + 1}</text>')
        # leading edge: anticlockwise, seen from the front
        tip = lay.start + lay.seat_length * 0.85
        ax, ay = tip * ca, tip * sa
        tx, ty = -sa, ca
        x0, y0 = p(ax - 6 * tx, ay - 6 * ty)
        x1, y1 = p(ax + 6 * tx, ay + 6 * ty)
        out.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="#a3600c" stroke-width="0.5" '
                   f'marker-end="url(#arrow)"/>')
    out.insert(2, '<defs><marker id="arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" '
                  'orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#a3600c"/></marker></defs>')
    for x, y in lay.screws:
        sx, sy = p(x, y)
        out.append(f'<circle cx="{sx}" cy="{sy}" r="{lay.drill / 2:.2f}" fill="none" stroke="#000" stroke-width="0.3"/>')
        out.append(f'<path d="M{float(sx) - 3:.2f},{sy} h6 M{sx},{float(sy) - 3:.2f} v6" stroke="#000" '
                   f'stroke-width="0.2"/>')
    out.append(f'<path d="M{float(cx) - 3:.2f},{cy} h6 M{cx},{float(cy) - 3:.2f} v6" stroke="#000" stroke-width="0.2"/>')
    y0 = 2.0 * half + 6.0
    out.append(f'<line x1="{margin}" y1="{y0:.2f}" x2="{margin + 100:.2f}" y2="{y0:.2f}" stroke="#000" '
               f'stroke-width="0.5"/>')
    out.append(f'<text x="{margin}" y="{y0 + 5:.2f}" font-size="3.2">this bar is 100 mm: print at 100 %, no '
               f'fit to page</text>')
    out.append(f'<text x="{margin}" y="{y0 + 10:.2f}" font-size="3.2">{title}: drill {lay.drill:g} mm '
               f'(M{lay.screw:g}) at every cross, both discs clamped together. Arrows: leading edge.</text>')
    out.append(f'<text x="{margin}" y="{y0 + 15:.2f}" font-size="3.2">Disc {2 * lay.radius:.1f} mm across, '
               f'shaft hole {lay.shaft:g} mm (printed), discs {lay.spacing:g} mm apart.</text>')
    out.append("</svg>")
    return "\n".join(out)
