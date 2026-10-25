# Upgrade plan: Phases 43–49

Request (2026-10-20), in short:

1. **Fibre.** Fill the areas round holes and inside the part with combinations
   of patterns instead of leaving cavities; several patterns in one layer;
   stronger fibre round holes; better fibre placement; every fibre path keeps
   the minimum bend radius, zigzag turns included.
2. **Materials and curing.** A separate tool and nozzle for each material;
   conductive ink cured with IR and the heated bed, insulation and structural
   material with UV; printing, material changes and curing coordinated so
   nothing overheats, everything cures and materials don't interfere.
3. **UI.** Three separate sections: Fibre Printing, PCB Printing (insulation and
   conductive ink) and Structural PCB (fibre + insulation + conductive ink in
   one part). Each shows only its own settings in one consistent layout;
   settings that can't apply are greyed out and disabled.
4. **STL.** Different patterns in one slice, chosen by geometry, loads, hole
   reinforcement and material limits, taking fibre direction, tool access,
   material compatibility, curing and the bend radius into account.
5. **Assessment.** Further upgrades for slicing accuracy, print reliability,
   process coordination, usability, and later sensor monitoring and closed-loop
   control, with the machine as it is.

## Status

| Phase | Topic | State |
|---|---|---|
| 43 | Bend radius everywhere | done (see CHANGELOG) |
| 44 | Mixed patterns in one layer, wrapped holes, cavity check | done: cavity check (corner gaps named), Fill Pockets, layer recipe (edge rings, hole wraps, bolt and drill-after holes; opt-in), property test, every bolt hole wrapped (zigzag cores), Auto weighing the recipe, holes marked on the canvas. Not done: lines into leftover slivers (see Phase 44 below, restated) |
| 45 | STL: mixed patterns per slice | in progress: the layer recipe on one-island STL slices (edge rings, core; narrow walls get rings), bores wrapped in turn, UV dose speed cap, height ranges and load direction, overhangs (slowed, reported, zigzags across) done (see CHANGELOG); open: material limits, property test |
| 46 | Three workspaces | planned |
| 47 | A tool and a cure per material, coordinated | planned |
| 48 | Accuracy and reliability | planned |
| 49 | Sensor-ready output | planned |

---

## 1. Where things stand

### Your printers (answered 2026-10-20)

| Printer | Heads | Curing | Workspace |
|---|---|---|---|
| **Fibre printer** | continuous-fibre head only | UV LED on the head (as laid) | Fibre Printing |
| **PCB printer** | two heads: conductive ink + insulation | to confirm (H8) | PCB Printing |
| **Integrated printer** | rotary tool dock: 3 nozzles + 1 spare (fibre, insulation, conductive ink, spare) | IR bulb, UV, heated bed | Structural PCB |

### The machine, as the slicer knows it today

The slicer has one machine profile for everything:

- 3-axis gantry, 180 × 220 × 200 mm (default machine profile), Marlin-style G-code.
- Fibre head: 0.8 mm needle. The resin pump (E axis) pulls the tow through, and
  the UV LED on the head (`M1995 P47 S…`) cures the resin as it is laid.
- Ink head: selected with `T1`; one 0.41 mm nozzle; a pneumatic valve
  (`M42 P4 S255` / `S0`) or a syringe on E. The program pauses (`M0`) to cut the
  tow and mount it.
- No heated-bed, IR or sensor commands anywhere in the output.

### What the code does now (measured on commit `a5a2ffb`, GUI defaults)

| Area | Finding | Evidence |
|---|---|---|
| Bend radius | Every zigzag U-turn is tighter than r_min: its radius is half the line gap (0.6 mm against 3 mm). | 1,355–3,080 tight points per part (3 test parts, 4 layers) for Oriented Serpentine and 45° Hatch |
| | The Fermat spiral turns from its inward to its outward pass in a U-turn of about 0.56 mm. | ~15 tight points per layer on every shape |
| | Layer-change climbs are not checked by the bend audit (it skips `is_transition` points). | 0–9 tight points per part on climbs |
| | Archimedean, Corner-Step and Auto keep the radius. | no tight points outside climbs |
| Cavities | The same spots stay empty in every layer, so they stack into cavities through the laminate. On a 60 × 40 mm plate with four M3 holes (Fill Solid, 4 layers): | 12 % of the part is empty in all 4 layers (5 % with Fill Centre); with zigzags, 25 % |
| | Round holes | 25 % of the 3 mm ring round each M3 hole is empty in all layers; half or more of the ring round a central 8 mm hole |
| | One pattern per layer (plus the optional wall ring and Fill Centre). | `geometry/laminate.py` |
| Tools | One ink head (`T1`, one valve, one nozzle) prints insulation, conductive ink and cover, with no stop to change material between them. | `diw/config.py`, `diw/gcode.py` |
| Curing | "UV cure between layers" turns the head's LED on and waits at one spot, so only the area under the LED gets light. | `diw/gcode.py` |
| | No bed heating or IR at all, and no temperature limits. | no heater commands in `src/` |
| Joining programs | The fibre and ink programs are joined by deleting lines that start with `M84` and `G0 X0 Y0`. | `board.combine_gcode` |
| Ink height | Ink starts at the highest fibre point. Where the top fibre layer left gaps (hollow centre, between lines) the surface is one layer lower, so the ink is laid in the air above it. | `board.py` |
| UI | **Slice board** (PCB tab) jumps to the Fibre tab and slices with that panel's fibre settings; *Electronics only* takes its bed position from the fibre panel's Start X/Y. | `main_window._slice_board`, `board.py` |
| | Ink settings is one list of about 48 rows that mixes materials, machine commands and checks. | `pcb_editor._build_diw_box` |
| | The greying rules for ink are written in the editor; the fibre ones are in the shared, tested rules (`capabilities.py`). | `pcb_editor._diw_states` |
| Sensors | Layer comments only: there is no machine-readable plan (expected fibre length, flow, UV, temperatures) to compare sensor readings with. | `gcode/writer.py` |

## 2. Limits the plan works within

- **One continuous fibre stroke per part.** Every pattern in a layer is joined
  into the same stroke; nothing is cut.
- **Wrapping a hole.** A fibre path that goes all the way round a hole shuts
  itself in: to get out it has to cross its own first lap. So full wraps are
  possible only where the stroke starts or ends, which is at a layer change.
  Everywhere else the fibre passes a hole in C-shaped part-wraps, as the rings
  that bend round holes do now.
- **Dense zigzags can't keep the bend radius.** A U-turn between neighbouring
  lines has a radius of half the line gap. Only rings and spirals can fill
  densely and keep the radius. Zigzag lines that keep it are 2 × r_min apart and
  spread over several layers (Phase 42).
- **Small holes.** The first ring round a hole can't be tighter than r_min. An
  M3 hole (1.6 mm radius) with r_min 3 mm keeps a 0.8 mm ring of resin only.
- **Ink can stop, start and travel; fibre can't.**
- **3 axes.** Ink goes only on surfaces the nozzle reaches from above.
- **Heat.** The cured fibre resin and the UV insulation have a maximum
  temperature. The conductive ink's heat cure must stay under it, for example
  by heating along the wires with IR instead of heating the whole board.
- The CLAUDE.md rules stay: r_min enforced in three places, the trajectory's
  shape, `cfrp_core` free of Qt, the sequential speed planner, golden outputs
  changed only on purpose. New behaviour is opt-in except where a decision below
  says otherwise (D1). Existing projects print the same unless an option is
  turned on.

## 3. Phases

| # | You get | Answers | Size |
|---|---|---|---|
| 43 | No fibre bend under r_min anywhere (strict mode) | R5 | M |
| 44 | Mixed patterns in one layer, full wraps round holes, cavity check (flat parts) | R1–R4 | L |
| 45 | STL: patterns per slice by shape, load direction, bores, overhangs, UV cure | R12, R13 | L |
| 46 | Three workspaces, one layout, greying for every setting | R9–R11 | L |
| 47 | Separate tools; UV, IR + bed curing; process rules and timeline | R6–R8, R13 | L |
| 48 | Accuracy and reliability upgrades | R14 | M |
| 49 | Sensor-ready output (monitoring, closed-loop hooks) | R14 | M |

Order: fibre first (43–45), because it doesn't depend on the hardware questions
in section 6. Then the workspaces (46), so that the tool and curing settings of
47 are built straight into the right place. Phases 48–49 build on 47. Phase 46
can move up to right after 43 if you want the new UI sooner.

### Phase 43: Bend radius everywhere

**Goal.** No fibre move bends tighter than r_min. Where nothing fits, the spot is
left empty and reported, instead of printing a slowed tight turn.

- **Strict bend radius** setting (Pattern & Layers), on for new projects (D1).
  Off = today's behaviour (tight turns slowed and warned).
- **Zigzags:** strict mode uses the Phase 42 spacing (lines 2 × r_min apart,
  shifted each layer). The info line says how many layers fill the area once.
- **Fill Centre:** the back-and-forth lines, which turn tighter than r_min,
  become one line along the middle of the hollow, reached by a gentle lane
  change. It moves across the hollow from layer to layer, so a few layers fill
  it.
- **Fermat:** the turn from the inward to the outward pass becomes a loop at
  r_min into the unfilled middle. Where the middle is too small, the spiral
  turns one ring earlier.
- **Joins:** the links between zigzag runs, detours round holes, entry routes,
  island bridges and wall-to-infill lane changes are rounded to r_min. A join
  that can't be rounded without leaving the part or crossing fibre is rejected
  and another order is tried.
- **Layer climbs** are checked too, and follow the path's curve.
- **Audit:** in strict mode a bend under r_min is an error that names the layer
  and what bent (turn, join or climb).
- **Auto (Best Fit)** rejects candidates with tight bends.
- **Coverage note:** "left empty because of the bend radius: X mm² on layer N".

*Trade-off:* a strict zigzag layer covers about a fifth of the area (1.2 mm gap,
3 mm radius). Solid parts use rings or the Phase 44 recipes, and Auto picks
them by itself.

*Code:* `geometry/serpentine.py`, `geometry/fermat.py`, `geometry/_common.py`,
`layers/common.py`, `layers/auto.py`, `validation.py`; config, capabilities, GUI.

*Done when:* the full validation sweep with strict mode on has no bend under
0.98 × r_min (climbs included), no new crossings and no fibre over a hole. A
property test on random outlines checks the same, and the goldens are unchanged.

### Phase 44: Mixed patterns in one layer, wrapped holes, cavity check

**Goal.** Each layer combines patterns so that edges, holes and the inside are
filled, joined into one stroke. Spots left empty in every layer are found and
filled wherever the bend radius allows.

New **Layer recipe** in Pattern & Layers:

| Setting | What it does |
|---|---|
| Edge rings (0–5) | Rings along the outline (today's Print Outer Boundary is 1). Next to a hole they bend round its rings. |
| Hole rings (0–5) | Rings round each hole. Per hole, on the canvas: ring count and "bolt hole" priority. |
| Core | The pattern for the rest (the current list). Zigzag lines run at the layer's layup angle. |
| Wrap holes at layer changes | A layer ends by spiralling into a hole's rings, and the next layer starts by spiralling out of them, so that hole gets full wraps in two layers. Holes take turns, bolt holes first. |
| Fill pockets | Leftover spots (hollow centre, slivers between zones) get extended lines or a centre line, wherever a way in keeps r_min. |

How a layer is joined (an inward layer): the edge rings spiral in → core lines,
which stop at the hole rings (the links between line runs pass round holes as
part-wraps) → the core ends next to the chosen hole → spiral into its rings →
climb there → the next layer spirals out of the same hole → core → edge rings
outwards → climb at the edge.

Rules for joining:
- A pattern that the stroke passes through must start and end on its zone's
  edge: zigzag or Fermat.
- Archimedean and Corner-Step spirals end in the middle, so they can only come
  first or last in a layer. With a spiral core, the only hole it can wrap is one
  where the spiral ends (a centre hole).
- A zone that can't be joined (keeping r_min, never crossing fibre) is left out
  on that layer and reported.

The other new features:
- **Cavity check.** The layers' coverage maps are compared. Spots empty in N
  layers in a row (default: all) are listed with size and place ("14 mm² next
  to hole 3, layers 1–8") and shown in the 3D view.
- **Auto (Best Fit)** tries recipes (edge rings 0–2, core pattern and angle,
  wraps on or off) and per-layer shifts (seam, centre line, line offset) that
  cover what the layers below left empty.
- **Drill-after holes** (per hole, off by default, D3). Holes smaller than
  2 × r_min can't be wrapped closely. Marked holes are printed over and listed in
  the report to be drilled afterwards; drilling cuts fibres.

*Code:* new `geometry/zones.py` (split a layer into zones, order and join them),
`layers/flat.py`, `layers/auto.py`, `coverage.py` (cavity map), `validation.py`,
the viewer, the canvas (per-hole settings).

*Done when:*
- On the 60 × 40 plate with four M3 holes (Fill Solid, 4 layers), the part of
  the plate empty in every layer drops from 12 % to under 4 % with Strict Bend
  Radius off. The rest is the rings that r_min forbids, listed per hole.
- *Restated 2026-10-20 (option 1: strict stays on by default).* With Strict Bend
  Radius on (r_min 3 mm, gap 1.2 mm) that target cannot be met: the rings' hollow
  centre (about 2 × r_min wide) takes one line per layer, reachable only going
  forwards, and no ring bends round an M3 hole closer than r_min. With strict on
  the cavity check must list every spot left empty in all layers, and every spot
  it lists must lie in the ring centre or next to a hole; wrapped bolt holes
  must have no such spot within their hole rings.
- With at least twice as many layers as holes, every bolt hole gets full wraps.
- *Restated 2026-10-25 (lines into slivers).* Measured on that plate (strict on,
  4 layers, Archimedean): 427 mm² empty next to the holes, 159 mm² in the ring
  centre, 17 mm² in the corners (now named "in a corner") and about 15 mm² of
  small spots between holes and corners. The corners and the ring centre take no
  fibre that keeps the bend radius, and the band round the two holes on one side
  (336 mm²) is left by the spiral generators themselves: their inner rings pass
  inside those holes on every layer. Filling it means changing the Archimedean
  and Corner-Step rings round holes, which changes golden outputs, so it waits
  for a decision. Auto with Fill Pockets and Edge Rings does not fill it either
  (recipe layers cover less there).
- No crossings, no bend under r_min and no fibre over a hole.
- Sweep cases for each recipe and a property test on random holed outlines pass.

### Phase 45: STL, mixed patterns per slice

**Goal.** Each STL slice gets the mix that suits its shape and loads, using the
Phase 44 recipes per slice and per island.

- **By geometry.** Walls narrower than 2 × r_min + gap get edge rings only; wide
  areas get the core at the layer's fibre angle. Bores (holes through many
  slices) get hole rings, and the wraps at layer changes take turns between the
  bores. The links between islands keep r_min.
- **By loads.** *Load direction* (an XY angle, or "all directions") sets the core
  angle on the layers that carry the load and orders the layup. *Height ranges*
  give a different recipe, layup and solid skins from one height to another (a
  stiff flange at the bottom, for example).
- **Support and tool access.** Areas of a slice with nothing under them
  (overhangs) are found. Fibre crosses them only in straight runs anchored on
  fibre at both ends, at the overhang speed (as the Mushroom does), at the angle
  that makes the spans shortest. Spans that are too long are left out and
  reported. Spots too narrow for the needle are flagged.
- **Curing in the path.** The resin cures under the head's LED as it is laid. A
  speed cap keeps the UV dose per mm above what the resin needs (settings:
  required dose, LED irradiance and spot length; off until set).
- **Material limits.** The recipe follows the material profile's bend radius,
  track gap and cure speed: a stiffer tow with a larger radius gets rings
  instead of lines.
- **Run time:** the per-slice cache is extended to recipes. Target: the slowest
  part in the test set no more than twice as slow as now.

*Code:* `layers/stl.py`, `layers/islands.py`, `geometry/zones.py`,
`geometry/mesh_slicer.py` (support map), a speed cap applied before the speed
planner (its loops untouched); config, capabilities, GUI.

*Done when:* the 25 test meshes have no new Fails or crossings and more
coverage per slice. New test meshes for thin walls, bores and overhangs pass,
as does a property test on random extruded shapes with holes.

### Phase 46: Three workspaces

**Goal.** Three separate workspaces with the same layout. Each shows only its own
settings. Settings that do nothing are greyed out and disabled, with the reason.

| Workspace | For | Settings |
|---|---|---|
| **Fibre Printing** | Carbon-fibre parts: shapes, STL, outlines | Part (shape, STL, or an outline drawn on the canvas with only the outline and hole tools), Pattern & Layers (+ recipe), Material, Speed & Flow, Seams. No ink. |
| **PCB Printing** | Insulation and conductive ink on a part you already have (a carbon part, glass, FR4 …) | Substrate (does it conduct, surface height, maximum temperature, where it sits on the bed), design canvas, ink layers, materials and curing, ink checks (spacing, resistance, current). No fibre. |
| **Structural PCB** | Fibre board + insulation + conductive ink in one print | Design canvas, Laminate (the fibre settings that matter for a board: recipe, layers, layup, hole rings, gap, bend radius; the rest under "All fibre settings…"), where the electronics go (on top, or after fibre layer N), ink layers, materials and curing, process steps. |

- **One printer per workspace:** Fibre Printing → the fibre printer, PCB
  Printing → the PCB printer, Structural PCB → the integrated printer. Each
  printer has its own machine profile (bed, heads, cure devices, G-code start and
  end), picked by the workspace, so a program is always written for the right
  machine.
- **Shared by all three:** a *Machine & Tools* window (one profile per printer,
  with the tools and cure devices from Phase 47) and a *Materials* library.
- **Same layout everywhere:**
  - settings on the left, in the same section order;
  - the design or 3D view in the middle (a Design / Preview switch in the PCB
    workspaces);
  - checks and results on the right;
  - the same toolbar (Slice, Save G-code, Report).
- **One 3D view** moves into whichever workspace is active, so slicing a board
  no longer jumps to another tab.
- **Greying:** every rule lives in `cfrp_core/capabilities.py` (no UI code,
  tested):
  - the fibre rules already there;
  - the ink rules, moved out of the editor;
  - new workspace rules: surface height only in PCB Printing; "after fibre
    layer N" only for embedded; valve or syringe settings; heat-cure settings
    only for materials cured by heat; tool settings only for tools in use; layer
    counts greyed while the Layers tab sets each layer.
- **Ink settings in sections** (Materials, Layers, Spacing & checks, Flow
  start/stop, Curing). Machine commands (valves, tool, UV, offsets) move to
  Machine & Tools.
- **Projects** remember their workspace, and older ones open in the right place:
  - structural and embedded → Structural PCB;
  - electronics only → PCB Printing;
  - fibre board only → Structural PCB, "laminate only".
- **Easy / Advanced** modes stay in both PCB workspaces. Each workspace opens with
  a ready example (a bracket with bolt holes; a strain gauge on a plate; a board
  with an LED).

*Code:* `gui/main_window.py`, `gui/pcb_editor.py` (split into canvas and ink
panel), `gui/controls_panel.py`, new `gui/workspaces.py` and
`gui/machine_dialog.py`, `cfrp_core/capabilities.py`, `cfrp_core/project.py`.

*Done when:* offscreen Qt tests pass for each workspace (only its sections,
every greying rule, slicing in each, old and new project files), as do
capability tests for every new rule.

### Phase 47: A tool and a cure per material, coordinated

**Goal.** Each material has its own tool, nozzle and cure. The slicer orders
printing, tool changes and curing so that nothing overheats, everything cures,
and materials don't interfere.

**Machine & Tools**
- **Tools:** T0 is the fibre head; T1 … Tn are ink heads. Each has:
  - its select command and X/Y/Z offset from the fibre needle;
  - its nozzle, and valve or syringe commands;
  - priming, a purge spot and a safe lift height.

  A tool is changed by command (several heads, or a tool changer) or by hand,
  at a pause with a clear message such as "Mount the silver ink head".
  - *Integrated printer:* the rotary dock changes tools by command: fibre,
    insulation, conductive ink, and the spare slot (for example the cover
    material). No manual pause, except cutting the tow if the fibre tool has no
    cutter (H9).
  - *PCB printer:* two heads, insulation and conductive ink, changed by command.
- **Cure devices:**
  - UV LED on the head: command, spot size, irradiance;
  - IR lamp: on the head (scanned) or fixed above the bed (flood); command;
    power;
  - heated bed: maximum temperature, heating and cooling rates; `M140`/`M190`
    by default.

  A device that isn't set up is never used, and a material that needs it gets
  an error.

**Materials library**
- fibre resin: UV-cured as it is laid; maximum temperature;
- insulation: UV, with its dose; maximum temperature;
- conductive ink: heat (bed and/or IR), temperature × time; maximum
  temperature; drying;
- cover: UV.

Each row of the Layers tab names its material. Its tool and cure follow from the
material and can be changed per row.

**Curing as real moves**
- *UV pass:* the LED sweeps the area printed on that layer, in lines one spot
  wide, with the nozzles lifted. Its speed gives the material's dose
  (dose = irradiance × spot length ÷ speed). With a flood lamp, today's "on,
  wait, off" stays.
- *Heat cure* (conductive ink): the bed heats to its temperature and waits until
  it is reached. The IR lamp then scans along the wires (or floods the board) for
  the material's time. The bed cools to a safe temperature before the next
  material.

**Coordination rules.** These are checked on every slice. Missing steps are
inserted where possible; otherwise the slice shows an error.
1. No printed material is heated above its maximum temperature (fibre resin,
   insulation, cover). The bed setpoint and the IR dose per area are checked
   against every material on the board.
2. Each layer is cured or dried before another material goes on it (no wet on
   wet).
3. The bed cools below the ink's printing temperature before dispensing (ink
   dries in a hot nozzle).
4. Before a tool change or a cure pass the head lifts above everything printed,
   and a parked tool must clear the part.
5. UV passes run with the nozzles of UV-curable inks lifted. A nozzle is purged
   after a long idle, UV or heat.
6. Conductive ink is heat-cured before the cover goes on, and before the upper
   fibre layers are laid (embedded).
7. Each material is used within its pot life from its first use (warning).
8. Operator steps (cutting the tow, swapping a head) are explicit pauses with
   messages.

Also in this phase:
- **G-code from a list of steps** (print, tool change, UV pass, heat cure, wait,
  pause), each from its template in Machine & Tools. This replaces joining
  programs by deleting lines.
- **Process timeline** in the PCB workspaces and in the report: every step with
  its start, length and temperatures. The print time includes heating, curing,
  cooling and pauses.
- **Ink on an STL part's flat top face** (tool access): pick a flat, upward face
  of an STL as the circuit area. The slicer checks that the ink head clears
  taller parts nearby and marks what it can't reach.
- **Existing projects:** one tool keeps printing everything until tools are set
  up, with a warning that the materials share a head.

*Code:* `machine.py` (tools, cure devices), new `cfrp_core/materials.py` and
`cfrp_core/process.py` (steps, rules, timeline), `diw/slicer.py`,
`diw/gcode.py`, `board.py`, `report.py`; GUI (Machine & Tools, Materials,
Layers tab).

*Done when:*
- Each rule has a passing and a failing test.
- In the G-code, every heater, lamp and valve that is turned on is turned off;
  everything is off at the end, and temperatures stay within limits.
- The PCB audit runs with multi-tool and cure setups and is clean.
- Fibre-only output is unchanged (goldens).

### Phase 48: Accuracy and reliability

| Upgrade | Fixes | Needs |
|---|---|---|
| **Laminate top map**: the ink height follows the real top of the fibre, with a warning where ink would bridge a dip. In Structural PCB the top fibre layer under the circuit is filled solid, centre included. | Ink laid in the air over dips | software |
| **G-code check**: the finished G-code is read back and checked: bounds per tool (offsets included), Z never below material already laid, every "on" has an "off", temperatures within limits, no dispensing during travel, every pause has a message. It runs after every slice and in CI. | Mistakes in the slicer or in your command templates, caught before the machine runs | software |
| **Calibration prints**: tool offsets (each ink head against the fibre needle, with crosses and verniers); a UV dose ladder (strips at rising dose); a heat-cure and resistivity coupon (test lines you measure, whose result goes into the material). | Offsets, dose and resistance from your machine instead of defaults | a multimeter |
| **Consumables**: fibre length against what's on the spool, ink per material against the syringe size, pot life against print time; a pre-print checklist of what to load in which head. | Running out mid-print | software |
| **Dry run**: the same program with no flow, UV or heat. | Checking motion, offsets and clearances | software |
| **Probe before ink**: a probe mesh (`G29`) over the board before the ink. | Warped laminates | a Z probe |
| **Safe end**: every program ends with UV, IR, bed and valves off. | Heaters left on | software |

*Done when:* the G-code check catches seeded mistakes (a missing "off", a wrong
offset, a Z drop) and passes on all audit outputs, and each coupon has a test.

### Phase 49: Sensor-ready output

- **Job file** (JSON next to the G-code): for every layer and path segment, the
  tool, material, expected fibre length, speed, flow, UV power, temperatures and
  cure dose, with ids that match comments in the G-code.
- **Event lines** (optional): `M118` at layer starts and ends, tool changes and
  cure steps, so a host program (OctoPrint, a Python script) can timestamp them.
- **Monitor (offline first):** load a sensor log (CSV: time + readings) with the
  job file and compare planned with measured:
  - fibre fed against expected (slip, break);
  - pump pressure against speed (checks the pump floor);
  - UV dose;
  - bed and IR temperatures against the schedule.

  A report flags anything off.
- **Closed-loop hooks:** the job file lists what a controller may change (feed
  override `M220`, flow `M221`, UV power) and the safe range per segment. It
  also marks checkpoints (`M400` + an event) where a host can check before going
  on. The live controller is later work, because it needs the sensors.
- **Sensors this prepares for** (most are add-ons): a fibre feed encoder, a pump
  pressure sensor, a UV radiometer, a thermocouple or IR thermometer at the
  part, a camera (layer photos against the planned coverage map), a Z probe.

*Done when:* the job file round-trips, and the monitor flags faults seeded in a
synthetic log (slip, cold cure, low UV). With event lines off, the G-code is
unchanged.

### Risks

- **44–45:** on crowded parts (holes close together) some zones can't be joined.
  They are skipped and reported. Recipes multiply Auto's candidates, so run time
  is measured every phase (cache, pruning).
- **46:** the biggest UI change so far. Every old project file must still open
  (tested).
- **47:** depends on the hardware answers. Commands are templates you can edit,
  and nothing heats until a device and a material ask for it.

## 4. Requirement coverage

| # | Requirement | Phase |
|---|---|---|
| R1 | Holes and internal regions filled with combinations of patterns, no cavities | 44, 45 |
| R2 | Several patterns in one layer for complete infill | 44, 45 |
| R3 | Stronger fibre round holes | 44 (hole rings, wraps at layer changes, drill-after), 45 (bores) |
| R4 | Better fibre placement | 44 (core at the layup angle, edge rings), 45 (load direction, height ranges) |
| R5 | Every fibre path keeps the bend radius, zigzags and tight turns included | 43 |
| R6 | A separate tool and nozzle for each material | 47 |
| R7 | Conductive ink cured by IR + bed; insulation and structural material by UV | 47 |
| R8 | Printing, material changes and curing coordinated (heat damage, under-curing, interference) | 47 (rules, steps, timeline), 48 (G-code check, coupons) |
| R9 | Three sections: Fibre, PCB, Structural PCB | 46 |
| R10 | Each section shows only its settings, in one consistent layout | 46 |
| R11 | Options that can't apply are greyed out and disabled | 46, 47 |
| R12 | STL: different strategies in one slice by geometry, loads, holes, material | 45 |
| R13 | STL: fibre direction, tool access, material compatibility, curing, bend radius | 45; 47 (ink on STL faces, materials); 43 |
| R14 | Further upgrades: accuracy, reliability, coordination, usability, sensors | 48, 49 (and the findings in section 1) |

## 5. Decisions

| # | Question | Recommendation |
|---|---|---|
| D1 | Strict bend radius on by default for new projects? | Yes. Saved projects and the golden tests keep today's behaviour. |
| D2 | Wrapping holes at layer changes puts those layers' climbs (a small bump) next to the holes. Is that acceptable? | Yes, for holes marked as bolt holes (a washer covers the bump). |
| D3 | Offer "drill after printing" for small holes? | Yes, per hole, off by default. |
| D4 | Workspaces as three tabs across the top, plus a shared Machine & Tools window? | Yes; it is closest to today's layout. |
| D5 | Phases in the order 43 → 49? | Yes, or 46 straight after 43 for the new UI sooner. |

## 6. Hardware facts needed for Phase 47

| # | Question | Assumed until answered |
|---|---|---|
| H1 | How many ink heads, and how are they changed? | **Answered:** three printers (fibre only; PCB with two heads, conductive + insulation; integrated with a rotary dock of 3 nozzles + 1 spare, IR bulb, UV and heated bed). See section 1. |
| H2 | UV for the ink on each printer: an LED on the head or a flood lamp? Spot size and irradiance (radiometer)? | Used as today ("on, wait, off") until spot data is entered |
| H3 | Integrated printer's IR bulb: on the dock / head or fixed above the bed? How is it switched, and at what power? | Fixed above the bed (flood), switched by a command you enter; nothing heats until it is set |
| H4 | Heated bed (integrated printer): maximum temperature? `M140`/`M190`? | `M140`/`M190`, at most 90 °C |
| H5 | Maximum temperature of the cured fibre resin and of the insulation; the conductive ink's cure (temperature × time, IR allowed?) | Not known: nothing is heated until they are entered |
| H6 | Is there a Z probe? | No (probe step off) |
| H7 | Does the firmware accept `M118`, `M400`, `G29`? | `M118` and `M400` yes (standard Marlin); `G29` only with a probe |
| H8 | PCB printer: how does it cure (UV, IR, heated bed, none)? | No cure step until set; a warning if a material needs one |
| H9 | Integrated printer: can the fibre tool cut the tow, and which dock slot holds which material (the T number for each)? | No cutter (pause to cut); T0 fibre, T1 insulation, T2 conductive ink, T3 spare |
| H10 | Bed size of each printer? | 180 × 220 × 200 mm for all three until entered |

## 7. How each phase is checked

Each phase is checked with:
- unit tests, property tests (hypothesis) and offscreen Qt tests;
- the validation sweep, multilayer audit and PCB audit, each extended with the
  phase's new options.

Golden outputs stay unchanged unless a decision says otherwise. The CHANGELOG
and the guides are updated, there is one commit per phase, and nothing is pushed.
