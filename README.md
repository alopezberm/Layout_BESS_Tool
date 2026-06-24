# BESS-Opt — Parametric Layout & Cabling Optimization Engine for Battery Energy Storage Systems

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)
![Shapely](https://img.shields.io/badge/Geometry-Shapely-1f7a8c?style=flat-square)
![SciPy](https://img.shields.io/badge/Solver-Hungarian-8CAAE6?style=flat-square&logo=scipy&logoColor=white)
![Status](https://img.shields.io/badge/Status-Production%20Ready-2ea44f?style=flat-square)

## Overview

**BESS-Opt** is a spatial optimization engine for utility-scale Battery Energy Storage System (BESS) plant design. It automates the placement of battery containers and their Medium-Voltage Stations (MVS) inside an arbitrary site polygon, honouring safety setbacks, non-buildable corridors, equipment clearances, and cable-length constraints — and then performs a global cable-routing reassignment to minimize copper.

What is traditionally a manual, multi-day CAD exercise is reduced to a parametric, repeatable run that produces engineering-grade KPIs (cluster count, total cable length, area saturation, plant power, plant energy) and an interactive layout that can be hand-tuned and exported.

## Repository Layout

The project is split into a headless core, a rendering layer, and two thin
front-ends (notebook + app) that share the core verbatim — no duplicated logic.

```
core/         layout & calculations (no UI, no plotting deps)
viz/          matplotlib + plotly + text rendering (imports core)
app/          Streamlit UI wrapper (imports core + viz)
Layout.ipynb  engineering notebook at the repo root (imports core + viz)
tests/        invariant tests
```

| Path | Role |
| :--- | :--- |
| [`core/config.py`](core/config.py) | Single source of truth: `DEFAULT_EQUIPMENT` + `build_config()`. Both modes build CONFIG here so they can never diverge. |
| [`core/geometry.py`](core/geometry.py) | Polygon handling (`shapely`), candidate grids, clearance generation, rotation, placement validity. |
| [`core/placement.py`](core/placement.py) | Multi-pass greedy placement heuristics + Hungarian global cable reassignment. |
| [`core/colocation.py`](core/colocation.py) | Co-located / paired-MVS hub engine (facility-location + hub-balanced assignment). |
| [`core/metrics.py`](core/metrics.py) | Layout KPIs and hub/civil-works metrics. |
| [`core/sizing.py`](core/sizing.py) | System sizing: total MW / MWh and 2H / 3H / 4H duration classification. |
| [`core/serialization.py`](core/serialization.py) | Layout ⇄ DataFrame round-trip and non-corrupting CSV export. |
| [`core/optimize.py`](core/optimize.py) | `run_bess_optimization` / `run_colocated_optimization` + `MODE_PROFILES`. |
| [`viz/`](viz/) | `matplotlib_plots`, `plotly_plots`, `compare` (text table). |
| [`app/app.py`](app/app.py) | Streamlit UI — three-phase workflow; widgets → `build_config` → core → viz. |
| [`Layout.ipynb`](Layout.ipynb) | Standalone engineering notebook at the repo root, no app dependency. |
| [`tests/test_engine.py`](tests/test_engine.py) | Invariant tests (no overlaps, capacity, in-bounds, sizing). |

## Algorithmic Highlights

1. **Dynamic Grid Resolution** — the seeding pass uses a coarse 2.0 m grid for speed; the `hyper_pack` mode follows up with a 0.5 m micro-grid pass to slot stragglers into otherwise-unreachable gaps.
2. **Per-Component 90° Rotation** — both BESS containers and MVS units independently evaluate 0° / 90° orientations, with asymmetric clearance zones remapped accordingly.
3. **Row-Alignment Penalty** — a configurable cost term coerces BESS units to inherit their parent MVS orientation and X/Y axis, producing clean architectural rows when uniformity matters.
4. **Global Min-Cost Reassignment** — after greedy placement, a Hungarian solver re-pairs every BESS to the closest feasible MVS slot across the entire site, minimizing the true Euclidean cable sum subject to the per-MVS capacity cap.
5. **Collision-Aware Editing** — manual edits in the UI re-run the placement validator so any out-of-bounds or overlapping component is flagged immediately.

## Operating Modes

| Mode | Strategy | Cable Routing | Density |
| :--- | :--- | :--- | :--- |
| **Conservative** | Strict cluster adjacency, heavy alignment penalty, clean uniform blocks. | Hard 25 m cap, cluster-bound. | Baseline |
| **Aggressive** | Relaxed adjacency, greedy distance matching. | Hard 25 m cap. | High |
| **Ultra-Aggressive** | No cable cap; MVS scaled linearly to support whatever fits. | Unlimited, Hungarian reassignment. | Maximum |
| **Hyper-Pack** | Adds a 0.5 m fine-grid saturation pass on top of Ultra-Aggressive. | Unlimited, Hungarian reassignment. | Physical saturation |

## Installation

Python 3.12 is recommended. Core runtime dependencies:

```bash
pip install numpy shapely scipy matplotlib plotly streamlit pandas
```

## Usage

### 1. Programmatic — the engine in a script or notebook

`build_config` is the single config constructor (equipment defaults to the
mandated `DEFAULT_EQUIPMENT`); the notebook and the app both use it.

```python
from core import build_config, run_bess_optimization, size_system
from viz import plot_individual, print_comparison

CONFIG = build_config(
    site_vertices=[(0, 0), (53.3, 0), (53.3, 90.4), (0, 90.4)],
    non_buildable=[],
    restricted=[],
    max_bess_per_mvs=4,
    max_cable_length=25,
    grid_resolution=2.0,
    bess_unit_mwh=5.0,
    mvs_station_mw=2.5,
)

result = run_bess_optimization(CONFIG, mode="hyper_pack", verbose=True)
plot_individual(result, CONFIG)

# System sizing: total MW / MWh and 2H / 3H / 4H duration class.
m = result["metrics"]
print(size_system(m["bess_count"], m["mvs_count"],
                  CONFIG["bess_unit_mwh"], CONFIG["mvs_station_mw"]))
```

To benchmark several strategies on the same site:

```python
modes = ["conservative", "aggressive", "ultra_aggressive", "hyper_pack"]
results = [run_bess_optimization(CONFIG, mode=m, verbose=False) for m in modes]
print_comparison(CONFIG, *results)
```

The standalone notebook lives at [`Layout.ipynb`](Layout.ipynb) (repo root)
and depends only on `core` + `viz` (no app).

### 2. Interactive — the Streamlit application

```bash
streamlit run app/app.py
```

The app exposes a three-phase workflow:

1. **Site definition** — paste the property boundary, non-buildable corridors and restricted zones as vertex lists; tune BESS / MVS clearances, max BESS per MVS, and commercial MWh / MW scaling factors.
2. **Multi-scenario benchmarking** — all four modes are solved in parallel and compared side-by-side on BESS count, MVS count, plant energy, plant power, total cable length, and area saturation.
3. **Deep-dive editor** — click a unit on the interactive Plotly canvas (or pick from the dropdown) to nudge its X/Y coordinates, toggle rotation, reassign its MVS network, or delete it. Collisions are validated live. Layouts can be exported as a 1920×1080 PNG and the bill of quantities as a CSV report.

Benchmark results are cached per input hash, so re-entering Phase 2 with unchanged parameters is instantaneous.

## Configuration Reference

| Key | Meaning |
| :--- | :--- |
| `site_vertices` | Ordered list of `(x, y)` tuples defining the outer property boundary. |
| `setback` | Inward buffer applied to the site before placement (metres). |
| `zones.non_buildable` | List of polygons where equipment may not be placed (access roads, cable corridors). |
| `zones.restricted` | List of polygons entirely excluded from the usable area. |
| `equipment.{BESS,MVS}.width/height` | Container footprint in metres. |
| `equipment.{BESS,MVS}.clearance` | Per-side clearance dictionary (`front`, `back`, `left`, `right`). |
| `max_bess_per_mvs` | Capacity cap for each MVS cluster. |
| `max_cable_length` | Maximum Euclidean BESS-to-MVS distance (set to `0` to disable). |
| `grid_resolution` | Coarse seeding grid spacing (metres). |
| `mvs_scoring_radius` | Radius used to score candidate MVS positions by nearby demand. |
| `min_mvs_spacing` | Minimum centre-to-centre spacing between MVS stations. |
| `bess_unit_mwh` / `mvs_station_mw` | Per-unit ratings used by `core.sizing.size_system`. |
| `colocation` | Co-located/hub parameters (`group_size`, `pad_gap`, `balance_tolerance`, `hub_search_radius`, `target_hub_count`). |

## Output

`run_bess_optimization` returns a dictionary containing the prepared site polygon, the non-buildable polygons, the lists of placed MVS and BESS objects (with footprint, clearance zone, rotation flag and assignment), and a `metrics` dictionary:

- `mvs_count`, `bess_count`, `full_mvs`
- `total_cable`, `avg_cable`, `max_cable_used`
- `buildable_area`, `equipment_area`
- `area_saturation_pct`, `capacity_saturation_pct`

## Tests

```bash
python -m pytest tests/ -q        # or: python tests/test_engine.py
```

The suite runs every mode (plus the co-located engine) end-to-end on the
default site and asserts the invariants any valid layout must satisfy: no
footprint/clearance overlaps, per-MVS capacity respected, all equipment inside
the site and out of non-buildable zones, and sane 2H/3H/4H sizing.

## Roadmap

- **Topographical awareness** — Z-axis elevation tiering for sloped sites.
- **Thermal heat-map overlays** — ambient HVAC clearance scoring.
- **DXF / GeoJSON export** — direct hand-off to CAD and GIS pipelines.
- **Multi-vendor equipment library** — pluggable container catalogue.