"""Sidebar control panel: one render_*_section() function per expander.

Each function returns a plain dict of the config keys it owns. The caller
(app.py) merges these fragments into the single `sys_config` dict passed to
the slicing pipeline -- kept separate here so each physical/process concern
(geometry, placement, materials, kinematics...) stays independently readable
and testable instead of one 250-line block.
"""
import math

import presets
import streamlit as st


def render_run_controls():
    st.header("Slicer Controls")
    sc1, sc2 = st.columns(2)
    auto_slice = sc1.checkbox("🔄 Auto-Slice", value=True, help="Re-slice immediately when a parameter changes.")
    force_slice = sc2.button(
        "🔪 Slice Now",
        type="primary",
        disabled=auto_slice,
        use_container_width=True,
        help="Run a one-off slicing pass without waiting for autoslice."
    )
    st.markdown("---")

    return {"auto_slice": auto_slice, "force_slice": force_slice}


def render_layer_strategy(p, is_3d, is_revolve=False, shape=None):
    with st.expander("📚 Layering & Layup", expanded=True):
        if is_revolve:
            st.info(
                "Z-revolve shells (sphere/dome/cylinder/tube/taper) print the same way as a "
                "flat laminate: sliced into layers, with a concentric multi-track wall at "
                "every layer that alternates outside-in / inside-out."
            )
            cfg_tracks = st.number_input(
                "Tracks Per Layer (Wall Thickness)",
                min_value=1,
                value=int(p.get("tracks_per_layer", 3)),
                step=1,
                help="Number of concentric fiber tracks in the wall at every Z-slice. More tracks = thicker wall."
            )
            revolve_infill_options = ["Archimedean (Concentric Spiral)", "Fermat Spiral (Connected Double Spiral)"]
            cfg_infill = st.selectbox(
                "Infill Pattern",
                revolve_infill_options,
                index=revolve_infill_options.index(p.get("infill_type", revolve_infill_options[0]))
                if p.get("infill_type", revolve_infill_options[0]) in revolve_infill_options else 0,
                help=(
                    "Concentric spiral alternates the starting side per layer. Fermat spiral traces "
                    "a connected double spiral instead -- pick it if a wall cross-section can ever "
                    "split into separate islands (e.g. a thin annulus pinching apart), since the "
                    "plain concentric spiral silently keeps only the largest island at a split."
                )
            )
            cfg_layers, cfg_layup_str = 1, "0"
            layup_sequence = [0.0]
        elif is_3d:
            st.info("Z-axis specimen modes are treated as specialty geometries; multi-layer layup is kept simple for research clarity.")
            cfg_layers, cfg_tracks, cfg_layup_str, cfg_infill = 1, 1, "0", "N/A"
            layup_sequence = [0.0]
        else:
            cfg_layers = st.number_input(
                "Total Layers",
                min_value=1,
                value=int(p["layers"]),
                step=1,
                help="Number of laminate passes to stack vertically."
            )

            cfg_tracks = st.number_input(
                "Tracks Per Layer",
                min_value=1,
                value=int(p.get("tracks_per_layer", 10)),
                step=1,
                help="Exact number of continuous fiber tracks in every layer. Spacing is checked against the bend radius."
            )
            infill_options = [
                "Archimedean (Concentric Spiral)",
                "Fermat Spiral (Connected Double Spiral)",
                "Oriented Serpentine",
            ]
            cfg_infill = st.selectbox(
                "Infill Pattern",
                infill_options,
                index=infill_options.index(p.get("infill_type", infill_options[0]))
                if p.get("infill_type", infill_options[0]) in infill_options else 0,
                help=(
                    "The internal fill strategy used between walls. Concentric spiral keeps one "
                    "forward winding direction and alternates only the starting side per layer. "
                    "Fermat spiral is the safe choice once the fillable area can split into "
                    "separate islands (holes, narrow necks) -- it visits every island in one "
                    "continuous in-then-out stroke instead of dropping the smaller ones."
                )
            )

            st.caption("Layup Sequence (comma separated angles in degrees)")
            cfg_layup_str = st.text_input(
                "e.g. 0, 90, 0 or 45, -45",
                value=p["layup"],
                help="Fiber orientation per layer. Change this to study anisotropy and stacking effects."
            )

            try:
                layup_sequence = [float(x.strip()) for x in cfg_layup_str.split(',') if x.strip()]
                if not layup_sequence:
                    layup_sequence = [0.0]
            except ValueError:
                st.error("Invalid layup sequence. Defaulting to [0.0]")
                layup_sequence = [0.0]

    return {
        "layers": cfg_layers, "layup_seq": cfg_layup_str, "infill_type": cfg_infill,
        "layup_sequence": layup_sequence, "tracks_per_layer": cfg_tracks
    }


def render_geometry_section(p):
    with st.expander("📐 Target Geometry", expanded=True):
        shape_list = presets.SHAPES
        shape_idx = shape_list.index(p["shape"]) if p["shape"] in shape_list else 0
        cfg_shape = st.selectbox(
            "Shape",
            shape_list,
            index=shape_idx,
            key="shape_select",
            help="Choose the part geometry to study. Z-axis shells are useful for hollow, dome, and tube research prints."
        )

        if cfg_shape == "3D Overhang (Mushroom)":
            c1, c2 = st.columns(2)
            cfg_base_r = c1.number_input("Base Anchor Radius (mm)", value=float(p["base_r"]), min_value=0.1, step=1.0)
            cfg_top_r = c2.number_input("Top Disc Radius (mm)", value=float(p["top_r"]), min_value=0.1, step=1.0)
            cfg_stem_h = st.number_input(
                "Stem Top Height (absolute Z, mm)",
                value=float(p["stem_h"]), min_value=0.1, step=1.0,
                help="Absolute Z coordinate of the stem top, not relative to the Z-offset."
            )
            cfg_wall_t, cfg_w, cfg_h, cfg_thick, cfg_boundary = 0.0, 0, 0, 0, False
        elif cfg_shape == "Hollow Sphere (Z)":
            # A sphere's height is exactly one diameter -- it isn't a free
            # parameter. Deriving it (instead of taking an independent
            # Height input) is what keeps the shape actually round instead
            # of squashed into a lens/egg.
            cfg_base_r = st.number_input("Outer Radius (mm)", value=float(p.get("base_r", 20.0)), min_value=1.0, step=1.0)
            cfg_stem_h = 2.0 * cfg_base_r
            st.caption(f"Height = 2 x Outer Radius = {cfg_stem_h:.1f} mm (fixed by geometry)")
            cfg_wall_t, cfg_top_r, cfg_w, cfg_h, cfg_thick, cfg_boundary = 0.0, 0.0, 0, 0, 0, False
        elif cfg_shape in {"Hollow Cylinder (Z)", "Hollow Square Tube (Z)", "Dome (Z)", "Tapered Cylinder (Z)", "Conical Dome (Z)"}:
            cfg_base_r = st.number_input("Outer Radius (mm)", value=float(p.get("base_r", 20.0)), min_value=1.0, step=1.0)
            cfg_stem_h = st.number_input("Height (Z mm)", value=float(p.get("stem_h", 25.0)), min_value=1.0, step=1.0)
            cfg_wall_t, cfg_top_r, cfg_w, cfg_h, cfg_thick, cfg_boundary = 0.0, 0.0, 0, 0, 0, False
        else:
            if cfg_shape in {"Hexagon", "Circle"}:
                cfg_w = st.number_input("Diameter / Width (mm)", value=float(p["w"]), min_value=1.0, help="Overall XY size of the selected symmetric profile.")
                cfg_h = cfg_w
            else:
                c1, c2 = st.columns(2)
                cfg_w = c1.number_input("Width (mm)", value=float(p["w"]), min_value=1.0, help="Overall XY width of the selected profile.")
                cfg_h = c2.number_input("Height (mm)", value=float(p["h"]), min_value=1.0, help="Overall XY height of the selected profile.")
            cfg_thick = (
                st.number_input("Bracket Thickness (mm)", value=float(p.get("thick", 15.0)), min_value=0.1, help="Thickness of each L-bracket leg.")
                if cfg_shape == "L-Bracket" else 0.0
            )
            cfg_boundary = st.checkbox("Print Outer Boundary", value=bool(p.get("include_boundary", False)), help="Add one perimeter pass around the internal layup.")
            cfg_base_r, cfg_top_r, cfg_stem_h, cfg_wall_t = 0.0, 0.0, 0.0, 0.0

    return {
        "shape": cfg_shape, "width": cfg_w, "height": cfg_h, "thickness": cfg_thick,
        "base_radius": cfg_base_r, "disc_radius": cfg_top_r,
        "stem_height": cfg_stem_h, "wall_thickness": cfg_wall_t,
        "include_boundary": cfg_boundary
    }


def render_placement_section(p):
    with st.expander("📍 Placement & Offsets", expanded=False):
        c3, c4 = st.columns(2)
        cfg_x = c3.number_input("Start X", value=float(p["start_x"]), step=1.0)
        cfg_y = c4.number_input("Start Y", value=float(p["start_y"]), step=1.0)
        c5, c6 = st.columns(2)
        # Track Gap is floored above 0: a value <= 0 previously hung the slicer in
        # an infinite loop inside the path generators.
        cfg_gap = c5.number_input(
            "Track Gap (mm)",
            value=float(p["gap"]),
            step=0.1,
            min_value=0.05,
            help="Distance between adjacent fiber tracks. For serpentine paths, the slicer enforces Gap >= 2 x Min Bend Radius."
        )
        cfg_z_off = c6.number_input("Z-Offset (SOD)", value=float(p["z_off"]), min_value=0.0, step=0.05)

    return {"start_x": cfg_x, "start_y": cfg_y, "track_gap": cfg_gap, "z_offset": cfg_z_off}


def render_materials_section(p):
    with st.expander("⚗️ Physio-Chemical Materials", expanded=False):
        c_m1, c_m2 = st.columns(2)
        cfg_n_dia = c_m1.number_input("Needle Dia (mm)", value=float(p.get("needle_dia", 0.8)), min_value=0.01, step=0.1)
        cfg_l_height = c_m2.number_input("Layer Height (mm)", value=float(p.get("layer_height", 0.2)), min_value=0.01, step=0.05)
        c_m3, c_m4 = st.columns(2)
        cfg_emult = c_m3.number_input("E-Multiplier", value=float(p.get("e_mult", 1.6)), min_value=0.01, step=0.1)
        cfg_density = c_m4.number_input("Liquid Density (g/mm³)", value=float(p.get("density", 0.00115)), min_value=0.00001, format="%.5f")
        cfg_weight = st.number_input(
            "Fiber Weight Measured (g/mm)",
            value=float(p.get("weight", 0.55 / (math.pi * 39))), min_value=0.00001, format="%.5f",
            help="Must be above zero: the extrusion constant divides by it."
        )

    return {
        "layer_height": cfg_l_height,
        "needle_dia": cfg_n_dia, "e_mult": cfg_emult, "density": cfg_density, "weight": cfg_weight,
    }


def render_kinematics_section(p, is_3d, is_revolve=False, shape=None):
    with st.expander("🚀 Kinematics & Rituals", expanded=False):
        calculated_speed = st.number_input("Print Speed (Max)", value=float(p["vmax"]), min_value=0.1, step=0.5)
        cfg_uv = st.number_input("UV Intensity (PWM)", value=int(p["uv"]), min_value=0, max_value=255, step=25)
        # Min Bend Radius is floored at 0: negative values fed into the join-style
        # buffer offsets produce degenerate/empty geometry rather than a real bend.
        cfg_radius = st.number_input(
            "Min Bend Radius (mm)",
            value=float(p["rad"]),
            step=0.5,
            min_value=0.0,
            help="Smallest allowed fiber bend. A serpentine U-turn needs at least twice this value in track spacing."
        )

        if is_3d and not is_revolve:
            # Mushroom: flat-speed specialty generator, single trajectory with
            # no per-layer seam ramp and no cornering-speed planning.
            cfg_vz = st.number_input("Z Pull Speed (mm/s)", value=float(p.get("v_z", 2.0)), min_value=0.1, step=0.5)
            if shape == "3D Overhang (Mushroom)":
                cfg_v_overhang = st.number_input("Top Disc Overhang Speed (mm/s)", value=float(p.get("v_overhang", 5.0)), min_value=0.1, step=0.5)
                cfg_dwell = st.number_input("Top Dwell (sec)", value=float(p.get("dwell", 1.5)), min_value=0.0, step=0.5)
            else:
                cfg_v_overhang, cfg_dwell = 0.0, 0.0
            cfg_vmin, cfg_accel, cfg_decel, cfg_p_adv, cfg_anchor = 0, 0, 0, 0, 0
            cfg_pump_stall_floor = 0
            cfg_transition_angle, cfg_seam_trim = 0.0, 0.0
        else:
            cfg_transition_angle = st.number_input(
                "Layer Transition Angle (deg)",
                value=float(p.get("transition_angle", 30.0)),
                min_value=5.0,
                max_value=85.0,
                step=5.0,
                help="Warns if the seam ramp (see Seam Trim Length below) ends up steeper than this, measured from the XY plane."
            )
            cfg_seam_trim = st.number_input(
                "Seam Trim Length (mm)",
                value=float(p.get("seam_trim", 3.0)),
                min_value=0.0,
                step=0.5,
                help=(
                    "Pulls back this much travel from the end of each layer and the start of the "
                    "next, then bridges the gap with a sloped move as Z climbs -- instead of jumping "
                    "straight between two points that can sit right on top of each other (e.g. a "
                    "closed single-track loop). Raise this if the transition-angle warning fires."
                )
            )
            # Revolve shells (sphere/dome/cylinder/tube/taper) use the same
            # jerk/accel-limited cornering-speed planner as the flat
            # laminate pipeline, so they need the same tuning knobs.
            cfg_vmin = st.number_input("Min Bend Speed (mm/s)", value=float(p["vmin"]), min_value=0.0, step=0.5)
            cfg_pump_stall_floor = st.number_input(
                "Pump-Stall Floor (mm/s-equiv)", value=float(p.get("pump_stall_floor", p["vmin"])), min_value=0.0, step=0.5,
                help=(
                    "Minimum pump flow, in the same mm/s-equivalent units as Min Bend Speed, "
                    "below which shear stress can no longer reliably pull the fiber through "
                    "(measure this from a step-response test under real back-pressure -- see "
                    "roadmap Week 1-2). Defaults equal to Min Bend Speed until you have that "
                    "number, which means NO real physical floor is enforced yet."
                )
            )
            c11, c12 = st.columns(2)
            cfg_decel = c11.number_input("Deceleration", value=float(p["decel"]), min_value=0.1, step=0.5)
            cfg_accel = c12.number_input("Acceleration", value=float(p["accel"]), min_value=0.1, step=0.5)
            cfg_p_adv = st.number_input("Pressure Adv.", value=float(p["p_adv"]), min_value=0.0, step=0.5)
            cfg_anchor = st.number_input("Lead-in Anchor Length (mm)", value=float(p.get("anchor", 15.0)), min_value=0.0, step=1.0)
            cfg_vz, cfg_dwell, cfg_v_overhang = 0, 0, 0

        cfg_purge_e = st.number_input("Purge Amount", value=float(p.get("purge_e", 15.0)), min_value=0.0)
        cfg_purge_f = st.number_input("Purge Speed", value=float(p.get("purge_f", 60.0)), min_value=1.0)
        cfg_travel_f = st.number_input("Approach Speed", value=float(p.get("travel_f", 600.0)), min_value=1.0)

    return {
        "v_max": calculated_speed, "uv": cfg_uv, "min_radius": cfg_radius,
        "transition_angle": cfg_transition_angle, "seam_trim_length": cfg_seam_trim,
        "v_z": cfg_vz, "v_overhang": cfg_v_overhang, "dwell": cfg_dwell,
        "v_min": cfg_vmin, "pump_stall_floor": cfg_pump_stall_floor,
        "decel": cfg_decel, "accel": cfg_accel, "p_advance": cfg_p_adv,
        "anchor_length": cfg_anchor, "purge_e": cfg_purge_e, "purge_f": cfg_purge_f,
        "travel_f": cfg_travel_f
    }


