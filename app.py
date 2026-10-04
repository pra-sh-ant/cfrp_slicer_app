"""CFRP Slicer Pro -- entry point.

Run with: streamlit run cfrp_slicer/app.py
"""
import math
import streamlit as st
from shapely.affinity import translate

import machine as machine_mod
import presets
from geometry import GeometryEngine
from kinematics import KinematicsEngine
from specialty import SpecialtyEngine
from validation import ValidationEngine, resolve_process_constraints
from gcode import GCodeCompiler
from ui import sidebar, results

st.set_page_config(layout="wide", page_title="CFRP Slicer Pro", page_icon="🔬")

if "last_slice" not in st.session_state: st.session_state.last_slice = None


def _trim_trajectory_tail(trajectory, trim_length):
    """Remove up to `trim_length` mm of travel from the end of `trajectory`
    (mutates it in place), never crossing into an earlier layer or a prior
    ramp. Returns the exact point at the new end (interpolated if the cut
    falls mid-segment)."""
    if trim_length <= 1e-9 or len(trajectory) < 2:
        last = trajectory[-1]
        return {'x': last['x'], 'y': last['y'], 'z': last['z']}

    target_layer = trajectory[-1].get('layer')
    run_start = len(trajectory) - 1
    while run_start > 0:
        pt = trajectory[run_start - 1]
        if pt.get('layer') != target_layer or pt.get('is_transition'):
            break
        run_start -= 1

    if run_start >= len(trajectory) - 1:
        last = trajectory[-1]
        return {'x': last['x'], 'y': last['y'], 'z': last['z']}

    run = trajectory[run_start:]
    total_length = sum(
        math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(run[:-1], run[1:])
    )
    trim_length = min(trim_length, total_length * 0.4)

    traveled = 0.0
    for idx in range(len(trajectory) - 1, run_start, -1):
        a, b = trajectory[idx - 1], trajectory[idx]
        seg = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
        if traveled + seg >= trim_length:
            frac = (trim_length - traveled) / seg if seg > 1e-9 else 0.0
            cut = {
                'x': b['x'] - (b['x'] - a['x']) * frac,
                'y': b['y'] - (b['y'] - a['y']) * frac,
                'z': b['z'],
            }
            del trajectory[idx:]
            return cut
        traveled += seg

    last = trajectory[-1]
    return {'x': last['x'], 'y': last['y'], 'z': last['z']}


def _trim_path_head(points, trim_length):
    """Remove up to `trim_length` mm of travel from the start of `points` (a
    layer's already-kinematics-planned point list). Returns (cut_point,
    remaining_points): `remaining_points` keeps its original speed/extrusion
    planning untouched, and `cut_point` is the exact interpolated boundary a
    ramp should arrive at."""
    if trim_length <= 1e-9 or len(points) < 2:
        first = points[0]
        return {'x': first['x'], 'y': first['y'], 'z': first['z']}, points

    total_length = sum(
        math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(points[:-1], points[1:])
    )
    trim_length = min(trim_length, total_length * 0.4)

    traveled = 0.0
    for idx in range(1, len(points)):
        a, b = points[idx - 1], points[idx]
        seg = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
        if traveled + seg >= trim_length:
            frac = (trim_length - traveled) / seg if seg > 1e-9 else 0.0
            cut = {
                'x': a['x'] + (b['x'] - a['x']) * frac,
                'y': a['y'] + (b['y'] - a['y']) * frac,
                'z': b['z'],
            }
            # points[idx] can be far from the exact cut (e.g. the far end of
            # a straight wall edge), so its old `dist` -- measured from `a`,
            # which is being dropped -- is stale. Recompute it from the cut.
            remaining = list(points[idx:])
            remaining[0] = dict(remaining[0])
            remaining[0]['dist'] = math.hypot(remaining[0]['x'] - cut['x'], remaining[0]['y'] - cut['y'])
            return cut, remaining
        traveled += seg

    first = points[0]
    return {'x': first['x'], 'y': first['y'], 'z': first['z']}, points


def _revolve_radius_profile(shape, sys_config, tracks, gap):
    """Return a function progress(0..1) -> (outer_radius, inner_radius) at
    that height, for the Z-revolve shell shapes.

    Every revolve shape has an intrinsic outer radius and a wall whose
    thickness is `tracks * gap` (tracks_per_layer controls wall thickness
    directly).
    """
    outer_radius = max(float(sys_config.get('base_radius', 18.0)), 1.0)
    wall = tracks * gap

    if shape == 'Hollow Sphere (Z)':
        # True circle cross-section: progress 0..1 maps to z = -R..R (a full
        # sphere is exactly 2R tall -- see the height override in
        # _run_revolve_pipeline), and radius(z) = sqrt(R^2 - z^2).
        # sin(pi*progress) was only a rough stand-in and visibly
        # flattens/points the poles.
        def sphere(progress):
            r = outer_radius * math.sqrt(max(0.0, 1.0 - (2.0 * progress - 1.0) ** 2))
            return r, max(0.0, r - wall)

        return sphere

    if shape == 'Dome (Z)':
        def dome(progress):
            r = outer_radius * math.sqrt(max(0.0, 1.0 - progress * progress))
            return r, max(0.0, r - wall)

        return dome

    if shape == 'Tapered Cylinder (Z)':
        r1 = max(outer_radius * 0.45, 3.0)

        def taper(progress):
            r = outer_radius * (1.0 - progress) + r1 * progress
            return r, max(0.0, r - wall)

        return taper

    # Hollow Cylinder / Hollow Square Tube: constant outer radius.
    return lambda progress: (outer_radius, max(0.0, outer_radius - wall))


def _kinematics_params(sys_config):
    """The speed-planner settings shared by every layered pipeline.
    resolve_process_constraints has already range-checked these; the
    defaults only cover callers that omit a key entirely."""
    v_max = float(sys_config.get('v_max', 10.0))
    v_min = float(sys_config.get('v_min', v_max * 0.5))
    return {
        'v_max': v_max,
        'v_min': v_min,
        'accel': float(sys_config.get('accel', 25.0)),
        'decel': float(sys_config.get('decel', 15.0)),
        'p_advance': float(sys_config.get('p_advance', 3.0)),
        'pump_floor': float(sys_config.get('pump_stall_floor', v_min)),
        'min_rad': float(sys_config.get('min_radius', 0.0)),
    }


def _add_lead_in_anchor(raw_path, anchor_length):
    """Prepend a straight tangent lead-in of `anchor_length` mm, pointing
    back along the path's first segment (first printed layer only)."""
    if anchor_length <= 0 or len(raw_path) < 2:
        return raw_path
    dx = raw_path[1][0] - raw_path[0][0]
    dy = raw_path[1][1] - raw_path[0][1]
    angle_rad = math.atan2(dy, dx)
    anchor_x = raw_path[0][0] - math.cos(angle_rad) * anchor_length
    anchor_y = raw_path[0][1] - math.sin(angle_rad) * anchor_length
    return [(anchor_x, anchor_y)] + list(raw_path)


def _plan_layer(raw_path, layer_z, layer_num, kin):
    """Speed-plan one layer's 2D path and tag every point with its layer."""
    layer_trajectory = KinematicsEngine.calculate_trajectory(
        tuple(raw_path), kin['v_max'], kin['v_min'], kin['accel'], kin['decel'],
        kin['p_advance'], z_height=layer_z, min_rad=kin['min_rad'],
        pump_floor=kin['pump_floor'],
    )
    for pt in layer_trajectory:
        pt['layer'] = layer_num
        pt['z_layer'] = layer_z
    return layer_trajectory


def _append_layer(trajectory, layer_trajectory, layer_num, seam_trim, ramp_speed):
    """Append one planned layer to `trajectory` (in place), bridged from the
    previous layer by a RAMPED Z-SEAM TRANSITION.

    Pull back `seam_trim` mm from the end of the previous layer and from the
    start of this one, opening a short gap that a sloped move fills while Z
    climbs -- instead of jumping straight between two points that can sit
    right on top of each other (e.g. a closed single-track loop, where the
    previous layer's end and this layer's start are the same XY location).
    This only trims arc length off the already-planned path, so it works the
    same way for any shape and any track count.

    Continuous fiber can only be laid moving forward/up -- callers must
    append layers strictly bottom to top, so each ramp only ever bridges the
    previous (lower-Z) tail forward into the next (higher-Z) head.
    """
    if trajectory and layer_trajectory:
        prev_cut = _trim_trajectory_tail(trajectory, seam_trim)
        first_cut, layer_trajectory = _trim_path_head(layer_trajectory, seam_trim)

        dx = first_cut['x'] - prev_cut['x']
        dy = first_cut['y'] - prev_cut['y']
        dzs = first_cut['z'] - prev_cut['z']
        seg_length = math.sqrt(dx ** 2 + dy ** 2 + dzs ** 2)
        steps = max(
            4 if abs(dzs) > 1e-9 else 1,
            int(math.ceil(seg_length / 0.75)),
            int(math.ceil(abs(dzs) / 0.05)),
        )
        for step in range(1, steps + 1):
            progress = step / steps
            point = {
                'x': prev_cut['x'] + dx * progress,
                'y': prev_cut['y'] + dy * progress,
                'z': prev_cut['z'] + dzs * progress,
            }
            prior = trajectory[-1]
            transition_dist = math.sqrt(
                (point['x'] - prior['x']) ** 2
                + (point['y'] - prior['y']) ** 2
                + (point['z'] - prior['z']) ** 2
            )
            # The ramp's own last step is the point that actually arrives at
            # this layer's true height -- that's what "layer_start" means
            # downstream (gcode's banner comment, validation's seam-jump
            # check), not the next original path vertex, which can be a
            # long, perfectly normal straight run away (e.g. a full wall
            # edge) from where the ramp lands.
            trajectory.append({
                "x": point['x'], "y": point['y'], "z": point['z'],
                "dist": float(transition_dist), "v": ramp_speed,
                "e_mult": 1.0, "is_transition": True,
                "layer_start": step == steps, "layer": layer_num,
            })

    trajectory.extend(layer_trajectory)


def _finalize(trajectory, target_shapes, sys_config, machine):
    """Shared tail of every pipeline: fit the path onto the bed (moving the
    target outlines by the same shift so the viewer keeps them aligned),
    then compile G-code and validate."""
    trajectory, shift_x, shift_y = machine_mod.fit_trajectory_to_bed(trajectory, machine)
    if shift_x or shift_y:
        st.info(f"Path auto-positioned for the homed machine envelope: X {shift_x:+.2f} mm, Y {shift_y:+.2f} mm.")
        target_shapes = [
            (z, translate(shape, xoff=shift_x, yoff=shift_y), layer)
            for z, shape, layer in target_shapes
        ]
    gcode_output = GCodeCompiler.compile(trajectory, sys_config)
    val_result = ValidationEngine.validate_trajectory(trajectory, sys_config, machine)
    return trajectory, target_shapes, gcode_output, val_result


def _run_revolve_pipeline(sys_config, machine):
    """Print a Z-revolve shell (sphere/dome/cylinder/tube/taper) with the
    same strategy as the planar multi-layer laminate: slice the height into
    `layer_height` steps, fill each slice's wall with `tracks_per_layer`
    concentric tracks alternating outside-in/inside-out per layer, and
    bridge every layer transition with the same seam-trim + slope ramp --
    instead of one big continuous helix sized off a fixed wall thickness.
    """
    shape = sys_config['shape']
    cx, cy = float(sys_config['start_x']), float(sys_config['start_y'])
    z0 = float(sys_config['z_offset'])
    outer_radius = max(float(sys_config.get('base_radius', 18.0)), 1.0)
    gap = float(sys_config['track_gap'])
    tracks = max(1, int(sys_config.get('tracks_per_layer', 3)))
    shape_type = 'square' if shape == 'Hollow Square Tube (Z)' else 'round'
    profile = _revolve_radius_profile(shape, sys_config, tracks, gap)
    kin = _kinematics_params(sys_config)
    seam_trim = max(0.0, float(sys_config.get('seam_trim_length', 3.0)))
    use_fermat = sys_config.get('infill_type') == "Fermat Spiral (Connected Double Spiral)"

    # A sphere's height isn't an independent parameter -- it's exactly one
    # diameter. Any other value just squashes/stretches the shape, so ignore
    # the configured Height field for it.
    if shape == 'Hollow Sphere (Z)':
        height = 2.0 * outer_radius
    else:
        height = max(float(sys_config.get('stem_height', 25.0)), 1e-6)

    layer_height = max(float(sys_config.get('layer_height', 0.2)), 0.05)
    num_layers = max(2, round(height / layer_height) + 1)

    trajectory = []
    target_shapes = []
    max_z = z0
    anchored = False

    # `i` always runs 0 (base, progress=0) to num_layers-1 (top, progress=1)
    # in ascending order -- see _append_layer for why this must never be
    # reversed or reshuffled, for any infill pattern or shape.
    for i in range(num_layers):
        progress = i / (num_layers - 1)
        layer_z = z0 + height * progress
        max_z = layer_z
        current_outer, current_inner = profile(progress)

        cross_section = GeometryEngine.get_revolve_cross_section(
            cx, cy, current_outer, current_inner, shape_type
        )
        target_shapes.append((layer_z, cross_section, i + 1))
        if cross_section.is_empty or cross_section.area < 1e-6:
            continue  # degenerate slice (e.g. right at a pole/tip) -- nothing to print

        is_outward = (i % 2 != 0)
        try:
            # track_count=None: fill as many tracks as actually fit rather
            # than demanding an exact count, since the annulus can be much
            # thinner than `tracks * gap` near a pole or tip.
            #
            # min_radius=0 here, not the configured bend radius: a round
            # wall has no real corners to round, and the square tube's
            # corners still get fiber-safe treatment from the
            # cornering-speed limiter (calculate_trajectory), same as the
            # flat 2D pipeline relies on for every sharp corner.
            if use_fermat:
                raw_path = GeometryEngine.generate_fermat_spiral_path(
                    cross_section, gap, 0.0, track_count=None, start_from_inside=is_outward
                )
            else:
                raw_path, _ = GeometryEngine.generate_archimedean_path(
                    cross_section, gap, 0.0, track_count=None, start_from_inside=is_outward
                )
        except ValueError:
            continue
        if not raw_path:
            continue

        # The lead-in goes on the first layer that actually prints -- a
        # sphere's bottom pole slice is empty, so that isn't always i == 0.
        if not anchored:
            raw_path = _add_lead_in_anchor(raw_path, float(sys_config.get('anchor_length', 0.0)))
            anchored = True

        layer_trajectory = _plan_layer(raw_path, layer_z, i + 1, kin)
        _append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin['v_min'])

    trajectory, target_shapes, gcode_output, val_result = _finalize(
        trajectory, target_shapes, sys_config, machine
    )
    return trajectory, target_shapes, gcode_output, val_result, max_z


def _run_slicing_pipeline(sys_config, is_3d, layup_sequence, machine):
    """Runs geometry -> kinematics -> validation -> gcode for the whole part.

    Bad process parameters (e.g. Track Gap <= 0) surface as a ValueError
    naming the offending layer, instead of an uncaught Shapely/NumPy
    exception crashing the whole app -- matching how a real slicer rejects
    invalid input instead of hanging or dying mid-slice.
    """
    if is_3d:
        if sys_config['shape'] in presets.Z_REVOLVE_SHAPES:
            return _run_revolve_pipeline(sys_config, machine)

        trajectory = SpecialtyEngine.generate_specialty_shape(sys_config)
        trajectory, target_shapes, gcode_output, val_result = _finalize(
            trajectory, [], sys_config, machine
        )
        max_z = max((pt['z'] for pt in trajectory), default=float(sys_config['z_offset']))
        return trajectory, target_shapes, gcode_output, val_result, max_z

    trajectory = []
    target_shapes = []
    max_z = sys_config['z_offset']
    last_pt = None
    layup_sequence = list(layup_sequence) or [0.0]
    kin = _kinematics_params(sys_config)
    seam_trim = max(0.0, float(sys_config.get('seam_trim_length', 3.0)))

    for i in range(sys_config['layers']):
        layer_z = sys_config['z_offset'] + (i * sys_config['layer_height'])
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        layer_config = sys_config.copy()
        layer_config['angle'] = current_angle

        try:
            base_poly = GeometryEngine.get_base_polygon(layer_config)

            if layer_config['shape'] == "Serpentine Infill":
                # base_poly here IS the exact zigzag print extent (both come
                # from the shared _serpentine_print_extent helper) -- unlike
                # every other shape, there's no separate wall to inset past,
                # so running it through get_printable_boundary too would
                # shrink the displayed boundary a second time and make it
                # disagree with the actually-printed path again.
                printable_boundary = base_poly
                raw_path = GeometryEngine.generate_serpentine_infill(layer_config)
            else:
                printable_boundary = GeometryEngine.get_printable_boundary(base_poly, layer_config)
                raw_path = GeometryEngine.generate_laminate_layer(
                    base_poly, layer_config, current_angle,
                    layer_idx=i, last_pt=last_pt
                )
            target_shapes.append((layer_z, printable_boundary, i + 1))
        except ValueError as exc:
            raise ValueError(f"Layer {i + 1}: {exc}") from exc

        if i == 0:
            raw_path = _add_lead_in_anchor(raw_path, float(sys_config.get('anchor_length', 0.0)))

        layer_trajectory = _plan_layer(raw_path, layer_z, i + 1, kin)
        _append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin['v_min'])

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])

    trajectory, target_shapes, gcode_output, val_result = _finalize(
        trajectory, target_shapes, sys_config, machine
    )
    return trajectory, target_shapes, gcode_output, val_result, max_z


def main():
    st.title("🔬 Continuous Fiber Slicer Pro (Multi-Layer) v5.0")
    machine = machine_mod.DEFAULT_MACHINE

    with st.sidebar:
        run_state = sidebar.render_run_controls()
        p = dict(presets.DEFAULT_PARAMS)

        # The Shape dropdown lives inside render_geometry_section, rendered
        # below -- but Layering & Kinematics (rendered above it) also need
        # to know the current shape. Streamlit persists widget values in
        # session_state by key across reruns, so read the live selection
        # here instead of the initial shape, which goes stale the moment
        # someone changes Shape. This is the one and only place is_3d gets
        # computed.
        live_shape = st.session_state.get("shape_select", p["shape"])
        is_3d = live_shape in presets.Z_SPECIALTY_SHAPES
        is_revolve = live_shape in presets.Z_REVOLVE_SHAPES

        layer_state = sidebar.render_layer_strategy(p, is_3d, is_revolve, live_shape)
        geometry_state = sidebar.render_geometry_section(p)
        placement_state = sidebar.render_placement_section(p)
        materials_state = sidebar.render_materials_section(p)
        kinematics_state = sidebar.render_kinematics_section(p, is_3d, is_revolve, live_shape)

    sys_config = {
        **geometry_state,
        **{k: v for k, v in layer_state.items() if k != "layup_sequence"},
        **placement_state,
        **materials_state,
        **kinematics_state,
    }

    try:
        sys_config = resolve_process_constraints(sys_config)
    except ValueError as exc:
        st.error(f"Parameter combination cannot be sliced: {exc}")
        st.stop()

    for note in sys_config["constraint_notes"]:
        st.info(note)

    if run_state["auto_slice"] or run_state["force_slice"] or st.session_state.last_slice is None:
        with st.spinner("Compiling Multi-Layer Toolpath..."):
            try:
                trajectory, target_shapes, gcode_output, val_result, max_z = _run_slicing_pipeline(
                    sys_config, is_3d, layer_state["layup_sequence"], machine
                )
            except ValueError as exc:
                st.error(f"Slicing failed: {exc}")
                st.stop()
            except Exception as exc:
                # Anything else is a slicer bug, not bad input -- show it
                # without discarding the traceback, and keep the app alive.
                st.error("Slicing failed with an unexpected internal error. No G-code was produced.")
                st.exception(exc)
                st.stop()

            st.session_state.last_slice = {
                "trajectory": trajectory,
                "target_shapes": target_shapes,
                "gcode_output": gcode_output,
                "val_result": val_result,
                "max_z": max_z,
                "layers": sys_config["layers"],
            }

    if st.session_state.last_slice is None:
        return

    results.render_results(st.session_state.last_slice)


if __name__ == "__main__":
    main()
