# BESS Layout Optimization Tool

> Automated spatial layout engine for Battery Energy Storage System sites — maximizes battery count on any polygon site while respecting clearances, exclusion zones, and MVS capacity constraints.

![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)
![Jupyter](https://img.shields.io/badge/Jupyter-Notebook-F37626?logo=jupyter&logoColor=white)
![Shapely](https://img.shields.io/badge/Shapely-2.x-4CAF50?logo=python&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-2.x-013243?logo=numpy&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

<!-- Replace the line below with your actual screenshot once you export one:
     plt.savefig("docs/layout.png", dpi=150, bbox_inches="tight")
-->
> **Screenshot:** run the notebook and export your layout to add an image here.

---

## What it does

Given a site boundary and a set of constraints, the optimizer automatically determines how to fit the maximum number of BESS containers into the buildable area, grouped into clusters around MVS/PCS inverter units.

It is intended for **early-stage feasibility studies and proposal layouts** — fast iteration over different site configurations matters more than a provably optimal solution at this stage.

```
Input                              Output
─────                              ──────
Site polygon vertices        →     Cluster count
Non-buildable zones               BESS count
Restricted zones                  Saturation rate
Equipment dimensions              Total cable length
Clearance requirements            2D layout plot
MVS capacity
```

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Adapting to a New Site](#adapting-to-a-new-site)
- [Configuration Reference](#configuration-reference)
- [How the Algorithm Works](#how-the-algorithm-works)
- [Output](#output)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [License](#license)

---

## Features

| | |
|---|---|
| Arbitrary site polygon | Any convex or non-convex boundary defined by (x, y) vertex lists |
| Two zone types | *Non-buildable* (cable corridors, fire lanes) block placement but stay inside the site; *restricted* zones are subtracted from the usable area entirely |
| Asymmetric clearances | Independent front / back / left / right distances per equipment type; clearance zones may overlap each other but never equipment footprints |
| Interleaved placement | One MVS placed, then its full BESS cluster filled, before the next MVS — prevents inverters from depleting battery space |
| Compact cluster scoring | Each BESS slot is scored by `dist_to_MVS + 0.5 × dist_to_nearest_cluster_member`, pulling successive units into tight rows |
| Cable crossing control | Single boolean toggle: greedy fill (max BESS) vs. Voronoi reassignment (no inter-cluster crossings) |
| Site setback | Optional inward buffer on the site boundary |
| Colour-coded plot | One colour per cluster; footprints, clearance zones, cable runs, and a metric summary in the title |

---

## Installation

**Requirements:** Python 3.9+, Jupyter Notebook or JupyterLab.

```bash
# 1. Clone
git clone https://github.com/<your-username>/Layout_BESS_Tool.git
cd Layout_BESS_Tool

# 2. Install dependencies
pip install shapely matplotlib numpy

# 3. Launch
jupyter notebook Layout.ipynb
```

The notebook's first cell also runs `pip install` automatically if you prefer not to install manually.

---

## Quick Start

1. Open `Layout.ipynb`.
2. Edit the **ZONE DEFINITIONS** cell — define your site polygon, non-buildable areas, and restricted areas as vertex lists.
3. Edit the **KEY INPUTS** cell — set container dimensions, clearances, and system constraints.
4. Run all cells (`Kernel → Restart & Run All`).

The last cell prints a results table and renders the layout plot.

---

## Adapting to a New Site

### Step 1 — Trace the site boundary

Define the usable perimeter as an ordered list of (x, y) vertices in metres (counter-clockwise or clockwise, Shapely accepts both):

```python
"site_vertices": [
    (0,    0),
    (80,   0),
    (80,  120),
    (0,  120),
]
```

### Step 2 — Define exclusion zones

```python
# Non-buildable: stays inside the site polygon, blocks placement
#   (cable trenches, fire lanes, access roads)
cable_corridor = [
    (20,  0),
    (30,  0),
    (30, 120),
    (20, 120),
]

# Restricted: completely removed from the site
#   (third-party land, environmental exclusions)
out_of_scope = [
    (60, 80),
    (80, 80),
    (80, 120),
    (60, 120),
]
```

Then wire them into `CONFIG`:

```python
"zones": {
    "non_buildable": [cable_corridor],   # list of polygons
    "restricted":    [out_of_scope],
}
```

### Step 3 — Set equipment specs and run

Adjust `BESS_WIDTH`, `BESS_HEIGHT`, clearances, and `MAX_BESS_PER_MVS` in **KEY INPUTS**, then re-run all cells.

---

## Configuration Reference

All parameters live in the **KEY INPUTS** cell. No other cell needs editing for a standard run.

### Equipment dimensions

| Parameter | Default | Notes |
|:---|:---:|:---|
| `BESS_WIDTH` | `6.06 m` | Container width along the X axis |
| `BESS_HEIGHT` | `2.44 m` | Container depth along the Y axis |
| `MVS_WIDTH` | `6.06 m` | |
| `MVS_HEIGHT` | `2.44 m` | |

> Defaults match the **Sungrow PowerTitan 20-ft** form factor.

### Clearance model

Each side of each equipment type carries an independent clearance distance. Clearance zones represent O&M access, fire separation, and ventilation — they **may overlap each other** but a footprint must never enter another unit's clearance zone.

```
           ┌──────────────────────────────┐
           │         front (+Y)           │
           │   ┌──────────────────────┐   │
  left     │   │                      │   │  right
  (−X)     │   │      FOOTPRINT       │   │  (+X)
           │   │    6.06 m × 2.44 m   │   │
           │   └──────────────────────┘   │
           │          back (−Y)           │
           └──────────────────────────────┘
```

```python
BESS_CLEARANCE = {"front": 2.0, "back": 1.0, "left": 1.0, "right": 1.0}
MVS_CLEARANCE  = {"front": 3.0, "back": 1.5, "left": 1.5, "right": 1.5}
```

### System constraints

| Parameter | Default | Description |
|:---|:---:|:---|
| `MAX_BESS_PER_MVS` | `4` | Hard cap on batteries per inverter cluster |
| `MIN_MVS_SPACING` | `0 m` | Extra minimum centre-to-centre gap between MVS units. `0` = rely on clearance geometry only (recommended) |
| `GRID_RESOLUTION` | `2.0 m` | Candidate placement step. Finer = more positions found, slower runtime |
| `SETBACK` | `0 m` | Inward buffer shrunk from the site boundary before placement |

### Cable crossing strategy

```python
AVOID_CABLE_CROSSINGS = False
```

| Value | Behaviour | Trade-off |
|:---|:---|:---|
| `False` | Greedy per-cluster: each MVS claims its closest BESS before the next MVS is placed | Maximum battery count; inter-cluster cables may cross |
| `True` | Same greedy placement, then a Voronoi swap: each BESS is reassigned to its nearest MVS without moving physically | No inter-cluster crossings; negligible reduction in battery count |

---

## How the Algorithm Works

### Site preparation

1. Build the site polygon from vertices.
2. Apply optional setback (`site.buffer(-SETBACK)`).
3. Subtract restricted zones (`site.difference(zone)`).
4. Generate a uniform candidate grid across the bounding box at `GRID_RESOLUTION` step.

### Interleaved cluster placement

A global pool `avail` tracks all grid positions not yet blocked by placed equipment. The main loop runs until `avail` is too sparse to score another cluster:

```
REPEAT:
  ┌─ MVS SELECTION ──────────────────────────────────────────────┐
  │  For every position in avail:                                │
  │    - Build MVS footprint + clearance at that position        │
  │    - Check placement validity                                │
  │    - Score = sum of distances to the k nearest avail points  │
  │      (proxy for expected intra-cluster cable length)         │
  │  → Commit the lowest-scoring valid position as next MVS      │
  │  → Prune avail for the MVS clearance zone                    │
  └──────────────────────────────────────────────────────────────┘
  ┌─ BESS FILLING ───────────────────────────────────────────────┐
  │  REPEAT up to MAX_BESS_PER_MVS times:                       │
  │    For every position remaining in avail:                    │
  │      score = dist_to_MVS + 0.5 × dist_to_nearest_cluster    │
  │    → Commit the lowest-scoring valid position as next BESS   │
  │    → Prune avail for this BESS's clearance zone              │
  │  The compactness term (0.5 × d_cluster) pulls each new       │
  │  BESS toward the growing cluster, forming tight rows         │
  └──────────────────────────────────────────────────────────────┘
  If MVS received zero BESS → discard it
UNTIL avail too sparse
```

Because `avail` shrinks after every placement (MVS and BESS alike), the loop self-terminates before battery space is exhausted. A two-phase approach — all MVS first, then all BESS — fails on dense sites because Phase 1 depletes `avail` before Phase 2 can run.

### Voronoi swap (optional)

When `AVOID_CABLE_CROSSINGS = True`, a post-processing pass runs after the greedy loop:

1. Clear all BESS-to-MVS assignments.
2. Sort every BESS by its distance to the nearest MVS.
3. Assign each BESS to the nearest MVS that still has capacity.

Physical positions are untouched. Only the electrical grouping changes, which eliminates cable crossings between clusters.

### Placement validity

A candidate passes if:

1. Its footprint is **fully contained** within the usable site polygon.
2. Its footprint does **not intersect** any non-buildable zone.
3. Its footprint does **not enter** the clearance zone of any already-placed unit.
4. Its clearance zone does **not overlap** any already-placed footprint.

---

## Output

### Console summary

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

### Layout plot legend

| Element | Meaning |
|:---|:---|
| Black outline | Usable site boundary (after setback and restricted zones removed) |
| Gold fill | Non-buildable zones |
| Solid colour fill (light) | Cluster colour — BESS footprints |
| Solid colour fill (dark) | Cluster colour — MVS footprint |
| Dashed outline | Clearance zone boundary |
| Thin lines | Cable runs (BESS centroid → MVS centroid) |
| `M1`, `M2`, … | MVS unit labels |

### Saving the plot

Add this line before `plt.show()` in the last cell to export the image:

```python
plt.savefig("docs/layout.png", dpi=150, bbox_inches="tight")
```

---

## Limitations

This tool is designed for speed and simplicity at the pre-feasibility stage. It does not cover:

- **Container rotation** — all units are placed at 0° (width along X, depth along Y). No 90° rotation option.
- **Optimality guarantees** — the greedy algorithm finds a good solution but not necessarily the global maximum.
- **Non-rectangular footprints** — L-shaped or irregular equipment cannot be modelled.
- **Mixed equipment types** — only one BESS type and one MVS type per run; no mixed-capacity clusters.
- **Vertical stacking** — 2D layout only; no multi-storey or raised-platform arrangements.
- **Cost optimisation** — the objective is purely to maximise BESS count; cable cost, land cost, and civil works are not considered.
- **Large sites with fine grids** — runtime scales roughly as O(|avail|²) per MVS selection; sites larger than ~200 m × 200 m with `GRID_RESOLUTION < 2.0 m` may be slow.

---

## Roadmap

- [ ] 90° container rotation option
- [ ] Configurable minimum BESS count per cluster (discard under-filled MVS)
- [ ] Export layout to DXF / SVG for CAD import
- [ ] Multi-scenario batch mode (sweep over `MAX_BESS_PER_MVS` or clearance values)
- [ ] Interactive site polygon tracing (click-to-define vertices)

---

## License

MIT License — see [LICENSE](LICENSE) for details.