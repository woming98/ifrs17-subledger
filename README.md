# IFRS 17 Subledger Demo

> **从精算输出到 GL 分录的完整 IFRS 17 流程演示**  
> 覆盖 GMM（Building Block Approach）+ PAA，含 Quota Share 再保险 RCA，全程可视化。

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35%2B-red)](https://streamlit.io/)
[![IFRS 17](https://img.shields.io/badge/Standard-IFRS%2017-green)](https://www.ifrs.org/issued-standards/list-of-standards/ifrs-17-insurance-contracts/)

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **GMM AOC 引擎** | 9 项变动分析：新业务 · 预期现金流释放 · 经验差异 · CSM 摊销 · LC 回转 · IFIE(P&L/OCI) · 假设变更 · FX |
| **PAA AOC 引擎** | 简化 AOC：未赚保费 · 赚取保费 · IACF 摊销 · 经验差异 |
| **Quota Share RCA** | 分出比例再保险合同资产，与直接业务 AOC 联动 |
| **GL 分录生成** | 完整借贷分录，科目来自可配置的 `chart_of_accounts.yaml` |
| **对账检验** | BOM + 变动 = EOM 自动验证；GL 借贷平衡检验 |
| **Streamlit 界面** | 瀑布图 · P&L 拆解 · BS 堆叠图 · 科目筛选 · Excel 导出 |

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 生成 Demo 数据

```bash
python data/generate_demo.py
```

生成 5 个 cohort（2024Q4 期间）：
- `TERM_GMM_2022Q4`：Term Non-Par 5年期（GMM, profitable, QS 30%）
- `MED_GMM_2021Q4`：长期 Medical Non-Par（GMM, profitable, QS 20%）
- `MED_GMM_ONEROUS_2020Q4`：长期 Medical Non-Par（GMM, **onerous**）
- `MED_PAA_2024Q3`：短期 Medical Non-Par（PAA, QS 25%）
- `RIDER_PAA_2024Q3`：短期 Rider Non-Par（PAA）

### 3. 命令行运行

```bash
python examples/run_subledger.py
```

输出：`output/subledger_output.xlsx`（含 P&L / BS / AOC / GL / 试算平衡 5 个 Sheet）

### 4. 启动 Streamlit 可视化界面

```bash
streamlit run app/streamlit_app.py
```

---

## 项目结构

```
ifrs17-subledger/
├── config/
│   └── chart_of_accounts.yaml     ← IFRS 17 标准科目表（可自定义）
├── data/
│   ├── generate_demo.py            ← Demo 数据生成脚本
│   ├── prior_period.csv            ← 期初余额（BOM）
│   └── current_period.csv          ← 期末余额（EOM）+ AOC 分项
├── src/
│   ├── models/
│   │   ├── base.py                 ← AOCResult 数据类 + 抽象基类
│   │   ├── gmm.py                  ← GMM AOC 引擎（9 项分解）
│   │   └── paa.py                  ← PAA AOC 引擎
│   ├── reinsurance.py              ← Quota Share RCA 计算
│   ├── subledger.py                ← GL 分录生成器
│   ├── reconciliation.py           ← 对账检验 + 瀑布汇总
│   └── report.py                   ← P&L / BS / Excel 导出
├── examples/
│   └── run_subledger.py            ← 命令行入口
├── app/
│   └── streamlit_app.py            ← Streamlit 可视化界面
└── requirements.txt
```

---

## 输入数据格式

### `prior_period.csv`（期初余额）

| 字段 | 说明 |
|------|------|
| `cohort_id` | 合同组唯一标识 |
| `product` | 产品名称 |
| `measurement_model` | `GMM` 或 `PAA` |
| `period` | 期间（如 `2024Q3`） |
| `pvfcf_eom` | 期末 PVFCF（即为下期 BOM） |
| `ra_eom` | 期末 RA |
| `csm_eom` | 期末 CSM（PAA 为 0） |
| `lc_eom` | 期末 LC（亏损部分） |

### `current_period.csv`（期末余额 + AOC 分项）

在期初格式基础上增加：

| 字段 | 说明 |
|------|------|
| `exp_cf_release` | 预期现金流释放（负数 = 减少负债） |
| `experience_var` | 经验差异（正数 = 实际更差） |
| `csm_amortisation` | CSM 摊销（负数） |
| `lc_reversal` | LC 回转（负数 = 减少亏损） |
| `finance_charge_pl` | IFIE P&L 部分（DAIR × BOM_ICL × dt） |
| `finance_charge_oci` | IFIE OCI 部分（利率变动效应） |
| `assumption_chg_pl` | 假设变更 → P&L |
| `assumption_chg_csm` | 假设变更 → CSM（profitable cohort） |
| `dair` | 锁定折现率（年化，用于计算 IFIE） |
| `cession_rate` | QS 分出比例（0–1） |
| `premium_written` | PAA：当期承保保费 |
| `premium_earned` | PAA：当期赚取保费（负数） |
| `iacf_amortisation` | PAA：获取成本摊销 |

---

## IFRS 17 概念说明

### GMM（General Measurement Model / BBA）

```
ICL_LRC = PVFCF + RA + CSM    ← profitable cohort
ICL_LRC = PVFCF + RA - LC     ← onerous cohort
```

**AOC 9 项拆解**：
1. 新业务首次确认（Day-1 entry）
2. 预期现金流释放（含 RA release）→ **Insurance Revenue**
3. 经验差异（实际 vs 预期）→ **Insurance Service Expense**
4. CSM 摊销（按覆盖单位）→ **Insurance Revenue**
5. 亏损合同 LC 回转 → **Insurance Revenue / ISE**
6. IFIE P&L（以 DAIR 展开折现）→ **IFIE（P&L）**
7. IFIE OCI（当前利率 vs DAIR）→ **OCI**（使用 OCI option）
8. 假设变更 → P&L（RA 变更 / onerous 调整）
9. 假设变更 → CSM（非经济性假设，profitable cohort 吸收）

### PAA（Premium Allocation Approach）

适用于保障期 ≤ 1 年的合同（IFRS 17.53）：

```
LRC = Unearned Premium Reserve (UPR) - IACF_asset + LC（若 onerous）
```

**关键差异**：无 CSM，ISR = 赚取保费，简化 IFIE。

### Quota Share RCA

```
RCA_ICL = -cession_rate × gross_ICL（再保险合同资产 = 直接业务负债的镜像）
RCA_AOC_item = -cession_rate × gross_AOC_item
```

---

## 路线图

- [x] Phase 1：GMM + PAA + QS RCA + Streamlit
- [ ] Phase 2：VFA（Variable Fee Approach，参与型）
- [ ] Phase 3：LIC（已发生理赔负债）分拆
- [ ] Phase 4：Prophet 输出直接对接（CSV schema 映射）
- [ ] Phase 5：多期间滚动（季度连续运行）

---

## License

MIT
