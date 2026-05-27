"""
IFRS 17 — 再保险合同资产（Reinsurance Contract Asset, RCA）

本模块处理 Quota Share（比例再保）分出业务的 RCA 计量。

IFRS 17 对再保险合同资产的关键规定：
  - IFRS 17.60–70A：再保险合同资产适用与对应直接业务相同的测量方法
  - IFRS 17.63：PVFCF_ceded = cession_rate × PVFCF_gross（简化比例分出）
  - IFRS 17.64：若基础合同亏损（LC > 0），分出部分的 LC 同样确认为 RCA 中的资产

Quota Share 分出逻辑（简化）：
  - RCA_PVFCF  = -cession_rate × gross_PVFCF     （资产，取负）
  - RCA_RA     = -cession_rate × gross_RA         （资产，取负）
  - RCA_CSM    = -cession_rate × gross_CSM        （资产，取负）
  - RCA_ICL    = RCA_PVFCF + RCA_RA + RCA_CSM     （资产 > 0 时表示净债权）

  净 ICL_net = gross_ICL + RCA_ICL
             = (1 - cession_rate) × gross_ICL

再保险变动分析（RCA AOC）由对应直接业务 AOC 乘以 cession_rate 得出，
但符号相反（直接业务 ICL 是负债，RCA 是资产）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models.base import AOCResult


@dataclass
class RCASummary:
    """单个 cohort 的 RCA 摘要（分出部分）"""

    cohort_id: str
    period: str
    cession_rate: float

    # ── 期初 / 期末 RCA（资产端，正数 = 资产）────────────────────────────
    rca_bom_pvfcf: float = 0.0
    rca_bom_ra: float = 0.0
    rca_bom_csm: float = 0.0
    rca_bom_icl: float = 0.0

    rca_eom_pvfcf: float = 0.0
    rca_eom_ra: float = 0.0
    rca_eom_csm: float = 0.0
    rca_eom_icl: float = 0.0

    # ── RCA AOC 各项 ─────────────────────────────────────────────────────
    rca_new_business: float = 0.0
    rca_expected_cf_release: float = 0.0
    rca_experience_variance: float = 0.0
    rca_csm_amortisation: float = 0.0
    rca_finance_charge_pl: float = 0.0
    rca_finance_charge_oci: float = 0.0
    rca_assumption_chg_pl: float = 0.0
    rca_assumption_chg_csm: float = 0.0
    rca_fx_effect: float = 0.0

    # ── 对账 ─────────────────────────────────────────────────────────────
    reconciliation_ok: bool = True
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
        """再保险分入 ISR（分出方视角为负）"""
        return -(self.rca_expected_cf_release + self.rca_csm_amortisation)

    @property
    def rca_ifie_pl(self) -> float:
        return self.rca_finance_charge_pl

    @property
    def rca_ifie_oci(self) -> float:
        return self.rca_finance_charge_oci

    def aoc_summary(self) -> dict:
        return {
            "RCA BOM ICL": self.rca_bom_icl,
            "① 新业务": self.rca_new_business,
            "② 预期现金流释放（含 RA）": self.rca_expected_cf_release,
            "③ 经验差异": self.rca_experience_variance,
            "④ CSM 摊销": self.rca_csm_amortisation,
            "⑥ IFIE — P&L": self.rca_finance_charge_pl,
            "⑦ IFIE — OCI": self.rca_finance_charge_oci,
            "⑧ 假设变更 → P&L": self.rca_assumption_chg_pl,
            "⑨ 假设变更 → CSM": self.rca_assumption_chg_csm,
            "汇率影响": self.rca_fx_effect,
            "RCA EOM ICL（计算）": self.rca_bom_icl + self.rca_total_movements,
            "RCA EOM ICL（输入）": self.rca_eom_icl,
            "对账差异": self.reconciliation_diff,
        }


def compute_rca(gross_aoc: "AOCResult", cession_rate: float | None = None) -> RCASummary:
    """
    根据直接业务 AOCResult 计算 Quota Share 分出的 RCA。

    Parameters
    ----------
    gross_aoc    : 直接业务 AOCResult（已由 GMMModel / PAAModel 计算完成）
    cession_rate : 覆盖 gross_aoc.cession_rate（若为 None，则取 gross_aoc 中的值）

    Returns
    -------
    RCASummary

    分出逻辑（比例再保，无滑动比例条款）：
      RCA 的所有科目 = -cession_rate × 对应 gross 科目
      （直接业务 ICL 是负债；RCA 是资产，符号相反）
    """
    q = cession_rate if cession_rate is not None else gross_aoc.cession_rate

    if q <= 0.0:
        # 无分出：返回零值摘要
        return RCASummary(
            cohort_id=gross_aoc.cohort_id,
            period=gross_aoc.period,
            cession_rate=0.0,
        )

    # BOM RCA（资产端：对应直接业务负债乘以 -q）
    rca_bom_pvfcf = -q * gross_aoc.bom_pvfcf
    rca_bom_ra    = -q * gross_aoc.bom_ra
    rca_bom_csm   = -q * gross_aoc.bom_csm
    rca_bom_icl   = rca_bom_pvfcf + rca_bom_ra + rca_bom_csm

    # EOM RCA
    rca_eom_pvfcf = -q * gross_aoc.eom_pvfcf
    rca_eom_ra    = -q * gross_aoc.eom_ra
    rca_eom_csm   = -q * gross_aoc.eom_csm
    rca_eom_icl   = rca_eom_pvfcf + rca_eom_ra + rca_eom_csm

    # AOC 各项（符号与 gross 相反：gross 是负债增加为正；RCA 是资产增加为正）
    rca = RCASummary(
        cohort_id=gross_aoc.cohort_id,
        period=gross_aoc.period,
        cession_rate=q,
        rca_bom_pvfcf=rca_bom_pvfcf,
        rca_bom_ra=rca_bom_ra,
        rca_bom_csm=rca_bom_csm,
        rca_bom_icl=rca_bom_icl,
        rca_eom_pvfcf=rca_eom_pvfcf,
        rca_eom_ra=rca_eom_ra,
        rca_eom_csm=rca_eom_csm,
        rca_eom_icl=rca_eom_icl,
        rca_new_business=      -q * gross_aoc.new_business,
        rca_expected_cf_release=-q * gross_aoc.expected_cf_release,
        rca_experience_variance=-q * gross_aoc.experience_variance,
        rca_csm_amortisation=  -q * gross_aoc.csm_amortisation,
        rca_finance_charge_pl= -q * gross_aoc.finance_charge_pl,
        rca_finance_charge_oci=-q * gross_aoc.finance_charge_oci,
        rca_assumption_chg_pl= -q * gross_aoc.assumption_chg_pl,
        rca_assumption_chg_csm=-q * gross_aoc.assumption_chg_csm,
        rca_fx_effect=         -q * gross_aoc.fx_effect,
    )

    # 对账
    computed_eom = rca.rca_bom_icl + rca.rca_total_movements
    diff = computed_eom - rca.rca_eom_icl
    rca.reconciliation_diff = round(diff, 4)
    rca.reconciliation_ok = abs(diff) <= 0.01

    return rca
