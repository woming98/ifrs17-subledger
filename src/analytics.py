"""
IFRS 17 Subledger — Multi-Period Analytics

Helper functions for building time series from multi-period AOC results.
Separated from reconciliation.py to keep module boundaries clean.
"""

from __future__ import annotations

from typing import List, Dict, Any

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
        | ifie_pl | ifie_oci | csm_amortisation | underlying_items_chg
    """
    rows = []
    for period, aoc_list in sorted(all_results.items()):
        for a in aoc_list:
            rows.append({
                "period":                    period,
                "cohort_id":                 a.cohort_id,
                "product":                   a.product,
                "model":                     a.measurement_model,
                "bom_csm":                   round(a.bom_csm, 2),
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
                "ifie_oci":                  round(a.ifie_oci, 2),
                "csm_amortisation":          round(a.csm_amortisation, 2),
                "underlying_items_chg":      round(getattr(a, "underlying_items_chg", 0.0), 2),
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
        "ifie_pl", "ifie_oci", "csm_amortisation", "underlying_items_chg",
    ]
    avail = [c for c in numeric_cols if c in ts_df.columns]
    return ts_df.groupby("period")[avail].sum().reset_index()


# ──────────────────────────────────────────────────────────────────────────────
# CSM Run-off Projection
# ──────────────────────────────────────────────────────────────────────────────

def project_csm_runoff(
    all_results: dict,
    n_quarters: int = 60,
    und_growth_rate: float = 0.005,
) -> List[Dict[str, Any]]:
    """
    Project the CSM run-off (amortisation glide path) for each cohort.

    Uses historical quarterly CSM amortisation rate (derived from the 4-quarter
    demo data) to project the CSM balance forward.

    For VFA cohorts, an additional 'Bull / Bear' scenario applies the
    underlying items growth assumption to the CSM.

    Parameters
    ----------
    all_results    : {period_str: [AOCResult, ...]}
    n_quarters     : projection horizon (default 60 = 15 years)
    und_growth_rate: quarterly VFA underlying growth rate for the bull scenario

    Returns
    -------
    List of dicts, one per cohort with CSM > 0:
        cohort_id | model | closing_csm | quarterly_rate | annual_rate
        | projections: [{label, csm, isr_from_csm}, ...]
        | bull_projections (VFA only)
    """
    periods = sorted(all_results.keys())
    if not periods:
        return []

    # Per-cohort historical quarterly amortisation rate
    cohort_data: Dict[str, list] = {}
    for p in periods:
        for r in all_results[p]:
            cohort_data.setdefault(r.cohort_id, []).append(r)

    last_p   = periods[-1]
    last_aoc = all_results[last_p]

    _QUARTER_LABELS = [
        f"{2024 + (i + 4) // 4}Q{((i + 4) % 4) + 1}" for i in range(n_quarters)
    ]

    results = []
    for r in last_aoc:
        if r.eom_csm <= 0:
            continue

        hist = cohort_data.get(r.cohort_id, [r])

        # Quarterly amortisation rate = -Σ(csm_amortisation) / Σ(bom_csm)
        total_amort  = sum(h.csm_amortisation for h in hist)   # negative
        total_bom    = sum(h.bom_csm for h in hist if h.bom_csm > 0)
        if total_bom > 1:
            q_rate = min(-total_amort / total_bom, 0.20)       # cap at 20%/qtr
        else:
            q_rate = 0.04  # fallback 4%/qtr ≈ 15% annual

        # VFA: also compute underlying-items effect (average per quarter)
        und_avg = 0.0
        if r.measurement_model == "VFA":
            und_total = sum(getattr(h, "underlying_items_chg", 0.0) for h in hist)
            und_avg   = und_total / len(hist)   # avg quarterly underlying Δ

        # Base projection
        base_proj = []
        csm = r.eom_csm
        for i, lbl in enumerate(_QUARTER_LABELS):
            amort = -csm * q_rate
            csm = max(0.0, csm + amort)
            base_proj.append({"label": lbl, "csm": round(csm, 2),
                               "isr_from_csm": round(-amort, 2)})
            if csm < 0.5:
                break

        # Bull scenario (VFA only): underlying items grow at und_growth_rate / quarter
        bull_proj = []
        if r.measurement_model == "VFA":
            csm_b = r.eom_csm
            for i, lbl in enumerate(_QUARTER_LABELS):
                und_gain = csm_b * und_growth_rate     # +ve: market gain
                amort_b  = -csm_b * q_rate
                csm_b    = max(0.0, csm_b + amort_b + und_gain)
                bull_proj.append({"label": lbl, "csm": round(csm_b, 2),
                                   "isr_from_csm": round(-amort_b, 2)})
                if csm_b < 0.5:
                    break

            # Bear scenario (VFA): underlying items shrink
            bear_proj = []
            csm_bear = r.eom_csm
            for i, lbl in enumerate(_QUARTER_LABELS):
                und_loss = -csm_bear * und_growth_rate
                amort_bear = -csm_bear * q_rate
                csm_bear   = max(0.0, csm_bear + amort_bear + und_loss)
                bear_proj.append({"label": lbl, "csm": round(csm_bear, 2),
                                   "isr_from_csm": round(-amort_bear, 2)})
                if csm_bear < 0.5:
                    break
        else:
            bull_proj = []
            bear_proj = []

        results.append({
            "cohort_id":      r.cohort_id,
            "product":        r.product,
            "model":          r.measurement_model,
            "closing_csm":    round(r.eom_csm, 2),
            "quarterly_rate": round(q_rate, 4),
            "annual_rate":    round(1 - (1 - q_rate) ** 4, 4),
            "projections":    base_proj,
            "bull_proj":      bull_proj,
            "bear_proj":      bear_proj,
        })

    return results
