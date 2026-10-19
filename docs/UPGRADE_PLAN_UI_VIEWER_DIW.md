# Upgrade plan: UI, viewer, STL robustness, DIW structural electronics

Request (2026-10-18): improve the UI and grey out options that do not apply to
the selected shape; upgrade the 3D viewer with current techniques; add DIW
(direct ink writing) slicing for structural electronics, with an editor for
custom structural-PCB shapes; make STL slicing for continuous fibre robust.

Rules that stay in force everywhere (CLAUDE.md): one continuous fibre stroke per
part, r_min enforced in geometry + `enforce_min_radius` + validation, the
trajectory `list[dict]` shape, `cfrp_core` free of Qt, golden outputs changed
only deliberately. New behaviour is opt-in unless it fixes a defect.

DIW is a second process with its own rules: ink can stop and start, so DIW paths
may travel (Z-hop, dispense off) between features. It never shares a stroke with
the fibre, and it runs only after the laminate is finished (embedding between
fibre layers would mean cutting the fibre).

---

## Status (2026-10-19)

| Phase | State | Commit |
|---|---|---|
| 29 STL robustness | **done**: Pass 12 → 24, crossings 7970 → 771, curvature points 2.51 M → 1.28 M; Fill Solid coverage 61 % → 79 % | `062324b` |
| 30 Option states + controls panel | **done**: `capabilities.py` + rebuilt panel (greyed rows/patterns with reasons, sections, units, tooltips, search) | `7299d7f` |
| 30 remainder | **done**: toolbar (Slice, Auto-slice, Open STL, Save G-code, CSV, Calibration), issue list with click-to-layer, new theme | `0b99945` |
| 31 3D viewer | **done**: shaded tubes in GPU buffers, range rendering, 8 colour modes, playback, picking, ortho, PNG export | `0b99945` |
| 31 follow-ups | **left**: translucent STL mesh overlay, building the mesh in the worker thread for very large parts | — |
| 32a Custom Outline + holes | **done**: shape, rings that bend round holes (board 17 % → 88 %), Corner-Step no longer runs over holes | `9c21c19` |
| 32b DIW core | **done**: design model, footprints, sensors, DIW slicer, DRC + resistance, DIW G-code, combined board slicing | `c2cf929` |
| 33 Structural PCB editor | **done**: editor tab, tools, footprints, sensor generators, undo/redo, live DRC, DIW settings, Slice board → fibre + ink in one view and one G-code | (this commit) |

| 34 Audit + first layer + modes | **done**: solid first layer (choosable pattern, coverage target), print modes (structural / fibre / electronics), STL repair + machine + all DIW settings exposed, plain-language help everywhere, fibre/resin/transition/end colours; full audit clean | (this commit) |

| 35 Easy/Advanced editor | **done**: easy 6-step mode, drag-drop parts, rotate, true bead width | `1694613` |
| 36 Deep PCB audit + Advanced panel | **done**: ink clipped to the board, insulation under all ink, 200 random boards clean; grouped Advanced panel, tabs, fixed widths | (this commit) |

| 37 Suggested upgrades | **done**: 1 vertex editing, 2 multi-select/copy/paste, 3 auto-router + connection lines, 4 DXF/netlist import + DXF export, 5 embedded electronics, 6 ink/insulation presets + UV cure, 7 flow start/stop compensation, 9 part overlay + measure, 10 HTML report. Not done: 8 (extra reinforcing loops / Fermat round holes), Gerber import, align/distribute, a DIW flow calibration coupon | (this commit) |

### Suggested upgrades (prioritised; Phase 37 did all but 8)
1. **Vertex editing**: drag outline corners and wire bends; insert/delete a bend.
2. **Multi-select and copy/paste** (rubber-band selection, Ctrl+C/V, align/distribute).
3. **Auto-router** for wires (grid A* around pads/holes with clearance), plus
   "connect these pads" ratsnest lines from net names.
4. **Netlist / schematic import** (KiCad netlist or simple CSV) to place parts and
   show which pads must connect; **Gerber/DXF import** of outlines and copper.
5. **Embedded electronics between fibre layers**: plan a deliberate fibre cut and
   restart (the one-stroke rule is per segment), so traces can sit inside the
   laminate; electrical vias through the laminate.
6. **Ink material presets** (silver, carbon, liquid metal, UV dielectric) with
   resistivity, bead width, speeds and cure; UV cure step between DIW layers.
7. **Start/stop flow compensation** for DIW (pre-pressure and retract distances)
   measured with a calibration coupon, like the fibre's pressure advance.
8. **Fibre steering around holes** with reinforcing loops (tailored fibre
   placement) and Fermat spirals that handle holes.
9. **Viewer**: translucent board/STL overlay, section view, measure tool,
   build the mesh in the worker thread for very large parts.
10. **Print simulation report** (PDF/HTML): time, materials, resistances, DRC,
    coverage per layer, for sharing with a team.

### The 28 basic upgrades (asked for after Phase 37)

| # | Upgrade | Status |
|---|---------|--------|
| 1 | Seam stagger / interlock for flat parts | open |
| 2 | Inter-layer checks (fibre over a hole, Z down, climb over a hole) | done (Phase 38) |
| 3 | Ramp / entry on holed boards crossing a hole | done (Phase 38): entry routes stay on the part |
| 4 | Solid bottom / top skins, per-layer plan | done (Phase 38) |
| 5 | Layup meaningful for spirals | open |
| 6 | Thickness and Z check | done (Phase 38): thickness note, Z-Offset note, Z-down error |
| 7 | Plain tight-bend summary / fix hints | done (Phase 38): "How to fix" per message |
| 8 | Fermat spiral around holes | open |
| 9 | Multi-piece slices in one stroke with flagged bridges | open |
| 10 | Mesh-problem markers in the 3D view | open |
| 11 | Adaptive layer height for STL | open |
| 12 | Ink over an insulation edge rule | open |
| 13 | Crossovers (bridges / wire levels) and vias | open |
| 14 | Current capacity per trace | open |
| 15 | Part orientation checks, pin-1 marker | open |
| 16 | Ink drying wait and ordering | open |
| 17 | Bead spread factor | open |
| 18 | Split layers.py | done (Phase 38) |
| 19 | No private cross-module imports | done (Phase 38) |
| 20 | Retire Streamlit | done (Phase 38): `legacy/streamlit/` |
| 21 | Typed trajectory | done (Phase 38): `TrajectoryPoint`, `check_trajectory` |
| 22 | Hypothesis property tests | done (Phase 38) |
| 23 | Audits in CI | done (Phase 38) |
| 24 | Speed: viewer mesh in the worker thread | open |
| 25 | Settings undo, "ignored keys" on load | open |
| 26 | One project file (.cfrpproj) | open |
| 27 | Named profiles (machine, fibre/resin, ink) | open |
| 28 | Highlight the invalid field | open |

### Other known limits
- Fermat spiral on holed outlines still stops at the first hole (caution shown) -- item 8.
- A zigzag round a hole near a 45° edge can keep a crossing (the U-turns reach
  ~0.2 mm past the edge corridor); zigzag layers on holed parts can cross the
  climb from a previous layer that ended mid-part (warnings).
- Embedding electronics between fibre layers needs two strokes (done as the
  `embedded` print mode with a pause, not inside one stroke).

Still open from Phase 29 (not defects, known limits): zigzag curvature
warnings are inherent while Track Gap < 2·r_min (U-turn radius gap/2); the
remaining 771 crossings are mostly ramps between layers on small shrinking
slices (cone/pyramid) and multi-island bridges; steep ramps on cone/pyramid
zigzags.

---

## Phase 29: STL slicing robustness (core)

Baseline: 25 test meshes × 5 infills × 2 island modes (250 runs).

| Defect found | Cause | Fix |
|---|---|---|
| Archimedean spiral drew a teardrop loop across its rings (sliced cylinder layers 21/61, tube, vase) | Clipper's invalid-ring repair (`buffer(0)`) returns the ring clockwise; blending a CW ring into CCW neighbours reverses the pairing | Orient repaired rings counter-clockwise like Clipper's output (`_clipper_to_polys`) |
| Zigzag patterns cross themselves on ring-shaped slices (pen holder 397, tube 586 crossings) | Boustrophedon runs are chained greedily nearest-first; connectors cut through later runs | Search the run order/orientation for the fewest crossings (exact for few runs, greedy with look-ahead beyond), route connectors in the margin between U-turns and outline |
| Solid slices only 35–60 % covered | Tracks Per Layer (7) also caps STL slices | Opt-in **Fill Solid** (`fill_solid`): track count per slice = as many as fit |
| Steep layer ramps on shrinking slices (cone/pyramid zigzag) | Next layer starts far inside the previous end | Measured and fixed in this phase |

Acceptance: suite re-run; no spiral crossings; zigzag crossings reduced; goldens
unchanged except where a defect fix touches them (listed in CHANGELOG).

## Phase 30: option states and UI

`cfrp_core/capabilities.py` (UI-free, unit-tested) is the single source of truth
for which settings apply:

- `infill_states(params)` → per pattern: enabled / disabled + reason, or a caution
  (e.g. Fermat on a revolve shell with gap < 2·r_min is replaced by Archimedean).
- `field_states(params)` → per setting key: enabled / disabled + reason.
  Examples from the code: layup is ignored by spiral patterns and by the fixed
  ±45° hatch; Tracks Per Layer is ignored by zigzags and by Fill Solid; seam trim
  and transition angle only matter with layer transitions (flat with >1 layer,
  STL); revolve shells climb helically (no seam ramps); the Mushroom runs at fixed
  speeds; seam stagger applies to round shells only; the fixed pressure advance is
  overridden by a PA table; Print Outer Boundary does nothing with Corner-Step
  (its ring 0 is the boundary).

GUI:
- Settings that do not apply are **greyed out** (not hidden) with the reason as
  tooltip; inapplicable infill patterns are greyed in the drop-down.
- Collapsible sections, unit suffixes, a tooltip on every setting, a settings
  search box, toolbar (Slice, Auto-slice, Open STL, Save G-code, PCB editor).
- Validation shown as an issue list; clicking an issue that names a layer shows
  that layer in the viewer.

## Phase 31: 3D viewer

Current viewer: one GL line strip, every n-th point (distorts curves), speed
colour only, single-layer slider, rebuilt on every slider move.

New viewer (same approach as libvgcode / PrusaSlicer 2.8, Bambu/Orca preview):
- Toolpaths drawn as **shaded tubes** with the bead's elliptical cross-section
  (track gap × layer height), built once into GPU buffers; custom GLSL
  Blinn-Phong shading (key + fill + headlight, specular), 4× MSAA.
- **Range rendering**: the stroke is ordered by layer, so a layer range or a
  move range is a contiguous index range. Sliders change the draw range only;
  no geometry is rebuilt. Layers below the range can be shown dimmed.
- **Colour modes**: feature (fibre / ramp / anchor / bridge / pump-floored /
  DIW conductor / dielectric / travel), speed, layer, height, flow, **bend radius**
  (red where below r_min), **fibre direction** (orientation map), tool.
- Dual-handle layer range slider, move scrubber with play/step and a nozzle
  marker, point read-out (layer, XYZ, speed, bend radius).
- Shape-preserving decimation (turning/length based) instead of every n-th point.
- Perspective/orthographic, view presets with keys 0–6, fit to part, screenshot.
- Fallback to the line renderer if the shader cannot compile.

## Phase 32: custom outlines + DIW core

- Shape **"Custom Outline"**: `GeometryConfig.custom_outline` / `custom_holes`
  (mm, polygon + holes) sliced like any flat laminate; holes are respected by
  every pattern.
- `cfrp_core/diw/`:
  - `design.py`: `BoardDesign` (outline, holes, traces with width and net, pads,
    conductor/dielectric zones), JSON save/load, parametric generators for
    structural-electronics features: meander (strain gauge / heater),
    interdigitated electrodes, spiral coil; a small footprint library.
  - `config.py`: `DiwConfig`: nozzle, line width, layer height, speeds, Z-hop,
    dispense mode (pneumatic on/off codes with delays, or volumetric E),
    dielectric margin and layers, conductor layers, encapsulation, tool offset,
    ink resistivity.
  - `slicer.py`: conductor regions = buffered traces ∪ pads ∪ zones per net;
    narrow traces as centreline passes, wide areas as perimeter + concentric
    fill; dielectric = conductors grown by the margin, clipped to the board,
    raster filled (alternating ±90°); nearest-neighbour + 2-opt ordering;
    travel with Z-hop.
  - `validation.py`: DRC (min width, net-to-net clearance → short risk, edge
    clearance, over holes), carbon fibre is conductive → conductors need the
    dielectric layer, per-trace resistance estimate, ink volume and time.
  - `gcode.py`: DIW section (tool select, offsets, dispense codes, dwell).
- `cfrp_core/board.py`: `slice_structural_board(design, process, diw, machine)`
  = fibre laminate of the outline + DIW on top, one bed shift for both, combined
  or separate G-code.

## Phase 33: structural PCB editor (GUI)

Tab "Structural PCB": QGraphicsView canvas in mm (Y up), grid + snap, zoom/pan,
cursor read-out; tools: select/move, board outline (rectangle, rounded
rectangle, circle, polygon), hole, trace (polyline + width + net), pad,
zone, footprints and parametric sensors; properties panel; undo/redo; DRC
markers on the canvas; New/Open/Save (`.spcb.json`), example board.
"Slice board" sends the outline to the fibre slicer (Custom Outline) and runs
DIW; the 3D view shows fibre and ink colour-coded by tool.

---

Each phase: tests (core unit tests, offscreen Qt smoke tests), CHANGELOG entry,
separate commit.
