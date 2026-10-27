# CFRP Slicer Pro: how it works (current spec)

> **Provenance.** This document was written by reading the code at the
> baseline commit (`e7571c2`). It describes intended behaviour as implemented
> then, so it is not an independent source of truth: where it disagrees with
> the code, the disagreement is listed in `docs/ARCHITECTURE.md`
> ("Spec/code mismatches").

## 1. Purpose and core constraint
The app generates toolpaths for a continuous-fibre printer. A carbon-fibre strand is pulled through a needle by resin flow, and the resin is UV-cured as it's laid down. Three physical rules shape almost every design decision:

1. **The fibre can't be cut or lifted.** The whole part is one continuous stroke, with no travel moves.
2. **The fibre can't bend sharper than a minimum radius `r_min`,** or it kinks or breaks.
3. **The fibre is only pulled through while resin flow is strong enough** (shear drag). If pump flow drops too low, the fibre stalls.

## 2. End-to-end flow
```
Settings panel ─► sys_config ─► resolve_process_constraints (check + adjust)
                                          │
              ┌───────────────────────────┼────────────────────────────┐
         flat laminate              revolve shell                 specialty
   (per layer: geometry →     (per Z slice: annulus →       (one-shot generator,
    anchor → kinematics →      spiral → anchor →             fixed speeds)
    ramp → append)             kinematics → ramp → append)
              └───────────────────────────┼────────────────────────────┘
                                          ▼
                 _finalize: fit to bed → G-code → validation
                                          ▼
                 SliceResult → dashboard / 3D viewer / G-code tab
```
Auto-slice is on by default, so every setting change re-slices (in a worker thread).

## 3. Material and extrusion constant (sidebar)
```
e_const = (needle_dia × layer_height × E_multiplier × density) / fiber_weight
```
Each move adds `ΔE = step × e_const × e_mult_point`, `step` the length of the move into the point (from the previous point; a point's `dist` from the speed planner is the step to the next point, §6). `e_mult_point` is the per-point speed factor from kinematics (§6), unrelated to the sidebar's "E-Multiplier". Fibre weight must be above zero, because it's the divisor.

## 4. Checking settings before slicing (`resolve_process_constraints`)
**Hard errors (raise `ValueError`):**
- `track_gap > 0`, `r_min ≥ 0`, `tracks_per_layer ≥ 1`, `v_max > 0`.
- Positive layer height, `e_const`, purge speed, approach speed; purge amount ≥ 0.
- Specialty shapes: `v_z > 0`; Mushroom also `v_overhang > 0`, `dwell ≥ 0`.
- Shapes that use the speed planner: `accel > 0`, `decel > 0`; `v_min`, pump floor, pressure advance, anchor length and seam trim ≥ 0.
- Flat 2D shapes: width, height > 0 and `2·r_min + gap < min(width, height)`.

**Automatic adjustments (each reported as an info note):**
- Serpentine: `gap = max(gap, 2·r_min)`.
- `v_min = min(v_min, v_max)`, `pump_floor = min(pump_floor, v_max)`.
- When `r_min > gap/2`, it reports `window = min(w,h) − 2·r_min − gap`.

## 5. Geometry (`geometry.py`)

### 5.1 Base outlines (translated to `(start_x, start_y)`)
| Shape | Polygon |
|---|---|
| Rectangle | (0,0),(w,0),(w,h),(0,h) |
| Triangle | (0,0),(w,0),(w/2,h) |
| Hexagon | r=w/2, vertices `(r + r·cos 60k°, r + r·sin 60k°)` |
| Circle | disc of radius w/2 centred at (w/2, h/2) |
| L-Bracket | (0,0),(w,0),(w,t),(t,t),(t,h),(0,h) |

### 5.2 Printable boundary
```
wall    = outline.buffer(−gap/2)
rounded = wall.buffer(−r_min).buffer(+r_min)     # morphological opening = fillet
```

### 5.3 A flat layer (`generate_laminate_layer`)
1. Wall = printable boundary; infill area = `wall.buffer(−0.6·gap)`, largest piece kept.
2. `is_outward = layer_index is odd` (even layers outside-in, odd inside-out).
3. Pattern: Archimedean, Fermat or Oriented Serpentine.
4. With "Print Outer Boundary": inside-out = infill then wall; outside-in = wall then infill. The wall loop is rotated to start nearest the joining point; only the zigzag may be reversed.
5. The previous layer's last point is prepended for continuity.

### 5.4 Concentric (Archimedean) spiral
1. Rings at inset `d_k = gap/2 + k·gap` of the **outer shell only**, filleted; stop when the offset vanishes (< 1 mm²), comes within `0.9 × gap/2` of a hole, or `track_count` is reached (raises if fewer fit).
2. Resample every ring to `N = max(120, L_max / 0.75)` points, phase-anchored to the previous ring's start.
3. Blend: `P(i) = (1 − u)·A_i + u·B_i`, `u = i/N` (one revolution per ring pair).
4. First and last ring traced as full closed laps.
5. Drop consecutive points < 0.05 mm apart.

### 5.5 Fermat connected double spiral
1. Chain A insets `gap/2 + 2k·gap`; chain B insets `1.5·gap + 2k·gap`.
2. Inward pass blends chain A.
3. Splits become child regions, visited depth-first, nearest first.
4. Outward pass blends chain B in reverse ring order with each ring's point order reversed (anchor kept).
5. `N = max(60, L_max/0.75)`; `track_count` is a soft cap (2 per ring pair); inside-out layers reverse the whole path.
6. Known limit: with 2+ islands the straight connector can cross printed track.

### 5.6 Oriented serpentine
Rotate by `−angle`; erode by `r_min` (fallback to un-eroded); scan lines at `miny + gap/2 + k·gap`, longest segment per line; alternate direction; join with a cubic Bézier (`ext = max(1.5·r_min, gap)`, 15 samples); rotate back.

### 5.7 "Serpentine Infill" shape
```
line_gap  = max(gap, 2·r_min)
width     = w − 2·r_min   (or w if "Invert Bends")
num_lines = ⌊h / line_gap⌋
U-turn    = semicircle, radius line_gap/2, 15 points
```
Rotated by the layup angle about (w/2, h/2), then translated. The same extents feed the displayed outline.

## 6. Speed planning (`kinematics.calculate_trajectory`)
1. `d_i = |p_{i+1} − p_i|`, last = 0.
2. Corner radius `R = |a||b||c| / (2|a × b|)` (9999 if collinear); if `R < 3·r_min`: `v ≤ v_min + (v_max − v_min)·max(0, (R − r_min)/(2·r_min))`.
3. Heading: `v ≤ v_min + (v_max − v_min)·(0.5 + 0.5·cosθ)`.
4. Backward `v_i ≤ √(v_{i+1}² + 2·decel·d_i)`; forward `v_i ≤ √(v_{i−1}² + 2·accel·d_{i−1})`.
5. `raw = v/v_max`; `e = max(raw, pump_floor/v_max)`; `pump_floored = e > raw`.
6. Pressure advance: each point uses the factor of the point `advance_dist` mm ahead.

Results are cached (`lru_cache`).

## 7. Hollow (revolve) shells
| Shape | r_outer(p) | Height |
|---|---|---|
| Sphere | `R·√(1 − (2p−1)²)` | fixed `2R` |
| Dome | `R·√(1 − p²)` | user |
| Tapered cylinder | `R(1−p) + r₁p`, `r₁ = max(0.45R, 3)` | user |
| Cylinder / square tube | `R` | user |

`r_inner = max(0, r_outer − tracks·gap)`; `layer_height = max(layer_height, 0.05)`; `num_layers = max(2, round(H/layer_height) + 1)`; `p_i = i/(num_layers − 1)`; `z_i = z0 + H·p_i`. Adaptive Layer Height (`shell_progress`): `z_0 = 0`, `z_{k+1} = min(z_k + h_k, H)`, `h_0 = layer_height`, `h_{k+1} = clamp(h_k·(1 + step/100))` between Layer Height and Layer Height Limit; a last layer under half the one before: `z_{n−2} = (z_{n−3} + z_{n−1})/2`; `p_i = z_i/H`, and each layer's `e_mult` × `(z_i − z_{i−1})/layer_height` (the first layer × 1). Fill uses `track_count=None`, `r_min = 0`; direction alternates; anchor on the first printed layer.

**Wind Turbine Blade (Z)** (`geometry/blade.py`), root length `L_r`, blade length `H` (Height); printed height `L_r + H`. Below `L_r` the section is the root, straight: a rectangle `W x T` (Root Width x Root Thickness, along the chord x across it; a square: `W x W`) with corners `1.05·r_min + gap/2`, never turned, or a circle of radius `R` (Outer Radius). Above it, `s = (h − L_r)/H`: up to `s = 0.30` (the shoulder) the root blends into the airfoil, point for point (the root's ray angle = airfoil parameter β + the airfoil's angle, trailing edge at β = 0, nose at π), with a smoothstep weight. From the shoulder, `u = (s − 0.3)/0.7`: chord `c = c_max + (c_tip − c_max)·u`, twist `θ = θ_max·(1 − u)²`; the airfoil is turned by pitch + `θ` (leading edge towards the pressure side). Airfoil: NACA 4-digit, camber 2 % at 40 %, thickness `t`, `x = (1 + cos β)/2`, `y_t = 5t(0.2969√x − 0.1260x − 0.3516x² + 0.2843x³ − 0.1036x⁴) + x·t_te/(2c)` (a linear flatback opening the trailing edge to `t_te = 2(1.05·r_min + gap)`); the pitch axis (30 % chord on the camber line) sits at `(start_x, start_y)`, the root's centre. Hollow: the section `tracks·gap` in, rounded at `1.05·r_min`, or solid once that closes. Each layer: rings at `gap/2 + k·gap` from the outline, rounded at `1.05·r_min`, until one comes within `0.9·gap/2` of the hollow; an inner ring shorter than `2π·1.05·r_min + 2L` is left out (and those inside it), `L = 1.5·√(6·gap·1.05·r_min)`. Rings are laid as whole laps, each starting midway along its upper side (between its extremes along the chord), joined by tangent Bézier lane changes over `L`; inward and outward alternate. Layers join along the last lap: `P = (1 − w)·A + w·B` (A the last lap from its end, B the next layer from its start, both by length, `w` a smoothstep) over a run of `max(6·d, 1.25·√(6·d·r_min))` mm of B (`d` the offset between them), skipping B's head where it lies behind A's end. A round root must fit one ring (`R ≥ r_min + 1.5·gap`), a rectangle `2·r_min + gap < min(W, T)`; Fermat is replaced by the rings.

Blade brim (Brim Rings `b`, 0–20, first layer only): `b` rings at the section's outline `.buffer(k·gap − gap/2)` (round joins), `k = b … 1`, each from its own seam point, joined by the lane changes and leading into the part's outer ring; laid after the anchor and, with it, flat at `z0`: the climb starts at the part's outer ring.

**Wind Turbine Hub** (`geometry/hub.py`): n blades (2–6) every `360/n` degrees, blade 1 along +Y, anticlockwise seen from the front. Seat k: the root's footprint `L_r x W` (round root: `2R`) along its line from `r0`. Screws (size d): two per seat, at `r0 + L_r/2` along its line, `W/2 + d/2 + 1.5` either side. `r0 = max((W/2 + 1)/tan(π/n), 1.25·shaft + 8)`, raised by 0.5 mm until every screw is `≥ 2.5·d` from the others and `≥ d/2 + 1` from every other seat. Disc radius: `max(farthest screw + 3·d, seat corner)`. Laid as a round Z shell, `hub_thickness` tall, centred at `(start_x, start_y)`: outer radius the disc's, inner the shaft hole's, rings from the edge in. Screw holes are not in the part (drilled after curing): notes and the 1:1 SVG template give them, drill `d + 0.5`, screw length the shortest standard one ≥ `2·disc + T + 0.4·d + 0.8·d + 1.5`. Nose cone (kit): radius `farthest screw + 1.125·d + 2` (≤ disc radius), 40 mm tall.

## 8. Flat laminate pipeline
`z_i = z_offset + i·layer_height`; `angle_i = layup[i mod len(layup)]`. Per layer: geometry → anchor (first layer) → kinematics → ramp → append.

## 9. Ramp between layers (`_append_layer`)
1. Trim previous tail by `seam_trim` (capped at 40% of that layer's run).
2. Trim new head likewise; recompute first remaining point's `dist`.
3. Ramp in `steps = max(4 if Δz>0 else 1, ⌈L/0.75⌉, ⌈|Δz|/0.05⌉)`, each with `v = v_min`, `e_mult = 1`, `is_transition`; last step = `layer_start`.
4. Angle = `atan(Δz/Δxy)`.
5. Strict Bend Radius (Phase 43): the climb is a Dubins curve (arcs at 1.03 or 1.005 × r_min and straight lines) from the tail cut, along its heading, to a head cut, along its heading. Tail cuts from `seam_trim` to `seam_trim + 30` mm, head cuts within 40 % of the layer, both only at vertices or inside straight segments ≥ 3 mm (a cut mid-arc reads ~2 % under the radius). Pairs are tried least fibre dropped first until the curve stays on the layer below or this one, keeps every bend of the join at r_min, crosses none of the new layer's fibre (any angle) or comes within half a gap of it, and is at least the run the transition angle needs. The climb is resampled evenly by length; Z rises linearly.

Lead-in anchor: `P_anchor = P0 − L·(cos θ, sin θ)`, `θ = atan2(P1 − P0)`.

## 10. Fit to bed
Bed 180 × 220 × 200 mm, 1 mm margin. Error if footprint > `bed − 2·margin`; else shift by `margin − min` or `bed − margin − max`. Target outlines get the same shift.

## 11. Specialty generators
- **Mushroom:** base spiral inward `r(t) = base_r − gap·t/(2π)` from `θ0 = atan2(−cy, −cx)`, 36 steps/turn (≥ 1); centre; stem to absolute `z = stem_height` with `cure_dwell`; top spiral outward `r = r_min + gap·t/(2π)`, `v_z` within `r_min + 2·gap`, else `v_overhang`.
- **Conical Dome:** revolve shell with `r = R(1 − progress)`, spiral Z-ramping.

## 12. G-code
Header; `G28 / G92 E0 / G90 / G0 Z15`; purge `G1 F{purge_f} E{purge_e}`, `G4 P1500`, `G92 E0`; approach `G0 F{travel_f}`; `M1995 P47 S{uv}`; plunge `G1 F300 Z{z_offset}`, `G4 P500`; per point `G1 F{v·60} X Y Z E{cumulative}` (a point with no move into it is not written), runs of points on one circle (within 0.02 mm, one way round, under half a turn, Z within 0.01 mm) as `G2`/`G3` with `I J` from the start to a centre at one radius from start and end; layer banners; `G4` at `cure_dwell`; `M1995 P47 S0`, lift +15, park, `M84`.

Hold points (`holds.py`): each `(at, seconds, cut)` marks the first point with `z ≥ z_first + at` that is not a climb or the anchor (`hold_s`, `hold_cut`; holds on one point add up; one above the top is left out with a note). After that point's move: `G4 P{1000·seconds}`; with `cut`, `M1995 P47 S0`, `M0` (pause to cut the fibre), `M1995 P47 S{uv}`, `G4 P500`. A hold point is never folded into a G2/G3 arc. Print time adds the hold seconds.

Cleaning pauses (`holds.place_clean_pauses`), any part: with Pause by Distance, at `k·clean_every_m` metres of path; with Pause by Time, at `k·clean_every_min` minutes (the sum of move length / `v`); `k = 1, 2, …` while `k·every < total − every/2`; each at the first point there that is not a climb or the anchor (`clean_s`; both rules may mark the same point once). After that point's move: `M1995 P47 S0`, `G4 P{1000·clean_pause_s}` (no move: the head stays put), `M1995 P47 S{uv}`, `G4 P500`. Never folded into an arc. Print time adds `clean_pause_s + 0.5` for each.

## 13. Validation
| Check | Rule | Result |
|---|---|---|
| Bounds | outside bed volume | Fail |
| Pump floor | any `pump_floored` point | Warning |
| Seam jump | XY distance at `layer_start` > gap | Fail |
| Ramp angle | steeper than `transition_angle + 2°` | Warning |
| Self-crossing | not simple and > 3 repeated points (0.01 mm) | Warning |
| Curvature | a bend under 0.98 × r_min, judged over 1.5 mm of path, climbs excluded | Warning |
| Curvature, Strict Bend Radius (flat and STL) | a bend under 0.98 × r_min anywhere, climbs included; the message names each layer and what bent (climb, turn ≥ 150°, join) | Fail |

Fail > Warning > Pass.

## 14. UI
PySide6 desktop app (`gui/`): settings panel with greyed-out settings, 3D viewer, dashboard, issue list, G-code tab, and the structural PCB editor. See `ARCHITECTURE.md` §1.

## 15. Modelling assumptions
- Pump floor unmeasured (defaults to `v_min`).
- Extrusion factor ∝ v/v_max.
- Specialty shapes approximate.
- Fermat multi-island connectors can cross.
