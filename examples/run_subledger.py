"""
IFRS 17 Subledger — 命令行入口脚本

功能：
  1. 读取 prior_period.csv 和 current_period.csv
  2. 按测量模型（GMM / PAA）分别调用 AOC 引擎
  3. 计算 Quota Share 再保险 RCA
  4. 生成 GL 分录并验证借贷平衡
  5. 打印控制台摘要 + 输出 Excel 报表

用法：
    cd ifrs17-subledger
    python examples/run_subledger.py

    # 自定义输入/输出路径：
    python examples/run_subledger.py \\
        --prior   data/prior_period.csv \\
        --current data/current_period.csv \\
        --coa     config/chart_of_accounts.yaml \\
        --output  output/subledger_2024Q4.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys

# 将项目根目录加入 Python 路径（便于在任意目录下运行）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

from src.models.gmm import GMMModel
from src.models.paa import PAAModel
from src.reinsurance import compute_rca
from src.subledger import ChartOfAccounts, generate_journal
from src.reconciliation import reconcile_portfolio, check_journal_balance, aoc_waterfall
from src.report import pl_summary, bs_summary, aoc_detail, gl_detail, trial_balance, export_to_excel


# ──────────────────────────────────────────────────────────────────────────────
# 颜色输出（终端支持时）
# ──────────────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _ok(msg):   return f"{GREEN}✓ {msg}{RESET}"
def _fail(msg): return f"{RED}✗ {msg}{RESET}"
def _info(msg): return f"{CYAN}{msg}{RESET}"
def _head(msg): return f"\n{BOLD}{YELLOW}{'─'*60}\n  {msg}\n{'─'*60}{RESET}"


# ──────────────────────────────────────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────────────────────────────────────

def run(
    prior_path: str,
    current_path: str,
    coa_path: str,
    output_path: str,
    verbose: bool = True,
) -> None:

    # ── 1. 加载数据 ────────────────────────────────────────────────────────
    print(_head("Step 1: 加载输入数据"))
    prior_df   = pd.read_csv(prior_path)
    current_df = pd.read_csv(current_path)
    print(f"  prior_period   : {len(prior_df)} 条 cohort")
    print(f"  current_period : {len(current_df)} 条 cohort")

    coa    = ChartOfAccounts(coa_path)
    gmm    = GMMModel()
    paa    = PAAModel()

    # ── 2. 按 cohort 逐行计算 AOC ──────────────────────────────────────────
    print(_head("Step 2: 计算 AOC（Analysis of Change）"))
    aoc_list = []

    for _, eom_row in current_df.iterrows():
        cid   = str(eom_row["cohort_id"])
        model = str(eom_row["measurement_model"]).upper()

        # 找到对应的 BOM 行
        bom_matches = prior_df[prior_df["cohort_id"] == cid]
        if bom_matches.empty:
            print(f"  {RED}[WARN] cohort {cid} 在 prior_period.csv 中找不到，跳过{RESET}")
            continue
        bom_row = bom_matches.iloc[0]

        # 调用对应测量模型
        if model == "GMM":
            result = gmm.compute_aoc(bom_row, eom_row)
        elif model == "PAA":
            result = paa.compute_aoc(bom_row, eom_row)
        else:
            print(f"  {RED}[WARN] 未知测量模型 '{model}'（cohort: {cid}），跳过{RESET}")
            continue

        aoc_list.append(result)
        status = _ok(f"{cid:<35} AOC 对账通过 (diff={result.reconciliation_diff:+.4f})")
        if not result.reconciliation_ok:
            status = _fail(f"{cid:<35} AOC 对账失败 (diff={result.reconciliation_diff:+.4f})")
        print(f"  {status}")

    # ── 3. 计算 RCA（Quota Share 分出）────────────────────────────────────
    print(_head("Step 3: 计算 RCA（Quota Share 再保险合同资产）"))
    rca_list = []
    for aoc in aoc_list:
        if aoc.cession_rate > 0:
            rca = compute_rca(aoc)
            rca_list.append(rca)
            print(f"  {_ok(f'{aoc.cohort_id:<35} RCA cession={aoc.cession_rate:.0%}')}")
        else:
            print(f"  {_info(f'{aoc.cohort_id:<35} 无再保（cession=0%）')}")

    # ── 4. 生成 GL 分录 ─────────────────────────────────────────────────
    print(_head("Step 4: 生成 GL 分录"))
    rca_by_id = {r.cohort_id: r for r in rca_list}
    batches   = []
    for aoc in aoc_list:
        rca     = rca_by_id.get(aoc.cohort_id)
        batch   = generate_journal(aoc, coa, rca)
        batches.append(batch)
        balanced = "借贷平衡 ✓" if batch.is_balanced() else f"借贷不平衡 ✗ (diff={batch.total_debits()-batch.total_credits():+.2f})"
        status = _ok(f"{aoc.cohort_id:<35} {balanced} ({len(batch.lines)} 行)") \
                 if batch.is_balanced() else \
                 _fail(f"{aoc.cohort_id:<35} {balanced}")
        print(f"  {status}")

    # ── 5. 汇总对账检验 ───────────────────────────────────────────────────
    print(_head("Step 5: 汇总对账检验"))
    recon_df = reconcile_portfolio(aoc_list)
    if verbose:
        print(recon_df[["cohort_id","bom_icl","total_movements","eom_icl_calc","eom_icl_input","diff","ok"]].to_string(index=False))

    gl_bal_df = check_journal_balance(batches)
    all_ok    = gl_bal_df["balanced"].all()
    print(f"\n  GL 分录借贷平衡检验：{'全部通过 ✓' if all_ok else '存在不平衡 ✗'}")

    # ── 6. 打印 P&L 和 BS 摘要 ────────────────────────────────────────────
    print(_head("Step 6: 保险服务 P&L 摘要"))
    pl_df = pl_summary(aoc_list, rca_list)
    pd.set_option("display.float_format", "{:,.2f}".format)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    cols = ["cohort_id","product","model","insurance_revenue","insurance_service_expense",
            "net_insurance_result","ifie_pl","net_pl_after_ri"]
    print(pl_df[cols].to_string(index=False))

    print(_head("Step 6b: ICL / RCA 资产负债表摘要（期末）"))
    bs_df = bs_summary(aoc_list, rca_list)
    cols_bs = ["cohort_id","product","model","icl_total","rca_total","net_icl"]
    print(bs_df[cols_bs].to_string(index=False))

    print(_head("Step 6c: 试算平衡表"))
    tb_df = trial_balance(batches)
    print(tb_df.to_string(index=False))

    # ── 7. 导出 Excel ─────────────────────────────────────────────────────
    print(_head("Step 7: 导出 Excel 报表"))
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    export_to_excel(aoc_list, rca_list, batches, output_path)

    print(f"\n{BOLD}{GREEN}━━━ 全部完成 ━━━{RESET}")
    print(f"  输出文件：{output_path}")
    print(f"  cohort 数：{len(aoc_list)} | 分录行数：{sum(len(b.lines) for b in batches)}")


# ──────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="IFRS 17 Subledger — 从精算输出到 GL 分录的完整流程演示",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--prior",   default="data/prior_period.csv",
                        help="期初余额 CSV（默认 data/prior_period.csv）")
    parser.add_argument("--current", default="data/current_period.csv",
                        help="期末余额 + AOC CSV（默认 data/current_period.csv）")
    parser.add_argument("--coa",     default="config/chart_of_accounts.yaml",
                        help="科目表 YAML（默认 config/chart_of_accounts.yaml）")
    parser.add_argument("--output",  default="output/subledger_output.xlsx",
                        help="Excel 输出路径（默认 output/subledger_output.xlsx）")
    parser.add_argument("--quiet",   action="store_true",
                        help="减少终端输出")
    args = parser.parse_args()

    run(
        prior_path=args.prior,
        current_path=args.current,
        coa_path=args.coa,
        output_path=args.output,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
