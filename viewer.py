"""Interactive Plotly 3D toolpath viewer."""
import plotly.graph_objects as go
import streamlit as st

import machine as machine_mod

# Above this many points, Plotly's Scatter3d line trace gets sluggish to
# rotate/zoom in the browser -- decimate what's DISPLAYED (never the real
# trajectory used for G-code/kinematics/validation) down to this budget.
MAX_VIEWER_POINTS = 6000


def _decimate(seq, max_points=MAX_VIEWER_POINTS):
    n = len(seq)
    if n <= max_points:
        return seq
    step = -(-n // max_points)  # ceil division
    thinned = seq[::step]
    if thinned[-1] is not seq[-1]:
        thinned = thinned + [seq[-1]]
    return thinned


# Draw at most this many layer outlines -- a tall revolve shell can have
# hundreds of slices, and every one of them is a full polygon ring.
MAX_VIEWER_OUTLINES = 150


def _outline_coords(target_shapes):
    """Flatten every (z, polygon, layer) outline into one None-separated
    x/y/z line list, so they all render as a single cheap Plotly trace."""
    shapes = _decimate(list(target_shapes), MAX_VIEWER_OUTLINES)
    xs, ys, zs = [], [], []
    for z, geom, _ in shapes:
        if geom is None or geom.is_empty:
            continue
        polys = list(geom.geoms) if geom.geom_type == 'MultiPolygon' else [geom]
        for poly in polys:
            if poly.geom_type != 'Polygon':
                continue
            for ring in [poly.exterior, *poly.interiors]:
                rx, ry = ring.xy
                xs.extend(list(rx) + [None])
                ys.extend(list(ry) + [None])
                zs.extend([z] * len(rx) + [None])
    return xs, ys, zs


def render_3d_viewer(trajectory, target_shapes, max_z,
                     machine: machine_mod.MachineConfig = machine_mod.DEFAULT_MACHINE):
    fig = go.Figure()

    fig.add_trace(go.Mesh3d(
        x=[0, machine.bed_x, machine.bed_x, 0], y=[0, 0, machine.bed_y, machine.bed_y], z=[0, 0, 0, 0],
        color='#e5e7eb', opacity=0.4, name="Print Bed", hoverinfo="skip"
    ))

    ox, oy, oz = _outline_coords(target_shapes)
    if ox:
        fig.add_trace(go.Scatter3d(
            x=ox, y=oy, z=oz, mode='lines',
            line=dict(color='#94a3b8', width=2, dash='dot'),
            name="Target Boundary", hoverinfo="skip"
        ))

    if trajectory:
        display_traj = _decimate(trajectory)
        if len(display_traj) < len(trajectory):
            st.caption(
                f"Displaying {len(display_traj):,} of {len(trajectory):,} points "
                "(thinned for viewer responsiveness -- the exported G-code keeps full resolution)."
            )
        xs = [pt['x'] for pt in display_traj]
        ys = [pt['y'] for pt in display_traj]
        zs = [pt['z'] for pt in display_traj]
        vs = [pt['v'] for pt in display_traj]

        fig.add_trace(go.Scatter3d(
            x=xs, y=ys, z=zs, mode='lines',
            line=dict(
                color=vs, colorscale='Turbo', width=6,
                colorbar=dict(title="Speed (mm/s)", len=0.6, thickness=15, x=1.0)
            ),
            name="Continuous Fiber Path"
        ))

        fig.add_trace(go.Scatter3d(
            x=[xs[-1]], y=[ys[-1]], z=[zs[-1]], mode='markers',
            marker=dict(color='#ec4899', size=10, symbol='circle'), name="Nozzle Indicator"
        ))

        fig.add_trace(go.Scatter3d(
            x=[xs[0]], y=[ys[0]], z=[zs[0]], mode='markers',
            marker=dict(color='#10b981', size=6, symbol='diamond'), name="Start Anchor"
        ))

    camera = dict(up=dict(x=0, y=0, z=1), center=dict(x=0, y=0, z=0), eye=dict(x=0, y=-2.0, z=0.5))

    fig.update_layout(
        scene_camera=camera,
        scene=dict(
            xaxis=dict(range=[0, machine.bed_x], title='X Axis (mm)', backgroundcolor="white"),
            yaxis=dict(range=[0, machine.bed_y], title='Y Axis (mm)', backgroundcolor="white"),
            zaxis=dict(range=[0, max(15, max_z + 5)], title='Z Axis (mm)', backgroundcolor="#f9fafb"),
            aspectmode='manual', aspectratio=dict(x=1.8, y=2.2, z=0.8)
        ),
        margin=dict(l=0, r=0, b=0, t=10),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )
    st.plotly_chart(fig, use_container_width=True, height=700)
