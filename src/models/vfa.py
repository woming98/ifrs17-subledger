"""
IFRS 17 — Variable Fee Approach (VFA) AOC Engine

VFA applies to participating contracts where the insurer acts as "principal"
managing underlying items on behalf of policyholders. Typical products:
  - Whole Life Participating (with-profit)
  - Endowment Participating

Key difference from GMM (IFRS 17.45A–45D):
  - CSM is linked to the fair value of underlying items (investment portfolio)
  - Changes in underlying items → adjust CSM (not P&L or OCI)
  - Experience variances relating to underlying items → adjust CSM (not ISE)
  - Only residual finance charge (net of underlying-items portion) → P&L
  - OCI option is generally NOT used under VFA (underlying items absorb most IFIE)

VFA AOC structure:
  ① New business first recognition
  ② Expected CF release (incl. RA release) → Insurance Revenue
  ③ Experience variance — non-underlying items → ISE
  ④ CSM amortisation (by coverage units) → Insurance Revenue
  ⑤ LC reversal (if applicable)
  ⑥ IFIE — residual P&L (small; underlying items absorb most)
  ⑦ IFIE — OCI (rare for VFA; default = 0)
  ⑧ Assumption change → P&L (RA changes)
  ⑨ Assumption change → CSM (non-economic)
  VFA+ Underlying items change → CSM (intra-ICL transfer, net ICL = 0)
  VFA+ Experience variance — underlying items → CSM (intra-ICL)

IFRS 17 references: §45A–45D, B101–B118
"""

from __future__ import annotations

import pandas as pd

from .base import AOCResult, MeasurementModel

_PERIOD_FRACTION = 0.25


class VFAModel(MeasurementModel):
    """
    VFA measurement model.

    Additional CSV columns vs GMM:
        underlying_items_chg   : Change in underlying items × policyholders' share
                                 (intra-ICL PVFCF→CSM transfer, net ICL = 0)
        exp_var_underlying     : Experience variance relating to underlying items
                                 → CSM (intra-ICL), NOT ISE
        exp_var_non_underlying : Experience variance from non-underlying items
                                 → ISE (defaults to experience_var if not provided)

    Note: finance_charge_oci is 0 by default for VFA; finance_charge_pl
          is much smaller than GMM (most IFIE absorbed by CSM via underlying items).
    """

    @property
    def model_name(self) -> str:
        return "VFA"

    def compute_aoc(
        self,
        bom_row: pd.Series,
        eom_row: pd.Series,
    ) -> AOCResult:

        # ── 0. Identifiers ────────────────────────────────────────────────
        cohort_id = str(eom_row["cohort_id"])
        product   = str(eom_row["product"])
        period    = str(eom_row["period"])
        currency  = str(eom_row.get("currency", "HKD"))
        dt        = float(eom_row.get("period_fraction", _PERIOD_FRACTION))

        # ── 1. Opening balances (from prior period row) ───────────────────
        bom_pvfcf = float(bom_row.get("pvfcf_eom", bom_row.get("pvfcf_bom", 0.0)))
        bom_ra    = float(bom_row.get("ra_eom",    bom_row.get("ra_bom",    0.0)))
        bom_csm   = float(bom_row.get("csm_eom",   bom_row.get("csm_bom",   0.0)))
        bom_lc    = float(bom_row.get("lc_eom",    bom_row.get("lc_bom",    0.0)))

        # ── 2. Closing balances (derived from multi-period generator) ─────
        eom_pvfcf = float(eom_row["pvfcf_eom"])
        eom_ra    = float(eom_row["ra_eom"])
        eom_csm   = float(eom_row["csm_eom"])
        eom_lc    = float(eom_row.get("lc_eom", 0.0))

        bom_icl = bom_pvfcf + bom_ra + bom_csm - bom_lc

        # ── 3. VFA — Underlying items change (intra-ICL PVFCF ↔ CSM) ─────
        # Positive = underlying items appreciated → CSM increases
        # Negative = underlying items fell (market correction) → CSM decreases
        underlying_items_chg = float(eom_row.get("underlying_items_chg", 0.0))

        # VFA experience variance split
        # exp_var_underlying: relates to underlying items → CSM (intra-ICL)
        # exp_var_non_underlying: does NOT relate to underlying items → ISE
        exp_var_underlying     = float(eom_row.get("exp_var_underlying",     0.0))
        exp_var_non_underlying = float(eom_row.get("exp_var_non_underlying",
                                       eom_row.get("experience_var", 0.0)))

        # ── 4. Standard AOC items ─────────────────────────────────────────
        new_biz_pvfcf = float(eom_row.get("new_biz_pvfcf", 0.0))
        new_biz_ra    = float(eom_row.get("new_biz_ra",    0.0))
        new_biz_csm   = float(eom_row.get("new_biz_csm",   0.0))
        new_business  = new_biz_pvfcf + new_biz_ra + new_biz_csm

        exp_cf_release   = float(eom_row.get("exp_cf_release",   0.0))
        csm_amortisation = float(eom_row.get("csm_amortisation", 0.0))
        lc_reversal      = float(eom_row.get("lc_reversal",      0.0))

        # VFA: finance_charge_pl is the residual IFIE after CSM absorption
        finance_charge_pl  = float(eom_row.get("finance_charge_pl",  0.0))
        finance_charge_oci = float(eom_row.get("finance_charge_oci", 0.0))  # usually 0 for VFA

        assumption_chg_pl  = float(eom_row.get("assumption_chg_pl",  0.0))
        assumption_chg_csm = float(eom_row.get("assumption_chg_csm", 0.0))
        fx_effect          = float(eom_row.get("fx_effect",          0.0))
        cession_rate       = float(eom_row.get("cession_rate",       0.0))

        # ── 5. Assemble AOCResult ─────────────────────────────────────────
        # NOTE: underlying_items_chg is set AFTER construction via setattr
        # to maintain backward compatibility with cached versions of base.py
        # that may not yet have this field defined in the dataclass.
        result = AOCResult(
            cohort_id=cohort_id,
            product=product,
            measurement_model=self.model_name,
            period=period,
            currency=currency,
            bom_pvfcf=bom_pvfcf,
            bom_ra=bom_ra,
            bom_csm=bom_csm,
            bom_lc=bom_lc,
            new_business=new_business,
            expected_cf_release=exp_cf_release,
            experience_variance=exp_var_non_underlying,   # only non-underlying → ISE
            csm_amortisation=csm_amortisation,
            lc_reversal=lc_reversal,
            finance_charge_pl=finance_charge_pl,
            finance_charge_oci=finance_charge_oci,
            assumption_chg_pl=assumption_chg_pl,
            assumption_chg_csm=assumption_chg_csm + exp_var_underlying,  # both absorbed by CSM
            fx_effect=fx_effect,
            eom_pvfcf=eom_pvfcf,
            eom_ra=eom_ra,
            eom_csm=eom_csm,
            eom_lc=eom_lc,
            cession_rate=cession_rate,
        )
        # Set VFA-specific field as plain attribute (works even without the dataclass field)
        result.underlying_items_chg = underlying_items_chg

        self._reconcile(result)
        return result
