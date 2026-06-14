# BESS-Opt: Parametric Layout & Cabling Optimization Engine for Battery Energy Storage Systems

![Layout Showcase](https://img.shields.io/badge/Optimization-2D%20Spatial%20Packing-blue?style=for-the-badge) ![Python](https://img.shields.io/badge/Python-3.12-yellow?style=for-the-badge) ![Status](https://img.shields.io/badge/Status-Production%20Ready-success?style=for-the-badge)

## Executive Summary
**BESS-Opt** is an advanced, algorithmically driven spatial optimization engine built for the utility-scale renewable energy sector. Designing the physical layout of Battery Energy Storage Systems (BESS) and Medium-Voltage Station (MVS) transformers is historically a manual, iterative, and labor-intensive process constrained by strict safety setbacks, cable length limits, and non-buildable zones.

This engine automatically solves the 2D spatial constraint packing problem. By digesting site polygons, dynamic clearance zones, and hardware footprints, BESS-Opt dynamically seeds transformer clusters, optimally packs battery containers, and executes a global min-cost cable routing assignment to maximize site energy density and minimize costly copper runs.

## Core Architecture
The repository has been decoupled into a headless mathematical core and an independent rendering module for maximum portability:

* **`engine.py`**: The mathematical powerhouse. Handles all coordinate manipulation via `shapely` polygons, dynamic clearance generation, obstacle intersection checks, and executes the multi-pass placement heuristics and Hungarian routing algorithms.
* **`visualization.py`**: The rendering module. Safely isolates `matplotlib` logic, mapping the engine's coordinate outputs into beautifully scaled layout graphics and console metric tables without polluting the core logic.
* **`Layout.ipynb`**: The pipeline execution playground. A minimalist entry point where site vertices, configuration dictionaries, and operational parameters are defined before passing them into the engine for head-to-head scenario comparisons.

## Algorithmic Innovation Highlights
The engine relies on several cutting-edge spatial strategies to push maximum energy density:

1. **Dynamic Grid Resolution**: Scales from rapid 2.0m seeding grids down to ultra-fine 0.25m/0.5m micro-grids to precisely slot straggler containers into impossibly tight gaps.
2. **Dual-Component 90° Cartesian Rotation**: Both the BESS blocks and MVS transformers dynamically rotate (0° / 90°), swapping Length/Width dimensions and mapping asymmetrical clearance zones instantly to clear boundaries and increase packing efficiency.
3. **Row-Alignment Penalization**: Uses configurable cost-penalties to mathematically coerce surrounding BESS units to match their parent MVS orientation, natively enforcing "clean" architectural rows when aesthetic uniformity is desired.
4. **Global Min-Cost Cable Routing**: Leverages `scipy.optimize.linear_sum_assignment` (the Hungarian algorithm) to perform a final global reassignment pass, freezing coordinates and swapping BESS-to-MVS assignments across the entire site to minimize the true Euclidean cable sum.

## The 4 Operating Modes
The engine ships with four distinct parameter profiles tailored to different design philosophies:

| Mode | Strategy & Philosophy | Cable Routing | Density |
| :--- | :--- | :--- | :--- |
| **Conservative** | Prioritizes clean, uniform geometric blocks. Strict adjacency requirements and heavy alignment penalties. | Hard 25m cap. Strict cluster binding. | Baseline Density |
| **Aggressive** | Relaxes adjacency logic allowing BESS units to straggle, but still strictly obeys the 25m physical cable run limit. | Hard 25m cap. Greedy distance matching. | High Density |
| **Ultra-Aggressive** | Lift all cable restrictions. Allows the algorithm to scale MVS infrastructure linearly to support whatever BESS footprint fits. | Unlimited runs. Global Hungarian Reassignment. | Maximum Grid Density |
| **Hyper-Pack** | Pure mathematical saturation. Executes an exhaustive, fine-grid re-scan (0.5m) to micro-shift units into any remaining space. | Unlimited runs. Global Hungarian Reassignment. | Absolute Physical Saturation |

## Quick Start / Usage

Executing a layout optimization is entirely parametric. 

```python
from engine import run_bess_optimization
from visualization import plot_individual, print_comparison

# 1. Define Site & Constraints
CONFIG = {
    "site_vertices": [(0,0), (53.3,0), (53.3,90.4), (0,90.4)],
    "setback": 0,
    "zones": {"non_buildable": [], "restricted": []},
    "equipment": {
        "BESS": {
            "width": 6.06, "height": 2.44,
            "clearance": {"front": 2.0, "back": 1.0, "left": 1.0, "right": 1.0}
        },
        "MVS": {
            "width": 6.06, "height": 2.44,
            "clearance": {"front": 3.0, "back": 1.5, "left": 1.5, "right": 1.5}
        }
    },
    "max_bess_per_mvs": 4,
    "max_cable_length": 25,
    "grid_resolution": 2.0,
}

# 2. Run the Engine
result = run_bess_optimization(CONFIG, mode="hyper_pack", verbose=True)

# 3. Visualize
plot_individual(result, CONFIG)
```

## Future Roadmap
- **Interactive Web Dashboard**: Upcoming deployment via **Streamlit** to allow users to visually drag-and-drop site boundaries, toggle constraints, and instantly visualize the four-mode output live in the browser.
- **Topographical Integrations**: Adding Z-axis awareness for multi-elevation tiering.
- **Thermal Heat-Map Profiling**: Overlaying ambient HVAC clearance scores.