"""Layout KPIs: cable totals, area/capacity saturation and co-located hub
balance metrics. Pure functions over placed-equipment dicts.
"""

import numpy as np


def total_cable_length(mvs_list):
    total = 0.0
    for mvs in mvs_list:
        mx, my = mvs["footprint"].centroid.x, mvs["footprint"].centroid.y
        for b in mvs["assigned_bess"]:
            bx, by = b["footprint"].centroid.x, b["footprint"].centroid.y
            total += np.hypot(bx - mx, by - my)
    return total


def compute_metrics(site, non_buildable, mvs_list, bess_list, max_ratio):
    n_mvs  = len(mvs_list)
    n_bess = len(bess_list)
    cable  = total_cable_length(mvs_list)
    avg    = cable / n_bess if n_bess else 0.0
    full   = sum(1 for m in mvs_list if len(m["assigned_bess"]) == max_ratio)

    max_cable = 0.0
    for m in mvs_list:
        mx, my = m["footprint"].centroid.x, m["footprint"].centroid.y
        for b in m["assigned_bess"]:
            bx, by = b["footprint"].centroid.x, b["footprint"].centroid.y
            d = float(np.hypot(bx - mx, by - my))
            if d > max_cable:
                max_cable = d

    buildable = site.area
    for nb in non_buildable:
        buildable -= nb.intersection(site).area

    eq_area = (
        sum(m["footprint"].area for m in mvs_list)
        + sum(b["footprint"].area for b in bess_list)
    )
    area_sat = (100 * eq_area / buildable) if buildable > 0 else 0.0
    cap_sat  = (100 * n_bess / (n_mvs * max_ratio)) if n_mvs else 0.0

    return {
        "mvs_count":               n_mvs,
        "bess_count":              n_bess,
        "max_cap":                 max_ratio,
        "full_mvs":                full,
        "total_cable":             cable,
        "avg_cable":               avg,
        "max_cable_used":          max_cable,
        "buildable_area":          buildable,
        "equipment_area":          eq_area,
        "area_saturation_pct":     area_sat,
        "capacity_saturation_pct": cap_sat,
    }


def compute_hub_metrics(mvs_list, config=None):
    """Civil-works / balance KPIs for the co-located scenario, derived purely
    from the ``hub_id`` tags on the MVS dicts."""
    hubs = {}
    for m in mvs_list:
        hid = m.get("hub_id")
        if hid:
            hubs.setdefault(hid, []).append(m)

    n_hubs = len(hubs)
    n_mvs  = len(mvs_list)
    paired = sum(len(v) for v in hubs.values())

    imbalances, hub_fill = [], []
    for members in hubs.values():
        fills = [len(m["assigned_bess"]) for m in members]
        imbalances.append((max(fills) - min(fills)) if len(members) > 1 else 0)
        hub_fill.append(sum(fills))

    avg_bess_per_hub = (sum(hub_fill) / n_hubs) if n_hubs else 0.0
    balance_index    = (sum(imbalances) / len(imbalances)) if imbalances else 0.0
    worst_imbalance  = max(imbalances) if imbalances else 0
    foundation_reduction = (100 * (n_mvs - n_hubs) / n_mvs) if n_mvs else 0.0

    return {
        "hub_count":                   n_hubs,
        "paired_mvs_count":            paired,
        "standalone_mvs_count":        n_mvs - paired,
        "avg_bess_per_hub":            avg_bess_per_hub,
        "balance_index":               balance_index,
        "worst_hub_imbalance":         worst_imbalance,
        "shared_foundations":          n_hubs,
        "standalone_foundation_equiv": n_mvs,
        "foundation_reduction_pct":    foundation_reduction,
    }