"""Round-trip between engine layouts and tabular form, plus CSV export.

Moved out of the Streamlit app so the notebook gets the same DataFrame export
and the same collision-validated re-import. Depends on pandas + core geometry.
"""

import pandas as pd

from .geometry import (
    get_oriented_dimensions,
    create_equipment_polygon,
    create_clearance_polygon,
    is_valid_placement,
)


def _row_angle(row):
    """Resolve an orientation angle (0/90/180/270) from a DataFrame row,
    tolerating the legacy boolean ``Rotated`` column. Works for a pandas Series
    or a plain dict (both expose ``.get``)."""
    angle = row.get("Angle", None)
    if angle is None or (isinstance(angle, float) and angle != angle):  # None / NaN
        return 90 if bool(row.get("Rotated", False)) else 0
    return int(angle) % 360


def engine_to_df(mvs_list, bess_list):
    """Flatten a placed layout to a DataFrame (ID, Type, X, Y, Rotated,
    Assigned_MVS)."""
    rows = []
    mvs_map = {id(m): f"M{i+1}" for i, m in enumerate(mvs_list)}
    for m in mvs_list:
        angle = int(m.get("angle", 90 if m.get("rotated") else 0))
        rows.append({
            "ID": mvs_map[id(m)],
            "Type": "MVS",
            "X": float(m["footprint"].bounds[0]),
            "Y": float(m["footprint"].bounds[1]),
            "Angle": angle,
            "Rotated": angle in (90, 270),
            "Assigned_MVS": None,
        })
    for i, b in enumerate(bess_list):
        assigned_m = mvs_map.get(id(b["mvs"]), None) if "mvs" in b and b["mvs"] else None
        angle = int(b.get("angle", 90 if b.get("rotated") else 0))
        rows.append({
            "ID": f"B{i+1}",
            "Type": "BESS",
            "X": float(b["footprint"].bounds[0]),
            "Y": float(b["footprint"].bounds[1]),
            "Angle": angle,
            "Rotated": angle in (90, 270),
            "Assigned_MVS": assigned_m,
        })
    return pd.DataFrame(rows)


def engine_to_df_with_hubs(mvs_list, bess_list):
    """Co-located export: the standard DataFrame plus a Hub_ID column tying each
    MVS (and each BESS, via its parent MVS) to its shared foundation pad."""
    df = engine_to_df(mvs_list, bess_list)
    mvs_hub = {f"M{i+1}": (m.get("hub_id") or "") for i, m in enumerate(mvs_list)}

    def _hub_for(row):
        if row["Type"] == "MVS":
            return mvs_hub.get(row["ID"], "")
        return mvs_hub.get(row["Assigned_MVS"], "")

    df["Hub_ID"] = df.apply(_hub_for, axis=1)
    return df


def df_to_engine(df, config, site, non_buildable):
    """Rebuild engine objects from an (edited) DataFrame, validating each
    placement against the site and previously-placed equipment. Returns
    ``(mvs_list, bess_list, errors)``."""
    mvs_list = []
    bess_list = []
    placed = []
    errors = []

    bess_eq = config["equipment"]["BESS"]
    mvs_eq = config["equipment"]["MVS"]

    mvs_df = df[df["Type"] == "MVS"]
    mvs_map = {}
    for _, row in mvs_df.iterrows():
        angle = _row_angle(row)
        w, h, cl = get_oriented_dimensions(mvs_eq["width"], mvs_eq["height"], mvs_eq["clearance"], angle)
        fp = create_equipment_polygon(row["X"], row["Y"], w, h)
        cl_poly = create_clearance_polygon(fp, cl)

        mvs_obj = {
            "type": "MVS",
            "footprint": fp,
            "clearance_zone": cl_poly,
            "assigned_bess": [],
            "angle": angle,
            "rotated": angle in (90, 270),
            "id": row["ID"],
        }

        if not is_valid_placement(fp, cl_poly, site, non_buildable, placed):
            errors.append(f"Collision Detected: MVS {row['ID']} overlaps with obstacles/equipment or goes out of bounds.")

        mvs_list.append(mvs_obj)
        placed.append(mvs_obj)
        mvs_map[row["ID"]] = mvs_obj

    bess_df = df[df["Type"] == "BESS"]
    for _, row in bess_df.iterrows():
        angle = _row_angle(row)
        w, h, cl = get_oriented_dimensions(bess_eq["width"], bess_eq["height"], bess_eq["clearance"], angle)
        fp = create_equipment_polygon(row["X"], row["Y"], w, h)
        cl_poly = create_clearance_polygon(fp, cl)

        assigned_mvs = mvs_map.get(row["Assigned_MVS"], None)

        bess_obj = {
            "type": "BESS",
            "footprint": fp,
            "clearance_zone": cl_poly,
            "mvs": assigned_mvs,
            "angle": angle,
            "rotated": angle in (90, 270),
            "id": row["ID"],
        }

        if assigned_mvs:
            assigned_mvs["assigned_bess"].append(bess_obj)

        if not is_valid_placement(fp, cl_poly, site, non_buildable, placed):
            errors.append(f"Collision Detected: BESS {row['ID']} overlaps with obstacles/equipment or goes out of bounds.")

        bess_list.append(bess_obj)
        placed.append(bess_obj)

    return mvs_list, bess_list, errors


def layout_to_csv(df, summary=None):
    """Serialise a layout DataFrame to a valid CSV string.

    Any ``summary`` dict is emitted as leading ``#``-prefixed comment lines so
    the tabular body stays parseable by spreadsheets and pandas
    (``pd.read_csv(..., comment='#')``) instead of being corrupted by trailing
    free text.
    """
    header = ""
    if summary:
        header = "".join(f"# {k}: {v}\n" for k, v in summary.items())
    return header + df.to_csv(index=False)