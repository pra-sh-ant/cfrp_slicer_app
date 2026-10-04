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
Sidebar inputs ─► sys_config ─► resolve_process_constraints (check + adjust)
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
                 st.session_state.last_slice → dashboard / 3D viewer / G-code tab
```
Auto-slice is on by default, so every widget change re-slices. The last result is kept in `st.session_state.last_slice`.

## 3. Material and extrusion constant (sidebar)
```
e_const = (needle_dia × layer_height × E_multiplier × density) / fiber_weight
```
Each move adds `ΔE = dist × e_const × e_mult_point`. `e_mult_point` is the per-point speed factor from kinematics (§6), unrelated to the sidebar's "E-Multiplier". Fibre weight must be above zero, because it's the divisor.

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

Results cached with `st.cache_data`.

## 7. Hollow (revolve) shells
| Shape | r_outer(p) | Height |
|---|---|---|
| Sphere | `R·√(1 − (2p−1)²)` | fixed `2R` |
| Dome | `R·√(1 − p²)` | user |
| Tapered cylinder | `R(1−p) + r₁p`, `r₁ = max(0.45R, 3)` | user |
| Cylinder / square tube | `R` | user |

`r_inner = max(0, r_outer − tracks·gap)`; `layer_height = max(layer_height, 0.05)`; `num_layers = max(2, round(H/layer_height) + 1)`; `p_i = i/(num_layers − 1)`; `z_i = z0 + H·p_i`. Fill uses `track_count=None`, `r_min = 0`; direction alternates; anchor on the first printed layer.

## 8. Flat laminate pipeline
`z_i = z_offset + i·layer_height`; `angle_i = layup[i mod len(layup)]`. Per layer: geometry → anchor (first layer) → kinematics → ramp → append.

## 9. Ramp between layers (`_append_layer`)
1. Trim previous tail by `seam_trim` (capped at 40% of that layer's run).
2. Trim new head likewise; recompute first remaining point's `dist`.
3. Ramp in `steps = max(4 if Δz>0 else 1, ⌈L/0.75⌉, ⌈|Δz|/0.05⌉)`, each with `v = v_min`, `e_mult = 1`, `is_transition`; last step = `layer_start`.
4. Angle = `atan(Δz/Δxy)`.

Lead-in anchor: `P_anchor = P0 − L·(cos θ, sin θ)`, `θ = atan2(P1 − P0)`.

## 10. Fit to bed
Bed 180 × 220 × 200 mm, 1 mm margin. Error if footprint > `bed − 2·margin`; else shift by `margin − min` or `bed − margin − max`. Target outlines get the same shift.

## 11. Specialty generators
- **Mushroom:** base spiral inward `r(t) = base_r − gap·t/(2π)` from `θ0 = atan2(−cy, −cx)`, 36 steps/turn (≥ 1); centre; stem to absolute `z = stem_height` with `cure_dwell`; top spiral outward `r = r_min + gap·t/(2π)`, `v_z` within `r_min + 2·gap`, else `v_overhang`.
- **Conical Dome:** revolve shell with `r = R(1 − progress)`, spiral Z-ramping.

## 12. G-code
Header; `G28 / G92 E0 / G90 / G0 Z15`; purge `G1 F{purge_f} E{purge_e}`, `G4 P1500`, `G92 E0`; approach `G0 F{travel_f}`; `M1995 P47 S{uv}`; plunge `G1 F300 Z{z_offset}`, `G4 P500`; per point `G1 F{v·60} X Y Z E{cumulative}`; layer banners; `G4` at `cure_dwell`; `M1995 P47 S0`, lift +15, park, `M84`.

## 13. Validation
| Check | Rule | Result |
|---|---|---|
| Bounds | outside bed volume | Fail |
| Pump floor | any `pump_floored` point | Warning |
| Seam jump | XY distance at `layer_start` > gap | Fail |
| Ramp angle | steeper than `transition_angle + 2°` | Warning |
| Self-crossing | not simple and > 3 repeated points (0.01 mm) | Warning |

Fail > Warning > Pass.

## 14. UI
Sidebar panels: run controls, layering, geometry, placement, materials, kinematics. Tabs: dashboard, 3D viewer (≤ 6,000 points, ≤ 150 outlines, full/single-layer/playback), G-code.

## 15. Modelling assumptions
- Pump floor unmeasured (defaults to `v_min`).
- Extrusion factor ∝ v/v_max.
- Specialty shapes approximate.
- Fermat multi-island connectors can cross.
