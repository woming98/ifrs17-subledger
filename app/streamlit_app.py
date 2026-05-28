"""
IFRS 17 Subledger — Streamlit App (Phase 2)

New in Phase 2:
  - VFA (Variable Fee Approach) for participating contracts
  - Multi-period roll-forward (4 quarters, 8 cohorts)
  - Time Series charts: ICL / CSM / ISR trends
  - "How It Works" explainer tab

Run:
    cd ifrs17-subledger
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import io
import os
import sys

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.models.gmm import GMMModel
from src.models.paa import PAAModel
from src.models.vfa import VFAModel
from src.reinsurance import compute_rca
from src.reinsurance_xl import compute_rca_treaties, load_treaties
from src.subledger import ChartOfAccounts, generate_journal
from src.reconciliation import reconcile_portfolio, check_journal_balance, aoc_waterfall
from src.analytics import build_timeseries, portfolio_timeseries, project_csm_runoff
from src.report import pl_summary, bs_summary, aoc_detail, gl_detail, trial_balance
from src.disclosures import (
    note1_icl_movement, note2_icl_components,
    note3_insurance_revenue, note4_ifie,
    note5_rca_movement, note6_maturity_profile, note1_cohort_detail,
)

# ──────────────────────────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="IFRS 17 Subledger Demo",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .section-title {font-size:1.1rem;font-weight:700;color:#1e293b;margin-top:.5rem}
  .concept-card  {background:#f0f9ff;border-left:4px solid #0284c7;border-radius:6px;
                  padding:14px 18px;margin-bottom:12px}
  .concept-title {font-weight:700;color:#0c4a6e;font-size:0.95rem}
  .concept-body  {font-size:0.88rem;color:#334155;margin-top:4px}
  .badge-gmm  {background:#dbeafe;color:#1e40af;border-radius:4px;padding:2px 7px;font-size:.78rem}
  .badge-paa  {background:#dcfce7;color:#166534;border-radius:4px;padding:2px 7px;font-size:.78rem}
  .badge-vfa  {background:#fef9c3;color:#854d0e;border-radius:4px;padding:2px 7px;font-size:.78rem}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

MODEL_DISPATCH = {"GMM": GMMModel(), "PAA": PAAModel(), "VFA": VFAModel()}
QUARTERS = ["2024Q1", "2024Q2", "2024Q3", "2024Q4"]
_DATA_DIR    = os.path.join(_ROOT, "data")
_COA_PATH    = os.path.join(_ROOT, "config", "chart_of_accounts.yaml")
_TREATY_PATH = os.path.join(_ROOT, "config", "reinsurance_treaties.yaml")


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("IFRS 17 Subledger")
    st.caption("Actuarial output → GL entries · Full process demo")

    st.divider()
    st.subheader("📥 Data Source")

    # ── 自定义 CSV 上传（O 功能）──────────────────────────────────────────
    _REQUIRED_COLS = {
        "cohort_id", "product", "measurement_model", "period",
        "pvfcf_eom", "ra_eom", "csm_eom",
        "exp_cf_release", "csm_amortisation", "finance_charge_pl",
    }
    uploaded_file = st.file_uploader(
        "Upload custom actuarial CSV", type=["csv"],
        help="Optional: upload your own actuarial output to replace the demo data.\n"
             "Required columns: cohort_id, product, measurement_model, period, "
             "pvfcf_eom, ra_eom, csm_eom, exp_cf_release, csm_amortisation, finance_charge_pl",
    )

    if uploaded_file is not None:
        try:
            _uploaded_df = pd.read_csv(uploaded_file)
            _missing = _REQUIRED_COLS - set(_uploaded_df.columns)
            if _missing:
                st.error(f"Missing columns: {', '.join(sorted(_missing))}")
                uploaded_file = None
            else:
                _tmp_path = os.path.join(_DATA_DIR, "_uploaded_tmp.csv")
                _uploaded_df.to_csv(_tmp_path, index=False)
                data_file = _tmp_path
                st.success(f"✓ Uploaded: {uploaded_file.name}  ({len(_uploaded_df)} rows)")
                with st.expander("Preview uploaded data", expanded=False):
                    st.dataframe(_uploaded_df.head(8), use_container_width=True)
        except Exception as e:
            st.error(f"Failed to read CSV: {e}")
            uploaded_file = None

    if uploaded_file is None:
        data_file = os.path.join(_DATA_DIR, "actuarial_output.csv")
        if not os.path.exists(data_file):
            st.error("Demo data not found.\nRun: `python data/generate_multi_period.py`")
            st.stop()
        st.success("✓ Demo data: actuarial_output.csv")

    st.divider()
    st.subheader("⚙️ Settings")
    sel_period = st.selectbox("Current period", QUARTERS, index=len(QUARTERS)-1)
    tol = st.number_input("Reconciliation tolerance", value=0.01, step=0.01, format="%.3f")

    run_btn = st.button("▶  Run Subledger", type="primary", use_container_width=True)
    if run_btn:
        st.session_state["has_run"] = True
    if st.sidebar.button("🔄 Reset", use_container_width=True):
        st.session_state["has_run"] = False
        st.rerun()

    st.divider()
    st.caption("**Cohorts in demo**")
    st.markdown("""
- <span class="badge-gmm">GMM</span> TERM_GMM_2022 — Term 5Y<br>
  &nbsp;&nbsp;🔀 **Layered XL**: L1(0–300k) Hanover 20% · L2(300k–600k) MR 20% + BOC 80%
- <span class="badge-gmm">GMM</span> MED_GMM_2021 — Medical LT · QS 20% Munich Re
- <span class="badge-gmm">GMM</span> MED_GMM_ONR — Onerous · No RI
- <span class="badge-gmm">GMM</span> MED_ONR_RECOVERY — ☠️→🟢 Onerous→Profitable · No RI
- <span class="badge-paa">PAA</span> MED_PAA — Medical ST · QS 25% Hannover Life Re
- <span class="badge-paa">PAA</span> RIDER_PAA — Rider ST · No RI
- <span class="badge-vfa">VFA</span> WL_VFA_2019 — Whole Life Par · QS 20% Swiss Re
- <span class="badge-vfa">VFA</span> ENDO_VFA_2022 — Endowment Par · No RI
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────────────────────

st.title("📊 IFRS 17 Subledger — Full Process Demo")
st.markdown("""
> **Coverage**: GMM · PAA · **VFA** · Quota Share + **Layered XL** RCA · AOC 9-step decomposition  
> **Multi-period**: 8 cohorts × 4 quarters · **Sensitivity analysis** · Custom CSV upload  
> **Reinsurance**: 3-treaty layered XL (Hanover Re / Munich Re / BOC Re) for TERM cohort
""")

if not st.session_state.get("has_run", False):
    st.info("Click **▶ Run Subledger** in the sidebar to start.")
    st.markdown("""
**What's in this demo?**

| Model | Products | Key Feature |
|-------|----------|-------------|
| **GMM** (Building Block) | Term Non-Par, Medical Non-Par | Full 9-item AOC, OCI option for IFIE |
| **PAA** (Premium Allocation) | Short-duration Medical, Rider | Unearned premium roll-forward, IACF amortisation |
| **VFA** (Variable Fee) | Whole Life Par, Endowment Par | CSM linked to underlying items; market-driven CSM volatility |

Switch to the **📖 How It Works** tab to understand IFRS 17 concepts.
""")
    st.stop()


# ──────────────────────────────────────────────────────────────────────────────
# Core computation: process ALL periods, cache result
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Running IFRS 17 subledger across all periods…")
def run_all_periods(data_path: str, coa_path: str, treaty_path: str, tol: float):
    """
    Process all 4 quarters from actuarial_output.csv.
    Uses reinsurance_treaties.yaml for layered XL + multi-reinsurer support.

    Returns:
        all_results : {period: [AOCResult, ...]}
        all_rca     : {period: [RCASummary, ...]}   # one entry per treaty
        all_batches : {period: [JournalBatch, ...]}
        ts_df       : long-form time series DataFrame
        warnings    : list of warning strings
    """
    df  = pd.read_csv(data_path)
    coa = ChartOfAccounts(coa_path)

    # 加载再保合同配置（分层 XL + QS）
    treaties_by_cohort, claim_dist = load_treaties(treaty_path)

    all_results: dict = {}
    all_rca:     dict = {}
    all_batches: dict = {}
    warnings = []

    for period in QUARTERS:
        curr_df = df[df["period"] == period]
        if curr_df.empty:
            warnings.append(f"No data found for period {period}.")
            continue

        # Prior period = previous quarter (or 2023Q4 opening for Q1)
        prior_period = "2023Q4" if period == "2024Q1" else QUARTERS[QUARTERS.index(period) - 1]
        prev_df = df[df["period"] == prior_period]

        aoc_list, rca_list, batches = [], [], []

        for _, eom_row in curr_df.iterrows():
            cid   = str(eom_row["cohort_id"])
            model = str(eom_row["measurement_model"]).upper()

            bom_matches = prev_df[prev_df["cohort_id"] == cid]
            if bom_matches.empty:
                warnings.append(f"[{period}] Cohort {cid} not found in prior period — skipped.")
                continue
            bom_row = bom_matches.iloc[0]

            engine = MODEL_DISPATCH.get(model)
            if engine is None:
                warnings.append(f"[{period}] Unknown model '{model}' for {cid} — skipped.")
                continue

            result = engine.compute_aoc(bom_row, eom_row)
            aoc_list.append(result)

        # 生成每个 cohort 的 RCA（优先用 treaty YAML，无则退回 CSV cession_rate）
        rcas_by_id: dict = {}  # cohort_id → List[RCASummary]
        for a in aoc_list:
            if a.cohort_id in treaties_by_cohort:
                treaties = treaties_by_cohort[a.cohort_id]
                rcas = compute_rca_treaties(a, treaties, claim_dist)
            elif a.cession_rate > 0:
                rcas = [compute_rca(a)]
            else:
                rcas = []

            if rcas:
                rca_list.extend(rcas)
                rcas_by_id[a.cohort_id] = rcas

        for a in aoc_list:
            batch = generate_journal(
                a, coa,
                rcas=rcas_by_id.get(a.cohort_id),
            )
            batches.append(batch)

        all_results[period] = aoc_list
        all_rca[period]     = rca_list
        all_batches[period] = batches

    ts_df = build_timeseries(all_results)
    return all_results, all_rca, all_batches, ts_df, warnings


all_results, all_rca, all_batches, ts_df, warnings = run_all_periods(data_file, _COA_PATH, _TREATY_PATH, tol)

for w in warnings:
    st.warning(w)

# Shortcut to selected period
aoc_list = all_results.get(sel_period, [])
rca_list = all_rca.get(sel_period, [])
batches  = all_batches.get(sel_period, [])


# ──────────────────────────────────────────────────────────────────────────────
# KPI metrics (selected period)
# ──────────────────────────────────────────────────────────────────────────────

total_icl     = sum(a.eom_icl for a in aoc_list)
total_rca_val = sum(r.rca_eom_icl for r in rca_list)
total_isr     = sum(a.insurance_revenue for a in aoc_list)
total_ise     = sum(a.insurance_service_expense for a in aoc_list)
total_isr_rca = sum(r.rca_insurance_revenue for r in rca_list)
net_pl        = total_isr - total_ise + sum(a.ifie_pl for a in aoc_list) + total_isr_rca
all_ok        = all(a.reconciliation_ok for a in aoc_list)

st.subheader(f"Period: {sel_period}  |  {len(aoc_list)} cohorts  |  GMM + PAA + VFA")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Gross ICL EOM",            f"{total_icl:,.0f}",      help="'000 HKD")
c2.metric("RCA EOM",                  f"{total_rca_val:,.0f}",  help="Reinsurance Contract Asset")
c3.metric("Net ICL",                  f"{total_icl - total_rca_val:,.0f}")
c4.metric("Insurance Revenue (ISR)",  f"{total_isr:,.0f}")
c5.metric("Net P&L (incl. RI)",       f"{net_pl:,.0f}")
c6.metric("Reconciliation",           "✅ All passed" if all_ok else "❌ Errors")


# ──────────────────────────────────────────────────────────────────────────────
# Tabs
# ──────────────────────────────────────────────────────────────────────────────

tab_dash, tab_aoc, tab_pl, tab_bs, tab_gl, tab_tb, tab_recon, tab_ts, tab_sens, tab_disc, tab_how = st.tabs([
    "🏠 Dashboard",
    "📈 AOC Waterfall",
    "💹 P&L Summary",
    "🏦 Balance Sheet",
    "📒 GL Entries",
    "⚖️ Trial Balance",
    "✅ Reconciliation",
    "📉 Time Series",
    "🎯 Sensitivity",
    "📋 Disclosures",
    "📖 How It Works",
])


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# Tab 0 — Executive Dashboard (R)
# ══════════════════════════════════════════════════════════════════════════════

with tab_dash:
    # ── Full-year aggregates ──────────────────────────────────────────────
    _DASH_YEAR = "2024"
    _fy_periods = sorted(k for k in all_results if k.startswith(_DASH_YEAR))
    _fy_all: list = [r for p in _fy_periods for r in all_results[p]]
    _fy_rca_flat: list = [r for p in _fy_periods for r in all_rca.get(p, [])]

    # Closing balances (last period)
    _last_aoc = all_results.get(_fy_periods[-1], []) if _fy_periods else []
    _first_aoc = all_results.get(_fy_periods[0], []) if _fy_periods else []

    def _s(lst, f):
        return sum(getattr(r, f, 0.0) for r in lst)

    # Gross ICL
    _gross_icl_open  = _s(_first_aoc, "bom_pvfcf") + _s(_first_aoc, "bom_ra") + _s(_first_aoc, "bom_csm") - _s(_first_aoc, "bom_lc")
    _gross_icl_close = _s(_last_aoc,  "eom_pvfcf") + _s(_last_aoc,  "eom_ra") + _s(_last_aoc,  "eom_csm") - _s(_last_aoc,  "eom_lc")
    _rca_close       = -sum(r.rca_eom_icl for r in _fy_rca_flat if r.period == _fy_periods[-1])  # asset +ve
    _net_icl_close   = _gross_icl_close - _rca_close

    # CSM
    _csm_open  = _s(_first_aoc, "bom_csm")
    _csm_close = _s(_last_aoc,  "eom_csm")
    _lc_open   = _s(_first_aoc, "bom_lc")
    _lc_close  = _s(_last_aoc,  "eom_lc")

    # Full-year P&L
    _fy_isr   = -(_s(_fy_all, "expected_cf_release") + _s(_fy_all, "csm_amortisation")
                  + min(_s(_fy_all, "lc_reversal"), 0.0))
    _fy_ise   = _s(_fy_all, "experience_variance") + max(_s(_fy_all, "lc_reversal"), 0.0)
    _fy_ifie  = _s(_fy_all, "finance_charge_pl")
    _fy_aspl  = _s(_fy_all, "assumption_chg_pl")
    _fy_ri_rev = -sum(
        r.rca_expected_cf_release + r.rca_csm_amortisation
        for r in _fy_rca_flat
    )
    _fy_net_pl = _fy_isr - _fy_ise + _fy_ifie + _fy_aspl + _fy_ri_rev

    # CSM bridge components
    _csm_amort   = _s(_fy_all, "csm_amortisation")       # negative
    _csm_as_csm  = _s(_fy_all, "assumption_chg_csm")     # intra-ICL
    _csm_und     = sum(getattr(r, "underlying_items_chg", 0.0) for r in _fy_all)
    _csm_new_biz = _csm_close - _csm_open - _csm_amort - _csm_as_csm - _csm_und  # residual = new biz + exp var underlying

    # Health ratios
    _csm_coverage = (_csm_close / _gross_icl_close * 100) if _gross_icl_close else 0
    _onerous_share = (_lc_close / (_lc_close + _csm_close) * 100) if (_lc_close + _csm_close) > 0 else 0
    _ri_coverage   = (_rca_close / _gross_icl_close * 100) if _gross_icl_close else 0
    _isr_yield     = (_fy_isr / ((_gross_icl_open + _gross_icl_close) / 2) * 100) if _gross_icl_open else 0

    # ── Section title ─────────────────────────────────────────────────────
    st.markdown("## 🏠 Executive Dashboard — IFRS 17 Portfolio Overview  |  Full Year 2024")
    st.caption(f"Portfolio: {len(_last_aoc)} cohorts · GMM + PAA + VFA · HKD '000 · Recon: {'✅ All passed' if all(a.reconciliation_ok for a in _last_aoc) else '⚠️ Check needed'}")

    # ── Row 1: KPI cards ──────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Gross ICL (Closing)",  f"{_gross_icl_close:,.0f}",
              delta=f"{_gross_icl_close - _gross_icl_open:,.0f} vs Jan-24",
              delta_color="inverse")
    k2.metric("Net ICL (after RI)",   f"{_net_icl_close:,.0f}",
              help="Gross ICL minus Reinsurance Contract Asset")
    k3.metric("CSM (Closing)",        f"{_csm_close:,.0f}",
              delta=f"{_csm_close - _csm_open:,.0f}",
              delta_color="normal")
    k4.metric("Full-Year ISR",        f"{_fy_isr:,.0f}",
              help="Insurance Revenue recognised in 2024")
    k5.metric("Full-Year Net P&L",    f"{_fy_net_pl:,.0f}",
              help="Net insurance result including RI and IFIE")
    k6.metric("RI Coverage",          f"{_ri_coverage:.1f}%",
              help="Reinsurance Contract Asset / Gross ICL")

    st.divider()

    # ── Row 2: ICL Donut + CSM Bridge ─────────────────────────────────────
    col_donut, col_csm = st.columns([1, 2])

    with col_donut:
        st.subheader("ICL Mix by Model")
        _model_icl = {}
        for r in _last_aoc:
            m = r.measurement_model
            _model_icl[m] = _model_icl.get(m, 0.0) + r.eom_icl
        _model_colors = {"GMM": "#3b82f6", "VFA": "#8b5cf6", "PAA": "#22c55e"}
        fig_donut = go.Figure(go.Pie(
            labels=list(_model_icl.keys()),
            values=[round(v, 1) for v in _model_icl.values()],
            hole=0.55,
            marker_colors=[_model_colors.get(m, "#94a3b8") for m in _model_icl],
            textinfo="label+percent",
            hovertemplate="%{label}: %{value:,.1f} ('000 HKD)<br>%{percent}<extra></extra>",
        ))
        fig_donut.add_annotation(
            text=f"<b>{_gross_icl_close:,.0f}</b><br><span style='font-size:10px'>Gross ICL</span>",
            x=0.5, y=0.5, showarrow=False, font=dict(size=14),
        )
        fig_donut.update_layout(
            height=320, margin=dict(l=10, r=10, t=20, b=10),
            showlegend=True, legend=dict(orientation="h", y=-0.1),
        )
        st.plotly_chart(fig_donut, use_container_width=True)

        # ── Mini health table ──────────────────────────────────────────────
        _health = pd.DataFrame([
            {"Indicator": "CSM Coverage",   "Value": f"{_csm_coverage:.1f}%",
             "Meaning": "Profit locked in ICL"},
            {"Indicator": "Onerous Share",  "Value": f"{_onerous_share:.1f}%",
             "Meaning": "LC / (LC + CSM)"},
            {"Indicator": "RI Coverage",    "Value": f"{_ri_coverage:.1f}%",
             "Meaning": "RCA / Gross ICL"},
            {"Indicator": "ISR Yield",      "Value": f"{_isr_yield:.1f}%",
             "Meaning": "ISR / Avg ICL"},
        ])
        st.dataframe(_health, use_container_width=True, hide_index=True, height=180)

    with col_csm:
        st.subheader("CSM Bridge — Full Year 2024")
        _csm_labels = [
            "Opening CSM<br>(1 Jan 2024)",
            "New Business<br>& Other",
            "Assumption Δ<br>→ CSM",
            "VFA: Underlying<br>Items Δ",
            "CSM Amortisation<br>(→ Revenue)",
            "Closing CSM<br>(31 Dec 2024)",
        ]
        _csm_values = [
            _csm_open,
            round(_csm_new_biz, 1),
            round(_csm_as_csm, 1),
            round(_csm_und, 1),
            round(_csm_amort, 1),
            _csm_close,
        ]
        _csm_measure = ["absolute", "relative", "relative", "relative", "relative", "total"]
        _csm_bar_colors = [
            "#3b82f6",  # opening: blue
            "#22c55e",  # NB: green
            "#a855f7",  # assumption → CSM: purple
            "#f59e0b",  # VFA underlying: amber
            "#ef4444",  # amortisation (negative): red
            "#1d4ed8",  # closing: dark blue
        ]
        fig_csm_bridge = go.Figure(go.Waterfall(
            orientation="v",
            measure=_csm_measure,
            x=_csm_labels,
            y=_csm_values,
            connector=dict(line=dict(color="#cbd5e1", width=1, dash="dot")),
            increasing=dict(marker_color="#22c55e"),
            decreasing=dict(marker_color="#ef4444"),
            totals=dict(marker_color="#3b82f6"),
            text=[f"{v:,.0f}" for v in _csm_values],
            textposition="outside",
            hovertemplate="%{x}: %{y:,.1f} ('000 HKD)<extra></extra>",
        ))
        fig_csm_bridge.update_layout(
            title="CSM: Opening → Movements → Closing ('000 HKD)",
            height=380, margin=dict(l=40, r=40, t=50, b=40),
            yaxis_title="'000 HKD",
            showlegend=False,
        )
        st.plotly_chart(fig_csm_bridge, use_container_width=True)

    st.divider()

    # ── Row 3: P&L Waterfall + Cohort Heatmap ─────────────────────────────
    col_pl, col_heat = st.columns([3, 2])

    with col_pl:
        st.subheader("P&L Waterfall — Full Year 2024")
        _pl_labels = [
            "Insurance<br>Revenue (ISR)",
            "Insurance<br>Service Exp (ISE)",
            "IFIE — P&L<br>(DAIR unwind)",
            "Assumption Δ<br>→ P&L",
            "RI Net<br>Revenue",
            "Net Insurance<br>Result",
        ]
        _pl_values = [
            round(_fy_isr, 1),
            round(-_fy_ise, 1),
            round(_fy_ifie, 1),
            round(_fy_aspl, 1),
            round(_fy_ri_rev, 1),
            round(_fy_net_pl, 1),
        ]
        _pl_measure = ["relative", "relative", "relative", "relative", "relative", "total"]

        fig_pl_wf = go.Figure(go.Waterfall(
            orientation="v",
            measure=_pl_measure,
            x=_pl_labels,
            y=_pl_values,
            connector=dict(line=dict(color="#cbd5e1", width=1, dash="dot")),
            increasing=dict(marker_color="#22c55e"),
            decreasing=dict(marker_color="#ef4444"),
            totals=dict(marker_color="#1d4ed8"),
            text=[f"{v:+,.0f}" for v in _pl_values],
            textposition="outside",
            hovertemplate="%{x}: %{y:,.1f} ('000 HKD)<extra></extra>",
        ))
        fig_pl_wf.update_layout(
            title="Full Year 2024 P&L Build-up ('000 HKD)",
            height=380, margin=dict(l=40, r=40, t=50, b=40),
            yaxis_title="'000 HKD",
            showlegend=False,
        )
        st.plotly_chart(fig_pl_wf, use_container_width=True)

    with col_heat:
        st.subheader("Cohort Snapshot")
        _cohort_rows = []
        for r in sorted(_last_aoc, key=lambda x: -x.eom_icl):
            _status = "🟢 Profitable" if r.eom_csm > 0 else ("☠️ Recovering" if r.cohort_id == "MED_ONR_RECOVERY" and r.eom_lc == 0 else "🔴 Onerous")
            _cohort_rows.append({
                "Cohort": r.cohort_id,
                "Model": r.measurement_model,
                "ICL": round(r.eom_icl, 0),
                "CSM": round(r.eom_csm, 0),
                "LC":  round(r.eom_lc, 0),
                "Status": _status,
            })
        _cohort_snap = pd.DataFrame(_cohort_rows)

        def _snap_style(row):
            if "Onerous" in row["Status"]:
                return ["background-color:#fff1f2"] * len(row)
            if "Recovering" in row["Status"]:
                return ["background-color:#f0fdf4"] * len(row)
            return [""] * len(row)

        st.dataframe(
            _cohort_snap.style.apply(_snap_style, axis=1)
                .format({"ICL": "{:,.0f}", "CSM": "{:,.0f}", "LC": "{:,.0f}"}),
            use_container_width=True, hide_index=True, height=330,
        )

    st.divider()

    # ── Row 4: Portfolio trends mini sparklines ───────────────────────────
    st.subheader("Portfolio Trends (Quarterly)")
    _port_ts = portfolio_timeseries(ts_df)

    _col_s1, _col_s2, _col_s3 = st.columns(3)

    with _col_s1:
        fig_icl_trend = go.Figure()
        fig_icl_trend.add_scatter(
            x=_port_ts["period"], y=_port_ts["eom_icl"],
            mode="lines+markers+text",
            line=dict(color="#3b82f6", width=3),
            marker=dict(size=8),
            text=[f"{v:,.0f}" for v in _port_ts["eom_icl"]],
            textposition="top center", textfont=dict(size=9),
        )
        fig_icl_trend.update_layout(
            title="Gross ICL Trend", height=220,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis=dict(showgrid=False), yaxis_title="'000",
        )
        st.plotly_chart(fig_icl_trend, use_container_width=True)

    with _col_s2:
        fig_csm_trend = go.Figure()
        fig_csm_trend.add_scatter(
            x=_port_ts["period"], y=_port_ts["eom_csm"],
            mode="lines+markers+text",
            line=dict(color="#22c55e", width=3),
            marker=dict(size=8),
            text=[f"{v:,.0f}" for v in _port_ts["eom_csm"]],
            textposition="top center", textfont=dict(size=9),
        )
        fig_csm_trend.add_scatter(
            x=_port_ts["period"], y=_port_ts["eom_lc"],
            mode="lines+markers",
            line=dict(color="#ef4444", width=2, dash="dash"),
            marker=dict(size=6), name="LC",
        )
        fig_csm_trend.update_layout(
            title="CSM (green) vs LC (red dashed) Trend", height=220,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis=dict(showgrid=False), yaxis_title="'000",
            showlegend=False,
        )
        st.plotly_chart(fig_csm_trend, use_container_width=True)

    with _col_s3:
        fig_isr_trend = go.Figure()
        fig_isr_trend.add_bar(
            x=_port_ts["period"], y=_port_ts["insurance_revenue"],
            marker_color="#22c55e", name="ISR", opacity=0.85,
        )
        fig_isr_trend.add_bar(
            x=_port_ts["period"], y=-_port_ts["insurance_service_expense"],
            marker_color="#ef4444", name="ISE (negative)", opacity=0.75,
        )
        fig_isr_trend.update_layout(
            barmode="group",
            title="ISR vs ISE by Quarter", height=220,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis=dict(showgrid=False), yaxis_title="'000",
            showlegend=True,
            legend=dict(orientation="h", y=-0.3),
        )
        st.plotly_chart(fig_isr_trend, use_container_width=True)

    # ── Onerous highlight ─────────────────────────────────────────────────
    st.divider()
    st.subheader("☠️ → 🟢  Onerous Contract Highlight")
    _onr_col1, _onr_col2, _onr_col3, _onr_col4, _onr_col5 = st.columns(5)
    _onr_cohorts = [r for r in _last_aoc if r.eom_lc > 0 or r.cohort_id == "MED_ONR_RECOVERY"]
    _total_lc = sum(r.eom_lc for r in _last_aoc)
    _n_onerous = sum(1 for r in _last_aoc if r.eom_lc > 0)
    _recovered = any(r.cohort_id == "MED_ONR_RECOVERY" and r.eom_lc == 0 and r.eom_csm > 0
                     for r in _last_aoc)

    _onr_col1.metric("Onerous Cohorts (Closing)", str(_n_onerous),
                     help="Cohorts with LC > 0 at 31 Dec 2024")
    _onr_col2.metric("Total LC (Closing)", f"{_total_lc:,.0f}",
                     delta=f"{_total_lc - _lc_open:,.0f}", delta_color="inverse")
    _onr_col3.metric("Onerous % of Portfolio", f"{_onerous_share:.1f}%",
                     help="LC / (LC + CSM) at 31 Dec 2024")
    _onr_col4.metric("MED_ONR_RECOVERY", "✅ Recovered!" if _recovered else "Still onerous",
                     help="The cohort that completed the full onerous→profitable lifecycle")
    _onr_col5.metric("New CSM from Recovery", f"{next((r.eom_csm for r in _last_aoc if r.cohort_id == 'MED_ONR_RECOVERY'), 0):,.0f}",
                     help="CSM born when LC cleared in Q4 2024")


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1 — AOC Waterfall
# ══════════════════════════════════════════════════════════════════════════════

with tab_aoc:
    st.markdown(f'<div class="section-title">ICL Movement Waterfall — {sel_period} (Portfolio)</div>',
                unsafe_allow_html=True)

    wf_df  = aoc_waterfall(aoc_list)
    labels = wf_df["aoc_item"].tolist()
    values = wf_df["amount"].tolist()
    measure = ["absolute"] + ["relative"] * (len(labels) - 2) + ["absolute"]

    fig_wf = go.Figure(go.Waterfall(
        orientation="v", measure=measure,
        x=labels, y=values,
        text=[f"{v:+,.0f}" for v in values], textposition="outside",
        connector={"line": {"color": "rgba(80,80,80,0.25)"}},
        increasing={"marker": {"color": "#ef4444"}},
        decreasing={"marker": {"color": "#22c55e"}},
        totals={"marker": {"color": "#3b82f6"}},
    ))
    fig_wf.update_layout(
        title=f"ICL Movement Waterfall — {sel_period} (all cohorts, '000 HKD)",
        xaxis_tickangle=-30, height=500,
        margin=dict(l=40, r=40, t=60, b=110),
        plot_bgcolor="#fafafa", paper_bgcolor="#fff",
    )
    st.plotly_chart(fig_wf, use_container_width=True)

    # Model filter
    col_m1, col_m2 = st.columns([2, 5])
    with col_m1:
        model_filter = st.multiselect("Filter by model", ["GMM", "PAA", "VFA"],
                                      default=["GMM", "PAA", "VFA"])
    filtered_aoc = [a for a in aoc_list if a.measurement_model in model_filter]

    aoc_df = aoc_detail(filtered_aoc)
    COLS = ["cohort_id", "product", "model", "BOM ICL",
            "① New Business", "② Expected CF Release (incl. RA)",
            "③ Experience Variance", "④ CSM Amortisation", "⑤ LC Reversal",
            "⑥ IFIE — P&L (DAIR unwind)", "⑦ IFIE — OCI (rate change)",
            "⑧ Assumption Change → P&L", "⑨ Assumption Change → CSM",
            "VFA — Underlying Items Change → CSM",
            "EOM ICL (Input)", "Recon Diff"]
    existing = [c for c in COLS if c in aoc_df.columns]
    st.dataframe(aoc_df[existing], use_container_width=True, height=280)


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2 — P&L Summary
# ══════════════════════════════════════════════════════════════════════════════

with tab_pl:
    st.markdown('<div class="section-title">Insurance Service P&L Summary</div>',
                unsafe_allow_html=True)

    pl_df = pl_summary(aoc_list, rca_list)
    st.dataframe(
        pl_df.style.format({c: "{:,.2f}" for c in pl_df.select_dtypes("number").columns}),
        use_container_width=True, height=300,
    )

    plot_df = pl_df[pl_df["cohort_id"] != "__TOTAL__"]
    fig_pl  = go.Figure()
    fig_pl.add_bar(name="Insurance Revenue (ISR)", x=plot_df["cohort_id"],
                   y=plot_df["insurance_revenue"],      marker_color="#22c55e")
    fig_pl.add_bar(name="Insurance Service Exp. (ISE)", x=plot_df["cohort_id"],
                   y=-plot_df["insurance_service_expense"], marker_color="#ef4444")
    fig_pl.add_bar(name="IFIE P&L", x=plot_df["cohort_id"],
                   y=plot_df["ifie_pl"],                marker_color="#f59e0b")
    fig_pl.add_bar(name="RI Revenue (RCA)", x=plot_df["cohort_id"],
                   y=plot_df["rca_insurance_revenue"],  marker_color="#6366f1")
    fig_pl.add_scatter(name="Net P&L (incl. RI)", x=plot_df["cohort_id"],
                       y=plot_df["net_pl_after_ri"],
                       mode="markers+lines",
                       marker=dict(size=10, color="#1e293b"), line=dict(dash="dash"))
    fig_pl.update_layout(
        barmode="group", title=f"P&L Breakdown — {sel_period} ('000 HKD)",
        height=420, xaxis_tickangle=-20, margin=dict(l=40, r=40, t=60, b=60),
    )
    st.plotly_chart(fig_pl, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3 — Balance Sheet
# ══════════════════════════════════════════════════════════════════════════════

with tab_bs:
    st.markdown('<div class="section-title">ICL / RCA Closing Balances</div>',
                unsafe_allow_html=True)

    bs_df = bs_summary(aoc_list, rca_list)
    st.dataframe(
        bs_df.style.format({c: "{:,.2f}" for c in bs_df.select_dtypes("number").columns}),
        use_container_width=True, height=300,
    )

    plot_bs = bs_df[bs_df["cohort_id"] != "__TOTAL__"]
    fig_bs  = go.Figure()
    fig_bs.add_bar(name="PVFCF", x=plot_bs["cohort_id"], y=plot_bs["icl_pvfcf"], marker_color="#3b82f6")
    fig_bs.add_bar(name="RA",    x=plot_bs["cohort_id"], y=plot_bs["icl_ra"],    marker_color="#60a5fa")
    fig_bs.add_bar(name="CSM",   x=plot_bs["cohort_id"], y=plot_bs["icl_csm"],   marker_color="#93c5fd")
    fig_bs.add_bar(name="LC",    x=plot_bs["cohort_id"], y=-plot_bs["icl_lc"],   marker_color="#ef4444")
    fig_bs.add_bar(name="RCA",   x=plot_bs["cohort_id"], y=plot_bs["rca_total"], marker_color="#22c55e")
    fig_bs.update_layout(
        barmode="stack", title=f"ICL Components — {sel_period} ('000 HKD)",
        height=440, xaxis_tickangle=-20, margin=dict(l=40, r=40, t=60, b=60),
    )
    st.plotly_chart(fig_bs, use_container_width=True)

    # ── RCA 再保人 / 分层明细 ─────────────────────────────────────────────
    if rca_list:
        with st.expander("📋 RCA Detail — by Reinsurer & Layer", expanded=False):
            rca_rows = []
            for r in rca_list:
                rca_rows.append({
                    "cohort_id":   r.cohort_id,
                    "period":      r.period,
                    "treaty_id":   r.treaty_id,
                    "reinsurer":   r.reinsurer,
                    "layer":       r.layer_label,
                    "eff_rate_%":  round(r.cession_rate * 100, 2),
                    "rca_bom":     round(r.rca_bom_icl, 2),
                    "rca_eom":     round(r.rca_eom_icl, 2),
                    "rca_isr":     round(r.rca_insurance_revenue, 2),
                    "recon_ok":    r.reconciliation_ok,
                })
            rca_detail_df = pd.DataFrame(rca_rows)

            def _color_recon(val):
                return "color:#16a34a;font-weight:700" if val else "color:#dc2626;font-weight:700"

            num_cols = ["eff_rate_%", "rca_bom", "rca_eom", "rca_isr"]
            st.dataframe(
                rca_detail_df.style
                    .format({c: "{:,.2f}" for c in num_cols})
                    .map(_color_recon, subset=["recon_ok"]),
                use_container_width=True,
                height=min(80 + len(rca_detail_df) * 35, 400),
            )

            # 分再保人柱图
            if len(rca_detail_df) > 1:
                fig_ri = px.bar(
                    rca_detail_df,
                    x="cohort_id", y="rca_eom",
                    color="reinsurer", text="layer",
                    barmode="stack",
                    title=f"RCA EOM by Reinsurer — {sel_period}",
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                fig_ri.update_layout(height=360, margin=dict(l=40, r=40, t=60, b=60))
                st.plotly_chart(fig_ri, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# Tab 4 — GL Entries
# ══════════════════════════════════════════════════════════════════════════════

with tab_gl:
    st.markdown('<div class="section-title">GL Journal Entries (Debit / Credit Log)</div>',
                unsafe_allow_html=True)

    gl_df = gl_detail(batches)
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        sel_cohort = st.selectbox("Filter by cohort", ["(All)"] + sorted(gl_df["cohort_id"].unique().tolist()))
    with col_f2:
        sel_acc = st.selectbox("Filter by account", ["(All)"] + sorted(gl_df["account_code"].unique().tolist()))

    filtered = gl_df.copy()
    if sel_cohort != "(All)": filtered = filtered[filtered["cohort_id"] == sel_cohort]
    if sel_acc    != "(All)": filtered = filtered[filtered["account_code"] == sel_acc]

    st.caption(f"Showing {len(filtered)} of {len(gl_df)} rows")
    st.dataframe(
        filtered.style.format({"debit": "{:,.2f}", "credit": "{:,.2f}"}),
        use_container_width=True, height=460,
    )

    csv_buf = io.StringIO()
    gl_df.to_csv(csv_buf, index=False, encoding="utf-8")
    st.download_button("⬇ Download GL Entries CSV", csv_buf.getvalue(),
                       file_name=f"gl_entries_{sel_period}.csv", mime="text/csv")


# ══════════════════════════════════════════════════════════════════════════════
# Tab 5 — Trial Balance
# ══════════════════════════════════════════════════════════════════════════════

with tab_tb:
    st.markdown('<div class="section-title">Trial Balance</div>', unsafe_allow_html=True)

    tb_df = trial_balance(batches)

    def color_net(val):
        if isinstance(val, (int, float)):
            return "color:#1d4ed8;" if val > 0 else ("color:#dc2626;" if val < 0 else "")
        return ""

    st.dataframe(
        tb_df.style
             .format({"total_debit": "{:,.2f}", "total_credit": "{:,.2f}", "net": "{:,.2f}"})
             .map(color_net, subset=["net"]),
        use_container_width=True, height=420,
    )

    total_dr = tb_df["total_debit"].sum()
    total_cr = tb_df["total_credit"].sum()
    diff_tb  = total_dr - total_cr

    c_a, c_b, c_c = st.columns(3)
    c_a.metric("Total Debits",  f"{total_dr:,.2f}")
    c_b.metric("Total Credits", f"{total_cr:,.2f}")
    c_c.metric("Difference",    f"{diff_tb:,.4f}",
               delta="✅ Balanced" if abs(diff_tb) < tol else "❌ Out of balance",
               delta_color="normal" if abs(diff_tb) < tol else "inverse")


# ══════════════════════════════════════════════════════════════════════════════
# Tab 6 — Reconciliation
# ══════════════════════════════════════════════════════════════════════════════

with tab_recon:
    st.markdown('<div class="section-title">AOC Reconciliation — BOM + Movements = EOM</div>',
                unsafe_allow_html=True)

    recon_df = reconcile_portfolio(aoc_list, tol)

    def flag_ok(val):
        if val is True:  return "background-color:#d1fae5;color:#065f46;"
        if val is False: return "background-color:#fee2e2;color:#991b1b;"
        return ""

    st.dataframe(
        recon_df.style
                .format({c: "{:,.2f}" for c in recon_df.select_dtypes("number").columns})
                .map(flag_ok, subset=["ok"]),
        use_container_width=True, height=320,
    )

    st.markdown('<div class="section-title">GL Balance Check</div>', unsafe_allow_html=True)
    bal_df = check_journal_balance(batches, tol)
    st.dataframe(
        bal_df.style
              .format({"total_debit": "{:,.2f}", "total_credit": "{:,.2f}", "diff": "{:,.4f}"})
              .map(flag_ok, subset=["balanced"]),
        use_container_width=True, height=280,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 7 — Time Series (NEW)
# ══════════════════════════════════════════════════════════════════════════════

with tab_ts:
    st.markdown('<div class="section-title">Multi-Period Analytics (2024Q1 – Q4)</div>',
                unsafe_allow_html=True)

    port_ts = portfolio_timeseries(ts_df)

    # ── Chart 1: Portfolio ICL trend ──────────────────────────────────────
    st.subheader("Portfolio ICL Trend")
    fig_icl = go.Figure()
    fig_icl.add_scatter(x=port_ts["period"], y=port_ts["eom_icl"],
                        name="Gross ICL", mode="lines+markers",
                        marker=dict(size=8), line=dict(color="#3b82f6", width=2))
    # PVFCF / RA / CSM stacked area
    fig_icl.add_scatter(x=port_ts["period"], y=port_ts["eom_pvfcf"],
                        name="PVFCF", mode="lines", line=dict(dash="dot", color="#60a5fa"))
    fig_icl.add_scatter(x=port_ts["period"], y=port_ts["eom_csm"],
                        name="CSM (total)", mode="lines+markers",
                        marker=dict(size=7), line=dict(color="#f59e0b", width=2))
    fig_icl.update_layout(
        title="Portfolio — Gross ICL, PVFCF & CSM over Time ('000 HKD)",
        height=380, margin=dict(l=40, r=40, t=60, b=40),
    )
    st.plotly_chart(fig_icl, use_container_width=True)

    # ── Chart 2: P&L trend ────────────────────────────────────────────────
    st.subheader("Portfolio P&L Trend")
    fig_pl_ts = go.Figure()
    fig_pl_ts.add_bar(x=port_ts["period"], y=port_ts["insurance_revenue"],
                      name="Insurance Revenue (ISR)", marker_color="#22c55e")
    fig_pl_ts.add_bar(x=port_ts["period"], y=-port_ts["insurance_service_expense"],
                      name="Insurance Service Exp. (ISE)", marker_color="#ef4444")
    fig_pl_ts.add_bar(x=port_ts["period"], y=port_ts["ifie_pl"],
                      name="IFIE — P&L", marker_color="#f59e0b")
    fig_pl_ts.update_layout(
        barmode="group", title="Portfolio P&L by Quarter ('000 HKD)",
        height=360, margin=dict(l=40, r=40, t=60, b=40),
    )
    st.plotly_chart(fig_pl_ts, use_container_width=True)

    # ── Chart 3: VFA CSM volatility vs GMM CSM ───────────────────────────
    st.subheader("VFA vs GMM — CSM Volatility (Underlying Items Effect)")
    st.markdown("""
> **Key insight**: VFA CSM (participating) is more volatile than GMM CSM because it absorbs 
> changes in underlying items (investment returns). A market correction in Q2 visibly dips 
> VFA CSM, while GMM CSM decreases steadily from amortisation only.
""")

    vfa_cohorts = ts_df[ts_df["model"] == "VFA"][["period", "cohort_id", "eom_csm", "underlying_items_chg"]]
    gmm_cohorts = ts_df[ts_df["model"] == "GMM"][["period", "cohort_id", "eom_csm"]]

    fig_csm = go.Figure()
    for cid in vfa_cohorts["cohort_id"].unique():
        d = vfa_cohorts[vfa_cohorts["cohort_id"] == cid]
        fig_csm.add_scatter(x=d["period"], y=d["eom_csm"],
                            name=f"VFA: {cid}", mode="lines+markers",
                            marker=dict(size=9), line=dict(width=2.5))
    for cid in gmm_cohorts["cohort_id"].unique():
        d = gmm_cohorts[gmm_cohorts["cohort_id"] == cid]
        fig_csm.add_scatter(x=d["period"], y=d["eom_csm"],
                            name=f"GMM: {cid}", mode="lines+markers",
                            marker=dict(size=6, symbol="square"), line=dict(dash="dash", width=1.5))
    fig_csm.update_layout(
        title="CSM by Cohort — VFA (solid) vs GMM (dashed) over 4 Quarters ('000 HKD)",
        height=420, margin=dict(l=40, r=40, t=60, b=40),
        legend=dict(orientation="v", x=1.01, y=1),
    )
    st.plotly_chart(fig_csm, use_container_width=True)

    # ── Chart 4: VFA underlying items change ─────────────────────────────
    st.subheader("VFA — Underlying Items Change vs CSM Movement")
    fig_und = go.Figure()
    vfa_ts = ts_df[ts_df["model"] == "VFA"]
    for cid in vfa_ts["cohort_id"].unique():
        d = vfa_ts[vfa_ts["cohort_id"] == cid]
        fig_und.add_bar(x=d["period"], y=d["underlying_items_chg"],
                        name=f"{cid} — Underlying Δ", opacity=0.75)
    fig_und.update_layout(
        barmode="group",
        title="VFA: Quarterly Underlying Items Change (absorbed by CSM) — '000 HKD",
        height=340, margin=dict(l=40, r=40, t=60, b=40),
    )
    st.plotly_chart(fig_und, use_container_width=True)

    # ── Chart 5: OCI Cumulative Reserve ──────────────────────────────────
    st.divider()
    st.subheader("📊 OCI Cumulative Reserve — Accumulated Insurance Finance Effect")
    st.markdown("""
Under the **OCI option** (applied to GMM cohorts), the interest rate change effect
accumulates in equity as an **OCI Reserve**.  
- When **rates rise**: OCI Reserve becomes more negative → equity eroded  
- When **rates fall**: OCI Reserve becomes more positive → equity boosted  
- The reserve **recycles back to P&L** as contracts run off (over remaining term)
""")

    # Compute cumulative OCI per cohort across the 4 quarters
    oci_df = ts_df[ts_df["ifie_oci"] != 0].copy()
    if not oci_df.empty:
        oci_df = oci_df.sort_values(["cohort_id", "period"])
        oci_df["oci_cumulative"] = oci_df.groupby("cohort_id")["ifie_oci"].cumsum()

        # Portfolio-level cumulative OCI
        port_oci = ts_df.groupby("period")["ifie_oci"].sum().reset_index()
        port_oci["oci_cumulative"] = port_oci["ifie_oci"].cumsum()

        col_oci1, col_oci2 = st.columns([2, 1])

        with col_oci1:
            fig_oci = go.Figure()
            # Portfolio total
            fig_oci.add_scatter(
                x=port_oci["period"], y=port_oci["oci_cumulative"],
                name="Portfolio Total", mode="lines+markers+text",
                line=dict(color="#1d4ed8", width=3),
                marker=dict(size=9),
                text=[f"{v:,.0f}" for v in port_oci["oci_cumulative"]],
                textposition="top center", textfont=dict(size=9),
            )
            # Per-cohort lines
            for cid in oci_df["cohort_id"].unique():
                d = oci_df[oci_df["cohort_id"] == cid]
                fig_oci.add_scatter(
                    x=d["period"], y=d["oci_cumulative"],
                    name=cid, mode="lines+markers",
                    line=dict(width=1.5, dash="dot"),
                    marker=dict(size=5),
                )
            fig_oci.add_hline(y=0, line_dash="dash", line_color="#94a3b8",
                              annotation_text="Zero line")
            fig_oci.update_layout(
                title="Accumulated OCI Reserve by Quarter ('000 HKD)<br>"
                      "<sup>Negative = equity erosion from rising rates</sup>",
                height=380, margin=dict(l=40, r=40, t=70, b=40),
                xaxis_title="Period", yaxis_title="Cumulative OCI ('000 HKD)",
            )
            st.plotly_chart(fig_oci, use_container_width=True)

        with col_oci2:
            # OCI reserve summary table
            oci_summary = []
            for cid in oci_df["cohort_id"].unique():
                d = oci_df[oci_df["cohort_id"] == cid]
                cumulative_q4 = d.iloc[-1]["oci_cumulative"] if not d.empty else 0
                quarterly_avg = d["ifie_oci"].mean()
                oci_summary.append({
                    "Cohort": cid,
                    "Q4 Reserve": round(cumulative_q4, 1),
                    "Avg / Qtr": round(quarterly_avg, 1),
                })
            oci_sumdf = pd.DataFrame(oci_summary).sort_values("Q4 Reserve")
            oci_port_total = port_oci["oci_cumulative"].iloc[-1] if not port_oci.empty else 0
            oci_sumdf.loc[len(oci_sumdf)] = {
                "Cohort": "TOTAL",
                "Q4 Reserve": round(oci_port_total, 1),
                "Avg / Qtr": round(port_oci["ifie_oci"].mean(), 1),
            }

            def _oci_style(row):
                if row["Cohort"] == "TOTAL":
                    return ["font-weight:700; background-color:#f0f4ff"] * len(row)
                if isinstance(row["Q4 Reserve"], float) and row["Q4 Reserve"] < 0:
                    return ["color:#dc2626"] * len(row)
                return [""] * len(row)

            st.dataframe(
                oci_sumdf.style.apply(_oci_style, axis=1)
                    .format({"Q4 Reserve": "{:,.1f}", "Avg / Qtr": "{:,.1f}"}),
                use_container_width=True, hide_index=True, height=280,
            )
            st.caption("""
**OCI Recycling:** When a GMM contract expires, the entire remaining OCI Reserve
recycles into P&L as a final IFIE adjustment. For a 10-year GMM cohort,
a reserve of −200 today would recycle +20/year to P&L income over the remaining life.
""")

        st.info("""
**Why GMM only?** PAA contracts typically have < 1-year duration — the DAIR ≈ current rate,
so there is negligible OCI effect. VFA contracts use the underlying items mechanism
to absorb most finance effects through CSM, so OCI is generally not applied.
""")

    # ── Chart 6: CSM Run-off Projection ──────────────────────────────────
    st.divider()
    st.subheader("📐 CSM Run-off Projection — Glide Path to Zero")
    st.markdown("""
Projects how the **CSM for each cohort will amortise** over future periods,
based on the **historical quarterly amortisation rate** observed in 2024.  
This shows the expected **future Insurance Revenue** locked in today's CSM.

For **VFA cohorts**, two additional scenarios illustrate how market movements
can extend or shorten the CSM life:
- 🟢 **Bull**: Underlying items grow +0.5%/quarter → CSM receives inflows, runs longer
- 🔴 **Bear**: Underlying items shrink −0.5%/quarter → CSM depletes faster
""")

    _runoff_data = project_csm_runoff(all_results, n_quarters=60)

    if _runoff_data:
        col_ro1, col_ro2 = st.columns([3, 1])

        with col_ro1:
            fig_ro = go.Figure()
            _ro_colors = {
                "GMM": ["#3b82f6", "#60a5fa", "#93c5fd", "#bfdbfe"],
                "VFA": ["#8b5cf6", "#a78bfa"],
                "PAA": ["#22c55e"],
            }
            _model_color_idx = {"GMM": 0, "VFA": 0, "PAA": 0}

            for cohort_info in _runoff_data:
                cid   = cohort_info["cohort_id"]
                model = cohort_info["model"]
                proj  = cohort_info["projections"]
                if not proj:
                    continue

                color_list = _ro_colors.get(model, ["#64748b"])
                idx = _model_color_idx[model]
                color = color_list[min(idx, len(color_list) - 1)]
                _model_color_idx[model] += 1

                x_vals = ["2024Q4"] + [p["label"] for p in proj]
                y_vals = [cohort_info["closing_csm"]] + [p["csm"] for p in proj]

                fig_ro.add_scatter(
                    x=x_vals, y=y_vals,
                    name=f"{cid} ({model})", mode="lines",
                    line=dict(color=color, width=2.5),
                    hovertemplate=f"{cid}<br>%{{x}}: CSM = %{{y:,.0f}}<extra></extra>",
                )

                # VFA: add bull/bear fan
                if model == "VFA" and cohort_info["bull_proj"] and cohort_info["bear_proj"]:
                    x_fan = ["2024Q4"] + [p["label"] for p in cohort_info["bull_proj"]]
                    y_bull = [cohort_info["closing_csm"]] + [p["csm"] for p in cohort_info["bull_proj"]]
                    y_bear = [cohort_info["closing_csm"]] + [p["csm"] for p in cohort_info["bear_proj"]]

                    fig_ro.add_scatter(
                        x=x_fan, y=y_bull,
                        name=f"{cid} Bull 🟢", mode="lines",
                        line=dict(color=color, width=1, dash="dash"),
                        showlegend=True,
                    )
                    fig_ro.add_scatter(
                        x=x_fan, y=y_bear,
                        name=f"{cid} Bear 🔴", mode="lines",
                        line=dict(color="#ef4444", width=1, dash="dot"),
                        fill="tonexty", fillcolor="rgba(239,68,68,0.06)",
                        showlegend=True,
                    )

            # Shade the projection region
            fig_ro.add_vrect(
                x0="2025Q1", x1=_runoff_data[0]["projections"][-1]["label"]
                    if _runoff_data[0]["projections"] else "2039Q4",
                fillcolor="rgba(241,245,249,0.4)", layer="below",
                annotation_text="Projected →", annotation_position="top left",
            )
            fig_ro.update_layout(
                title="CSM Glide Path by Cohort — Base Case + VFA Bull/Bear ('000 HKD)",
                height=440, margin=dict(l=40, r=40, t=60, b=40),
                xaxis_title="Quarter", yaxis_title="CSM Balance ('000 HKD)",
                legend=dict(orientation="v", x=1.01, y=1),
            )
            # Only show every 4th tick to avoid crowding
            fig_ro.update_xaxes(
                tickmode="array",
                tickvals=[p["label"] for coh in _runoff_data
                          for i, p in enumerate(coh["projections"]) if i % 4 == 0][:20],
                tickangle=-45,
            )
            st.plotly_chart(fig_ro, use_container_width=True)

        with col_ro2:
            st.caption("**Cohort Summary**")
            _ro_table = []
            for coh in _runoff_data:
                proj = coh["projections"]
                _exhaustion = proj[-1]["label"] if proj and proj[-1]["csm"] < 1 else ">"
                _total_future_isr = round(sum(p["isr_from_csm"] for p in proj), 0)
                _ro_table.append({
                    "Cohort":         coh["cohort_id"],
                    "Model":          coh["model"],
                    "Closing CSM":    round(coh["closing_csm"], 0),
                    "Annual Rate":    f"{coh['annual_rate']:.1%}",
                    "Future ISR*":    _total_future_isr,
                    "Exhausted":      _exhaustion,
                })
            ro_df = pd.DataFrame(_ro_table)
            st.dataframe(
                ro_df.style.format({
                    "Closing CSM": "{:,.0f}",
                    "Future ISR*": "{:,.0f}",
                }).highlight_max(subset=["Closing CSM"], color="#dbeafe"),
                use_container_width=True, hide_index=True,
            )
            st.caption("""
*Future ISR = projected cumulative Insurance Revenue from CSM amortisation only.
Excludes expected CF release, RA release, and experience items.

**Annual Rate** = CSM depleted per year under base case assumptions.
""")

        st.info("""
**Key Insights:**
- **VFA** cohorts have much lower annual amortisation rates (12–14%) vs **GMM** (15–37%),
  reflecting longer duration participating business. But VFA CSM can swing dramatically
  with underlying asset performance.
- **MED_ONR_RECOVERY** shows the youngest CSM (just born in Q4 2024 from LC recovery),
  hence the lowest amortisation rate — this contract has just turned profitable.
- The CSM Glide Path tells management *how much future profit* is locked into the book today.
""")

    # ── Chart 8: Onerous Contract Full Lifecycle ──────────────────────────
    st.divider()
    st.subheader("☠️ → 🟢  Onerous Contract Lifecycle — MED_ONR_RECOVERY")
    st.markdown("""
This cohort demonstrates the **complete IFRS 17 onerous contract journey**
— from Day-1 loss recognition to full recovery with CSM formation:
""")

    onr_ts = ts_df[ts_df["cohort_id"] == "MED_ONR_RECOVERY"].copy()
    if not onr_ts.empty:
        # Add opening period if present
        opening_label = "Opening\n(2023Q4)"
        onr_ts = onr_ts.sort_values("period")

        # ── Phase annotation ──────────────────────────────────────────────
        phase_map = {
            "2024Q1": "Q1 — Deterioration<br>(extra LC recognised)",
            "2024Q2": "Q2 — Stabilisation<br>(LC starts releasing)",
            "2024Q3": "Q3 — Major Improvement<br>(LC collapses: 730→115)",
            "2024Q4": "Q4 — Full Recovery<br>(LC=0, CSM=160 born 🎉)",
        }

        col_lc_csm, col_icl = st.columns([3, 2])

        with col_lc_csm:
            fig_onr = go.Figure()
            fig_onr.add_scatter(
                x=onr_ts["period"], y=onr_ts["eom_lc"],
                name="LC (Loss Component)", mode="lines+markers",
                marker=dict(size=10, color="#ef4444"),
                line=dict(width=3, color="#ef4444"),
                fill="tozeroy", fillcolor="rgba(239,68,68,0.15)",
            )
            fig_onr.add_scatter(
                x=onr_ts["period"], y=onr_ts["eom_csm"],
                name="CSM (born in Q4)", mode="lines+markers",
                marker=dict(size=10, color="#22c55e", symbol="star"),
                line=dict(width=3, color="#22c55e"),
                fill="tozeroy", fillcolor="rgba(34,197,94,0.15)",
            )
            # Annotation for the recovery crossover
            fig_onr.add_vline(x="2024Q4", line_dash="dash", line_color="#22c55e", line_width=2)
            fig_onr.add_annotation(
                x="2024Q4", y=max(onr_ts["eom_lc"].max(), 50) * 0.7,
                text="<b>LC = 0<br>CSM = 160<br>✅ Profitable!</b>",
                showarrow=True, arrowhead=2, arrowcolor="#22c55e",
                font=dict(color="#166534", size=12),
                bgcolor="#dcfce7", bordercolor="#22c55e",
            )
            fig_onr.update_layout(
                title="LC vs CSM — Onerous Contract Lifecycle ('000 HKD)",
                height=400, margin=dict(l=40, r=40, t=60, b=40),
                xaxis_title="Period", yaxis_title="Balance ('000 HKD)",
            )
            st.plotly_chart(fig_onr, use_container_width=True)

        with col_icl:
            # Summary table with phase labels
            summary_rows = []
            for _, row in onr_ts.iterrows():
                p = row["period"]
                summary_rows.append({
                    "Period": p,
                    "Phase": phase_map.get(p, p),
                    "LC": int(row["eom_lc"]),
                    "CSM": int(row["eom_csm"]),
                    "ICL": int(row["eom_pvfcf"] + row["eom_ra"] + row["eom_csm"] - row["eom_lc"]),
                })
            onr_summary = pd.DataFrame(summary_rows)
            st.dataframe(
                onr_summary.style
                    .format({"LC": "{:,.0f}", "CSM": "{:,.0f}", "ICL": "{:,.0f}"})
                    .map(lambda v: "color:#ef4444;font-weight:700" if isinstance(v, int) and v > 0 else "", subset=["LC"])
                    .map(lambda v: "color:#16a34a;font-weight:700" if isinstance(v, int) and v > 0 else "", subset=["CSM"]),
                use_container_width=True,
                height=220,
            )

            # P&L waterfall for this cohort
            st.caption("**P&L impact by quarter:**")
            onr_pl = ts_df[ts_df["cohort_id"] == "MED_ONR_RECOVERY"][
                ["period", "insurance_revenue", "ifie_pl"]
            ].copy()
            onr_pl["ISR (Revenue)"] = onr_pl["insurance_revenue"]
            onr_pl["IFIE"] = onr_pl["ifie_pl"]
            fig_pl_onr = go.Figure()
            fig_pl_onr.add_bar(x=onr_pl["period"], y=onr_pl["ISR (Revenue)"],
                               name="ISR", marker_color="#22c55e")
            fig_pl_onr.add_bar(x=onr_pl["period"], y=onr_pl["IFIE"],
                               name="IFIE", marker_color="#f59e0b")
            fig_pl_onr.update_layout(
                barmode="stack", title="P&L — by quarter",
                height=250, margin=dict(l=20, r=20, t=40, b=30),
                showlegend=True,
            )
            st.plotly_chart(fig_pl_onr, use_container_width=True)

        # Key IFRS 17 notes
        st.info("""
**IFRS 17 Mechanics highlighted in this lifecycle:**
- **Q1**: `lc_reversal > 0` → Dr ISE / Cr ICL-LC → LC rises (contract gets MORE onerous)
- **Q2–Q3**: `lc_reversal < 0` → Dr ICL-LC / Cr ISR → LC releases as service is delivered  
- **Q3**: Large assumption improvement → massive LC release (Dr ICL-LC / Cr ISR)
- **Q4**: Final LC cleared + `assumption_chg_csm = 160` → Dr ICL-PVFCF / Cr ICL-CSM → **CSM born!**  
  The contract has crossed from onerous to profitable. Future CSM of 160 will amortise as Insurance Revenue.
""")
    else:
        st.info("MED_ONR_RECOVERY cohort not found in loaded data.")

    # ══════════════════════════════════════════════════════════════════════
    # CSM Run-off Projection
    # ══════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📐 CSM Run-off Projection — Glide Path")
    st.markdown("""
Projects the **CSM amortisation trajectory** for each cohort, based on the historical
quarterly amortisation rate observed in 2024.  This is the IFRS 17 equivalent of
"how many years of profit are still locked in the book?"
""")

    # ── Compute historical amortisation rates from all_results ────────────
    _fy_periods_ts = sorted(k for k in all_results if k.startswith("2024"))

    # Default quarterly amort rates (used when < 1 quarter of history, e.g. newly-recovered)
    _DEFAULT_AMORT_RATES = {"GMM": 0.12, "VFA": 0.035, "PAA": 0.25}

    # Per cohort: collect (bom_csm, csm_amortisation) pairs from each quarter
    _cohort_amort_info = {}   # cohort_id → {product, model, closing_csm, avg_rate}
    for p in _fy_periods_ts:
        for r in all_results[p]:
            info = _cohort_amort_info.setdefault(r.cohort_id, {
                "product": r.product,
                "model":   r.measurement_model,
                "closing_csm": 0.0,
                "rates": [],
            })
            # Always update closing CSM
            if r.eom_csm > 0:
                info["closing_csm"] = r.eom_csm
            # Rate only calculable when BOM > 0
            if r.bom_csm > 0:
                rate = -r.csm_amortisation / r.bom_csm   # positive fraction
                info["rates"].append(rate)

    # Fallback: cohorts with closing CSM but no history (e.g. newly-recovered Q4)
    for cid, info in _cohort_amort_info.items():
        if info["closing_csm"] > 0 and not info["rates"]:
            info["rates"] = [_DEFAULT_AMORT_RATES.get(info["model"], 0.08)]
            info["_default_rate"] = True

    # ── Project 12 quarters (3 years) forward ────────────────────────────
    _N_PROJ = 12    # quarters to project
    _PROJ_PERIODS = [f"Q{i+1}" for i in range(_N_PROJ)]

    _runoff_rows = []       # for table
    _fig_runoff = go.Figure()

    # Color palette
    _model_pal = {"GMM": "#3b82f6", "VFA": "#8b5cf6", "PAA": "#22c55e"}

    for cid, info in sorted(_cohort_amort_info.items()):
        if info["closing_csm"] <= 0:
            continue
        avg_rate = (sum(info["rates"]) / len(info["rates"])) if info["rates"] else 0.05

        # Three scenarios: base, fast (+25%), slow (-25%)
        csm_base = info["closing_csm"]
        proj_base, proj_fast, proj_slow = [], [], []
        isr_base = []
        csm_b, csm_f, csm_s = csm_base, csm_base, csm_base

        for _ in range(_N_PROJ):
            isr_base.append(round(csm_b * avg_rate, 1))
            proj_base.append(round(csm_b, 1))
            proj_fast.append(round(csm_f, 1))
            proj_slow.append(round(csm_s, 1))
            csm_b = max(0, csm_b * (1 - avg_rate))
            csm_f = max(0, csm_f * (1 - avg_rate * 1.25))
            csm_s = max(0, csm_s * (1 - avg_rate * 0.75))

        color = _model_pal.get(info["model"], "#64748b")

        # Base line (solid)
        _fig_runoff.add_scatter(
            x=_PROJ_PERIODS, y=proj_base,
            name=f"{cid} (base)",
            mode="lines+markers",
            line=dict(width=2.5, color=color),
            marker=dict(size=6),
        )
        # Scenario band (filled area between fast and slow)
        _r = int(color[1:3], 16); _g = int(color[3:5], 16); _b = int(color[5:7], 16)
        _fill_rgba = f"rgba({_r},{_g},{_b},0.10)"
        _fig_runoff.add_scatter(
            x=_PROJ_PERIODS + _PROJ_PERIODS[::-1],
            y=proj_fast + proj_slow[::-1],
            fill="toself",
            fillcolor=_fill_rgba,
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
            name=f"{cid} range",
        )

        # Table row
        _runoff_rows.append({
            "Cohort":          cid,
            "Model":           info["model"],
            "Closing CSM":     round(info["closing_csm"], 0),
            "Qtrly Amort Rate":f"{avg_rate*100:.1f}%",
            "Quarters to Zero":int(round(-1 / (avg_rate + 1e-9) * 0.693 / 0.25))
                               if avg_rate > 0.005 else 99,
            "Proj ISR Q1":     round(info["closing_csm"] * avg_rate, 0),
            "Proj ISR Q4":     round(isr_base[3], 0) if len(isr_base) > 3 else 0,
            "Proj ISR Q8":     round(isr_base[7], 0) if len(isr_base) > 7 else 0,
            "Proj CSM Yr3":    round(proj_base[-1], 0),
        })

    _fig_runoff.update_layout(
        title="CSM Glide Path — Base (solid) with ±25% Amortisation Speed Range ('000 HKD)",
        height=430, margin=dict(l=40, r=40, t=60, b=40),
        xaxis_title="Projected Quarter (from 31 Dec 2024)",
        yaxis_title="CSM Balance ('000 HKD)",
        legend=dict(orientation="v", x=1.01, y=1),
    )

    if _runoff_rows:
        _col_proj_chart, _col_proj_table = st.columns([3, 2])
        with _col_proj_chart:
            st.plotly_chart(_fig_runoff, use_container_width=True)
        with _col_proj_table:
            _runoff_df = pd.DataFrame(_runoff_rows)
            st.caption("**CSM Run-off Summary** — projected from 31 Dec 2024")
            st.dataframe(
                _runoff_df.style
                    .format({"Closing CSM": "{:,.0f}",
                             "Proj ISR Q1": "{:,.0f}",
                             "Proj ISR Q4": "{:,.0f}",
                             "Proj ISR Q8": "{:,.0f}",
                             "Proj CSM Yr3": "{:,.0f}"})
                    .bar(subset=["Closing CSM"], color="#bfdbfe", vmin=0)
                    .highlight_min(subset=["Quarters to Zero"], color="#fef3c7"),
                use_container_width=True, height=330,
            )

        st.info("""
**How to read this chart:**
- **Solid line** = base case projection using 2024 average quarterly amortisation rate
- **Shaded band** = range between 25%-faster (upper bound) and 25%-slower (lower bound) amortisation
- **"Quarters to Zero"** ≈ the half-life estimate of the CSM (ln(2) / quarterly rate × 4 qtrs / yr)
- The steeper the decline, the faster profits are recognised as Insurance Revenue
- VFA CSMs are more volatile (market-linked); GMM CSMs decline steadily

*Note: This is a simplified run-off assuming constant coverage unit pattern.  
In practice, actuaries use coverage unit schedules (e.g., in-force policies, sums insured) per IFRS 17.B119.*
""")
    else:
        st.info("No profitable cohorts with CSM > 0 found.")

    # ══════════════════════════════════════════════════════════════════════
    # OCI Reserve Accumulation
    # ══════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📊 OCI Reserve — Cumulative Accumulation")
    st.markdown("""
Under the **OCI option** (applied to GMM cohorts), the difference between the
current discount rate and the locked-in DAIR flows through **Other Comprehensive Income**
rather than P&L.  This builds up an **OCI Reserve** in shareholders' equity over time
— which reverses to P&L as contracts mature.
""")

    # ── Build OCI accumulation from all_results ───────────────────────────
    _all_periods_sorted = sorted(all_results.keys())

    # Per cohort: running OCI reserve
    _oci_by_cohort = {}    # cohort_id → {model, period_label, cumulative_oci}
    _oci_portfolio = {}    # period → portfolio cumulative OCI

    _running_cohort_oci = {}  # cohort_id → running total
    _running_port_oci = 0.0

    for p in _all_periods_sorted:
        _period_port_oci = 0.0
        for r in all_results[p]:
            cid = r.cohort_id
            _running_cohort_oci[cid] = _running_cohort_oci.get(cid, 0.0) + r.finance_charge_oci

            if cid not in _oci_by_cohort:
                _oci_by_cohort[cid] = {"model": r.measurement_model, "periods": [], "oci_cum": [], "oci_qtr": []}
            _oci_by_cohort[cid]["periods"].append(p)
            _oci_by_cohort[cid]["oci_cum"].append(round(_running_cohort_oci[cid], 2))
            _oci_by_cohort[cid]["oci_qtr"].append(round(r.finance_charge_oci, 2))
            _period_port_oci += r.finance_charge_oci

        _running_port_oci += _period_port_oci
        _oci_portfolio[p] = round(_running_port_oci, 2)

    _oci_periods = sorted(_oci_portfolio.keys())

    # ── Plot cumulative OCI by cohort + portfolio total ───────────────────
    _col_oci_l, _col_oci_r = st.columns([3, 2])

    with _col_oci_l:
        fig_oci = go.Figure()

        # Cohort lines (only GMM ones with OCI)
        for cid, data in _oci_by_cohort.items():
            if data["model"] != "GMM":
                continue
            if all(v == 0 for v in data["oci_cum"]):
                continue
            fig_oci.add_scatter(
                x=data["periods"], y=data["oci_cum"],
                name=f"{cid}",
                mode="lines+markers",
                line=dict(width=1.5, dash="dot"),
                marker=dict(size=5),
            )

        # Portfolio total (bold)
        fig_oci.add_scatter(
            x=_oci_periods,
            y=[_oci_portfolio[p] for p in _oci_periods],
            name="Portfolio Total",
            mode="lines+markers",
            line=dict(width=3.5, color="#1d4ed8"),
            marker=dict(size=9, symbol="diamond"),
            fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
        )

        fig_oci.add_hline(y=0, line_dash="solid", line_color="#94a3b8", line_width=1)
        fig_oci.update_layout(
            title="Cumulative OCI Reserve — Building in Shareholders' Equity ('000 HKD)",
            height=380, margin=dict(l=40, r=40, t=60, b=40),
            xaxis_title="Period", yaxis_title="Cumulative OCI Reserve ('000 HKD)",
            legend=dict(orientation="v", x=1.01, y=1),
        )
        st.plotly_chart(fig_oci, use_container_width=True)

        # Quarterly OCI waterfall (portfolio)
        with st.expander("📊 Quarterly OCI Flow — Portfolio"):
            _port_qtr_oci = []
            for p in _oci_periods:
                _q_total = sum(
                    data["oci_qtr"][data["periods"].index(p)]
                    for data in _oci_by_cohort.values()
                    if p in data["periods"]
                )
                _port_qtr_oci.append(_q_total)

            fig_qtr_oci = go.Figure()
            fig_qtr_oci.add_bar(
                x=_oci_periods, y=_port_qtr_oci,
                marker_color=["#22c55e" if v < 0 else "#ef4444" for v in _port_qtr_oci],
                text=[f"{v:+,.1f}" for v in _port_qtr_oci],
                textposition="outside",
            )
            fig_qtr_oci.add_hline(y=0, line_color="#94a3b8")
            fig_qtr_oci.update_layout(
                title="Quarterly OCI Flow — Positive = OCI Expense, Negative = OCI Income",
                height=280, margin=dict(l=20, r=20, t=50, b=30),
                showlegend=False,
            )
            st.plotly_chart(fig_qtr_oci, use_container_width=True)

    with _col_oci_r:
        # OCI Reserve summary table
        st.caption("**Closing OCI Reserve by Cohort** (31 Dec 2024)")
        _oci_summary_rows = []
        for cid, data in sorted(_oci_by_cohort.items()):
            if not data["oci_cum"]:
                continue
            _total = data["oci_cum"][-1]
            if abs(_total) < 0.01:
                continue
            _oci_summary_rows.append({
                "Cohort":         cid,
                "Model":          data["model"],
                "OCI Reserve":    round(_total, 1),
                "Equity Impact":  "↑ Equity" if _total < 0 else "↓ Equity",
            })
        _oci_summary_rows.append({
            "Cohort":         "TOTAL",
            "Model":          "",
            "OCI Reserve":    round(_oci_portfolio.get(_oci_periods[-1], 0), 1) if _oci_periods else 0,
            "Equity Impact":  "",
        })
        _oci_summary_df = pd.DataFrame(_oci_summary_rows)

        def _oci_style(row):
            if row["Cohort"] == "TOTAL":
                return ["font-weight:700; background-color:#f0f4ff"] * len(row)
            return [""] * len(row)

        st.dataframe(
            _oci_summary_df.style.apply(_oci_style, axis=1)
                .format({"OCI Reserve": "{:+,.1f}"})
                .map(lambda v: "color:#16a34a" if isinstance(v, float) and v < 0 else
                               ("color:#dc2626" if isinstance(v, float) and v > 0 else ""),
                     subset=["OCI Reserve"]),
            use_container_width=True, height=320,
        )

        # ── Rate shock impact calculator ──────────────────────────────────
        st.markdown("---")
        st.caption("**What-if: Rate Shock on OCI Reserve**")
        _rate_shock_oci = st.slider(
            "Parallel rate shift (bps)",
            min_value=-300, max_value=300, value=0, step=25,
            key="oci_rate_shock",
            help="Estimate OCI impact of a one-off parallel yield curve shift",
        )

        if _rate_shock_oci != 0:
            # OCI impact ≈ -Duration × PVFCF × Δrate  (for GMM cohorts only)
            _DURATION_OCI = {"GMM": 8.0, "VFA": 0.0, "PAA": 0.0}
            _oci_shock_total = 0.0
            _oci_shock_rows = []
            for r in _last_aoc:
                dur = _DURATION_OCI.get(r.measurement_model, 0.0)
                delta = -(dur * r.eom_pvfcf * _rate_shock_oci / 10000)
                if abs(delta) > 0.1:
                    _oci_shock_rows.append({"Cohort": r.cohort_id, "ΔOCI": round(delta, 0)})
                    _oci_shock_total += delta

            _sign = "+" if _oci_shock_total > 0 else ""
            if _oci_shock_total > 0:
                st.error(f"OCI Reserve change: **{_sign}{_oci_shock_total:,.0f}** '000 HKD  \n"
                         f"→ Shareholders' equity **decreases** (OCI expense)")
            else:
                st.success(f"OCI Reserve change: **{_sign}{_oci_shock_total:,.0f}** '000 HKD  \n"
                           f"→ Shareholders' equity **increases** (OCI income)")
            if _oci_shock_rows:
                st.dataframe(pd.DataFrame(_oci_shock_rows).style.format({"ΔOCI": "{:+,.0f}"}),
                             use_container_width=True, height=200, hide_index=True)
        else:
            st.caption("Move the slider to estimate the OCI impact of a rate shock.")

    st.info("""
**IFRS 17 OCI Reserve mechanics:**
- **Negative** reserve (cumulative OCI income) → builds equity → typical in a **falling rate** environment
  (current rate < DAIR → ICL increases → but routed to OCI not P&L)
- **Positive** reserve (cumulative OCI expense) → reduces equity → typical in a **rising rate** environment
- The OCI reserve is recycled to P&L gradually as the underlying contracts mature/expire
- Under PAA and VFA (non-OCI option), this mechanism doesn't apply — IFIE goes fully to P&L
- **Practical implication:** A rising-rate environment silently erodes insurance company equity
  (visible in OCI, not P&L) — a key risk for IFRS 17 preparers
""")

    # ── Raw time series table ─────────────────────────────────────────────
    with st.expander("View raw time series data"):
        st.dataframe(
            ts_df.style.format({c: "{:,.2f}" for c in ts_df.select_dtypes("number").columns}),
            use_container_width=True, height=400,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# Tab 8 — Sensitivity Analysis (H)
# ══════════════════════════════════════════════════════════════════════════════

with tab_sens:
    st.markdown("## 🎯 Sensitivity Analysis")
    st.markdown("""
Adjust the shock parameters below to see how key IFRS 17 metrics respond.
All impacts are **approximate parametric estimates** — the same approach used in
actuarial sensitivity testing before full re-projection.
""")

    # ── Duration assumptions per model ───────────────────────────────────
    _DURATION = {"GMM": 8.0, "VFA": 12.0, "PAA": 0.5}
    # Expense proportion of PVFCF (approximate)
    _EXPENSE_PCT = 0.18

    # ── Sliders ──────────────────────────────────────────────────────────
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        rate_shock = st.slider(
            "📐 Interest rate shock (bps)",
            min_value=-300, max_value=300, value=0, step=25,
            help="Parallel shift in discount curve.\n"
                 "ΔPVFCF = −Duration × PVFCF × Δrate\n"
                 "Profitable GMM/VFA: absorbed by CSM (no P&L)\n"
                 "Onerous / PAA: flows to P&L",
        )
    with sc2:
        mortality_shock = st.slider(
            "💀 Mortality / morbidity shock (%)",
            min_value=-30, max_value=30, value=0, step=5,
            help="Percentage change in expected claims.\n"
                 "Profitable GMM/VFA: absorbed by CSM\n"
                 "Onerous / PAA: flows to P&L",
        )
    with sc3:
        expense_shock = st.slider(
            "💼 Expense shock (%)",
            min_value=-20, max_value=20, value=0, step=5,
            help="Percentage change in maintenance expenses.\n"
                 "ΔPVFCF ≈ expense_proportion × PVFCF × Δexpense\n"
                 "Same routing as mortality shock.",
        )

    # ── Compute impacts ──────────────────────────────────────────────────
    def _sensitivity_rows(aoc_list_in, rate_bps, mort_pct, exp_pct):
        rows = []
        for a in aoc_list_in:
            model = a.measurement_model
            dur   = _DURATION.get(model, 8.0)

            # Rate shock → ΔPVFCF
            d_pvfcf_rate  = -dur * a.eom_pvfcf * (rate_bps / 10000)

            # Mortality shock → approximate ΔPVFCF
            # Expected annual claims ≈ exp_cf_release × 4 (quarterly → annual)
            # Remaining coverage ≈ |CSM / csm_amortisation| periods
            annual_claims = abs(a.expected_cf_release) * 4
            if abs(a.csm_amortisation) > 1e-3:
                remaining_periods = min(abs(a.eom_csm / a.csm_amortisation), 40)
            else:
                remaining_periods = 8
            d_pvfcf_mort = annual_claims * (mort_pct / 100) * remaining_periods * 0.25

            # Expense shock → approximate ΔPVFCF
            d_pvfcf_exp = _EXPENSE_PCT * abs(a.eom_pvfcf) * (exp_pct / 100)

            d_pvfcf_total = d_pvfcf_rate + d_pvfcf_mort + d_pvfcf_exp

            # Route: CSM absorbs for profitable GMM/VFA; P&L for onerous/PAA
            is_profitable_bba = (a.eom_csm > 0 and model in ("GMM", "VFA"))
            if is_profitable_bba:
                d_csm = -d_pvfcf_total   # CSM absorbs (negative = CSM decreases when PVFCF rises)
                d_icl =  0.0             # Net ICL unchanged (PVFCF up, CSM down equally)
                d_pl  =  0.0
            else:
                d_csm =  0.0
                d_icl =  d_pvfcf_total
                d_pl  =  d_pvfcf_total   # Goes straight to P&L / LC

            rows.append({
                "cohort_id":       a.cohort_id,
                "model":           model,
                "product":         a.product,
                "eom_icl":         round(a.eom_icl, 0),
                "eom_csm":         round(a.eom_csm, 0),
                "Δ PVFCF (rate)":  round(d_pvfcf_rate, 0),
                "Δ PVFCF (mort)":  round(d_pvfcf_mort, 0),
                "Δ PVFCF (exp)":   round(d_pvfcf_exp,  0),
                "Δ ICL":           round(d_icl, 0),
                "Δ CSM":           round(d_csm, 0),
                "Δ P&L":           round(d_pl, 0),
            })
        return pd.DataFrame(rows)

    sens_df = _sensitivity_rows(aoc_list, rate_shock, mortality_shock, expense_shock)

    if sens_df.empty:
        st.info("Run the subledger first to enable sensitivity analysis.")
    else:
        total_d_icl = sens_df["Δ ICL"].sum()
        total_d_csm = sens_df["Δ CSM"].sum()
        total_d_pl  = sens_df["Δ P&L"].sum()

        # ── KPI delta cards ──────────────────────────────────────────────
        sk1, sk2, sk3 = st.columns(3)
        sk1.metric("Δ Net ICL",  f"{total_d_icl:+,.0f}", help="Change in total Insurance Contract Liability")
        sk2.metric("Δ CSM",      f"{total_d_csm:+,.0f}", help="Change in Contractual Service Margin (profitable)")
        sk3.metric("Δ P&L",      f"{total_d_pl:+,.0f}",  help="Change in Net P&L (onerous + PAA)")

        # ── Tornado chart ────────────────────────────────────────────────
        st.markdown("#### 🌪️ Tornado Chart — Portfolio Impact")
        st.caption("Shows approximate impact of each shock component on each metric (current slider values).")

        tornado_data = {
            "Shock": ["Rate shock", "Mortality shock", "Expense shock"] * 3,
            "Metric": (["Δ ICL"] * 3) + (["Δ CSM"] * 3) + (["Δ P&L"] * 3),
            "Impact": [
                sens_df["Δ PVFCF (rate)"].sum() if total_d_icl != 0 else 0,
                sens_df["Δ PVFCF (mort)"].sum() if total_d_icl != 0 else 0,
                sens_df["Δ PVFCF (exp)"].sum()  if total_d_icl != 0 else 0,
                -sens_df["Δ PVFCF (rate)"][sens_df["Δ CSM"] != 0].sum(),
                -sens_df["Δ PVFCF (mort)"][sens_df["Δ CSM"] != 0].sum(),
                -sens_df["Δ PVFCF (exp)"][sens_df["Δ CSM"] != 0].sum(),
                sens_df["Δ PVFCF (rate)"][sens_df["Δ P&L"] != 0].sum(),
                sens_df["Δ PVFCF (mort)"][sens_df["Δ P&L"] != 0].sum(),
                sens_df["Δ PVFCF (exp)"][sens_df["Δ P&L"] != 0].sum(),
            ],
        }
        t_df = pd.DataFrame(tornado_data)
        t_df = t_df[t_df["Impact"].abs() > 0.1]

        if not t_df.empty:
            fig_tornado = px.bar(
                t_df, x="Impact", y="Shock", color="Metric",
                orientation="h", barmode="group",
                color_discrete_map={"Δ ICL": "#3b82f6", "Δ CSM": "#f59e0b", "Δ P&L": "#ef4444"},
                title=f"Sensitivity Impact — {sel_period}",
            )
            fig_tornado.add_vline(x=0, line_dash="dash", line_color="gray")
            fig_tornado.update_layout(height=360, margin=dict(l=40, r=40, t=60, b=40))
            st.plotly_chart(fig_tornado, use_container_width=True)
        else:
            st.info("Move the sliders to see the tornado chart.")

        # ── Standard scenario comparison ─────────────────────────────────
        st.markdown("#### 📊 Standard Scenarios — Portfolio Summary")
        st.caption("Pre-computed sensitivities for standard shocks (independent, single-factor).")

        scenarios = [
            ("Rate +100bps",   100,  0,  0),
            ("Rate −100bps",  -100,  0,  0),
            ("Rate +200bps",   200,  0,  0),
            ("Rate −200bps",  -200,  0,  0),
            ("Mortality +10%",   0, 10,  0),
            ("Mortality −10%",   0,-10,  0),
            ("Mortality +20%",   0, 20,  0),
            ("Expense +10%",     0,  0, 10),
            ("Expense −10%",     0,  0,-10),
        ]
        scen_rows = []
        for label, r, m, e in scenarios:
            df_s = _sensitivity_rows(aoc_list, r, m, e)
            scen_rows.append({
                "Scenario":  label,
                "Δ ICL":     df_s["Δ ICL"].sum(),
                "Δ CSM":     df_s["Δ CSM"].sum(),
                "Δ P&L":     df_s["Δ P&L"].sum(),
                "ICL % chg": f"{df_s['Δ ICL'].sum() / max(abs(total_icl), 1) * 100:+.2f}%",
                "P&L % chg": f"{df_s['Δ P&L'].sum() / max(abs(total_isr), 1) * 100:+.2f}%",
            })
        scen_df = pd.DataFrame(scen_rows)

        def _color_impact(val):
            if isinstance(val, (int, float)):
                if val > 0: return "color:#dc2626"
                if val < 0: return "color:#16a34a"
            return ""

        st.dataframe(
            scen_df.style
                .format({"Δ ICL": "{:+,.0f}", "Δ CSM": "{:+,.0f}", "Δ P&L": "{:+,.0f}"})
                .map(_color_impact, subset=["Δ ICL", "Δ CSM", "Δ P&L"]),
            use_container_width=True, height=360,
        )

        # ── Per-cohort breakdown ──────────────────────────────────────────
        st.markdown("#### 🔍 Per-Cohort Breakdown (current sliders)")
        num_cols_s = ["eom_icl", "eom_csm", "Δ PVFCF (rate)", "Δ PVFCF (mort)",
                      "Δ PVFCF (exp)", "Δ ICL", "Δ CSM", "Δ P&L"]
        st.dataframe(
            sens_df.style
                .format({c: "{:+,.0f}" for c in ["Δ PVFCF (rate)", "Δ PVFCF (mort)",
                                                   "Δ PVFCF (exp)", "Δ ICL", "Δ CSM", "Δ P&L"]})
                .format({c: "{:,.0f}" for c in ["eom_icl", "eom_csm"]})
                .map(_color_impact, subset=["Δ ICL", "Δ CSM", "Δ P&L"]),
            use_container_width=True,
            height=min(80 + len(sens_df) * 35, 380),
        )

        st.caption("""
**Methodology note**: Impacts are parametric approximations.
Rate sensitivity uses modified duration (GMM 8y · VFA 12y · PAA 0.5y).
Mortality sensitivity uses remaining coverage units derived from CSM/amortisation ratio.
Expense sensitivity uses 18% of PVFCF as the expense proportion.
For profitable GMM/VFA cohorts: ΔPVFCF is fully absorbed by CSM (ΔICL = 0, ΔP&L = 0).
For onerous contracts and PAA: ΔPVFCF flows to ICL and P&L.
""")


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# Tab 9 — Disclosures (J2)
# ══════════════════════════════════════════════════════════════════════════════

with tab_disc:
    st.markdown("## 📋 IFRS 17 Disclosure Notes — Full Year 2024")
    st.markdown("""
These notes follow the **IFRS 17 quantitative disclosure requirements** (paragraphs 97–132).  
All amounts in **HKD '000**. Intra-ICL transfers (CSM/PVFCF reclassifications) are separately identified.
""")

    _YEAR = "2024"

    # ── Note 1: ICL Movement Table ────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Note 1 — Insurance Contract Liabilities: Movement in 2024")
    st.caption("IFRS 17.100A — Rollforward of the ICL balance by AOC line item and measurement model")

    n1 = note1_icl_movement(all_results, _YEAR)
    if not n1.empty:
        def _style_n1(row):
            styles = [""] * len(row)
            label = str(row.name).strip()
            if label.startswith("Opening") or label.startswith("Closing"):
                styles = ["font-weight:700; background-color:#f0f4ff"] * len(row)
            elif label.startswith("Sub-total") or label.startswith("Net Change"):
                styles = ["font-weight:600; background-color:#f8fafc"] * len(row)
            elif label.startswith("───"):
                styles = ["color:#6b7280; font-style:italic"] * len(row)
            return styles

        numeric_cols = [c for c in n1.columns if c in ["GMM", "VFA", "PAA", "Total"]]
        fmt_dict = {c: "{:,.1f}" for c in numeric_cols}

        st.dataframe(
            n1.style.apply(_style_n1, axis=1).format(fmt_dict, na_rep=""),
            use_container_width=True,
            height=550,
        )

        # Visual: Net change by model bar chart
        with st.expander("📊 Visual: Net ICL Change by Model & Movement Category"):
            # Extract key rows for the visual
            svc_rows = {
                "Exp. CF Release": "② Expected CF Release (incl. RA release)",
                "Exp. Variance":   "    ③ Experience Variance",
                "CSM Amortisation":"    ④ CSM Amortisation",
                "LC Reversal":     "    ⑤ LC Reversal / Additional LC",
                "Assumption Δ P&L":"    ⑧ Assumption Changes → P&L",
            }
            fi_rows = {
                "IFIE P&L":  "    ⑥ IFIE — P&L (locked-in DAIR unwind)",
                "IFIE OCI":  "    ⑦ IFIE — OCI (current rate vs DAIR)",
            }
            models_vis = ["GMM", "VFA", "PAA"]
            colors = {"GMM": "#3b82f6", "VFA": "#8b5cf6", "PAA": "#22c55e"}

            fig_mv = go.Figure()
            for row_label, row_key in {**svc_rows, **fi_rows}.items():
                if row_key in n1.index:
                    row_data = n1.loc[row_key]
                    for m in models_vis:
                        val = row_data.get(m, 0)
                        if isinstance(val, str) or val == 0:
                            continue
                        fig_mv.add_bar(
                            name=f"{m}: {row_label}",
                            x=[m], y=[float(val)],
                            marker_color=colors.get(m, "#64748b"),
                            opacity=0.75,
                            legendgroup=m,
                            showlegend=True,
                            text=[row_label],
                        )
            fig_mv.update_layout(
                barmode="relative",
                title="ICL Net Change by Movement Category and Model ('000 HKD)",
                height=420, margin=dict(l=40, r=40, t=60, b=40),
                xaxis_title="Measurement Model",
                yaxis_title="Amount ('000 HKD)",
            )
            st.plotly_chart(fig_mv, use_container_width=True)
    else:
        st.info("Run the subledger to generate disclosure data.")

    # ── Note 2: ICL Balance by Component ─────────────────────────────────
    st.markdown("---")
    st.markdown("### Note 2 — ICL Balance by Component")
    st.caption("IFRS 17 — Opening and closing balances for PVFCF, RA, CSM, and LC by measurement model")

    n2 = note2_icl_components(all_results, _YEAR)
    if not n2.empty:
        def _style_n2(row):
            styles = [""] * len(row)
            comp = str(row.name[-1]) if hasattr(row.name, '__len__') else str(row.name)
            if comp == "Total ICL":
                styles = ["font-weight:700; background-color:#f0f4ff"] * len(row)
            return styles

        st.dataframe(
            n2.style.apply(_style_n2, axis=1)
              .format("{:,.1f}")
              .map(lambda v: "color:#ef4444" if isinstance(v, float) and v < 0 else
                             ("color:#16a34a" if isinstance(v, float) and v > 0 else ""),
                   subset=["Change"]),
            use_container_width=True,
            height=500,
        )

        # Stacked bar: opening vs closing by model
        with st.expander("📊 Visual: Opening vs Closing ICL by Component"):
            n2_reset = n2.reset_index()
            n2_total = n2_reset[n2_reset["Model"] == "TOTAL"]
            comps = ["PVFCF", "RA", "CSM", "LC"]
            fig_comp = go.Figure()
            comp_colors = {"PVFCF": "#3b82f6", "RA": "#f59e0b", "CSM": "#22c55e", "LC": "#ef4444"}
            for comp in comps:
                row_c = n2_total[n2_total["Component"] == comp]
                if row_c.empty:
                    continue
                open_v = float(row_c["Opening"].iloc[0])
                close_v = float(row_c["Closing"].iloc[0])
                # LC is a negative entry in ICL (already negated in Note 2)
                fig_comp.add_bar(name=f"{comp} (Opening)", x=["Opening"],
                                 y=[open_v], marker_color=comp_colors[comp], opacity=0.6)
                fig_comp.add_bar(name=f"{comp} (Closing)", x=["Closing"],
                                 y=[close_v], marker_color=comp_colors[comp])
            fig_comp.update_layout(
                barmode="stack",
                title="Portfolio ICL: Opening vs Closing — by Component ('000 HKD)",
                height=380, margin=dict(l=40, r=40, t=60, b=40),
            )
            st.plotly_chart(fig_comp, use_container_width=True)

    # ── Note 3: Insurance Revenue Analysis ───────────────────────────────
    st.markdown("---")
    st.markdown("### Note 3 — Analysis of Insurance Revenue")
    st.caption("IFRS 17.83 — Components of Insurance Revenue recognised in 2024")

    n3 = note3_insurance_revenue(all_results, _YEAR)
    if not n3.empty:
        def _style_n3(row):
            if row.name == "Total":
                return ["font-weight:700; background-color:#f0f4ff"] * len(row)
            return [""] * len(row)

        isr_cols = [c for c in n3.columns if c != "— Additional LC Recognised (ISE)"]
        st.dataframe(
            n3.style.apply(_style_n3, axis=1)
              .format("{:,.1f}")
              .bar(subset=["Total Insurance Revenue (ISR)"],
                   color="#bbf7d0", vmin=0),
            use_container_width=True,
        )

        st.info("""
**Reading guide:**
- **Expected CF Release**: Release of expected future benefits + RA unwinding (the core of revenue)  
- **CSM Amortisation**: Profit margin released as service is delivered (equal coverage units)  
- **LC Release**: For onerous contracts — LC reversal is also recognised as revenue (service delivery)  
- **Total ISR**: This is the "top-line" of an IFRS 17 insurer's income statement
""")

    # ── Note 4: IFIE ──────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Note 4 — Insurance Finance Income / Expense (IFIE)")
    st.caption("IFRS 17.88–92 — Split between P&L (locked-in DAIR) and OCI (rate change effect)")

    n4 = note4_ifie(all_results, _YEAR)
    if not n4.empty:
        def _style_n4(row):
            if row.name == "Total":
                return ["font-weight:700; background-color:#f0f4ff"] * len(row)
            return [""] * len(row)

        st.dataframe(
            n4.style.apply(_style_n4, axis=1).format("{:,.1f}"),
            use_container_width=True,
        )
        st.info("""
**OCI option explained:** Under the OCI option (applied to GMM cohorts here), the total IFIE is split:  
— **P&L**: Uses the DAIR (locked-in rate at inception). Stable, predictable line in the income statement.  
— **OCI**: The difference between DAIR and the current discount rate applied to PVFCF.  
  When rates rise: OCI is negative (accumulated OCI reserve builds up, reduces equity).  
  This is released back to P&L over the remaining contract lifetime.  
VFA contracts generally don't use the OCI option — the underlying items mechanism already routes most IFIE through CSM.
""")

    # ── Note 5: RCA Movement ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Note 5 — Reinsurance Contract Assets: Movement in 2024")
    st.caption("IFRS 17.60–70A — The ceded reinsurance programme mirrors the direct ICL at the cession rate")

    # all_rca is a dict {period: [RCASummary]}; flatten for note5
    _all_rca_flat = [r for rcas in all_rca.values() for r in rcas]
    n5 = note5_rca_movement(_all_rca_flat, _YEAR)
    if not n5.empty:
        def _style_n5(row):
            label = str(row.name).strip()
            if label.startswith("Opening") or label.startswith("Closing"):
                return ["font-weight:700; background-color:#f0f4ff"] * len(row)
            elif label.startswith("Sub-total") or label.startswith("Net Change"):
                return ["font-weight:600; background-color:#f8fafc"] * len(row)
            elif label.startswith("───"):
                return ["color:#6b7280; font-style:italic"] * len(row)
            return [""] * len(row)

        st.dataframe(
            n5.style.apply(_style_n5, axis=1).format("{:,.1f}", na_rep=""),
            use_container_width=True,
        )
        st.caption("""
*RCA is presented as an asset (positive = recoverable from reinsurers).  
The movement mirrors the gross ICL at the effective cession rate per cohort.*
""")

    # ── Note 6: Maturity Profile ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Note 6 — Maturity Profile of Undiscounted Future Cash Flows")
    st.caption("IFRS 17.132(b) — Expected timing of net future cash flows by time bucket")

    n6 = note6_maturity_profile(all_results, _YEAR)
    if not n6.empty:
        def _style_n6(row):
            if row.name[0] == "TOTAL":
                return ["font-weight:700; background-color:#f0f4ff"] * len(row)
            return [""] * len(row)

        bucket_cols = ["< 1 year", "1 – 3 years", "3 – 5 years", "> 5 years", "Grand Total"]
        fmt_cols = {c: "{:,.1f}" for c in bucket_cols if c in n6.columns}
        st.dataframe(
            n6.style.apply(_style_n6, axis=1)
              .format(fmt_cols)
              .bar(subset=["Grand Total"], color="#dbeafe", vmin=0),
            use_container_width=True,
            height=380,
        )

        # Stacked bar chart
        with st.expander("📊 Visual: Maturity Distribution by Cohort"):
            n6_reset = n6.reset_index()
            n6_data = n6_reset[n6_reset["Cohort"] != "TOTAL"]
            bucket_labels = ["< 1 year", "1 – 3 years", "3 – 5 years", "> 5 years"]
            bucket_colors = ["#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6"]

            fig_mat = go.Figure()
            for b_label, b_color in zip(bucket_labels, bucket_colors):
                if b_label in n6_data.columns:
                    fig_mat.add_bar(
                        name=b_label,
                        x=n6_data["Cohort"],
                        y=n6_data[b_label],
                        marker_color=b_color,
                    )
            fig_mat.update_layout(
                barmode="stack",
                title="Undiscounted Future Cash Flows by Maturity Bucket ('000 HKD)",
                height=400, margin=dict(l=40, r=40, t=60, b=80),
                xaxis_title="Cohort", yaxis_title="Undiscounted CF ('000 HKD)",
                xaxis=dict(tickangle=-30),
            )
            st.plotly_chart(fig_mat, use_container_width=True)

        st.caption("""
*Simplified: assumes uniform cash flow distribution over expected duration  
(GMM: 8 yrs, VFA: 15 yrs, PAA: 0.5 yrs). Undiscounted approximation = PVFCF × 1.12.*
""")

    # ── Cohort detail (supplement) ────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Supplement — Per-Cohort ICL Summary")
    st.caption("Full-year 2024 rollforward by cohort — useful for audit and management reporting")

    nd = note1_cohort_detail(all_results, _YEAR)
    if not nd.empty:
        def _style_nd(row):
            if row.name.startswith("MED_ONR"):
                return ["background-color:#fff7ed"] * len(row)
            return [""] * len(row)

        fmt_nd = {c: "{:,.1f}" for c in nd.select_dtypes("number").columns}
        st.dataframe(
            nd.style.apply(_style_nd, axis=1)
              .format(fmt_nd)
              .map(lambda v: "color:#ef4444;font-weight:700"
                   if isinstance(v, float) and v > 5 and "LC" in str(v) else "", subset=["Closing LC"])
              .highlight_max(subset=["Closing CSM"], color="#dcfce7"),
            use_container_width=True,
        )

    # ── Excel download ─────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⬇️  Download Disclosure Notes")
    import io as _io_disc
    buf_disc = _io_disc.BytesIO()
    with pd.ExcelWriter(buf_disc, engine="openpyxl") as writer:
        if not n1.empty:
            n1.to_excel(writer, sheet_name="Note1_ICL_Movement")
        if not n2.empty:
            n2.to_excel(writer, sheet_name="Note2_ICL_Components")
        if not n3.empty:
            n3.to_excel(writer, sheet_name="Note3_Insurance_Revenue")
        if not n4.empty:
            n4.to_excel(writer, sheet_name="Note4_IFIE")
        if not n5.empty:
            n5.to_excel(writer, sheet_name="Note5_RCA_Movement")
        if not n6.empty:
            n6.to_excel(writer, sheet_name="Note6_Maturity")
        if not nd.empty:
            nd.to_excel(writer, sheet_name="Cohort_Detail")
    buf_disc.seek(0)
    st.download_button(
        "📥  Download Disclosure Notes (.xlsx)",
        data=buf_disc,
        file_name="IFRS17_Disclosures_2024.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 10 — How It Works (NEW)
# ══════════════════════════════════════════════════════════════════════════════

with tab_how:
    st.markdown("## 📖 IFRS 17 — How It Works")
    st.markdown("""
IFRS 17 *Insurance Contracts* (effective 1 Jan 2023) is the global accounting standard 
that replaces IFRS 4. It requires insurers to measure insurance liabilities using 
current, explicit, and unbiased estimates — and to recognise profit only as insurance 
service is delivered.
""")

    # ── Section 1: Three measurement models ──────────────────────────────
    st.markdown("### 1. Three Measurement Models")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("""
<div class="concept-card">
  <div class="concept-title">🔵 GMM — General Measurement Model</div>
  <div class="concept-body">
  Also called BBA (Building Block Approach). Applies to most long-duration contracts.<br><br>
  <b>ICL = PVFCF + RA + CSM</b><br><br>
  Three building blocks:<br>
  • PVFCF: Present value of future cash flows (discounted at current rates)<br>
  • RA: Risk adjustment (compensation for uncertainty)<br>
  • CSM: Contractual Service Margin (unearned profit, released over service)
  </div>
</div>
""", unsafe_allow_html=True)

    with c2:
        st.markdown("""
<div class="concept-card">
  <div class="concept-title">🟢 PAA — Premium Allocation Approach</div>
  <div class="concept-body">
  A simplified model for contracts ≤ 1 year (or where GMM gives similar results).<br><br>
  <b>LRC = Unearned Premium − IACF asset</b><br><br>
  No CSM — profit emerges as premium is earned each period.<br>
  Simpler but less precise for long-tail risks.<br><br>
  <i>Products: short-duration medical, riders, annual policies</i>
  </div>
</div>
""", unsafe_allow_html=True)

    with c3:
        st.markdown("""
<div class="concept-card">
  <div class="concept-title">🟡 VFA — Variable Fee Approach</div>
  <div class="concept-body">
  For participating (with-profit) contracts where the insurer manages underlying items 
  on behalf of policyholders.<br><br>
  <b>ICL = PVFCF + RA + CSM</b> (same structure as GMM)<br><br>
  Key difference: <b>CSM is linked to underlying items (investment portfolio)</b>.<br>
  Market gains/losses → CSM (not P&L or OCI).<br><br>
  <i>Products: whole life par, endowment par</i>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Section 2: AOC 9-item breakdown ──────────────────────────────────
    st.markdown("### 2. Analysis of Change (AOC) — 9-Item Decomposition")
    st.markdown("""
Every period, the ICL movement is decomposed into 9 items. This is the heart of 
IFRS 17 reporting — it links every balance sheet movement to a P&L or OCI line.
""")

    aoc_items = [
        ("① New Business",                  "P&L / CSM", "First recognition of new contracts in the period. If profitable: PVFCF + RA = CSM (Day-1 P&L = 0). If onerous: loss recognised immediately."),
        ("② Expected CF Release (incl. RA)", "Insurance Revenue (P&L)", "Release of expected future cash flows that become due in the period, plus RA released as service is provided. Core driver of Insurance Revenue."),
        ("③ Experience Variance",            "Insurance Service Expense (P&L)", "Actual cash flows vs expected. Adverse experience (claims > expected) increases ISE. Favourable experience reduces ISE."),
        ("④ CSM Amortisation",               "Insurance Revenue (P&L)", "CSM is released to P&L based on coverage units consumed in the period. Steady, predictable profit pattern."),
        ("⑤ LC Reversal",                    "Insurance Revenue (P&L)", "For onerous contracts: the Loss Component (LC) is reversed as service is provided, reducing the initial loss recognised."),
        ("⑥ IFIE — P&L (DAIR unwind)",       "IFIE — P&L", "Interest unwinding on ICL using the locked-in discount rate at initial recognition (DAIR). Always goes to P&L."),
        ("⑦ IFIE — OCI (rate change)",        "OCI", "Difference between current discount rate and DAIR applied to PVFCF. Under the OCI option, this volatility is excluded from P&L and reported in Other Comprehensive Income."),
        ("⑧ Assumption Change → P&L",        "Insurance Service Expense (P&L)", "Changes in non-economic assumptions (e.g., mortality, lapse) affecting RA, or changes for onerous contracts where CSM = 0."),
        ("⑨ Assumption Change → CSM",        "CSM (Balance Sheet)", "Changes in non-economic assumptions for profitable contracts are absorbed by CSM — preventing P&L volatility. CSM acts as a buffer."),
    ]

    for item, driver, explanation in aoc_items:
        with st.expander(f"**{item}** → {driver}"):
            st.write(explanation)

    # ── Section 3: VFA special mechanics ─────────────────────────────────
    st.markdown("### 3. VFA Special Mechanics — Why CSM is Volatile")
    st.markdown("""
<div class="concept-card">
  <div class="concept-title">VFA: Underlying Items Change → CSM</div>
  <div class="concept-body">
  In VFA, the insurer's obligation to policyholders includes a share of the 
  underlying items (investment fund). When the fund value changes:<br><br>
  
  📈 <b>Market rally</b>: Fund value ↑ → PVFCF ↑ (more benefits owed) — but CSM also ↑ 
  (insurer earns a larger fee) → <b>net ICL unchanged</b>, CSM increases<br><br>
  
  📉 <b>Market correction</b>: Fund value ↓ → PVFCF ↓ → CSM ↓ → 
  <b>CSM absorbs the market loss, protecting P&L</b><br><br>
  
  This is why VFA CSM is far more volatile than GMM CSM (which only decreases 
  steadily from amortisation and assumption changes).
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Section 4: OCI Option explained ──────────────────────────────────
    st.markdown("### 4. OCI Option — Reducing P&L Volatility")
    st.markdown("""
Under IFRS 17, a key accounting policy choice is whether to present insurance finance 
income/expense (IFIE) **fully in P&L**, or to **split it between P&L and OCI**:

| Option | P&L shows | OCI shows | Typical users |
|--------|-----------|-----------|---------------|
| **P&L only** | Full IFIE (current rate) | Nothing | Simple books, no ALM mismatch |
| **OCI option** | IFIE at locked-in DAIR | Rate-change difference | Most large insurers |

The **OCI option** means P&L is stable (locked-in rate), while OCI absorbs 
the mark-to-market noise from interest rate movements. This demo uses the OCI option 
for GMM cohorts (item ⑦ above).

VFA generally does NOT use the OCI option — the underlying items mechanism already 
routes most IFIE through CSM.
""")

    # ── Section 5: Reinsurance RCA ────────────────────────────────────────
    st.markdown("### 5. Quota Share Reinsurance — RCA")
    st.markdown("""
<div class="concept-card">
  <div class="concept-title">Reinsurance Contract Asset (RCA)</div>
  <div class="concept-body">
  IFRS 17 requires reinsurance contracts to be measured separately from the underlying 
  direct business. For Quota Share (proportional) reinsurance:<br><br>
  
  <b>RCA = cession_rate × gross ICL</b> (but as an asset, opposite sign)<br><br>
  
  The RCA follows the same AOC structure as the gross ICL — every movement in the 
  direct business has a corresponding movement in the RCA at the cession rate.<br><br>
  
  Net ICL = Gross ICL − RCA = (1 − cession_rate) × Gross ICL
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Section 6: Layered XL Reinsurance & LEV ──────────────────────────
    st.markdown("### 6. Layered Excess-of-Loss Reinsurance & LEV")
    st.markdown("""
Real-world reinsurance programmes are rarely a single Quota Share.  
A typical arrangement stacks **multiple layers**, each potentially split across several reinsurers:
""")

    st.code("""
Example: Medical / Term product reinsurance programme
─────────────────────────────────────────────────────
  Layer 1  (claim  0 – 300k)   80% retained,  20% → Hanover
  Layer 2  (claim 300k – 600k) 20% → MR,      80% → BOC Re
  Above 600k                   retained (or separate Cat XL)
""", language="")

    st.markdown("""
**Key concept — Limited Expected Value (LEV)**

To price each layer, actuaries use the *layer function* derived from LEV:

$$E[\\text{Layer}_{[a,b]}] = E[X \\wedge b] - E[X \\wedge a]$$

where the LEV at limit $d$ is:

$$E[X \\wedge d] = \\int_0^d S(x)\\, dx, \\quad S(x) = P(X > x)$$

For a **log-normal** claim distribution $X \\sim \\text{LogNormal}(\\mu, \\sigma)$:

$$E[X \\wedge d] = e^{\\mu + \\sigma^2/2}\\, \\Phi\\!\\left(\\frac{\\ln d - \\mu - \\sigma^2}{\\sigma}\\right) + d\\left[1 - \\Phi\\!\\left(\\frac{\\ln d - \\mu}{\\sigma}\\right)\\right]$$

This gives the **expected claim recovery per layer**, which feeds into each RCA's PVFCF.

**Why does each layer need a separate RCA?**  
Layer 2 (300k–600k) has *lower frequency* but *higher severity* than Layer 1.  
Their RA (risk adjustment) profiles differ, and each reinsurance contract is a distinct 
legal entity under IFRS 17 — they cannot be merged.
""")

    st.info("""
**In practice (and in this demo):** The actuarial system (Prophet / MoSes) outputs  
**pre-calculated PVFCF per cohort** — the LEV layer-splitting is done *inside* Prophet,  
not in the subledger. The subledger simply reads the numbers from `actuarial_output.csv`  
and processes each RCA independently. This is the standard architecture in production.
""")

    # ── Section 7: Onerous Contracts — Full Lifecycle ────────────────────
    st.markdown("### 7. Onerous Contracts — The Full Lifecycle")
    st.markdown("""
An onerous contract arises when **PVFCF + RA > 0** at initial recognition — the group
of contracts is expected to make a net loss.  Under IFRS 17 this loss must be recognised
**immediately on Day 1** (no deferral).  The **Loss Component (LC)** tracks this liability.
""")

    c_onr1, c_onr2 = st.columns(2)
    with c_onr1:
        st.markdown("""
<div class="concept-card">
  <div class="concept-title">☠️ Day-1 Recognition — LC Born</div>
  <div class="concept-body">
  When a group is onerous at initial recognition:<br>
  <code>LC = PVFCF + RA &gt; 0, CSM = 0</code><br><br>
  <b>Journal entry:</b><br>
  Dr Insurance Service Expense (ISE)<br>
  Cr ICL — Loss Component<br><br>
  The full expected loss is charged to P&amp;L immediately.
  No profit margin (CSM) exists — the contract is under water.
  </div>
</div>
""", unsafe_allow_html=True)

    with c_onr2:
        st.markdown("""
<div class="concept-card">
  <div class="concept-title">📉 Additional Loss — LC Increases</div>
  <div class="concept-body">
  If experience is worse than expected (e.g. adverse claims),
  the contract becomes <i>more</i> onerous:<br>
  <code>lc_reversal &gt; 0</code> (positive → LC rises)<br><br>
  <b>Journal entry:</b><br>
  Dr ISE (additional loss)<br>
  Cr ICL — Loss Component<br><br>
  The extra loss goes straight to P&amp;L — there is no CSM buffer.
  This is the key asymmetry vs. profitable contracts.
  </div>
</div>
""", unsafe_allow_html=True)

    c_onr3, c_onr4 = st.columns(2)
    with c_onr3:
        st.markdown("""
<div class="concept-card">
  <div class="concept-title">📈 Service Delivery — LC Releases</div>
  <div class="concept-body">
  As insurance coverage is provided each period, the LC is
  systematically released (like CSM amortisation for profitable contracts):<br>
  <code>lc_reversal &lt; 0</code> (negative → LC falls)<br><br>
  <b>Journal entry:</b><br>
  Dr ICL — Loss Component<br>
  Cr Insurance Revenue (ISR)<br><br>
  This LC release is the onerous contract's equivalent of "profit emergence"
  — even though the contract is still loss-making overall.
  </div>
</div>
""", unsafe_allow_html=True)

    with c_onr4:
        st.markdown("""
<div class="concept-card">
  <div class="concept-title">🟢 Recovery — LC → 0, CSM Forms</div>
  <div class="concept-body">
  If assumptions improve sufficiently (e.g. mortality/morbidity revision):<br>
  1. Improvement first <b>absorbs the remaining LC</b> (Dr ICL-LC / Cr ISR)<br>
  2. Any <b>excess improvement</b> creates a NEW CSM:<br>
  <code>assumption_chg_csm &gt; 0, lc_eom = 0</code><br><br>
  <b>Journal entry (for excess):</b><br>
  Dr ICL — PVFCF<br>
  Cr ICL — CSM<br><br>
  The contract has flipped from onerous to profitable. The new CSM
  will amortise as Insurance Revenue in future periods.
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown("""
**Demo in this app:** the `MED_ONR_RECOVERY` cohort in the 📉 Time Series tab walks through
this exact journey:

| Period | Event | LC | CSM |
|--------|-------|----|-----|
| 2023Q4 | Day-1 onerous recognition | **750** | 0 |
| 2024Q1 | Bad experience → extra LC | **830** ↑ | 0 |
| 2024Q2 | Stabilisation, regular service LC release | 730 ↓ | 0 |
| 2024Q3 | Major assumption improvement, large LC release | 115 ↓↓ | 0 |
| 2024Q4 | **Last LC cleared + excess → CSM born** | **0** ✅ | **160** 🟢 |
""")

    # ── Section 8: This subledger's flow ──────────────────────────────────
    st.markdown("### 8. This Subledger's Data Flow")
    st.code("""
Actuarial System (Prophet / MoSes)
    actuarial_output.csv
    Columns: cohort_id | period | measurement_model
             pvfcf_eom | ra_eom | csm_eom | lc_eom
             exp_cf_release | experience_var | csm_amortisation
             finance_charge_pl | finance_charge_oci
             assumption_chg_pl | assumption_chg_csm
             underlying_items_chg (VFA only)
             premium_written | premium_earned (PAA only)
             dair | cession_rate
          │
          ▼
  IFRS 17 AOC Engine (gmm.py / paa.py / vfa.py)
    → AOCResult: 9-item decomposition, BOM + movements = EOM check
          │
          ├── Quota Share RCA (reinsurance.py)
          │     → RCASummary: mirror of direct business at cession rate
          │
          ▼
  GL Journal Entry Generator (subledger.py)
    → JournalBatch: debit/credit pairs, account codes from chart_of_accounts.yaml
    → Verified: total debits = total credits (balance check)
          │
          ▼
  Reports (report.py)
    → P&L Summary | Balance Sheet | AOC Detail
    → GL Entries | Trial Balance | Time Series
    → Excel export (6 sheets)
""", language="")

    st.info("💡 The code for all engines is open source at "
            "[github.com/woming98/ifrs17-subledger](https://github.com/woming98/ifrs17-subledger). "
            "See `src/models/` for GMM, PAA, and VFA implementations.")


# ──────────────────────────────────────────────────────────────────────────────
# Footer
# ──────────────────────────────────────────────────────────────────────────────

st.divider()
col_dl, col_info = st.columns([2, 5])

with col_dl:
    st.subheader("📤 Export Excel")
    if st.button("Generate & Download", type="secondary"):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pl_summary(aoc_list, rca_list).to_excel(writer,     sheet_name="P&L Summary",    index=False)
            bs_summary(aoc_list, rca_list).to_excel(writer,     sheet_name="BS Summary",      index=False)
            aoc_detail(aoc_list).to_excel(writer,               sheet_name="AOC Detail",      index=False)
            gl_detail(batches).to_excel(writer,                 sheet_name="GL Entries",      index=False)
            trial_balance(batches).to_excel(writer,             sheet_name="Trial Balance",   index=False)
            reconcile_portfolio(aoc_list, tol).to_excel(writer, sheet_name="Reconciliation",  index=False)
            ts_df.to_excel(writer,                              sheet_name="Time Series",     index=False)
        buf.seek(0)
        st.download_button(
            "⬇ Download Excel",
            data=buf.getvalue(),
            file_name=f"subledger_{sel_period}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with col_info:
    st.caption("""
**IFRS 17 Subledger Demo** · Python 3.10+ · Streamlit · Plotly  
Models: GMM (IFRS 17.32–52) · PAA (IFRS 17.53–59) · VFA (IFRS 17.45A–45D)  
[github.com/woming98/ifrs17-subledger](https://github.com/woming98/ifrs17-subledger)
""")
