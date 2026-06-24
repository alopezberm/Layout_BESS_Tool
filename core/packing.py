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


def _pack(site, non_buildable, bess_eq, mvs_eq, max_ratio, transpose, mvs_every):
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
                if is_valid_placement(fp, cl, site, non_buildable, placed):
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


def run_row_packing(config, verbose=False):
    """Top-level entry point for the row/shelf packing engine.

    Tries both global orientations, keeps the denser one, then assigns BESS to
    MVS with the shared Hungarian solver (unlimited cable within the packed
    field) and surfaces any BESS that could not be assigned.
    """
    site_raw = create_site(config["site_vertices"])
    site, non_buildable = prepare_site(site_raw, config)

    bess_eq = config["equipment"]["BESS"]
    mvs_eq = config["equipment"]["MVS"]
    max_ratio = config["max_bess_per_mvs"]

    max_cable = config.get("max_cable_length", 25)

    # Sweep both global orientations and a few MVS densities; keep the layout
    # that serves the most BESS after cable-cap assignment.
    mvs_every_options = sorted({max_ratio, 2, 3, 5, 6}, reverse=True)
    best = None
    for transpose in (False, True):
        for mvs_every in mvs_every_options:
            mvs_l, bess_l = _pack(site, non_buildable, bess_eq, mvs_eq, max_ratio, transpose, mvs_every)
            if mvs_l and bess_l:
                mvs_l, bess_l, dr = _optimal_reassign(
                    mvs_l, bess_l, max_cable if max_cable else 0, max_ratio)
            else:
                dr = 0
            served = len(bess_l)
            if best is None or served > best[0]:
                best = (served, mvs_l, bess_l, transpose, mvs_every, dr)

    _, mvs_list, bess_list, transpose, mvs_every, dropped = best

    metrics = compute_metrics(site, non_buildable, mvs_list, bess_list, max_ratio)
    metrics["dropped_bess"] = dropped

    if verbose:
        print(f"\n----- ROW PACK ({'vertical' if transpose else 'horizontal'} rows, MVS every {mvs_every}) -----")
        print(f"MVS units placed   : {metrics['mvs_count']}")
        print(f"BESS units placed  : {metrics['bess_count']}")
        print(f"Fully sat. MVS     : {metrics['full_mvs']} / {metrics['mvs_count']}")
        print(f"Total cable        : {metrics['total_cable']:.1f} m")
        print(f"Capacity saturation: {metrics['capacity_saturation_pct']:.1f}%")
        if dropped:
            print(f"NOTE: {dropped} extra BESS packed beyond total MVS capacity (unassigned).")

    return {
        "mode": "row_pack",
        "site": site,
        "non_buildable": non_buildable,
        "mvs_list": mvs_list,
        "bess_list": bess_list,
        "metrics": metrics,
    }