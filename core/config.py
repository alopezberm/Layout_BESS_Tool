"""Single source of truth for equipment definitions and run configuration.

Both the notebook and the Streamlit app build their CONFIG through
``build_config`` so the two execution modes can never silently diverge.
"""

import copy

# Mandated equipment geometry (metres). Defined ONCE here and reused everywhere.
DEFAULT_EQUIPMENT = {
    "BESS": {
        "width": 6.06,
        "height": 2.44,
        "clearance": {"front": 2.0, "back": 1.0, "left": 1.0, "right": 1.0},
    },
    "MVS": {
        "width": 6.06,
        "height": 2.44,
        "clearance": {"front": 3.0, "back": 1.5, "left": 1.5, "right": 1.5},
    },
}

# Engine defaults. These are the values used by BOTH modes unless overridden,
# which is what guarantees identical results across notebook and app.
DEFAULT_PARAMS = {
    "setback": 0,
    "max_bess_per_mvs": 4,
    "max_cable_length": 25,
    "mvs_scoring_radius": 25,
    "min_mvs_spacing": 0,
    "grid_resolution": 2.0,
    # Commercial / sizing scaling factors (see core.sizing).
    "bess_unit_mwh": 5.0,
    "mvs_station_mw": 2.5,
}

# Default co-location (paired / hub) parameters.
DEFAULT_COLOCATION = {
    "enabled": False,
    "group_size": 2,
    "pad_gap": 0.5,
    "balance_tolerance": 1,
    "hub_search_radius": 8.0,
    "target_hub_count": 0,
}


def build_config(
    site_vertices,
    non_buildable=None,
    restricted=None,
    equipment=None,
    bess_clearance=None,
    mvs_clearance=None,
    colocation=None,
    **overrides,
):
    """Assemble a validated CONFIG dict.

    Parameters
    ----------
    site_vertices : list[tuple]
        Ordered (x, y) outer property boundary.
    non_buildable, restricted : list[list[tuple]] | None
        Lists of polygons (each a list of vertices). A single polygon may also
        be passed and will be wrapped automatically.
    equipment : dict | None
        Full equipment override; defaults to ``DEFAULT_EQUIPMENT``.
    bess_clearance, mvs_clearance : dict | None
        Convenience per-side clearance overrides (front/back/left/right) applied
        on top of the default equipment without restating width/height.
    colocation : dict | None
        Co-location overrides merged onto ``DEFAULT_COLOCATION``.
    **overrides
        Any of the keys in ``DEFAULT_PARAMS`` (e.g. ``max_bess_per_mvs``).
    """
    eq = copy.deepcopy(equipment) if equipment is not None else copy.deepcopy(DEFAULT_EQUIPMENT)
    if bess_clearance:
        eq["BESS"]["clearance"].update(bess_clearance)
    if mvs_clearance:
        eq["MVS"]["clearance"].update(mvs_clearance)

    params = dict(DEFAULT_PARAMS)
    unknown = set(overrides) - set(DEFAULT_PARAMS)
    if unknown:
        raise ValueError(f"Unknown config override(s): {sorted(unknown)}")
    params.update(overrides)

    config = {
        "site_vertices": list(site_vertices),
        "zones": {
            "non_buildable": _as_polygon_list(non_buildable),
            "restricted": _as_polygon_list(restricted),
        },
        "equipment": eq,
        **params,
    }

    co = dict(DEFAULT_COLOCATION)
    if colocation:
        co.update(colocation)
    config["colocation"] = co

    return config


def _as_polygon_list(zones):
    """Normalise zone input to a list of polygons (each a list of vertices).

    Accepts: None -> []; a single polygon (list of (x, y) tuples) -> [polygon];
    a list of polygons -> unchanged.
    """
    if not zones:
        return []
    first = zones[0]
    # A bare polygon looks like [(x, y), (x, y), ...]; detect the (x, y) tuple.
    if isinstance(first, (tuple, list)) and len(first) == 2 and all(
        isinstance(c, (int, float)) for c in first
    ):
        return [list(zones)]
    return [list(z) for z in zones]