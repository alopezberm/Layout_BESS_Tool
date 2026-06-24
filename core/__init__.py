"""BESS-Opt core: headless layout, sizing, metrics and (de)serialization.

This package has no dependency on Streamlit, matplotlib or Plotly — it is the
single shared engine used identically by the notebook (`notebook/`) and the
Streamlit app (`app/`). Rendering lives in `viz/`.
"""

from .config import DEFAULT_EQUIPMENT, build_config
from .geometry import (
    create_site,
    prepare_site,
    create_candidate_grid,
    create_equipment_polygon,
    create_clearance_polygon,
    is_valid_placement,
    get_rotated_dimensions,
    get_oriented_dimensions,
    ORIENTATIONS,
)
from .metrics import total_cable_length, compute_metrics, compute_hub_metrics
from .sizing import size_system, classify_duration
from .optimize import run_colocated_optimization
from .packing import run_row_packing
from .serialization import (
    engine_to_df,
    engine_to_df_with_hubs,
    df_to_engine,
    layout_to_csv,
)

__all__ = [
    "DEFAULT_EQUIPMENT",
    "build_config",
    "create_site",
    "prepare_site",
    "create_candidate_grid",
    "create_equipment_polygon",
    "create_clearance_polygon",
    "is_valid_placement",
    "get_rotated_dimensions",
    "get_oriented_dimensions",
    "ORIENTATIONS",
    "total_cable_length",
    "compute_metrics",
    "compute_hub_metrics",
    "size_system",
    "classify_duration",
    "run_colocated_optimization",
    "run_row_packing",
    "engine_to_df",
    "engine_to_df_with_hubs",
    "df_to_engine",
    "layout_to_csv",
]