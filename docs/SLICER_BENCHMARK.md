# Slicer benchmark: what to take from OrcaSlicer / Bambu Studio / Cura

Survey date: 2026-10-17. Companion to `DEEP_DIVE.md` (how this slicer works).

## 1. What was examined

| App | Version | Examined |
|---|---|---|
| OrcaSlicer | 2.4.2 | ~880 option keys from bundled profiles (`resources/profiles/*/{process,filament,machine}`), tooltip strings in `OrcaSlicer.dll` |
| Bambu Studio | 02.08.02.61 | Not analysed separately; Orca is a fork, so most Orca findings apply |
| UltiMaker Cura | 5.13.0 | 736-setting catalogue `share/cura/resources/definitions/fdmprinter.def.json`; bundled libs (pyclipper, shapely, trimesh, scipy, nlopt) |
| CHITUBOX Basic | 2.0 | Skipped (resin/DLP, not toolpath-based) |

Method: shipped data only (settings, tooltips, bundled libraries). No disassembly.
Orca/Bambu are AGPL: ideas are reimplemented here, no code is copied.

## 2. Features to adopt

Constraints that filter everything: one continuous stroke (no travel, retraction, cut),
min bend radius `r_min`, resin flow drives fibre advance, UV cure as laid.

| # | Feature (source keys) | Where it lands | Improvement for CFRP |
|---|---|---|---|
| 1 | Robust mesh slicing: holes, all islands, closing radius (Orca `slice_closing_radius`; Cura slicing tolerance / meshfix) | `geometry/mesh_slicer.py` | Today only the largest polygon is kept and holes are ignored. Keep the outer/hole hierarchy and every island |
| 2 | Calibration generators (Orca `resources/calib`) | new `calibration.py` | Coupons for min bend radius, pump floor, speed vs track width, track pitch; replaces placeholder values |
| 3 | Adaptive pressure advance (Orca `adaptive_pressure_advance_model`: PA fitted vs flow and accel) | `kinematics.py` | Fixed `pressure_advance_mm` becomes a measured table + interpolation |
| 4 | Extrusion-rate smoothing (Orca) | `kinematics._plan` | Limit d(e_mult)/dt; resin pumps lag, so step changes in flow starve or flood the tow |
| 5 | Junction deviation / lateral accel (Orca `machine_max_junction_deviation`, Cura jerk) | `kinematics.py` corner cap | v = sqrt(a_lat * R) instead of the linear radius map |
| 6 | Overhang classification (Orca `overhang_1_4..4_4_speed`, `overhang_flow_ratio`, `slow_down_curled_perimeters`) | new pass over trajectory, STL + mushroom | Unsupported fraction per point -> lower speed, more resin, more UV dwell (`v`, `e_mult`, `cure_dwell`) |
| 7 | Minimum layer time (Orca `slow_down_layer_time`) | `kinematics.py` / layers | Guarantees cure/gel time per layer |
| 8 | Spiral-vase flow ramps (Orca `spiral_starting_flow_ratio`, `spiral_finishing_flow_ratio`, `spiral_mode_max_xy_smoothing`) | `build_revolve_shell` | No blob at lead-in, no starved end |
| 9 | Scarf seam (Orca `seam_slope_*`, `scarf_joint_flow_ratio`, `scarf_angle_threshold`, `staggered_inner_seams`) | `layers.append_layer`, `_helical_connector` | Flow taper along ramps; staggered seam angle per layer (fixes revolve seam drift); conditional ramp on smooth loops |
| 10 | Layup schedule (Orca `sparse_infill_rotate_template`, `infill_rotate_step`, `solid_infill_direction`) | `laminate.py`, `serpentine.py` | Stacking sequence e.g. [0/45/90/-45]s, with symmetric + balanced check (unbalanced laminates warp) |
| 11 | Skin / core density (Orca `skin_infill_*`, `skeleton_*`; Cura gradual infill) | per-layer `gap` | Sandwich layups, graded fibre volume fraction |
| 12 | Shrinkage and contour compensation (Orca `filament_shrinkage_compensation_z`, `xy_contour_compensation`, `xy_hole_compensation`; Cura `xy_offset`) | `config.py`, outline stage | Thermoset cure shrinkage (1-3 %) and tow-width offset |
| 13 | Per-feature flow/speed (Orca `first_layer_flow_ratio`, `initial_layer_speed`, `small_perimeter_speed`) | per-point `e_mult`, `v` | More resin on layer 1, slower small loops |
| 14 | Thin regions (Orca Arachne, `detect_thin_wall`, gap fill) | `geometry/` | Fixed tow width, so use a medial-axis single-tow path where Fermat's U-turn fails |
| 15 | Combing (Cura `travel_avoid_*`) | `fermat.py` connectors | Route connectors through the region interior so they never cross laid fibre |
| 16 | Adaptive layer height (Orca/Cura) | domes, STL | Layer height follows slope; less stair-stepping |
| 17 | Profile inheritance (Orca `inherits`, machine/filament/process split) | `presets.py` | Machine / material / process presets with parents and compatibility |
| 18 | Viewer colour modes + estimates (Orca preview) | `viewer.py`, GUI | Colour by speed, `e_mult`, curvature, overhang; totals for fibre length, resin mass, time |
| - | Arc fitting / resolution (Orca `enable_arc_fitting`, Cura `meshfix_maximum_deviation`) | `gcode/writer.py` | Already present (G2/G3, 0.02 mm) |

## 3. Rejected

Retraction, wipe, Z-hop, ooze prevention (conflict with no-travel); prime/wipe tower,
multi-material, tree supports (hardware); fuzzy skin, ironing, lightning infill
(break fibre continuity or one-stroke rule).

## 4. Beyond the surveyed slicers

None of them model fibre. From composites practice (not found in the installed apps):
- Coverage map: rasterise the tow footprint per layer, report void % and overlap %
  (stronger than the current self-crossing check).
- Stress-aligned paths: orient tracks along principal stress directions of a simple FE field.
- Fibre volume fraction and mass report.

## 5. Implementation order and status

Every feature is opt-in and off by default, so golden tests stay byte-identical. A feature
that changes default output regenerates goldens deliberately (`python -m tests.golden.regenerate`).

| Order | Item | Status |
|---|---|---|
| 1 | STL multi-island + holes (#1) | done (Phase 23); holes need a new generator, see 6 |
| 2 | Calibration + measured PA/pump model + extrusion-rate smoothing (#2-4) | done (Phase 27): 4 coupons + PA table; rate smoothing not needed (see s7) |
| 3 | Coverage check + overhang-aware speed/cure (#6, s4) | coverage done (Phase 23); overhang planned |
| 4 | Layup schedule with symmetric/balanced check (#10) | planned |
| 5 | Shrinkage + tow-width compensation (#12) | planned |
| 6 | Scarf flow taper + staggered seams (#9) | staggered seams done for round shells (Phase 27, opt-in); flow taper planned |
| 7 | Viewer colour modes + estimates (#18) | planned |
| 8 | Hole-aware spiral generator (contour tree, rings around holes linked into one stroke) | planned |

## 6. Phase 23 findings (item 1)

- STL import did not work in the shipped `.venv312`: Trimesh's `section()` needs
  scipy and `fill_holes()` needs networkx, neither installed; errors were swallowed.
  Replaced with an in-house numpy/shapely slicer (no new dependencies).
- Islands: an outside-in or inside-out spiral ends at its centre, so any connector
  to the next island crosses laid track. Multi-island layers therefore use paths
  that enter and leave on the perimeter (Fermat, serpentine) and route exits along
  the outer track ring when needed. Gaps are spanned by fibre (`is_bridge`).
- Holes, measured with the new coverage metric (80x40 plate, two 8 mm holes, full
  fill, coverage / overlap):

  | Generator | rings stop at hole (current) | keyhole slit (tried, removed) |
  |---|---|---|
  | Archimedean | 83.4 % / 5.8 % | 61.0 % / 11.0 % |
  | Fermat | 66.3 % / 0.5 % | 37.2 % / 5.3 % |
  | Serpentine | 60.9 % / 4.5 % | 55.5 % / 4.1 % |

  Keyholing makes consecutive rings differ in shape, which breaks the full-lap
  blend (`blend_chain`) and Fermat's U-turn arc. Item 8 is the real fix.
- Other gaps the metric exposes: serpentine erodes the region by r_min (a 30 mm pad
  is ~64 % covered); Fermat leaves its centre unfilled (~70 %).

## 7. Phase 27 (item 2) and seam stagger

All opt-in; defaults leave part output byte-identical (golden tests).
- Calibration coupons (`cfrp_core/calibration.py`; GUI Tools > Generate Calibration
  G-code; `tools/make_calibration.py`): pump floor, bend radius, pressure advance,
  track gap. Each is one continuous stroke within r_min (except the bend-radius
  coupon, whose turns are the test), with every band labelled in the G-code and
  reading instructions in the header.
- Measured pressure advance: `kinematics.pressure_advance_table` (GUI "PA Table",
  e.g. `5:1.0, 10:3.0`) interpolated at each point's speed. A pump that lags by
  time t needs a look-ahead of about v·t, so the advance should grow with speed.
- Extrusion-rate smoothing (Orca) is not added: flow per mm follows v/v_max, so flow
  rate goes as v² and its rate of change is already bounded by the accel/decel
  limits; a separate limiter would only alter parts.
- Staggered seams for round revolve shells (`seam_stagger_deg`, GUI "Seam Stagger"):
  rotates each layer's ring so seams do not form a vertical line (stack 25 -> 1 at
  137.5°). Not offered for flat parts: a continuous stroke must start each layer where
  the last ended, so moving the seam would add a connector across the part.

