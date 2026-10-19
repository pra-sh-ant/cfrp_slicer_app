"""Renders the sliced result: validation banner, 3D visualizer tab, G-code tab."""
import streamlit as st
from viewer import render_3d_viewer


def render_validation_banner(val_data):
    if val_data["status"] == "Fail":
        st.error(" \n\n ".join(val_data["messages"]))
    elif val_data["status"] == "Warning":
        st.warning(" \n\n ".join(val_data["messages"]))
    else:
        st.success(val_data["messages"][0])


def render_research_dashboard(ls):
    traj = ls["trajectory"]
    if not traj:
        st.info("No trajectory generated yet.")
        return

    x_vals = [pt["x"] for pt in traj]
    y_vals = [pt["y"] for pt in traj]
    z_vals = [pt["z"] for pt in traj]

    cols = st.columns(5)
    metrics = [
        ("Points", len(traj)),
        ("X span", f"{min(x_vals):.1f}–{max(x_vals):.1f} mm"),
        ("Y span", f"{min(y_vals):.1f}–{max(y_vals):.1f} mm"),
        ("Z span", f"{min(z_vals):.1f}–{max(z_vals):.1f} mm"),
        ("Layers", max(pt.get("layer", 1) for pt in traj)),
    ]
    for col, (label, value) in zip(cols, metrics, strict=False):
        col.metric(label, value)

    st.caption("Research summary: use this dashboard to compare shell geometry, layer coverage, build height, and spatial footprint before exporting G-code.")


def render_visualizer_tab(ls):
    st.markdown("### 🎬 Visualization Controls")

    display_traj = ls["trajectory"]
    display_shapes = ls["target_shapes"]

    max_layer = max([pt.get('layer', 1) for pt in display_traj]) if display_traj else 1

    c_mode, c_slider = st.columns([1, 2])

    with c_mode:
        view_mode = st.radio("Display Mode", ["Full Model", "Layer Isolator", "Toolpath Playback"], horizontal=False)

    with c_slider:
        if view_mode == "Layer Isolator":
            sel_layer = st.slider("Inspect Specific Layer", 1, max(1, max_layer), 1)
            display_traj = [pt for pt in display_traj if pt.get('layer', 1) == sel_layer]
            display_shapes = [s for s in display_shapes if len(s) > 2 and s[2] == sel_layer]

        elif view_mode == "Toolpath Playback":
            progress = st.slider("Scrub Draw Progress", 0.0, 100.0, 100.0, step=0.5, format="%.1f%%")
            cutoff = int(len(display_traj) * (progress / 100.0))
            display_traj = display_traj[:cutoff]

            if display_traj:
                cur_layer = display_traj[-1].get('layer', 1)
                display_shapes = [s for s in display_shapes if len(s) > 2 and s[2] <= cur_layer]
            else:
                display_shapes = []
        else:
            st.info("Showing complete continuous fiber layout.")

    st.markdown("---")

    render_3d_viewer(display_traj, display_shapes, ls["max_z"])


def render_gcode_tab(ls):
    st.download_button(
        "💾 Export Laminate .gcode", ls["gcode_output"],
        f"CFRP_Laminate_{ls['layers']}L.gcode", type="primary"
    )
    st.code(ls["gcode_output"], language="gcode", line_numbers=True)


def render_results(ls):
    render_validation_banner(ls["val_result"])

    tab1, tab2, tab3 = st.tabs(["📊 Research Dashboard", "👁️ Interactive Toolpath Visualizer", "📝 Compiled G-Code"])
    with tab1:
        render_research_dashboard(ls)
    with tab2:
        render_visualizer_tab(ls)
    with tab3:
        render_gcode_tab(ls)
