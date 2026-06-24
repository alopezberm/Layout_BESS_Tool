"""Shared placement helpers: candidate pruning, greedy cluster growth, a
straggler-fill pass, and the global Hungarian cable reassignment.

These are the validated primitives reused by the co-located engine
(``core.optimize``) and the row packer (``core.packing``). Pure layout logic —
no rendering, no UI.
"""

import numpy as np

from .geometry import (
    create_equipment_polygon,
    create_clearance_polygon,
    is_valid_placement,
    get_oriented_dimensions,
    ORIENTATIONS,
)


def _prune_avail(avail, blocker_fp, blocker_cl, bess_eq):
    bess_cl_dict = bess_eq["clearance"]
    w, h = bess_eq["width"], bess_eq["height"]
    out = []
    for (gx, gy) in avail:
        keep = False
        for angle in ORIENTATIONS:
            rw, rh, rcl = get_oriented_dimensions(w, h, bess_cl_dict, angle)
            b_fp = create_equipment_polygon(gx, gy, rw, rh)
            b_cl = create_clearance_polygon(b_fp, rcl)
            if not (b_fp.intersects(blocker_cl) or blocker_fp.intersects(b_cl)):
                keep = True
                break
        if keep:
            out.append((gx, gy))
    return out


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
            for angle in ORIENTATIONS:
                rw, rh, rcl = get_oriented_dimensions(w, h, bess_cl_dict, angle)
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
                        c.get("angle", 0) == angle
                        for c in members
                    )
                    if not aligned:
                        score += alignment_weight

                if score < best_score:
                    best_score = score
                    best = (b_fp, b_cl, angle)

        if best is None:
            break
        b_fp, b_cl, best_angle = best
        bess_obj = {
            "type":           "BESS",
            "footprint":      b_fp,
            "clearance_zone": b_cl,
            "mvs":            mvs_obj,
            "angle":          best_angle,
            "rotated":        best_angle in (90, 270),
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
            for angle in ORIENTATIONS:
                rw, rh, rcl = get_oriented_dimensions(w, h, bess_cl_dict, angle)
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
                candidates.append((best_d, b_fp, b_cl, best_mvs, angle))

        if not candidates:
            break
        candidates.sort(key=lambda c: c[0])
        _, b_fp, b_cl, mvs, best_angle = candidates[0]
        bess_obj = {
            "type":           "BESS",
            "footprint":      b_fp,
            "clearance_zone": b_cl,
            "mvs":            mvs,
            "angle":          best_angle,
            "rotated":        best_angle in (90, 270),
        }
        placed.append(bess_obj)
        bess_list.append(bess_obj)
        mvs["assigned_bess"].append(bess_obj)
        avail = _prune_avail(avail, b_fp, b_cl, bess_eq)
    return avail


def _optimal_reassign(mvs_list, bess_list, max_cable, max_ratio):
    """Global min-cost BESS->MVS-slot reassignment (Hungarian, scipy-free
    fallback). Returns ``(mvs_list, bess_list, dropped)`` where ``dropped`` is
    the count of placed BESS with no feasible MVS slot under the cable cap
    (surfaced, never silent). Empty MVS are pruned."""
    if not mvs_list or not bess_list:
        return mvs_list, bess_list, 0

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
    assigned_rows = set()
    for i, j in zip(row_ind, col_ind):
        if cost[i, j] >= INF:
            continue
        m = mvs_list[j // max_ratio]
        b = bess_list[i]
        b["mvs"] = m
        m["assigned_bess"].append(b)
        new_bess.append(b)
        assigned_rows.add(int(i))

    dropped = n_b - len(assigned_rows)
    mvs_list = [m for m in mvs_list if m["assigned_bess"]]
    return mvs_list, new_bess, dropped