# CFRP slicer: deep dive (how slicing works)

Companion to `ARCHITECTURE.md` (layout/history) and `SPEC_current.md` (formulas). This file explains the *why* and the data flow, and what to reuse or change when extending the slicer for continuous-fiber thermoset parts. Read from code on 2026-10-17; not re-run.

## 1. Governing rule

The fiber is never cut or lifted, so a part is **one continuous stroke**: no travel moves, no retraction. Three physical limits shape the code:

| Limit | Enforced by |
|---|---|
| No cut/lift | One polyline per layer; layers joined by ramps (`layers.append_layer`) or one helical stroke (`build_revolve_shell`) |
| Min bend radius `r_min` | `geometry._common.fillet` (geometry time), `enforce_min_radius` (post-path), curvature audit in `validation.py` |
| Fiber only advances with enough resin flow | `kinematics._plan`: `e_mult = max(v/v_max, pump_floor/v_max)` |

Resin is UV-cured as laid: UV on once at start (`M1995 P47`), off at end.

## 2. Pipeline

```
ProcessConfig -> constraints.resolve_process_constraints (soft fixes + notes)
  -> builder: build_flat_laminate | build_revolve_shell | build_stl_laminate | specialty_shape
       -> trajectory: list[dict] {x,y,z,dist,v,e_mult,layer,layer_start,is_transition,pump_floored,z_layer,cure_dwell}
  -> machine.fit_trajectory_to_bed -> gcode.compile_gcode -> validation.validate_trajectory
```

Entry: `pipeline.slice_part`. Core has no UI imports (`tests/test_core_isolation.py`). The trajectory dict list is the shared interface for G-code, validation and viewer.

## 3. From shape to 2D region

- Primitives (`geometry/outlines.py`): same outline every layer.
- Revolve shells: radius profile `r(progress)` -> annulus per Z (`geometry/revolve.py`). No mesh.
- STL (`geometry/mesh_slicer.py`): numpy triangle-plane cut at mid-layer Z, segments chained into rings, even-odd combined (holes and all islands kept, world XY), small leaks closed by `stl_closing_radius_mm`. Trimesh only loads the file. `stl_islands="largest"` (default) keeps one island per slice; `"all"` fills every island in one stroke (`layers._multi_island_layer`): perimeter-entry paths per island (Fermat / serpentine), connectors flagged `is_bridge`.

## 4. From region to one continuous path (`geometry/`)

- **Archimedean** (`archimedean.py`): inset rings at `gap/2 + k*gap` (pyclipper offset on the *outer shell only*, then `fillet`), resample each ring to equal point count, phase-align to previous ring, then `blend_chain`: `P(i)=(1-u)A_i+uB_i`, u 0->1 per revolution. Constant pitch, no reversal, no self-crossing. Outside-in starts with a full outer lap.
- **Fermat double spiral** (`fermat.py`): chain A (inward, insets `gap/2+2k*gap`) and chain B (return, `1.5*gap+2k*gap`, ring order and winding reversed). Offset splits -> child regions visited depth-first, bridged along a printed ring. Arc U-turn at the centre. Fails in narrow walls (U-turn < r_min), so `constraints.py` swaps to Archimedean for revolve shells.
- **Oriented serpentine** (`serpentine.py`): rotate by -theta, erode by r_min, scanlines at `gap` pitch keeping the **longest segment per line**, arc U-turns radius `max(chord/2, r_min)`, rotate back. The only pattern that realises a real layup angle. 45-degree hatch is this at +/-45.
- Corner-step spiral: `corner_step.py` (not covered here).
- Layers alternate outside-in (even) / inside-out (odd) so the stroke end meets the next start (`laminate.laminate_layer`).

## 5. Joining layers

- Flat: `append_layer` trims `seam_trim` mm from previous tail and new head (<=40% of run), bridges with a tangent-continuous Hermite ramp, Z linear, points flagged `is_transition`, speed `v_min`. Last ramp point carries `layer_start`.
- Revolve: one vase-mode stroke. `_helical_connector` laps forward with smoothstep radius, `_spiral_z` raises Z linearly with path length, `_skip_behind` shortens connectors, single-ring layers collapse into their connector.
- First layer gets `add_lead_in_anchor` (tangent tail) or `add_arc_lead_in_anchor` (round shells).

## 6. Speed / extrusion (`kinematics.py`)

Per point: corner-radius cap (v_max at R>=3*r_min down to v_min at R=r_min), heading cap `0.5+0.5cos(theta)`, backward/forward accel scans (kept as exact sequential loops on purpose, see ARCHITECTURE s4), pump floor, then pressure-advance look-ahead by `advance_dist` mm. G-code: `E += dist * e_const * e_mult`.

## 7. G-code and validation

`gcode/writer.py`: Marlin-style, one `G1 F X Y Z E` per point, G2/G3 arc fitting (tol 0.02 mm, >=4 points, breaks at layer starts/transitions). `validation.py`: bounds, seam jump > gap (Fail); pump floor, ramp angle, self-crossing (Z-chunked), curvature (Warning).

## 8. Known limits

Fermat multi-island connectors can cross printed track; pump floor unmeasured (= v_min); `e ~ v/v_max` is an assumption; revolve seam angle drifts; a few Fermat curvature violations on Rectangle/Circle. Holes: spiral rings stop near the first hole (`hole_keepouts`), so area beyond it is unfilled (plate with 2 holes: 83 % coverage Archimedean, 66 % Fermat, 61 % serpentine); a keyhole-slit approach was tried and measured worse with every generator, so holes need a contour-tree generator. Serpentine loses edge coverage (erodes by r_min); Fermat leaves its centre unfilled. `SliceResult.stats["coverage"]` measures all of these.

## 9. Extending for continuous-fiber thermoset parts

Reuse: continuous-polyline contract, three-layer curvature defence, `blend_chain`, Hermite seam ramps + angle check, kinematics planner (measure `pump_floor`), frozen config + constraints + validation layering, golden tests (`tests/golden`).

Change/add: hole-aware spiral generator (contour tree); unidirectional angle fill that keeps concave-region fiber (serpentine drops all but the longest segment per line); per-layer cure strategy (UV power/dwell) instead of UV-on-for-job; measured flow model; revisit "no travel" if hardware can cut/re-feed. Design assumption: part = stack of continuous 2D spirals (plates, shells, domes). Branches/ribs/bosses need retrace or cut.
