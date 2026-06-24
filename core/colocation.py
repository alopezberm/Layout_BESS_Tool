"""Co-located / paired MVS optimization — a parallel scenario engine that seeds
MVS stations onto shared foundation pads (paired or grouped into central power
hubs) BEFORE the BESS packing routine runs. It reuses the baseline primitives
(``is_valid_placement``, ``_grow_cluster``, ``_stragglers_pass``,
``_prune_avail``) unchanged. Only two stages carry new logic: (1) hub location,
solved as a capacitated continuous facility-location problem via weighted Lloyd
relaxation with a Weiszfeld geometric-median update (minimises trenching length,
not squared distance); and (2) intra-hub cable balancing, solved with a
Hungarian assignment wrapped in a bounded Lagrangian re-weighting loop.
"""

import numpy as np
from shapely.geometry import Point as _Point

from .geometry import (
    create_equipment_polygon,
    create_clearance_polygon,
    is_valid_placement,
    get_oriented_dimensions,
    ORIENTATIONS,
)
from .placement import _prune_avail


def _geometric_median(pts, weights, max_iter=100, tol=1e-5):
    """Weiszfeld iteration: the point minimising the WEIGHTED SUM of Euclidean
    distances to ``pts``. This is the trenching-length-optimal hub location, as
    opposed to the centroid (which minimises squared distance / I^2R loss)."""
    y = np.average(pts, axis=0, weights=weights)
    for _ in range(max_iter):
        d = np.sqrt(((pts - y) ** 2).sum(axis=1))
        d = np.where(d < 1e-9, 1e-9, d)
        w = weights / d
        y_new = (pts * w[:, None]).sum(axis=0) / w.sum()
        if np.sqrt(((y_new - y) ** 2).sum()) < tol:
            return y_new
        y = y_new
    return y


def _kmeanspp_init(pts, weights, k, rng):
    """k-means++ seeding so the Lloyd relaxation avoids poor local optima."""
    n = len(pts)
    centers = [pts[int(rng.integers(n))]]
    d2 = ((pts - centers[0]) ** 2).sum(axis=1)
    for _ in range(1, k):
        probs = d2 * weights
        s = probs.sum()
        idx = int(rng.integers(n)) if s <= 0 else int(rng.choice(n, p=probs / s))
        centers.append(pts[idx])
        d2 = np.minimum(d2, ((pts - pts[idx]) ** 2).sum(axis=1))
    return np.array(centers)


def _weighted_lloyd_relaxation(pts, weights, k, max_iter=50, tol=1e-3, seed=0):
    """Lloyd's algorithm with a geometric-median (Weiszfeld) relocation step.
    Returns k continuous hub centres that minimise total expected cable run to
    the demand field, before any BESS physically exists."""
    rng = np.random.default_rng(seed)
    k = max(1, min(k, len(pts)))
    centers = _kmeanspp_init(pts, weights, k, rng)
    for _ in range(max_iter):
        dists = np.sqrt(((pts[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2))
        labels = np.argmin(dists, axis=1)
        new = []
        for c in range(k):
            mask = labels == c
            if not mask.any():
                new.append(pts[int(rng.integers(len(pts)))])
            else:
                new.append(_geometric_median(pts[mask], weights[mask]))
        new = np.array(new)
        shift = np.sqrt(((new - centers) ** 2).sum(axis=1)).max()
        centers = new
        if shift < tol:
            break
    return centers


def _determine_hub_count(buildable_area, bess_eq, max_ratio, group_size,
                         packing_efficiency=0.85):
    """Size the number of hubs k from buildable area and per-hub capacity.
    Each hub serves group_size MVS x max_ratio BESS."""
    import math
    cl = bess_eq["clearance"]
    cell = ((bess_eq["width"] + cl["left"] + cl["right"]) *
            (bess_eq["height"] + cl["front"] + cl["back"]))
    if cell <= 0:
        return 1
    est_bess = packing_efficiency * buildable_area / cell
    cap_per_hub = max(1, max_ratio * group_size)
    return max(1, math.ceil(est_bess / cap_per_hub))


def _snap_hub_to_pad(center, group_size, pad_gap, site, non_buildable, placed,
                     mvs_eq, search_radius, grid_res):
    """Feasibility repair: take a continuous (possibly infeasible) hub centre
    and find the nearest discrete, valid placement of a SHARED PAD holding
    ``group_size`` MVS laid side-by-side. Members are validated only against
    EXTERNAL ``placed`` equipment, never against their own pad siblings — this
    is what permits the tighter-than-standard-clearance co-location on one slab.
    Returns a list of MVS dicts (one per pad slot) or None if no fit is found."""
    mvs_cl = mvs_eq["clearance"]
    w, h = mvs_eq["width"], mvs_eq["height"]
    cx0, cy0 = float(center[0]), float(center[1])
    step = max(1.0, grid_res / 2.0)
    rng = np.arange(-search_radius, search_radius + 1e-9, step)
    offsets = [(dx, dy) for dx in rng for dy in rng
               if dx * dx + dy * dy <= search_radius * search_radius]
    offsets.sort(key=lambda o: o[0] * o[0] + o[1] * o[1])

    for dx, dy in offsets:
        cx, cy = cx0 + dx, cy0 + dy
        for angle in ORIENTATIONS:
            rw, rh, rcl = get_oriented_dimensions(w, h, mvs_cl, angle)
            pad_w = group_size * rw + (group_size - 1) * pad_gap
            pad_h = rh
            ax = cx - pad_w / 2.0
            ay = cy - pad_h / 2.0
            members = []
            ok = True
            for g in range(group_size):
                mx = ax + g * (rw + pad_gap)
                fp = create_equipment_polygon(mx, ay, rw, rh)
                cl = create_clearance_polygon(fp, rcl)
                if not is_valid_placement(fp, cl, site, non_buildable, placed):
                    ok = False
                    break
                members.append({
                    "type":           "MVS",
                    "footprint":      fp,
                    "clearance_zone": cl,
                    "assigned_bess":  [],
                    "angle":          angle,
                    "rotated":        angle in (90, 270),
                })
            if ok:
                return members
    return None


def place_clustered_hubs(site, non_buildable, grid, config):
    """Stage-1 of the co-located pipeline: locate optimal hub positions inside
    the (possibly irregular/concave) buildable zone, then snap each to a valid
    shared pad. Returns (mvs_list, placed, avail) ready for the unmodified BESS
    packing routine."""
    mvs_eq  = config["equipment"]["MVS"]
    bess_eq = config["equipment"]["BESS"]
    co      = config.get("colocation", {})
    group_size    = int(co.get("group_size", 2))
    pad_gap       = float(co.get("pad_gap", 0.5))
    search_radius = float(co.get("hub_search_radius", 8.0))
    target        = int(co.get("target_hub_count", 0) or 0)
    max_ratio     = config["max_bess_per_mvs"]
    grid_res      = config.get("grid_resolution", 2.0)

    cx_off = bess_eq["width"]  / 2
    cy_off = bess_eq["height"] / 2
    pts = np.array([[x + cx_off, y + cy_off] for x, y in grid]) if grid else np.empty((0, 2))

    if len(pts) == 0:
        return [], [], list(grid)

    weights = np.ones(len(pts))

    buildable = site.area
    for nb in non_buildable:
        buildable -= nb.intersection(site).area

    if target > 0:
        k = target
    else:
        k = _determine_hub_count(buildable, bess_eq, max_ratio, group_size)
    k = max(1, min(k, len(pts)))

    centers = _weighted_lloyd_relaxation(pts, weights, k)

    placed, mvs_list, avail = [], [], list(grid)
    hub_n = 0
    for center in centers:
        members = _snap_hub_to_pad(center, group_size, pad_gap, site,
                                   non_buildable, placed, mvs_eq,
                                   search_radius, grid_res)
        if members is None:
            continue
        hub_n += 1
        hub_id = f"H{hub_n}"
        for pos, mem in enumerate(members):
            mem["hub_id"]      = hub_id
            mem["pad_position"] = pos
            mem["hub_size"]    = len(members)
            placed.append(mem)
            mvs_list.append(mem)
            avail = _prune_avail(avail, mem["footprint"], mem["clearance_zone"], bess_eq)
    return mvs_list, placed, avail


def _solve_lap(cost, INF):
    """Bipartite minimum-cost assignment (Hungarian) with a scipy-free greedy
    fallback — identical contract to the baseline reassignment solver."""
    try:
        from scipy.optimize import linear_sum_assignment
        return linear_sum_assignment(cost)
    except ImportError:
        n_b = cost.shape[0]
        order = sorted(range(n_b), key=lambda i: cost[i].min())
        used, row_ind, col_ind = set(), [], []
        for i in order:
            for j in np.argsort(cost[i]):
                j = int(j)
                if j in used or cost[i, j] >= INF:
                    continue
                row_ind.append(i)
                col_ind.append(j)
                used.add(j)
                break
        return np.array(row_ind), np.array(col_ind)


def _optimal_reassign_hub_balanced(mvs_list, bess_list, max_cable, max_ratio,
                                   balance_tolerance, max_balance_iter=5):
    """Stage-3 of the co-located pipeline. Globally re-pairs BESS to MVS slots
    minimising cable length, with two co-location-aware refinements over the
    baseline reassignment:
      (1) cost = distance to the MVS footprint EDGE (cable-entry point), not the
          centroid — more physical for pad members and removes the averaging
          artefact that biases load toward one sibling;
      (2) a best-effort Lagrangian re-weighting loop nudges intra-hub member
          fills to within ``balance_tolerance``. If the iteration cap is hit
          without convergence the layout is kept as-is (soft target) and the
          residual imbalance is surfaced via ``compute_hub_metrics``.
    Unlike the baseline, empty MVS are NOT pruned — a shared pad is physically
    poured for the whole hub regardless of final fill."""
    if not mvs_list or not bess_list:
        return mvs_list, bess_list

    n_b = len(bess_list)
    n_m = len(mvs_list)
    n_slots = n_m * max_ratio
    INF = 1e9

    bp = np.array([[b["footprint"].centroid.x, b["footprint"].centroid.y]
                   for b in bess_list])

    base = np.full((n_b, n_slots), INF)
    for j, m in enumerate(mvs_list):
        fp = m["footprint"]
        d = np.array([fp.distance(_Point(x, y)) for x, y in bp])
        feasible = np.ones_like(d, dtype=bool) if max_cable <= 0 else d <= max_cable
        for k in range(max_ratio):
            base[feasible, j * max_ratio + k] = d[feasible]

    penalty_step = 5.0 if max_cable <= 0 else max(2.0, 0.25 * max_cable)
    penalty = np.zeros(n_m)

    hubs = {}
    for j, m in enumerate(mvs_list):
        hubs.setdefault(m.get("hub_id", f"_solo{j}"), []).append(j)

    assignment = []
    for it in range(max_balance_iter + 1):
        cost = base.copy()
        for j in range(n_m):
            if penalty[j] > 0:
                lo, hi = j * max_ratio, (j + 1) * max_ratio
                block = cost[:, lo:hi]
                cost[:, lo:hi] = np.where(block < INF, block + penalty[j], INF)

        row_ind, col_ind = _solve_lap(cost, INF)

        fills = np.zeros(n_m, dtype=int)
        assignment = []
        for i, j in zip(row_ind, col_ind):
            if base[i, j] >= INF:
                continue
            m_idx = j // max_ratio
            fills[m_idx] += 1
            assignment.append((i, m_idx))

        balanced = True
        for idxs in hubs.values():
            if len(idxs) < 2:
                continue
            fmax = max(fills[j] for j in idxs)
            fmin = min(fills[j] for j in idxs)
            if fmax - fmin > balance_tolerance:
                balanced = False
                for j in idxs:
                    if fills[j] == fmax:
                        penalty[j] += penalty_step
        if balanced or it == max_balance_iter:
            break

    for m in mvs_list:
        m["assigned_bess"] = []
    new_bess = []
    for i, m_idx in assignment:
        m = mvs_list[m_idx]
        b = bess_list[i]
        b["mvs"] = m
        m["assigned_bess"].append(b)
        new_bess.append(b)

    return mvs_list, new_bess