"""
多期 Demo 数据生成器

生成 actuarial_output.csv，包含：
  7 个 cohort × 5 个期间（2023Q4 期初 + 2024Q1~Q4）= 35 行

cohort 列表：
  1. TERM_GMM_2022    — 5Y Term Non-Par (GMM, profitable, QS 30%)
  2. MED_GMM_2021     — Medical Non-Par LT (GMM, profitable, QS 20%)
  3. MED_GMM_ONR      — Medical Non-Par LT (GMM, onerous)
  4. MED_PAA          — Medical Non-Par ST (PAA, QS 25%)
  5. RIDER_PAA        — Rider Non-Par ST (PAA)
  6. WL_VFA_2019      — Whole Life Participating (VFA, QS 20%)  ← NEW
  7. ENDO_VFA_2022    — Endowment Participating (VFA)           ← NEW

运行：
    cd ifrs17-subledger
    python data/generate_multi_period.py
"""

import csv
import os

# ──────────────────────────────────────────────────────────────────────────────
# 核心：从 BOM + AOC 参数 → EOM（保证对账一致）
# ──────────────────────────────────────────────────────────────────────────────

def apply_movements(bom: dict, mv: dict, model: str) -> dict:
    """
    根据期初余额 (bom) 和变动参数 (mv) 计算期末余额 (eom)。
    保证 BOM + 变动 = EOM，100% 对账。

    Parameters
    ----------
    bom : {'pvfcf', 'ra', 'csm', 'lc'}   — 期初各分量
    mv  : AOC 变动参数字典（见下方字段说明）
    model : 'GMM', 'PAA', 'VFA'

    mv 字段：
      ra_release         : RA 释放（负数，直接减少 RA 负债，含在 exp_cf_release 中）
      pvfcf_cf_release   : PVFCF 部分的预期现金流释放（负数）
      experience_var     : 经验差异（正 = 实际更差）[non-underlying for VFA]
      csm_amortisation   : CSM 摊销（负数）
      lc_reversal        : LC 回转（负数 = 减少 LC）
      finance_charge_pl  : IFIE P&L（正数 = 增加负债）
      finance_charge_oci : IFIE OCI
      assumption_chg_pl  : 假设变更 → P&L
      assumption_chg_csm : 假设变更 → CSM（intra-ICL）
      underlying_items_chg: VFA 基础项目变动 → CSM（intra-ICL）
      exp_var_underlying : VFA 基础项目经验差异 → CSM（intra-ICL）
      fx_effect          : 汇率变动
      new_business       : 新业务净额（通常季度内为 0）
      lc_recognition     : LC 新增确认（onerous cohort）
      premium_written    : PAA 承保保费
      premium_earned     : PAA 赚取保费（负数）
      iacf_amortisation  : PAA IACF 摊销（正数）
    """
    ra_rel   = mv.get("ra_release", 0.0)
    pvfcf_rel = mv.get("pvfcf_cf_release", 0.0)
    exp_var  = mv.get("experience_var", 0.0)
    csm_amort = mv.get("csm_amortisation", 0.0)
    lc_rev   = mv.get("lc_reversal", 0.0)
    lc_recog = mv.get("lc_recognition", 0.0)
    fi_pl    = mv.get("finance_charge_pl", 0.0)
    fi_oci   = mv.get("finance_charge_oci", 0.0)
    as_pl    = mv.get("assumption_chg_pl", 0.0)
    as_csm   = mv.get("assumption_chg_csm", 0.0)
    und_chg  = mv.get("underlying_items_chg", 0.0)
    ev_und   = mv.get("exp_var_underlying", 0.0)
    fx       = mv.get("fx_effect", 0.0)
    new_biz  = mv.get("new_business", 0.0)

    # EOM RA = BOM RA + RA release
    eom_ra = bom["ra"] + ra_rel

    # EOM CSM = BOM CSM + amortisation + intra-ICL transfers
    # (csm_amort is negative; as_csm, und_chg, ev_und are intra-ICL)
    eom_csm = max(0.0, bom["csm"] + csm_amort + as_csm + und_chg + ev_und)

    # EOM LC = BOM LC + new recognition + reversal
    eom_lc = max(0.0, bom["lc"] + lc_recog + lc_rev)

    # Total ICL movements (excl. intra-ICL transfers)
    total_mv = (
        new_biz
        + pvfcf_rel
        + ra_rel
        + exp_var
        + csm_amort
        + lc_rev
        + fi_pl
        + fi_oci
        + as_pl
        + fx
    )
    # PAA: premium movements
    if model == "PAA":
        prem_written = mv.get("premium_written", 0.0)
        prem_earned  = mv.get("premium_earned", 0.0)
        iacf_amort   = mv.get("iacf_amortisation", 0.0)
        # total_mv replaces exp_cf_release for PAA
        total_mv = prem_written + prem_earned + exp_var + (-iacf_amort) + fi_pl + fx

    bom_icl = bom["pvfcf"] + bom["ra"] + bom["csm"] - bom["lc"]
    eom_icl = bom_icl + total_mv
    eom_pvfcf = eom_icl - eom_ra - eom_csm + eom_lc

    return {
        "pvfcf_eom": round(eom_pvfcf, 2),
        "ra_eom":    round(eom_ra,    2),
        "csm_eom":   round(eom_csm,   2),
        "lc_eom":    round(eom_lc,    2),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 7 个 cohort 的配置
# ──────────────────────────────────────────────────────────────────────────────

COHORTS = [

    # ── 1. TERM_GMM ──────────────────────────────────────────────────────────
    {
        "cohort_id": "TERM_GMM_2022",
        "product":   "Term Non-Par 5Y",
        "model":     "GMM",
        "currency":  "HKD",
        "cession_rate": 0.30,
        "dair":         0.016,
        "opening":  {"pvfcf": 15500.0, "ra": 640.0, "csm": 2720.0, "lc": 0.0},
        "quarters": [
            # Q1 2024
            {"ra_release": -40, "pvfcf_cf_release": -515, "experience_var": 30,
             "csm_amortisation": -265, "finance_charge_pl": 81.4, "finance_charge_oci": -45,
             "assumption_chg_csm": 20},
            # Q2 2024
            {"ra_release": -40, "pvfcf_cf_release": -505, "experience_var": 60,
             "csm_amortisation": -260, "finance_charge_pl": 72.4, "finance_charge_oci": 25,
             "assumption_chg_csm": 0},
            # Q3 2024
            {"ra_release": -40, "pvfcf_cf_release": -495, "experience_var": 20,
             "csm_amortisation": -255, "finance_charge_pl": 70.2, "finance_charge_oci": -60,
             "assumption_chg_csm": 50},
            # Q4 2024
            {"ra_release": -40, "pvfcf_cf_release": -485, "experience_var": 40,
             "csm_amortisation": -250, "finance_charge_pl": 67.0, "finance_charge_oci": -35,
             "assumption_chg_csm": 30},
        ],
    },

    # ── 2. MED_GMM ───────────────────────────────────────────────────────────
    {
        "cohort_id": "MED_GMM_2021",
        "product":   "Medical Non-Par LT",
        "model":     "GMM",
        "currency":  "HKD",
        "cession_rate": 0.20,
        "dair":         0.016,
        "opening":  {"pvfcf": 8500.0, "ra": 425.0, "csm": 1050.0, "lc": 0.0},
        "quarters": [
            {"ra_release": -25, "pvfcf_cf_release": -285, "experience_var": 50,
             "csm_amortisation": -145, "finance_charge_pl": 39.9, "finance_charge_oci": -30,
             "assumption_chg_pl": 5, "assumption_chg_csm": -15},
            {"ra_release": -25, "pvfcf_cf_release": -280, "experience_var": 110,
             "csm_amortisation": -142, "finance_charge_pl": 37.2, "finance_charge_oci": 20,
             "assumption_chg_pl": 10, "assumption_chg_csm": 0},
            {"ra_release": -25, "pvfcf_cf_release": -275, "experience_var": 80,
             "csm_amortisation": -140, "finance_charge_pl": 35.1, "finance_charge_oci": -28,
             "assumption_chg_pl": 8, "assumption_chg_csm": -25},
            {"ra_release": -25, "pvfcf_cf_release": -270, "experience_var": 120,
             "csm_amortisation": -140, "finance_charge_pl": 38.4, "finance_charge_oci": -28,
             "assumption_chg_pl": 10, "assumption_chg_csm": -25},
        ],
    },

    # ── 3. MED_GMM_ONEROUS ───────────────────────────────────────────────────
    {
        "cohort_id": "MED_GMM_ONR",
        "product":   "Medical Non-Par LT (Onerous)",
        "model":     "GMM",
        "currency":  "HKD",
        "cession_rate": 0.00,
        "dair":         0.016,
        "opening":  {"pvfcf": 3600.0, "ra": 180.0, "csm": 0.0, "lc": 230.0},
        "quarters": [
            # LC gradually releases as service is provided
            {"ra_release": -13, "pvfcf_cf_release": -187, "experience_var": 25,
             "lc_reversal": -60, "finance_charge_pl": 13.7, "finance_charge_oci": -10,
             "assumption_chg_pl": 5},
            {"ra_release": -13, "pvfcf_cf_release": -183, "experience_var": 35,
             "lc_reversal": -65, "finance_charge_pl": 12.8, "finance_charge_oci": 8,
             "assumption_chg_pl": 8},
            {"ra_release": -13, "pvfcf_cf_release": -179, "experience_var": 28,
             "lc_reversal": -60, "finance_charge_pl": 12.1, "finance_charge_oci": -9,
             "assumption_chg_pl": 4},
            {"ra_release": -13, "pvfcf_cf_release": -175, "experience_var": 30,
             "lc_reversal": -70, "finance_charge_pl": 13.9, "finance_charge_oci": -9.9,
             "assumption_chg_pl": 5},
        ],
    },

    # ── 4. MED_ONR_RECOVERY — Onerous → Full Recovery Lifecycle ─────────────
    #
    # 展示 IFRS 17 亏损合同完整生命周期（面试最高频考点）：
    #
    #   Opening : LC = 750（首次确认亏损，高额 LC）
    #   Q1      : 经验差异恶化 → 额外 LC 确认（lc_reversal > 0）LC 升至 830
    #   Q2      : 企稳，正常服务期 LC 释放（lc_reversal < 0）
    #   Q3      : 重大假设改善 → 大额 lc_reversal，LC 从 730 降至 115
    #   Q4      : 最后 115 LC 全部释放 + assumption_chg_csm 形成 CSM 160
    #             → LC = 0，CSM = 160，合同恢复盈利！
    #
    # IFRS 17 关键规则：
    #   - lc_reversal > 0（正）：确认额外亏损 → Dr ISE / Cr ICL_LC
    #   - lc_reversal < 0（负）：服务期释放  → Dr ICL_LC / Cr ISR
    #   - LC → 0 且改善超出 LC：超额进 CSM   → Dr ICL_PVFCF / Cr ICL_CSM
    {
        "cohort_id": "MED_ONR_RECOVERY",
        "product":   "Medical Non-Par LT (Onerous→Recovery)",
        "model":     "GMM",
        "currency":  "HKD",
        "cession_rate": 0.00,
        "dair":          0.016,
        # Day-1 recognition: PVFCF + RA > 0 → onerous, LC = 750, CSM = 0
        "opening":  {"pvfcf": 5000.0, "ra": 250.0, "csm": 0.0, "lc": 750.0},
        "quarters": [
            # ── Q1 2024: FURTHER DETERIORATION ──────────────────────────────
            # Bad claims experience forces additional LC recognition (lc_reversal > 0)
            # Net ICL barely changes; but the LC INCREASES (contract gets worse)
            {"ra_release": -12, "pvfcf_cf_release": -175, "experience_var": 90,
             "lc_reversal": 80,   # POSITIVE = additional LC Dr ISE / Cr ICL_LC
             "finance_charge_pl": 16.5},
            # After Q1: LC = 830 (worsened!), CSM = 0 → still deeply onerous

            # ── Q2 2024: STABILIZATION ──────────────────────────────────────
            # Claims normalise; regular service-delivery LC release begins
            {"ra_release": -12, "pvfcf_cf_release": -180, "experience_var": 40,
             "lc_reversal": -100,  # NEGATIVE = service release Dr ICL_LC / Cr ISR
             "finance_charge_pl": 15.0},
            # After Q2: LC = 730, still onerous but stabilizing

            # ── Q3 2024: MAJOR ASSUMPTION IMPROVEMENT ───────────────────────
            # Medical cost trend assumption revised downward (major good news)
            # Large LC release (service delivery + assumption improvement combined)
            {"ra_release": -12, "pvfcf_cf_release": -175, "experience_var": 20,
             "lc_reversal": -615,  # BIG release: service + assumption improvement
             "finance_charge_pl": 14.0},
            # After Q3: LC = 115 (from 730, almost cleared!)

            # ── Q4 2024: FULL RECOVERY ───────────────────────────────────────
            # Last LC cleared; excess improvement forms NEW CSM → profitable!
            # This is the key IFRS 17 onerous → profitable crossover moment
            {"ra_release": -12, "pvfcf_cf_release": -170, "experience_var": 15,
             "lc_reversal": -115,   # Clears the LAST of LC (Dr ICL_LC / Cr ISR)
             "finance_charge_pl": 12.0,
             "assumption_chg_csm": 160},  # Excess improvement → NEW CSM BORN!
            # After Q4: LC = 0, CSM = 160 → CONTRACT IS PROFITABLE AGAIN! 🎉
        ],
    },

    # ── 5. MED_PAA ───────────────────────────────────────────────────────────
    {
        "cohort_id": "MED_PAA",
        "product":   "Medical Non-Par ST (PAA)",
        "model":     "PAA",
        "currency":  "HKD",
        "cession_rate": 0.25,
        "dair":         0.0,
        # UPR = pvfcf; IACF asset = ra (treated as separate asset, sign positive)
        "opening":  {"pvfcf": 2400.0, "ra": 120.0, "csm": 0.0, "lc": 0.0},
        "quarters": [
            # Each quarter earns ~600 of premium (1yr policy renews annually)
            {"premium_written": 600, "premium_earned": -600, "iacf_amortisation": 30,
             "experience_var": 10},
            {"premium_written": 600, "premium_earned": -600, "iacf_amortisation": 30,
             "experience_var": 12},
            {"premium_written": 600, "premium_earned": -600, "iacf_amortisation": 30,
             "experience_var": 15},
            {"premium_written": 0,   "premium_earned": -600, "iacf_amortisation": 30,
             "experience_var": 15},
        ],
    },

    # ── 5. RIDER_PAA ─────────────────────────────────────────────────────────
    {
        "cohort_id": "RIDER_PAA",
        "product":   "Rider Non-Par (PAA)",
        "model":     "PAA",
        "currency":  "HKD",
        "cession_rate": 0.00,
        "dair":         0.0,
        "opening":  {"pvfcf": 1600.0, "ra": 80.0, "csm": 0.0, "lc": 0.0},
        "quarters": [
            {"premium_written": 400, "premium_earned": -400, "iacf_amortisation": 20,
             "experience_var": 5},
            {"premium_written": 400, "premium_earned": -400, "iacf_amortisation": 20,
             "experience_var": 7},
            {"premium_written": 400, "premium_earned": -400, "iacf_amortisation": 20,
             "experience_var": 8},
            {"premium_written": 0,   "premium_earned": -400, "iacf_amortisation": 20,
             "experience_var": 8},
        ],
    },

    # ── 6. WL_VFA — Whole Life Par（VFA，大型组合）──────────────────────────
    #
    # 情景设计（展示 VFA 的 CSM 波动性）：
    #   Q1: 股市上涨 → 强正向 underlying_items_chg → CSM 大幅增加
    #   Q2: 市场调整 → 负向 underlying_items_chg → CSM 显著下滑
    #   Q3: 市场回暖 → 中等正向
    #   Q4: 平稳
    {
        "cohort_id": "WL_VFA_2019",
        "product":   "Whole Life Par (VFA)",
        "model":     "VFA",
        "currency":  "HKD",
        "cession_rate": 0.20,
        "dair":         0.018,
        # Large mature book
        "opening":  {"pvfcf": 42000.0, "ra": 1680.0, "csm": 11500.0, "lc": 0.0},
        "quarters": [
            # Q1: Bull market (+8% underlying return → large CSM inflow)
            {"ra_release": -80, "pvfcf_cf_release": -820, "experience_var": 50,
             "csm_amortisation": -380, "finance_charge_pl": 255.6,
             "underlying_items_chg": 1200,   # strong market Q1
             "exp_var_underlying": -30,       # favorable underlying experience → CSM
             "assumption_chg_csm": 50},
            # Q2: Market correction (−5%) → CSM drop
            {"ra_release": -80, "pvfcf_cf_release": -830, "experience_var": 80,
             "csm_amortisation": -385, "finance_charge_pl": 263.2,
             "underlying_items_chg": -900,   # market correction → CSM falls
             "exp_var_underlying": 40,        # adverse underlying experience → CSM (absorbed)
             "assumption_chg_csm": 0},
            # Q3: Recovery (+3%)
            {"ra_release": -80, "pvfcf_cf_release": -825, "experience_var": 60,
             "csm_amortisation": -375, "finance_charge_pl": 249.8,
             "underlying_items_chg": 500,
             "exp_var_underlying": -20,
             "assumption_chg_csm": 30},
            # Q4: Stable (+2%)
            {"ra_release": -80, "pvfcf_cf_release": -820, "experience_var": 55,
             "csm_amortisation": -370, "finance_charge_pl": 252.1,
             "underlying_items_chg": 320,
             "exp_var_underlying": -15,
             "assumption_chg_csm": 20},
        ],
    },

    # ── 7. ENDO_VFA — Endowment Par（VFA，中型）─────────────────────────────
    {
        "cohort_id": "ENDO_VFA_2022",
        "product":   "Endowment Par 20Y (VFA)",
        "model":     "VFA",
        "currency":  "HKD",
        "cession_rate": 0.00,
        "dair":         0.020,
        "opening":  {"pvfcf": 9500.0, "ra": 380.0, "csm": 3200.0, "lc": 0.0},
        "quarters": [
            {"ra_release": -20, "pvfcf_cf_release": -230, "experience_var": 15,
             "csm_amortisation": -120, "finance_charge_pl": 76.4,
             "underlying_items_chg": 300, "exp_var_underlying": -10, "assumption_chg_csm": 20},
            {"ra_release": -20, "pvfcf_cf_release": -228, "experience_var": 25,
             "csm_amortisation": -122, "finance_charge_pl": 74.8,
             "underlying_items_chg": -200, "exp_var_underlying": 15, "assumption_chg_csm": 0},
            {"ra_release": -20, "pvfcf_cf_release": -226, "experience_var": 18,
             "csm_amortisation": -120, "finance_charge_pl": 73.2,
             "underlying_items_chg": 150, "exp_var_underlying": -8, "assumption_chg_csm": 15},
            {"ra_release": -20, "pvfcf_cf_release": -224, "experience_var": 20,
             "csm_amortisation": -118, "finance_charge_pl": 72.0,
             "underlying_items_chg": 100, "exp_var_underlying": -5, "assumption_chg_csm": 10},
        ],
    },
]

PERIODS   = ["2023Q4", "2024Q1", "2024Q2", "2024Q3", "2024Q4"]
QUARTERS  = ["2024Q1", "2024Q2", "2024Q3", "2024Q4"]


# ──────────────────────────────────────────────────────────────────────────────
# CSV 字段定义
# ──────────────────────────────────────────────────────────────────────────────

FIELDS = [
    "cohort_id", "product", "measurement_model", "period", "currency",
    # EOM balances
    "pvfcf_eom", "ra_eom", "csm_eom", "lc_eom",
    # GMM / VFA AOC items
    "new_biz_pvfcf", "new_biz_ra", "new_biz_csm",
    "exp_cf_release",         # = pvfcf_cf_release + ra_release
    "experience_var",         # non-underlying for VFA
    "csm_amortisation", "lc_reversal",
    "finance_charge_pl", "finance_charge_oci",
    "assumption_chg_pl", "assumption_chg_csm",
    "fx_effect",
    # VFA-specific
    "underlying_items_chg", "exp_var_underlying",
    # PAA-specific
    "premium_written", "premium_earned", "iacf_amortisation",
    # Metadata
    "dair", "cession_rate", "period_fraction",
]


def fill(row: dict) -> dict:
    """补全所有缺失字段为 0 或 ''."""
    out = {}
    for f in FIELDS:
        if f in ("cohort_id", "product", "measurement_model", "period", "currency"):
            out[f] = row.get(f, "")
        else:
            out[f] = row.get(f, 0.0)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 生成逻辑
# ──────────────────────────────────────────────────────────────────────────────

def generate_rows() -> list[dict]:
    rows = []
    for coh in COHORTS:
        cid      = coh["cohort_id"]
        product  = coh["product"]
        model    = coh["model"]
        currency = coh["currency"]
        cession  = coh["cession_rate"]
        dair_val = coh["dair"]
        opening  = coh["opening"].copy()   # {pvfcf, ra, csm, lc}

        # 2023Q4 — 期初行（仅余额，无 AOC 分项）
        rows.append(fill({
            "cohort_id":          cid,
            "product":            product,
            "measurement_model":  model,
            "period":             "2023Q4",
            "currency":           currency,
            "pvfcf_eom":          opening["pvfcf"],
            "ra_eom":             opening["ra"],
            "csm_eom":            opening["csm"],
            "lc_eom":             opening["lc"],
            "dair":               dair_val,
            "cession_rate":       cession,
            "period_fraction":    0.25,
        }))

        # 逐季度滚动
        bom = opening.copy()
        for i, qtr_mv in enumerate(coh["quarters"]):
            period_label = QUARTERS[i]
            eom = apply_movements(bom, qtr_mv, model)

            # exp_cf_release = pvfcf part + RA part (combined for AOC reporting)
            ra_rel    = qtr_mv.get("ra_release", 0.0)
            pvfcf_rel = qtr_mv.get("pvfcf_cf_release", 0.0)
            exp_cf    = ra_rel + pvfcf_rel

            # PAA: override exp_cf_release with PAA logic
            if model == "PAA":
                exp_cf = 0.0   # PAA uses premium_written/earned instead

            row = {
                "cohort_id":          cid,
                "product":            product,
                "measurement_model":  model,
                "period":             period_label,
                "currency":           currency,
                # EOM balances (computed, guaranteed consistent)
                "pvfcf_eom":          eom["pvfcf_eom"],
                "ra_eom":             eom["ra_eom"],
                "csm_eom":            eom["csm_eom"],
                "lc_eom":             eom["lc_eom"],
                # AOC items
                "new_biz_pvfcf":      qtr_mv.get("new_business", 0.0),
                "new_biz_ra":         0.0,
                "new_biz_csm":        0.0,
                "exp_cf_release":     exp_cf,
                "experience_var":     qtr_mv.get("experience_var", 0.0),
                "csm_amortisation":   qtr_mv.get("csm_amortisation", 0.0),
                "lc_reversal":        qtr_mv.get("lc_reversal", 0.0),
                "finance_charge_pl":  qtr_mv.get("finance_charge_pl", 0.0),
                "finance_charge_oci": qtr_mv.get("finance_charge_oci", 0.0),
                "assumption_chg_pl":  qtr_mv.get("assumption_chg_pl", 0.0),
                "assumption_chg_csm": qtr_mv.get("assumption_chg_csm", 0.0),
                "fx_effect":          qtr_mv.get("fx_effect", 0.0),
                # VFA
                "underlying_items_chg": qtr_mv.get("underlying_items_chg", 0.0),
                "exp_var_underlying":   qtr_mv.get("exp_var_underlying", 0.0),
                # PAA
                "premium_written":    qtr_mv.get("premium_written", 0.0),
                "premium_earned":     qtr_mv.get("premium_earned", 0.0),
                "iacf_amortisation":  qtr_mv.get("iacf_amortisation", 0.0),
                # Metadata
                "dair":               dair_val,
                "cession_rate":       cession,
                "period_fraction":    0.25,
            }
            rows.append(fill(row))

            # Roll forward: EOM becomes next period's BOM
            bom = {
                "pvfcf": eom["pvfcf_eom"],
                "ra":    eom["ra_eom"],
                "csm":   eom["csm_eom"],
                "lc":    eom["lc_eom"],
            }

    return rows


# ──────────────────────────────────────────────────────────────────────────────
# 写文件
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(base_dir, "actuarial_output.csv")

    rows = generate_rows()
    os.makedirs(base_dir, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[generate_multi_period] Written: {out_path}")
    print(f"  Rows: {len(rows)} ({len(COHORTS)} cohorts × {len(PERIODS)} periods)")
    print(f"  Cohorts: {[c['cohort_id'] for c in COHORTS]}")
    print(f"  Periods: {PERIODS}")
