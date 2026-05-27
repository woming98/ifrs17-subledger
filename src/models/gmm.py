"""
IFRS 17 — General Measurement Model (GMM) AOC 引擎

GMM（又称 BBA, Building Block Approach）适用于：
  - 非参与型长期保险（Term Non-Par、长期 Medical Non-Par）
  - 不满足 PAA 简化条件的合同

负债构成：ICL_LRC = PVFCF + RA + CSM (profitable) | PVFCF + RA - LC (onerous)

AOC 9 项分解：
  ① 新业务首次确认
  ② 预期现金流释放（含 RA 释放）→ Insurance Revenue
  ③ 经验差异（实际 vs 预期）→ Insurance Service Expense
  ④ CSM 摊销（按覆盖单位）→ Insurance Revenue
  ⑤ 亏损合同 LC 回转（profitable 时为 0）
  ⑥ IFIE P&L（按 DAIR 展开折现）
  ⑦ IFIE OCI（当前利率 vs DAIR 差异）
  ⑧ 假设变更 → P&L（RA 变更、经济性假设）
  ⑨ 假设变更 → CSM（非经济性假设，profitable cohort）
  + FX 汇率影响

对应 IFRS 17 段落：IFRS 17.40–52, B96–B119
"""

from __future__ import annotations

import pandas as pd

from .base import AOCResult, MeasurementModel

# ──────────────────────────────────────────────────────────────────────────────
# 辅助常量：期间年化因子（季度 = 0.25）
# ──────────────────────────────────────────────────────────────────────────────
_PERIOD_FRACTION = 0.25   # 默认按季度处理；行中若有 period_fraction 字段则覆盖


class GMMModel(MeasurementModel):
    """
    GMM 测量模型：
      - 盈利合同（CSM ≥ 0）：CSM 吸收有利假设变更，按覆盖单位摊销
      - 亏损合同（onerous）：LC 驱动，首期全额亏损入 P&L；后续 LC 余额随服务释放

    输入 CSV 列（current_period）：
        cohort_id, product, measurement_model, period, currency,
        pvfcf_bom, ra_bom, csm_bom, lc_bom,
        pvfcf_eom, ra_eom, csm_eom, lc_eom,
        dair,                           # 锁定折现率（年化）
        new_biz_pvfcf, new_biz_ra, new_biz_csm,
        exp_cf_release,                 # 预期现金流释放（含 RA release），符号为负数
        experience_var,                 # 实际 - 预期（正 = 实际更差，增加负债）
        csm_amortisation,              # CSM 摊销金额（负数，减少负债）
        lc_reversal,                   # LC 回转金额（负数，减少负债）
        assumption_chg_pl,             # 假设变更 → P&L（正 = 增加负债）
        assumption_chg_csm,            # 假设变更 → CSM（负 = 有利，减少 CSM 余额）
        fx_effect,                     # 汇率影响（正 = 增加负债）
        cession_rate,                  # 再保分出比例 [0, 1]
        period_fraction,               # 可选，默认 0.25（季度）

    注意：finance_charge_pl / finance_charge_oci 由本引擎根据 DAIR 推算，
          无需在 CSV 中提供（若提供则直接使用）。
    """

    @property
    def model_name(self) -> str:
        return "GMM"

    def compute_aoc(
        self,
        bom_row: pd.Series,
        eom_row: pd.Series,
    ) -> AOCResult:
        """
        按 GMM 计算单个 cohort 的完整 AOC。

        bom_row 来自 prior_period.csv（提供 BOM 余额）。
        eom_row 来自 current_period.csv（提供 EOM 余额 + AOC 分项）。
        """

        # ── 0. 基础信息 ────────────────────────────────────────────────────
        cohort_id = str(eom_row["cohort_id"])
        product   = str(eom_row["product"])
        period    = str(eom_row["period"])
        currency  = str(eom_row.get("currency", "HKD"))
        dt        = float(eom_row.get("period_fraction", _PERIOD_FRACTION))

        # ── 1. 期初余额（来自 prior period 行）────────────────────────────
        bom_pvfcf = float(bom_row.get("pvfcf_eom", bom_row.get("pvfcf_bom", 0.0)))
        bom_ra    = float(bom_row.get("ra_eom",    bom_row.get("ra_bom",    0.0)))
        bom_csm   = float(bom_row.get("csm_eom",   bom_row.get("csm_bom",   0.0)))
        bom_lc    = float(bom_row.get("lc_eom",    bom_row.get("lc_bom",    0.0)))

        # ── 2. 期末余额（来自 current period 行）─────────────────────────
        eom_pvfcf = float(eom_row["pvfcf_eom"])
        eom_ra    = float(eom_row["ra_eom"])
        eom_csm   = float(eom_row["csm_eom"])
        eom_lc    = float(eom_row["lc_eom"])

        bom_icl = bom_pvfcf + bom_ra + bom_csm - bom_lc

        # ── 3. 新业务首次确认 ─────────────────────────────────────────────
        new_biz_pvfcf = float(eom_row.get("new_biz_pvfcf", 0.0))
        new_biz_ra    = float(eom_row.get("new_biz_ra",    0.0))
        new_biz_csm   = float(eom_row.get("new_biz_csm",   0.0))
        new_business  = new_biz_pvfcf + new_biz_ra + new_biz_csm

        # ── 4. 预期现金流释放（含 RA 释放）→ Insurance Revenue ─────────
        exp_cf_release = float(eom_row.get("exp_cf_release", 0.0))

        # ── 5. 经验差异 → ISE ──────────────────────────────────────────
        experience_var = float(eom_row.get("experience_var", 0.0))

        # ── 6. CSM 摊销 → Insurance Revenue ──────────────────────────
        csm_amortisation = float(eom_row.get("csm_amortisation", 0.0))

        # ── 7. LC 回转（onerous cohort 转回）──────────────────────────
        lc_reversal = float(eom_row.get("lc_reversal", 0.0))

        # ── 8. IFIE：优先使用 CSV 中预计算值，否则推算 ────────────────
        if "finance_charge_pl" in eom_row and pd.notna(eom_row["finance_charge_pl"]):
            finance_charge_pl = float(eom_row["finance_charge_pl"])
        else:
            # IFIE P&L = BOM_ICL × DAIR × dt（以锁定利率展开折现）
            dair = float(eom_row.get("dair", 0.0))
            finance_charge_pl = bom_icl * dair * dt

        if "finance_charge_oci" in eom_row and pd.notna(eom_row["finance_charge_oci"]):
            finance_charge_oci = float(eom_row["finance_charge_oci"])
        else:
            # OCI 部分：总利率变动效应减去 P&L 部分
            # 简化：OCI = (EOM_PVFCF + EOM_RA - BOM_PVFCF - BOM_RA) - 其他非利率项
            # 若无 current_rate 字段，默认 OCI = 0（无 OCI option 场景）
            finance_charge_oci = float(eom_row.get("finance_charge_oci", 0.0))

        # ── 9. 假设变更 ───────────────────────────────────────────────
        assumption_chg_pl  = float(eom_row.get("assumption_chg_pl",  0.0))
        assumption_chg_csm = float(eom_row.get("assumption_chg_csm", 0.0))

        # ── 10. 汇率影响 ──────────────────────────────────────────────
        fx_effect = float(eom_row.get("fx_effect", 0.0))

        # ── 11. 再保信息 ──────────────────────────────────────────────
        cession_rate = float(eom_row.get("cession_rate", 0.0))

        # ── 12. 组装 AOCResult ────────────────────────────────────────
        result = AOCResult(
            cohort_id=cohort_id,
            product=product,
            measurement_model=self.model_name,
            period=period,
            currency=currency,
            # BOM
            bom_pvfcf=bom_pvfcf,
            bom_ra=bom_ra,
            bom_csm=bom_csm,
            bom_lc=bom_lc,
            # AOC
            new_business=new_business,
            expected_cf_release=exp_cf_release,
            experience_variance=experience_var,
            csm_amortisation=csm_amortisation,
            lc_reversal=lc_reversal,
            finance_charge_pl=finance_charge_pl,
            finance_charge_oci=finance_charge_oci,
            assumption_chg_pl=assumption_chg_pl,
            assumption_chg_csm=assumption_chg_csm,
            fx_effect=fx_effect,
            # EOM
            eom_pvfcf=eom_pvfcf,
            eom_ra=eom_ra,
            eom_csm=eom_csm,
            eom_lc=eom_lc,
            # 再保
            cession_rate=cession_rate,
        )

        # ── 13. 对账检验 ──────────────────────────────────────────────
        self._reconcile(result)

        return result
