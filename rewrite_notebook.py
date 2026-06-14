import json

nb = {
 "cells": [
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "# BESS Layout Optimization Engine\n",
    "\n",
    "This notebook runs the 4-mode layout optimization engine for Battery Energy Storage Systems (BESS).\n",
    "All heavy lifting, routing, placement loops, and visualization logic have been extracted to `engine.py` and `visualization.py`."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": None,
   "metadata": {},
   "outputs": [],
   "source": [
    "from engine import run_bess_optimization\n",
    "from visualization import print_comparison, plot_comparison, plot_all_standalone\n",
    "\n",
    "# =========================================================\n",
    "# CONFIGURATION\n",
    "# =========================================================\n",
    "cable_corridor = [\n",
    "    (15.4,  0),\n",
    "    (21.9,  0),\n",
    "    (47.7,  90.4),\n",
    "    (31.9,  90.4)\n",
    "]\n",
    "\n",
    "out_of_scope = [\n",
    "    (21.9,  16),\n",
    "    (53.3,  16),\n",
    "    (53.3,  90.4),\n",
    "    (47.7,  90.4)\n",
    "]\n",
    "\n",
    "CONFIG = {\n",
    "    \"site_vertices\": [\n",
    "        (0,     0),\n",
    "        (53.3,  0),\n",
    "        (53.3,  16),\n",
    "        (21.9,  16),\n",
    "        (47.7,  90.4),\n",
    "        (8,     90.4),\n",
    "        (0,     90.4),\n",
    "    ],\n",
    "    \"setback\": 0,\n",
    "    \"zones\": {\n",
    "        \"non_buildable\": [cable_corridor],\n",
    "        \"restricted\":    [out_of_scope]\n",
    "    },\n",
    "    \"equipment\": {\n",
    "        \"BESS\": {\n",
    "            \"width\":     6.06,\n",
    "            \"height\":    2.44,\n",
    "            \"clearance\": {\"front\": 2.0, \"back\": 1.0, \"left\": 1.0, \"right\": 1.0}\n",
    "        },\n",
    "        \"MVS\": {\n",
    "            \"width\":     6.06,\n",
    "            \"height\":    2.44,\n",
    "            \"clearance\": {\"front\": 3.0, \"back\": 1.5, \"left\": 1.5, \"right\": 1.5}\n",
    "        }\n",
    "    },\n",
    "    \"max_bess_per_mvs\":   4,\n",
    "    \"max_cable_length\":   25,\n",
    "    \"mvs_scoring_radius\": 25,\n",
    "    \"min_mvs_spacing\":    0,\n",
    "    \"grid_resolution\":    2.0,\n",
    "}"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": None,
   "metadata": {},
   "outputs": [],
   "source": [
    "import time\n",
    "\n",
    "scenarios = []\n",
    "for mode in (\"conservative\", \"aggressive\", \"ultra_aggressive\", \"hyper_pack\"):\n",
    "    t0 = time.perf_counter()\n",
    "    res = run_bess_optimization(CONFIG, mode=mode, verbose=False)\n",
    "    elapsed = time.perf_counter() - t0\n",
    "    print(f\"{mode:<20s} -> {res['metrics']['bess_count']:3d} BESS in {elapsed:5.1f} s\")\n",
    "    scenarios.append(res)\n",
    "\n",
    "# Print metrics table\n",
    "print_comparison(*scenarios)"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": None,
   "metadata": {},
   "outputs": [],
   "source": [
    "# 2x2 panel overview\n",
    "plot_comparison(*scenarios, config=CONFIG)"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": None,
   "metadata": {},
   "outputs": [],
   "source": [
    "# Full-size standalone figures\n",
    "plot_all_standalone(*scenarios, config=CONFIG)"
   ]
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "Python 3",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "codemirror_mode": {
    "name": "ipython",
    "version": 3
   },
   "file_extension": ".py",
   "mimetype": "text/x-python",
   "name": "python",
   "nbconvert_exporter": "python",
   "pygments_lexer": "ipython3",
   "version": "3.12.0"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 4
}

with open("c:/Users/Alejandro/GitHub/Layout_BESS_Tool/Layout.ipynb", "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1)
