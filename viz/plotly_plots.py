"""Interactive Plotly renderers used by the Streamlit app and notebooks."""

import plotly.graph_objects as go


def plot_layout_plotly(site, non_buildable, mvs_list, bess_list, config, title=None):
    fig = go.Figure()

    if not site.is_empty:
        x, y = site.exterior.xy
        fig.add_trace(go.Scatter(x=list(x), y=list(y), mode='lines', line=dict(color='black', width=2), name="Site Boundary", hoverinfo='skip'))

    for zone in non_buildable:
        if not zone.is_empty:
            x, y = zone.exterior.xy
            fig.add_trace(go.Scatter(x=list(x), y=list(y), fill='toself', fillcolor='rgba(255, 215, 0, 0.45)', line=dict(color='goldenrod', width=1), name="Non-buildable", hoverinfo='skip'))

    for i, mvs in enumerate(mvs_list):
        mvs_id = mvs.get("id", f"M{i+1}")

        # MVS Clearance Shadow
        if "clearance_zone" in mvs and not mvs["clearance_zone"].is_empty:
            cx, cy = mvs["clearance_zone"].exterior.xy
            fig.add_trace(go.Scatter(
                x=list(cx), y=list(cy), fill='toself', fillcolor='rgba(150, 150, 150, 0.1)',
                line=dict(color='rgba(150, 150, 150, 0.5)', width=1, dash='dash'),
                hoverinfo='skip', showlegend=False
            ))

        # BESS and cables
        for j, bess in enumerate(mvs["assigned_bess"]):
            bess_id = bess.get("id", f"B{j+1}_(M{i+1})")

            # BESS Clearance Shadow
            if "clearance_zone" in bess and not bess["clearance_zone"].is_empty:
                cx, cy = bess["clearance_zone"].exterior.xy
                fig.add_trace(go.Scatter(
                    x=list(cx), y=list(cy), fill='toself', fillcolor='rgba(150, 150, 150, 0.1)',
                    line=dict(color='rgba(150, 150, 150, 0.5)', width=1, dash='dash'),
                    hoverinfo='skip', showlegend=False
                ))

            # Cable
            fig.add_trace(go.Scatter(
                x=[bess["footprint"].centroid.x, mvs["footprint"].centroid.x],
                y=[bess["footprint"].centroid.y, mvs["footprint"].centroid.y],
                mode='lines', line=dict(color='#757575', width=1.5), opacity=0.7, showlegend=False, hoverinfo='skip'
            ))

            # BESS Footprint
            bx, by = bess["footprint"].exterior.xy
            rot_str = "90°" if bess.get("rotated", False) else "0°"
            hover_text = f"ID: {bess_id}<br>Type: BESS<br>X: {bess['footprint'].bounds[0]:.1f}<br>Y: {bess['footprint'].bounds[1]:.1f}<br>Rotated: {rot_str}<br>Assigned MVS: {mvs_id}"

            fig.add_trace(go.Scatter(
                x=list(bx), y=list(by), fill='toself', fillcolor='#0D47A1', opacity=0.9, line=dict(color='#90CAF9', width=1.5),
                name=f"BESS ({mvs_id})", text=hover_text, hoverinfo='text', showlegend=False, customdata=[bess_id]*len(bx)
            ))

        # MVS Footprint
        mx, my = mvs["footprint"].exterior.xy
        rot_str = "90°" if mvs.get("rotated", False) else "0°"
        hover_text = f"ID: {mvs_id}<br>Type: MVS<br>X: {mvs['footprint'].bounds[0]:.1f}<br>Y: {mvs['footprint'].bounds[1]:.1f}<br>Rotated: {rot_str}<br>Assigned BESS: {len(mvs['assigned_bess'])}"

        fig.add_trace(go.Scatter(
            x=list(mx), y=list(my), fill='toself', fillcolor='#E65100', opacity=1.0, line=dict(color='black', width=2),
            name=f"MVS {mvs_id}", text=hover_text, hoverinfo='text', customdata=[mvs_id]*len(mx)
        ))

    fig.update_layout(
        title=title or "BESS Layout Visualization",
        xaxis_title="X (m)", yaxis_title="Y (m)",
        yaxis=dict(scaleanchor="x", scaleratio=1),
        plot_bgcolor='white',
        margin=dict(l=20, r=20, t=40, b=20),
        height=700
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')

    return fig


def plot_layout_plotly_hubs(site, non_buildable, mvs_list, bess_list, config, title=None):
    """Co-located variant of plot_layout_plotly. Renders the standard interactive
    layout, then overlays a dashed outline + label around each shared foundation
    pad (hub) so the clustering is visually explicit."""
    fig = plot_layout_plotly(site, non_buildable, mvs_list, bess_list, config,
                             title=title or "Co-Located / Paired MVS Layout")

    hubs = {}
    for m in mvs_list:
        hid = m.get("hub_id")
        if hid:
            hubs.setdefault(hid, []).append(m)

    margin = 0.6
    for hid, members in hubs.items():
        if len(members) < 2:
            continue
        minx = min(m["footprint"].bounds[0] for m in members) - margin
        miny = min(m["footprint"].bounds[1] for m in members) - margin
        maxx = max(m["footprint"].bounds[2] for m in members) + margin
        maxy = max(m["footprint"].bounds[3] for m in members) + margin

        fig.add_trace(go.Scatter(
            x=[minx, maxx, maxx, minx, minx],
            y=[miny, miny, maxy, maxy, miny],
            mode='lines',
            line=dict(color='#00897B', width=2.5, dash='dot'),
            name=f"Shared Pad {hid}",
            hoverinfo='skip',
        ))
        fig.add_trace(go.Scatter(
            x=[(minx + maxx) / 2.0], y=[maxy + margin],
            mode='text', text=[f"⬡ {hid} ({len(members)} MVS)"],
            textfont=dict(color='#00695C', size=12),
            showlegend=False, hoverinfo='skip',
        ))

    return fig