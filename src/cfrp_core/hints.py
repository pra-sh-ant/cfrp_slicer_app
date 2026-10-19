"""Plain-language fixes for slicer and board-check messages.

`fix_hint(message)` returns one or two sentences telling a non-specialist
what the problem means for the part and which setting to change, or "" for
messages that already say so. Kept apart from the message text itself,
which the golden tests pin down.
"""
from __future__ import annotations

# (substring of the message, hint); first match wins.
_HINTS: tuple[tuple[str, str], ...] = (
    ("Curvature:", "The fibre bends tighter than it can there and may kink or fray. Use fewer Tracks Per "
                   "Layer, a bigger part, or a spiral pattern (gentler than zigzag U-turns); lower Min Bend "
                   "Radius only if your tow really bends that tight."),
    ("crosses itself", "The fibre runs over its own track on that layer: a bump and a weak spot. Try another "
                       "Infill Pattern or Tracks Per Layer, or turn Include Boundary off."),
    ("Fibre over a hole", "Tow laid over a hole sags and is cut when the hole is cleaned out. Give the hole more "
                          "room from the edge and other holes, or lower Track Gap / Min Bend Radius."),
    ("Z goes down", "The nozzle would press into fibre it already laid. This should never happen: check "
                    "Layer Height and Z-Offset, and report the part if they look right."),
    ("Layer climb over empty space", "The climb to the next layer passes over a hole or off the part, so the "
                                     "tow hangs there. Try another Infill Pattern, or move holes away from "
                                     "where layers end."),
    ("transition ramp exceeded", "The climb between layers is steeper than allowed. Raise Transition Angle, "
                                 "lower Layer Height, or make the part bigger."),
    ("Disjointed Z-Seam", "The nozzle moves sideways while it lifts to the next layer, which can snap the "
                          "fibre. Increase Seam Trim or try another pattern."),
    ("UNMEASURED", "Measure the pump's slowest steady speed (calibration print) and enter it as Pump-Stall "
                   "Floor."),
    ("Pump-Stall Floor engaged", "In tight turns the resin pump cannot slow down as much as the nozzle, so a "
                                 "little extra resin is laid there. Usually harmless; fewer tight turns avoid it."),
    ("Island bridges", "Fibre between separate islands spans air. Cut the ties after curing, or print the "
                       "islands as separate parts."),
    ("No toolpath on layer", "Those layers are too small to fill, leaving a gap in the part. Lower Track Gap "
                             "or Min Bend Radius, or check the model."),
    ("exceeds the bed", "The part does not fit the machine. Make it smaller, move Start X/Y, or check the "
                        "Machine section's bed size."),
    ("Z travel", "The part is taller than the machine can reach. Use fewer layers or check Max Z."),
    ("not watertight", "Repair the model in your CAD program (close the open edges) and export it again."),
    ("mesh has holes", "Repair the model in your CAD program (close the open edges) and export it again."),
    ("came out empty", "Repair the model, or check that its bodies touch."),
    # structural PCB
    ("narrower than the", "Make the trace wider (Trace Width) or use a finer needle."),
    ("smaller than one", "Make the pad bigger than the bead, or use a finer needle."),
    ("of the board edge", "Move it inward, or lower Edge Clearance if your board allows it."),
    ("of the hole", "Move it away from the hole, or route it round."),
    ("keepout zone", "Move it out of the keep-out, or reshape the zone."),
    ("Short:", "Two different nets touch. Move one, or use Auto-route to re-route it."),
    ("Clearance:", "The traces are closer than the ink can keep apart. Move them, or lower the clearance "
                   "only if your ink spreads less."),
    ("Carbon fibre is conductive", "Add a dielectric (insulation) layer between the fibre and the ink, or "
                                   "pick a print mode with one."),
)


def fix_hint(message: str) -> str:
    """Plain fix for `message`, or '' when there is none (or it already says how)."""
    for key, hint in _HINTS:
        if key in message:
            return hint
    return ""
