"""
IFRS 17 Subledger — Disclosure Notes Generator

生成 IFRS 17 标准财务报表附注，包含：
  Note 1 : ICL Movement Table      — Insurance Contract Liabilities rollforward
  Note 2 : ICL Balance by Component — PVFCF / RA / CSM / LC opening vs closing
  Note 3 : Analysis of Insurance Revenue
  Note 4 : Insurance Finance Income/Expense (IFIE)
  Note 5 : Reinsurance Contract Assets Movement
  Note 6 : Maturity Profile (simplified undiscounted CF bucketing)

参考准则：IFRS 17.97-100 (quantitative disclosures), IFRS 17.100A (rollforward)
"""

from typing import Dict, List

import pandas as pd

from src.models.base import AOCResult
from src.reinsurance import RCASummary


# ──────────────────────────────────────────────────────────────────────────────
# 内部聚合工具
# ──────────────────────────────────────────────────────────────────────────────

def _sum_field(results: List[AOCResult], field: str) -> float:
    return sum(getattr(r, field, 0.0) for r in results)


def _sum_rca_field(rcas: List[RCASummary], field: str) -> float:
    return sum(getattr(r, field, 0.0) for r in rcas)


def _agg_by_model(results: List[AOCResult], field: str) -> Dict[str, float]:
    out = {"GMM": 0.0, "VFA": 0.0, "PAA": 0.0, "Total": 0.0}
    for r in results:
        m = r.measurement_model if r.measurement_model in out else "GMM"
        v = getattr(r, field, 0.0)
        out[m] += v
        out["Total"] += v
    return out


def _und_by_model(results: List[AOCResult]) -> Dict[str, float]:
    """VFA underlying items change"""
    out = {"GMM": 0.0, "VFA": 0.0, "PAA": 0.0, "Total": 0.0}
    for r in results:
        v = getattr(r, "underlying_items_chg", 0.0)
        out["VFA"] += v
        out["Total"] += v
    return out


def _fmt(x: float) -> float:
    return round(x, 1)


# ──────────────────────────────────────────────────────────────────────────────
# Note 1 — ICL Movement Table (full year, by AOC line item × model)
# ──────────────────────────────────────────────────────────────────────────────

def note1_icl_movement(
    all_results: Dict[str, List[AOCResult]],
    year: str = "2024",
) -> pd.DataFrame:
    """
    Note 1 : Insurance Contract Liabilities — Movement in {year}

    Returns a DataFrame with:
      rows  = AOC line items (opening, movements, closing)
      cols  = GMM | VFA | PAA | Total

    all_results : {period_str: [AOCResult, ...]}
    """
    periods = sorted(k for k in all_results if k.startswith(year))
    if not periods:
        return pd.DataFrame()

    all_period_results: List[AOCResult] = []
    for p in periods:
        all_period_results.extend(all_results[p])

    # Opening = BOM of the very first period
    first_period_results = all_results[periods[0]]

    def opening(field: str) -> Dict[str, float]:
        out = {"GMM": 0.0, "VFA": 0.0, "PAA": 0.0, "Total": 0.0}
        for r in first_period_results:
            m = r.measurement_model if r.measurement_model in out else "GMM"
            v = getattr(r, field, 0.0)
            out[m] += v
            out["Total"] += v
        return out

    # Closing = EOM of the last period
    last_period_results = all_results[periods[-1]]

    def closing(field: str) -> Dict[str, float]:
        out = {"GMM": 0.0, "VFA": 0.0, "PAA": 0.0, "Total": 0.0}
        for r in last_period_results:
            m = r.measurement_model if r.measurement_model in out else "GMM"
            v = getattr(r, field, 0.0)
            out[m] += v
            out["Total"] += v
        return out

    def mv(field: str) -> Dict[str, float]:
        return _agg_by_model(all_period_results, field)

    # Opening ICL = PVFCF + RA + CSM - LC
    open_icl = {
        k: opening("bom_pvfcf")[k] + opening("bom_ra")[k]
           + opening("bom_csm")[k] - opening("bom_lc")[k]
        for k in ["GMM", "VFA", "PAA", "Total"]
    }
    close_icl = {
        k: closing("eom_pvfcf")[k] + closing("eom_ra")[k]
           + closing("eom_csm")[k] - closing("eom_lc")[k]
        for k in ["GMM", "VFA", "PAA", "Total"]
    }

    # 构建行
    rows = []

    def _row(label: str, d: Dict[str, float], indent: int = 0) -> dict:
        prefix = "    " * indent
        return {
            "Movement": prefix + label,
            "GMM":   _fmt(d["GMM"]),
            "VFA":   _fmt(d["VFA"]),
            "PAA":   _fmt(d["PAA"]),
            "Total": _fmt(d["Total"]),
        }

    rows.append(_row(f"Opening Balance (1 Jan {year})", open_icl))

    # ── Insurance Service ──────────────────────────────────────────────────
    rows.append({"Movement": "─── Insurance Service", "GMM": "", "VFA": "", "PAA": "", "Total": ""})

    rows.append(_row("① New Business", mv("new_business"), indent=1))
    rows.append(_row("② Expected CF Release (incl. RA release)", mv("expected_cf_release"), indent=1))
    rows.append(_row("③ Experience Variance", mv("experience_variance"), indent=1))
    rows.append(_row("④ CSM Amortisation", mv("csm_amortisation"), indent=1))
    rows.append(_row("⑤ LC Reversal / Additional LC", mv("lc_reversal"), indent=1))
    rows.append(_row("⑧ Assumption Changes → P&L", mv("assumption_chg_pl"), indent=1))
    rows.append(_row("⑨ Assumption Changes → CSM  (intra-ICL, net = 0)", mv("assumption_chg_csm"), indent=1))

    # VFA underlying items change (intra-ICL)
    und = _und_by_model(all_period_results)
    rows.append(_row("VFA: Underlying Items Change → CSM  (intra-ICL, net = 0)", und, indent=1))

    # Sub-total Insurance Service
    svc_sub = {
        k: (mv("new_business")[k]
            + mv("expected_cf_release")[k]
            + mv("experience_variance")[k]
            + mv("csm_amortisation")[k]
            + mv("lc_reversal")[k]
            + mv("assumption_chg_pl")[k])
        for k in ["GMM", "VFA", "PAA", "Total"]
    }
    rows.append(_row("Sub-total — Insurance Service", svc_sub))

    # ── Insurance Finance ──────────────────────────────────────────────────
    rows.append({"Movement": "─── Insurance Finance", "GMM": "", "VFA": "", "PAA": "", "Total": ""})

    rows.append(_row("⑥ IFIE — P&L (locked-in DAIR unwind)", mv("finance_charge_pl"), indent=1))
    rows.append(_row("⑦ IFIE — OCI (current rate vs DAIR)", mv("finance_charge_oci"), indent=1))

    fin_sub = {
        k: mv("finance_charge_pl")[k] + mv("finance_charge_oci")[k]
        for k in ["GMM", "VFA", "PAA", "Total"]
    }
    rows.append(_row("Sub-total — Insurance Finance", fin_sub))

    # ── FX / Other ────────────────────────────────────────────────────────
    rows.append(_row("FX / Other Effects", mv("fx_effect")))

    rows.append({"Movement": "─────────────────────", "GMM": "", "VFA": "", "PAA": "", "Total": ""})
    rows.append(_row(f"Closing Balance (31 Dec {year})", close_icl))

    net = {k: close_icl[k] - open_icl[k] for k in ["GMM", "VFA", "PAA", "Total"]}
    rows.append(_row("  Net Change", net))

    return pd.DataFrame(rows).set_index("Movement")


# ──────────────────────────────────────────────────────────────────────────────
# Note 2 — ICL Balance by Component
# ──────────────────────────────────────────────────────────────────────────────

def note2_icl_components(
    all_results: Dict[str, List[AOCResult]],
    year: str = "2024",
) -> pd.DataFrame:
    """
    Note 2 : ICL Balance Sheet Components (PVFCF / RA / CSM / LC)

    Shows opening and closing balances for each ICL component,
    broken down by measurement model.
    """
    periods = sorted(k for k in all_results if k.startswith(year))
    if not periods:
        return pd.DataFrame()

    first = all_results[periods[0]]
    last  = all_results[periods[-1]]
    models = ["GMM", "VFA", "PAA"]

    rows = []
    for m in models:
        m_open = [r for r in first if r.measurement_model == m]
        m_close = [r for r in last  if r.measurement_model == m]

        def s(lst, f): return sum(getattr(r, f, 0.0) for r in lst)

        pvfcf_o  = s(m_open,  "bom_pvfcf");    pvfcf_c  = s(m_close, "eom_pvfcf")
        ra_o     = s(m_open,  "bom_ra");        ra_c     = s(m_close, "eom_ra")
        csm_o    = s(m_open,  "bom_csm");       csm_c    = s(m_close, "eom_csm")
        lc_o     = s(m_open,  "bom_lc");        lc_c     = s(m_close, "eom_lc")
        icl_o    = pvfcf_o + ra_o + csm_o - lc_o
        icl_c    = pvfcf_c + ra_c + csm_c - lc_c

        rows.append({"Model": m, "Component": "PVFCF",    "Opening": _fmt(pvfcf_o), "Closing": _fmt(pvfcf_c), "Change": _fmt(pvfcf_c - pvfcf_o)})
        rows.append({"Model": m, "Component": "RA",       "Opening": _fmt(ra_o),    "Closing": _fmt(ra_c),    "Change": _fmt(ra_c - ra_o)})
        rows.append({"Model": m, "Component": "CSM",      "Opening": _fmt(csm_o),   "Closing": _fmt(csm_c),   "Change": _fmt(csm_c - csm_o)})
        rows.append({"Model": m, "Component": "LC",       "Opening": _fmt(-lc_o),   "Closing": _fmt(-lc_c),   "Change": _fmt(-lc_c + lc_o)})
        rows.append({"Model": m, "Component": "Total ICL","Opening": _fmt(icl_o),   "Closing": _fmt(icl_c),   "Change": _fmt(icl_c - icl_o)})

    # Portfolio total
    def sp(lst, f): return sum(getattr(r, f, 0.0) for r in lst)
    pvfcf_o  = sp(first, "bom_pvfcf");   pvfcf_c  = sp(last, "eom_pvfcf")
    ra_o     = sp(first, "bom_ra");       ra_c     = sp(last, "eom_ra")
    csm_o    = sp(first, "bom_csm");      csm_c    = sp(last, "eom_csm")
    lc_o     = sp(first, "bom_lc");       lc_c     = sp(last, "eom_lc")
    icl_o    = pvfcf_o + ra_o + csm_o - lc_o
    icl_c    = pvfcf_c + ra_c + csm_c - lc_c
    rows.append({"Model": "TOTAL", "Component": "PVFCF",    "Opening": _fmt(pvfcf_o), "Closing": _fmt(pvfcf_c), "Change": _fmt(pvfcf_c - pvfcf_o)})
    rows.append({"Model": "TOTAL", "Component": "RA",       "Opening": _fmt(ra_o),    "Closing": _fmt(ra_c),    "Change": _fmt(ra_c - ra_o)})
    rows.append({"Model": "TOTAL", "Component": "CSM",      "Opening": _fmt(csm_o),   "Closing": _fmt(csm_c),   "Change": _fmt(csm_c - csm_o)})
    rows.append({"Model": "TOTAL", "Component": "LC",       "Opening": _fmt(-lc_o),   "Closing": _fmt(-lc_c),   "Change": _fmt(-lc_c + lc_o)})
    rows.append({"Model": "TOTAL", "Component": "Total ICL","Opening": _fmt(icl_o),   "Closing": _fmt(icl_c),   "Change": _fmt(icl_c - icl_o)})

    df = pd.DataFrame(rows)
    return df.set_index(["Model", "Component"])


# ──────────────────────────────────────────────────────────────────────────────
# Note 3 — Analysis of Insurance Revenue
# ──────────────────────────────────────────────────────────────────────────────

def note3_insurance_revenue(
    all_results: Dict[str, List[AOCResult]],
    year: str = "2024",
) -> pd.DataFrame:
    """
    Note 3 : Analysis of Insurance Revenue (IFRS 17.83-85)

    Insurance Revenue = release of the ICL relating to the provision of
    coverage in the period (Expected CF release + RA release + CSM amort + LC reversal)
    """
    periods = sorted(k for k in all_results if k.startswith(year))
    if not periods:
        return pd.DataFrame()

    all_p: List[AOCResult] = []
    for p in periods:
        all_p.extend(all_results[p])

    def by_model(field: str) -> dict:
        return _agg_by_model(all_p, field)

    # Expected CF release is negative (reduces liability) → revenue is positive
    ecf   = by_model("expected_cf_release")
    csma  = by_model("csm_amortisation")
    lcr   = by_model("lc_reversal")

    rows = []
    for m in ["GMM", "VFA", "PAA", "Total"]:
        # Revenue items are negative in AOC (liability decreases = income)
        # Flip sign to show as positive revenue
        ecf_rev  = _fmt(-ecf[m])      # Expected CF + RA release
        csm_rev  = _fmt(-csma[m])     # CSM amortisation → revenue
        # LC reversal: negative lc_reversal = LC released → revenue
        # Positive lc_reversal = additional LC recognised → extra ISE (not revenue)
        lc_neg  = min(lcr[m], 0.0)    # only negative (release) part
        lc_pos  = max(lcr[m], 0.0)    # positive (additional LC) part → ISE
        lc_rev_amt = _fmt(-lc_neg)    # flip sign for revenue
        lc_ise_amt = _fmt(lc_pos)     # positive = additional loss → ISE

        total_rev = _fmt(ecf_rev + csm_rev + lc_rev_amt)
        rows.append({
            "Model":                                   m,
            "Expected CF Release (incl. RA)":         ecf_rev,
            "CSM Amortisation":                       csm_rev,
            "LC Release (service delivery)":          lc_rev_amt,
            "Total Insurance Revenue (ISR)":          total_rev,
            "— Additional LC Recognised (ISE)":       lc_ise_amt,
        })

    df = pd.DataFrame(rows).set_index("Model")
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Note 4 — Insurance Finance Income / Expense (IFIE)
# ──────────────────────────────────────────────────────────────────────────────

def note4_ifie(
    all_results: Dict[str, List[AOCResult]],
    year: str = "2024",
) -> pd.DataFrame:
    """
    Note 4 : Insurance Finance Income and Expense (IFRS 17.88-92)

    Shows P&L portion (DAIR unwind) and OCI portion (rate change effect),
    by measurement model.
    """
    periods = sorted(k for k in all_results if k.startswith(year))
    if not periods:
        return pd.DataFrame()

    all_p: List[AOCResult] = []
    for p in periods:
        all_p.extend(all_results[p])

    rows = []
    for m in ["GMM", "VFA", "PAA", "Total"]:
        fi_pl  = _agg_by_model(all_p, "finance_charge_pl")[m]
        fi_oci = _agg_by_model(all_p, "finance_charge_oci")[m]
        rows.append({
            "Model":                                  m,
            "IFIE — P&L  (locked-in DAIR unwind)":   _fmt(fi_pl),
            "IFIE — OCI  (current rate vs DAIR)":     _fmt(fi_oci),
            "Total IFIE":                             _fmt(fi_pl + fi_oci),
        })

    return pd.DataFrame(rows).set_index("Model")


# ──────────────────────────────────────────────────────────────────────────────
# Note 5 — Reinsurance Contract Assets Movement
# ──────────────────────────────────────────────────────────────────────────────

def note5_rca_movement(
    all_rca: List[RCASummary],
    year: str = "2024",
) -> pd.DataFrame:
    """
    Note 5 : Reinsurance Contract Assets (RCA) — Movement in {year}

    Mirror of Note 1 but for the ceded reinsurance side.
    RCA is an asset, so positive = asset balance.
    """
    rca_y = [r for r in all_rca if r.period.startswith(year)]
    if not rca_y:
        return pd.DataFrame()

    # Opening BOM: earliest period per cohort
    earliest_period = sorted(set(r.period for r in rca_y))[0]
    open_rcas = [r for r in rca_y if r.period == earliest_period]
    last_period = sorted(set(r.period for r in rca_y))[-1]
    close_rcas = [r for r in rca_y if r.period == last_period]

    # RCA is an ASSET → flip sign for disclosure (internal sign = negative)
    def s_bom(rcas):  return -sum(r.rca_bom_icl for r in rcas)
    def s_eom(rcas):  return -sum(r.rca_eom_icl for r in rcas)
    def s_mv(field):  return -_sum_rca_field(rca_y, field)

    open_total  = _fmt(s_bom(open_rcas))
    close_total = _fmt(s_eom(close_rcas))

    # Aggregate by reinsurer
    reinsurers = sorted(set(r.reinsurer for r in rca_y if r.reinsurer not in ("Unknown",)))
    if not reinsurers:
        reinsurers = ["All Reinsurers"]

    rows = []
    def _row(lbl, val, indent=0):
        return {"Movement": "    " * indent + lbl, "Amount ('000 HKD)": _fmt(val)}

    rows.append({"Movement": f"Opening RCA Balance (1 Jan {year})", "Amount ('000 HKD)": open_total})
    rows.append({"Movement": "─── Insurance Service", "Amount ('000 HKD)": ""})
    rows.append(_row("① New Business", s_mv("rca_new_business"), indent=1))
    rows.append(_row("② Expected CF Release", s_mv("rca_expected_cf_release"), indent=1))
    rows.append(_row("③ Experience Variance", s_mv("rca_experience_variance"), indent=1))
    rows.append(_row("④ CSM Amortisation", s_mv("rca_csm_amortisation"), indent=1))
    rows.append(_row("⑧ Assumption Changes → P&L", s_mv("rca_assumption_chg_pl"), indent=1))
    svc_sub = (s_mv("rca_new_business") + s_mv("rca_expected_cf_release")
               + s_mv("rca_experience_variance") + s_mv("rca_csm_amortisation")
               + s_mv("rca_assumption_chg_pl"))
    rows.append(_row("Sub-total — Insurance Service", svc_sub))
    rows.append({"Movement": "─── Insurance Finance", "Amount ('000 HKD)": ""})
    rows.append(_row("⑥ IFIE — P&L", s_mv("rca_finance_charge_pl"), indent=1))
    rows.append(_row("⑦ IFIE — OCI", s_mv("rca_finance_charge_oci"), indent=1))
    rows.append(_row("Sub-total — Insurance Finance",
                     s_mv("rca_finance_charge_pl") + s_mv("rca_finance_charge_oci")))
    rows.append(_row("FX / Other", s_mv("rca_fx_effect")))
    rows.append({"Movement": "─────────────────────", "Amount ('000 HKD)": ""})
    rows.append({"Movement": f"Closing RCA Balance (31 Dec {year})", "Amount ('000 HKD)": close_total})
    rows.append({"Movement": "  Net Change", "Amount ('000 HKD)": _fmt(close_total - open_total)})

    # ── By reinsurer breakdown (asset sign = positive) ────────────────────
    rows.append({"Movement": "─── Closing Balance by Reinsurer", "Amount ('000 HKD)": ""})
    reinsurer_close: dict = {}
    for r in close_rcas:
        reinsurer_close[r.reinsurer] = reinsurer_close.get(r.reinsurer, 0.0) + (-r.rca_eom_icl)
    for rn, amt in sorted(reinsurer_close.items(), key=lambda x: -abs(x[1])):
        rows.append({"Movement": f"    {rn}", "Amount ('000 HKD)": _fmt(amt)})

    return pd.DataFrame(rows).set_index("Movement")


# ──────────────────────────────────────────────────────────────────────────────
# Note 6 — Maturity Profile (Undiscounted Future Cash Flows)
# ──────────────────────────────────────────────────────────────────────────────

# Duration assumptions by model (years remaining on average)
_DURATION = {"GMM": 8.0, "VFA": 15.0, "PAA": 0.5}

# Cash flow buckets (IFRS 17.132(b))
_BUCKETS = [
    ("< 1 year",   0, 1),
    ("1 – 3 years", 1, 3),
    ("3 – 5 years", 3, 5),
    ("> 5 years",   5, 999),
]


def note6_maturity_profile(
    all_results: Dict[str, List[AOCResult]],
    year: str = "2024",
    discount_adjustment: float = 1.12,
) -> pd.DataFrame:
    """
    Note 6 : Maturity Profile — Undiscounted Future Cash Flows (IFRS 17.132)

    Uses simplified uniform distribution over each cohort's expected duration.
    The undiscounted PVFCF = PVFCF × discount_adjustment (approximate).

    Parameters
    ----------
    discount_adjustment : rough undiscounting factor (1 + avg_rate × avg_duration / 2)
    """
    periods = sorted(k for k in all_results if k.startswith(year))
    if not periods:
        return pd.DataFrame()

    last_results = all_results[periods[-1]]

    rows = []
    totals = {b: 0.0 for b, _, _ in _BUCKETS}
    totals["Grand Total"] = 0.0

    models_seen = {}
    for r in last_results:
        m = r.measurement_model
        dur = _DURATION.get(m, 8.0)
        # Approximate undiscounted amount
        undiscounted_pvfcf = r.eom_pvfcf * discount_adjustment
        per_year = undiscounted_pvfcf / max(dur, 0.5)

        bucket_amts = {}
        for bname, bstart, bend in _BUCKETS:
            # Years falling within this bucket (capped to duration)
            yrs_in = max(0.0, min(bend, dur) - bstart)
            amt = per_year * yrs_in
            bucket_amts[bname] = _fmt(amt)
            totals[bname] += amt

        bucket_amts["Grand Total"] = _fmt(undiscounted_pvfcf)
        totals["Grand Total"] += undiscounted_pvfcf

        row = {
            "Cohort": r.cohort_id,
            "Product": r.product,
            "Model": m,
            "Duration (yrs)": dur,
        }
        row.update(bucket_amts)
        rows.append(row)

    # Totals row
    total_row = {
        "Cohort": "TOTAL",
        "Product": "",
        "Model": "",
        "Duration (yrs)": "",
    }
    for bname, _, _ in _BUCKETS:
        total_row[bname] = _fmt(totals[bname])
    total_row["Grand Total"] = _fmt(totals["Grand Total"])
    rows.append(total_row)

    df = pd.DataFrame(rows)
    return df.set_index(["Cohort", "Product", "Model"])


# ──────────────────────────────────────────────────────────────────────────────
# Cohort-level ICL rollforward (per-cohort detail)
# ──────────────────────────────────────────────────────────────────────────────

def note1_cohort_detail(
    all_results: Dict[str, List[AOCResult]],
    year: str = "2024",
) -> pd.DataFrame:
    """
    Detailed ICL rollforward per cohort (supplement to Note 1).

    Returns DataFrame:
      index  = cohort_id
      cols   = Opening ICL | Various AOC movements | Closing ICL | Model
    """
    periods = sorted(k for k in all_results if k.startswith(year))
    if not periods:
        return pd.DataFrame()

    first_p = all_results[periods[0]]
    last_p  = all_results[periods[-1]]

    # Map cohort → all periods' AOCResult
    cohort_results: Dict[str, List[AOCResult]] = {}
    for p in periods:
        for r in all_results[p]:
            cohort_results.setdefault(r.cohort_id, []).append(r)

    rows = []
    for cid, cresults in sorted(cohort_results.items()):
        r0 = cresults[0]   # first quarter
        rN = cresults[-1]  # last quarter

        # Opening
        open_icl = r0.bom_pvfcf + r0.bom_ra + r0.bom_csm - r0.bom_lc
        close_icl = rN.eom_pvfcf + rN.eom_ra + rN.eom_csm - rN.eom_lc

        def sm(field): return sum(getattr(r, field, 0.0) for r in cresults)

        isr  = -(sm("expected_cf_release") + sm("csm_amortisation"))
        ise  = -(sm("experience_variance"))
        lc_r = sm("lc_reversal")
        fi   = sm("finance_charge_pl") + sm("finance_charge_oci")
        aspl = sm("assumption_chg_pl")
        ascsm= sm("assumption_chg_csm")

        rows.append({
            "Cohort":          cid,
            "Product":         r0.product,
            "Model":           r0.measurement_model,
            "Opening ICL":     _fmt(open_icl),
            "ISR Sources":     _fmt(-(sm("expected_cf_release") + sm("csm_amortisation")
                                       + min(lc_r, 0.0))),
            "ISE (Exp. Var)":  _fmt(sm("experience_variance")),
            "Addl LC (ISE)":   _fmt(max(lc_r, 0.0)),
            "IFIE":            _fmt(fi),
            "Assumption Δ":    _fmt(aspl),
            "Closing ICL":     _fmt(close_icl),
            "Net Change":      _fmt(close_icl - open_icl),
            "Closing LC":      _fmt(rN.eom_lc),
            "Closing CSM":     _fmt(rN.eom_csm),
        })

    return pd.DataFrame(rows).set_index("Cohort")
