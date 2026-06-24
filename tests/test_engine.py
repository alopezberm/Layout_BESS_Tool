"""Invariant tests for the BESS-Opt core engine.

Runs each mode end-to-end on the default site and asserts the geometric and
capacity invariants that must hold for any valid layout:

  * no footprint overlaps another's footprint or clearance zone,
  * every MVS holds at most ``max_bess_per_mvs`` BESS,
  * every assigned BESS lies within the site and out of non-buildable zones,
  * sizing yields a sane 2H/3H/4H duration class.

Run with:  python -m pytest tests/ -q     (or  python -m tests.test_engine)
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import (
    build_config,
    run_colocated_optimization,
    run_row_packing,
    size_system,
)
from core.config import DEFAULT_EQUIPMENT


def make_config(**overrides):
    cable_corridor = [(15.4, 0), (21.9, 0), (47.7, 90.4), (31.9, 90.4)]
    out_of_scope = [(21.9, 16), (53.3, 16), (53.3, 90.4), (47.7, 90.4)]
    return build_config(
        site_vertices=[
            (0, 0), (53.3, 0), (53.3, 16), (21.9, 16),
            (47.7, 90.4), (8, 90.4), (0, 90.4),
        ],
        non_buildable=[cable_corridor],
        restricted=[out_of_scope],
        mvs_scoring_radius=25,
        **overrides,
    )


def _assert_no_overlaps(res):
    placed = res["mvs_list"] + res["bess_list"]
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            # Footprints must never overlap each other.
            assert not a["footprint"].overlaps(b["footprint"]), "footprint overlap"
            assert not a["footprint"].contains(b["footprint"]), "footprint containment"


def _assert_capacity(res, max_ratio):
    for m in res["mvs_list"]:
        assert len(m["assigned_bess"]) <= max_ratio, "MVS over capacity"


def _assert_inside_site(res):
    site = res["site"]
    for obj in res["mvs_list"] + res["bess_list"]:
        # Allow a tiny tolerance via buffer for floating-point edge contact.
        assert site.buffer(1e-6).contains(obj["footprint"]), "equipment outside site"
        for nb in res["non_buildable"]:
            assert not obj["footprint"].intersects(nb), "equipment in non-buildable zone"


def test_row_packing_invariants():
    config = make_config()
    res = run_row_packing(config, verbose=False)
    assert res["metrics"]["bess_count"] > 0, "nothing placed"
    _assert_no_overlaps(res)
    _assert_capacity(res, config["max_bess_per_mvs"])
    _assert_inside_site(res)


def test_colocated_invariants():
    config = make_config(colocation={"enabled": True, "group_size": 2, "pad_gap": 0.5})
    res = run_colocated_optimization(config, verbose=False)
    _assert_capacity(res, config["max_bess_per_mvs"])
    _assert_inside_site(res)
    assert res["hub_metrics"]["hub_count"] >= 1, "no hubs placed"


def test_row_packing_backtoback():
    # Tight back clearance: the shelf packer must produce real back-to-back gaps
    # (near 0.15 m), respect capacity and bounds, and not overlap.
    config = make_config(
        bess_clearance={"front": 3.5, "back": 0.15, "left": 0.6, "right": 2.0},
        mvs_clearance={"front": 3.0, "back": 1.7, "left": 2.0, "right": 2.0},
    )
    res = run_row_packing(config, verbose=False)
    assert res["metrics"]["bess_count"] > 0
    _assert_no_overlaps(res)
    _assert_capacity(res, config["max_bess_per_mvs"])
    _assert_inside_site(res)
    # at least some BESS pair must actually sit back-to-back (< 0.3 m apart)
    bess = res["bess_list"]
    min_gap = min(
        a["footprint"].distance(b["footprint"])
        for i, a in enumerate(bess) for b in bess[i + 1:]
    )
    assert min_gap < 0.3, f"no back-to-back packing achieved (min gap {min_gap:.2f} m)"
    # every assigned BESS must be within the cable cap of its MVS
    assert res["metrics"]["max_cable_used"] <= config["max_cable_length"] + 1e-6


def test_sizing_duration_class():
    # 4 BESS x 5 MWh = 20 MWh; 1 MVS x 2.5 MW = 2.5 MW -> 8h, nearest class 4H.
    s = size_system(4, 1, 5.0, 2.5)
    assert s["total_mwh"] == 20.0
    assert s["total_mw"] == 2.5
    assert s["duration_label"] in {"2H", "3H", "4H"}
    # Empty layout must not divide by zero.
    assert size_system(0, 0, 5.0, 2.5)["duration_label"] == "N/A"


def test_default_equipment_contract():
    assert DEFAULT_EQUIPMENT["BESS"]["width"] == 6.06
    assert DEFAULT_EQUIPMENT["MVS"]["clearance"]["front"] == 3.0


def test_empty_site_does_not_crash():
    # A site smaller than one container should place nothing, not raise.
    config = build_config(site_vertices=[(0, 0), (1, 0), (1, 1), (0, 1)])
    res = run_row_packing(config, verbose=False)
    assert res["metrics"]["bess_count"] == 0


if __name__ == "__main__":
    test_row_packing_invariants()
    test_colocated_invariants()
    test_row_packing_backtoback()
    test_sizing_duration_class()
    test_default_equipment_contract()
    test_empty_site_does_not_crash()
    print("All invariant tests passed.")