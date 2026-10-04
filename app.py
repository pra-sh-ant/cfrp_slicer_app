"""CFRP Slicer Pro -- legacy Streamlit front end over cfrp_core.

Run with: streamlit run app.py

All slicing logic lives in src/cfrp_core; this file only collects sidebar
inputs, calls the core and renders results. It is replaced by the desktop
GUI in Phase 7 and removed in Phase 10.
"""
import streamlit as st

import presets
from cfrp_core import DEFAULT_MACHINE, ConfigError, from_legacy_dict, resolve_process_constraints, slice_part
from ui import results, sidebar

st.set_page_config(layout="wide", page_title="CFRP Slicer Pro", page_icon="🔬")

if "last_slice" not in st.session_state:
    st.session_state.last_slice = None


def main():
    st.title("🔬 Continuous Fiber Slicer Pro (Multi-Layer) v5.0")
    machine = DEFAULT_MACHINE

    with st.sidebar:
        run_state = sidebar.render_run_controls()
        p = dict(presets.DEFAULT_PARAMS)

        # The Shape dropdown lives inside render_geometry_section, rendered
        # below -- but Layering & Kinematics (rendered above it) also need
        # to know the current shape. Streamlit persists widget values in
        # session_state by key across reruns, so read the live selection
        # here instead of the initial shape, which goes stale the moment
        # someone changes Shape.
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
        config = from_legacy_dict(sys_config, layup=layer_state["layup_sequence"])
    except ConfigError as exc:
        st.error(f"Parameter combination cannot be sliced: {exc}")
        st.stop()

    for note in resolve_process_constraints(config).notes:
        st.info(note)

    if run_state["auto_slice"] or run_state["force_slice"] or st.session_state.last_slice is None:
        with st.spinner("Compiling Multi-Layer Toolpath..."):
            try:
                result = slice_part(config, machine)
            except ValueError as exc:
                st.error(f"Slicing failed: {exc}")
                st.stop()
            except Exception as exc:
                # Anything else is a slicer bug, not bad input -- show it
                # without discarding the traceback, and keep the app alive.
                st.error("Slicing failed with an unexpected internal error. No G-code was produced.")
                st.exception(exc)
                st.stop()

            for note in result.info:
                st.info(note)
            st.session_state.last_slice = {
                "trajectory": result.trajectory,
                "target_shapes": result.target_shapes,
                "gcode_output": result.gcode,
                "val_result": result.validation,
                "max_z": result.max_z,
                "layers": result.config.layers,
            }

    if st.session_state.last_slice is None:
        return

    results.render_results(st.session_state.last_slice)


if __name__ == "__main__":
    main()
