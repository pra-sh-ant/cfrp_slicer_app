"""Plain-language help for every setting (tooltips and the settings search).
Each entry: what it does, then what changes when you raise or lower it."""

HELP = {
    # --- part ---------------------------------------------------------------
    "shape": "What you are printing: a flat plate (rectangle, triangle, hexagon, circle), a hollow "
             "shell that grows upward (cylinder, tube, sphere, dome…), the overhang test, a 3D model "
             "from a file (STL), or a board you drew in the PCB editor (Custom Outline).",
    "width": "How wide the flat part is (left to right). For a hexagon or circle this is the diameter.",
    "height": "How deep the flat part is (front to back on the bed). This is NOT the thickness; "
              "thickness comes from Total Layers x Layer Height.",
    "include_boundary": "Adds one extra loop of fibre right along the outer edge of every layer. "
                        "Gives a clean, strong edge; costs a little fibre.",
    "custom_outline": "The board shape (and its holes) you drew in the Structural PCB editor. "
                      "Click Edit… to change it.",
    "stl_path": "The 3D model to print. It is cut into flat slices, one per layer, and each slice is "
                "filled with fibre.",
    "stl_scale": "Makes the model bigger or smaller. Use 25.4 if the file was drawn in inches, "
                 "10 for centimetres, 1000 for metres. 1 = the file is already in millimetres.",
    "stl_islands": "When a slice has several separate pieces (e.g. table legs): off = only the "
                   "biggest piece gets fibre; on = all pieces, with the fibre stretched across the "
                   "gaps between them (snip those bridges after curing).",
    "stl_closing_radius": "Repairs tiny cracks in the model's slices: gaps narrower than about twice "
                          "this are closed. Raise it for a messy model; lower it if real thin slots vanish.",
    "stl_stitch": "Repairs holes in a broken model: open edges closer than this are joined. Raise it "
                  "for badly broken files; 0 turns the repair off.",
    "base_radius": "How wide the shell is: its outer radius (or the radius of the overhang test's base).",
    "stem_height": "How tall the shell is.",
    "disc_radius": "Radius of the overhang test's top disc (how far it sticks out).",
    "stem_top": "Height of the overhang test's stem, measured from the bed.",

    # --- pattern & layers -------------------------------------------------------
    "infill_type": "How the fibre is laid in each layer. Spirals (Archimedean, Fermat, Corner-Step) "
                   "go round and round following the outline - smooth, few tight turns. Zigzags "
                   "(Serpentine, 45° Hatch) lay straight lines at an angle and turn back at the "
                   "edges - stiff in one direction, many tight turns.",
    "tracks_per_layer": "How many rounds of fibre each layer gets (spirals), or how thick the wall of "
                        "a shell is. More = stronger and heavier; fewer = faster, lighter, hollow "
                        "in the middle.",
    "fill_solid": "Ignore Tracks Per Layer and fill every layer completely (as many rounds as fit).",
    "first_layer_solid": "Make the very first layer completely filled, whatever the other layers do. "
                         "Gives the part a closed, flat base that sticks well to the bed.",
    "first_layer_infill": "Pattern for the first layer and for the solid bottom/top layers. Spirals "
                          "fill closest to the edge (fullest base); zigzag line ends must stay a bend "
                          "radius away from the edge.",
    "solid_bottom_layers": "How many layers at the bottom are filled completely (skins), whatever the "
                           "other layers do: a closed, stiff base. On an imported model, every place "
                           "where the part has a bottom surface (an overhang) gets them too. Off = only "
                           "First Layer Solid.",
    "solid_top_layers": "How many layers at the top are filled completely: a closed, smooth top "
                        "surface. On an imported model, every place where the part has a top surface "
                        "(the top of a base under thinner walls) gets them too.",
    "first_layer_min_coverage": "Warn me if the first layer is less covered with fibre than this "
                                "(0 = don't check). Tight bends can't reach the very corners, so "
                                "100 % is rarely possible.",
    "layers": "How many flat layers are stacked. Thickness = layers x layer height.",
    "layup_seq": "The direction of the fibre in each layer, in degrees, repeating: e.g. 0, 90 "
                 "crosses the layers for strength both ways; 0, 45, 90, -45 is strong in every "
                 "direction. Only zigzag patterns have a direction.",

    # --- placement -----------------------------------------------------------------
    "start_x": "Where the part sits on the bed, left to right.",
    "start_y": "Where the part sits on the bed, front to back.",
    "track_gap": "Distance between neighbouring fibre lines (centre to centre), about the width of "
                 "the fibre tow. Smaller = denser and stronger but slower; larger = gaps between lines.",
    "z_offset": "How high the nozzle is above the bed for the first layer. Too low scrapes, too high "
                "and the fibre doesn't stick.",

    # --- material ---------------------------------------------------------------------
    "needle_dia": "Inside diameter of the resin needle.",
    "layer_height": "Thickness of one layer (and how thinly an STL model is sliced). Thinner = finer "
                    "but more layers and longer prints.",
    "e_mult": "Resin amount multiplier. Raise it if the fibre looks dry, lower it if resin pools.",
    "density": "Density of the liquid resin (from its data sheet).",
    "weight": "How heavy the fibre tow is per millimetre (from its data sheet). Sets how much resin "
              "each millimetre of fibre needs.",

    # --- speed & flow ---------------------------------------------------------------------
    "v_max": "Fastest printing speed, used on straight runs. Faster = shorter prints but less time "
             "for the fibre to settle.",
    "v_min": "Slowest speed in the tightest bends. Lower = gentler on the fibre in curves.",
    "accel": "How quickly the head speeds up. Higher = snappier but harder on the fibre.",
    "decel": "How quickly the head slows down before a bend.",
    "p_advance": "Resin pressure takes a moment to change. This starts changing the flow this many "
                 "millimetres early, so speed-ups and slow-downs don't leave blobs or dry spots.",
    "pa_table": "Measured pressure advance at several speeds, as speed:advance pairs (from the "
                "pressure-advance calibration coupon), e.g. 5:1.0, 10:3.0. Leave empty to use the "
                "single value above.",
    "pump_stall_floor": "The pump never runs slower than this, because below it the resin stops "
                        "flowing reliably. Measure it with the pump-floor calibration coupon.",
    "min_radius": "Tightest bend the fibre may take without breaking or kinking. Bigger = safer for "
                  "the fibre but corners and small details can't be reached.",

    # --- seams & transitions -----------------------------------------------------------------
    "anchor_length": "A short straight start before the part begins, to grip the fibre to the bed.",
    "transition_angle": "When moving up to the next layer, how steeply the nozzle may climb. Lower = "
                        "gentler climb, needs more room.",
    "seam_trim_length": "How much is cut off each layer's end to make room for the climb to the next "
                        "layer.",
    "seam_stagger": "Round shells: turn each layer's start point by this angle so the starts don't "
                    "line up into one weak seam (137.5° spreads them evenly).",

    # --- mushroom ----------------------------------------------------------------------------
    "v_z": "Speed of the overhang test's vertical pull.",
    "v_overhang": "Speed on the overhang test's sticking-out disc.",
    "dwell": "Pause at the top of the overhang test's stem so the UV light can cure it.",

    # --- G-code start -------------------------------------------------------------------------
    "uv": "UV light brightness while printing (0-255).",
    "purge_e": "How much resin is pushed out before the print to clear the needle.",
    "purge_f": "How fast that purge is pushed.",
    "travel_f": "Speed of the first move to the start point.",

    # --- machine ---------------------------------------------------------------------------------
    "bed_x": "Usable bed width of your printer (left to right).",
    "bed_y": "Usable bed depth of your printer (front to back).",
    "z_max": "Highest the nozzle can go.",
    "bed_margin": "Keep the print this far from the bed edges when it is moved to fit.",
}

DIW_HELP = {
    "print_mode": "What to print: Structural = carbon-fibre board AND the electronics on top; "
                  "Fibre only = just the board; Electronics only = just the ink, on a part that "
                  "already exists (set Surface height).",
    "nozzle": "Inside diameter of the ink needle.",
    "line_width": "How wide one printed ink line is (usually a bit wider than the needle).",
    "layer_height": "How thick one ink layer is.",
    "conductor_layers": "How many layers of conductive ink are stacked. More = lower resistance, "
                        "thicker traces.",
    "dielectric": "Print an insulating layer under the traces. Carbon fibre conducts electricity, "
                  "so without it every trace would short through the board.",
    "dielectric_layers": "How many insulating layers. More = safer insulation.",
    "dielectric_margin": "How far the insulation reaches beyond each trace.",
    "encapsulate": "Cover the traces with a protective layer afterwards (pads stay open for soldering).",
    "encapsulation_layers": "How many protective layers.",
    "clearance": "Smallest allowed gap between traces of different signals. Ink spreads a little, "
                 "so too-close traces can touch.",
    "edge_clearance": "Keep ink at least this far from the board edge and from holes.",
    "speed_conductor": "Printing speed for conductive ink.",
    "speed_dielectric": "Printing speed for insulating ink.",
    "travel_speed": "Speed when moving between lines with the ink off.",
    "z_hop": "Lift the needle by this much when moving between lines, so it doesn't drag through wet ink.",
    "dispense": "Pneumatic = the ink is pushed by air and switched on/off by a valve command; "
                "Volumetric = a syringe pump driven like an extruder (E axis).",
    "dispense_on": "Printer command that opens the ink valve.",
    "dispense_off": "Printer command that closes the ink valve.",
    "on_delay": "Wait this long after opening the valve before moving, so the ink is flowing.",
    "off_delay": "Wait this long after closing the valve, so the line ends cleanly.",
    "e_per_mm3": "Volumetric only: how many E units push out 1 mm³ of ink.",
    "tool_select": "Printer command that selects the ink head (e.g. T1).",
    "tool_offset": "How far the ink needle sits from the fibre nozzle (X, Y, Z), so both print in "
                   "the same place.",
    "pause_before": "Pause before the ink starts so you can cut the fibre and mount the ink head.",
    "resistivity": "How well the cured ink conducts (Ω·mm). Silver ~0.00004, carbon ~0.1. Used to "
                   "estimate each trace's resistance.",
    "cured_ratio": "How much thinner the ink gets when it dries/cures (0.5 = half the wet height).",
    "carbon": "Tick when the board is carbon fibre (it conducts): the checks then insist on the "
              "insulating layer.",
    "surface_z": "Electronics only: height of the top of the part you print onto.",
    "ink_preset": "Fill in typical values for a conductive ink (silver, carbon, liquid metal, copper). "
                  "Then fine-tune with a test print.",
    "diel_preset": "Fill in typical values for the insulating ink (UV-curable, epoxy, silicone).",
    "stop_early": "Close the valve this far before each line ends: the ink still in the needle finishes "
                  "the line instead of leaving a blob. Raise it if line ends are fat.",
    "prime_e": "Push a little extra ink at the start of each line so it starts full. Raise it if line "
               "starts are thin.",
    "retract_e": "Pull the ink back a little at each line end so it stops cleanly. Raise it if ink "
                 "strings between lines.",
    "uv_cure": "Switch the UV light on after each ink layer to harden it before the next one.",
    "uv_cure_s": "How long each UV cure lasts.",
    "uv_on": "Printer command that switches the UV light on.",
    "uv_off": "Printer command that switches the UV light off.",
    "embed_after": "Embedded mode: after which fibre layer the electronics are printed (the rest of the "
                   "layers go on top). 'middle' = half way.",
    "spread_factor": "How much wider a line gets after it is laid, as the ink flows out (1 = not at all). "
                     "Measure a printed line and divide by Line width. Lines are then spaced and kept "
                     "inside the edges by the spread width, and the checks use it.",
    "min_insulation_overlap": "How far conductive ink must stay inside the insulation's edge. Ink that "
                              "spreads past the edge touches the carbon fibre and shorts through it.",
    "max_current_density": "How much current each mm² of cured ink may carry before it gets too warm. "
                           "Sets every wire's 'carries up to' figure; give a wire a Current to check it.",
    "layer_wait": "Pause after each ink layer so solvent inks dry before the next layer goes on top "
                  "(0 = no pause). UV inks use UV cure instead.",
    "bridge_layers": "Insulation layers printed where a crossover (level-2) wire crosses another net.",
    "bridge_margin": "How far the crossover insulation reaches past the crossing on every side.",
}
