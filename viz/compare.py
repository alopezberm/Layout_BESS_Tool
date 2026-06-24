"""Text comparison table across scenarios (notebook/CLI friendly)."""

from core.sizing import size_system


def print_comparison(config, *results):
    """Print a side-by-side metrics table. ``config`` supplies the per-unit
    MWh / MW ratings used for plant sizing; ``results`` are the dicts returned
    by ``run_bess_optimization``."""
    bess_cap = config.get("bess_unit_mwh", 5.0)
    mvs_pow  = config.get("mvs_station_mw", 2.5)

    def _sizing(m):
        return size_system(m["bess_count"], m["mvs_count"], bess_cap, mvs_pow)

    rows = [
        ("MVS Count",         lambda m: f"{m['mvs_count']}"),
        ("BESS Count",        lambda m: f"{m['bess_count']}"),
        ("Total Power (MW)",  lambda m: f"{_sizing(m)['total_mw']:.1f}"),
        ("Total Energy (MWh)",lambda m: f"{_sizing(m)['total_mwh']:.1f}"),
        ("Duration",          lambda m: f"{_sizing(m)['duration_label']}"),
        ("Fully Sat. MVS",    lambda m: f"{m['full_mvs']} / {m['mvs_count']}"),
        ("Total Cable (m)",   lambda m: f"{m['total_cable']:.1f}"),
        ("Avg Cable (m)",     lambda m: f"{m['avg_cable']:.1f}"),
        ("Max Cable (m)",     lambda m: f"{m['max_cable_used']:.1f}"),
        ("Area Saturation",   lambda m: f"{m['area_saturation_pct']:.1f}%"),
        ("Capacity Sat.",     lambda m: f"{m['capacity_saturation_pct']:.1f}%"),
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