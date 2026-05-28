"""
IFRS 17 — 再保险合同资产（Reinsurance Contract Asset, RCA）

原有 Quota Share 接口（向后兼容）。
分层 XL + LEV + 多再保人功能见 reinsurance_xl.py。

IFRS 17 关键条款：
  IFRS 17.60–70A: 再保合同适用相同测量方法
  IFRS 17.63:     PVFCF_ceded 按合同条款计算
  IFRS 17.64:     亏损合同的分出 LC 同样确认为 RCA 资产
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

import yaml

if TYPE_CHECKING:
    from .models.base import AOCResult


# ──────────────────────────────────────────────────────────────────────────────
# RCA 数据结构（与 reinsurance_xl.py 保持一致，可互相导入）
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RCASummary:
    """单个再保合同的 RCA 摘要（分出部分）"""

    cohort_id:    str
    period:       str
    cession_rate: float
    treaty_id:    str = "QS"
    reinsurer:    str = "Unknown"
    layer_label:  str = "QS"

    # ── 期初 / 期末 RCA（资产端，正数 = 资产）────────────────────────────
    rca_bom_pvfcf: float = 0.0
    rca_bom_ra:    float = 0.0
    rca_bom_csm:   float = 0.0
    rca_bom_icl:   float = 0.0

    rca_eom_pvfcf: float = 0.0
    rca_eom_ra:    float = 0.0
    rca_eom_csm:   float = 0.0
    rca_eom_icl:   float = 0.0

    # ── RCA AOC 各项 ─────────────────────────────────────────────────────
    rca_new_business:        float = 0.0
    rca_expected_cf_release: float = 0.0
    rca_experience_variance: float = 0.0
    rca_csm_amortisation:    float = 0.0
    rca_finance_charge_pl:   float = 0.0
    rca_finance_charge_oci:  float = 0.0
    rca_assumption_chg_pl:   float = 0.0
    rca_assumption_chg_csm:  float = 0.0
    rca_fx_effect:           float = 0.0

    # ── 对账 ─────────────────────────────────────────────────────────────
    reconciliation_ok:   bool  = True
    reconciliation_diff: float = 0.0

    @property
    def rca_total_movements(self) -> float:
        return (
            self.rca_new_business
            + self.rca_expected_cf_release
            + self.rca_experience_variance
            + self.rca_csm_amortisation
            + self.rca_finance_charge_pl
            + self.rca_finance_charge_oci
            + self.rca_assumption_chg_pl
            + self.rca_assumption_chg_csm
            + self.rca_fx_effect
        )

    @property
    def rca_insurance_revenue(self) -> float:
        return -(self.rca_expected_cf_release + self.rca_csm_amortisation)

    @property
    def rca_ifie_pl(self) -> float:
        return self.rca_finance_charge_pl

    @property
    def rca_ifie_oci(self) -> float:
        return self.rca_finance_charge_oci

    def aoc_summary(self) -> dict:
        tag = f"[{self.reinsurer} / {self.layer_label}]"
        return {
            f"RCA BOM ICL {tag}":             self.rca_bom_icl,
            f"① New Business {tag}":          self.rca_new_business,
            f"② Expected CF Release {tag}":   self.rca_expected_cf_release,
            f"③ Experience Variance {tag}":   self.rca_experience_variance,
            f"④ CSM Amortisation {tag}":      self.rca_csm_amortisation,
            f"⑥ IFIE — P&L {tag}":            self.rca_finance_charge_pl,
            f"⑦ IFIE — OCI {tag}":            self.rca_finance_charge_oci,
            f"⑧ Assumption Chg → P&L {tag}":  self.rca_assumption_chg_pl,
            f"⑨ Assumption Chg → CSM {tag}":  self.rca_assumption_chg_csm,
            f"FX Effect {tag}":               self.rca_fx_effect,
            f"RCA EOM ICL Calc {tag}":        self.rca_bom_icl + self.rca_total_movements,
            f"RCA EOM ICL Input {tag}":       self.rca_eom_icl,
            f"Recon Diff {tag}":              self.reconciliation_diff,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 内部构建函数（被 reinsurance_xl.py 和 compute_rca 共用）
# ──────────────────────────────────────────────────────────────────────────────

def _build_rca(gross_aoc, eff_rate: float, treaty_id: str, reinsurer: str, layer_label: str) -> RCASummary:
    """按有效分保比例构建 RCASummary"""
    q = eff_rate

    rca_bom_pvfcf = -q * gross_aoc.bom_pvfcf
    rca_bom_ra    = -q * gross_aoc.bom_ra
    rca_bom_csm   = -q * gross_aoc.bom_csm
    rca_bom_icl   = rca_bom_pvfcf + rca_bom_ra + rca_bom_csm

    rca_eom_pvfcf = -q * gross_aoc.eom_pvfcf
    rca_eom_ra    = -q * gross_aoc.eom_ra
    rca_eom_csm   = -q * gross_aoc.eom_csm
    rca_eom_icl   = rca_eom_pvfcf + rca_eom_ra + rca_eom_csm

    rca = RCASummary(
        cohort_id=gross_aoc.cohort_id,
        period=gross_aoc.period,
        cession_rate=q,
        treaty_id=treaty_id,
        reinsurer=reinsurer,
        layer_label=layer_label,
        rca_bom_pvfcf=rca_bom_pvfcf,
        rca_bom_ra=rca_bom_ra,
        rca_bom_csm=rca_bom_csm,
        rca_bom_icl=rca_bom_icl,
        rca_eom_pvfcf=rca_eom_pvfcf,
        rca_eom_ra=rca_eom_ra,
        rca_eom_csm=rca_eom_csm,
        rca_eom_icl=rca_eom_icl,
        rca_new_business=       -q * gross_aoc.new_business,
        rca_expected_cf_release=-q * gross_aoc.expected_cf_release,
        rca_experience_variance=-q * gross_aoc.experience_variance,
        rca_csm_amortisation=   -q * gross_aoc.csm_amortisation,
        rca_finance_charge_pl=  -q * gross_aoc.finance_charge_pl,
        rca_finance_charge_oci= -q * gross_aoc.finance_charge_oci,
        rca_assumption_chg_pl=  -q * gross_aoc.assumption_chg_pl,
        rca_assumption_chg_csm= -q * gross_aoc.assumption_chg_csm,
        rca_fx_effect=          -q * gross_aoc.fx_effect,
    )

    computed_eom = rca.rca_bom_icl + rca.rca_total_movements
    diff = computed_eom - rca.rca_eom_icl
    rca.reconciliation_diff = round(diff, 4)
    rca.reconciliation_ok   = abs(diff) <= 0.01
    return rca


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口（向后兼容）
# ──────────────────────────────────────────────────────────────────────────────

def compute_rca(gross_aoc, cession_rate: Optional[float] = None) -> RCASummary:
    """
    Quota Share 分出接口（向后兼容）。
    分层 XL 请使用 reinsurance_xl.compute_rca_treaties。
    """
    q = cession_rate if cession_rate is not None else gross_aoc.cession_rate
    if q <= 0.0:
        return RCASummary(
            cohort_id=gross_aoc.cohort_id,
            period=gross_aoc.period,
            cession_rate=0.0,
        )
    return _build_rca(gross_aoc, q, "QS", "Reinsurer", "QS")
