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
