"""
IFRS 17 Subledger — Streamlit Visualization App

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
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.models.gmm import GMMModel
from src.models.paa import PAAModel
from src.reinsurance import compute_rca
from src.subledger import ChartOfAccounts, generate_journal
from src.reconciliation import reconcile_portfolio, check_journal_balance, aoc_waterfall
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
  .section-title { font-size:1.1rem; font-weight:700; color:#1e293b; margin-top:0.5rem; }
  .ok-badge   { background:#d1fae5; color:#065f46; border-radius:4px; padding:2px 8px; font-size:0.8rem; }
  .fail-badge { background:#fee2e2; color:#991b1b; border-radius:4px; padding:2px 8px; font-size:0.8rem; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar — data input
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("IFRS 17 Subledger")
    st.caption("End-to-end demo: actuarial output → GL entries")

    st.divider()
    st.subheader("📥 Data Input")

    use_demo = st.toggle("Use built-in demo data", value=True)

    if use_demo:
        _data_dir    = os.path.join(_ROOT, "data")
        prior_path   = os.path.join(_data_dir, "prior_period.csv")
        current_path = os.path.join(_data_dir, "current_period.csv")
        if not os.path.exists(prior_path):
            st.error("Demo data not found. Please run:\n`python data/generate_demo.py`")
            st.stop()
    else:
        prior_file   = st.file_uploader("Opening balances CSV (prior_period.csv)",   type="csv")
        current_file = st.file_uploader("Closing balances + AOC CSV (current_period.csv)", type="csv")
        if prior_file is None or current_file is None:
            st.info("Please upload both CSV files, or enable demo mode.")
            st.stop()
        prior_path   = prior_file
        current_path = current_file

    st.divider()
    st.subheader("⚙️ Settings")
    tol = st.number_input("Reconciliation tolerance ('000 HKD)", value=0.01, step=0.01, format="%.3f")

    run_btn = st.button("▶  Run Subledger", type="primary", use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────────────────────

st.title("📊 IFRS 17 Subledger — Full Process Demo")
st.markdown("""
> **Scope**: GMM (Term Non-Par + Medical Non-Par incl. Onerous cohort) + PAA (short-duration Medical / Rider)  
> **Covers**: AOC 9-step decomposition · Quota Share ceded RCA · GL debit/credit entries · P&L / BS / Trial Balance · Excel export
""")

if not run_btn:
    st.info("Select a data source on the left and click **▶ Run Subledger** to start.")

    st.subheader("IFRS 17 Subledger Data Flow")
    st.markdown("""
```
Actuarial System Output (Prophet / MoSes)
    prior_period.csv   →  Opening balances (BOM)
    current_period.csv →  Closing balances (EOM) + AOC 9-item decomposition
           │
           ▼
  ┌──────────────────────────────────────┐
  │  IFRS 17 AOC Engine                 │
  │  ┌──────────┐  ┌───────────────┐   │
  │  │  GMM     │  │   PAA         │   │
  │  │ (BBA)    │  │ (≤1yr / UPR)  │   │
  │  └──────────┘  └───────────────┘   │
  └──────────────┬───────────────────────┘
                 │
                 ├── Quota Share RCA calculation
                 │
                 ▼
  ┌──────────────────────────────────────┐
  │  GL Journal Entry Generator         │
  │  Accounts: ICL / RCA / ISR / ISE /  │
  │            IFIE (P&L) / OCI         │
  └──────────────┬───────────────────────┘
                 │
         ┌───────┼──────────┐
         ▼       ▼          ▼
     P&L Summary  BS Summary  Trial Balance
     AOC Waterfall  GL Entries  Excel Export
```
""")
    st.stop()


# ──────────────────────────────────────────────────────────────────────────────
# Core computation (cached)
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Computing…")
def run_subledger(prior_path, current_path, coa_path, tol):
    prior_df   = pd.read_csv(prior_path)
    current_df = pd.read_csv(current_path)
    coa        = ChartOfAccounts(coa_path)
    gmm        = GMMModel()
    paa        = PAAModel()

    aoc_list = []
    warnings = []

    for _, eom_row in current_df.iterrows():
        cid   = str(eom_row["cohort_id"])
        model = str(eom_row["measurement_model"]).upper()
        bom_matches = prior_df[prior_df["cohort_id"] == cid]
        if bom_matches.empty:
            warnings.append(f"Cohort {cid} not found in prior_period.csv — skipped.")
            continue
        bom_row = bom_matches.iloc[0]
        if model == "GMM":
            result = gmm.compute_aoc(bom_row, eom_row)
        elif model == "PAA":
            result = paa.compute_aoc(bom_row, eom_row)
        else:
            warnings.append(f"Unknown measurement model '{model}' (cohort: {cid}) — skipped.")
            continue
        aoc_list.append(result)

    rca_list  = [compute_rca(a) for a in aoc_list if a.cession_rate > 0]
    rca_by_id = {r.cohort_id: r for r in rca_list}
    batches   = [generate_journal(a, coa, rca_by_id.get(a.cohort_id)) for a in aoc_list]

    return aoc_list, rca_list, batches, warnings


coa_path = os.path.join(_ROOT, "config", "chart_of_accounts.yaml")
aoc_list, rca_list, batches, warnings = run_subledger(prior_path, current_path, coa_path, tol)

for w in warnings:
    st.warning(w)


# ──────────────────────────────────────────────────────────────────────────────
# KPI metrics
# ──────────────────────────────────────────────────────────────────────────────

period        = aoc_list[0].period if aoc_list else "N/A"
total_icl     = sum(a.eom_icl for a in aoc_list)
total_rca     = sum(r.rca_eom_icl for r in rca_list)
total_isr     = sum(a.insurance_revenue for a in aoc_list)
total_ise     = sum(a.insurance_service_expense for a in aoc_list)
total_isr_rca = sum(r.rca_insurance_revenue for r in rca_list)
net_pl        = total_isr - total_ise + sum(a.ifie_pl for a in aoc_list) + total_isr_rca
all_recon_ok  = all(a.reconciliation_ok for a in aoc_list)
all_bal_ok    = all(b.is_balanced(tol) for b in batches)

st.subheader(f"Period: {period}  |  {len(aoc_list)} cohorts")

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    st.metric("ICL EOM (Total)", f"{total_icl:,.0f}",
              help="Gross Insurance Contract Liability — closing balance ('000 HKD)")
with c2:
    st.metric("RCA EOM", f"{total_rca:,.0f}",
              help="Reinsurance Contract Asset — closing balance ('000 HKD)")
with c3:
    st.metric("Net ICL", f"{total_icl - total_rca:,.0f}")
with c4:
    st.metric("Insurance Revenue (ISR)", f"{total_isr:,.0f}")
with c5:
    st.metric("Net P&L (incl. RI)", f"{net_pl:,.0f}")
with c6:
    recon_label = "✅ All passed" if all_recon_ok and all_bal_ok else "❌ Check errors"
    st.metric("Reconciliation", recon_label)


# ──────────────────────────────────────────────────────────────────────────────
# Tabs
# ──────────────────────────────────────────────────────────────────────────────

tab_aoc, tab_pl, tab_bs, tab_gl, tab_tb, tab_recon = st.tabs([
    "📈 AOC Waterfall",
    "💹 P&L Summary",
    "🏦 Balance Sheet",
    "📒 GL Entries",
    "⚖️ Trial Balance",
    "✅ Reconciliation",
])


# ── Tab 1: AOC Waterfall ──────────────────────────────────────────────────────

with tab_aoc:
    st.markdown('<div class="section-title">ICL Movement Waterfall (Portfolio Total)</div>',
                unsafe_allow_html=True)

    wf_df  = aoc_waterfall(aoc_list)
    labels = wf_df["aoc_item"].tolist()
    values = wf_df["amount"].tolist()

    measure = ["absolute"] + ["relative"] * (len(labels) - 2) + ["absolute"]
    text    = [f"{v:+,.0f}" for v in values]

    fig_wf = go.Figure(go.Waterfall(
        orientation="v",
        measure=measure,
        x=labels,
        y=values,
        text=text,
        textposition="outside",
        connector={"line": {"color": "rgba(63,63,63,0.3)"}},
        increasing={"marker": {"color": "#ef4444"}},
        decreasing={"marker": {"color": "#22c55e"}},
        totals={"marker":    {"color": "#3b82f6"}},
    ))
    fig_wf.update_layout(
        title=f"ICL Movement Waterfall — {period} (Portfolio, '000 HKD)",
        xaxis_tickangle=-30,
        height=500,
        margin=dict(l=40, r=40, t=60, b=100),
        plot_bgcolor="#fafafa",
        paper_bgcolor="#ffffff",
    )
    st.plotly_chart(fig_wf, use_container_width=True)

    st.markdown("**AOC Detail by Cohort**")
    aoc_df = aoc_detail(aoc_list)
    aoc_cols = [
        "cohort_id", "product", "model", "BOM ICL",
        "① New Business", "② Expected CF Release (incl. RA)",
        "③ Experience Variance", "④ CSM Amortisation", "⑤ LC Reversal",
        "⑥ IFIE — P&L (DAIR unwind)", "⑦ IFIE — OCI (rate change)",
        "⑧ Assumption Change → P&L", "⑨ Assumption Change → CSM",
        "EOM ICL (Input)", "Recon Diff",
    ]
    existing_cols = [c for c in aoc_cols if c in aoc_df.columns]
    st.dataframe(aoc_df[existing_cols], use_container_width=True, height=250)


# ── Tab 2: P&L Summary ───────────────────────────────────────────────────────

with tab_pl:
    st.markdown('<div class="section-title">Insurance Service P&L Summary</div>',
                unsafe_allow_html=True)

    pl_df = pl_summary(aoc_list, rca_list)
    st.dataframe(
        pl_df.style.format({c: "{:,.2f}" for c in pl_df.select_dtypes("number").columns}),
        use_container_width=True,
        height=280,
    )

    plot_df = pl_df[pl_df["cohort_id"] != "__TOTAL__"]
    fig_pl  = go.Figure()
    fig_pl.add_bar(name="Insurance Revenue (ISR)", x=plot_df["cohort_id"],
                   y=plot_df["insurance_revenue"],      marker_color="#22c55e")
    fig_pl.add_bar(name="Insurance Service Expense (ISE)", x=plot_df["cohort_id"],
                   y=-plot_df["insurance_service_expense"], marker_color="#ef4444")
    fig_pl.add_bar(name="IFIE P&L", x=plot_df["cohort_id"],
                   y=plot_df["ifie_pl"],                marker_color="#f59e0b")
    fig_pl.add_bar(name="RI Revenue (RCA)", x=plot_df["cohort_id"],
                   y=plot_df["rca_insurance_revenue"],  marker_color="#6366f1")
    fig_pl.add_scatter(
        name="Net P&L (incl. RI)", x=plot_df["cohort_id"], y=plot_df["net_pl_after_ri"],
        mode="markers+lines", marker=dict(size=10, color="#1e293b"), line=dict(dash="dash"),
    )
    fig_pl.update_layout(
        barmode="group",
        title=f"P&L Breakdown — {period} ('000 HKD)",
        height=420,
        xaxis_tickangle=-20,
        margin=dict(l=40, r=40, t=60, b=60),
    )
    st.plotly_chart(fig_pl, use_container_width=True)


# ── Tab 3: Balance Sheet ──────────────────────────────────────────────────────

with tab_bs:
    st.markdown('<div class="section-title">ICL / RCA Closing Balances (Balance Sheet)</div>',
                unsafe_allow_html=True)

    bs_df = bs_summary(aoc_list, rca_list)
    st.dataframe(
        bs_df.style.format({c: "{:,.2f}" for c in bs_df.select_dtypes("number").columns}),
        use_container_width=True,
        height=280,
    )

    plot_bs = bs_df[bs_df["cohort_id"] != "__TOTAL__"]
    fig_bs  = go.Figure()
    fig_bs.add_bar(name="PVFCF",  x=plot_bs["cohort_id"], y=plot_bs["icl_pvfcf"], marker_color="#3b82f6")
    fig_bs.add_bar(name="RA",     x=plot_bs["cohort_id"], y=plot_bs["icl_ra"],    marker_color="#60a5fa")
    fig_bs.add_bar(name="CSM",    x=plot_bs["cohort_id"], y=plot_bs["icl_csm"],   marker_color="#93c5fd")
    fig_bs.add_bar(name="LC",     x=plot_bs["cohort_id"], y=-plot_bs["icl_lc"],   marker_color="#ef4444")
    fig_bs.add_bar(name="RCA",    x=plot_bs["cohort_id"], y=plot_bs["rca_total"], marker_color="#22c55e")
    fig_bs.update_layout(
        barmode="stack",
        title=f"ICL Component Breakdown — {period} ('000 HKD)",
        height=440,
        xaxis_tickangle=-20,
        margin=dict(l=40, r=40, t=60, b=60),
    )
    st.plotly_chart(fig_bs, use_container_width=True)


# ── Tab 4: GL Entries ─────────────────────────────────────────────────────────

with tab_gl:
    st.markdown('<div class="section-title">GL Journal Entries (Full Debit / Credit Log)</div>',
                unsafe_allow_html=True)

    gl_df = gl_detail(batches)

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        cohort_options = ["(All)"] + sorted(gl_df["cohort_id"].unique().tolist())
        sel_cohort = st.selectbox("Filter by cohort", cohort_options)
    with col_f2:
        acc_options = ["(All)"] + sorted(gl_df["account_code"].unique().tolist())
        sel_acc = st.selectbox("Filter by account", acc_options)

    filtered = gl_df.copy()
    if sel_cohort != "(All)":
        filtered = filtered[filtered["cohort_id"] == sel_cohort]
    if sel_acc != "(All)":
        filtered = filtered[filtered["account_code"] == sel_acc]

    st.caption(f"Showing {len(filtered)} rows (total {len(gl_df)} rows)")
    st.dataframe(
        filtered.style.format({"debit": "{:,.2f}", "credit": "{:,.2f}"}),
        use_container_width=True,
        height=460,
    )

    csv_buf = io.StringIO()
    gl_df.to_csv(csv_buf, index=False, encoding="utf-8")
    st.download_button(
        "⬇ Download GL Entries CSV", csv_buf.getvalue(),
        file_name=f"gl_entries_{period}.csv", mime="text/csv",
    )


# ── Tab 5: Trial Balance ──────────────────────────────────────────────────────

with tab_tb:
    st.markdown('<div class="section-title">Trial Balance</div>', unsafe_allow_html=True)

    tb_df = trial_balance(batches)

    def color_net(val):
        if isinstance(val, (int, float)):
            if val > 0:   return "color: #1d4ed8;"
            elif val < 0: return "color: #dc2626;"
        return ""

    st.dataframe(
        tb_df.style
             .format({"total_debit": "{:,.2f}", "total_credit": "{:,.2f}", "net": "{:,.2f}"})
             .map(color_net, subset=["net"]),     # pandas >= 2.1: map() replaces applymap()
        use_container_width=True,
        height=420,
    )

    total_dr = tb_df["total_debit"].sum()
    total_cr = tb_df["total_credit"].sum()
    diff_tb  = total_dr - total_cr

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Total Debits",  f"{total_dr:,.2f}")
    col_b.metric("Total Credits", f"{total_cr:,.2f}")
    col_c.metric("Difference",    f"{diff_tb:,.4f}",
                 delta="✅ Balanced" if abs(diff_tb) < tol else "❌ Out of balance",
                 delta_color="normal" if abs(diff_tb) < tol else "inverse")


# ── Tab 6: Reconciliation ─────────────────────────────────────────────────────

with tab_recon:
    st.markdown('<div class="section-title">AOC Reconciliation (BOM + Movements = EOM)</div>',
                unsafe_allow_html=True)

    recon_df = reconcile_portfolio(aoc_list, tol)

    def flag_ok(val):
        if val is True:  return "background-color: #d1fae5; color: #065f46;"
        if val is False: return "background-color: #fee2e2; color: #991b1b;"
        return ""

    st.dataframe(
        recon_df.style
                .format({c: "{:,.2f}" for c in recon_df.select_dtypes("number").columns})
                .map(flag_ok, subset=["ok"]),
        use_container_width=True,
        height=300,
    )

    st.markdown('<div class="section-title">GL Entry Balance Check (Debits = Credits)</div>',
                unsafe_allow_html=True)
    bal_df = check_journal_balance(batches, tol)
    st.dataframe(
        bal_df.style
              .format({"total_debit": "{:,.2f}", "total_credit": "{:,.2f}", "diff": "{:,.4f}"})
              .map(flag_ok, subset=["balanced"]),
        use_container_width=True,
        height=280,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Footer — Excel export
# ──────────────────────────────────────────────────────────────────────────────

st.divider()
st.subheader("📤 Export Full Report (Excel)")

if st.button("Generate & Download Excel", type="secondary"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pl_summary(aoc_list, rca_list).to_excel(writer,      sheet_name="P&L Summary",      index=False)
        bs_summary(aoc_list, rca_list).to_excel(writer,      sheet_name="BS Summary",        index=False)
        aoc_detail(aoc_list).to_excel(writer,                sheet_name="AOC Detail",        index=False)
        gl_detail(batches).to_excel(writer,                  sheet_name="GL Entries",        index=False)
        trial_balance(batches).to_excel(writer,              sheet_name="Trial Balance",     index=False)
        reconcile_portfolio(aoc_list, tol).to_excel(writer,  sheet_name="Reconciliation",   index=False)
    buf.seek(0)
    st.download_button(
        "⬇ Download subledger_output.xlsx",
        data=buf.getvalue(),
        file_name=f"subledger_{period}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.caption("IFRS 17 Subledger Demo · Python + Streamlit · github.com/woming98/ifrs17-subledger")
