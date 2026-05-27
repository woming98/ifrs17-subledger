"""
IFRS 17 — Premium Allocation Approach (PAA) AOC 引擎

PAA 适用于：
  - 保障期 ≤ 1 年的合同（short-duration medical / rider）
  - 或满足 IFRS 17.53 "PAA 合理近似 GMM" 判断的合同

PAA 负债结构（LRC）：
  LRC = 未赚保费（Unearned Premium）- 获取成本资产（IACF / IACF_asset）
        + 亏损部分（LC）（若合同亏损）

PAA AOC（简化版，对应 IFRS 17.55–59）：
  ① 新业务：当期收到保费 → 增加 LRC
  ② 保费赚取（Earned Premium）→ 释放 LRC → Insurance Revenue
  ③ 获取成本摊销（IACF amortisation）→ Insurance Service Expense
  ④ 亏损合同损失（LC 确认 / 回转）→ P&L
  ⑤ IFIE（保险融资收入 / 费用）：PAA 通常不折现，但可选折现（IFRS 17.56）
  ⑥ 经验差异：仅 LIC（已发生未报告准备金）相关
  ⑦ FX 汇率影响

P&L 影响：
  Insurance Revenue = 赚取保费 - IACF 摊销
  Insurance Service Expense = 实际理赔 + 理赔管理费 +/- LIC 变动

与 GMM 的关键差异：
  - LRC 不含 CSM（直接用未赚保费）
  - 无需维护折现率锁定（DAIR）→ 通常无 IFIE OCI 选项
  - 简洁：省略 CSM 摊销步骤
"""

from __future__ import annotations

import pandas as pd

from .base import AOCResult, MeasurementModel

_PERIOD_FRACTION = 0.25


class PAAModel(MeasurementModel):
    """
    PAA 测量模型。

    输入 CSV 列（current_period）：
        cohort_id, product, measurement_model, period, currency,
        pvfcf_bom, ra_bom, csm_bom(=0), lc_bom,
        pvfcf_eom, ra_eom, csm_eom(=0), lc_eom,
        premium_written,      # 当期承保保费（新业务 + 续期）
        premium_earned,       # 当期赚取保费（负数，减少 LRC）
        iacf_amortisation,    # 获取成本摊销（正数 = 费用增加）
        exp_cf_release,       # 包含 RA 释放（负数，减少 LRC）
        experience_var,       # 理赔实际 - 预期（正 = 更差）
        lc_reversal,          # PAA 亏损合同回转
        fx_effect,
        cession_rate,
        period_fraction,

    PAA 中 pvfcf_bom/eom 对应未赚保费（Unearned Premium Reserve, UPR），
    ra_bom/eom 对应 IACF 资产余额（用负数表示资产），lc_bom/eom 对应亏损部分。
    """

    @property
    def model_name(self) -> str:
        return "PAA"

    def compute_aoc(
        self,
        bom_row: pd.Series,
        eom_row: pd.Series,
    ) -> AOCResult:

        # ── 0. 基础信息 ────────────────────────────────────────────────
        cohort_id = str(eom_row["cohort_id"])
        product   = str(eom_row["product"])
        period    = str(eom_row["period"])
        currency  = str(eom_row.get("currency", "HKD"))

        # ── 1. 期初余额 ────────────────────────────────────────────────
        bom_pvfcf = float(bom_row.get("pvfcf_eom", bom_row.get("pvfcf_bom", 0.0)))
        bom_ra    = float(bom_row.get("ra_eom",    bom_row.get("ra_bom",    0.0)))
        bom_csm   = 0.0   # PAA 无 CSM
        bom_lc    = float(bom_row.get("lc_eom",    bom_row.get("lc_bom",    0.0)))

        # ── 2. 期末余额 ────────────────────────────────────────────────
        eom_pvfcf = float(eom_row["pvfcf_eom"])
        eom_ra    = float(eom_row.get("ra_eom",  0.0))
        eom_csm   = 0.0
        eom_lc    = float(eom_row.get("lc_eom",  0.0))

        # ── 3. PAA 特有字段 ────────────────────────────────────────────
        premium_written   = float(eom_row.get("premium_written",   0.0))
        premium_earned    = float(eom_row.get("premium_earned",    0.0))
        iacf_amortisation = float(eom_row.get("iacf_amortisation", 0.0))
        exp_cf_release    = float(eom_row.get("exp_cf_release",    0.0))
        experience_var    = float(eom_row.get("experience_var",    0.0))
        lc_reversal       = float(eom_row.get("lc_reversal",       0.0))
        fx_effect         = float(eom_row.get("fx_effect",         0.0))
        cession_rate      = float(eom_row.get("cession_rate",      0.0))

        # PAA 通常不做 OCI option（不折现则无 IFIE OCI）
        finance_charge_pl  = float(eom_row.get("finance_charge_pl",  0.0))
        finance_charge_oci = 0.0

        # ── 4. 将 PAA 各项映射到 AOCResult 通用字段 ───────────────────
        # new_business = 当期承保保费（增加 LRC）
        new_business = premium_written
        # expected_cf_release = 赚取保费 + RA 释放（负数，减少 LRC）
        exp_cf_for_aoc = premium_earned + exp_cf_release
        # csm_amortisation → 用于 IACF 摊销（负数 = 费用，增加 P&L 费用）
        csm_amortisation_equiv = -iacf_amortisation   # 映射到同一字段

        # ── 5. 组装 AOCResult ──────────────────────────────────────────
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
            expected_cf_release=exp_cf_for_aoc,
            experience_variance=experience_var,
            csm_amortisation=csm_amortisation_equiv,
            lc_reversal=lc_reversal,
            finance_charge_pl=finance_charge_pl,
            finance_charge_oci=finance_charge_oci,
            assumption_chg_pl=0.0,
            assumption_chg_csm=0.0,
            fx_effect=fx_effect,
            eom_pvfcf=eom_pvfcf,
            eom_ra=eom_ra,
            eom_csm=eom_csm,
            eom_lc=eom_lc,
            cession_rate=cession_rate,
        )

        self._reconcile(result)
        return result
