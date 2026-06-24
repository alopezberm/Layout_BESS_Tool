"""Top-level optimization entry points and the mode-strategy table.

These orchestrators are what the notebook and the app both call. They own no
geometry or scoring logic themselves — they wire the stages in placement.py /
colocation.py / metrics.py together.
"""

from .geometry import create_site, prepare_site, create_candidate_grid
from .placement import place_clusters, _grow_cluster, _stragglers_pass
from .colocation import place_clustered_hubs, _optimal_reassign_hub_balanced
from .metrics import compute_metrics, compute_hub_metrics

MODE_PROFILES = {
    "conservative": {
        "alignment_weight":       8.0,
        "mvs_y_band_bonus":       0.85,
        "require_adjacency":      True,
        "do_stragglers":          False,
        "cable_cap_override":     None,
        "hungarian_cap_override": None,
        "fine_grid_resolution":   None,
    },
    "aggressive": {
        "alignment_weight":       0.0,
        "mvs_y_band_bonus":       1.0,
        "require_adjacency":      False,
        "do_stragglers":          True,
        "cable_cap_override":     None,
        "hungarian_cap_override": None,
        "fine_grid_resolution":   None,
    },
    "ultra_aggressive": {
        "alignment_weight":       0.0,
        "mvs_y_band_bonus":       1.0,
        "require_adjacency":      False,
        "do_stragglers":          True,
        "cable_cap_override":     0,
        "hungarian_cap_override": 0,
        "fine_grid_resolution":   None,
    },
    "hyper_pack": {
        "alignment_weight":       0.0,
        "mvs_y_band_bonus":       1.0,
        "require_adjacency":      False,
        "do_stragglers":          True,
        "cable_cap_override":     0,
        "hungarian_cap_override": 0,
        "fine_grid_resolution":   0.5,
    },
}


def run_bess_optimization(config, mode="aggressive", verbose=True):
    if mode not in MODE_PROFILES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {list(MODE_PROFILES)}")
    profile = MODE_PROFILES[mode]

    site_raw = create_site(config["site_vertices"])
    site, non_buildable = prepare_site(site_raw, config)
    grid = create_candidate_grid(site, config["grid_resolution"])

    mvs_list, bess_list, dropped = place_clusters(site, non_buildable, grid, config, profile)
    metrics = compute_metrics(site, non_buildable, mvs_list, bess_list,
                              config["max_bess_per_mvs"])
    metrics["dropped_bess"] = dropped

    if verbose:
        print(f"\n----- {mode.upper()} -----")
        print(f"MVS units placed   : {metrics['mvs_count']}")
        print(f"BESS units placed  : {metrics['bess_count']}")
        print(f"Fully sat. MVS     : {metrics['full_mvs']} / {metrics['mvs_count']}")
        print(f"Total cable        : {metrics['total_cable']:.1f} m")
        print(f"Avg cable / BESS   : {metrics['avg_cable']:.1f} m")
        print(f"Max cable run      : {metrics['max_cable_used']:.1f} m")
        print(f"Area saturation    : {metrics['area_saturation_pct']:.1f}%")
        print(f"Capacity saturation: {metrics['capacity_saturation_pct']:.1f}%")
        if dropped:
            print(f"WARNING: {dropped} placed BESS left unassigned "
                  f"(no MVS slot within the cable cap).")

    return {
        "mode":          mode,
        "site":          site,
        "non_buildable": non_buildable,
        "mvs_list":      mvs_list,
        "bess_list":     bess_list,
        "metrics":       metrics,
    }


def run_colocated_optimization(config, mode="aggressive", verbose=False):
    """Co-Located / Paired MVS scenario. Mirrors ``run_bess_optimization`` but
    swaps the MVS seeding stage for hub facility-location and the final
    assignment for the hub-balanced variant. The BESS packing in between is the
    unmodified baseline machinery."""
    profile = MODE_PROFILES.get(mode, MODE_PROFILES["aggressive"])

    site_raw = create_site(config["site_vertices"])
    site, non_buildable = prepare_site(site_raw, config)
    grid = create_candidate_grid(site, config["grid_resolution"])

    bess_eq   = config["equipment"]["BESS"]
    max_ratio = config["max_bess_per_mvs"]
    base_cable = config.get("max_cable_length", 25)
    eff_cable     = profile["cable_cap_override"]     if profile["cable_cap_override"]     is not None else base_cable
    eff_hungarian = profile["hungarian_cap_override"] if profile["hungarian_cap_override"] is not None else base_cable

    co = config.get("colocation", {})
    balance_tol = int(co.get("balance_tolerance", 1))

    # Stage 1 — hub facility-location + shared-pad snapping (BEFORE BESS exist)
    mvs_list, placed, avail = place_clustered_hubs(site, non_buildable, grid, config)

    # Stage 2 — unmodified baseline BESS packing around the seeded hubs
    bess_list = []
    for mvs in mvs_list:
        added, avail = _grow_cluster(
            mvs, avail, site, non_buildable, placed, bess_eq,
            max_ratio, eff_cable,
            profile["alignment_weight"], profile["require_adjacency"],
        )
        bess_list.extend(added)

    if profile["do_stragglers"] and mvs_list:
        avail = _stragglers_pass(
            avail, site, non_buildable, placed, mvs_list, bess_list,
            bess_eq, max_ratio, eff_cable,
        )

    # Stage 3 — hub-balanced Hungarian cable assignment
    if mvs_list and bess_list:
        mvs_list, bess_list = _optimal_reassign_hub_balanced(
            mvs_list, bess_list, eff_hungarian, max_ratio, balance_tol,
        )

    metrics     = compute_metrics(site, non_buildable, mvs_list, bess_list, max_ratio)
    hub_metrics = compute_hub_metrics(mvs_list, config)

    if verbose:
        print(f"\n----- CO-LOCATED ({mode.upper()}) -----")
        print(f"Hubs placed        : {hub_metrics['hub_count']}")
        print(f"MVS units placed   : {metrics['mvs_count']}")
        print(f"BESS units placed  : {metrics['bess_count']}")
        print(f"Avg BESS / hub     : {hub_metrics['avg_bess_per_hub']:.1f}")
        print(f"Hub balance index  : {hub_metrics['balance_index']:.2f}")
        print(f"Foundation saving  : {hub_metrics['foundation_reduction_pct']:.1f}%")
        print(f"Total cable        : {metrics['total_cable']:.1f} m")

    return {
        "mode":          mode,
        "site":          site,
        "non_buildable": non_buildable,
        "mvs_list":      mvs_list,
        "bess_list":     bess_list,
        "metrics":       metrics,
        "hub_metrics":   hub_metrics,
    }