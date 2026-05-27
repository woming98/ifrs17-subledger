"""
IFRS 17 — 对账检验模块

功能：
  1. 单 cohort 对账：BOM ICL + AOC 合计 = EOM ICL
  2. 全组合汇总对账：跨 cohort 检验
  3. GL 分录借贷平衡检验
  4. 生成对账报告（DataFrame 格式）
"""

from __future__ import annotations

from typing import List

import pandas as pd

from .models.base import AOCResult
from .subledger import JournalBatch


# ──────────────────────────────────────────────────────────────────────────────
# 单 cohort 对账
# ──────────────────────────────────────────────────────────────────────────────

def reconcile_cohort(aoc: AOCResult, tol: float = 0.01) -> dict:
    """
    检验单个 cohort 的 BOM + 变动 = EOM 对账关系。

    Returns
    -------
    dict : {
        "cohort_id"    : str,
        "period"       : str,
        "bom_icl"      : float,
        "total_movements" : float,
        "eom_icl_calc" : float,   # BOM + 合计
        "eom_icl_input": float,   # 来自 CSV 的 EOM 余额
        "diff"         : float,   # calc - input
        "ok"           : bool,
    }
    """
    calc_eom = aoc.bom_icl + aoc.total_movements
    diff     = calc_eom - aoc.eom_icl
    return {
        "cohort_id":       aoc.cohort_id,
        "product":         aoc.product,
        "measurement_model": aoc.measurement_model,
        "period":          aoc.period,
        "bom_icl":         round(aoc.bom_icl, 2),
        "total_movements": round(aoc.total_movements, 2),
        "eom_icl_calc":    round(calc_eom, 2),
        "eom_icl_input":   round(aoc.eom_icl, 2),
        "diff":            round(diff, 4),
        "ok":              abs(diff) <= tol,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 全组合汇总对账
# ──────────────────────────────────────────────────────────────────────────────

def reconcile_portfolio(
    aoc_list: List[AOCResult],
    tol: float = 0.01,
) -> pd.DataFrame:
    """
    对所有 cohort 进行对账检验，返回汇总 DataFrame。

    Columns:
        cohort_id | product | measurement_model | period
        | bom_icl | total_movements | eom_icl_calc | eom_icl_input | diff | ok
    """
    rows = [reconcile_cohort(a, tol) for a in aoc_list]
    df   = pd.DataFrame(rows)

    # 汇总行
    summary_row = {
        "cohort_id": "__TOTAL__",
        "product": "",
        "measurement_model": "",
        "period": df["period"].iloc[0] if len(df) else "",
        "bom_icl":         df["bom_icl"].sum(),
        "total_movements": df["total_movements"].sum(),
        "eom_icl_calc":    df["eom_icl_calc"].sum(),
        "eom_icl_input":   df["eom_icl_input"].sum(),
        "diff":            round(df["diff"].sum(), 4),
        "ok":              df["ok"].all(),
    }
    df = pd.concat([df, pd.DataFrame([summary_row])], ignore_index=True)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# GL 分录借贷平衡检验
# ──────────────────────────────────────────────────────────────────────────────

def check_journal_balance(batches: List[JournalBatch], tol: float = 0.01) -> pd.DataFrame:
    """
    检验每个分录批的借贷合计是否平衡。

    Returns
    -------
    DataFrame with columns: cohort_id | period | total_debit | total_credit | diff | balanced
    """
    rows = []
    for b in batches:
        dr = b.total_debits()
        cr = b.total_credits()
        rows.append({
            "cohort_id":    b.cohort_id,
            "period":       b.period,
            "total_debit":  round(dr, 2),
            "total_credit": round(cr, 2),
            "diff":         round(dr - cr, 4),
            "balanced":     b.is_balanced(tol),
        })

    df = pd.DataFrame(rows)
    if len(df):
        all_row = {
            "cohort_id":    "__ALL__",
            "period":       df["period"].iloc[0],
            "total_debit":  df["total_debit"].sum(),
            "total_credit": df["total_credit"].sum(),
            "diff":         round(df["total_debit"].sum() - df["total_credit"].sum(), 4),
            "balanced":     df["balanced"].all(),
        }
        df = pd.concat([df, pd.DataFrame([all_row])], ignore_index=True)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# AOC 汇总瀑布（用于报表）
# ──────────────────────────────────────────────────────────────────────────────

def aoc_waterfall(aoc_list: List[AOCResult]) -> pd.DataFrame:
    """
    将所有 cohort 的 AOC 各项合计，生成组合级瀑布表。

    Returns
    -------
    DataFrame with columns: aoc_item | amount
    """
    items = [
        ("BOM ICL",                         lambda a: a.bom_icl),
        ("① 新业务首次确认",               lambda a: a.new_business),
        ("② 预期现金流释放（含 RA）",       lambda a: a.expected_cf_release),
        ("③ 经验差异",                      lambda a: a.experience_variance),
        ("④ CSM 摊销",                      lambda a: a.csm_amortisation),
        ("⑤ 亏损合同 LC 回转",             lambda a: a.lc_reversal),
        ("⑥ IFIE — P&L（DAIR 展开）",     lambda a: a.finance_charge_pl),
        ("⑦ IFIE — OCI（利率变动）",       lambda a: a.finance_charge_oci),
        ("⑧ 假设变更 → P&L",              lambda a: a.assumption_chg_pl),
        ("⑨ 假设变更 → CSM",              lambda a: a.assumption_chg_csm),
        ("汇率影响",                        lambda a: a.fx_effect),
        ("EOM ICL（输入）",                 lambda a: a.eom_icl),
    ]
    rows = []
    for label, fn in items:
        total = sum(fn(a) for a in aoc_list)
        rows.append({"aoc_item": label, "amount": round(total, 2)})
    return pd.DataFrame(rows)
