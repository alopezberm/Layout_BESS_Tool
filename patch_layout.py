import json

def patch_notebook(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        nb = json.load(f)

    for cell in nb['cells']:
        if cell['cell_type'] != 'code':
            continue
        
        source = "".join(cell['source'])
        
        # 1. Add get_rotated_dimensions to the cell with create_equipment_polygon
        if "def is_valid_placement" in source and "create_clearance_polygon" in source:
            if "def get_rotated_dimensions" not in source:
                addition = '''
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
'''
                source = source + addition
            
        # 2. Modify _grow_cluster, _stragglers_pass, _stragglers_pass_singlepass, _prune_avail
        if "def _grow_cluster" in source and "def _stragglers_pass" in source:
            old_prune = '''def _prune_avail(avail, blocker_fp, blocker_cl, bess_eq):
    """Drop grid positions whose BESS footprint/clearance conflicts with blocker."""
    bess_cl_dict = bess_eq["clearance"]
    w, h = bess_eq["width"], bess_eq["height"]
    out = []
    for (gx, gy) in avail:
        b_fp = create_equipment_polygon(gx, gy, w, h)
        b_cl = create_clearance_polygon(b_fp, bess_cl_dict)
        if b_fp.intersects(blocker_cl) or blocker_fp.intersects(b_cl):
            continue
        out.append((gx, gy))
    return out'''

            new_prune = '''def _prune_avail(avail, blocker_fp, blocker_cl, bess_eq):
    """Drop grid positions whose BESS footprint/clearance conflicts with blocker."""
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
    return out'''
            source = source.replace(old_prune, new_prune)

            old_grow = '''        for (gx, gy) in avail:
            b_fp = create_equipment_polygon(gx, gy, w, h)
            b_cl = create_clearance_polygon(b_fp, bess_cl_dict)
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
                    abs(c["footprint"].centroid.x - bx) < tol or
                    abs(c["footprint"].centroid.y - by) < tol
                    for c in members
                )
                if not aligned:
                    score += alignment_weight

            if score < best_score:
                best_score = score
                best = (b_fp, b_cl)

        if best is None:
            break

        b_fp, b_cl = best
        bess_obj = {
            "type":           "BESS",
            "footprint":      b_fp,
            "clearance_zone": b_cl,
            "mvs":            mvs_obj,
        }'''

            new_grow = '''        for (gx, gy) in avail:
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
        }'''
            source = source.replace(old_grow, new_grow)

            old_straggler = '''        for (gx, gy) in avail:
            b_fp = create_equipment_polygon(gx, gy, w, h)
            b_cl = create_clearance_polygon(b_fp, bess_cl_dict)
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
            candidates.append((best_d, b_fp, b_cl, best_mvs))

        if not candidates:
            break

        candidates.sort(key=lambda c: c[0])
        _, b_fp, b_cl, mvs = candidates[0]
        bess_obj = {
            "type":           "BESS",
            "footprint":      b_fp,
            "clearance_zone": b_cl,
            "mvs":            mvs,
        }'''
            
            new_straggler = '''        for (gx, gy) in avail:
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
        }'''
            source = source.replace(old_straggler, new_straggler)

            old_straggler_sp = '''    for (gx, gy) in fine_avail:
        b_fp = create_equipment_polygon(gx, gy, w, h)
        b_cl = create_clearance_polygon(b_fp, bess_cl_dict)
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
        candidates.append((best_d, b_fp, b_cl, bx, by))

    candidates.sort(key=lambda c: c[0])

    added = 0
    for _, b_fp, b_cl, bx, by in candidates:
        # Re-check validity against everything placed since the scan.
        if not is_valid_placement(b_fp, b_cl, site, non_buildable, placed):
            continue
        # Pick nearest MVS with remaining capacity at placement time.
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
        }'''
            
            new_straggler_sp = '''    for (gx, gy) in fine_avail:
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
        # Re-check validity against everything placed since the scan.
        if not is_valid_placement(b_fp, b_cl, site, non_buildable, placed):
            continue
        # Pick nearest MVS with remaining capacity at placement time.
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
        }'''
            source = source.replace(old_straggler_sp, new_straggler_sp)

        # 3. Modify plot_layout
        if "def plot_layout" in source:
            old_plot = '''        for bess in mvs["assigned_bess"]:
            ax.plot(
                [bess["footprint"].centroid.x, mvs["footprint"].centroid.x],
                [bess["footprint"].centroid.y, mvs["footprint"].centroid.y],
                color=col, linewidth=1.2, alpha=0.85, zorder=2,
            )
            bess_cl_poly = create_clearance_polygon(bess["footprint"], bess_cl_cfg)'''

            new_plot = '''        for bess in mvs["assigned_bess"]:
            ax.plot(
                [bess["footprint"].centroid.x, mvs["footprint"].centroid.x],
                [bess["footprint"].centroid.y, mvs["footprint"].centroid.y],
                color=col, linewidth=1.2, alpha=0.85, zorder=2,
            )
            if bess.get("rotated", False):
                r_bess_cl_cfg = {
                    "front": bess_cl_cfg["left"],
                    "back": bess_cl_cfg["right"],
                    "left": bess_cl_cfg["back"],
                    "right": bess_cl_cfg["front"],
                }
            else:
                r_bess_cl_cfg = bess_cl_cfg
            bess_cl_poly = create_clearance_polygon(bess["footprint"], r_bess_cl_cfg)'''
            source = source.replace(old_plot, new_plot)

        cell['source'] = source.splitlines(keepends=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(nb, f, indent=1)

if __name__ == '__main__':
    patch_notebook('c:/Users/Alejandro/GitHub/Layout_BESS_Tool/Layout.ipynb')
    print("Patch applied to Layout.ipynb.")
