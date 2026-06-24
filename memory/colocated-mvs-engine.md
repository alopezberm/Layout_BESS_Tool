---
name: colocated-mvs-engine
description: Design contract for the Co-Located / Paired MVS optimization engine (parallel to the baseline)
metadata:
  type: project
---

The "Co-Located / Paired MVS" feature is a **parallel scenario engine** added alongside the baseline standalone optimizer. Added 2026-06-16. **Restructured 2026-06-24** into packages: the flat `engine.py` / `visualization.py` / `app.py` were split into `core/` (config, geometry, placement, colocation, metrics, sizing, serialization, optimize), `viz/`, `app/app.py`, and `notebook/Layout.ipynb`. The co-located code now lives in `core/colocation.py` (+ `compute_hub_metrics` in `core/metrics.py`, `run_colocated_optimization` in `core/optimize.py`). Baseline functions were renamed public: `_compute_metrics`→`compute_metrics`, `_compute_hub_metrics`→`compute_hub_metrics`; `place_clusters` now takes a `profile` dict and returns `(mvs_list, bess_list, dropped)`. `MODE_PROFILES` is in `core/optimize.py`.

**Hard design contract — additive only:** The baseline heuristics (`place_clusters`, `_grow_cluster`, `_stragglers_pass`, `_optimal_reassign`, `compute_metrics`, etc.) keep their algorithm intact. Baseline behavior verified unchanged (10 MVS / 35 BESS on the default site).

**core/colocation.py symbols:** `_geometric_median` (Weiszfeld — minimizes Σdistance = trenching length, NOT centroid/squared distance), `_kmeanspp_init`, `_weighted_lloyd_relaxation`, `_determine_hub_count`, `_snap_hub_to_pad`, `place_clustered_hubs`, `_solve_lap`, `_optimal_reassign_hub_balanced`. Reuses `from shapely.geometry import Point as _Point` and imports `_prune_avail` from `core/placement.py`.

**Pipeline:** Stage 1 = facility-location (Lloyd + Weiszfeld) seeds hub centers BEFORE BESS exist → snap to valid shared pad. Stage 2 = unmodified baseline BESS packing (`_grow_cluster` + `_stragglers_pass`). Stage 3 = `_optimal_reassign_hub_balanced` (Hungarian via `_solve_lap` + bounded Lagrangian re-weighting for soft balance).

**Pad-clearance rule (critical):** In `_snap_hub_to_pad`, each MVS member is validated with `is_valid_placement` against EXTERNAL `placed` only — pad siblings are exempt from inter-member clearance. This is what lets co-located MVS sit tighter than standard clearance on one shared slab. Distinct hubs DO keep full clearance from each other.

**Metadata tags on hub MVS dicts:** `hub_id` (e.g. "H1"), `pad_position`, `hub_size`. `_compute_hub_metrics` and `plot_layout_plotly_hubs` (dashed pad outlines) rely on these. Empty MVS are NOT pruned in the balanced reassign (the pad is poured regardless).

**Balance is a SOFT target:** if the re-weighting iteration cap is hit, the layout is kept and the residual `balance_index` / `worst_hub_imbalance` is surfaced in KPIs (a `st.warning` shows if worst > tolerance). Never rejected.

**Target Hub Count vs Snap Search Radius behavior:** `target_hub_count` (0 = auto) overrides the computed k. On narrow/concave sites a low target can UNDER-place hubs if centers can't snap within `hub_search_radius` — observed 1 hub @ radius 8 vs 3 hubs @ radius 25 for target=3. Auto hub-count avoids this. Both exposed as sliders in the co-located tab.

**app/app.py:** thin UI wrapper — `st.tabs()` split "🏭 Standard Automated Design" (Phase 1/2/3) vs "🔗 Industrial Co-Located Design". Co-located tab uses isolated session_state keys `colocated_results` / `colocated_config`, never touches `benchmark_results`. Shared sidebar (clearances/capacity) serves both tabs. Config for both tabs is built via `core.build_config` (the `make_config` helper), so notebook and app can't diverge. `engine_to_df_with_hubs` (now in `core/serialization.py`) adds a `Hub_ID` column; CSV export uses `core.layout_to_csv` (summary as leading `#` comment lines, not appended free text).

Related: [[bess-tool-python-env]].