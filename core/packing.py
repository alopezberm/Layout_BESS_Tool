"""Row / shelf packing engine.

A from-scratch placement strategy that arranges BESS in dense back-to-back rows
("shelves") so the small back clearance (e.g. 0.15 m) is actually exploited —
something the grid heuristics in ``placement.py`` cannot do because they only
sample a fixed lattice and never reach sub-grid gaps.

Layout idea (one global orientation; the engine tries both and keeps the best):
  * shelves are stacked along one axis; within a shelf, units repeat along the
    other axis at pitch = footprint + side clearance.
  * consecutive BESS shelves alternate orientation so their facing sides are
    both the "back" -> they touch at `back` (true back-to-back). The next
    boundary then falls on the large `front`, forming a natural access aisle.
  * one MVS shelf is inserted every `max_ratio` BESS shelves, so the BESS:MVS
    unit ratio matches the capacity cap and every BESS has a station nearby.

Inter-shelf gaps are computed analytically from the per-side clearances, but
every individual unit is still validated with the shared ``is_valid_placement``
— so the output is always collision-free, even on concave sites or around
non-buildable zones; the analytic positions are merely candidates.
"""

import numpy as np

from .geometry import (
    create_site,
    prepare_site,
    create_equipment_polygon,
    create_clearance_polygon,
    is_valid_placement,
    get_oriented_dimensions,
)
from .placement import _optimal_reassign
from .metrics import compute_metrics

# Small slack so flush-at-clearance boxes do not share an exact boundary
# (shapely treats edge-touching polygons as intersecting).
EPS = 1e-3

# Step for the local search that snaps a unit to the next valid spot in a row.
SEARCH_STEP = 0.1


def _shelf_sequence(mvs_every):
    """Yield (kind, base_phase) for an unbounded run of shelves.

    Each band holds `mvs_every` BESS shelves (alternating phase for
    back-to-back) plus one MVS shelf placed in the MIDDLE of the band, so the
    farthest BESS row is only ~`mvs_every/2` rows from its station — keeping
    cable runs short. Smaller `mvs_every` -> more stations (denser BESS can be
    served at the cost of BESS area).
    """
    mid = mvs_every // 2
    band = []
    phase = 0
    for i in range(mvs_every + 1):
        if i == mid:
            band.append(("MVS", 0))
        else:
            band.append(("BESS", phase % 2))
            phase += 1
    while True:
        for item in band:
            yield item


def _pack(site, non_buildable, bess_eq, mvs_eq, max_ratio, transpose, mvs_every,
          restricted=()):
    """Pack one orientation. ``transpose=False`` stacks shelves along y (BESS
    long-axis horizontal); ``True`` stacks along x. ``mvs_every`` BESS rows per
    MVS row. Returns (mvs_list, bess_list).
    """
    bw, bh = bess_eq["width"], bess_eq["height"]
    bcl = bess_eq["clearance"]
    mw, mh = mvs_eq["width"], mvs_eq["height"]
    mcl = mvs_eq["clearance"]

    minx, miny, maxx, maxy = site.bounds
    if transpose:
        stack_min, stack_max = minx, maxx
        across_min, across_max = miny, maxy
        bess_angles = (270, 90)   # long axis along y; back faces +-x
        mvs_angle = 90
        top_key, bot_key = "right", "left"     # +stack / -stack physical sides
        across_keys = ("front", "back")        # perpendicular (along y)
    else:
        stack_min, stack_max = miny, maxy
        across_min, across_max = minx, maxx
        bess_angles = (180, 0)    # long axis along x; back faces +-y
        mvs_angle = 0
        top_key, bot_key = "front", "back"
        across_keys = ("left", "right")

    def dims(eq_w, eq_h, cl, angle):
        rw, rh, rcl = get_oriented_dimensions(eq_w, eq_h, cl, angle)
        stack_extent = rw if transpose else rh
        across_extent = rh if transpose else rw
        return rw, rh, rcl, stack_extent, across_extent

    def make(across, stack, rw, rh):
        x, y = (stack, across) if transpose else (across, stack)
        return create_equipment_polygon(x, y, rw, rh)

    placed, bess_list, mvs_list = [], [], []
    seq = _shelf_sequence(mvs_every)

    stack = stack_min + EPS
    prev_top_cl = None        # clearance on the top side of the previous shelf
    prev_top_edge = None      # stack coordinate of the previous shelf's far edge
    shelves_done = 0
    empty_streak = 0

    while stack < stack_max and shelves_done < 10_000:
        kind, phase = next(seq)
        if kind == "BESS":
            angle = bess_angles[phase]
            rw, rh, rcl, s_ext, a_ext = dims(bw, bh, bcl, angle)
        else:
            angle = mvs_angle
            rw, rh, rcl, s_ext, a_ext = dims(mw, mh, mcl, angle)

        # Analytic baseline: flush against the previous shelf's top side.
        if prev_top_cl is not None:
            gap = max(prev_top_cl, rcl[bot_key])
            stack = prev_top_edge + gap + EPS

        if stack + s_ext > stack_max:
            break

        across_gap = max(rcl[across_keys[0]], rcl[across_keys[1]])
        shelf_objs = []
        # Adaptive across-fill: instead of a fixed lattice (which misaligns with
        # a diagonal/concave boundary and wastes most rows), local-search forward
        # for the next valid position, then jump one pitch past the placed unit.
        across = across_min + EPS
        search_cap = a_ext + across_gap + 8.0  # how far to probe before giving up
        while across + a_ext <= across_max:
            t = across
            placed_here = None
            while t + a_ext <= across_max and (t - across) <= search_cap:
                fp = make(t, stack, rw, rh)
                cl = create_clearance_polygon(fp, rcl)
                if is_valid_placement(fp, cl, site, non_buildable, placed, restricted):
                    placed_here = (fp, cl, t)
                    break
                t += SEARCH_STEP
            if placed_here is None:
                across = t + 1.0   # skipped a dead stretch (notch / outside)
                continue
            fp, cl, t = placed_here
            if kind == "BESS":
                obj = {"type": "BESS", "footprint": fp, "clearance_zone": cl,
                       "mvs": None, "angle": angle, "rotated": angle in (90, 270)}
                bess_list.append(obj)
            else:
                obj = {"type": "MVS", "footprint": fp, "clearance_zone": cl,
                       "assigned_bess": [], "angle": angle, "rotated": angle in (90, 270)}
                mvs_list.append(obj)
            placed.append(obj)
            shelf_objs.append(obj)
            across = t + a_ext + across_gap + EPS

        shelves_done += 1
        prev_top_cl = rcl[top_key]
        prev_top_edge = stack + s_ext
        if shelf_objs:
            empty_streak = 0
        else:
            # Nothing fit on this shelf (e.g. a concave notch); keep sweeping a
            # little, but bail out after a long empty run past the geometry.
            empty_streak += 1
            if empty_streak > 6:
                break

    return mvs_list, bess_list


def _mvs_every_options(ratios):
    """MVS-row densities to sweep: the BESS:MVS ratios themselves plus a few
    neighbours (denser/sparser station rows)."""
    return sorted(set(ratios) | {2, 3, 5, 6}, reverse=True)


def _pack_all(site, non_buildable, restricted, bess_eq, mvs_eq, mvs_every_options):
    """Run ``_pack`` for both global orientations and every MVS-row density.

    Packing is independent of the BESS:MVS ratio (only the assignment uses it),
    so these raw layouts can be shared across ratio scenarios. Returns a list of
    ``(transpose, mvs_every, mvs_list, bess_list)``.
    """
    out = []
    for transpose in (False, True):
        for mvs_every in mvs_every_options:
            mvs_l, bess_l = _pack(site, non_buildable, bess_eq, mvs_eq, None,
                                  transpose, mvs_every, restricted)
            out.append((transpose, mvs_every, mvs_l, bess_l))
    return out


def _clone(mvs_list, bess_list):
    """Fresh equipment dicts (geometry shared — shapely objects are immutable),
    with assignments cleared. Lets one raw packing feed several scenarios
    without their assignments overwriting each other."""
    return ([{**m, "assigned_bess": []} for m in mvs_list],
            [{**b, "mvs": None} for b in bess_list])


def _best_max_fill(packings, max_ratio, max_cable):
    """The original Row-Pack objective: the packing that serves the most BESS.
    Returns ``(served, mvs_list, bess_list, transpose, mvs_every, dropped)``."""
    best = None
    for transpose, mvs_every, mvs_raw, bess_raw in packings:
        mvs_l, bess_l = _clone(mvs_raw, bess_raw)
        if mvs_l and bess_l:
            mvs_l, bess_l, dr = _optimal_reassign(
                mvs_l, bess_l, max_cable if max_cable else 0, max_ratio)
        else:
            dr = 0
        served = len(bess_l)
        if best is None or served > best[0]:
            best = (served, mvs_l, bess_l, transpose, mvs_every, dr)
    return best


def _centroids(objs):
    return np.array([[o["footprint"].centroid.x, o["footprint"].centroid.y]
                     for o in objs]).reshape(-1, 2)


def _mst_length(pts):
    """Length of the Euclidean minimum spanning tree over ``pts`` (Prim) — a
    proxy for the MV collection cable daisy-chaining the selected stations."""
    n = len(pts)
    if n < 2:
        return 0.0
    in_tree = np.zeros(n, bool)
    in_tree[0] = True
    dist = np.hypot(*(pts - pts[0]).T)
    total = 0.0
    for _ in range(n - 1):
        d = np.where(in_tree, np.inf, dist)
        j = int(np.argmin(d))
        total += d[j]
        in_tree[j] = True
        dist = np.minimum(dist, np.hypot(*(pts - pts[j]).T))
    return float(total)


def _target_subset(mvs_raw, bess_raw, n_bess, n_mvs, max_cable):
    """Pick exactly ``n_bess`` BESS on exactly ``n_mvs`` MVS out of one raw
    packing, balanced (every MVS gets ``floor`` or ``ceil`` of
    ``n_bess / n_mvs``), minimising LV cable (BESS->MVS) + MV cable (MST
    between the chosen MVS) — i.e. the shortest-cabled, most compact block.
    Returns ``(score, mvs_idx, [(bess_idx, mvs_idx), ...])`` or None.

    Candidate MVS groups are (a) contiguous windows in shelf-sweep order — the
    plant fills the site from one side, leaving the rest free — and (b) the
    ``n_mvs`` nearest stations around each MVS. For each group a min-cost
    assignment picks the BESS: each MVS has ``floor`` mandatory slots (cost
    biased so they are always filled) plus one optional slot when the split is
    uneven, and ``n_rows - n_bess`` zero-cost "not built" columns let the
    farthest containers drop out.
    """
    from .colocation import _solve_lap

    k = n_mvs
    n_m, n_b = len(mvs_raw), len(bess_raw)
    if k < 1 or n_m < k or n_b < n_bess:
        return None
    base, extra = divmod(n_bess, k)
    cap = base + (1 if extra else 0)

    mp, bp = _centroids(mvs_raw), _centroids(bess_raw)
    D = np.hypot(bp[:, None, 0] - mp[None, :, 0], bp[:, None, 1] - mp[None, :, 1])
    feasible = D <= max_cable if max_cable and max_cable > 0 else np.ones_like(D, bool)

    groups = {tuple(range(i, i + k)) for i in range(n_m - k + 1)}
    MD = np.hypot(mp[:, None, 0] - mp[None, :, 0], mp[:, None, 1] - mp[None, :, 1])
    for i in range(n_m):
        groups.add(tuple(sorted(np.argsort(MD[i], kind="stable")[:k].tolist())))

    INF, BONUS = 1e9, 1e6
    best = None
    for g in groups:
        g = list(g)
        rows = np.flatnonzero(feasible[:, g].any(axis=1))
        if len(rows) < n_bess:
            continue
        feas = feasible[np.ix_(rows, g)]
        dist = D[np.ix_(rows, g)]
        cols, owner = [], []
        for j in range(k):
            for slot in range(cap):
                bonus = BONUS if slot < base else 0.0
                cols.append(np.where(feas[:, j], dist[:, j] - bonus, INF))
                owner.append(j)
        n_slots = len(cols)
        skip = np.zeros((len(rows), len(rows) - n_bess))        # "not built"
        cost = np.hstack([np.column_stack(cols), skip])
        r_ind, c_ind = _solve_lap(cost, INF)
        pairs, total = [], 0.0
        counts = np.zeros(k, int)
        for r, c in zip(r_ind, c_ind):
            if c < n_slots and cost[r, c] < INF:
                j = owner[c]
                pairs.append((int(rows[r]), g[j]))
                counts[j] += 1
                total += dist[r, j]
        if len(pairs) != n_bess or counts.min() < base:
            continue
        total += _mst_length(mp[g])
        if best is None or total < best[0] - 1e-6:
            best = (total, g, pairs)
    return best


def _build_from_subset(mvs_raw, bess_raw, pairs):
    """Materialise a chosen subset as fresh MVS/BESS dicts with assignments."""
    mvs_l, bess_l = _clone(mvs_raw, bess_raw)
    used_m = sorted({j for _, j in pairs})
    remap = {j: mvs_l[j] for j in used_m}
    out_b = []
    for i, j in sorted(pairs):
        b, m = bess_l[i], remap[j]
        b["mvs"] = m
        m["assigned_bess"].append(b)
        out_b.append(b)
    return [remap[j] for j in used_m], out_b


def _result(site, non_buildable, restricted, mvs_list, bess_list, max_ratio,
            dropped, label=None, target=None):
    metrics = compute_metrics(site, non_buildable, mvs_list, bess_list, max_ratio)
    metrics["dropped_bess"] = dropped
    res = {
        "mode": "row_pack",
        "site": site,
        "non_buildable": non_buildable,
        "restricted": restricted,
        "mvs_list": mvs_list,
        "bess_list": bess_list,
        "metrics": metrics,
    }
    if label:
        res["label"] = label
    if target is not None:
        res["target"] = target
    return res


def _print_summary(res, header):
    m = res["metrics"]
    print(f"\n----- {header} -----")
    print(f"MVS units placed   : {m['mvs_count']}")
    print(f"BESS units placed  : {m['bess_count']}")
    print(f"Fully sat. MVS     : {m['full_mvs']} / {m['mvs_count']}")
    print(f"Total cable        : {m['total_cable']:.1f} m")
    print(f"Capacity saturation: {m['capacity_saturation_pct']:.1f}%")
    if m.get("dropped_bess"):
        print(f"NOTE: {m['dropped_bess']} extra BESS packed beyond total MVS capacity (unassigned).")
    t = res.get("target")
    if t:
        status = "MET" if t["met"] else f"NOT MET — max {t['achieved_mwh']:.1f} MWh fits"
        print(f"Target             : {t['target_mwh']:.1f} MWh @ {t['duration_h']:g}H "
              f"({t['target_bess']} BESS / {t['target_mvs']} MVS) -> {status}")
        print(f"Plant              : {t['achieved_mwh']:.1f} MWh | {t['achieved_mw']:.1f} MW | "
              f"{t['achieved_duration_h']:.2f} h | up to {t['bess_per_mvs']} BESS/MVS")


def run_row_packing(config, verbose=False):
    """Top-level entry point for the row/shelf packing engine.

    Tries both global orientations, keeps the denser one, then assigns BESS to
    MVS with the shared Hungarian solver (unlimited cable within the packed
    field) and surfaces any BESS that could not be assigned.
    """
    site_raw = create_site(config["site_vertices"])
    site, non_buildable, restricted = prepare_site(site_raw, config)

    bess_eq = config["equipment"]["BESS"]
    mvs_eq = config["equipment"]["MVS"]
    max_ratio = config["max_bess_per_mvs"]

    max_cable = config.get("max_cable_length", 25)

    # Sweep both global orientations and a few MVS densities; keep the layout
    # that serves the most BESS after cable-cap assignment.
    packings = _pack_all(site, non_buildable, restricted, bess_eq, mvs_eq,
                         _mvs_every_options([max_ratio]))
    _, mvs_list, bess_list, transpose, mvs_every, dropped = _best_max_fill(
        packings, max_ratio, max_cable)

    res = _result(site, non_buildable, restricted, mvs_list, bess_list,
                  max_ratio, dropped)
    if verbose:
        _print_summary(res, f"ROW PACK ({'vertical' if transpose else 'horizontal'} rows, MVS every {mvs_every})")
    return res


def _mvs_needed(n_bess, unit_mwh, duration_h, mvs_mw):
    """MVS stations needed so ``n_bess`` containers discharge in ``duration_h``."""
    return max(1, int(np.ceil(n_bess * unit_mwh / duration_h / mvs_mw - 1e-6)))


def run_target_packing(config, target_mwh, durations=(4,), verbose=False):
    """EXTRA — Row Pack sized to an energy target at a chosen system duration.

    For each duration (hours) in ``durations``:
      * BESS = ceil(target_mwh / bess_unit_mwh)
      * MVS  = ceil(BESS energy / duration / mvs_station_mw)
    i.e. the duration fixes the plant power and hence the station count; BESS
    are split evenly across the MVS (floor/ceil). Across every packing of the
    site it keeps the block with minimum LV + MV cable. If the target does not
    fit, it returns the largest plant that does fit **at the same duration**
    and flags the shortfall.

    The primary objective of the tool is still ``run_row_packing`` (maximum
    number of BESS); this is an optional sizing study on top of it.

    Returns ``{duration_h: result}``; each result is the usual Row-Pack dict
    plus ``label`` and ``target`` = {target_mwh, duration_h, target_bess,
    target_mvs, target_mw, achieved_bess, achieved_mvs, achieved_mwh,
    achieved_mw, achieved_duration_h, bess_per_mvs, met, shortfall_mwh}.
    """
    site_raw = create_site(config["site_vertices"])
    site, non_buildable, restricted = prepare_site(site_raw, config)

    bess_eq = config["equipment"]["BESS"]
    mvs_eq = config["equipment"]["MVS"]
    max_cable = config.get("max_cable_length", 25)
    unit_mwh = config["bess_unit_mwh"]
    mvs_mw = config["mvs_station_mw"]
    if unit_mwh <= 0 or mvs_mw <= 0:
        raise ValueError("bess_unit_mwh and mvs_station_mw must be > 0 to size to a target")
    if target_mwh <= 0:
        raise ValueError("target_mwh must be > 0 (use run_row_packing to maximise)")
    durations = [float(h) for h in durations]
    n_target = int(np.ceil(target_mwh / unit_mwh - 1e-9))

    # One packing sweep shared by every duration; MVS-row densities around the
    # BESS:MVS ratios these durations imply.
    ratios = {max(1, round(h * mvs_mw / unit_mwh)) for h in durations}
    packings = _pack_all(site, non_buildable, restricted, bess_eq, mvs_eq,
                         _mvs_every_options(ratios))

    def solve(n, h):
        k = _mvs_needed(n, unit_mwh, h, mvs_mw)
        best = None
        for transpose, mvs_every, mvs_raw, bess_raw in packings:
            sel = _target_subset(mvs_raw, bess_raw, n, k, max_cable)
            if sel and (best is None or sel[0] < best[0] - 1e-6):
                best = (sel[0], mvs_raw, bess_raw, sel[2], transpose, mvs_every)
        return best

    results = {}
    for h in durations:
        best = solve(n_target, h)
        if best is None:
            # Largest plant that still fits at this duration (binary search).
            lo, hi = 0, n_target - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                sol = solve(mid, h)
                if sol is not None:
                    lo, best = mid, sol
                else:
                    hi = mid - 1
            if lo > 0 and (best is None or len(best[3]) != lo):
                best = solve(lo, h)

        if best is not None:
            _, mvs_raw, bess_raw, pairs, transpose, mvs_every = best
            mvs_list, bess_list = _build_from_subset(mvs_raw, bess_raw, pairs)
        else:
            mvs_list, bess_list, transpose, mvs_every = [], [], False, 0

        n_b, n_m = len(bess_list), len(mvs_list)
        cap = max((len(m["assigned_bess"]) for m in mvs_list), default=0)
        target_mvs = _mvs_needed(n_target, unit_mwh, h, mvs_mw)
        target = {
            "target_mwh": float(target_mwh),
            "duration_h": h,
            "target_bess": n_target,
            "target_mvs": target_mvs,
            "target_mw": target_mvs * mvs_mw,
            "achieved_bess": n_b,
            "achieved_mvs": n_m,
            "achieved_mwh": n_b * unit_mwh,
            "achieved_mw": n_m * mvs_mw,
            "achieved_duration_h": (n_b * unit_mwh / (n_m * mvs_mw)) if n_m else 0.0,
            "bess_per_mvs": cap,
            "met": n_b >= n_target,
            "shortfall_mwh": max(0.0, (n_target - n_b) * unit_mwh),
        }
        res = _result(site, non_buildable, restricted, mvs_list, bess_list,
                      max(cap, 1), 0, label=f"{h:g}H", target=target)
        results[int(h) if h.is_integer() else h] = res
        if verbose:
            _print_summary(res, f"TARGET {target_mwh:g} MWh @ {h:g}H "
                                f"({'vertical' if transpose else 'horizontal'} rows, "
                                f"MVS every {mvs_every})")
    return results
