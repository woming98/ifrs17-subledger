"""
IFRS 17 Subledger — Multi-Period Analytics

Helper functions for building time series from multi-period AOC results.
Separated from reconciliation.py to keep module boundaries clean.
"""

from __future__ import annotations

import pandas as pd


def build_timeseries(all_results: dict) -> pd.DataFrame:
    """
    Build a long-form time series DataFrame from multi-period AOC results.

    Parameters
    ----------
    all_results : {period_str: [AOCResult, ...]}

    Returns
    -------
    DataFrame with columns:
        period | cohort_id | product | model
        | bom_icl | eom_icl | eom_pvfcf | eom_ra | eom_csm | eom_lc
        | insurance_revenue | insurance_service_expense | net_insurance_result
        | ifie_pl | underlying_items_chg
    """
    rows = []
    for period, aoc_list in sorted(all_results.items()):
        for a in aoc_list:
            rows.append({
                "period":                    period,
                "cohort_id":                 a.cohort_id,
                "product":                   a.product,
                "model":                     a.measurement_model,
                "bom_icl":                   round(a.bom_icl, 2),
                "eom_icl":                   round(a.eom_icl, 2),
                "eom_pvfcf":                 round(a.eom_pvfcf, 2),
                "eom_ra":                    round(a.eom_ra, 2),
                "eom_csm":                   round(a.eom_csm, 2),
                "eom_lc":                    round(a.eom_lc, 2),
                "insurance_revenue":         round(a.insurance_revenue, 2),
                "insurance_service_expense": round(a.insurance_service_expense, 2),
                "net_insurance_result":      round(a.net_insurance_result, 2),
                "ifie_pl":                   round(a.ifie_pl, 2),
                "underlying_items_chg":      round(a.underlying_items_chg, 2),
            })
    return pd.DataFrame(rows)


def portfolio_timeseries(ts_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate the cohort-level time series to portfolio level
    (sum across all cohorts per period).

    Parameters
    ----------
    ts_df : output of build_timeseries()

    Returns
    -------
    DataFrame indexed by period with summed numeric columns.
    """
    numeric_cols = [
        "bom_icl", "eom_icl", "eom_pvfcf", "eom_ra", "eom_csm", "eom_lc",
        "insurance_revenue", "insurance_service_expense", "net_insurance_result",
        "ifie_pl", "underlying_items_chg",
    ]
    return ts_df.groupby("period")[numeric_cols].sum().reset_index()
