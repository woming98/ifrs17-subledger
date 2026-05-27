"""
IFRS 17 测量模型 — 基础数据结构与抽象基类

AOCResult 储存一个 cohort 一个期间的完整变动分析（Analysis of Change）。
MeasurementModel 是 GMM / PAA / VFA 的抽象父类。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ──────────────────────────────────────────────────────────────────────────────
# AOC 结果数据类
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AOCResult:
    """
    一个 cohort × period 的 IFRS 17 变动分析结果。

    期初 (BOM) + 各项变动 = 期末 (EOM)
    对账关系：
        EOM_ICL = BOM_ICL + new_business + expected_cf_release
                + experience_variance + csm_amortisation + lc_reversal
                + finance_charge_pl + finance_charge_oci
                + assumption_chg_pl + assumption_chg_csm + fx_effect
    """

    # ── 标识 ────────────────────────────────────────────────────────────────
    cohort_id: str
    product: str
    measurement_model: str          # "GMM" / "PAA" / "VFA"
    period: str                     # e.g. "2024Q4"
    currency: str = "HKD"

    # ── 期初余额 ─────────────────────────────────────────────────────────────
    bom_pvfcf: float = 0.0          # 未来现金流现值（正数 = 负债）
    bom_ra: float = 0.0             # 风险调整
    bom_csm: float = 0.0            # 合同服务边际（仅 profitable）
    bom_lc: float = 0.0             # 亏损部分（仅 onerous）

    # ── AOC 9 项变动 ─────────────────────────────────────────────────────────
    # 1. 新业务首次确认
    new_business: float = 0.0
    # 2. 预期现金流释放（含 RA 释放）→ Insurance Revenue (P&L)
    expected_cf_release: float = 0.0
    # 3. 经验差异（实际 vs 预期现金流）→ Insurance Service Expense (P&L)
    experience_variance: float = 0.0
    # 4. CSM 摊销 → Insurance Revenue (P&L)
    csm_amortisation: float = 0.0
    # 5. 亏损合同回转（LC → P&L）
    lc_reversal: float = 0.0
    # 6. 财务费用 — P&L 部分（按锁定利率 DAIR 展开折现）→ IFIE (P&L)
    finance_charge_pl: float = 0.0
    # 7. 财务费用 — OCI 部分（当前利率 vs DAIR 差异）→ OCI
    finance_charge_oci: float = 0.0
    # 8. 假设变更 → P&L（RA 变更 / onerous 调整）
    assumption_chg_pl: float = 0.0
    # 9. 假设变更 → CSM（非经济性假设变更，对 profitable cohort）
    assumption_chg_csm: float = 0.0
    # 额外：汇率变动（FX translation）
    fx_effect: float = 0.0

    # ── 期末余额 ─────────────────────────────────────────────────────────────
    eom_pvfcf: float = 0.0
    eom_ra: float = 0.0
    eom_csm: float = 0.0
    eom_lc: float = 0.0

    # ── 对账结果 ─────────────────────────────────────────────────────────────
    reconciliation_ok: bool = True
    reconciliation_diff: float = 0.0    # EOM_ICL(计算) - EOM_ICL(输入)，应趋近于零

    # ── 再保分出（Quota Share RCA）────────────────────────────────────────────
    cession_rate: float = 0.0
    rca_bom: float = 0.0            # 期初再保合同资产 ICL
    rca_eom: float = 0.0            # 期末再保合同资产 ICL
    rca_aoc: "AOCResult | None" = field(default=None, repr=False)

    # ── 衍生属性 ─────────────────────────────────────────────────────────────

    @property
    def bom_icl(self) -> float:
        """期初 ICL（负债），onerous 时用 LC 替代 CSM"""
        return self.bom_pvfcf + self.bom_ra + self.bom_csm - self.bom_lc

    @property
    def eom_icl(self) -> float:
        """期末 ICL（负债）"""
        return self.eom_pvfcf + self.eom_ra + self.eom_csm - self.eom_lc

    @property
    def total_movements(self) -> float:
        """
        影响 ICL 总额的 AOC 变动合计（不含期初）。

        注意：assumption_chg_csm 是 ICL 内部的 PVFCF ↔ CSM 转账，
        净 ICL 效应为零，不计入此合计。
        """
        return (
            self.new_business
            + self.expected_cf_release
            + self.experience_variance
            + self.csm_amortisation
            + self.lc_reversal
            + self.finance_charge_pl
            + self.finance_charge_oci
            + self.assumption_chg_pl
            # assumption_chg_csm 为 intra-ICL 转账，净效应为零，不含在此
            + self.fx_effect
        )

    @property
    def insurance_revenue(self) -> float:
        """
        保险收入 (ISR / Insurance Revenue)
        = 预期现金流释放（含 RA release）+ CSM 摊销
        负数表示收入（降低负债 → P&L 贡献正值）
        """
        return -(self.expected_cf_release + self.csm_amortisation)

    @property
    def insurance_service_expense(self) -> float:
        """
        保险服务费用 (ISE / Insurance Service Expense)
        = 经验差异（负数表示实际好于预期）
        """
        return -self.experience_variance

    @property
    def net_insurance_result(self) -> float:
        """净保险服务结果 = ISR - ISE"""
        return self.insurance_revenue - self.insurance_service_expense

    @property
    def ifie_pl(self) -> float:
        """IFIE — P&L 部分（以 DAIR 展开折现，放 P&L）"""
        return self.finance_charge_pl

    @property
    def ifie_oci(self) -> float:
        """IFIE — OCI 部分（当前利率 vs DAIR 差异，走 OCI）"""
        return self.finance_charge_oci

    def aoc_summary(self) -> dict:
        """Return all AOC movement items as a dict (used by reporting)."""
        return {
            "BOM ICL": self.bom_icl,
            "① New Business": self.new_business,
            "② Expected CF Release (incl. RA)": self.expected_cf_release,
            "③ Experience Variance": self.experience_variance,
            "④ CSM Amortisation": self.csm_amortisation,
            "⑤ LC Reversal": self.lc_reversal,
            "⑥ IFIE — P&L (DAIR unwind)": self.finance_charge_pl,
            "⑦ IFIE — OCI (rate change)": self.finance_charge_oci,
            "⑧ Assumption Change → P&L": self.assumption_chg_pl,
            "⑨ Assumption Change → CSM": self.assumption_chg_csm,
            "FX Effect": self.fx_effect,
            "EOM ICL (Calculated)": self.bom_icl + self.total_movements,
            "EOM ICL (Input)": self.eom_icl,
            "Recon Diff": self.reconciliation_diff,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 抽象测量模型基类
# ──────────────────────────────────────────────────────────────────────────────

class MeasurementModel(ABC):
    """
    IFRS 17 测量模型抽象基类。

    子类（GMMModel、PAAModel）需实现 compute_aoc()，
    输入同一 cohort 的期初行（prior_period）和期末行（current_period），
    输出标准化的 AOCResult。
    """

    @abstractmethod
    def compute_aoc(
        self,
        bom_row: pd.Series,
        eom_row: pd.Series,
    ) -> AOCResult:
        """
        计算单个 cohort 的 Analysis of Change。

        Parameters
        ----------
        bom_row : pd.Series
            prior_period.csv 中对应 cohort 的数据行（期初余额）。
        eom_row : pd.Series
            current_period.csv 中对应 cohort 的数据行（期末余额 + AOC 分项）。

        Returns
        -------
        AOCResult
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回测量模型名称"""
        ...

    def _reconcile(self, result: AOCResult, tol: float = 0.01) -> AOCResult:
        """
        检验 BOM + 变动 = EOM，并将差异写入 result。

        tol : 容忍误差（默认 0.01，单位同输入金额）
        """
        computed_eom = result.bom_icl + result.total_movements
        diff = computed_eom - result.eom_icl
        result.reconciliation_diff = round(diff, 4)
        result.reconciliation_ok = abs(diff) <= tol
        return result
