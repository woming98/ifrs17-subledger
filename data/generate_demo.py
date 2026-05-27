"""
Demo 数据生成脚本

生成 prior_period.csv 和 current_period.csv，
包含 5 个 cohort（2 GMM profitable + 1 GMM onerous + 2 PAA）。

运行：
    cd ifrs17-subledger
    python data/generate_demo.py
"""

import csv
import os

# ──────────────────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────────────────

def write_csv(path, rows, fieldnames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[generate_demo] 已写入 {path}（{len(rows)} 行）")


# ──────────────────────────────────────────────────────────────────────────────
# 定义 5 个 cohort 的完整参数
#
# 金额单位：HKD 千元（'000 HKD）
# 期间：2024Q4（季度，period_fraction=0.25）
# ──────────────────────────────────────────────────────────────────────────────

# cohort 1：Term Non-Par 5 年期（GMM, profitable）
# BOM ICL = 15,000 + 600 + 2,400 = 18,000
TERM_GMM = {
    "prior": {
        "cohort_id": "TERM_GMM_2022Q4",
        "product": "Term Non-Par 5Y",
        "measurement_model": "GMM",
        "period": "2024Q3",
        "currency": "HKD",
        "pvfcf_eom": 15000.00,
        "ra_eom":      600.00,
        "csm_eom":    2400.00,
        "lc_eom":        0.00,
    },
    "current": {
        "cohort_id": "TERM_GMM_2022Q4",
        "product": "Term Non-Par 5Y",
        "measurement_model": "GMM",
        "period": "2024Q4",
        "currency": "HKD",
        # EOM 余额
        # BOM_ICL=18000; 变动=-705（excl. assumption_chg_csm intra转）
        # EOM_ICL=17295 = 17295-560-2200=14535 (PVFCF)
        "pvfcf_eom": 14535.00,
        "ra_eom":      560.00,
        "csm_eom":    2200.00,   # BOM 2400 - amort 250 + chg 50
        "lc_eom":        0.00,
        # AOC 分项（单位：千元）
        "new_biz_pvfcf":      0.00,
        "new_biz_ra":         0.00,
        "new_biz_csm":        0.00,
        "exp_cf_release":  -550.00,    # 预期现金流释放（含 RA -40），减少负债
        "experience_var":    80.00,    # 实际赔付略高于预期
        "csm_amortisation": -250.00,   # CSM 按覆盖单位摊销
        "lc_reversal":        0.00,
        "finance_charge_pl":  72.00,   # BOM_ICL(18000) × 1.6%(DAIR) × 0.25
        "finance_charge_oci": -57.00,  # 利率上升，PVFCF 市值下降 → OCI 收益
        "assumption_chg_pl":   0.00,
        "assumption_chg_csm":  50.00,  # 死亡率假设改善 → 有利变更吸收至 CSM
        "fx_effect":           0.00,
        "dair":               0.016,   # 锁定折现率（年化 1.6%）
        "cession_rate":        0.30,   # QS 30% 分出
        "period_fraction":     0.25,
    }
}

# cohort 2：长期 Medical Non-Par（GMM, profitable）
# BOM ICL = 8,200 + 410 + 980 = 9,590
MED_GMM = {
    "prior": {
        "cohort_id": "MED_GMM_2021Q4",
        "product": "Medical Non-Par LT",
        "measurement_model": "GMM",
        "period": "2024Q3",
        "currency": "HKD",
        "pvfcf_eom": 8200.00,
        "ra_eom":     410.00,
        "csm_eom":    980.00,
        "lc_eom":       0.00,
    },
    "current": {
        "cohort_id": "MED_GMM_2021Q4",
        "product": "Medical Non-Par LT",
        "measurement_model": "GMM",
        "period": "2024Q4",
        "currency": "HKD",
        # EOM_ICL=9280; RA=385; CSM=980-140-25=815; PVFCF=9280-385-815=8080
        "pvfcf_eom": 8080.00,
        "ra_eom":     385.00,
        "csm_eom":    815.00,
        "lc_eom":       0.00,
        "new_biz_pvfcf":     0.00,
        "new_biz_ra":        0.00,
        "new_biz_csm":       0.00,
        "exp_cf_release":  -310.00,   # 预期医疗理赔释放
        "experience_var":   120.00,   # 医疗通胀 → 实际理赔超出预期
        "csm_amortisation": -140.00,
        "lc_reversal":        0.00,
        "finance_charge_pl":  38.36,  # 9590 × 1.6% × 0.25
        "finance_charge_oci": -28.36, # 利率变动 OCI
        "assumption_chg_pl":  10.00,  # 医疗通胀假设上调 → P&L 费用
        "assumption_chg_csm": -25.00, # 改善死亡率 → CSM 吸收（有利）
        "fx_effect":           0.00,
        "dair":               0.016,
        "cession_rate":        0.20,  # QS 20%
        "period_fraction":     0.25,
    }
}

# cohort 3：长期 Medical Non-Par（GMM, ONEROUS）
# BOM ICL = 3,500 + 175 - (-200) = 3,875（LC=200，有亏损）
MED_GMM_ONEROUS = {
    "prior": {
        "cohort_id": "MED_GMM_ONEROUS_2020Q4",
        "product": "Medical Non-Par LT (Onerous)",
        "measurement_model": "GMM",
        "period": "2024Q3",
        "currency": "HKD",
        "pvfcf_eom": 3500.00,
        "ra_eom":     175.00,
        "csm_eom":      0.00,   # onerous：无 CSM
        "lc_eom":      200.00,  # LC = 200（亏损部分）
    },
    "current": {
        "cohort_id": "MED_GMM_ONEROUS_2020Q4",
        "product": "Medical Non-Par LT (Onerous)",
        "measurement_model": "GMM",
        "period": "2024Q4",
        "currency": "HKD",
        # BOM_ICL=3475; 变动=-231; EOM_ICL=3244
        # LC=200-70=130; RA=162; PVFCF=3244-162+130=3212
        "pvfcf_eom": 3212.00,
        "ra_eom":     162.00,
        "csm_eom":      0.00,
        "lc_eom":      130.00,  # LC 部分释放（随保障服务）
        "new_biz_pvfcf":     0.00,
        "new_biz_ra":        0.00,
        "new_biz_csm":       0.00,
        "exp_cf_release":  -200.00,  # 预期现金流释放
        "experience_var":    30.00,  # 实际略差
        "csm_amortisation":   0.00,  # onerous：无 CSM 摊销
        "lc_reversal":       -70.00, # LC 随服务期回转（减少亏损负债）→ Revenue
        "finance_charge_pl":  13.90, # 3475 × 1.6% × 0.25
        "finance_charge_oci": -9.90,
        "assumption_chg_pl":   5.00, # 假设微幅恶化，直接进 P&L（因为 onerous）
        "assumption_chg_csm":  0.00,
        "fx_effect":           0.00,
        "dair":               0.016,
        "cession_rate":        0.00, # 无再保
        "period_fraction":     0.25,
    }
}

# cohort 4：短期 Medical Non-Par（PAA）
# BOM LRC (UPR) = 1,200（纯 PAA，无 CSM）
MED_PAA = {
    "prior": {
        "cohort_id": "MED_PAA_2024Q3",
        "product": "Medical Non-Par ST (PAA)",
        "measurement_model": "PAA",
        "period": "2024Q3",
        "currency": "HKD",
        "pvfcf_eom": 1200.00,  # UPR（未赚保费准备金）
        "ra_eom":      60.00,  # IACF 资产余额（正数，后续会被抵消）
        "csm_eom":      0.00,
        "lc_eom":        0.00,
    },
    "current": {
        "cohort_id": "MED_PAA_2024Q3",
        "product": "Medical Non-Par ST (PAA)",
        "measurement_model": "PAA",
        "period": "2024Q4",
        "currency": "HKD",
        # BOM_ICL=1260; 变动=-615; EOM_ICL=645; RA=30; PVFCF=615
        "pvfcf_eom":   615.00,  # UPR 期末余额
        "ra_eom":       30.00,  # IACF 余额减少
        "csm_eom":       0.00,
        "lc_eom":         0.00,
        # PAA 特有字段
        "premium_written":     0.00,    # 本季度无新续期（年缴保费）
        "premium_earned":   -600.00,    # 赚取保费 600（减少 LRC）
        "iacf_amortisation":  30.00,    # IACF 摊销（费用）
        "exp_cf_release":      0.00,    # PAA 中已含在 premium_earned
        "experience_var":     15.00,    # 实际理赔超出预期
        "lc_reversal":         0.00,
        "finance_charge_pl":   0.00,    # PAA 通常不折现
        "fx_effect":           0.00,
        "cession_rate":        0.25,    # QS 25%
        "period_fraction":     0.25,
    }
}

# cohort 5：Rider Non-Par（PAA，短期附加险）
# BOM LRC = 800
RIDER_PAA = {
    "prior": {
        "cohort_id": "RIDER_PAA_2024Q3",
        "product": "Rider Non-Par (PAA)",
        "measurement_model": "PAA",
        "period": "2024Q3",
        "currency": "HKD",
        "pvfcf_eom": 800.00,
        "ra_eom":     40.00,
        "csm_eom":     0.00,
        "lc_eom":      0.00,
    },
    "current": {
        "cohort_id": "RIDER_PAA_2024Q3",
        "product": "Rider Non-Par (PAA)",
        "measurement_model": "PAA",
        "period": "2024Q4",
        "currency": "HKD",
        # BOM_ICL=840; 变动=-412; EOM_ICL=428; RA=20; PVFCF=408
        "pvfcf_eom":   408.00,
        "ra_eom":       20.00,
        "csm_eom":       0.00,
        "lc_eom":         0.00,
        "premium_written":     0.00,
        "premium_earned":   -400.00,
        "iacf_amortisation":  20.00,
        "exp_cf_release":      0.00,
        "experience_var":      8.00,
        "lc_reversal":         0.00,
        "finance_charge_pl":   0.00,
        "fx_effect":           0.00,
        "cession_rate":        0.00,
        "period_fraction":     0.25,
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 字段顺序定义
# ──────────────────────────────────────────────────────────────────────────────

PRIOR_FIELDS = [
    "cohort_id", "product", "measurement_model", "period", "currency",
    "pvfcf_eom", "ra_eom", "csm_eom", "lc_eom",
]

CURRENT_FIELDS = [
    "cohort_id", "product", "measurement_model", "period", "currency",
    # EOM 余额
    "pvfcf_eom", "ra_eom", "csm_eom", "lc_eom",
    # GMM AOC 分项
    "new_biz_pvfcf", "new_biz_ra", "new_biz_csm",
    "exp_cf_release", "experience_var",
    "csm_amortisation", "lc_reversal",
    "finance_charge_pl", "finance_charge_oci",
    "assumption_chg_pl", "assumption_chg_csm",
    "fx_effect",
    # PAA 特有
    "premium_written", "premium_earned", "iacf_amortisation",
    # 元数据
    "dair", "cession_rate", "period_fraction",
]

# ──────────────────────────────────────────────────────────────────────────────
# 补全缺失字段为 0
# ──────────────────────────────────────────────────────────────────────────────

def fill_defaults(row: dict, fields: list) -> dict:
    out = {}
    for f in fields:
        out[f] = row.get(f, 0.0) if f not in ("cohort_id", "product", "measurement_model", "period", "currency") else row.get(f, "")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 生成文件
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))

    cohorts = [TERM_GMM, MED_GMM, MED_GMM_ONEROUS, MED_PAA, RIDER_PAA]

    prior_rows   = [fill_defaults(c["prior"],   PRIOR_FIELDS)   for c in cohorts]
    current_rows = [fill_defaults(c["current"], CURRENT_FIELDS) for c in cohorts]

    write_csv(os.path.join(base_dir, "prior_period.csv"),   prior_rows,   PRIOR_FIELDS)
    write_csv(os.path.join(base_dir, "current_period.csv"), current_rows, CURRENT_FIELDS)

    print("\n[generate_demo] Demo 数据生成完毕。")
    print("  prior_period.csv   → 期初余额（BOM）")
    print("  current_period.csv → 期末余额（EOM）+ AOC 分项")
