"""Static matplotlib renderers: single layout, panel comparison, standalone."""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from core.geometry import create_clearance_polygon
from core.metrics import total_cable_length
from core.sizing import size_system

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


def _scenario_title(res, config=None):
    m = res["metrics"]
    title = (
        f"{res['mode'].replace('_', ' ').upper()} — "
        f"{m['mvs_count']} MVS | {m['bess_count']} BESS | "
        f"Cable {m['total_cable']:.0f} m | "
        f"Area sat {m['area_saturation_pct']:.1f}%"
    )
    if config is not None:
        s = size_system(
            m["bess_count"], m["mvs_count"],
            config.get("bess_unit_mwh", 5.0), config.get("mvs_station_mw", 2.5),
        )
        title += (
            f"\n{s['total_mw']:.1f} MW | {s['total_mwh']:.1f} MWh | "
            f"{s['duration_label']} duration"
        )
    return title


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
            config, ax=ax, title=_scenario_title(res, config),
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
        title=_scenario_title(result, config),
        figsize=figsize,
    )
    plt.show()


def plot_all_standalone(*results, config, figsize=DEFAULT_STANDALONE_FIGSIZE):
    for res in results:
        plot_individual(res, config, figsize=figsize)