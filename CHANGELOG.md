# Changelog

All behaviour changes are listed here. Refactors that keep the golden
outputs identical are listed under "Internal".

## [Unreleased]

### Added (Phase 40 — usability)
- **Projects** (`cfrp_core.project`, File > Open / Save Project, Ctrl+Shift+O / S):
  one `.cfrpproj` file with the slicer settings (machine included), the PCB
  design, the ink settings and the print mode. The STL is referenced relative to
  the project when it lies beside it, so the folder can be moved or shared.
  Projects open from Open Recent too.
- **Named profiles** (`cfrp_core.profiles`): *Machine* (bed, G-code start),
  *Fibre / resin* (needle, flow, density, tow weight, gap, bend radius, layer
  height, speeds, pressure advance) and *Ink process* (every DIW setting); pick,
  save as, delete, stored in `~/.cfrp_slicer/profiles` (`$CFRP_PROFILE_DIR`).
  A profile only carries its own settings (a machine profile never changes the
  part).
- **Settings undo / redo** (Edit menu, Ctrl+Z / Ctrl+Y on the fibre tab; the PCB
  editor keeps its own): changes are grouped once they pause for 0.6 s; loading a
  file can be undone.
- **Ignored settings are reported** when a parameters file or project from
  another version has keys this one does not use (instead of dropping them
  silently).
- **The setting an error names is outlined in red** (label or field path; the
  longer label wins, "Layer Height" is not "Height"), its section opened and
  scrolled to; cleared by the next successful slice.
- **Faster large parts**: the slicing thread also builds what the 3D view draws
  (thinned data and tube meshes, `toolpath_mesh.prepare`); the window only uploads
  them (300k points: ~0.7 s of mesh building moved off the UI thread).

### Added (Phase 39 — structural PCB: crossovers, current, spread, checks)
- **Crossovers**: a wire on level 2 (`Trace.level`, editor *Crossover (level 2)*
  or the wire's *Level*) may cross another net's wire. The DIW slicer prints the
  level-1 ink, then an insulating bridge over each crossing (`bridge_layers`,
  `bridge_margin_mm`, feature `crossover`), then the level-2 wires with
  **conformal Z**: each point one layer above what lies under it, climbs started
  early enough to stay under 30°. A level-2 wire joins its own net where it
  touches it; crossing another net's pad is an error (the pad must stay bare).
  *Auto-route* falls back to a crossover when level 1 has no way through.
- **Current capacity** (`max_current_density_a_mm2`, default 20 A/mm²): every
  wire's "carries up to"; a wire with a `current_a` above it is reported with the
  width it needs; voltage drop and power shown in the editor.
- **Bead spread** (`spread_factor`): fills, raster spacing and edge insets use the
  spread bead, and so do the width and clearance checks.
- **Insulation edge rule** (`min_insulation_overlap_mm`, default 0.5): ink closer
  than that to the insulation's edge is reported (it would touch the carbon).
- **Shorted two-pin parts**: a part whose two pads are on one net is reported;
  pin 1 of every part is marked in the editor.
- **Drying wait** between ink layers (`layer_wait_s`, G4); print time now
  includes drying and UV cure waits.
- `tools/pcb_audit.py` checks that no ink is printed below ink already laid
  (replacing "Z never drops inside a layer", which crossovers now do on purpose)
  and audits a spread / drying / 3-layer-bridge configuration; 80 random boards
  (4 crossovers routed): no problem.

### Fixed (Phase 39)
- Editor: the *Ink settings* page was forced visible in Advanced mode and drew
  over the *Checks* tab.
- `pyproject.toml` did not list `pyclipper`, which the core imports (a wheel
  install failed on import); `trimesh` is now the `stl` extra.

### Added (Phase 38 — robustness, multi-layer planning, code health)
- **Solid bottom / top layers** (`solid_bottom_layers`, `solid_top_layers`, GUI
  *Pattern & Layers*, default off): that many layers at the bottom and top are
  filled solid with the First Layer / Skin Pattern. On an imported STL every slice
  whose top or bottom is exposed gets them too (the top of a base under thinner
  walls, the underside of an overhang). The info list names the skin layers.
- **Layer plan** (`layers.plan_layers`): pattern, fill and spiral direction per
  layer. Archimedean / Corner-Step layers after a zigzag layer now start
  outside-in from the perimeter, where the zigzag ended (inside-out, the climb ran
  to the centre and the layer's rings crossed it): with a zigzag first layer under
  spiral layers, crossings on layer 2 fell from 30 to 11 (Corner-Step) and 26 to
  20 (Archimedean) over four shapes x two zigzags. A run of an odd number of
  spiral layers between zigzag layers ends with a Fermat layer, noted in the info.
  Without skins and with one pattern the plan is unchanged.
- **New checks** (errors): fibre over a hole of the part; Z going down; a
  layer-to-layer climb over a hole or off the part.
- **"How to fix"** under each warning / error in the issue list and the print
  report (`cfrp_core.hints.fix_hint`), in plain words.
- Info notes: laminate thickness (layers x layer height), and a Z-Offset under
  half the layer height.
- **Property tests** (`tests/test_property_fibre.py`, hypothesis): random convex
  outlines and plates with up to three holes, every pattern, random gap / radius /
  layers / fill, must keep the fibre rules. Cases found by longer runs are pinned.
- **Audits in CI**: `validation_sweep.py --quick`, `multilayer_audit.py --quick`
  and `pcb_audit.py 12` run after the tests and fail the build on a crash, a Fail
  status or a broken invariant.
- `cfrp_core.trajectory`: `TrajectoryPoint` (the keys a point may carry) and
  `check_trajectory` (missing / unknown keys, non-finite numbers, bad flags).

### Fixed (Phase 38 — found by the property tests and a 480-case holed-plate scan)
- Ring spirals on plates with holes (Archimedean's rings that bend round holes,
  Corner-Step on holed outlines) put the lane-change seam in the middle of the
  longest edge even when a hole sat there: the lane change ran 0.8 mm over the
  hole, or across the next ring (4 crossings). The seam now moves to the next
  clean place (other straights, then points round the ring), counting the jump
  from where the stroke arrives.
- Radius smoothing pulled a ring's inside corner by a hole 2 mm over the hole
  (`enforce_min_radius_clear` keeps smoothed points out of holes).
- Zigzag connectors that no single margin ring could serve fell back to a straight
  line, 1.9 mm across a hole by the plate's edge; they now go round on the margin
  rings, and a connector off the part is never chosen.
- The entry from the previous layer (or the wall loop) into a zigzag on a holed
  part hopped onto a ring across the plate, over a hole (7 Fails in the scan); entry
  routes must now stay on the part, and the zigzag starts at the corner whose way in
  crosses fewest lines.
- "Requested N tracks, but only N-1 fit" on holed plates although the constraint
  step had allowed N: the rings that bend round holes are now used whenever only
  they reach the requested count (10 refusals in the scan).
- STL parts whose bottom slices are empty (a sphere, a cone tip): the first printed
  slice and the next one could both run outside-in; directions now count from the
  first printed slice.
- Fill Solid with a different First Layer Pattern gave that first layer the main
  pattern's track count instead of filling it.

### Known limits (Phase 38)
- A zigzag around a hole close to a 45° edge can keep a crossing: the only clean
  order needs a connector along the edge, where the U-turns reach ~0.2 mm past
  the corridor. Zigzag layers on holed parts can still cross the climb from the
  previous layer when it ended mid-part. Both are warnings, not fibre over a hole.

### Internal (Phase 38)
- `layers.py` split into the `layers/` package (`common`, `planning`, `flat`,
  `stl`, `revolve`); no module imports another's private helpers
  (`is_rectangle`, `stitch_rings`, `resample_ring_cw`, `ring_from`, `wall_seam`
  are public now).
- The Streamlit front end moved to `legacy/streamlit/` with its own requirements
  and tests; the root requirements are the core + the PySide6 GUI.
- Golden outputs unchanged (63 golden tests).

### Added (Phase 37 — the suggested upgrades)
- **Auto-router** (`cfrp_core.diw.router`): unrouted connections (ratsnest: same-net
  islands joined by a minimum spanning tree) drawn dashed in the editor;
  Auto-route draws each with grid A* (8 directions, bend penalty) clear of other
  nets, holes, keepouts and the edge, re-planning after every wire, and names the
  nets it cannot reach.
- **Imports / exports** (`cfrp_core.diw.io`): board outline + holes from ASCII DXF
  (LWPOLYLINE, POLYLINE, LINE chains, ARC, CIRCLE, $INSUNITS); netlists as
  `ref,pin,net` CSV or `NET: R1.2 D1-1` text; DXF export of outline, holes, wires.
- **Ink presets** (silver flake, nanoparticle silver, carbon/graphene, liquid metal,
  copper) and insulation presets (UV, epoxy, silicone).
- **Flow start/stop compensation**: close the valve early (pneumatic), prime and
  retract (volumetric); **UV cure** between ink layers (commands and time).
- **Embedded electronics** (print mode `embedded`, `embed_after_layer`): lower
  laminate -> insulated electronics (covering forced on) -> upper laminate as a
  second fibre stroke, filled solid, with windows over the pads; one program
  (no re-homing, two pauses).
- **Print report** (`cfrp_core.report.html_report`, toolbar *Report…*): one HTML
  page with the 3D view, time, materials, checks, coverage per layer, ink and
  wire resistances.
- **Editor**: multi-select (Shift+click, area rectangle, Ctrl+A), move/rotate/
  delete of many, copy/paste/duplicate with new part names, corner/bend editing of
  wires, areas and the board outline (drag, double-click to add, right-click to
  remove), Import/export menu, connection lines toggle, Auto-route buttons, ink
  preset choice in Easy mode; Easy and Advanced print-mode boxes stay in sync.
- **Viewer**: see-through part overlay (STL model as placed, or the extruded
  outline) and a measuring tool (two clicks: distance, plan distance, height).
- `tools/pcb_audit.py` also routes half the random boards and checks the embedded
  mode (ink between the laminates, one program).
- **DIW flow coupon** (`cfrp_core.diw.calibration.flow_coupon`, written with the other
  calibration coupons): six lines with increasing close-early distance / retract.
- Fixed: the early valve close only fired when a path point happened to lie inside
  the distance (never on long straight raster lines); the move is now split exactly
  there.
- Fixed (found by the audit): holes that pinch a board split the wall centreline
  and `printable_boundary` crashed (MultiPolygon has no exterior); each piece is now
  rounded, and with Print Outer Boundary the stroke follows the largest piece
  instead of printing nothing.

### Fixed (Phase 36 — deep PCB / structural audit, usable Advanced panel)
- New `tools/pcb_audit.py`: random boards (outline, holes, every palette part,
  wires between random pads, moves and rotations) sliced in all three print
  modes, checking invariants. It found, now fixed:
  - **ink printed off the board** (174 points) and **over holes** (16) when a part
    or wire was placed too near an edge/hole: conductor paths are now clipped to
    the board minus holes (whole bead inside) and the checks say how much ink
    was left out;
  - **conductive ink off the insulation** near edges (288 points): the dielectric
    was trimmed further back than the ink; it now reaches as far as any ink.
  After the fixes 200 random boards x 3 modes: no invariant broken (also checked:
  shorts always reported, no fibre over a hole, ink above the fibre, ink on/off
  balanced, ink on the laminate's placement).
- Advanced editor panel rebuilt: grouped (Tools, New item settings, Sensors,
  Selection, Print), tools as a two-column grid of real buttons with the active
  one highlighted, board tools named in words; Easy/Advanced as a segmented
  switch; right side as tabs (Checks / How to / Ink settings) under the selected
  item instead of one long scroll; panel widths fixed (no dead gap).

### Changed / Fixed (Phase 35 — easy PCB editor, drag/drop/rotate, true bead width)
- PCB editor has **Easy** (default) and **Advanced** modes. Easy is six numbered
  steps in plain words: board size + Make board, 4 corner holes, a parts list to
  drag onto the board, Draw wire with three width presets, move/rotate, check &
  print (print mode + Slice board); the DIW process uses defaults and a "How to"
  guide replaces it. Advanced keeps every tool and setting. A hint line under the
  canvas says what the current tool expects.
- **Drag and drop** parts from the list onto the board; **live preview** while
  dragging; **rotate** the selection 90° (R / Shift+R or buttons); a placed
  footprint's pads select, move, rotate and delete as one part (labelled R1, D1…).
  Wires drawn without a net take the net of the pad they start on (or a new one).
  Palette includes sensors (strain gauge, heater, touch/humidity electrodes, coil).
  Editing logic is UI-free and tested (`cfrp_core.diw.edit`).
- **Bead width fixed in the viewer**: the bead was drawn as wide as Track Gap
  (the spacing, 1.2 mm) and the resin coat wider still; it is now the needle
  diameter (capped at the spacing), with the dry fibre tow inside at ~half that
  width. Outside Feature colours the full bead is drawn.
- Guide for designing a structural PCB in plain language: `docs/PCB_EDITOR_GUIDE.md`.

### Added / Fixed (Phase 34 — first layer, print modes, every setting tunable, plain-language help, viewer colours)
- **First layer**: `first_layer_solid` lays the first layer with as many tracks as fit;
  `first_layer_infill` picks its pattern (a spiral reaches 90-99 % on compact parts,
  zigzag line ends stay a bend radius in, ~83 %); `first_layer_min_coverage` warns
  below a target; `stats["first_coverage"]` reports it. A spiral base under zigzag
  layers runs inside-out so it ends on the perimeter where the zigzag starts, with
  its lead-in anchor laid on the innermost ring (`ring_tail`; a straight anchor
  from the centre was crossed by every ring). GUI default: on.
- **Print modes** for a structural PCB (`slice_structural_board(mode=...)`, editor
  drop-down): structural (fibre + electronics), fibre board only, electronics only
  (on an existing surface `DiwConfig.surface_z_mm` high, at Start X/Y).
- **More settings exposed**: STL crack closing and hole stitching; a Machine
  section (bed size, max height, edge margin; the viewer redraws the bed); DIW
  encapsulation layers, travel speed, Z-hop, valve open/close delays, E per mm³,
  tool command, head offset X/Y/Z, pause before ink, cure shrink, surface height.
  DIW settings that do not apply (valve codes in volumetric mode, …) are greyed
  with the reason.
- **Plain-language help** on every setting (fibre and DIW): what it does and what
  raising/lowering it changes.
- **Viewer colours**: carbon-grey fibre core inside a see-through amber resin coat
  (Feature colours, "Resin" toggle), layer transitions cyan, the last 10 mm of the
  stroke "End of path" pink with a red end marker (start marker green).
- Fixed: the tube shader was registered under pyqtgraph's default shader name and
  replaced the bed's shader (bed drew in the wrong colour).

Audit (all built-in shapes x patterns x settings, 1182 cases): no failures or
exceptions; the only warnings are curvature at U-turns (inherent while Track Gap <
2 x r_min) and one Fermat crossing on a triangle. 252-case multi-layer audit: no
errors; spiral "reversals" on triangles are seam trims on the r_min innermost ring.
Goldens unchanged.

### Added (Phase 33 — Structural PCB editor)
- New top-level tab **Structural PCB (DIW)** (`gui/pcb_editor.py`): mm canvas (Y
  up) with grid and snap, zoom/pan, cursor read-out; tools for the board outline
  (rectangle, rounded rectangle, polygon), holes, traces (width + net), pads, zones
  (conductor / dielectric / keepout), footprints with auto-numbered references,
  and generators for a strain-gauge/heater meander, interdigitated electrodes and
  a spiral coil; select, drag-move, delete, undo/redo; properties of the selection
  (trace length and resistance); live DRC list; DIW process settings;
  New / Example / Open / Save (`.spcb.json`).
- **Slice board** sends the outline (with its holes) to the fibre slicer as the
  Custom Outline and slices the ink on top: one 3D view (colour by Feature or
  Tool), merged fibre + DIW checks in the issue list, DIW ink, volume, time and
  per-trace resistance on the dashboard, combined G-code. The Custom Outline
  row's Edit… button and the toolbar's PCB Editor open the editor.

### Added (Phase 32b — DIW slicing for structural electronics)
- `cfrp_core.diw`: structural PCB design model (`BoardDesign`: outline, holes,
  traces with width/net, pads, conductor/dielectric/keepout zones; JSON
  `.spcb.json`), footprint library (1206/0805, SOT-23, headers, JST-PH, test
  pad), parametric sensors (meander strain gauge/heater, interdigitated
  electrodes, spiral coil) and an example board.
- DIW slicer (`DiwConfig`): dielectric raster under the conductors (carbon fibre
  conducts), conductors as centreline passes or concentric fills, optional
  encapsulation leaving pads open; segments chained into zigzags, rings of a pad
  into one path; nearest-neighbour + 2-opt ordering; Z-hop travel; pneumatic
  (valve codes + dwell) or volumetric (E) dispensing; tool select and offset.
- DRC: shorts by connectivity, net-to-net clearance, bead-width minimum, edge /
  hole / keepout clearance, missing dielectric on carbon; per-trace resistance
  estimate (resistivity, cured thickness); ink length/volume, starts, time.
- `cfrp_core.board.slice_structural_board`: fibre laminate of the board (Custom
  Outline with its holes) + DIW on its top surface at the same bed placement,
  combined G-code (fibre, pause to cut the tow, DIW head). `SliceResult.bed_shift`
  records the auto-positioning shift.

### Added / Fixed (Phase 32a — Custom Outline shape, rings that bend round holes)
- Shape **Custom Outline** (`geometry.custom_outline`, `custom_holes`, mm, placed at
  Start X/Y): any outline with holes, e.g. a structural PCB from the editor; sliced
  like the flat laminates. Too-small or degenerate outlines raise plain errors.
- **Holes stopped spirals at the first hole**: a 60 x 40 board with four corner
  bolt holes got one Archimedean ring (17 % coverage). New `notched_rings`: each
  ring is the shell inset minus a keep-out per hole that grows one gap per ring
  once reached, so rings bend round holes one gap apart (fibre steered round
  bolt holes). Archimedean uses them (joined by lane changes) when holes would
  stop it early (annuli keep the blended spiral); the track cap counts them.
  Board: 17 % -> 55 % (7 tracks), 88 % with Fill Solid, no fibre over a hole.
- **Corner-Step ran over holes**: an outline with holes was taken for a rectangle
  and got the analytic rectangle path (13 mm of fibre across bolt holes). Holed
  outlines now use the polygon path with notched rings.
- Zigzag layer entry on holed outlines is routed off the holes (straight only if
  that stays on the part and crosses nothing).
- Fermat on a custom outline with holes carries a caution in the pattern list.

Goldens unchanged.

### Added (Phase 31 — 3D viewer; Phase 30 remainder — toolbar, issues, theme)
- Viewer rebuilt (`gui/viewer_gl.py`, `gui/toolpath_mesh.py`, `gui/range_slider.py`):
  toolpaths as shaded tubes with the bead's elliptical cross-section in GPU
  buffers, custom Blinn-Phong shader (key light + headlight + hemispheric
  ambient, specular), 4x MSAA. The stroke is ordered by layer, so the dual-handle
  layer range slider and the move scrubber only change the drawn index range;
  lower layers optionally stay visible, dimmed. Colour modes: feature (fibre,
  ramp, anchor, bridge, pump floor, DIW kinds), speed, layer, height, flow,
  **bend radius** (red below r_min, count in the legend), **fibre direction**,
  tool. Playback in real print time (1x-500x) with a nozzle marker, click a
  point to inspect it, perspective/orthographic, view keys 0-6/F, PNG export.
  Shape-preserving decimation replaces every-n-th-point sampling; falls back to
  lines if the shader cannot compile. The camera re-frames only when the part
  moves or resizes.
- Validation banner replaced by an issue list (status pill, summary, warnings,
  notes); clicking an issue that names a layer shows that layer.
- Toolbar: Slice, Auto-slice toggle (remembered), Open STL, Save G-code, Export
  CSV, Calibration; busy indicator while slicing; new dark theme in which
  greyed-out settings read as disabled.

### Added (Phase 30 — option states, greyed-out settings, new controls panel)
- `cfrp_core.capabilities`: `field_states` / `infill_states` say which settings and
  patterns apply to a setup and why not (read off the builders; e.g. layup is
  ignored by spirals and the fixed ±45° hatch, Tracks Per Layer by zigzags,
  seam trim / transition angle by single-layer parts and helical shells, the
  fixed pressure advance by a PA table). `tests/test_capabilities.py` proves it:
  changing any setting reported as unused leaves the sliced path identical.
- GUI controls panel rebuilt: collapsible sections ("n not used here"), unit
  suffixes, a help tooltip on every setting, settings search; settings that do
  not apply are greyed out with the reason (nothing is hidden); patterns that do
  not apply are greyed in the Infill list, patterns that will raise curvature
  warnings carry a caution icon and an inline hint. Offscreen Qt tests added.
- Trajectory points of the lead-in anchor carry `is_anchor` (viewer colouring).

### Fixed (Phase 29 — robust STL slicing for continuous fibre)
Same 25-mesh suite as Phase 28 (250 runs): Pass 12 → 24, self-crossings 7970 →
771, curvature points 2.51 M → 1.28 M; remaining errors are only the intended
unit/thin-wall messages.
- **Spiral teardrop loops**: Clipper's invalid-ring repair (`buffer(0)`) returned
  rings clockwise; blended into counter-clockwise neighbours the spiral drew a
  loop across its rings (sliced cylinder, tube, vase). Repaired rings are now
  oriented like Clipper's output.
- **Zigzag crossings around holes**: run-to-run connectors are routed straight or
  along margin rings (both ways round), scored by crossings then length, and the
  run order and direction is chosen by an exact subset search (greedy beyond 8
  runs). A classic cellular decomposition is tried as well and the cleaner path
  kept. Plate with two bolt holes: 14 → 0 crossings at every angle.
- **Thin walls**: slices too narrow for zigzag U-turns, with no room for Fermat's
  two tracks, or fitting no Corner-Step ring now fall back to Archimedean rings
  (info note) instead of stubs or a failed part; thin islands in multi-island
  mode get one wall lap.
- **Fill Solid** (`fill_solid`, opt-in): spirals lay as many tracks as fit (flat
  parts and STL slices). Mean STL coverage 61 % → 79 %. The low-coverage warning
  names it.
- `count_crossings` moved to `geometry._common` (one bulk STRtree query).

Goldens unchanged.

### Fixed (Phase 28 — real-world STL import)
Tested 25 meshes (pen holder, cylinder, cube, cone, pyramid, sphere, tray, tube, torus,
bracket with holes, table, two parts, inch/metre models, off-origin, missing face,
flipped normals, overlapping bodies, thin walls, thin plate; binary/ASCII STL, OBJ)
with every infill and island mode.
- **Silent truncation of broken meshes**: a single missing triangle left slice
  contours open; those slices were dropped and a 30 mm cylinder printed 1.4 mm tall
  with status Pass. Open contours are now stitched (gaps up to `stl_stitch_mm`,
  default 5 mm, as Cura's stitching) and every mesh problem becomes a validation
  warning: not watertight, holes bridged, holes too large (outline dropped), empty
  slices inside the model.
- **Overlapping bodies cancelled** (even-odd across the whole mesh turned the overlap
  into a hole): bodies are now sliced separately and unioned; a body with inward
  normals is a cavity. numpy-only body split (`mesh_bodies`).
- **Serpentine / 45° hatch on parts with holes or concavities**: kept only the longest
  segment per scan line, so U-turns crossed holes (320 mm of an 880 mm tube layer in
  mid-air). Now a boustrophedon decomposition: every segment kept, runs where the
  convex hull of consecutive segments lies on the part, joined by connectors that stay
  on the part and avoid laid and future fibre. Convex parts keep the single zigzag.
- **Angled zigzag layers were shifted**: rotated back about the path's own centroid
  instead of the outline's (0.5 mm on a triangle, off the part around holes).
- Thin walls: zigzag erosion falls back to one gap (U-turns stay inside) before the
  bare outline.
- `printable_boundary` dropped holes (fillet works on the exterior): coverage and the
  boundary wall now respect bores.
- Layers on sliced meshes start where the last ended (outer-ring phase / Corner-Step
  seam follow the previous end) instead of a chord across the rings.
- `stl_scale` (GUI "STL Scale") and a unit hint for models under 5 mm; specific errors
  for walls thinner than one track and for Fermat on single-track walls; the
  width/height constraint note no longer appears for STL; low-coverage warning names
  the Tracks Per Layer cap.
- GUI: "Print All Islands" now triggers a re-slice.

Golden changes: `*_serp` and `layup_*_serp` move back by <= 0.29 mm (rotation pivot
fix); everything else byte-identical. Built-in sweep crossings 19 -> 1.

### Added (Phase 27 — calibration coupons, measured pressure advance, seam stagger)
All opt-in; with defaults every part's output is byte-identical (goldens unchanged).
- **Calibration coupons** (`cfrp_core.calibration`, GUI *Tools > Generate Calibration
  G-code…*, `tools/make_calibration.py`): pump floor (lines at descending speed, flow
  following speed), bend radius (U-turns at descending radius), pressure advance
  (slow/fast/slow lines, one per advance, planned with the part's own accel and
  look-ahead), track gap (one exact-offset spiral whose ring spacing grows band by
  band). One continuous stroke each, labelled bands, reading instructions in the
  header.
- **Pressure advance table** (`kinematics.pressure_advance_table`, GUI "PA Table"):
  measured (speed, advance) pairs interpolated per point; a constant table gives
  exactly the fixed-advance output.
- **Seam stagger** for round revolve shells (`seam_stagger_deg`, GUI "Seam Stagger";
  0 = aligned as before).
- G-code writer: optional per-point `comment` (used by coupons; absent in parts).

### Fixed (Phase 26 — multi-layer toolpath planning)
New `tools/multilayer_audit.py` judges what happens between layers (per seam: ramp
chord, fibre reversal, steepest step; per part: seam stacking, footprint drift,
coverage, Z monotonic) over 252 multi-layer cases. Sweep: Pass 451 → 518, steep ramps
6 → 0, config errors 32 → 0. Goldens regenerated deliberately (see bottom).
- **Seam tail trim dropped a whole segment**: `trim_trajectory_tail` deleted the
  vertex past the cut without adding the cut, so on long straight segments the
  layer ended early (113 mm on a Corner-Step rectangle edge, 128 mm on a wall side)
  and that stretch was driven as ramp (ramp speed, e_mult 1). The vertex is now
  moved onto the cut, keeping its planned speed/flow.
- **Corner-Step rectangles, multi-layer**: odd layers use a new analytic outward
  path (`_rect_path_outward`, same clockwise winding, starts where the inward path
  ends; its steps provably do not cross) instead of the reversed path, and inward
  layers after one start with ring 0's corner arc instead of cutting it. No fibre
  reversal at any seam; rectangles pass.
- Corner-Step polygon rings are sampled ≤ 0.5 mm (fixed 256 points left 1.6 mm
  chords on big rings, cutting their r_min corners).
- **Ramps**: a short Hermite whose end tangents disagree with the chord falls back
  to a straight ramp when any step would exceed the angle (was up to 67° on narrow
  triangle seams); head extension cuts inside long segments.
- **Zigzag layer start** penalises folding back on the arrival direction.
- **Flat track counts** (Archimedean, Corner-Step rectangle) are capped to what fits
  with a note, like revolve shells, instead of failing every layer.
- Curvature audit slack 2% (seam cut points inserted mid-arc read ~1.7% under).

Audit after: every spiral (Archimedean, Fermat, Corner-Step) on every shape has no
fibre reversal at seams (except the 135×25 triangle, whose innermost ring is a
sliver), ramp chords ~6 mm, steepest ≤ 2.3°, footprint drift ~0. Remaining by
design/physics: line patterns (serpentine/hatch) fold back at some seams because a
scan line ends heading into the edge; seams alternate between two spots on flat parts
and align on revolve shells (one vertical seam line).

Golden changes: every flat multi-layer case gains the seam cut point (≈1 point per
seam) and moves slightly; statuses unchanged.

### Fixed (Phase 25 — curvature: real kinks removed, audit made exact)
Sweep (`tools/validation_sweep.py`, 1182 cases): Pass 95 → 451, crossings 60 → 16,
steep ramps 33 → 6, no crashes. Goldens regenerated deliberately (see bottom).
- **`enforce_min_radius` was creating kinks**: arcs built at exactly r_min read
  2.99999… and were "smoothed", bending their joins to R ≈ 1.6 mm (every Corner-Step
  corner; also crossings). It now ignores radii within 0.1% of r_min.
- **Curvature audit measures bend over 1.5 mm of tow** (`local_bend_radii`):
  length / turning within the window. Exact on arcs at any sampling; a corner after
  a long straight still reads tight; a coarse polygonal arc resampled finely no
  longer reads as a tight bend.
- **Corner-Step on polygons**: ring-to-ring steps are lane changes of length
  1.5·√(6·gap·r) on one seam (middle of the longest straight), so they bend at
  ≥ r_min and never drift onto corners; outward (odd) layers keep the winding
  instead of reversing the fibre at the seam. Circle/hexagon/triangle multi-layer
  now pass. Rectangles keep the analytic path; its odd layers are still reversed.
- **Boundary wall hand-over**: the wall seam is the projection of the previous end
  (or the middle of the longest side), not the nearest polygon vertex (usually a
  corner), and wall↔infill steps are the same lane change. Fermat and Archimedean
  enter at the wall's seam in its direction.
- **Fermat on revolve shells** switches to Archimedean whenever gap < 2·r_min (its
  reversal joins adjacent tracks, ≤ gap/2 radius, on every layer — the old
  wall-band test did not capture that). Flat Fermat gets an explanatory note.
- **Ramp head extension** cuts inside long segments (it skipped to the next vertex,
  e.g. 120 mm away on a Corner-Step rectangle) and is capped at two seam-trims, so
  an impossible ramp (thick layer, shallow angle) is still reported. Reversal seams
  start the ramp at the tail cut point.
- **Coverage metric** repairs invalid tow strips and can no longer fail a slice.

Remaining curvature is physical: serpentine/hatch and Fermat U-turns join adjacent
tracks (radius ≤ gap/2) whenever gap < 2·r_min; the constraint notes say so.

Golden changes: G-code changed for `*_fermat`, `*_serp`, `layup_*_serp`,
`rectangle_arch`, `hexagon_arch`, `triangle_arch`, `rectangle_arch_boundary` (smoother
seams). Now Pass: `circle_arch`, `rectangle_arch`, `rectangle_arch_boundary`,
`sphere_arch`, `dome_arch`, `taper_arch`, `conical_dome`. Serpentine counts rise
(every point of their 0.6 mm U-turns is counted now).

### Fixed (Phase 24 — validation sweep: every shape × infill × key setting)
Found with `tools/validation_sweep.py` (1182 cases from GUI defaults). Before:
406 Pass / 702 Warning / 42 Fail / 32 config errors, and two checks were blind.
Goldens regenerated deliberately (see bottom).
- **Fail: empty trajectory, square tube with 1 track** (every size). The hole kept
  sharp corners inside ~5 mm outer fillets, thinning the wall to 0.8 mm at the
  corners so no ring passed the hole clearance. Hole corners are now concentric
  with the outer ones (`r_outer − wall`).
- **Fail: empty trajectory, revolve wall wider than the radius allows** (e.g. 7
  tracks at R 10 mm). The track count is now capped to what fits at min bend
  radius, with a constraint note; a part too small for one track raises a clear
  error, as does a shell where no layer fills.
- **Missing layers mid-shell** (tapered/conical, 1 track). pyclipper occasionally
  returns a micro self-intersecting offset; `fillet` grew it into a spike that hit
  the hole and dropped the ring. Invalid clipper output is now repaired.
- **Square tube connector cut the corners**: the helical connector lapped a circle.
  For non-round shells it now follows the rings' own outline (polar blend), and
  their spiral pairs ring points by angle (`resample_polar_ring`), which cannot
  cross for nested star-shaped rings. Round shells unchanged (byte-identical).
- **Fermat layers started across the part**: the 180° seam phase-shift left the
  jump from the previous layer as a straight chord at layer Z under every ring.
  Layers now enter where the last one ended, choosing the winding (`mirror`) that
  continues its direction instead of reversing.
- **Zigzag (serpentine / 45° hatch) entries cut across their own lines**: the
  layer now starts at the zigzag corner nearest the previous end (or the wall's
  end), routed along an edge corridor if the straight jump would still cross.
- **Zigzag U-turns**: were circular caps fitted to the chord (90° kinks at the
  joins; wrong-way bulges at slanted/curved edges). Now a straight level-off plus a
  semicircle of radius gap/2 (`_uturn`): tangent-continuous, never crosses its
  neighbours. Line ends are eroded by `max(r_min, gap)` so turns stay inside.
- **Steep layer ramps** (Corner-Step, short seams): `append_layer(min_run=...)`
  moves the head cut until the ramp has the horizontal run the transition angle
  needs; reversal seams (turn > 90°) use a straight ramp instead of a hairpin.
- **Validator: self-crossing check was blind and noisy.** It counted repeated points
  only when `is_simple` failed (false positives on seam overlaps, missed real
  X-crossings). It now counts segment pairs crossing at ≥ 20° (shallower overlap is
  reported by the coverage metric).
- **Validator: curvature audit was blind at corners after long segments.** The
  three-point circumradius grows with leg length (a 90° corner after a scan line
  read ~25 mm). Now `local_bend_radii`: ½·min(leg) / sin(θ/2) on the de-slivered
  path, 1% slack for arcs at exactly r_min.
- New constraint note for line patterns with gap < 2·r_min (U-turns are at most
  gap/2 radius — physics, not a defect).

After: 0 Fail; real crossings 999 → 60 (degenerate 135×25 triangle corners, Fermat
centre); steep ramps 252 → 33; curvature warnings now appear where they are real
(serpentine/hatch U-turns, Fermat centre, Corner-Step corners, wall joins).

Golden changes: paths changed for all `*_fermat`, `*_serp`, `layup_*_serp`,
`triangle_arch` (seam/entry/U-turn fixes) and `square_tube_arch` (corner fix:
9963 → 12739 points, full 3 rings through the corners). Archimedean and revolve
G-code is otherwise byte-identical; their validation messages changed only through
the stricter audits (1–6 tight points each; `rectangle_arch_boundary` 38 at the wall
join; `conical_dome` loses a false "crosses itself").

### Added / Fixed (Phase 23 — robust STL slicing, multi-island strokes, coverage metric)
See `docs/SLICER_BENCHMARK.md` item 1. Golden outputs unchanged (no STL cases).
- **Fix: STL import was broken without scipy/networkx.** Trimesh's `section()` needs
  scipy and `fill_holes()` needs networkx; neither is a declared dependency, and
  the exceptions were swallowed, so every slice was dropped ("no valid slices") and
  non-watertight meshes crashed on load. Slicing is now done in numpy + shapely:
  triangle-plane segments (edge points always interpolated from the lower vertex,
  so shared edges join exactly), chained into rings, combined even-odd. Holes and
  every island are kept, in world XY. Mesh repair is best-effort.
- **Slice closing radius** (`geometry.stl_closing_radius_mm`, default 0.049 mm, as
  Orca's `slice_closing_radius`): closes small leaks in bad meshes.
- **Multi-island STL** (`geometry.stl_islands="all"`, GUI "Print All Islands"):
  every island of a slice is printed in one stroke. Each island is filled with a
  path that enters and leaves on its perimeter (Fermat for spiral infills,
  serpentine for line infills); connectors leave from the side facing the next
  island, retracing the outer track ring when a straight exit would cross laid
  fibre. Connector points carry `is_bridge`; validation reports bridged length.
  Default `"largest"` keeps the previous single-island behaviour.
- Fix: an STL layer whose Archimedean or Corner-Step track count did not fit was
  silently skipped (Corner-Step could yield an empty trajectory); it now falls
  back to as many tracks as fit. If no layer fills, slicing raises a clear error.
- Fix: an empty slice no longer collapses the Z of every slice above it.
- **Coverage metric** (`cfrp_core.coverage`): per-layer fibre coverage (void %) and
  tow overlap; area-weighted totals in `SliceResult.stats` and the GUI stats panel.
- Validation: new warnings for island bridges and for layers with no toolpath.
- Internal: `arc_along_ring` moved from `fermat.py` to `geometry/_common.py`.

### Changed / Fixed (Phase 22 — continuous helical seams for revolve shells)
- **Revolve shells are one continuous stroke** (vase-mode style): rings are linked
  by a forward helical connector (no trimming, no F300 `STRICT Z-SEAM LIFT` block,
  no direction reversal at seams). Z climbs linearly over each ring + connector,
  radius blends with a smoothstep, speed is planned once over the whole path.
- Single-track (single-ring) layers are exactly one helical lap each (no retraced
  lap, no false "path crosses itself" warnings); first/last layers stay whole.
- Fermat spiral on a revolve wall band narrower than 2×min radius + gap is
  replaced by Archimedean with an info note (the Fermat U-turn cannot fit).
- Flat-laminate layer ramps are tangent-continuous (cubic Hermite) instead of a
  straight chord.
- Validation: Z-aware self-crossing check; new curvature warning computed on the
  final bed-fitted trajectory (replaces the pipeline info note).
- GUI: retired slicer threads are kept alive until finished (fixes a hard crash
  when parameters change during a running slice).
- Performance: vectorized ring resampling; per-layer Archimedean path cache
  (full sphere 4.6 s -> 1.6 s cold, 0.7 s when only non-geometry params change).

### Added / Fixed (Phase 18 — full outer perimeter + 45° diagonal entry + 45° diagonal hatch)
- **Full outer-boundary trace**: outside-in archimedean spiral now opens with a
  complete closed lap of the outermost ring before stepping inward, guaranteeing
  full perimeter coverage on every even layer.
- **45° diagonal entry segment**: after the outer lap, a 3 mm bridging segment
  exits the perimeter seam at ~45° (bisector of ring tangent and inward normal)
  and lands on ring-1, replacing the previous abrupt radial step.
- **45° Diagonal Hatch** infill pattern: new option in the infill dropdown.
  Uses `oriented_serpentine` at +45° on even layers and −45° on odd layers for
  balanced cross-ply coverage.
- **Seam-trim ramp fix** (triangle and other narrow shapes): passes the previous
  layer's endpoint as `seam_hint` to inside-out `archimedean_path`, aligning the
  innermost-ring phase anchor with the outside-in seam so the layer-to-layer
  ramp trim cuts diverge correctly and the ≤30° ramp limit is always met.

### Fixed (Phase 12 — Fermat 2+ islands ring-arc fix)
- **Fermat connector no longer crosses printed rings** in non-convex shapes with
  2+ simultaneously-open child branches.  Root cause: the straight connector
  between child sub-regions exited the innermost printed A-ring boundary in
  non-convex geometries (e.g. L-bracket).
  Fix: new `_arc_along_ring(ring_poly, p_from, p_to)` helper routes the
  inter-child connector along the shorter arc of `a_rings[-1].exterior`
  instead of using a straight line; `visit_children` receives `innermost_ring`
  and applies the arc bridge between every pair of children (not before the
  first). When no rings exist yet (very small shapes), `poly` itself is used
  as a proxy ring.
- `fermat_spiral_path` docstring updated: known limit note removed.
- `docs/ARCHITECTURE.md`: Phase 12 status, known issues cleared.

### Added (Phase 11 — About dialog + trajectory CSV export)
- **Help → About**: dialog showing Python version, PySide6 version, and a
  one-line summary of completed phases.
- **Export CSV** button in G-code tab: writes the full trajectory as a CSV
  (columns: idx, x, y, z, v, dist, e_mult, layer, is_transition, layer_start).
  Enabled only when a trajectory exists; disabled until first successful slice.

### Added (Phase 10 — keyboard shortcuts)
- Ctrl+S → Save Parameters
- Ctrl+O → Load Parameters
- Ctrl+Shift+R → Reset to Defaults
- F5 → Slice Now (bypass debounce, force immediate re-slice)
- Ctrl+Q → Quit
- Escape → Cancel running slice (window-level shortcut, works without opening menu)
- All shortcuts shown in the File menu next to their actions.

### Added (Phase 9 — cancel button + window geometry persistence)
- **Cancel button** in the status bar: visible while slicing, hides on
  completion. Calls `SlicerWorker.cancel()` (cooperative flag) then
  disconnects signals so stale results are silently dropped.
- **Window geometry persistence**: `closeEvent` saves geometry via QSettings;
  `__init__` restores it on next launch (size, position, maximised state).

### Added (Phase 8 — print time, fiber length, fiber mass)
- `SliceResult.stats`: pre-computed dict of `print_time_s`, `fiber_length_mm`,
  `fiber_mass_g` (sum over non-transition trajectory points using `dist/v` for
  time and `fiber_weight_g_per_mm` for mass).
- Dashboard tab now shows Print time (h/m/s), Fiber length (m + mm), and
  Fiber mass (mg + g) below a separator after the geometry stats.

### Added / Fixed (Phase 7 — Recent Files + layer-height clamp)
- **Recent Files** submenu (File → Open Recent): remembers the last 8 saved/
  loaded `.json` parameter files via QSettings; includes "Clear Recent Files".
- **Layer-height clamp consistency**: `build_flat_laminate` now clamps
  `layer_height` to ≥ 0.05 mm (same as `build_revolve_shell`), preventing
  degenerate zero-height layers if a very small value slips through.

### Fixed (Phase 6 — Fermat seam phase-shift)
- **Fermat layer transitions now Pass validation** (previously Warning for all
  Fermat cases including `cylinder_fermat`).  Root cause: both the end of
  layer N and the start of layer N+1 landed on the outer ring at nearly the
  same angular position → XY gap ≈ `track_gap` → near-vertical ramp.
  Fix: pass `entry_hint = opposite(previous_layer_end)` to
  `fermat_spiral_path`, rotating the outer-ring phase-anchor ~180° each
  layer.  XY gap is now ≈ infill diameter, ramp angle ≈ 0.3°.
- Applies to flat laminates (`laminate_layer`) and revolve shells
  (`build_revolve_shell`) using Fermat infill.
- Golden data regenerated for all 6 Fermat cases; all now Pass.

### Added (Phase 3 — PySide6 desktop app)
- `gui_app.py` — entry point: `.venv312\Scripts\python.exe gui_app.py`
- `gui/` package: `main_window`, `controls_panel`, `viewer_gl`, `results_panel`,
  `slicer_worker` (slicing runs in a background QThread; UI stays responsive).
- Dark-themed PySide6 window: left parameter panel + right tabs (3D View /
  Dashboard / G-code). Slices automatically 500 ms after any parameter change.
- 3D viewer uses pyqtgraph OpenGL — fiber path coloured by speed (Turbo
  palette), layer isolation slider, print-bed mesh.
- G-code export via Save dialog.
- `pyproject.toml` `[gui]` extra: `PySide6 ≥ 6.7`, `pyqtgraph ≥ 0.13`,
  `PyOpenGL ≥ 3.1`.

### Changed (Phase 2)
- **Spiral Z-ramping** for all revolve shells (sphere, dome, cylinder, tube,
  taper, conical dome). Z increases continuously along each layer's path
  instead of stepping at the seam, eliminating the visible layer transition.
- **Conical Dome** promoted from a fixed-speed specialty shape to the revolve
  pipeline: now gets multi-track walls, speed planning, and spiral Z-ramping.
  Radius profile: r = R(1 - progress).
- **Default part** changed from 10 to 7 tracks per layer so the default
  135 × 25 mm rectangle slices successfully at startup.
- **Mushroom stem height** labelled as "Stem Top Height (absolute Z, mm)" in
  the UI for clarity.

### Removed
- **Torus (Z)** shape deleted (was a decorative looping curve with 360
  self-intersections, not a printable geometric torus).

### Internal
- Phase 1: all slicing logic moved into the UI-free `src/cfrp_core` package
  (golden outputs unchanged: G-code byte-identical, arrays within 4e-15).
  - Typed, frozen pydantic v2 config (`ProcessConfig` and sections) replaces
    the flat `sys_config` dict; validators carry the legacy error messages.
    `from_legacy_dict` converts old dicts.
  - `e_const` is computed by the core, no longer by the sidebar.
  - `st.cache_data` replaced by `functools.lru_cache` (copies returned).
  - Speed planner vectorised (≈7× faster); accel/decel scans kept exact.
  - Machine bed moved to a `MachineProfile` JSON (same 180 × 220 × 200 mm,
    1 mm margin default).
  - Bed auto-positioning is returned as a note instead of the core calling
    `st.info`.
  - Root modules `geometry.py`, `kinematics.py`, `specialty.py`,
    `validation.py`, `gcode.py`, `machine.py` removed; the Streamlit app is a
    thin front end over the core.
- Phase 0: git repository, `pyproject.toml` (ruff, pytest, hypothesis
  config), `requirements.lock` pinned for Python 3.12, CI workflow for
  `windows-latest`, 27 golden characterization cases, `docs/ARCHITECTURE.md`,
  `docs/SPEC_current.md`. Lint-only code fixes (import order, explicit
  `zip(strict=False)`, one statement per line); no behaviour change.
