"""
IFRS 17 Subledger — Streamlit App (Phase 2)

New in Phase 2:
  - VFA (Variable Fee Approach) for participating contracts
  - Multi-period roll-forward (4 quarters, 7 cohorts)
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
from src.subledger import ChartOfAccounts, generate_journal
from src.reconciliation import (
    reconcile_portfolio, check_journal_balance, aoc_waterfall,
    build_timeseries, portfolio_timeseries,
)
from src.report import pl_summary, bs_summary, aoc_detail, gl_detail, trial_balance

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
_DATA_DIR = os.path.join(_ROOT, "data")
_COA_PATH = os.path.join(_ROOT, "config", "chart_of_accounts.yaml")


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("IFRS 17 Subledger")
    st.caption("Actuarial output → GL entries · Full process demo")

    st.divider()
    st.subheader("📥 Data Source")
    data_file = os.path.join(_DATA_DIR, "actuarial_output.csv")
    if not os.path.exists(data_file):
        st.error("Multi-period data not found.\nPlease run:\n`python data/generate_multi_period.py`")
        st.stop()
    st.success(f"✓ actuarial_output.csv loaded")

    st.divider()
    st.subheader("⚙️ Settings")
    sel_period = st.selectbox("Current period", QUARTERS, index=len(QUARTERS)-1)
    tol = st.number_input("Reconciliation tolerance", value=0.01, step=0.01, format="%.3f")

    run_btn = st.button("▶  Run Subledger", type="primary", use_container_width=True)

    st.divider()
    st.caption("**Cohorts in demo**")
    st.markdown("""
- <span class="badge-gmm">GMM</span> TERM_GMM_2022 (Term 5Y, QS 30%)
- <span class="badge-gmm">GMM</span> MED_GMM_2021 (Medical LT, QS 20%)
- <span class="badge-gmm">GMM</span> MED_GMM_ONR (Onerous)
- <span class="badge-paa">PAA</span> MED_PAA (Medical ST, QS 25%)
- <span class="badge-paa">PAA</span> RIDER_PAA (Rider ST)
- <span class="badge-vfa">VFA</span> WL_VFA_2019 (Whole Life Par, QS 20%)
- <span class="badge-vfa">VFA</span> ENDO_VFA_2022 (Endowment Par)
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────────────────────

st.title("📊 IFRS 17 Subledger — Full Process Demo")
st.markdown("""
> **Coverage**: GMM · PAA · **VFA** (new) · Quota Share RCA · AOC 9-step decomposition  
> **Multi-period**: 7 cohorts × 4 quarters (2024Q1–Q4) · Time series analytics
""")

if not run_btn:
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
def run_all_periods(data_path: str, coa_path: str, tol: float):
    """
    Process all 4 quarters from actuarial_output.csv.
    Returns:
        all_results : {period: [AOCResult, ...]}
        all_rca     : {period: [RCASummary, ...]}
        all_batches : {period: [JournalBatch, ...]}
        ts_df       : long-form time series DataFrame
        warnings    : list of warning strings
    """
    df  = pd.read_csv(data_path)
    coa = ChartOfAccounts(coa_path)

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

        rca_by_id = {}
        for a in aoc_list:
            if a.cession_rate > 0:
                rca = compute_rca(a)
                rca_list.append(rca)
                rca_by_id[a.cohort_id] = rca

        for a in aoc_list:
            batch = generate_journal(a, coa, rca_by_id.get(a.cohort_id))
            batches.append(batch)

        all_results[period] = aoc_list
        all_rca[period]     = rca_list
        all_batches[period] = batches

    ts_df = build_timeseries(all_results)
    return all_results, all_rca, all_batches, ts_df, warnings


all_results, all_rca, all_batches, ts_df, warnings = run_all_periods(data_file, _COA_PATH, tol)

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

tab_aoc, tab_pl, tab_bs, tab_gl, tab_tb, tab_recon, tab_ts, tab_how = st.tabs([
    "📈 AOC Waterfall",
    "💹 P&L Summary",
    "🏦 Balance Sheet",
    "📒 GL Entries",
    "⚖️ Trial Balance",
    "✅ Reconciliation",
    "📉 Time Series",
    "📖 How It Works",
])


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

    # ── Raw time series table ─────────────────────────────────────────────
    with st.expander("View raw time series data"):
        st.dataframe(
            ts_df.style.format({c: "{:,.2f}" for c in ts_df.select_dtypes("number").columns}),
            use_container_width=True, height=400,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 8 — How It Works (NEW)
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

    # ── Section 6: This subledger's flow ──────────────────────────────────
    st.markdown("### 6. This Subledger's Data Flow")
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
