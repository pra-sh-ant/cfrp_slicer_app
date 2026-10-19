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
