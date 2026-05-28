"""
IFRS 17 — GL 分录生成器（Subledger Journal Entry Engine）

功能：
  根据每个 cohort 的 AOCResult（直接业务 + RCA），
  生成标准化的借 / 贷会计分录，科目代码来自 chart_of_accounts.yaml。

分录逻辑：
  IFRS 17 对直接业务的处理原则：
    - ICL 负债增加 → Cr ICL / Dr Insurance Service Expense（或 IFIE 等）
    - ICL 负债减少 → Dr ICL / Cr Insurance Revenue（或 IFIE 等）
  本模块对每一 AOC 项目分别生成一笔分录（或两笔拆分分录），
  以保持完整的审计链。

GL 科目体系（见 chart_of_accounts.yaml）：
  资产负债表：
    2100 ICL-LRC PVFCF | 2110 ICL-LRC RA | 2120 ICL-LRC CSM | 2130 ICL-LRC LC
    2200 ICL-LIC
    2300 RCA PVFCF | 2310 RCA RA | 2320 RCA CSM
    1900 Cash / Payable（理赔 / 保费收付）
  利润表（P&L）：
    4100 Insurance Revenue (ISR)
    4200 Insurance Service Expense (ISE)
    4300 IFIE — P&L
  其他综合收益（OCI）：
    5100 OCI — Insurance Finance
"""

import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import yaml

from .models.base import AOCResult
from .reinsurance import RCASummary
from typing import List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class JournalLine:
    """单笔借贷明细行"""
    entry_id: str           # 分录批号（同一分录的借贷用相同 entry_id）
    cohort_id: str
    period: str
    aoc_item: str           # AOC 项目标签（如 "④ CSM摊销"）
    account_code: str
    account_name: str
    debit: float = 0.0
    credit: float = 0.0
    currency: str = "HKD"
    note: str = ""

    @property
    def net(self) -> float:
        """借 - 贷（正 = 借方净额）"""
        return self.debit - self.credit


@dataclass
class JournalBatch:
    """一个 cohort × period 的完整分录批"""
    cohort_id: str
    period: str
    lines: List[JournalLine] = field(default_factory=list)

    def add(
        self,
        aoc_item: str,
        debit_code: str,
        debit_name: str,
        credit_code: str,
        credit_name: str,
        amount: float,
        currency: str = "HKD",
        note: str = "",
    ) -> None:
        """添加一笔借贷分录（amount 恒为正数）"""
        if abs(amount) < 1e-6:
            return   # 零金额不记录
        eid = uuid.uuid4().hex[:8]
        self.lines.append(JournalLine(
            entry_id=eid,
            cohort_id=self.cohort_id,
            period=self.period,
            aoc_item=aoc_item,
            account_code=debit_code,
            account_name=debit_name,
            debit=abs(amount),
            credit=0.0,
            currency=currency,
            note=note,
        ))
        self.lines.append(JournalLine(
            entry_id=eid,
            cohort_id=self.cohort_id,
            period=self.period,
            aoc_item=aoc_item,
            account_code=credit_code,
            account_name=credit_name,
            debit=0.0,
            credit=abs(amount),
            currency=currency,
            note=note,
        ))

    def total_debits(self) -> float:
        return sum(l.debit for l in self.lines)

    def total_credits(self) -> float:
        return sum(l.credit for l in self.lines)

    def is_balanced(self, tol: float = 0.01) -> bool:
        return abs(self.total_debits() - self.total_credits()) <= tol


# ──────────────────────────────────────────────────────────────────────────────
# 科目查找（从 chart_of_accounts.yaml 加载）
# ──────────────────────────────────────────────────────────────────────────────

class ChartOfAccounts:
    """科目表包装器"""

    def __init__(self, yaml_path: str):
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # 将科目列表平铺为 {code: name} 字典
        self._accounts: dict[str, str] = {}
        for section in data.get("accounts", {}).values():
            for item in section:
                self._accounts[str(item["code"])] = item["name"]

    def name(self, code: str) -> str:
        return self._accounts.get(str(code), f"Unknown({code})")

    def pair(self, code: str) -> tuple[str, str]:
        """返回 (code, name) 元组"""
        return (str(code), self.name(code))


# ──────────────────────────────────────────────────────────────────────────────
# 主分录生成函数
# ──────────────────────────────────────────────────────────────────────────────

def generate_journal(
    aoc: AOCResult,
    coa: ChartOfAccounts,
    rca: Optional[RCASummary] = None,
    rcas: Optional[List[RCASummary]] = None,
) -> JournalBatch:
    """
    根据 AOCResult（直接业务 + 可选 RCA 列表）生成 GL 分录批。

    参数
    ----
    rca  : 单个 RCASummary（向后兼容旧接口）
    rcas : 多个 RCASummary 列表（新接口，支持分层 XL 多家再保人）
    若两者均提供，rcas 优先。

    会计逻辑：
      ICL 负债增加（正数 movement）→ Dr 对侧 / Cr ICL
      ICL 负债减少（负数 movement）→ Dr ICL / Cr 对侧
    """
    batch = JournalBatch(cohort_id=aoc.cohort_id, period=aoc.period)
    ccy = aoc.currency

    # ── 科目代码常量 ──────────────────────────────────────────────────────
    ICL_PVFCF   = "2100"
    ICL_RA      = "2110"
    ICL_CSM     = "2120"
    ICL_LC      = "2130"
    ICL_LIC     = "2200"
    RCA_PVFCF   = "2300"
    RCA_RA      = "2310"
    RCA_CSM     = "2320"
    CASH        = "1900"
    ISR         = "4100"
    ISE         = "4200"
    IFIE_PL     = "4300"
    OCI         = "5100"
    RETAINED    = "3100"

    def icl_code(component: str) -> str:
        """根据分量返回 ICL 科目代码"""
        return {"pvfcf": ICL_PVFCF, "ra": ICL_RA, "csm": ICL_CSM, "lc": ICL_LC}.get(component, ICL_PVFCF)

    def dr_icl_cr_pl(label: str, amount: float, pl_code: str, note: str = "") -> None:
        """ICL 负债减少（revenue / benefit）：Dr ICL / Cr P&L"""
        if amount < 0:   # 减少负债
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), pl_code, coa.name(pl_code), -amount, ccy, note)
        else:            # 增加负债（费用超出预期）
            batch.add(label, pl_code, coa.name(pl_code), ICL_PVFCF, coa.name(ICL_PVFCF), amount, ccy, note)

    # ====================================================================
    # 直接业务分录
    # ====================================================================

    # ① New Business — Initial Recognition
    if abs(aoc.new_business) > 1e-6:
        label = "① New Business — Initial Recognition"
        nb = aoc.new_business
        if nb > 0:
            batch.add(label, RETAINED, coa.name(RETAINED), ICL_PVFCF, coa.name(ICL_PVFCF), abs(nb), ccy,
                      "New business: net PVFCF + RA + CSM recognised on Day 1")
        else:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), RETAINED, coa.name(RETAINED), abs(nb), ccy)

    # ② Expected CF Release (incl. RA release) → Insurance Revenue
    if abs(aoc.expected_cf_release) > 1e-6:
        label = "② Expected CF Release (incl. RA release)"
        batch.add(
            label,
            ICL_PVFCF, coa.name(ICL_PVFCF),
            ISR,        coa.name(ISR),
            abs(aoc.expected_cf_release), ccy,
            "Expected CF release recognised as Insurance Revenue",
        ) if aoc.expected_cf_release < 0 else batch.add(
            label,
            ISE, coa.name(ISE),
            ICL_PVFCF, coa.name(ICL_PVFCF),
            abs(aoc.expected_cf_release), ccy,
        )

    # ③ Experience Variance → Insurance Service Expense
    if abs(aoc.experience_variance) > 1e-6:
        label = "③ Experience Variance (Actual vs Expected)"
        if aoc.experience_variance > 0:
            batch.add(label, ISE, coa.name(ISE), ICL_PVFCF, coa.name(ICL_PVFCF),
                      aoc.experience_variance, ccy, "Actual claims exceeded expectations")
        else:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), ISE, coa.name(ISE),
                      abs(aoc.experience_variance), ccy, "Actual claims better than expected")

    # ④ CSM Amortisation → Insurance Revenue
    if abs(aoc.csm_amortisation) > 1e-6:
        label = "④ CSM Amortisation"
        batch.add(
            label,
            ICL_CSM, coa.name(ICL_CSM),
            ISR,      coa.name(ISR),
            abs(aoc.csm_amortisation), ccy,
            "CSM amortised to Insurance Revenue based on coverage units",
        ) if aoc.csm_amortisation < 0 else batch.add(
            label,
            ISE, coa.name(ISE),
            ICL_CSM, coa.name(ICL_CSM),
            abs(aoc.csm_amortisation), ccy,
        )

    # ⑤ LC Reversal / Additional LC (Onerous Contract)
    if abs(aoc.lc_reversal) > 1e-6:
        label = "⑤ LC Reversal / Additional LC (Onerous)"
        if aoc.lc_reversal < 0:
            batch.add(label, ICL_LC, coa.name(ICL_LC), ISR, coa.name(ISR),
                      abs(aoc.lc_reversal), ccy, "Loss Component reversed as service is delivered")
        else:
            batch.add(label, ISE, coa.name(ISE), ICL_LC, coa.name(ICL_LC),
                      aoc.lc_reversal, ccy, "Contract became onerous — Loss Component recognised")

    # ⑥ IFIE — P&L (discount unwinding at locked-in DAIR)
    if abs(aoc.finance_charge_pl) > 1e-6:
        label = "⑥ IFIE — P&L (DAIR unwind)"
        if aoc.finance_charge_pl > 0:
            batch.add(label, IFIE_PL, coa.name(IFIE_PL), ICL_PVFCF, coa.name(ICL_PVFCF),
                      aoc.finance_charge_pl, ccy, "Discount unwinding at DAIR (P&L)")
        else:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), IFIE_PL, coa.name(IFIE_PL),
                      abs(aoc.finance_charge_pl), ccy)

    # ⑦ IFIE — OCI (current rate vs DAIR difference)
    if abs(aoc.finance_charge_oci) > 1e-6:
        label = "⑦ IFIE — OCI (current rate vs DAIR)"
        if aoc.finance_charge_oci > 0:
            batch.add(label, OCI, coa.name(OCI), ICL_PVFCF, coa.name(ICL_PVFCF),
                      aoc.finance_charge_oci, ccy, "Rates fell — PVFCF increased, routed to OCI")
        else:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), OCI, coa.name(OCI),
                      abs(aoc.finance_charge_oci), ccy, "Rates rose — PVFCF decreased, routed to OCI")

    # ⑧ Assumption Changes → P&L
    if abs(aoc.assumption_chg_pl) > 1e-6:
        label = "⑧ Assumption Changes → P&L"
        if aoc.assumption_chg_pl > 0:
            batch.add(label, ISE, coa.name(ISE), ICL_PVFCF, coa.name(ICL_PVFCF),
                      aoc.assumption_chg_pl, ccy, "Adverse assumption change → ISE")
        else:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), ISE, coa.name(ISE),
                      abs(aoc.assumption_chg_pl), ccy, "Favourable assumption change → ISE (negative)")

    # ⑨ Assumption Changes → CSM (profitable contract; absorbed by CSM not P&L)
    if abs(aoc.assumption_chg_csm) > 1e-6:
        label = "⑨ Assumption Changes → CSM"
        if aoc.assumption_chg_csm > 0:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), ICL_CSM, coa.name(ICL_CSM),
                      aoc.assumption_chg_csm, ccy, "Favourable non-economic change absorbed by CSM")
        else:
            batch.add(label, ICL_CSM, coa.name(ICL_CSM), ICL_PVFCF, coa.name(ICL_PVFCF),
                      abs(aoc.assumption_chg_csm), ccy, "Adverse non-economic change reduces CSM first")

    # FX 汇率影响
    if abs(aoc.fx_effect) > 1e-6:
        label = "FX Effect"
        if aoc.fx_effect > 0:
            batch.add(label, OCI, coa.name(OCI), ICL_PVFCF, coa.name(ICL_PVFCF),
                      aoc.fx_effect, ccy, "FX translation difference")
        else:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), OCI, coa.name(OCI),
                      abs(aoc.fx_effect), ccy)

    # VFA: Underlying items change (intra-ICL PVFCF ↔ CSM)
    if abs(getattr(aoc, "underlying_items_chg", 0.0)) > 1e-6:
        _und_chg = getattr(aoc, "underlying_items_chg", 0.0)
        label = "VFA — Underlying Items Change → CSM"
        if _und_chg > 0:
            batch.add(label, ICL_PVFCF, coa.name(ICL_PVFCF), ICL_CSM, coa.name(ICL_CSM),
                      _und_chg, ccy, "Underlying items gain absorbed by CSM")
        else:
            batch.add(label, ICL_CSM, coa.name(ICL_CSM), ICL_PVFCF, coa.name(ICL_PVFCF),
                      abs(_und_chg), ccy, "Underlying items loss charged to CSM")

    # ====================================================================
    # 再保险 RCA 分录（支持多合同：分层 XL 每家再保人一笔）
    # ====================================================================
    # rcas 优先（新接口），否则退回旧接口 rca
    rca_list_to_process: list = []
    if rcas:
        rca_list_to_process = [r for r in rcas if r.cession_rate > 0]
    elif rca is not None and rca.cession_rate > 0:
        rca_list_to_process = [rca]

    for _rca in rca_list_to_process:
        _add_rca_entries(batch, _rca, coa, ccy)

    return batch


def _add_rca_entries(
    batch: JournalBatch,
    rca: RCASummary,
    coa: ChartOfAccounts,
    ccy: str,
) -> None:
    """
    追加 RCA（再保险合同资产）分录。
    RCA 是资产端，科目 2300/2310/2320。
    对侧科目与直接业务相同（ISR / ISE / IFIE / OCI），但方向相反。
    """
    RCA_PVFCF = "2300"
    RCA_RA    = "2310"
    RCA_CSM   = "2320"
    ISR       = "4100"
    ISE       = "4200"
    IFIE_PL   = "4300"
    OCI       = "5100"
    RETAINED  = "3100"

    ri_tag = f"{rca.reinsurer} / {rca.layer_label}"

    def rca_add(label, dr_code, dr_name, cr_code, cr_name, amount):
        if abs(amount) > 1e-6:
            batch.add(f"[RCA {ri_tag}] {label}", dr_code, dr_name, cr_code, cr_name, abs(amount), ccy)

    # ① New Business
    nb = rca.rca_new_business
    if abs(nb) > 1e-6:
        if nb < 0:
            rca_add("New Business", RCA_PVFCF, coa.name(RCA_PVFCF), RETAINED, coa.name(RETAINED), abs(nb))
        else:
            rca_add("New Business", RETAINED, coa.name(RETAINED), RCA_PVFCF, coa.name(RCA_PVFCF), nb)

    # ② Expected CF Release
    ecf = rca.rca_expected_cf_release
    if abs(ecf) > 1e-6:
        if ecf > 0:
            rca_add("Expected CF Release", ISR, coa.name(ISR), RCA_PVFCF, coa.name(RCA_PVFCF), ecf)
        else:
            rca_add("Expected CF Release", RCA_PVFCF, coa.name(RCA_PVFCF), ISR, coa.name(ISR), abs(ecf))

    # ③ Experience Variance
    ev = rca.rca_experience_variance
    if abs(ev) > 1e-6:
        if ev < 0:
            rca_add("Experience Variance", RCA_PVFCF, coa.name(RCA_PVFCF), ISE, coa.name(ISE), abs(ev))
        else:
            rca_add("Experience Variance", ISE, coa.name(ISE), RCA_PVFCF, coa.name(RCA_PVFCF), ev)

    # ④ CSM Amortisation
    ca = rca.rca_csm_amortisation
    if abs(ca) > 1e-6:
        if ca > 0:
            rca_add("CSM Amortisation", ISR, coa.name(ISR), RCA_CSM, coa.name(RCA_CSM), ca)
        else:
            rca_add("CSM Amortisation", RCA_CSM, coa.name(RCA_CSM), ISR, coa.name(ISR), abs(ca))

    # ⑥ IFIE P&L
    ip = rca.rca_finance_charge_pl
    if abs(ip) > 1e-6:
        if ip < 0:
            rca_add("IFIE P&L", RCA_PVFCF, coa.name(RCA_PVFCF), IFIE_PL, coa.name(IFIE_PL), abs(ip))
        else:
            rca_add("IFIE P&L", IFIE_PL, coa.name(IFIE_PL), RCA_PVFCF, coa.name(RCA_PVFCF), ip)

    # ⑦ IFIE OCI
    io = rca.rca_finance_charge_oci
    if abs(io) > 1e-6:
        if io < 0:
            rca_add("IFIE OCI", RCA_PVFCF, coa.name(RCA_PVFCF), OCI, coa.name(OCI), abs(io))
        else:
            rca_add("IFIE OCI", OCI, coa.name(OCI), RCA_PVFCF, coa.name(RCA_PVFCF), io)
