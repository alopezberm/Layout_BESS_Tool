"""Streamlit UI — a thin interface layer over the shared `core` engine.

All layout maths, sizing and (de)serialization live in `core`; all rendering in
`viz`. This file only collects widget values, calls `build_config(...)`, runs the
engine and draws the results. No optimization logic lives here.
"""

import os
import sys
import ast
import hashlib

# Allow `streamlit run app/app.py` from the repo root to import the packages.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from core import (
    build_config,
    run_colocated_optimization,
    run_row_packing,
    compute_metrics,
    create_site,
    prepare_site,
    size_system,
    engine_to_df,
    engine_to_df_with_hubs,
    df_to_engine,
    layout_to_csv,
)
from viz import plot_layout_plotly, plot_layout_plotly_hubs

st.set_page_config(page_title="BESS-Opt Engine", layout="wide")

st.markdown("""
<style>
.block-container {
    padding-top: 2rem !important;
    padding-bottom: 1rem !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.5rem !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.9rem !important;
}
</style>
""", unsafe_allow_html=True)

# Initialize session state cache
for key, default in [
    ("phase", 1),
    ("benchmark_results", {}),
    ("selected_id", None),
    ("active_mode", None),
    ("colocated_results", None),
    ("colocated_config", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def get_hash(state_str):
    return hashlib.sha256(state_str.encode()).hexdigest()


def parse_zone(text):
    """Parse a zone text area into a list of polygons. Accepts a single polygon
    or a list of polygons; blank -> []."""
    text = text.strip()
    if not text:
        return []
    return [ast.literal_eval(text)]


# Shared default site geometry (also used to pre-fill the co-located tab)
DEFAULT_SITE_VERTICES  = "[(0, 0), (53.3, 0), (53.3, 16), (21.9, 16), (47.7, 90.4), (8, 90.4), (0, 90.4)]"
DEFAULT_NONBUILD_ZONE  = "[(15.4, 0), (21.9, 0), (47.7, 90.4), (31.9, 90.4)]"
DEFAULT_RESTRICT_ZONE  = "[(21.9, 16), (53.3, 16), (53.3, 90.4), (47.7, 90.4)]"

# =========================================================
# UI DEFINITION
# =========================================================
st.title("🔋 BESS-Opt: Spatial Optimization Engine")

# ---------------------------------------------------------
# STEP 2: TECHNICAL & COMMERCIAL PARAMETERS (Sidebar — shared by both tabs)
# ---------------------------------------------------------
with st.sidebar:
    st.header("Step 2: Parameters")

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
    mvs_pow = st.slider("MVS Station Power (MW)", 0.0, 15.0, 2.5, 0.5)

BESS_CLEARANCE = {"front": b_front, "back": b_back, "left": b_left, "right": b_right}
MVS_CLEARANCE  = {"front": m_front, "back": m_back, "left": m_left, "right": m_right}


def make_config(site_vertices, non_buildable, restricted, colocation=None):
    """Single config builder for both tabs — delegates to core.build_config so
    the app can never diverge from the notebook."""
    return build_config(
        site_vertices,
        non_buildable=non_buildable,
        restricted=restricted,
        bess_clearance=BESS_CLEARANCE,
        mvs_clearance=MVS_CLEARANCE,
        max_bess_per_mvs=max_bess,
        bess_unit_mwh=bess_cap,
        mvs_station_mw=mvs_pow,
        colocation=colocation,
    )


# ---------------------------------------------------------
# TOP-LEVEL WORKFLOW TABS
# ---------------------------------------------------------
tab_standard, tab_colocated = st.tabs([
    "🏭 Standard Automated Design",
    "🔗 Industrial Co-Located Design",
])

with tab_standard:
    # ---------------------------------------------------------
    # PHASE 1: INPUT VIEW
    # ---------------------------------------------------------
    if st.session_state["phase"] == 1:
        with st.expander("Step 1: Site Boundary & Zone Definitions", expanded=True):
            st.markdown("Define the exact coordinate vertices (X, Y tuples) for the physical site.")
            site_input = st.text_area("Global Property Boundary", value=DEFAULT_SITE_VERTICES, help="The outermost perimeter where equipment can be placed.")
            non_buildable_input = st.text_area("Non-Buildable Zones", value=DEFAULT_NONBUILD_ZONE, help="Areas reserved for access roads, environmental restrictions, or buffer corridors where no hardware can be placed.")
            restricted_input = st.text_area("Restricted / Out of Scope Zones", value=DEFAULT_RESTRICT_ZONE, help="Additional zones completely excluded from evaluation.")

        st.session_state["site_input"] = site_input
        st.session_state["non_buildable_input"] = non_buildable_input
        st.session_state["restricted_input"] = restricted_input

        st.markdown("---")
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            if st.button("🚀 Run Row-Pack Optimization Engine", type="primary", use_container_width=True):
                st.session_state["phase"] = 2
                st.rerun()

    # ---------------------------------------------------------
    # PARSE CONFIG (Needed for Phase 2 and 3)
    # ---------------------------------------------------------
    if st.session_state["phase"] >= 2:
        try:
            site_vertices = ast.literal_eval(st.session_state["site_input"])
            non_buildable_vertices = parse_zone(st.session_state["non_buildable_input"])
            restricted_vertices = parse_zone(st.session_state["restricted_input"])
            CONFIG = make_config(site_vertices, non_buildable_vertices, restricted_vertices)
        except Exception as e:
            st.error(f"Error parsing coordinates: {e}")
            if st.button("⬅️ Back"):
                st.session_state["phase"] = 1
                st.rerun()
            st.stop()

    # ---------------------------------------------------------
    # PHASE 2: BENCHMARKING GRID
    # ---------------------------------------------------------
    if st.session_state["phase"] == 2:
        st.markdown("## 📊 Row-Pack Layout Result")
        if st.button("⬅️ Edit Site Parameters"):
            st.session_state["phase"] = 1
            st.rerun()

        state_str = f"{st.session_state['site_input']}|{st.session_state['non_buildable_input']}|{st.session_state['restricted_input']}|{b_front}|{b_back}|{b_left}|{b_right}|{m_front}|{m_back}|{m_left}|{m_right}|{max_bess}"
        cache_key = get_hash(state_str)
        modes = ["row_pack"]

        if cache_key not in st.session_state["benchmark_results"]:
            st.session_state["benchmark_results"][cache_key] = {}

            progress_bar = st.progress(0)
            status_text = st.empty()

            try:
                for i, mode in enumerate(modes):
                    status_text.text("Calculating Row Pack layout (back-to-back)...")
                    res = run_row_packing(CONFIG, verbose=False)
                    st.session_state["benchmark_results"][cache_key][mode] = {
                        "df": engine_to_df(res["mvs_list"], res["bess_list"]),
                        "metrics": res["metrics"],
                    }
                    progress_bar.progress((i + 1) / len(modes))
            except Exception as e:
                del st.session_state["benchmark_results"][cache_key]
                status_text.empty()
                progress_bar.empty()
                st.error(f"Optimization failed (check the site geometry): {e}")
                st.stop()

            status_text.empty()
            progress_bar.empty()

        results = st.session_state["benchmark_results"][cache_key]
        cols = st.columns(len(modes))

        for i, mode in enumerate(modes):
            with cols[i]:
                st.markdown(f"### {mode.replace('_', ' ').title()}")
                metrics = results[mode]["metrics"]
                sizing = size_system(metrics["bess_count"], metrics["mvs_count"], bess_cap, mvs_pow)

                st.metric("Total BESS", metrics["bess_count"])
                st.metric("Total MVS", metrics["mvs_count"])
                st.metric("Plant Energy (MWh)", f"{sizing['total_mwh']:.1f}")
                st.metric("Plant Power (MW)", f"{sizing['total_mw']:.1f}")
                st.metric("Duration", sizing["duration_label"])
                st.metric("Total Cable (m)", f"{metrics['total_cable']:.1f}")
                st.metric("Area Saturation", f"{metrics['area_saturation_pct']:.1f}%")

                if metrics.get("dropped_bess"):
                    st.warning(f"{metrics['dropped_bess']} BESS unassigned (cable cap).")

                if st.button(f"🔎 Select {mode.replace('_', ' ').title()}", key=f"select_{mode}", use_container_width=True):
                    st.session_state["active_mode"] = mode
                    st.session_state["editor_df"] = results[mode]["df"].copy()
                    st.session_state["phase"] = 3
                    st.session_state["selected_id"] = None
                    st.rerun()

    # ---------------------------------------------------------
    # PHASE 3: DEEP-DIVE & QUICK ADJUSTMENTS
    # ---------------------------------------------------------
    if st.session_state["phase"] == 3:
        mode_title = st.session_state["active_mode"].replace('_', ' ').title()

        col_back, col_title = st.columns([1, 10])
        with col_back:
            if st.button("⬅️ Back to Benchmarks"):
                st.session_state["phase"] = 2
                st.rerun()
        with col_title:
            st.markdown(f"## Phase 3: Deep-Dive & Quick Adjustments - **{mode_title}**")

        col_chart, col_data = st.columns([3, 2])

        editor_df = st.session_state["editor_df"]
        site_raw = create_site(CONFIG["site_vertices"])
        site, non_buildable = prepare_site(site_raw, CONFIG)
        mvs_list, bess_list, placement_errors = df_to_engine(editor_df, CONFIG, site, non_buildable)

        with col_chart:
            for err in placement_errors:
                st.error(err)
            fig = plot_layout_plotly(site, non_buildable, mvs_list, bess_list, CONFIG, title=f"Interactive Layout: {mode_title}")
            fig.update_layout(dragmode='pan')

            plotly_config = {
                'scrollZoom': True,
                'toImageButtonOptions': {'format': 'png', 'filename': f'bess_layout_{mode_title}', 'height': 1080, 'width': 1920}
            }

            event = st.plotly_chart(fig, use_container_width=True, on_select="rerun", selection_mode="points", config=plotly_config)

            if event and event.get("selection") and event["selection"].get("points"):
                selected_customdata = event["selection"]["points"][0].get("customdata", None)
                if selected_customdata and selected_customdata != st.session_state["selected_id"]:
                    st.session_state["selected_id"] = selected_customdata
                    st.rerun()

        with col_data:
            metrics = compute_metrics(site, non_buildable, mvs_list, bess_list, CONFIG["max_bess_per_mvs"])
            sizing = size_system(metrics["bess_count"], metrics["mvs_count"], bess_cap, mvs_pow)

            kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
            kpi_col1.metric("MVS Placed", metrics["mvs_count"])
            kpi_col2.metric("BESS Placed", metrics["bess_count"])
            kpi_col3.metric("Area Saturation", f"{metrics['area_saturation_pct']:.1f}%")

            kpi_col4, kpi_col5, kpi_col6 = st.columns(3)
            kpi_col4.metric("Total Cable", f"{metrics['total_cable']:.1f} m")
            kpi_col5.metric("Total Energy", f"{sizing['total_mwh']:.1f} MWh")
            kpi_col6.metric("Total Power", f"{sizing['total_mw']:.1f} MW")

            st.caption(f"Storage duration: **{sizing['duration_label']}** ({sizing['duration_h']:.1f} h)")

            st.markdown("---")
            st.markdown("### Component Control Panel")

            available_ids = editor_df["ID"].tolist()
            current_idx = 0
            if st.session_state["selected_id"] in available_ids:
                current_idx = available_ids.index(st.session_state["selected_id"]) + 1

            selected_comp = st.selectbox("Select Component to Edit (or click on Chart)", options=[None] + available_ids, index=current_idx)

            if selected_comp != st.session_state["selected_id"]:
                st.session_state["selected_id"] = selected_comp
                st.rerun()

            if st.session_state["selected_id"] and st.session_state["selected_id"] in available_ids:
                comp_id = st.session_state["selected_id"]
                idx = editor_df.index[editor_df['ID'] == comp_id][0]
                row = editor_df.loc[idx]

                st.info(f"**Currently Editing: {row['Type']} {comp_id}**")

                edit_col1, edit_col2 = st.columns(2)
                new_x = edit_col1.number_input("X Coordinate (m)", value=float(row['X']), step=0.1)
                new_y = edit_col2.number_input("Y Coordinate (m)", value=float(row['Y']), step=0.1)

                cur_angle = int(row["Angle"]) if "Angle" in editor_df.columns and pd.notna(row.get("Angle")) else (90 if bool(row.get("Rotated", False)) else 0)
                if cur_angle not in (0, 90, 180, 270):
                    cur_angle = 0
                new_angle = st.selectbox(
                    "Orientation (°) — 180°/270° enable back-to-back packing",
                    options=[0, 90, 180, 270],
                    index=[0, 90, 180, 270].index(cur_angle),
                )

                new_mvs = row['Assigned_MVS']
                if row['Type'] == "BESS":
                    mvs_opts = editor_df[editor_df['Type'] == "MVS"]["ID"].tolist()
                    mvs_idx = mvs_opts.index(row['Assigned_MVS']) if row['Assigned_MVS'] in mvs_opts else 0
                    new_mvs = st.selectbox("🔌 Reassign MVS Network", options=mvs_opts, index=mvs_idx)

                if st.button("🗑️ Delete Asset", type="primary"):
                    st.session_state["editor_df"] = editor_df.drop(idx).reset_index(drop=True)
                    st.session_state["selected_id"] = None
                    st.rerun()

                if new_x != row['X'] or new_y != row['Y'] or new_angle != cur_angle or new_mvs != row['Assigned_MVS']:
                    editor_df.at[idx, 'X'] = new_x
                    editor_df.at[idx, 'Y'] = new_y
                    editor_df.at[idx, 'Angle'] = new_angle
                    editor_df.at[idx, 'Rotated'] = new_angle in (90, 270)
                    editor_df.at[idx, 'Assigned_MVS'] = new_mvs
                    st.session_state["editor_df"] = editor_df
                    st.rerun()
            else:
                st.caption("No component selected. Click a unit on the chart or use the dropdown to tune its parameters.")

            st.markdown("---")
            action_col1, action_col2 = st.columns(2)

            if action_col1.button("➕ Add BESS Container"):
                new_row = pd.DataFrame([{
                    "ID": f"B_NEW_{len(editor_df)}", "Type": "BESS", "X": 25.0, "Y": 45.0,
                    "Angle": 0, "Rotated": False, "Assigned_MVS": "M1"
                }])
                st.session_state["editor_df"] = pd.concat([editor_df, new_row], ignore_index=True)
                st.rerun()

            if action_col2.button("⚡ Add MVS Station"):
                new_row = pd.DataFrame([{
                    "ID": f"M_NEW_{len(editor_df)}", "Type": "MVS", "X": 25.0, "Y": 45.0,
                    "Angle": 0, "Rotated": False, "Assigned_MVS": None
                }])
                st.session_state["editor_df"] = pd.concat([editor_df, new_row], ignore_index=True)
                st.rerun()

            export_df = editor_df.copy()
            export_df.loc[export_df["Type"] == "BESS", "Capacity (MWh)"] = bess_cap
            export_df.loc[export_df["Type"] == "MVS", "Capacity (MWh)"] = 0.0
            export_df.loc[export_df["Type"] == "MVS", "Power (MW)"] = mvs_pow
            export_df.loc[export_df["Type"] == "BESS", "Power (MW)"] = 0.0

            csv_str = layout_to_csv(export_df, summary={
                "Total Plant Power (MW)": f"{sizing['total_mw']:.1f}",
                "Total Plant Energy (MWh)": f"{sizing['total_mwh']:.1f}",
                "Storage Duration": sizing["duration_label"],
            })

            st.download_button(
                label="📥 Download Engineering Report (CSV)",
                data=csv_str.encode('utf-8'),
                file_name=f'bess_boq_{st.session_state["active_mode"]}.csv',
                mime='text/csv',
                use_container_width=True
            )

# =========================================================
# CO-LOCATED / PAIRED MVS DESIGN TAB (parallel scenario engine)
# =========================================================
with tab_colocated:
    st.markdown("## 🔗 Industrial Co-Located / Paired MVS Design")
    st.caption(
        "Cluster MVS stations onto shared foundation pads — paired side-by-side or grouped "
        "into central power hubs. Hub positions are solved as a capacitated facility-location "
        "problem (weighted Lloyd relaxation with a Weiszfeld geometric-median update that "
        "minimises trenching length) **before** the BESS packing routine deploys. Cable "
        "assignment then balances load across each hub's members. This runs entirely parallel "
        "to — and never alters — the standard automated design engine."
    )

    with st.expander("Step 1: Site Boundary & Co-Location Parameters", expanded=True):
        c_site_input = st.text_area(
            "Global Property Boundary",
            value=st.session_state.get("site_input", DEFAULT_SITE_VERTICES),
            key="co_site_input",
        )
        c_nb_input = st.text_area(
            "Non-Buildable Zones",
            value=st.session_state.get("non_buildable_input", DEFAULT_NONBUILD_ZONE),
            key="co_nb_input",
        )
        c_r_input = st.text_area(
            "Restricted / Out of Scope Zones",
            value=st.session_state.get("restricted_input", DEFAULT_RESTRICT_ZONE),
            key="co_r_input",
        )

        st.markdown("#### Hub Configuration")
        cc1, cc2, cc3 = st.columns(3)
        group_size = cc1.slider("MVS per Hub", 2, 6, 2, 1,
                                help="2 = paired MVS on a shared pad. 3+ = central power hub.")
        pad_gap = cc2.slider("Shared Pad Gap (m)", 0.0, 5.0, 0.5, 0.25,
                             help="Spacing between MVS on the same foundation slab.")
        balance_tol = cc3.slider("Hub Balance Tolerance (BESS)", 0, 4, 1, 1,
                                 help="Best-effort max difference in BESS count between members of a hub.")

        cc4, cc5 = st.columns(2)
        hub_radius = cc4.slider("Hub Snap Search Radius (m)", 1.0, 25.0, 8.0, 0.5,
                                help="How far the optimal continuous hub centre may be relocated to find a valid pad.")
        target_hubs = cc5.number_input("Target Hub Count (0 = auto)", 0, 100, 0, 1,
                                       help="Override the automatic facility-location hub count.")

    run_co = st.button("🔗 Run Co-Located / Paired MVS Optimization", type="primary", use_container_width=True)

    if run_co:
        try:
            c_site_vertices = ast.literal_eval(c_site_input)
            c_nb_vertices = parse_zone(c_nb_input)
            c_r_vertices = parse_zone(c_r_input)
        except Exception as e:
            st.error(f"Error parsing coordinates: {e}")
            st.stop()

        CO_CONFIG = make_config(
            c_site_vertices, c_nb_vertices, c_r_vertices,
            colocation={
                "enabled": True,
                "group_size": int(group_size),
                "pad_gap": float(pad_gap),
                "balance_tolerance": int(balance_tol),
                "hub_search_radius": float(hub_radius),
                "target_hub_count": int(target_hubs),
            },
        )

        try:
            with st.spinner("Solving facility-location hubs and hub-balanced cable assignment..."):
                co_res = run_colocated_optimization(CO_CONFIG, verbose=False)
            st.session_state["colocated_results"] = co_res
            st.session_state["colocated_config"] = CO_CONFIG
        except Exception as e:
            st.error(f"Co-located optimization failed (check the site geometry): {e}")
            st.stop()

    if st.session_state["colocated_results"] is not None:
        co_res = st.session_state["colocated_results"]
        CO_CONFIG = st.session_state["colocated_config"]
        co_metrics = co_res["metrics"]
        hub_metrics = co_res["hub_metrics"]

        co_chart, co_data = st.columns([3, 2])

        with co_chart:
            fig = plot_layout_plotly_hubs(
                co_res["site"], co_res["non_buildable"],
                co_res["mvs_list"], co_res["bess_list"], CO_CONFIG,
                title="Co-Located Hub Layout",
            )
            fig.update_layout(dragmode='pan')
            plotly_config = {
                'scrollZoom': True,
                'toImageButtonOptions': {'format': 'png', 'filename': 'bess_colocated_layout', 'height': 1080, 'width': 1920}
            }
            st.plotly_chart(fig, use_container_width=True, config=plotly_config)

        with co_data:
            sizing = size_system(co_metrics["bess_count"], co_metrics["mvs_count"], bess_cap, mvs_pow)

            st.markdown("### ⬡ Hub Performance")
            hk1, hk2, hk3 = st.columns(3)
            hk1.metric("Hubs (Shared Pads)", hub_metrics["hub_count"])
            hk2.metric("MVS Placed", co_metrics["mvs_count"])
            hk3.metric("BESS Placed", co_metrics["bess_count"])

            hk4, hk5, hk6 = st.columns(3)
            hk4.metric("Avg BESS / Hub", f"{hub_metrics['avg_bess_per_hub']:.1f}")
            hk5.metric("Hub Balance Index", f"{hub_metrics['balance_index']:.2f}",
                       help="Average BESS-count spread within hubs. Lower is better; 0 = perfectly balanced.")
            hk6.metric("Worst Imbalance", hub_metrics["worst_hub_imbalance"])

            st.markdown("### 🏗️ Civil Works Savings")
            cw1, cw2, cw3 = st.columns(3)
            cw1.metric("Shared Foundations", hub_metrics["shared_foundations"],
                       help="One poured pad per hub vs. one per standalone MVS.")
            cw2.metric("Standalone Equiv.", hub_metrics["standalone_foundation_equiv"])
            cw3.metric("Foundation Reduction", f"{hub_metrics['foundation_reduction_pct']:.1f}%")

            st.markdown("### ⚡ Plant & Cabling")
            pk1, pk2, pk3 = st.columns(3)
            pk1.metric("Plant Energy", f"{sizing['total_mwh']:.1f} MWh")
            pk2.metric("Plant Power", f"{sizing['total_mw']:.1f} MW")
            pk3.metric("Total Cable", f"{co_metrics['total_cable']:.1f} m")
            st.caption(f"Storage duration: **{sizing['duration_label']}** ({sizing['duration_h']:.1f} h)")

            if hub_metrics["worst_hub_imbalance"] > balance_tol:
                st.warning(
                    f"Balance target (≤{balance_tol}) not fully met after the re-weighting cap; "
                    f"worst hub spread is {hub_metrics['worst_hub_imbalance']} BESS. "
                    "Layout retained as best-effort — review the balance index above."
                )
            else:
                st.success(f"All hubs balanced within tolerance (≤{balance_tol} BESS spread).")

            st.markdown("---")
            co_export_df = engine_to_df_with_hubs(co_res["mvs_list"], co_res["bess_list"])
            co_export_df.loc[co_export_df["Type"] == "BESS", "Capacity (MWh)"] = bess_cap
            co_export_df.loc[co_export_df["Type"] == "MVS", "Capacity (MWh)"] = 0.0
            co_export_df.loc[co_export_df["Type"] == "MVS", "Power (MW)"] = mvs_pow
            co_export_df.loc[co_export_df["Type"] == "BESS", "Power (MW)"] = 0.0

            co_csv = layout_to_csv(co_export_df, summary={
                "Hubs (Shared Pads)": hub_metrics["hub_count"],
                "Foundation Reduction (%)": f"{hub_metrics['foundation_reduction_pct']:.1f}",
                "Hub Balance Index": f"{hub_metrics['balance_index']:.2f}",
                "Total Plant Power (MW)": f"{sizing['total_mw']:.1f}",
                "Total Plant Energy (MWh)": f"{sizing['total_mwh']:.1f}",
                "Storage Duration": sizing["duration_label"],
            })

            st.download_button(
                label="📥 Download Co-Located Engineering Report (CSV)",
                data=co_csv.encode('utf-8'),
                file_name='bess_boq_colocated.csv',
                mime='text/csv',
                use_container_width=True,
            )
    else:
        st.info("Configure the hub parameters above and run the optimization to generate the co-located layout.")