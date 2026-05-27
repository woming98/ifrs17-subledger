"""
IFRS 17 Subledger — Streamlit 可视化界面

功能：
  - 上传或使用 Demo 数据
  - 完整 IFRS 17 流程：精算输入 → AOC → GL 分录 → P&L / BS / 试算平衡
  - 交互式图表：ICL 瀑布图、P&L 拆解、试算平衡表
  - 可导出 Excel

运行：
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

# ── 确保 src 可被 import ────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.models.gmm import GMMModel
from src.models.paa import PAAModel
from src.reinsurance import compute_rca
from src.subledger import ChartOfAccounts, generate_journal
from src.reconciliation import reconcile_portfolio, check_journal_balance, aoc_waterfall
from src.report import pl_summary, bs_summary, aoc_detail, gl_detail, trial_balance, export_to_excel

# ──────────────────────────────────────────────────────────────────────────────
# 页面配置
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="IFRS 17 Subledger Demo",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 自定义 CSS
st.markdown("""
<style>
  .metric-card {
    background: #f0f4f8;
    border-radius: 8px;
    padding: 16px 20px;
    border-left: 4px solid #2563eb;
    margin-bottom: 8px;
  }
  .metric-label { font-size: 0.78rem; color: #64748b; font-weight: 600; text-transform: uppercase; }
  .metric-value { font-size: 1.5rem; font-weight: 700; color: #1e293b; }
  .ok-badge  { background:#d1fae5; color:#065f46; border-radius:4px; padding:2px 8px; font-size:0.8rem; }
  .fail-badge{ background:#fee2e2; color:#991b1b; border-radius:4px; padding:2px 8px; font-size:0.8rem; }
  .section-title { font-size:1.15rem; font-weight:700; color:#1e293b; margin-top:1rem; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# 侧边栏：数据输入
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://raw.githubusercontent.com/woming98/ifrs17-subledger/main/docs/ifrs17_logo.png",
             use_column_width=True) if False else None  # 占位，有 logo 时开启
    st.title("IFRS 17 Subledger")
    st.caption("从精算输出到 GL 分录的完整流程演示")

    st.divider()
    st.subheader("📥 数据输入")

    use_demo = st.toggle("使用内置 Demo 数据", value=True)

    if use_demo:
        _data_dir = os.path.join(_ROOT, "data")
        prior_path   = os.path.join(_data_dir, "prior_period.csv")
        current_path = os.path.join(_data_dir, "current_period.csv")
        if not os.path.exists(prior_path):
            st.error("Demo 数据不存在，请先运行：\n`python data/generate_demo.py`")
            st.stop()
    else:
        prior_file   = st.file_uploader("期初余额 CSV（prior_period.csv）",   type="csv")
        current_file = st.file_uploader("期末余额+AOC CSV（current_period.csv）", type="csv")
        if prior_file is None or current_file is None:
            st.info("请上传两个 CSV 文件，或开启 Demo 模式。")
            st.stop()
        prior_path   = prior_file
        current_path = current_file

    st.divider()
    st.subheader("⚙️ 参数")
    tol = st.number_input("对账容差（'000 HKD）", value=0.01, step=0.01, format="%.3f")

    run_btn = st.button("▶ 运行 Subledger", type="primary", use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# 标题区
# ──────────────────────────────────────────────────────────────────────────────

st.title("📊 IFRS 17 Subledger — 完整流程演示")
st.markdown("""
> **演示范围**：GMM（Term Non-Par + Medical Non-Par 含 Onerous）+ PAA（短期 Medical / Rider）  
> **覆盖**：AOC 9 项拆解 · Quota Share 再保险 RCA · GL 分录借贷 · P&L / BS / 试算平衡 · Excel 导出
""")

if not run_btn:
    st.info("在左侧选择数据来源，点击「▶ 运行 Subledger」开始。")

    # 流程示意图
    st.subheader("IFRS 17 Subledger 数据流")
    st.markdown("""
```
精算系统输出（Prophet / MoSes）
    prior_period.csv   →  期初余额（BOM）
    current_period.csv →  期末余额（EOM）+ AOC 9 项拆解
           │
           ▼
  ┌─────────────────────────────────────┐
  │  IFRS 17 AOC 引擎                  │
  │  ┌──────────┐  ┌──────────────┐   │
  │  │  GMM     │  │   PAA        │   │
  │  │ (BBA)    │  │ (≤1yr / UPR) │   │
  │  └──────────┘  └──────────────┘   │
  └───────────────┬─────────────────────┘
                  │
                  ├── Quota Share RCA 计算
                  │
                  ▼
  ┌─────────────────────────────────────┐
  │  GL 分录生成器（Subledger Engine）  │
  │  账户：ICL / RCA / ISR / ISE /     │
  │       IFIE(P&L) / OCI              │
  └───────────────┬─────────────────────┘
                  │
          ┌───────┼──────────┐
          ▼       ▼          ▼
      P&L 摘要  BS 摘要  试算平衡表
      AOC 瀑布  GL 明细  Excel 导出
```
""")
    st.stop()


# ──────────────────────────────────────────────────────────────────────────────
# 核心计算（缓存）
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="计算中…")
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
            warnings.append(f"cohort {cid} 在 prior_period.csv 中找不到，已跳过。")
            continue
        bom_row = bom_matches.iloc[0]
        if model == "GMM":
            result = gmm.compute_aoc(bom_row, eom_row)
        elif model == "PAA":
            result = paa.compute_aoc(bom_row, eom_row)
        else:
            warnings.append(f"未知测量模型 '{model}'（{cid}），已跳过。")
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
# KPI 卡片
# ──────────────────────────────────────────────────────────────────────────────

period    = aoc_list[0].period if aoc_list else "N/A"
total_icl = sum(a.eom_icl for a in aoc_list)
total_rca = sum(r.rca_eom_icl for r in rca_list)
total_isr = sum(a.insurance_revenue for a in aoc_list)
total_ise = sum(a.insurance_service_expense for a in aoc_list)
total_isr_rca = sum(r.rca_insurance_revenue for r in rca_list)
net_pl        = total_isr - total_ise + sum(a.ifie_pl for a in aoc_list) + total_isr_rca
all_recon_ok  = all(a.reconciliation_ok for a in aoc_list)
all_bal_ok    = all(b.is_balanced(tol) for b in batches)

st.subheader(f"期间：{period}  |  {len(aoc_list)} 个 cohort")

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    st.metric("ICL 期末（总）", f"{total_icl:,.0f}", help="直接业务保险合同负债期末余额（'000 HKD）")
with c2:
    st.metric("RCA 期末", f"{total_rca:,.0f}", help="再保险合同资产期末余额（'000 HKD）")
with c3:
    st.metric("净 ICL", f"{total_icl - total_rca:,.0f}")
with c4:
    st.metric("保险收入（ISR）", f"{total_isr:,.0f}")
with c5:
    st.metric("净 P&L（含 RI）", f"{net_pl:,.0f}")
with c6:
    recon_ok_label = "✅ 全部通过" if all_recon_ok and all_bal_ok else "❌ 存在错误"
    st.metric("对账状态", recon_ok_label)


# ──────────────────────────────────────────────────────────────────────────────
# Tab 布局
# ──────────────────────────────────────────────────────────────────────────────

tab_aoc, tab_pl, tab_bs, tab_gl, tab_tb, tab_recon = st.tabs([
    "📈 AOC 瀑布",
    "💹 P&L 摘要",
    "🏦 资产负债表",
    "📒 GL 分录",
    "⚖️ 试算平衡",
    "✅ 对账检验",
])


# ── Tab 1: AOC 瀑布图 ─────────────────────────────────────────────────────────

with tab_aoc:
    st.markdown('<div class="section-title">ICL 变动瀑布图（组合合计）</div>', unsafe_allow_html=True)

    wf_df = aoc_waterfall(aoc_list)
    labels = wf_df["aoc_item"].tolist()
    values = wf_df["amount"].tolist()

    # 构建瀑布图数据
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
        increasing={"marker": {"color": "#ef4444"}},   # 增加负债 → 红
        decreasing={"marker": {"color": "#22c55e"}},   # 减少负债 → 绿
        totals={"marker":    {"color": "#3b82f6"}},    # 合计 → 蓝
    ))
    fig_wf.update_layout(
        title=f"ICL 变动瀑布（{period}）— 全组合合计（'000 HKD）",
        xaxis_tickangle=-30,
        height=480,
        margin=dict(l=40, r=40, t=60, b=80),
        plot_bgcolor="#fafafa",
        paper_bgcolor="#ffffff",
    )
    st.plotly_chart(fig_wf, use_container_width=True)

    # 逐 cohort 明细表
    st.markdown("**逐 cohort AOC 明细**")
    aoc_df = aoc_detail(aoc_list)
    aoc_cols = ["cohort_id","product","model","BOM ICL",
                "① 新业务首次确认","② 预期现金流释放（含 RA）",
                "③ 经验差异","④ CSM 摊销","⑤ 亏损合同 LC 回转",
                "⑥ IFIE — P&L（DAIR 展开）","⑦ IFIE — OCI（利率变动）",
                "⑧ 假设变更 → P&L","⑨ 假设变更 → CSM",
                "EOM ICL（输入）","对账差异"]
    existing_cols = [c for c in aoc_cols if c in aoc_df.columns]
    st.dataframe(aoc_df[existing_cols], use_container_width=True, height=240)


# ── Tab 2: P&L 摘要 ────────────────────────────────────────────────────────────

with tab_pl:
    st.markdown('<div class="section-title">保险服务 P&L 摘要</div>', unsafe_allow_html=True)

    pl_df = pl_summary(aoc_list, rca_list)
    st.dataframe(
        pl_df.style.format({c: "{:,.2f}" for c in pl_df.select_dtypes("number").columns}),
        use_container_width=True,
        height=280,
    )

    # 柱状图：ISR vs ISE vs IFIE vs Net（不含汇总行）
    plot_df = pl_df[pl_df["cohort_id"] != "__TOTAL__"]
    fig_pl = go.Figure()
    fig_pl.add_bar(name="保险收入 ISR", x=plot_df["cohort_id"], y=plot_df["insurance_revenue"],
                   marker_color="#22c55e")
    fig_pl.add_bar(name="保险服务费用 ISE", x=plot_df["cohort_id"], y=-plot_df["insurance_service_expense"],
                   marker_color="#ef4444")
    fig_pl.add_bar(name="IFIE P&L", x=plot_df["cohort_id"], y=plot_df["ifie_pl"],
                   marker_color="#f59e0b")
    fig_pl.add_bar(name="再保险收入（RCA）", x=plot_df["cohort_id"], y=plot_df["rca_insurance_revenue"],
                   marker_color="#6366f1")
    fig_pl.add_scatter(name="净 P&L（含 RI）", x=plot_df["cohort_id"], y=plot_df["net_pl_after_ri"],
                       mode="markers+lines", marker=dict(size=10, color="#1e293b"), line=dict(dash="dash"))
    fig_pl.update_layout(
        barmode="group",
        title=f"P&L 拆解（{period}，'000 HKD）",
        height=400,
        xaxis_tickangle=-20,
        margin=dict(l=40, r=40, t=60, b=60),
    )
    st.plotly_chart(fig_pl, use_container_width=True)


# ── Tab 3: 资产负债表 ──────────────────────────────────────────────────────────

with tab_bs:
    st.markdown('<div class="section-title">ICL / RCA 期末余额（资产负债表摘要）</div>', unsafe_allow_html=True)

    bs_df = bs_summary(aoc_list, rca_list)
    st.dataframe(
        bs_df.style.format({c: "{:,.2f}" for c in bs_df.select_dtypes("number").columns}),
        use_container_width=True,
        height=280,
    )

    # 堆叠柱：ICL 分量
    plot_bs = bs_df[bs_df["cohort_id"] != "__TOTAL__"]
    fig_bs = go.Figure()
    fig_bs.add_bar(name="PVFCF", x=plot_bs["cohort_id"], y=plot_bs["icl_pvfcf"],   marker_color="#3b82f6")
    fig_bs.add_bar(name="RA",    x=plot_bs["cohort_id"], y=plot_bs["icl_ra"],      marker_color="#60a5fa")
    fig_bs.add_bar(name="CSM",   x=plot_bs["cohort_id"], y=plot_bs["icl_csm"],     marker_color="#93c5fd")
    fig_bs.add_bar(name="LC",    x=plot_bs["cohort_id"], y=-plot_bs["icl_lc"],     marker_color="#ef4444")
    fig_bs.add_bar(name="RCA",   x=plot_bs["cohort_id"], y=plot_bs["rca_total"],   marker_color="#22c55e")
    fig_bs.update_layout(
        barmode="stack",
        title=f"ICL 分量构成（{period}，'000 HKD）",
        height=420,
        xaxis_tickangle=-20,
        margin=dict(l=40, r=40, t=60, b=60),
    )
    st.plotly_chart(fig_bs, use_container_width=True)


# ── Tab 4: GL 分录 ─────────────────────────────────────────────────────────────

with tab_gl:
    st.markdown('<div class="section-title">GL 分录明细（完整借贷记录）</div>', unsafe_allow_html=True)

    gl_df = gl_detail(batches)

    # 筛选器
    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        cohort_options = ["（全部）"] + sorted(gl_df["cohort_id"].unique().tolist())
        sel_cohort = st.selectbox("筛选 cohort", cohort_options)
    with col_filter2:
        acc_options = ["（全部）"] + sorted(gl_df["account_code"].unique().tolist())
        sel_acc = st.selectbox("筛选科目", acc_options)

    filtered = gl_df.copy()
    if sel_cohort != "（全部）":
        filtered = filtered[filtered["cohort_id"] == sel_cohort]
    if sel_acc != "（全部）":
        filtered = filtered[filtered["account_code"] == sel_acc]

    st.caption(f"显示 {len(filtered)} 行（总计 {len(gl_df)} 行）")
    st.dataframe(
        filtered.style.format({"debit": "{:,.2f}", "credit": "{:,.2f}"}),
        use_container_width=True,
        height=450,
    )

    # 下载按钮
    csv_buf = io.StringIO()
    gl_df.to_csv(csv_buf, index=False, encoding="utf-8")
    st.download_button("⬇ 下载 GL 分录 CSV", csv_buf.getvalue(),
                       file_name=f"gl_entries_{period}.csv", mime="text/csv")


# ── Tab 5: 试算平衡 ────────────────────────────────────────────────────────────

with tab_tb:
    st.markdown('<div class="section-title">试算平衡表（Trial Balance）</div>', unsafe_allow_html=True)

    tb_df = trial_balance(batches)

    # 颜色高亮：正 net = 借方余额（资产/费用）；负 net = 贷方余额（负债/收入）
    def color_net(val):
        if isinstance(val, float):
            if val > 0:   return "color: #1d4ed8;"
            elif val < 0: return "color: #dc2626;"
        return ""

    st.dataframe(
        tb_df.style
             .format({"total_debit": "{:,.2f}", "total_credit": "{:,.2f}", "net": "{:,.2f}"})
             .applymap(color_net, subset=["net"]),
        use_container_width=True,
        height=420,
    )

    total_dr = tb_df["total_debit"].sum()
    total_cr = tb_df["total_credit"].sum()
    diff_tb  = total_dr - total_cr

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("借方合计", f"{total_dr:,.2f}")
    col_b.metric("贷方合计", f"{total_cr:,.2f}")
    col_c.metric("差额", f"{diff_tb:,.4f}",
                 delta="✅ 平衡" if abs(diff_tb) < tol else "❌ 不平衡",
                 delta_color="normal" if abs(diff_tb) < tol else "inverse")


# ── Tab 6: 对账检验 ────────────────────────────────────────────────────────────

with tab_recon:
    st.markdown('<div class="section-title">AOC 对账检验（BOM + 变动 = EOM）</div>', unsafe_allow_html=True)

    recon_df = reconcile_portfolio(aoc_list, tol)

    def flag_ok(val):
        if val is True:   return "background-color: #d1fae5; color: #065f46;"
        if val is False:  return "background-color: #fee2e2; color: #991b1b;"
        return ""

    st.dataframe(
        recon_df.style
                .format({c: "{:,.2f}" for c in recon_df.select_dtypes("number").columns})
                .applymap(flag_ok, subset=["ok"]),
        use_container_width=True,
        height=300,
    )

    st.markdown('<div class="section-title">GL 分录借贷平衡检验</div>', unsafe_allow_html=True)
    bal_df = check_journal_balance(batches, tol)
    st.dataframe(
        bal_df.style
              .format({"total_debit": "{:,.2f}", "total_credit": "{:,.2f}", "diff": "{:,.4f}"})
              .applymap(flag_ok, subset=["balanced"]),
        use_container_width=True,
        height=280,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 底部：导出 Excel
# ──────────────────────────────────────────────────────────────────────────────

st.divider()
st.subheader("📤 导出完整报表（Excel）")

if st.button("生成并下载 Excel", type="secondary"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pl_summary(aoc_list, rca_list).to_excel(writer, sheet_name="P&L Summary", index=False)
        bs_summary(aoc_list, rca_list).to_excel(writer, sheet_name="BS Summary",  index=False)
        aoc_detail(aoc_list).to_excel(writer,          sheet_name="AOC Detail",   index=False)
        gl_detail(batches).to_excel(writer,             sheet_name="GL Entries",   index=False)
        trial_balance(batches).to_excel(writer,         sheet_name="Trial Balance",index=False)
        reconcile_portfolio(aoc_list, tol).to_excel(writer, sheet_name="Reconciliation", index=False)
    buf.seek(0)
    st.download_button(
        "⬇ 下载 subledger_output.xlsx",
        data=buf.getvalue(),
        file_name=f"subledger_{period}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.caption("IFRS 17 Subledger Demo · Built with Python + Streamlit · github.com/woming98/ifrs17-subledger")
