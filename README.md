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

| File | Role |
| :--- | :--- |
| [`engine.py`](engine.py) | Headless mathematical core. Polygon handling (`shapely`), clearance generation, multi-pass placement heuristics, and the Hungarian-algorithm global cable reassignment (`scipy.optimize.linear_sum_assignment`). |
| [`visualization.py`](visualization.py) | Rendering module. Static layouts via `matplotlib` (single, panel comparison) and interactive Plotly figures with hover metadata, click-selection and PNG export. |
| [`app.py`](app.py) | Streamlit application — three-phase UI (site definition → multi-mode benchmarking → interactive deep-dive and CSV export). |
| [`Layout.ipynb`](Layout.ipynb) | Notebook entry point for scripted, head-to-head scenario comparisons. |
| [`tests/test_engine.py`](tests/test_engine.py) | Smoke test driving the engine off the notebook's `CONFIG`. |

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

```python
from engine import run_bess_optimization
from visualization import plot_individual, print_comparison

CONFIG = {
    "site_vertices": [(0, 0), (53.3, 0), (53.3, 90.4), (0, 90.4)],
    "setback": 0,
    "zones": {"non_buildable": [], "restricted": []},
    "equipment": {
        "BESS": {
            "width": 6.06, "height": 2.44,
            "clearance": {"front": 2.0, "back": 1.0, "left": 1.0, "right": 1.0},
        },
        "MVS": {
            "width": 6.06, "height": 2.44,
            "clearance": {"front": 3.0, "back": 1.5, "left": 1.5, "right": 1.5},
        },
    },
    "max_bess_per_mvs": 4,
    "max_cable_length": 25,
    "grid_resolution": 2.0,
}

result = run_bess_optimization(CONFIG, mode="hyper_pack", verbose=True)
plot_individual(result, CONFIG)
```

To benchmark several strategies on the same site:

```python
modes = ["conservative", "aggressive", "ultra_aggressive", "hyper_pack"]
results = [run_bess_optimization(CONFIG, mode=m, verbose=False) for m in modes]
print_comparison(CONFIG, *results)
```

### 2. Interactive — the Streamlit application

```bash
streamlit run app.py
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

## Output

`run_bess_optimization` returns a dictionary containing the prepared site polygon, the non-buildable polygons, the lists of placed MVS and BESS objects (with footprint, clearance zone, rotation flag and assignment), and a `metrics` dictionary:

- `mvs_count`, `bess_count`, `full_mvs`
- `total_cable`, `avg_cable`, `max_cable_used`
- `buildable_area`, `equipment_area`
- `area_saturation_pct`, `capacity_saturation_pct`

## Tests

```bash
python -m tests.test_engine
```

The smoke test loads the configuration defined in `Layout.ipynb` and runs the `conservative` and `hyper_pack` modes end-to-end, printing the metrics block for each.

## Roadmap

- **Topographical awareness** — Z-axis elevation tiering for sloped sites.
- **Thermal heat-map overlays** — ambient HVAC clearance scoring.
- **DXF / GeoJSON export** — direct hand-off to CAD and GIS pipelines.
- **Multi-vendor equipment library** — pluggable container catalogue.