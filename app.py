import streamlit as st
import pandas as pd
import ast
import hashlib

from engine import (
    run_bess_optimization, 
    _compute_metrics, 
    create_site, 
    prepare_site, 
    get_rotated_dimensions, 
    create_equipment_polygon, 
    create_clearance_polygon, 
    is_valid_placement
)
from visualization import plot_layout_plotly

st.set_page_config(page_title="BESS-Opt Engine", layout="wide")

# Initialize session state cache
if "baseline_cache" not in st.session_state:
    st.session_state["baseline_cache"] = {}

# =========================================================
# HELPERS
# =========================================================
def engine_to_df(mvs_list, bess_list):
    rows = []
    mvs_map = {id(m): f"M{i+1}" for i, m in enumerate(mvs_list)}
    for i, m in enumerate(mvs_list):
        rows.append({
            "ID": mvs_map[id(m)],
            "Type": "MVS",
            "X": float(m["footprint"].bounds[0]),
            "Y": float(m["footprint"].bounds[1]),
            "Rotated": bool(m.get("rotated", False)),
            "Assigned_MVS": None
        })
    for i, b in enumerate(bess_list):
        assigned_m = mvs_map.get(id(b["mvs"]), None) if "mvs" in b else None
        rows.append({
            "ID": f"B{i+1}",
            "Type": "BESS",
            "X": float(b["footprint"].bounds[0]),
            "Y": float(b["footprint"].bounds[1]),
            "Rotated": bool(b.get("rotated", False)),
            "Assigned_MVS": assigned_m
        })
    return pd.DataFrame(rows)

def df_to_engine(df, config, site, non_buildable):
    mvs_list = []
    bess_list = []
    placed = []
    errors = []
    
    bess_eq = config["equipment"]["BESS"]
    mvs_eq = config["equipment"]["MVS"]
    
    mvs_df = df[df["Type"] == "MVS"]
    mvs_map = {}
    for _, row in mvs_df.iterrows():
        rotated = bool(row["Rotated"])
        w, h, cl = get_rotated_dimensions(mvs_eq["width"], mvs_eq["height"], mvs_eq["clearance"], rotated)
        fp = create_equipment_polygon(row["X"], row["Y"], w, h)
        cl_poly = create_clearance_polygon(fp, cl)
        
        mvs_obj = {
            "type": "MVS",
            "footprint": fp,
            "clearance_zone": cl_poly,
            "assigned_bess": [],
            "rotated": rotated,
            "id": row["ID"]
        }
        
        valid = is_valid_placement(fp, cl_poly, site, non_buildable, placed)
        if not valid:
            errors.append(f"Collision Detected: MVS {row['ID']} overlaps with obstacles/equipment or goes out of bounds.")
            
        mvs_list.append(mvs_obj)
        placed.append(mvs_obj)
        mvs_map[row["ID"]] = mvs_obj
        
    bess_df = df[df["Type"] == "BESS"]
    for _, row in bess_df.iterrows():
        rotated = bool(row["Rotated"])
        w, h, cl = get_rotated_dimensions(bess_eq["width"], bess_eq["height"], bess_eq["clearance"], rotated)
        fp = create_equipment_polygon(row["X"], row["Y"], w, h)
        cl_poly = create_clearance_polygon(fp, cl)
        
        assigned_mvs_id = row["Assigned_MVS"]
        assigned_mvs = mvs_map.get(assigned_mvs_id, None)
        
        bess_obj = {
            "type": "BESS",
            "footprint": fp,
            "clearance_zone": cl_poly,
            "mvs": assigned_mvs,
            "rotated": rotated,
            "id": row["ID"]
        }
        
        if assigned_mvs:
            assigned_mvs["assigned_bess"].append(bess_obj)
            
        valid = is_valid_placement(fp, cl_poly, site, non_buildable, placed)
        if not valid:
            errors.append(f"Collision Detected: BESS {row['ID']} overlaps with obstacles/equipment or goes out of bounds.")
            
        bess_list.append(bess_obj)
        placed.append(bess_obj)
        
    return mvs_list, bess_list, errors

def get_hash(state_str):
    return hashlib.sha256(state_str.encode()).hexdigest()

# =========================================================
# UI DEFINITION
# =========================================================
st.title("🔋 BESS-Opt: Interactive Engineering Dashboard")

# ---------------------------------------------------------
# STEP 1: SITE BOUNDARY & ZONE DEFINITIONS
# ---------------------------------------------------------
default_site = "[(0, 0), (53.3, 0), (53.3, 16), (21.9, 16), (47.7, 90.4), (8, 90.4), (0, 90.4)]"
default_cable = "[(15.4, 0), (21.9, 0), (47.7, 90.4), (31.9, 90.4)]"
default_restrict = "[(21.9, 16), (53.3, 16), (53.3, 90.4), (47.7, 90.4)]"

with st.expander("Step 1: Site Boundary & Zone Definitions", expanded=True):
    st.markdown("Define the exact coordinate vertices (X, Y tuples) for the physical site.")
    site_input = st.text_area("Global Property Boundary", value=default_site, help="The outermost perimeter where equipment can be placed.")
    non_buildable_input = st.text_area("Non-Buildable Zones", value=default_cable, help="Areas reserved for access roads, environmental restrictions, or buffer corridors where no hardware can be placed.")
    restricted_input = st.text_area("Restricted / Out of Scope Zones", value=default_restrict, help="Additional zones completely excluded from evaluation.")

try:
    site_vertices = ast.literal_eval(site_input)
    non_buildable_vertices = [ast.literal_eval(non_buildable_input)] if non_buildable_input.strip() else []
    restricted_vertices = [ast.literal_eval(restricted_input)] if restricted_input.strip() else []
except Exception as e:
    st.error(f"Error parsing coordinates: {e}")
    st.stop()

# ---------------------------------------------------------
# STEP 2: TECHNICAL & COMMERCIAL PARAMETERS
# ---------------------------------------------------------
with st.sidebar:
    st.header("Step 2: Parameters")
    
    mode = st.selectbox("Baseline Mode", ["conservative", "aggressive", "ultra_aggressive", "hyper_pack"])
    
    st.subheader("BESS Clearances (m)")
    b_front = st.slider("BESS Front", 0.0, 5.0, 2.0, 0.25)
    b_back  = st.slider("BESS Back",  0.0, 5.0, 1.0, 0.25)
    b_left  = st.slider("BESS Left",  0.0, 5.0, 1.0, 0.25)
    b_right = st.slider("BESS Right", 0.0, 5.0, 1.0, 0.25)
    
    st.subheader("MVS Clearances (m)")
    m_front = st.slider("MVS Front", 0.0, 5.0, 3.0, 0.25)
    m_back  = st.slider("MVS Back",  0.0, 5.0, 1.5, 0.25)
    m_left  = st.slider("MVS Left",  0.0, 5.0, 1.5, 0.25)
    m_right = st.slider("MVS Right", 0.0, 5.0, 1.5, 0.25)
    
    st.subheader("Capacity")
    max_bess = st.slider("Max BESS per MVS", 1, 8, 4, 1)

    st.subheader("Commercial Equipment Scaling")
    bess_cap = st.slider("BESS Unit Capacity (MWh)", 0.0, 15.0, 5.0, 0.5)
    mvs_pow = st.slider("MVS Station Power (MW)", 0.0, 10.0, 2.5, 0.5)

CONFIG = {
    "site_vertices": site_vertices,
    "setback": 0,
    "zones": {"non_buildable": non_buildable_vertices, "restricted": restricted_vertices},
    "equipment": {
        "BESS": {
            "width": 6.06, "height": 2.44,
            "clearance": {"front": b_front, "back": b_back, "left": b_left, "right": b_right}
        },
        "MVS": {
            "width": 6.06, "height": 2.44,
            "clearance": {"front": m_front, "back": m_back, "left": m_left, "right": m_right}
        }
    },
    "max_bess_per_mvs": max_bess,
    "max_cable_length": 25,
    "grid_resolution": 2.0,
    "bess_unit_mwh": bess_cap,
    "mvs_station_mw": mvs_pow
}

# ---------------------------------------------------------
# CACHING & ENGINE EXECUTION
# ---------------------------------------------------------
state_str = f"{site_input}|{non_buildable_input}|{restricted_input}|{mode}|{b_front}|{b_back}|{b_left}|{b_right}|{m_front}|{m_back}|{m_left}|{m_right}|{max_bess}"
cache_key = get_hash(state_str)

if cache_key not in st.session_state["baseline_cache"]:
    with st.spinner(f"Executing {mode.replace('_', ' ').title()} Optimization (First Run)..."):
        baseline_res = run_bess_optimization(CONFIG, mode=mode, verbose=False)
        st.session_state["baseline_cache"][cache_key] = engine_to_df(baseline_res["mvs_list"], baseline_res["bess_list"])

# Determine if the physical parameters changed, if so we load the cache to the editor
if "last_cache_key" not in st.session_state or st.session_state["last_cache_key"] != cache_key:
    st.session_state["editor_df"] = st.session_state["baseline_cache"][cache_key].copy()
    st.session_state["last_cache_key"] = cache_key

# ---------------------------------------------------------
# STEP 3: GRAPHICAL ENGINE & INTERACTIVE SANDBOX
# ---------------------------------------------------------
st.header("Step 3: Graphical Engine & Interactive Sandbox")

col_chart, col_data = st.columns([3, 2])

# We use the editor state
editor_df = st.session_state["editor_df"]

site_raw = create_site(CONFIG["site_vertices"])
site, non_buildable = prepare_site(site_raw, CONFIG)
mvs_list, bess_list, placement_errors = df_to_engine(editor_df, CONFIG, site, non_buildable)

with col_chart:
    st.markdown("### 2D Interactive Layout")
    for err in placement_errors:
        st.error(err)
    fig = plot_layout_plotly(site, non_buildable, mvs_list, bess_list, CONFIG, title=f"BESS Layout ({mode.replace('_', ' ').title()})")
    st.plotly_chart(fig, use_container_width=True)

with col_data:
    st.markdown("### Live Performance Metrics")
    metrics = _compute_metrics(site, non_buildable, mvs_list, bess_list, CONFIG["max_bess_per_mvs"])
    total_energy = metrics["bess_count"] * bess_cap
    total_power = metrics["mvs_count"] * mvs_pow
    
    kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
    kpi_col1.metric("MVS Placed", metrics["mvs_count"])
    kpi_col2.metric("BESS Placed", metrics["bess_count"])
    kpi_col3.metric("Area Saturation", f"{metrics['area_saturation_pct']:.1f}%")
    
    kpi_col4, kpi_col5, kpi_col6 = st.columns(3)
    kpi_col4.metric("Total Cable", f"{metrics['total_cable']:.1f} m")
    kpi_col5.metric("Total Energy", f"{total_energy:.1f} MWh")
    kpi_col6.metric("Total Power", f"{total_power:.1f} MW")

    st.markdown("### Interactive Data Sandbox")
    st.markdown("Modify coordinates, rotate units, reassign networks, or add/delete rows.")
    new_edited_df = st.data_editor(editor_df, num_rows="dynamic", use_container_width=True)
    
    if not new_edited_df.equals(editor_df):
        st.session_state["editor_df"] = new_edited_df
        st.rerun()

    export_df = new_edited_df.copy()
    export_df.loc[export_df["Type"] == "BESS", "Capacity (MWh)"] = bess_cap
    export_df.loc[export_df["Type"] == "MVS", "Capacity (MWh)"] = 0.0
    export_df.loc[export_df["Type"] == "MVS", "Power (MW)"] = mvs_pow
    export_df.loc[export_df["Type"] == "BESS", "Power (MW)"] = 0.0

    csv_str = export_df.to_csv(index=False)
    summary_lines = f"\n\nTotal Plant Power: {total_power:.1f} MW\nTotal Plant Energy: {total_energy:.1f} MWh\n"
    csv_str += summary_lines

    st.download_button(
        label="📥 Download Engineering Report (CSV)",
        data=csv_str.encode('utf-8'),
        file_name='bess_boq_layout.csv',
        mime='text/csv',
        use_container_width=True
    )
