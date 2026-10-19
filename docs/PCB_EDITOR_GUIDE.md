# Designing a structural PCB, step by step

A *structural PCB* is a carbon-fibre board that is also a circuit board: the
fibre makes it strong, and conductive ink printed on top makes the wires.
You draw it in the **Structural PCB (DIW)** tab. Start in **Easy** mode (top
left); switch to **Advanced** only when you need a tool or setting Easy hides.

## The 6 steps (Easy mode)

1. **Board.** Type the width and height, keep *Rounded corners* ticked (the
   fibre can't follow sharp corners), press **Make board**.
2. **Holes.** Press **Add 4 corner holes** for M3 screws, or **Place a hole…**
   and click where one goes. The fibre automatically steers round holes.
3. **Parts.** Drag a part from the list (LED, resistor, connector, sensor…)
   and drop it on the board. Or click it in the list, then click the board.
4. **Wires.** Pick a width (*Normal* is fine; *Wide* for power), press
   **Draw wire**, click on a pad, click wherever the wire should bend, and
   double-click on the other pad. A wire takes its name (net) from the pad it
   starts on, so the checker knows which wires belong together.
5. **Move & rotate.** Click anything to select it (it turns blue). Drag to
   move it, press **R** to turn it 90° (Shift+R the other way), **Delete** to
   remove it, **Ctrl+Z** to undo. A part's pads always move together.
6. **Check & print.** The *Checks* panel says in plain words whether anything
   touches or is too close to an edge or hole. Fix anything red. Choose what
   to print and press **Slice board**:
   - *Structural*: the fibre board and the electronics on top.
   - *Fibre board only*: just the board (add the electronics later).
   - *Electronics only*: just the ink, on a part you already have.

The 3D view then shows the fibre (grey core in amber resin) and the ink
(insulation green, conductive ink silver). Save the G-code from the toolbar.

## Faster ways

- **Let it draw the wires.** Give pads the same net (draw one wire, import a
  netlist, or type a net in Advanced): dashed lines show what still needs
  connecting. Press **Auto-route** and the wires are drawn for you, keeping
  clear of other wires, holes and the edge. Anything it cannot reach is named
  under the canvas.
- **Select many.** Shift+click adds to the selection; drag on an empty part of
  the board to select everything inside the rectangle; Ctrl+A selects all.
  Move, rotate and delete then act on all of them. Ctrl+C / Ctrl+V copy and
  paste (copied parts get new names), Ctrl+D duplicates.
- **Change a shape.** Click a wire, an area or the board edge: white squares
  appear on its corners. Drag one to move it, double-click an edge to add a
  corner, right-click a corner to remove it.
- **Import.** *Import / export* reads a board outline (and holes) from a DXF
  drawn in any CAD program, or a netlist (`ref,pin,net` lines or
  `NET: R1.2 D1.1`) to tell which pads connect. Export DXF sends the outline
  and wires back to CAD.
- **Inks.** Pick your ink in step 6 (silver, nanoparticle silver, carbon,
  liquid metal, copper): line width, speed and resistance follow.
- **Electronics inside the board.** Print mode *Embedded* prints part of the
  fibre, then the electronics (insulated below and above), then the rest of
  the fibre on top, leaving a window over every pad. The printer pauses twice:
  to cut the fibre and mount the ink head, and to re-thread the fibre.
- **Report.** The main toolbar's *Report…* saves a page with the print time,
  materials, every check, coverage per layer and each wire's resistance.

## Good to know

- **Carbon fibre conducts electricity.** The slicer prints an insulating layer
  under every wire for you; keep it on (Advanced > *Insulate under the ink*).
- **Keep wires apart.** About 0.5 mm between different wires, 1 mm from the
  board edge and from holes; the checker warns you otherwise.
- **Resistance.** Each wire's length and approximate resistance are listed
  under *Checks*. Wider or more ink layers = lower resistance.
- **Sensors are just wires.** The strain gauge, heater, touch electrodes and
  coil in the parts list are wire patterns; drag them on like any part.
- **Save your design** with *Save…* (`.spcb.json`) and open it again later.

## Advanced mode

The left panel is grouped:

- **Tools**: Select / move, Square board, Rounded board, Free-shape board,
  Hole, Wire, Pad, Zone, Part. The highlighted button is the active tool; the
  line under the canvas says what to do next.
- **New item settings**: grid, wire width, net name (empty = automatic), hole
  diameter, pad size and shape, zone kind (filled ink / extra insulation / no
  ink), which part and at what angle, and its name.
- **Sensors**: strain gauge, interdigitated electrodes and coil with your own
  dimensions.
- **Selection**: rotate 90° either way, delete.
- **Print**: what to print, and Slice board.

On the right, *Checks* lists problems and each wire's resistance, *How to*
repeats these steps, and *Ink settings* holds the full ink process (needle,
line width, layer counts, speeds, valve commands and delays or syringe
E-steps, tool command and head offset, resistivity, surface height). Hover
over any setting to see what it does.

## What the slicer guarantees

- Ink is never printed off the board or over a hole, even if you place a wire
  there (the checker tells you how much was left out and why).
- With insulation on, every bit of conductive ink sits on insulation.
- The fibre steers round every hole; no fibre crosses a hole.
