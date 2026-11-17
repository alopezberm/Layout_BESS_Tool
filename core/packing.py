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

import itertools

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

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


def _shelf_sequence(mvs_every, start=0):
    """Yield (kind, base_phase) for an unbounded run of shelves.

    Each band holds `mvs_every` BESS shelves (alternating phase for
    back-to-back) plus one MVS shelf placed in the MIDDLE of the band, so the
    farthest BESS row is only ~`mvs_every/2` rows from its station — keeping
    cable runs short. Smaller `mvs_every` -> more stations (denser BESS can be
    served at the cost of BESS area).

    ``start`` rotates the band so the sweep begins at a different shelf of the
    cycle — shifting where the MVS rows land relative to the site edge, which
    can leave each station centred among its BESS on irregular boundaries.
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
    band = band[start % len(band):] + band[:start % len(band)]
    while True:
        for item in band:
            yield item


def _pack(site, non_buildable, bess_eq, mvs_eq, max_ratio, transpose, mvs_every,
          restricted=(), start=0):
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
    seq = _shelf_sequence(mvs_every, start)

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
                       "assigned_bess": [], "angle": angle, "rotated": angle in (90, 270),
                       # axis along which this MVS's shelves stack (0 = x, 1 = y)
                       "stack_axis": 0 if transpose else 1}
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


def _pack_variants(area, non_buildable, restricted, bess_eq, mvs_eq,
                   mvs_every_options, shift_for):
    """``_pack`` over ``area`` for both orientations, every MVS-row density and,
    for the densities in ``shift_for``, every rotation of the shelf band."""
    out = []
    for transpose in (False, True):
        for mvs_every in mvs_every_options:
            starts = range(mvs_every + 1) if mvs_every in shift_for else (0,)
            for start in starts:
                mvs_l, bess_l = _pack(area, non_buildable, bess_eq, mvs_eq, None,
                                      transpose, mvs_every, restricted, start)
                out.append((transpose, mvs_every, mvs_l, bess_l))
    return out


# Best per-zone packings combined into whole-site candidates (per zone).
ZONE_TOP_K = 4


def _per_zone_packings(site, non_buildable, restricted, bess_eq, mvs_eq,
                       mvs_every_options, shift_for, zones, max_ratio, max_cable,
                       global_packings=()):
    """Whole-site candidates built by packing every buildable zone on its own.

    A global packing shares one shelf grid across the site, so on a site split
    into separate zones the MVS rows can only be centred in one of them. Here
    each zone gets its own orientation / density / MVS-row offset — or the part
    of a global packing that falls inside it, when that fits more; per zone the
    ``ZONE_TOP_K`` packings serving the most BESS (ties: best layout score —
    compact, centred, convex cable) are kept and every combination is merged
    (re-validated against the other zones' units) into one candidate.
    """
    min_area = bess_eq["width"] * bess_eq["height"]
    useful = [z for z in zones if z.area >= min_area]
    if len(useful) < 2:
        return []

    # Each global packing split by zone, so a zone may also keep the global grid.
    split = [(t, e, _zone_index(m, useful), _zone_index(b, useful), m, b)
             for t, e, m, b in global_packings]

    ranked = []
    for zi, zone in enumerate(useful):
        # Per zone only the ratio densities (every offset) — the ones that can
        # centre the stations; other densities still come in via the global split.
        cands = _pack_variants(zone, non_buildable, restricted, bess_eq, mvs_eq,
                               sorted(shift_for) or mvs_every_options, shift_for)
        cands += [(t, e, [o for o, z in zip(m, zm) if z == zi],
                   [o for o, z in zip(b, zb) if z == zi])
                  for t, e, zm, zb, m, b in split]
        cands = [c for c in cands if c[2] and c[3]]
        if not cands:
            continue
        served = []
        _best_max_fill(cands, max_ratio, max_cable, served)
        # Only candidates that can make the top-K by BESS count need a layout score.
        cutoff = sorted(served, reverse=True)[min(ZONE_TOP_K, len(served)) - 1]
        scored = []
        for cand, n in zip(cands, served):
            if n == 0 or n < cutoff:
                continue
            sel = _target_subset(cand[2], cand[3], n, -(-n // max_ratio), max_cable)
            scored.append((-n, sel[0] if sel else np.inf, len(scored), cand))
        scored.sort(key=lambda x: x[:3])
        if scored:
            ranked.append([c for *_, c in scored[:ZONE_TOP_K]])
    if len(ranked) < 2:
        return []

    out = []
    for combo in itertools.product(*ranked):
        placed, mvs_all, bess_all = [], [], []
        for _, _, mvs_l, bess_l in combo:
            for obj in mvs_l + bess_l:
                if is_valid_placement(obj["footprint"], obj["clearance_zone"], site,
                                      non_buildable, placed, restricted):
                    placed.append(obj)
                    (mvs_all if obj["type"] == "MVS" else bess_all).append(obj)
        out.append((None, "per-zone", mvs_all, bess_all))
    return out


def _pack_all(site, non_buildable, restricted, bess_eq, mvs_eq, mvs_every_options,
              shift_for=(), zones=None, max_ratio=None, max_cable=None):
    """Run ``_pack`` for both global orientations and every MVS-row density.

    Packing is independent of the BESS:MVS ratio (only the assignment uses it),
    so these raw layouts can be shared across ratio scenarios. For the densities
    in ``shift_for`` every rotation of the shelf band is also tried (see
    ``_shelf_sequence``). With several buildable ``zones``, per-zone packings
    (see ``_per_zone_packings``) are added as further candidates. Returns a
    list of ``(transpose, mvs_every, mvs_list, bess_list)``; per-zone
    candidates carry ``transpose=None``.
    """
    out = _pack_variants(site, non_buildable, restricted, bess_eq, mvs_eq,
                         mvs_every_options, shift_for)
    if zones and max_ratio:
        out += _per_zone_packings(site, non_buildable, restricted, bess_eq, mvs_eq,
                                  mvs_every_options, shift_for, zones, max_ratio,
                                  max_cable, global_packings=out)
    return out


def _clone(mvs_list, bess_list):
    """Fresh equipment dicts (geometry shared — shapely objects are immutable),
    with assignments cleared. Lets one raw packing feed several scenarios
    without their assignments overwriting each other."""
    return ([{**m, "assigned_bess": []} for m in mvs_list],
            [{**b, "mvs": None} for b in bess_list])


def _best_max_fill(packings, max_ratio, max_cable, served_out=None):
    """The original Row-Pack objective: the packing that serves the most BESS.
    Returns ``(served, mvs_list, bess_list, transpose, mvs_every, dropped)``;
    if ``served_out`` is a list, the BESS served by each packing is appended."""
    best = None
    for transpose, mvs_every, mvs_raw, bess_raw in packings:
        mvs_l, bess_l = _clone(mvs_raw, bess_raw)
        if mvs_l and bess_l:
            mvs_l, bess_l, dr = _optimal_reassign(
                mvs_l, bess_l, max_cable if max_cable else 0, max_ratio)
        else:
            # No station placed (or no BESS): nothing is served.
            mvs_l, bess_l, dr = [], [], len(bess_l)
        served = len(bess_l)
        if served_out is not None:
            served_out.append(served)
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


def _buildable_zones(site, non_buildable):
    """Connected buildable areas: the site minus the non-buildable zones, split
    into its separate polygons. A cable between two of them would have to cross
    non-buildable land."""
    area = site.difference(unary_union(non_buildable)) if non_buildable else site
    return list(getattr(area, "geoms", [area]))


def _zone_index(objs, zones):
    """Index of the buildable zone holding each object's footprint centroid."""
    out = np.zeros(len(objs), int)
    for i, o in enumerate(objs):
        c = o["footprint"].centroid
        out[i] = int(np.argmin([z.distance(c) for z in zones])) if len(zones) > 1 else 0
    return out


# Convex LV-cable cost: a BESS at ``d`` metres from its MVS costs d^2 / REF, so
# a run of REF metres costs REF and every extra metre costs progressively more
# (two BESS at 4 m -> 8, one at 4 m + one at 5 m -> 10.25). The optimiser thus
# keeps every BESS close to its station instead of saving total length by
# pushing a single unit far away. Reported cable KPIs stay in real metres.
CABLE_REF_M = 4.0


def _cable_cost(d):
    return d * d / CABLE_REF_M


# Cost added for each BESS sitting on the "wrong" side of its MVS, so the
# station ends up in the middle of its cluster (2+2 rather than 1+3). Soft: it is
# accepted when the geometry leaves no alternative (e.g. MVS at a row end).
CENTER_PENALTY = 50.0
# Cost of one LV cable crossing non-buildable land in the last-resort pass —
# large, so the layout uses as few crossings as possible.
CROSS_PENALTY = 1000.0
# Offsets (m) along the stacking axis below this count as "beside" the MVS.
SIDE_TOL = 0.5


def _slot_sides(cap):
    """Side of each of an MVS's ``cap`` BESS slots along the stacking axis:
    alternating -1 / +1 so the cluster is split evenly around the station; with
    an odd cap the last slot may sit on either side (0)."""
    return [0 if (cap % 2 and i == cap - 1) else (-1 if i % 2 == 0 else 1)
            for i in range(cap)]


def _side_penalty(offset, side):
    """CENTER_PENALTY where a BESS with stack-axis ``offset`` (BESS - MVS)
    lies on the opposite side to the slot's ``side``; 0 otherwise."""
    if side == 0:
        return np.zeros_like(offset)
    return np.where(side * offset < -SIDE_TOL, CENTER_PENALTY, 0.0)


def _stack_offsets(bp, mvs_list):
    """Offset of every BESS (rows of ``bp``) from every MVS along that MVS's own
    shelf-stacking axis."""
    mp = _centroids(mvs_list)
    ax = np.array([m.get("stack_axis", 1) for m in mvs_list], int)
    return bp[:, ax] - mp[np.arange(len(mvs_list)), ax][None, :]


def _rebalance_clusters(mvs_list, bess_list, max_ratio, max_cable):
    """Re-pair an already-served layout so each MVS sits in the middle of its
    BESS (convex cable cost plus the side penalty). Keeps the original
    assignment if the re-pairing would leave a BESS unserved or an MVS empty,
    so BESS / MVS counts never change."""
    from .colocation import _solve_lap

    if not mvs_list or not bess_list:
        return mvs_list, bess_list
    mp, bp = _centroids(mvs_list), _centroids(bess_list)
    D = np.hypot(bp[:, None, 0] - mp[None, :, 0], bp[:, None, 1] - mp[None, :, 1])
    off = _stack_offsets(bp, mvs_list)
    feasible = D <= max_cable if max_cable and max_cable > 0 else np.ones_like(D, bool)
    INF = 1e9
    cols, owner = [], []
    for j in range(len(mvs_list)):
        for side in _slot_sides(max_ratio):
            cols.append(np.where(feasible[:, j],
                                 _cable_cost(D[:, j]) + _side_penalty(off[:, j], side), INF))
            owner.append(j)
    cost = np.column_stack(cols)
    r_ind, c_ind = _solve_lap(cost, INF)
    used = {owner[c] for r, c in zip(r_ind, c_ind) if cost[r, c] < INF}
    if (sum(cost[r, c] < INF for r, c in zip(r_ind, c_ind)) != len(bess_list)
            or len(used) != len(mvs_list)):
        return mvs_list, bess_list   # never drop a BESS or empty a station
    for m in mvs_list:
        m["assigned_bess"] = []
    for r, c in zip(r_ind, c_ind):
        m, b = mvs_list[owner[c]], bess_list[r]
        b["mvs"] = m
        m["assigned_bess"].append(b)
    return [m for m in mvs_list if m["assigned_bess"]], bess_list


def _drop_group(D, feasible, n_bess, k, cap):
    """MVS group of size ``k`` found by greedy closing: start from every
    station, assign ``n_bess`` BESS (convex cable cost, ``cap`` per MVS) and
    repeatedly close the least-loaded station until ``k`` remain. Unlike
    windows, this yields spread-out groups (e.g. every other station along a
    long zone) when the cable cap demands it. Returns a tuple or None."""
    from .colocation import _solve_lap

    INF = 1e9
    g = list(range(D.shape[1]))
    while len(g) > k:
        rows = np.flatnonzero(feasible[:, g].any(axis=1))
        if len(rows) < n_bess:
            return None
        sub = np.where(feasible[np.ix_(rows, g)], _cable_cost(D[np.ix_(rows, g)]), INF)
        cost = np.hstack([np.repeat(sub, cap, axis=1),
                          np.zeros((len(rows), len(rows) - n_bess))])
        r_ind, c_ind = _solve_lap(cost, INF)
        load = np.zeros(len(g))
        spend = np.zeros(len(g))
        n_slots = len(g) * cap
        for r, c in zip(r_ind, c_ind):
            if c < n_slots and cost[r, c] < INF:
                load[c // cap] += 1
                spend[c // cap] += cost[r, c]
        if load.sum() < n_bess:
            return None
        # Close the station serving the fewest BESS (ties: the costliest).
        g.pop(int(np.lexsort((-spend, load))[0]))
    return tuple(g)


def _target_subset(mvs_raw, bess_raw, n_bess, n_mvs, max_cable,
                   zone_b=None, zone_m=None, soft_zones=False):
    """Pick exactly ``n_bess`` BESS on exactly ``n_mvs`` MVS out of one raw
    packing, balanced (every MVS gets ``floor`` or ``ceil`` of
    ``n_bess / n_mvs``), minimising the convex LV cable cost (BESS->MVS, see
    ``CABLE_REF_M``) + MV cable (MST between the chosen MVS) — i.e. the most
    compact block with every BESS as close as possible to its station.
    Returns ``(score, mvs_idx, [(bess_idx, mvs_idx), ...])`` or None.

    Candidate MVS groups are (a) contiguous windows in shelf-sweep order — the
    plant fills the site from one side, leaving the rest free — (b) the
    ``n_mvs`` nearest stations around each MVS and (c) a greedy-closing group
    (``_drop_group``) that can spread stations out along a long zone. For each group a min-cost
    assignment picks the BESS: each MVS has ``floor`` mandatory slots (cost
    biased so they are always filled) plus one optional slot when the split is
    uneven, and ``n_rows - n_bess`` zero-cost "not built" columns let the
    farthest containers drop out.

    Each MVS's slots are split between both sides along its own shelf-stacking
    axis (see ``CENTER_PENALTY``) so the station ends up in the middle of its
    cluster.

    With ``zone_b`` / ``zone_m`` (buildable-zone index per BESS / MVS), a BESS
    may only feed an MVS in its own zone — no LV cable crosses non-buildable
    land. With ``soft_zones`` a crossing is allowed but costs
    ``CROSS_PENALTY``, so as few as possible are used.
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
    CROSS = np.zeros_like(D)
    if zone_b is not None:
        same = zone_b[:, None] == zone_m[None, :]
        if soft_zones:
            CROSS = np.where(same, 0.0, CROSS_PENALTY)
        else:
            feasible &= same
    OFF = _stack_offsets(bp, mvs_raw)
    sides = _slot_sides(cap)

    groups = {tuple(range(i, i + k)) for i in range(n_m - k + 1)}
    MD = np.hypot(mp[:, None, 0] - mp[None, :, 0], mp[:, None, 1] - mp[None, :, 1])
    for i in range(n_m):
        groups.add(tuple(sorted(np.argsort(MD[i], kind="stable")[:k].tolist())))
    dg = _drop_group(D, feasible, n_bess, k, cap)
    if dg is not None:
        groups.add(dg)

    INF, BONUS = 1e9, 1e6
    best = None
    for g in groups:
        g = list(g)
        rows = np.flatnonzero(feasible[:, g].any(axis=1))
        if len(rows) < n_bess:
            continue
        feas = feasible[np.ix_(rows, g)]
        dist = D[np.ix_(rows, g)]
        off = OFF[np.ix_(rows, g)]
        cross = CROSS[np.ix_(rows, g)]
        cols, owner, pens = [], [], []
        for j in range(k):
            for slot in range(cap):
                bonus = BONUS if slot < base else 0.0
                pen = _side_penalty(off[:, j], sides[slot]) + cross[:, j]
                cols.append(np.where(feas[:, j], _cable_cost(dist[:, j]) + pen - bonus, INF))
                owner.append(j)
                pens.append(pen)
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
                total += _cable_cost(dist[r, j]) + pens[c][r]
        if len(pairs) != n_bess or counts.min() < base:
            continue
        total += _mst_length(mp[g])
        if best is None or total < best[0] - 1e-6:
            best = (total, g, pairs)
    return best


def _select_layout(packings, zone_ids, n_bess, n_mvs, max_cable, allow_cross_zone):
    """Best ``n_bess`` BESS on ``n_mvs`` MVS across all ``packings`` (see
    ``_target_subset``). Every BESS is first kept on an MVS of its own buildable
    zone; LV cables across non-buildable land are allowed only as a last resort
    (and only with ``allow_cross_zone``). Returns
    ``(score, mvs_raw, bess_raw, pairs, transpose, mvs_every)`` or None."""
    def once(same_zone):
        best = None
        for (transpose, mvs_every, mvs_raw, bess_raw), (zm, zb) in zip(packings, zone_ids):
            sel = _target_subset(mvs_raw, bess_raw, n_bess, n_mvs, max_cable, zb, zm,
                                 soft_zones=not same_zone)
            if sel and (best is None or sel[0] < best[0] - 1e-6):
                best = (sel[0], mvs_raw, bess_raw, sel[2], transpose, mvs_every)
        return best

    best = once(same_zone=True)
    multi_zone = any(len(set(zm)) > 1 or len(set(zb)) > 1 for zm, zb in zone_ids)
    if best is None and allow_cross_zone and multi_zone:
        best = once(same_zone=False)
    return best


def _cross_zone_count(mvs_list, bess_list, zones):
    """BESS whose LV cable crosses non-buildable land (MVS in another zone)."""
    zb = _zone_index(bess_list, zones)
    zm = dict(zip(map(id, mvs_list), _zone_index(mvs_list, zones)))
    return int(sum(zm[id(b["mvs"])] != z for b, z in zip(bess_list, zb)))


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


def _rows_label(transpose, mvs_every):
    if transpose is None:
        return "rows packed per buildable zone"
    return f"{'vertical' if transpose else 'horizontal'} rows, MVS every {mvs_every}"


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
    if m.get("cross_zone_cables"):
        print(f"WARNING: {m['cross_zone_cables']} BESS cable(s) cross non-buildable land "
              "(needed to reach the maximum).")
    t = res.get("target")
    if t:
        status = "MET" if t["met"] else f"NOT MET — max {t['achieved_mwh']:.1f} MWh fits"
        print(f"Target             : {t['target_mwh']:.1f} MWh @ {t['duration_h']:g}H "
              f"({t['target_bess']} BESS / {t['target_mvs']} MVS) -> {status}")
        print(f"Plant              : {t['achieved_mwh']:.1f} MWh | {t['achieved_mw']:.1f} MW | "
              f"{t['achieved_duration_h']:.2f} h | up to {t['bess_per_mvs']} BESS/MVS")
        if t.get("cross_zone_cables"):
            print(f"WARNING: {t['cross_zone_cables']} BESS cable(s) cross non-buildable land "
                  "(needed to fit more BESS; allow_cross_zone=False forbids it).")


def run_row_packing(config, verbose=False):
    """Top-level entry point for the row/shelf packing engine — maximises the
    number of BESS.

    1. Packs the site for both global orientations and several MVS-row
       densities/offsets, and finds the largest number of BESS that can be
       served (Hungarian assignment under the cable and per-MVS caps).
    2. Lays out exactly that many BESS on the fewest MVS with the same
       selection as the target-sizing study: convex cable cost (every BESS
       close to its station), MVS centred in its cluster, compact block, and
       LV cables kept inside one buildable zone unless crossing is the only
       way to reach the maximum.
    """
    site_raw = create_site(config["site_vertices"])
    site, non_buildable, restricted = prepare_site(site_raw, config)

    bess_eq = config["equipment"]["BESS"]
    mvs_eq = config["equipment"]["MVS"]
    max_ratio = config["max_bess_per_mvs"]

    max_cable = config.get("max_cable_length", 25)

    # Sweep both global orientations and a few MVS densities; keep the layout
    # that serves the most BESS after cable-cap assignment.
    # The MVS-row offsets are also tried at the cap density, so the maximum is
    # searched over the same candidates the target-sizing study uses.
    zones = _buildable_zones(site, non_buildable)
    packings = _pack_all(site, non_buildable, restricted, bess_eq, mvs_eq,
                         _mvs_every_options([max_ratio]), shift_for={max_ratio},
                         zones=zones, max_ratio=max_ratio, max_cable=max_cable)
    per_packing = []
    served, mvs_list, bess_list, transpose, mvs_every, dropped = _best_max_fill(
        packings, max_ratio, max_cable, per_packing)

    best = None
    if served:
        # Only packings that can serve the maximum can host the final layout.
        top = [pk for pk, n in zip(packings, per_packing) if n == served]
        zone_ids = [(_zone_index(m, zones), _zone_index(b, zones)) for _, _, m, b in top]
        best = _select_layout(top, zone_ids, served, -(-served // max_ratio),
                              max_cable, allow_cross_zone=True)
    if best is not None:
        _, mvs_raw, bess_raw, pairs, transpose, mvs_every = best
        mvs_list, bess_list = _build_from_subset(mvs_raw, bess_raw, pairs)
        dropped = 0
    else:
        # Fallback: keep the max-fill assignment, re-paired so each MVS sits
        # mid-cluster (same BESS / MVS counts).
        mvs_list, bess_list = _rebalance_clusters(
            mvs_list, bess_list, max_ratio, max_cable)

    res = _result(site, non_buildable, restricted, mvs_list, bess_list,
                  max_ratio, dropped)
    res["metrics"]["cross_zone_cables"] = _cross_zone_count(mvs_list, bess_list, zones)
    if verbose:
        _print_summary(res, f"ROW PACK ({_rows_label(transpose, mvs_every)})")
    return res


def _mvs_needed(n_bess, unit_mwh, duration_h, mvs_mw, max_ratio):
    """MVS stations needed so ``n_bess`` containers discharge in ``duration_h``
    without any MVS exceeding ``max_ratio`` BESS (the equipment's hard cap)."""
    by_power = int(np.ceil(n_bess * unit_mwh / duration_h / mvs_mw - 1e-6))
    by_cap = -(-n_bess // max_ratio)
    return max(1, by_power, by_cap)


def run_target_packing(config, target_mwh, durations=(4,), allow_cross_zone=True,
                       verbose=False):
    """EXTRA — Row Pack sized to an energy target at a chosen system duration.

    For each duration (hours) in ``durations``:
      * BESS = ceil(target_mwh / bess_unit_mwh)
      * MVS  = ceil(BESS energy / duration / mvs_station_mw), and never fewer
        than ceil(BESS / max_bess_per_mvs) — no MVS exceeds the equipment cap
    i.e. the duration fixes the plant power and hence the station count; BESS
    are split evenly across the MVS (floor/ceil). Across every packing of the
    site it keeps the block with minimum convex LV cost + MV cable (the same
    selection ``run_row_packing`` uses for its maximum), first requiring every
    BESS to feed an MVS in its own buildable zone (no LV cable across
    non-buildable land) and relaxing that only if the target cannot be met
    otherwise (``target["cross_zone_cables"]`` counts them);
    ``allow_cross_zone=False`` never relaxes it. If the target does not
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
    max_ratio = config["max_bess_per_mvs"]
    if unit_mwh <= 0 or mvs_mw <= 0:
        raise ValueError("bess_unit_mwh and mvs_station_mw must be > 0 to size to a target")
    if target_mwh <= 0:
        raise ValueError("target_mwh must be > 0 (use run_row_packing to maximise)")
    durations = [float(h) for h in durations]
    n_target = int(np.ceil(target_mwh / unit_mwh - 1e-9))

    # One packing sweep shared by every duration; MVS-row densities around the
    # BESS:MVS ratios these durations imply.
    ratios = {min(max_ratio, max(1, round(h * mvs_mw / unit_mwh))) for h in durations}
    # Buildable zone of every packed unit, to keep LV cables inside one zone.
    zones = _buildable_zones(site, non_buildable)
    packings = _pack_all(site, non_buildable, restricted, bess_eq, mvs_eq,
                         _mvs_every_options(ratios), shift_for=ratios,
                         zones=zones, max_ratio=max_ratio, max_cable=max_cable)
    zone_ids = [(_zone_index(m, zones), _zone_index(b, zones)) for _, _, m, b in packings]

    def solve(n, h):
        k = _mvs_needed(n, unit_mwh, h, mvs_mw, max_ratio)
        return _select_layout(packings, zone_ids, n, k, max_cable, allow_cross_zone)

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
        cross = _cross_zone_count(mvs_list, bess_list, zones)
        cap = max((len(m["assigned_bess"]) for m in mvs_list), default=0)
        target_mvs = _mvs_needed(n_target, unit_mwh, h, mvs_mw, max_ratio)
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
            "cross_zone_cables": cross,
            "met": n_b >= n_target,
            "shortfall_mwh": max(0.0, (n_target - n_b) * unit_mwh),
        }
        res = _result(site, non_buildable, restricted, mvs_list, bess_list,
                      max(cap, 1), 0, label=f"{h:g}H", target=target)
        results[int(h) if h.is_integer() else h] = res
        if verbose:
            _print_summary(res, f"TARGET {target_mwh:g} MWh @ {h:g}H "
                                f"({_rows_label(transpose, mvs_every)})")
    return results
