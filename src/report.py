"""
IFRS 17 — 报表生成模块

输出：
  1. P&L 摘要（保险服务结果 + IFIE）
  2. 资产负债表摘要（ICL / RCA 期末余额）
  3. GL 分录明细（完整借贷表）
  4. AOC 瀑布表（各项变动）
  5. 审计追踪（Audit Trail）：分录 ↔ AOC 项 ↔ cohort 映射
"""

from __future__ import annotations

from typing import List

import pandas as pd

from .models.base import AOCResult
from .reinsurance import RCASummary
from .subledger import JournalBatch
from .reconciliation import aoc_waterfall


# ──────────────────────────────────────────────────────────────────────────────
# P&L 摘要
# ──────────────────────────────────────────────────────────────────────────────

def pl_summary(
    aoc_list: List[AOCResult],
    rca_list: List[RCASummary] | None = None,
) -> pd.DataFrame:
    """
    生成 IFRS 17 利润表摘要（按 cohort 行展示，最后一行汇总）。

    Columns:
        cohort_id | product | model
        | insurance_revenue | insurance_service_expense | net_insurance_result
        | ifie_pl | total_pl
        | rca_isr（再保险收入/费用）| net_pl_after_ri
    """
    rows = []
    rca_by_id = {r.cohort_id: r for r in (rca_list or [])}

    for a in aoc_list:
        rca = rca_by_id.get(a.cohort_id)
        rca_isr = rca.rca_insurance_revenue if rca else 0.0
        rca_ifie = rca.rca_ifie_pl if rca else 0.0
        rows.append({
            "cohort_id":                 a.cohort_id,
            "product":                   a.product,
            "model":                     a.measurement_model,
            "insurance_revenue":         round(a.insurance_revenue, 2),
            "insurance_service_expense": round(a.insurance_service_expense, 2),
            "net_insurance_result":      round(a.net_insurance_result, 2),
            "ifie_pl":                   round(a.ifie_pl, 2),
            "total_pl_gross":            round(a.net_insurance_result + a.ifie_pl, 2),
            "rca_insurance_revenue":     round(rca_isr, 2),
            "rca_ifie_pl":               round(rca_ifie, 2),
            "net_pl_after_ri":           round(a.net_insurance_result + a.ifie_pl + rca_isr + rca_ifie, 2),
        })

    df = pd.DataFrame(rows)
    if len(df):
        total = {c: df[c].sum() if df[c].dtype in ["float64", "int64"] else "" for c in df.columns}
        total["cohort_id"] = "__TOTAL__"
        total["product"] = ""
        total["model"] = ""
        df = pd.concat([df, pd.DataFrame([total])], ignore_index=True)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 资产负债表摘要
# ──────────────────────────────────────────────────────────────────────────────

def bs_summary(
    aoc_list: List[AOCResult],
    rca_list: List[RCASummary] | None = None,
) -> pd.DataFrame:
    """
    生成 IFRS 17 资产负债表摘要（期末余额）。

    Columns:
        cohort_id | product | model
        | icl_pvfcf | icl_ra | icl_csm | icl_lc | icl_total
        | rca_pvfcf | rca_ra | rca_csm | rca_total
        | net_icl（gross - RCA）
    """
    rows = []
    rca_by_id = {r.cohort_id: r for r in (rca_list or [])}

    for a in aoc_list:
        rca = rca_by_id.get(a.cohort_id)
        rows.append({
            "cohort_id":   a.cohort_id,
            "product":     a.product,
            "model":       a.measurement_model,
            "icl_pvfcf":   round(a.eom_pvfcf, 2),
            "icl_ra":      round(a.eom_ra,    2),
            "icl_csm":     round(a.eom_csm,   2),
            "icl_lc":      round(a.eom_lc,    2),
            "icl_total":   round(a.eom_icl,   2),
            "rca_pvfcf":   round(rca.rca_eom_pvfcf if rca else 0.0, 2),
            "rca_ra":      round(rca.rca_eom_ra    if rca else 0.0, 2),
            "rca_csm":     round(rca.rca_eom_csm   if rca else 0.0, 2),
            "rca_total":   round(rca.rca_eom_icl   if rca else 0.0, 2),
            "net_icl":     round(a.eom_icl - (rca.rca_eom_icl if rca else 0.0), 2),
        })

    df = pd.DataFrame(rows)
    if len(df):
        total = {c: df[c].sum() if df[c].dtype in ["float64", "int64"] else "" for c in df.columns}
        total["cohort_id"] = "__TOTAL__"
        total["product"] = ""
        total["model"] = ""
        df = pd.concat([df, pd.DataFrame([total])], ignore_index=True)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# GL 分录明细
# ──────────────────────────────────────────────────────────────────────────────

def gl_detail(batches: List[JournalBatch]) -> pd.DataFrame:
    """
    将所有 JournalBatch 中的 JournalLine 合并为一张完整分录表。

    Columns:
        entry_id | cohort_id | period | aoc_item
        | account_code | account_name | debit | credit | currency | note
    """
    rows = []
    for b in batches:
        for line in b.lines:
            rows.append({
                "entry_id":     line.entry_id,
                "cohort_id":    line.cohort_id,
                "period":       line.period,
                "aoc_item":     line.aoc_item,
                "account_code": line.account_code,
                "account_name": line.account_name,
                "debit":        line.debit,
                "credit":       line.credit,
                "currency":     line.currency,
                "note":         line.note,
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["entry_id","cohort_id","period","aoc_item",
                 "account_code","account_name","debit","credit","currency","note"]
    )


# ──────────────────────────────────────────────────────────────────────────────
# 试算平衡表（Trial Balance）
# ──────────────────────────────────────────────────────────────────────────────

def trial_balance(batches: List[JournalBatch]) -> pd.DataFrame:
    """
    汇总各科目的借贷合计，生成试算平衡表。

    Columns: account_code | account_name | total_debit | total_credit | net
    """
    detail = gl_detail(batches)
    if detail.empty:
        return pd.DataFrame()

    tb = (
        detail.groupby(["account_code", "account_name"])
        .agg(total_debit=("debit", "sum"), total_credit=("credit", "sum"))
        .reset_index()
    )
    tb["net"] = tb["total_debit"] - tb["total_credit"]
    tb = tb.sort_values("account_code").reset_index(drop=True)
    tb["total_debit"]  = tb["total_debit"].round(2)
    tb["total_credit"] = tb["total_credit"].round(2)
    tb["net"]          = tb["net"].round(2)
    return tb


# ──────────────────────────────────────────────────────────────────────────────
# AOC 瀑布（逐 cohort + 汇总）
# ──────────────────────────────────────────────────────────────────────────────

def aoc_detail(aoc_list: List[AOCResult]) -> pd.DataFrame:
    """
    生成各 cohort 的 AOC 明细表（行 = cohort，列 = AOC 各项）。
    最后一行为组合汇总。
    """
    rows = []
    for a in aoc_list:
        row = {"cohort_id": a.cohort_id, "product": a.product, "model": a.measurement_model}
        row.update({k: round(v, 2) for k, v in a.aoc_summary().items()})
        rows.append(row)
    df = pd.DataFrame(rows)
    if len(df):
        total = {c: df[c].sum() if df[c].dtype in ["float64","int64"] else "" for c in df.columns}
        total["cohort_id"] = "__TOTAL__"
        df = pd.concat([df, pd.DataFrame([total])], ignore_index=True)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 全量报告（方便一键输出 Excel）
# ──────────────────────────────────────────────────────────────────────────────

def export_to_excel(
    aoc_list: List[AOCResult],
    rca_list: List[RCASummary],
    batches: List[JournalBatch],
    output_path: str,
) -> None:
    """
    将所有报表写入 Excel，每张报表一个 Sheet。
    """
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pl_summary(aoc_list, rca_list).to_excel(
            writer, sheet_name="P&L Summary", index=False)
        bs_summary(aoc_list, rca_list).to_excel(
            writer, sheet_name="BS Summary", index=False)
        aoc_detail(aoc_list).to_excel(
            writer, sheet_name="AOC Detail", index=False)
        gl_detail(batches).to_excel(
            writer, sheet_name="GL Entries", index=False)
        trial_balance(batches).to_excel(
            writer, sheet_name="Trial Balance", index=False)

    print(f"[report] 已写入 Excel：{output_path}")
