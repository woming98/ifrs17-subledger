"""
IFRS 17 — Reinsurance Treaty Engine (Proportional + Layered XL)

For XL treaties, the effective cession rate is pre-computed from the
treaty structure under a total-loss assumption:

    effective_rate = layer_size × cession_rate / sum_insured

This is appropriate for life products (term / whole life) where each
claim is either a full death benefit or zero — the distribution is
bimodal, not continuous, so LEV is not needed.

For products with partial-loss severity distributions (large medical,
catastrophe XL), effective rates should come directly from the
actuarial model (e.g. Prophet outputs PVFCF per reinsurer directly).

IFRS 17 reference: §60–70A (reinsurance contract measurement)
"""

from dataclasses import dataclass
from typing import List

import yaml

from .reinsurance import RCASummary, _build_rca


# ──────────────────────────────────────────────────────────────────────────────
# RCATreaty — treaty structure
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RCATreaty:
    """
    Single reinsurance treaty.

    Fields
    ------
    treaty_id              : unique identifier (e.g. "TERM_HAN")
    cohort_id              : direct business cohort this treaty covers
    reinsurer              : reinsurer name
    layer_label            : display label (e.g. "XL L1 (0–300k)" or "QS 20%")
    effective_cession_rate : pre-computed cession rate used for PVFCF scaling
    """
    treaty_id:              str
    cohort_id:              str
    reinsurer:              str
    layer_label:            str
    effective_cession_rate: float


# ──────────────────────────────────────────────────────────────────────────────
# Load treaty configuration from YAML
# ──────────────────────────────────────────────────────────────────────────────

def load_treaties(yaml_path: str):
    """
    Load treaty configuration from reinsurance_treaties.yaml.

    Returns
    -------
    treaties_by_cohort : dict[str, List[RCATreaty]]
    claim_dist         : dict (empty — kept for backward-compatible signature)
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    treaties_by_cohort: dict = {}
    for t in cfg.get("treaties", []):
        cid = str(t["cohort_id"])
        treaty = RCATreaty(
            treaty_id=str(t["treaty_id"]),
            cohort_id=cid,
            reinsurer=str(t["reinsurer"]),
            layer_label=str(t.get("layer_label", "")),
            effective_cession_rate=float(t["effective_cession_rate"]),
        )
        treaties_by_cohort.setdefault(cid, []).append(treaty)

    return treaties_by_cohort, {}   # empty claim_dist kept for compat


# ──────────────────────────────────────────────────────────────────────────────
# Compute RCA summaries for all treaties on a cohort
# ──────────────────────────────────────────────────────────────────────────────

def compute_rca_treaties(
    gross_aoc,
    treaties: List[RCATreaty],
    claim_dist: dict,          # unused — kept for backward-compatible signature
) -> List[RCASummary]:
    """
    Compute one RCASummary per treaty using the pre-computed effective rate.

    Each treaty scales the gross AOC by its effective_cession_rate,
    identical to a Quota Share calculation.
    """
    if not treaties:
        return []

    results: List[RCASummary] = []
    for treaty in treaties:
        rate = treaty.effective_cession_rate
        if rate <= 0.0:
            continue
        results.append(
            _build_rca(gross_aoc, rate, treaty.treaty_id,
                       treaty.reinsurer, treaty.layer_label)
        )
    return results
