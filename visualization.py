import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from engine import create_clearance_polygon, total_cable_length

DEFAULT_STANDALONE_FIGSIZE = (10, 14)
DEFAULT_PANEL_FIGSIZE      = (7, 10)

def plot_layout(site, non_buildable, mvs_list, bess_list, config,
                ax=None, title=None, figsize=None):
    bess_cl_cfg = config["equipment"]["BESS"]["clearance"]
    mvs_cl_cfg  = config["equipment"]["MVS"]["clearance"]

    n   = len(mvs_list)
    tab = plt.cm.tab10.colors if n <= 10 else plt.cm.tab20.colors
    colors = [tab[i % len(tab)] for i in range(n)]

    standalone = ax is None
    if standalone:
        fig, ax = plt.subplots(figsize=figsize or DEFAULT_STANDALONE_FIGSIZE)

    if not site.is_empty:
        x, y = site.exterior.xy
        ax.plot(x, y, color="black", linewidth=2)

    for zone in non_buildable:
        if not zone.is_empty:
            x, y = zone.exterior.xy
            ax.fill(x, y, color="gold", alpha=0.45)
            ax.plot(x, y, color="goldenrod", linewidth=1)

    legend_handles = [mpatches.Patch(color="gold", alpha=0.45, label="Non-buildable zone")]

    for i, (mvs, col) in enumerate(zip(mvs_list, colors)):
        r, g, b = col[:3]
        dark    = (r * 0.50, g * 0.50, b * 0.50)

        if mvs.get("rotated", False):
            r_mvs_cl_cfg = {
                "front": mvs_cl_cfg["left"],
                "back": mvs_cl_cfg["right"],
                "left": mvs_cl_cfg["back"],
                "right": mvs_cl_cfg["front"],
            }
        else:
            r_mvs_cl_cfg = mvs_cl_cfg
        mvs_cl_poly = create_clearance_polygon(mvs["footprint"], r_mvs_cl_cfg)
        x, y = mvs_cl_poly.exterior.xy
        ax.fill(x, y, color=col, alpha=0.12, zorder=1)
        ax.plot(x, y, color=col, linewidth=0.8, linestyle="--", alpha=0.6, zorder=1)

        for bess in mvs["assigned_bess"]:
            ax.plot(
                [bess["footprint"].centroid.x, mvs["footprint"].centroid.x],
                [bess["footprint"].centroid.y, mvs["footprint"].centroid.y],
                color=col, linewidth=1.2, alpha=0.85, zorder=2,
            )
            if bess.get("rotated", False):
                r_bess_cl_cfg = {
                    "front": bess_cl_cfg["left"],
                    "back": bess_cl_cfg["right"],
                    "left": bess_cl_cfg["back"],
                    "right": bess_cl_cfg["front"],
                }
            else:
                r_bess_cl_cfg = bess_cl_cfg
            bess_cl_poly = create_clearance_polygon(bess["footprint"], r_bess_cl_cfg)
            x, y = bess_cl_poly.exterior.xy
            ax.fill(x, y, color=col, alpha=0.10, zorder=1)
            ax.plot(x, y, color=col, linewidth=0.5, linestyle="--", alpha=0.4, zorder=1)

            x, y = bess["footprint"].exterior.xy
            ax.fill(x, y, color=col, alpha=0.75, zorder=3)
            ax.plot(x, y, color="black", linewidth=0.5, zorder=3)

        x, y = mvs["footprint"].exterior.xy
        ax.fill(x, y, color=dark, alpha=0.95, zorder=4)
        ax.plot(x, y, color="black", linewidth=0.8, zorder=4)
        label_fs = 8 if standalone else 6
        ax.text(
            mvs["footprint"].centroid.x, mvs["footprint"].centroid.y,
            f"M{i + 1}", ha="center", va="center",
            fontsize=label_fs, color="white", fontweight="bold", zorder=5,
        )

        legend_handles.append(mpatches.Patch(
            color=col, alpha=0.80,
            label=f"Cluster {i + 1} — M{i + 1} + {len(mvs['assigned_bess'])} BESS",
        ))

    if title is None:
        cable        = total_cable_length(mvs_list)
        total_bess_n = sum(len(m["assigned_bess"]) for m in mvs_list)
        if total_bess_n == 0:
            title = "BESS Layout — No equipment placed"
        else:
            title = (
                f"BESS Layout — {n} clusters | {total_bess_n} BESS | "
                f"Cable {cable:.0f} m (avg {cable / total_bess_n:.1f} m/BESS)"
            )

    legend_fs = 8 if standalone else 6
    title_fs  = 13 if standalone else 11
    ax.legend(handles=legend_handles, loc="upper right", fontsize=legend_fs)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=title_fs, fontweight="bold")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.grid(True, linestyle="--", alpha=0.3)

    if standalone:
        plt.tight_layout()

def _scenario_title(res):
    m = res["metrics"]
    return (
        f"{res['mode'].replace('_', ' ').upper()} — "
        f"{m['mvs_count']} MVS | {m['bess_count']} BESS | "
        f"Cable {m['total_cable']:.0f} m | "
        f"Area sat {m['area_saturation_pct']:.1f}%"
    )

def _grid_shape(n):
    if n <= 3:
        return 1, n
    cols = 2
    rows = (n + cols - 1) // cols
    return rows, cols

def plot_comparison(*results, config, panel_size=DEFAULT_PANEL_FIGSIZE):
    n = len(results)
    rows, cols = _grid_shape(n)
    fig, axes = plt.subplots(rows, cols, figsize=(panel_size[0] * cols, panel_size[1] * rows))
    if hasattr(axes, "flatten"):
        axes_flat = list(axes.flatten())
    else:
        axes_flat = [axes]
    for ax, res in zip(axes_flat, results):
        plot_layout(
            res["site"], res["non_buildable"],
            res["mvs_list"], res["bess_list"],
            config, ax=ax, title=_scenario_title(res),
        )
    for ax in axes_flat[len(results):]:
        ax.axis("off")
    plt.tight_layout()
    plt.show()

def plot_individual(result, config, figsize=DEFAULT_STANDALONE_FIGSIZE):
    plot_layout(
        result["site"], result["non_buildable"],
        result["mvs_list"], result["bess_list"],
        config,
        title=_scenario_title(result),
        figsize=figsize,
    )
    plt.show()

def plot_all_standalone(*results, config, figsize=DEFAULT_STANDALONE_FIGSIZE):
    for res in results:
        plot_individual(res, config, figsize=figsize)

import plotly.graph_objects as go
import plotly.express as px

def plot_layout_plotly(site, non_buildable, mvs_list, bess_list, config, title=None):
    fig = go.Figure()

    if not site.is_empty:
        x, y = site.exterior.xy
        fig.add_trace(go.Scatter(x=list(x), y=list(y), mode='lines', line=dict(color='black', width=2), name="Site Boundary", hoverinfo='skip'))

    for zone in non_buildable:
        if not zone.is_empty:
            x, y = zone.exterior.xy
            fig.add_trace(go.Scatter(x=list(x), y=list(y), fill='toself', fillcolor='rgba(255, 215, 0, 0.45)', line=dict(color='goldenrod', width=1), name="Non-buildable", hoverinfo='skip'))

    colors = px.colors.qualitative.Plotly
    mvs_map = {id(m): f"M{i+1}" for i, m in enumerate(mvs_list)}
    
    for i, mvs in enumerate(mvs_list):
        col = colors[i % len(colors)]
        mvs_id = mvs.get("id", f"M{i+1}")
        
        # BESS and cables
        for j, bess in enumerate(mvs["assigned_bess"]):
            bess_id = bess.get("id", f"B{j+1}_(M{i+1})")
            
            # Cable
            fig.add_trace(go.Scatter(
                x=[bess["footprint"].centroid.x, mvs["footprint"].centroid.x],
                y=[bess["footprint"].centroid.y, mvs["footprint"].centroid.y],
                mode='lines', line=dict(color=col, width=1.5), opacity=0.7, showlegend=False, hoverinfo='skip'
            ))
            
            # BESS Footprint
            bx, by = bess["footprint"].exterior.xy
            rot_str = "90°" if bess.get("rotated", False) else "0°"
            hover_text = f"ID: {bess_id}<br>Type: BESS<br>X: {bess['footprint'].bounds[0]:.1f}<br>Y: {bess['footprint'].bounds[1]:.1f}<br>Rotated: {rot_str}<br>Assigned MVS: {mvs_id}"
            
            fig.add_trace(go.Scatter(
                x=list(bx), y=list(by), fill='toself', fillcolor=col, opacity=0.8, line=dict(color='black', width=1),
                name=f"BESS ({mvs_id})", text=hover_text, hoverinfo='text', showlegend=False
            ))
            
        # MVS Footprint
        mx, my = mvs["footprint"].exterior.xy
        rot_str = "90°" if mvs.get("rotated", False) else "0°"
        hover_text = f"ID: {mvs_id}<br>Type: MVS<br>X: {mvs['footprint'].bounds[0]:.1f}<br>Y: {mvs['footprint'].bounds[1]:.1f}<br>Rotated: {rot_str}<br>Assigned BESS: {len(mvs['assigned_bess'])}"
        
        fig.add_trace(go.Scatter(
            x=list(mx), y=list(my), fill='toself', fillcolor=col, opacity=1.0, line=dict(color='black', width=2),
            name=f"MVS {mvs_id}", text=hover_text, hoverinfo='text'
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

def print_comparison(config, *results):
    bess_cap = config.get("bess_unit_mwh", 5.0)
    mvs_pow  = config.get("mvs_station_mw", 2.5)

    rows = [
        ("MVS Count",       lambda m: f"{m['mvs_count']}"),
        ("BESS Count",      lambda m: f"{m['bess_count']}"),
        ("Total Power (MW)",lambda m: f"{m['mvs_count'] * mvs_pow:.1f}"),
        ("Total Energy (MWh)",lambda m: f"{m['bess_count'] * bess_cap:.1f}"),
        ("Fully Sat. MVS",  lambda m: f"{m['full_mvs']} / {m['mvs_count']}"),
        ("Total Cable (m)", lambda m: f"{m['total_cable']:.1f}"),
        ("Avg Cable (m)",   lambda m: f"{m['avg_cable']:.1f}"),
        ("Max Cable (m)",   lambda m: f"{m['max_cable_used']:.1f}"),
        ("Area Saturation", lambda m: f"{m['area_saturation_pct']:.1f}%"),
        ("Capacity Sat.",   lambda m: f"{m['capacity_saturation_pct']:.1f}%"),
    ]
    pretty = {
        "conservative":     "Conservative",
        "aggressive":       "Aggressive",
        "ultra_aggressive": "Ultra-Aggressive",
        "hyper_pack":       "Hyper-Pack",
    }
    headers = ["Metric"] + [pretty.get(r["mode"], r["mode"]) for r in results]
    col_w = 20
    sep = "─" * (col_w * len(headers))

    print()
    print(sep)
    print("".join(h.ljust(col_w) for h in headers))
    print(sep)
    for label, fn in rows:
        cells = [label] + [fn(r["metrics"]) for r in results]
        print("".join(c.ljust(col_w) for c in cells))
    print(sep)
