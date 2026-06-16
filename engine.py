import numpy as np
from shapely.geometry import Polygon, box

def create_site(vertices):
    site = Polygon(vertices)
    if not site.is_valid:
        raise ValueError("Invalid polygon: check vertex order")
    return site

def prepare_site(site, config):
    usable_site = site.buffer(-config["setback"])
    for zone in config["zones"]["restricted"]:
        zone_poly = Polygon(zone)
        usable_site = usable_site.difference(zone_poly)
    non_buildable_polys = [Polygon(z) for z in config["zones"]["non_buildable"]]
    return usable_site, non_buildable_polys

def create_candidate_grid(site, resolution):
    minx, miny, maxx, maxy = site.bounds
    xs = np.arange(minx, maxx, resolution)
    ys = np.arange(miny, maxy, resolution)
    return [(x, y) for x in xs for y in ys]

def create_equipment_polygon(x, y, width, height):
    return box(x, y, x + width, y + height)

def create_clearance_polygon(footprint, clearance):
    minx, miny, maxx, maxy = footprint.bounds
    return box(
        minx - clearance["left"],
        miny - clearance["back"],
        maxx + clearance["right"],
        maxy + clearance["front"],
    )

def is_valid_placement(candidate_fp, candidate_cl, site, non_buildable, placed):
    if not site.contains(candidate_fp):
        return False
    for zone in non_buildable:
        if candidate_fp.intersects(zone):
            return False
    for obj in placed:
        if candidate_fp.intersects(obj["clearance_zone"]):
            return False
        if candidate_cl.intersects(obj["footprint"]):
            return False
    return True

def get_rotated_dimensions(w, h, cl_dict, rotated):
    if not rotated:
        return w, h, cl_dict
    else:
        return h, w, {
            "front": cl_dict["left"],
            "back": cl_dict["right"],
            "left": cl_dict["back"],
            "right": cl_dict["front"],
        }

def _prune_avail(avail, blocker_fp, blocker_cl, bess_eq):
    bess_cl_dict = bess_eq["clearance"]
    w, h = bess_eq["width"], bess_eq["height"]
    out = []
    for (gx, gy) in avail:
        keep = False
        for rotated in [False, True]:
            rw, rh, rcl = get_rotated_dimensions(w, h, bess_cl_dict, rotated)
            b_fp = create_equipment_polygon(gx, gy, rw, rh)
            b_cl = create_clearance_polygon(b_fp, rcl)
            if not (b_fp.intersects(blocker_cl) or blocker_fp.intersects(b_cl)):
                keep = True
                break
        if keep:
            out.append((gx, gy))
    return out

def _score_mvs(cx, cy, avail_arr, scoring_r, max_ratio):
    dists = np.hypot(avail_arr[:, 0] - cx, avail_arr[:, 1] - cy)
    reachable = dists[dists <= scoring_r] if scoring_r > 0 else dists
    top_k = np.sort(reachable)[1 : max_ratio + 1]
    return float(top_k.sum()) if top_k.size > 0 else float("inf")

def _grow_cluster(mvs_obj, avail, site, non_buildable, placed, bess_eq,
                  max_ratio, max_cable, alignment_weight, require_adjacency):
    bess_cl_dict = bess_eq["clearance"]
    w, h = bess_eq["width"], bess_eq["height"]
    mx_c, my_c = mvs_obj["footprint"].centroid.x, mvs_obj["footprint"].centroid.y
    members = [mvs_obj]
    added = []
    for _ in range(max_ratio):
        best = None
        best_score = float("inf")
        for (gx, gy) in avail:
            for rotated in [False, True]:
                rw, rh, rcl = get_rotated_dimensions(w, h, bess_cl_dict, rotated)
                b_fp = create_equipment_polygon(gx, gy, rw, rh)
                b_cl = create_clearance_polygon(b_fp, rcl)
                if not is_valid_placement(b_fp, b_cl, site, non_buildable, placed):
                    continue
                bx, by = b_fp.centroid.x, b_fp.centroid.y
                d_mvs = np.hypot(bx - mx_c, by - my_c)
                if max_cable > 0 and d_mvs > max_cable:
                    continue
                if require_adjacency and not any(
                    b_cl.intersects(c["clearance_zone"]) for c in members
                ):
                    continue

                d_cluster = min(
                    np.hypot(c["footprint"].centroid.x - bx,
                             c["footprint"].centroid.y - by)
                    for c in members
                )
                score = d_mvs + 0.5 * d_cluster

                if alignment_weight > 0:
                    tol = 0.5
                    aligned = any(
                        (abs(c["footprint"].centroid.x - bx) < tol or
                         abs(c["footprint"].centroid.y - by) < tol) and
                        c.get("rotated", False) == rotated
                        for c in members
                    )
                    if not aligned:
                        score += alignment_weight

                if score < best_score:
                    best_score = score
                    best = (b_fp, b_cl, rotated)

        if best is None:
            break
        b_fp, b_cl, best_rotated = best
        bess_obj = {
            "type":           "BESS",
            "footprint":      b_fp,
            "clearance_zone": b_cl,
            "mvs":            mvs_obj,
            "rotated":        best_rotated
        }
        placed.append(bess_obj)
        mvs_obj["assigned_bess"].append(bess_obj)
        members.append(bess_obj)
        added.append(bess_obj)
        avail = _prune_avail(avail, b_fp, b_cl, bess_eq)
    return added, avail

def _stragglers_pass(avail, site, non_buildable, placed, mvs_list, bess_list,
                     bess_eq, max_ratio, max_cable):
    bess_cl_dict = bess_eq["clearance"]
    w, h = bess_eq["width"], bess_eq["height"]
    unlimited = max_cable <= 0
    while True:
        candidates = []
        for (gx, gy) in avail:
            for rotated in [False, True]:
                rw, rh, rcl = get_rotated_dimensions(w, h, bess_cl_dict, rotated)
                b_fp = create_equipment_polygon(gx, gy, rw, rh)
                b_cl = create_clearance_polygon(b_fp, rcl)
                if not is_valid_placement(b_fp, b_cl, site, non_buildable, placed):
                    continue
                bx, by = b_fp.centroid.x, b_fp.centroid.y
                best_mvs = None
                best_d = float("inf")
                for m in mvs_list:
                    if len(m["assigned_bess"]) >= max_ratio:
                        continue
                    mx, my = m["footprint"].centroid.x, m["footprint"].centroid.y
                    d = np.hypot(bx - mx, by - my)
                    if (unlimited or d <= max_cable) and d < best_d:
                        best_d = d
                        best_mvs = m
                if best_mvs is None:
                    continue
                candidates.append((best_d, b_fp, b_cl, best_mvs, rotated))

        if not candidates:
            break
        candidates.sort(key=lambda c: c[0])
        _, b_fp, b_cl, mvs, best_rotated = candidates[0]
        bess_obj = {
            "type":           "BESS",
            "footprint":      b_fp,
            "clearance_zone": b_cl,
            "mvs":            mvs,
            "rotated":        best_rotated
        }
        placed.append(bess_obj)
        bess_list.append(bess_obj)
        mvs["assigned_bess"].append(bess_obj)
        avail = _prune_avail(avail, b_fp, b_cl, bess_eq)
    return avail

def _stragglers_pass_singlepass(fine_avail, site, non_buildable, placed,
                                mvs_list, bess_list, bess_eq, max_ratio,
                                max_cable):
    bess_cl_dict = bess_eq["clearance"]
    w, h = bess_eq["width"], bess_eq["height"]
    unlimited = max_cable <= 0
    candidates = []
    for (gx, gy) in fine_avail:
        for rotated in [False, True]:
            rw, rh, rcl = get_rotated_dimensions(w, h, bess_cl_dict, rotated)
            b_fp = create_equipment_polygon(gx, gy, rw, rh)
            b_cl = create_clearance_polygon(b_fp, rcl)
            if not is_valid_placement(b_fp, b_cl, site, non_buildable, placed):
                continue
            bx, by = b_fp.centroid.x, b_fp.centroid.y
            best_d = float("inf")
            for m in mvs_list:
                mx, my = m["footprint"].centroid.x, m["footprint"].centroid.y
                d = np.hypot(bx - mx, by - my)
                if (unlimited or d <= max_cable) and d < best_d:
                    best_d = d
            if best_d == float("inf"):
                continue
            candidates.append((best_d, b_fp, b_cl, bx, by, rotated))

    candidates.sort(key=lambda c: c[0])
    added = 0
    for _, b_fp, b_cl, bx, by, rotated in candidates:
        if not is_valid_placement(b_fp, b_cl, site, non_buildable, placed):
            continue
        target = None
        target_d = float("inf")
        for m in mvs_list:
            if len(m["assigned_bess"]) >= max_ratio:
                continue
            mx, my = m["footprint"].centroid.x, m["footprint"].centroid.y
            d = np.hypot(bx - mx, by - my)
            if (unlimited or d <= max_cable) and d < target_d:
                target_d = d
                target = m
        if target is None:
            continue
        bess_obj = {
            "type":           "BESS",
            "footprint":      b_fp,
            "clearance_zone": b_cl,
            "mvs":            target,
            "rotated":        rotated
        }
        placed.append(bess_obj)
        bess_list.append(bess_obj)
        target["assigned_bess"].append(bess_obj)
        added += 1
    return added

def _try_add_mvs(site, non_buildable, placed, mvs_eq, fine_resolution):
    mvs_cl_dict = mvs_eq["clearance"]
    w, h = mvs_eq["width"], mvs_eq["height"]
    for (mx, my) in create_candidate_grid(site, fine_resolution):
        for rotated in [False, True]:
            mw, mh, mcl = get_rotated_dimensions(w, h, mvs_cl_dict, rotated)
            mvs_fp = create_equipment_polygon(mx, my, mw, mh)
            mvs_cl = create_clearance_polygon(mvs_fp, mcl)
            if is_valid_placement(mvs_fp, mvs_cl, site, non_buildable, placed):
                return {
                    "type":           "MVS",
                    "footprint":      mvs_fp,
                    "clearance_zone": mvs_cl,
                    "assigned_bess":  [],
                    "rotated":        rotated
                }
    return None

def _hyper_pack_pass(site, non_buildable, placed, mvs_list, bess_list,
                     bess_eq, mvs_eq, max_ratio, max_cable, fine_resolution):
    while True:
        fine_avail = create_candidate_grid(site, fine_resolution)
        for obj in placed:
            fine_avail = _prune_avail(fine_avail, obj["footprint"],
                                      obj["clearance_zone"], bess_eq)
        added = _stragglers_pass_singlepass(
            fine_avail, site, non_buildable, placed,
            mvs_list, bess_list, bess_eq, max_ratio, max_cable,
        )
        if added == 0:
            if all(len(m["assigned_bess"]) >= max_ratio for m in mvs_list):
                new_mvs = _try_add_mvs(site, non_buildable, placed, mvs_eq, fine_resolution)
                if new_mvs is None:
                    break
                placed.append(new_mvs)
                mvs_list.append(new_mvs)
                continue
            break

def _optimal_reassign(mvs_list, bess_list, max_cable, max_ratio):
    if not mvs_list or not bess_list:
        return mvs_list, bess_list

    n_b = len(bess_list)
    n_m = len(mvs_list)
    n_slots = n_m * max_ratio

    bp = np.array([[b["footprint"].centroid.x, b["footprint"].centroid.y] for b in bess_list])
    mp = np.array([[m["footprint"].centroid.x, m["footprint"].centroid.y] for m in mvs_list])

    INF = 1e9
    cost = np.full((n_b, n_slots), INF)
    for j in range(n_m):
        d = np.hypot(bp[:, 0] - mp[j, 0], bp[:, 1] - mp[j, 1])
        feasible = np.ones_like(d, dtype=bool) if max_cable <= 0 else d <= max_cable
        for k in range(max_ratio):
            cost[feasible, j * max_ratio + k] = d[feasible]

    try:
        from scipy.optimize import linear_sum_assignment
        row_ind, col_ind = linear_sum_assignment(cost)
    except ImportError:
        order = sorted(range(n_b), key=lambda i: cost[i].min())
        used = set()
        row_ind, col_ind = [], []
        for i in order:
            for j in np.argsort(cost[i]):
                j = int(j)
                if j in used or cost[i, j] >= INF:
                    continue
                row_ind.append(i)
                col_ind.append(j)
                used.add(j)
                break
        row_ind = np.array(row_ind)
        col_ind = np.array(col_ind)

    for m in mvs_list:
        m["assigned_bess"] = []
    new_bess = []
    for i, j in zip(row_ind, col_ind):
        if cost[i, j] >= INF:
            continue
        m = mvs_list[j // max_ratio]
        b = bess_list[i]
        b["mvs"] = m
        m["assigned_bess"].append(b)
        new_bess.append(b)

    mvs_list = [m for m in mvs_list if m["assigned_bess"]]
    return mvs_list, new_bess

_MODE_PROFILES = {
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

def place_clusters(site, non_buildable, grid, config, mode="aggressive"):
    if mode not in _MODE_PROFILES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {list(_MODE_PROFILES)}")
    profile = _MODE_PROFILES[mode]

    mvs_eq      = config["equipment"]["MVS"]
    bess_eq     = config["equipment"]["BESS"]
    mvs_cl_dict = mvs_eq["clearance"]
    min_spacing = config.get("min_mvs_spacing", 0)
    max_ratio   = config["max_bess_per_mvs"]
    scoring_r   = config.get("mvs_scoring_radius", 0)

    base_cable    = config.get("max_cable_length", 25)
    eff_cable     = profile["cable_cap_override"] if profile["cable_cap_override"] is not None else base_cable
    eff_hungarian = profile["hungarian_cap_override"] if profile["hungarian_cap_override"] is not None else base_cable
    fine_res      = profile["fine_grid_resolution"]

    cx_off = bess_eq["width"]  / 2
    cy_off = bess_eq["height"] / 2

    placed    = []
    mvs_list  = []
    bess_list = []
    avail     = list(grid)

    while True:
        if len(avail) < max_ratio + 1:
            break

        avail_arr = np.array([[x + cx_off, y + cy_off] for x, y in avail])
        best = None
        best_score = float("inf")

        for (mx, my) in avail:
            for rotated in [False, True]:
                mw, mh, mcl = get_rotated_dimensions(mvs_eq["width"], mvs_eq["height"], mvs_cl_dict, rotated)
                mvs_fp = create_equipment_polygon(mx, my, mw, mh)
                mvs_cl = create_clearance_polygon(mvs_fp, mcl)
                if not is_valid_placement(mvs_fp, mvs_cl, site, non_buildable, placed):
                    continue

                cx, cy = mvs_fp.centroid.x, mvs_fp.centroid.y
                if min_spacing > 0 and any(
                    (cx - m["footprint"].centroid.x) ** 2 + (cy - m["footprint"].centroid.y) ** 2
                    < min_spacing ** 2
                    for m in mvs_list
                ):
                    continue

                score = _score_mvs(cx, cy, avail_arr, scoring_r, max_ratio)

                if profile["mvs_y_band_bonus"] < 1.0 and mvs_list:
                    tol = 1.5
                    if any(abs(cy - m["footprint"].centroid.y) < tol for m in mvs_list):
                        score *= profile["mvs_y_band_bonus"]

                if score < best_score:
                    best_score = score
                    best = (mvs_fp, mvs_cl, rotated)

        if best is None:
            break

        mvs_fp, mvs_cl, best_rotated = best
        mvs_obj = {
            "type":           "MVS",
            "footprint":      mvs_fp,
            "clearance_zone": mvs_cl,
            "assigned_bess":  [],
            "rotated":        best_rotated
        }
        placed.append(mvs_obj)
        mvs_list.append(mvs_obj)
        avail = _prune_avail(avail, mvs_fp, mvs_cl, bess_eq)

        added, avail = _grow_cluster(
            mvs_obj, avail, site, non_buildable, placed, bess_eq,
            max_ratio, eff_cable,
            profile["alignment_weight"],
            profile["require_adjacency"],
        )
        bess_list.extend(added)

        if not mvs_obj["assigned_bess"]:
            placed.remove(mvs_obj)
            mvs_list.remove(mvs_obj)

    if profile["do_stragglers"] and mvs_list:
        avail = _stragglers_pass(
            avail, site, non_buildable, placed, mvs_list, bess_list,
            bess_eq, max_ratio, eff_cable,
        )

    if fine_res is not None and mvs_list:
        _hyper_pack_pass(
            site, non_buildable, placed, mvs_list, bess_list,
            bess_eq, mvs_eq, max_ratio, eff_cable, fine_res,
        )

    if mvs_list:
        mvs_list, bess_list = _optimal_reassign(mvs_list, bess_list, eff_hungarian, max_ratio)

    return mvs_list, bess_list

def total_cable_length(mvs_list):
    total = 0.0
    for mvs in mvs_list:
        mx, my = mvs["footprint"].centroid.x, mvs["footprint"].centroid.y
        for b in mvs["assigned_bess"]:
            bx, by = b["footprint"].centroid.x, b["footprint"].centroid.y
            total += np.hypot(bx - mx, by - my)
    return total

def _compute_metrics(site, non_buildable, mvs_list, bess_list, max_ratio):
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

def run_bess_optimization(config, mode="aggressive", verbose=True):
    site_raw = create_site(config["site_vertices"])
    site, non_buildable = prepare_site(site_raw, config)
    grid = create_candidate_grid(site, config["grid_resolution"])

    mvs_list, bess_list = place_clusters(site, non_buildable, grid, config, mode=mode)
    metrics = _compute_metrics(site, non_buildable, mvs_list, bess_list,
                               config["max_bess_per_mvs"])

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

    return {
        "mode":          mode,
        "site":          site,
        "non_buildable": non_buildable,
        "mvs_list":      mvs_list,
        "bess_list":     bess_list,
        "metrics":       metrics,
    }

# =============================================================================
# CO-LOCATED / PAIRED MVS OPTIMIZATION  (additive parallel engine)
# -----------------------------------------------------------------------------
# A completely separate scenario engine that seeds MVS stations onto shared
# foundation pads (paired or grouped into central power hubs) BEFORE the BESS
# packing routine runs. It reuses every validated baseline primitive
# (is_valid_placement, _grow_cluster, _stragglers_pass, _prune_avail,
# _compute_metrics) without modifying a single line of them. Only two stages
# carry genuinely new logic: (1) where the MVS hubs go — solved as a capacitated
# continuous facility-location problem via weighted Lloyd relaxation with a
# Weiszfeld geometric-median update (minimises trenching length, not squared
# distance); and (2) how cable load balances inside each hub — solved with the
# baseline Hungarian assignment wrapped in a bounded Lagrangian re-weighting loop.
# =============================================================================
from shapely.geometry import Point as _Point


def _geometric_median(pts, weights, max_iter=100, tol=1e-5):
    """Weiszfeld iteration: the point minimising the WEIGHTED SUM of Euclidean
    distances to `pts`. This is the trenching-length-optimal hub location, as
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
    `group_size` MVS laid side-by-side. Members are validated only against
    EXTERNAL `placed` equipment, never against their own pad siblings — this is
    what permits the tighter-than-standard-clearance co-location on one slab.
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
        for rotated in (False, True):
            rw, rh, rcl = get_rotated_dimensions(w, h, mvs_cl, rotated)
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
                    "rotated":        rotated,
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
    fallback — identical contract to the baseline _optimal_reassign solver."""
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
    baseline _optimal_reassign:
      (1) cost = distance to the MVS footprint EDGE (cable-entry point), not the
          centroid — more physical for pad members and removes the averaging
          artefact that biases load toward one sibling;
      (2) a best-effort Lagrangian re-weighting loop nudges intra-hub member
          fills to within `balance_tolerance`. If the iteration cap is hit
          without convergence the layout is kept as-is (soft target) and the
          residual imbalance is surfaced via _compute_hub_metrics.
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


def _compute_hub_metrics(mvs_list, config):
    """Civil-works / balance KPIs for the co-located scenario. All quantities
    are additive and derived purely from hub tags already on the MVS dicts."""
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


def run_colocated_optimization(config, mode="aggressive", verbose=False):
    """Top-level entry point for the Co-Located / Paired MVS scenario. Mirrors
    run_bess_optimization but swaps the MVS seeding stage for hub facility-
    location and the final assignment for the hub-balanced Hungarian variant.
    The BESS packing in between is the unmodified baseline machinery."""
    profile = _MODE_PROFILES.get(mode, _MODE_PROFILES["aggressive"])

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

    metrics     = _compute_metrics(site, non_buildable, mvs_list, bess_list, max_ratio)
    hub_metrics = _compute_hub_metrics(mvs_list, config)

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
