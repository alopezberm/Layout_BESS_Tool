import json

def patch_notebook(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        nb = json.load(f)

    for cell in nb['cells']:
        if cell['cell_type'] != 'code':
            continue
        
        source = "".join(cell['source'])
        
        if "def place_clusters" in source:
            old_mvs_loop = '''        for (mx, my) in avail:
            mvs_fp = create_equipment_polygon(mx, my, mvs_eq["width"], mvs_eq["height"])
            mvs_cl = create_clearance_polygon(mvs_fp, mvs_cl_dict)
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
                best = (mvs_fp, mvs_cl)

        if best is None:
            break

        mvs_fp, mvs_cl = best
        mvs_obj = {
            "type":           "MVS",
            "footprint":      mvs_fp,
            "clearance_zone": mvs_cl,
            "assigned_bess":  [],
        }'''

            new_mvs_loop = '''        for (mx, my) in avail:
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
        }'''
            source = source.replace(old_mvs_loop, new_mvs_loop)

            old_try_add = '''    for (mx, my) in create_candidate_grid(site, fine_resolution):
        mvs_fp = create_equipment_polygon(mx, my, w, h)
        mvs_cl = create_clearance_polygon(mvs_fp, mvs_cl_dict)
        if is_valid_placement(mvs_fp, mvs_cl, site, non_buildable, placed):
            return {
                "type":           "MVS",
                "footprint":      mvs_fp,
                "clearance_zone": mvs_cl,
                "assigned_bess":  [],
            }
    return None'''

            new_try_add = '''    for (mx, my) in create_candidate_grid(site, fine_resolution):
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
    return None'''
            source = source.replace(old_try_add, new_try_add)

        if "def plot_layout" in source:
            old_plot = '''        mvs_cl_poly = create_clearance_polygon(mvs["footprint"], mvs_cl_cfg)'''
            
            new_plot = '''        if mvs.get("rotated", False):
            r_mvs_cl_cfg = {
                "front": mvs_cl_cfg["left"],
                "back": mvs_cl_cfg["right"],
                "left": mvs_cl_cfg["back"],
                "right": mvs_cl_cfg["front"],
            }
        else:
            r_mvs_cl_cfg = mvs_cl_cfg
        mvs_cl_poly = create_clearance_polygon(mvs["footprint"], r_mvs_cl_cfg)'''
            source = source.replace(old_plot, new_plot)

        cell['source'] = source.splitlines(keepends=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(nb, f, indent=1)

if __name__ == '__main__':
    patch_notebook('c:/Users/Alejandro/GitHub/Layout_BESS_Tool/Layout.ipynb')
    print("Patch applied to Layout.ipynb for MVS rotation.")
