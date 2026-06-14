# BESS Layout Optimization Tool

A Python-based spatial optimization engine for Battery Energy Storage System (BESS) site layouts. Given an arbitrary site polygon, it automatically maximizes the number of battery containers placed while respecting equipment footprints, asymmetric clearance zones, non-buildable areas, and MVS/PCS capacity constraints.

![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python)
![Jupyter](https://img.shields.io/badge/Jupyter-Notebook-orange?logo=jupyter)
![Shapely](https://img.shields.io/badge/Shapely-2.x-green)
![NumPy](https://img.shields.io/badge/NumPy-2.x-013243?logo=numpy)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
- [Algorithm](#algorithm)
- [Output](#output)
- [Project Structure](#project-structure)

---

## Overview

Sizing and arranging BESS containers on a constrained site is a time-consuming task during the development phase of an energy storage project. This tool automates the spatial layout step: you define the site boundary, exclusion zones, equipment specs, and clustering rules — and the optimizer fills the site with as many battery containers as possible.

It is designed to support **early-stage feasibility studies and proposal layouts**, where quick iteration over different site configurations is more valuable than an exact optimal solution.

---

## Features

- **Arbitrary site polygon** — define any convex or non-convex boundary using a list of (x, y) vertices
- **Two zone types** — *non-buildable* zones (cable corridors, fire lanes) block placement but remain inside the site; *restricted* zones (out-of-scope areas) are fully subtracted from the usable area
- **Asymmetric clearance per equipment side** — front / back / left / right clearances modelled independently; clearance zones may overlap each other but not equipment footprints
- **Interleaved greedy placement** — places one MVS and immediately fills its BESS cluster before moving to the next, preventing the optimizer from depleting battery space with inverter units
- **Compact cluster scoring** — each successive BESS in a cluster is scored by distance to MVS plus a compactness term, naturally forming row-like clusters rather than scattered arrangements
- **Cable crossing toggle** — `AVOID_CABLE_CROSSINGS = False` maximises battery count; `True` performs a Voronoi reassignment pass that eliminates inter-cluster cable crossings at no physical rearrangement cost
- **Site setback** — optional inward buffer on the site boundary
- **Colour-coded visualization** — each cluster rendered in a distinct colour with footprints, clearance zones, cable runs, and a summary title

---

## Installation

### Prerequisites

- Python 3.9 or later
- Jupyter Notebook or JupyterLab

### Install dependencies

```bash
pip install shapely matplotlib numpy
```

Or run the first cell of the notebook, which installs everything automatically.

### Clone the repository

```bash
git clone https://github.com/<your-username>/Layout_BESS_Tool.git
cd Layout_BESS_Tool
jupyter notebook Layout.ipynb
```

---

## Quick Start

1. Open `Layout.ipynb` in Jupyter.
2. Edit the **KEY INPUTS** cell with your site geometry and equipment specs.
3. Run all cells (`Kernel → Restart & Run All`).
4. The final cell produces a results summary and a 2D layout plot.

---

## Configuration Reference

All user-facing parameters are consolidated in the **KEY INPUTS** cell. No other cell needs to be modified for a typical use case.

### Equipment dimensions

| Parameter | Default | Description |
|---|---|---|
| `BESS_WIDTH` | `6.06 m` | Container width (X axis) |
| `BESS_HEIGHT` | `2.44 m` | Container depth (Y axis) |
| `MVS_WIDTH` | `6.06 m` | MVS/PCS width |
| `MVS_HEIGHT` | `2.44 m` | MVS/PCS depth |

> Default values match the **Sungrow PowerTitan 20-ft** container form factor.

### Clearance distances

Clearances are defined per side for each equipment type. Clearance zones **can overlap** each other — they represent O&M access, fire safety, and ventilation requirements, not hard structural exclusions. Equipment footprints, however, must never enter another unit's clearance zone.

| Side | Direction |
|---|---|
| `front` | +Y — faces the access aisle |
| `back` | −Y — rear of the container |
| `left` | −X |
| `right` | +X |

```python
BESS_CLEARANCE = {"front": 2.0, "back": 1.0, "left": 1.0, "right": 1.0}
MVS_CLEARANCE  = {"front": 3.0, "back": 1.5, "left": 1.5, "right": 1.5}
```

### System constraints

| Parameter | Default | Description |
|---|---|---|
| `MAX_BESS_PER_MVS` | `4` | Hard cap on battery units per inverter cluster |
| `MIN_MVS_SPACING` | `0` | Optional minimum centre-to-centre distance between MVS units (m). Set to `0` to rely only on clearance geometry (recommended) |
| `GRID_RESOLUTION` | `2.0 m` | Candidate placement grid step. Finer grids find more positions but increase runtime |
| `SETBACK` | `0 m` | Inward buffer applied to the site boundary before placement |

### Cable crossing strategy

```python
AVOID_CABLE_CROSSINGS = False   # True → Voronoi reassignment pass
```

| Value | Behaviour |
|---|---|
| `False` | Greedy per-cluster fill. Each MVS takes the closest available BESS before the next MVS is placed. Maximises total battery count. Inter-cluster cables may cross. |
| `True` | After greedy placement, all BESS are globally reassigned to their geometrically nearest MVS (Voronoi swap). Physical positions are unchanged; only the electrical assignment changes. Eliminates cable crossings with minimal impact on battery count. |

---

## Algorithm

The placement engine runs in a single interleaved loop:

```
while avail has enough positions:
    1. Score all valid MVS candidates
       → pick the one whose k nearest available positions are closest
         (minimises expected intra-cluster cable length)
    2. Place that MVS; prune avail for its clearance zone
    3. Fill up to MAX_BESS_PER_MVS BESS for this MVS
       → at each slot, scan avail and score each candidate:
           score = dist_to_MVS + 0.5 × dist_to_nearest_cluster_member
       → this compactness term pulls successive BESS toward the growing
         cluster, creating tight row-like arrangements
    4. Prune avail for each placed BESS
    5. If MVS got zero BESS, discard it and continue
```

Because `avail` shrinks with every piece of equipment placed — both MVS and BESS — the loop naturally terminates before battery space is exhausted. This avoids the failure mode of a two-phase approach (all MVS first, then all BESS) where Phase 1 can deplete all available positions before any batteries are placed.

If `AVOID_CABLE_CROSSINGS = True`, a post-processing Voronoi swap pass runs after the greedy loop: all BESS keep their physical positions but are globally reassigned to the nearest MVS with remaining capacity.

### Placement validity rules

A candidate position is valid if and only if:

1. The equipment **footprint** is fully contained within the usable site polygon
2. The footprint does not intersect any non-buildable zone
3. The footprint does not enter the clearance zone of any already-placed unit
4. The new unit's clearance zone does not overlap any already-placed footprint

---

## Output

After running, the notebook prints a results summary:

```
===== RESULTS =====
MVS units placed   : 8
BESS units placed  : 30
Max BESS per MVS   : 4
Avg BESS per MVS   : 3.75
Fully saturated MVS: 6 / 8
Saturation rate    : 93.8%

Total cable length : 412.3 m
Avg cable per BESS : 13.7 m
```

And renders a 2D layout plot:

- **Black outline** — usable site boundary
- **Gold fill** — non-buildable zones
- **Colour per cluster** — each MVS and its assigned BESS share a colour
- **Dashed outlines** — clearance zones (lighter shade)
- **Lines** — cable runs from each BESS centroid to its MVS
- **Title** — cluster count, total BESS, total cable length, and average cable per BESS

> To add the layout image to this README, export the plot with `plt.savefig("layout.png", dpi=150)` and replace this note with `![Layout](layout.png)`.

---

## Project Structure

```
Layout_BESS_Tool/
└── Layout.ipynb        # Single-notebook tool — all logic, config, and visualisation
```

The notebook is intentionally self-contained. All geometry logic, the placement engine, and the visualisation live in a single file so it can be shared and run without any package installation beyond three standard scientific Python libraries.

---

## Dependencies

| Library | Purpose |
|---|---|
| [Shapely](https://shapely.readthedocs.io/) | 2D polygon geometry — containment, intersection, buffering |
| [NumPy](https://numpy.org/) | Vectorised distance calculations and grid generation |
| [Matplotlib](https://matplotlib.org/) | Layout visualisation |