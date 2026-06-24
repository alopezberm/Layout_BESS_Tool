"""Top-level optimization entry points.

The primary layout engine is the row/shelf packer (``core.packing.run_row_packing``,
re-exported here for convenience). This module additionally hosts the co-located /
paired-MVS scenario, which reuses the validated baseline placement helpers
(``_grow_cluster`` / ``_stragglers_pass``) around facility-located hub pads.
"""

from .geometry import create_site, prepare_site, create_candidate_grid
from .placement import _grow_cluster, _stragglers_pass
from .colocation import place_clustered_hubs, _optimal_reassign_hub_balanced
from .metrics import compute_metrics, compute_hub_metrics
from .packing import run_row_packing

# Single packing profile for the co-located BESS growth (relaxed adjacency,
# straggler fill, cap taken from the config). Replaces the old multi-mode table.
_CO_PROFILE = {
    "alignment_weight":  0.0,
    "require_adjacency": False,
    "do_stragglers":     True,
}


def run_colocated_optimization(config, verbose=False):
    """Co-Located / Paired MVS scenario: facility-located hub pads seeded BEFORE
    the BESS packing, then hub-balanced cable assignment. The BESS packing in
    between is the unmodified baseline machinery."""
    site_raw = create_site(config["site_vertices"])
    site, non_buildable = prepare_site(site_raw, config)
    grid = create_candidate_grid(site, config["grid_resolution"])

    bess_eq = config["equipment"]["BESS"]
    max_ratio = config["max_bess_per_mvs"]
    eff_cable = config.get("max_cable_length", 25)

    co = config.get("colocation", {})
    balance_tol = int(co.get("balance_tolerance", 1))

    # Stage 1 — hub facility-location + shared-pad snapping (BEFORE BESS exist)
    mvs_list, placed, avail = place_clustered_hubs(site, non_buildable, grid, config)

    # Stage 2 — baseline BESS packing around the seeded hubs
    bess_list = []
    for mvs in mvs_list:
        added, avail = _grow_cluster(
            mvs, avail, site, non_buildable, placed, bess_eq,
            max_ratio, eff_cable,
            _CO_PROFILE["alignment_weight"], _CO_PROFILE["require_adjacency"],
        )
        bess_list.extend(added)

    if _CO_PROFILE["do_stragglers"] and mvs_list:
        avail = _stragglers_pass(
            avail, site, non_buildable, placed, mvs_list, bess_list,
            bess_eq, max_ratio, eff_cable,
        )

    # Stage 3 — hub-balanced Hungarian cable assignment
    if mvs_list and bess_list:
        mvs_list, bess_list = _optimal_reassign_hub_balanced(
            mvs_list, bess_list, eff_cable, max_ratio, balance_tol,
        )

    metrics = compute_metrics(site, non_buildable, mvs_list, bess_list, max_ratio)
    hub_metrics = compute_hub_metrics(mvs_list, config)

    if verbose:
        print("\n----- CO-LOCATED -----")
        print(f"Hubs placed        : {hub_metrics['hub_count']}")
        print(f"MVS units placed   : {metrics['mvs_count']}")
        print(f"BESS units placed  : {metrics['bess_count']}")
        print(f"Avg BESS / hub     : {hub_metrics['avg_bess_per_hub']:.1f}")
        print(f"Hub balance index  : {hub_metrics['balance_index']:.2f}")
        print(f"Foundation saving  : {hub_metrics['foundation_reduction_pct']:.1f}%")
        print(f"Total cable        : {metrics['total_cable']:.1f} m")

    return {
        "mode":          "colocated",
        "site":          site,
        "non_buildable": non_buildable,
        "mvs_list":      mvs_list,
        "bess_list":     bess_list,
        "metrics":       metrics,
        "hub_metrics":   hub_metrics,
    }