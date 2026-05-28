# IFRS 17 Subledger — Full Process Demo

> **From actuarial output to GL entries — the complete IFRS 17 workflow, fully automated and visualised.**

[![Live Demo](https://img.shields.io/badge/🚀_Live_Demo-Streamlit_Cloud-FF4B4B?style=for-the-badge)](https://ifrs17-subledger.streamlit.app/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35%2B-FF4B4B?style=flat&logo=streamlit)](https://streamlit.io/)
[![IFRS 17](https://img.shields.io/badge/Standard-IFRS%2017-004B87?style=flat)](https://www.ifrs.org/issued-standards/list-of-standards/ifrs-17-insurance-contracts/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat)](LICENSE)

---

## What Is This?

A production-style **IFRS 17 subledger engine** that reads actuarial model output (Prophet / MoSes style CSVs)
and produces a complete set of IFRS 17 outputs — all wired together and deployable as an interactive web app.

This project demonstrates the **full IFRS 17 data pipeline** that actuarial and finance teams implement at
insurance companies, covering every major concept from the standard:

```
Actuarial System Output (Prophet / MoSes)
        actuarial_output.csv
               │
               ▼
    IFRS 17 AOC Engine
    ┌──────────────────────────────────────────────────────┐
    │  GMM · PAA · VFA   →  AOCResult (9-item breakdown)   │
    │  Onerous Contracts →  Loss Component (LC) lifecycle  │
    │  Quota Share RCA   →  Reinsurance Contract Asset     │
    │  Layered XL        →  Pre-computed effective rates    │
    └──────────────────────────────────────────────────────┘
               │
               ▼
    GL Journal Entry Generator
    ┌─────────────────────────────────────────────┐
    │  Double-entry bookkeeping  (DR = CR check)  │
    │  Chart of accounts from YAML config         │
    └─────────────────────────────────────────────┘
               │
               ▼
    Reports & Visualisation
    ┌─────────────────────────────────────────────────────────┐
    │  Executive Dashboard · AOC Waterfall · P&L · BS · GL   │
    │  Disclosure Notes (IFRS 17.97-132) · Sensitivity       │
    │  CSM Run-off Glide Path · OCI Reserve Accumulation     │
    │  Onerous Contract Lifecycle · Time Series Trends       │
    └─────────────────────────────────────────────────────────┘
```

---

## Features

### Measurement Models
| Model | Products | Key Feature |
|-------|----------|-------------|
| **GMM** (General Measurement Model) | Term Non-Par, Medical Non-Par LT | Full 9-item AOC, CSM buffer, OCI option |
| **PAA** (Premium Allocation Approach) | Medical ST, Rider Non-Par | Simplified UPR-based, IACF amortisation |
| **VFA** (Variable Fee Approach) | Whole Life Par, Endowment Par | CSM linked to underlying items (fund performance) |

### Analysis of Change (AOC) — 9 Items
1. New Business (Day-1 recognition)
2. Expected Cash Flow Release (incl. RA release) → **Insurance Revenue**
3. Experience Variance → **Insurance Service Expense**
4. CSM Amortisation → **Insurance Revenue**
5. LC Reversal / Additional LC → **ISR / ISE**
6. IFIE — P&L (locked-in DAIR unwind)
7. IFIE — OCI (current rate vs DAIR, under OCI option)
8. Assumption Changes → P&L
9. Assumption Changes → CSM *(intra-ICL transfer)*

### Reinsurance
- **Quota Share** — proportional cession at cohort-level; full RCA AOC mirror
- **Layered Excess-of-Loss** — 2-layer structure (Hanover Re / Munich Re / BOC Re) with pre-computed effective cession rates
  - Under total-loss assumption (term/life products): `effective_rate = layer_size × cession_rate / sum_insured`
  - Each reinsurer gets an independent RCA (separate legal entity under IFRS 17 §60)

### Onerous Contract Full Lifecycle ☠️ → 🟢
Demo cohort `MED_ONR_RECOVERY` walks through every IFRS 17 onerous contract event:
| Period | Event | LC | CSM |
|--------|-------|----|-----|
| 2023Q4 | Day-1 onerous — LC born | 750 | 0 |
| 2024Q1 | Bad experience — LC worsens | 830 ↑ | 0 |
| 2024Q2 | Stabilisation, service delivery release | 730 ↓ | 0 |
| 2024Q3 | Major assumption improvement | 115 ↓↓ | 0 |
| 2024Q4 | **LC = 0, CSM = 160 born** ✅ | 0 | 160 |

### Web Interface — 11 Tabs

| Tab | Content |
|-----|---------|
| 🏠 **Dashboard** | KPI cards · CSM Bridge waterfall · P&L waterfall · Cohort snapshot · Sparklines · Onerous highlight |
| 📈 **AOC Waterfall** | Interactive waterfall by cohort and period |
| 💹 **P&L Summary** | ISR / ISE / IFIE breakdown with charts |
| 🏦 **Balance Sheet** | ICL component stack (PVFCF / RA / CSM / LC) + RCA detail by reinsurer |
| 📒 **GL Entries** | Full double-entry journal, searchable by account |
| ⚖️ **Trial Balance** | Aggregated debit / credit totals |
| ✅ **Reconciliation** | BOM + Movements = EOM check per cohort |
| 📉 **Time Series** | Multi-quarter trends · CSM Glide Path · OCI Reserve accumulation |
| 🎯 **Sensitivity** | Tornado chart · Rate / mortality / expense shocks · Per-cohort breakdown |
| 📋 **Disclosures** | 6 IFRS 17 Notes (ICL rollforward · Revenue · IFIE · RCA · Maturity) + Excel download |
| 📖 **How It Works** | Plain-English explainer: GMM/PAA/VFA · AOC · OCI option · Reinsurance · Onerous lifecycle · Transition methods |

---

## Quick Start

### Option A — Live Web App (no install required)

**[→ Open ifrs17-subledger.streamlit.app](https://ifrs17-subledger.streamlit.app/)**

Click **▶ Run Subledger** in the sidebar to load the demo portfolio (8 cohorts × 5 periods).

### Option B — Run Locally

```bash
# 1. Clone
git clone https://github.com/woming98/ifrs17-subledger.git
cd ifrs17-subledger

# 2. Install
pip install -r requirements.txt

# 3. (Optional) Regenerate demo data
python data/generate_multi_period.py

# 4. Launch
streamlit run app/streamlit_app.py
```

### Option C — Use Your Own Actuarial Data

Upload your own `actuarial_output.csv` via the **Custom CSV Upload** sidebar widget.
Required columns (minimum):

```
cohort_id, product, measurement_model, period, currency,
pvfcf_eom, ra_eom, csm_eom, lc_eom,
exp_cf_release, experience_var, csm_amortisation, lc_reversal,
finance_charge_pl, finance_charge_oci,
assumption_chg_pl, assumption_chg_csm,
dair, cession_rate, period_fraction
```

---

## Demo Portfolio

8 cohorts covering all three measurement models and multiple reinsurance structures:

| Cohort | Product | Model | Reinsurance | Special Feature |
|--------|---------|-------|-------------|-----------------|
| `TERM_GMM_2022` | Term Non-Par 5Y | GMM | Layered XL (Hanover/MR/BOC) | Hanover 12% · MR 8% · BOC 32% (total-loss) |
| `MED_GMM_2021` | Medical Non-Par LT | GMM | QS 20% Munich Re | Adverse experience |
| `MED_GMM_ONR` | Medical Non-Par LT | GMM | None | Persistently onerous |
| **`MED_ONR_RECOVERY`** | Medical Non-Par LT | GMM | None | **Full onerous → profitable lifecycle** |
| `MED_PAA` | Medical Non-Par ST | PAA | QS 25% Hannover | Short-term PAA |
| `RIDER_PAA` | Rider Non-Par | PAA | None | No reinsurance |
| `WL_VFA_2019` | Whole Life Par | VFA | QS 20% Swiss Re | Market-driven CSM volatility |
| `ENDO_VFA_2022` | Endowment Par 20Y | VFA | None | Long-duration VFA |

---

## Project Structure

```
ifrs17-subledger/
├── config/
│   ├── chart_of_accounts.yaml     ← IFRS 17 GL account codes (customisable)
│   └── reinsurance_treaties.yaml  ← Treaty definitions with pre-computed effective rates
│
├── data/
│   ├── generate_multi_period.py   ← Demo data generator (8 cohorts × 5 periods)
│   └── actuarial_output.csv       ← Pre-generated demo data (40 rows)
│
├── src/
│   ├── models/
│   │   ├── base.py                ← AOCResult dataclass + abstract MeasurementModel
│   │   ├── gmm.py                 ← GMM AOC engine (9-item decomposition)
│   │   ├── paa.py                 ← PAA AOC engine
│   │   └── vfa.py                 ← VFA AOC engine (underlying items, intra-ICL)
│   ├── reinsurance.py             ← Quota Share RCA (backward-compatible)
│   ├── reinsurance_xl.py          ← Layered XL engine (proportional effective-rate approach)
│   ├── subledger.py               ← GL journal entry generator
│   ├── reconciliation.py          ← BOM+Movements=EOM checker + waterfall builder
│   ├── analytics.py               ← Multi-period time series aggregation
│   ├── disclosures.py             ← IFRS 17 Note disclosures (Notes 1–6)
│   └── report.py                  ← P&L / BS / Excel export
│
├── app/
│   └── streamlit_app.py           ← Streamlit web interface (11 tabs)
│
├── examples/
│   └── run_subledger.py           ← CLI entry point
│
└── requirements.txt
```

---

## Key IFRS 17 Concepts Demonstrated

### ICL Measurement Formula
```
ICL (profitable) = PVFCF + RA + CSM
ICL (onerous)    = PVFCF + RA − LC      (CSM = 0)
Net ICL          = Gross ICL − RCA
```

### CSM Run-off Glide Path
The **Time Series** tab projects the CSM balance forward 12 quarters based on the
historical amortisation rate, with ±25% scenario bands. This answers:
*"How many years of profit are still locked in the book?"*

### OCI Reserve Mechanics
Under the OCI option (GMM cohorts), the interest rate sensitivity of the ICL is
routed to **Other Comprehensive Income** rather than P&L. The app tracks the
cumulative OCI Reserve and estimates its response to yield curve shocks.

### Layered XL — Effective Cession Rates (Total-Loss Assumption)
For life products (term / whole life) where claims are binary (full benefit or zero),
the effective cession rate per reinsurer is calculated algebraically upfront:

```
effective_rate = layer_size × cession_rate / sum_insured

Example — TERM_GMM_2022 (sum insured = HKD 500k):
  Layer 1 (0–300k): Hanover Re 20%  → 300k × 20% / 500k = 12%
  Layer 2 (300–600k): Munich Re 20% → 200k × 20% / 500k =  8%
  Layer 2 (300–600k): BOC Re 80%    → 200k × 80% / 500k = 32%
  Retention                         → 300k × 80% / 500k = 48%
  Total                                                  = 100% ✓
```

In production, actuarial systems (Prophet/MoSes) output PVFCF per reinsurer directly.
The subledger reads these numbers and treats each reinsurer as a proportional (QS-style) RCA.

### IFRS 17 Disclosure Notes (Tab 📋)
Generates all 6 standard disclosure tables required by IFRS 17.97–132:
- Note 1: ICL Rollforward (by AOC line item × model)
- Note 2: ICL Balance by Component (PVFCF / RA / CSM / LC)
- Note 3: Analysis of Insurance Revenue
- Note 4: Insurance Finance Income/Expense (P&L vs OCI)
- Note 5: Reinsurance Contract Assets Movement
- Note 6: Maturity Profile (undiscounted cash flows by time bucket)

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Backend engine | Pure Python 3.10+ · `dataclasses` · `abc` |
| Data processing | `pandas` |
| Web UI | `Streamlit` |
| Interactive charts | `Plotly` |
| Config | `PyYAML` |
| Excel export | `openpyxl` (formatted: colour headers, bold totals, number format) |
| Deployment | Streamlit Community Cloud |

---

## Background

Built as a portfolio project to demonstrate actuarial and financial modelling expertise
in the IFRS 17 domain. The architecture mirrors the subledger systems used at major
insurance companies — where actuarial systems (Prophet, MoSes, AXIS) feed into
finance subledgers that generate compliant GL entries and regulatory disclosures.

---

## License

MIT © 2025
