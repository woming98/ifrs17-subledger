"""
IFRS 17 — 再保险合同资产（Reinsurance Contract Asset, RCA）

支持两种再保结构：
  1. Quota Share (QS)   — 比例再保，layer_upper = None
  2. Layered Excess of Loss (XL) — 分层超额赔付，按层次边界切分

分层 XL 的有效分保比例通过 LEV（Limited Expected Value）计算：
  effective_rate = cession_rate_in_layer × E[Layer expected loss] / E[Total]

在实际生产中，Prophet / MoSes 直接输出各层 PVFCF；
本 demo 用 LEV 演示分层逻辑，两种方式结果等价。

IFRS 17 关键条款：
  IFRS 17.60–70A: 再保合同适用相同测量方法
  IFRS 17.63:     PVFCF_ceded 按合同条款计算
  IFRS 17.64:     亏损合同的分出 LC 同样确认为 RCA 资产
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

import yaml

if TYPE_CHECKING:
    from .models.base import AOCResult


# ──────────────────────────────────────────────────────────────────────────────
# LEV（Limited Expected Value）函数
# ──────────────────────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """标准正态 CDF，使用 math.erfc 实现，无需 scipy"""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def lev_lognormal(d: float, mu: float, sigma: float) -> float:
    """
    对数正态分布 LogNormal(mu, sigma) 的有限期望值：
        E[X ∧ d] = E[min(X, d)]

    公式：
        E[X ∧ d] = e^(μ+σ²/2) · Φ((ln d − μ − σ²)/σ)
                 + d · [1 − Φ((ln d − μ)/σ)]

    参数
    ----
    d     : 截断上限（赔付金额）
    mu    : 对数均值（ln 赔付 的均值）
    sigma : 对数标准差

    在实际业务中，此函数由 Prophet 内部调用，subledger 读取已计算的 PVFCF。
    """
    if d <= 0.0:
        return 0.0
    mean   = math.exp(mu + 0.5 * sigma * sigma)
    z1     = (math.log(d) - mu - sigma * sigma) / sigma
    z2     = (math.log(d) - mu) / sigma
    return mean * _norm_cdf(z1) + d * (1.0 - _norm_cdf(z2))


def layer_expected_loss(
    lower: float,
    upper: Optional[float],
    mu: float,
    sigma: float,
) -> float:
    """
    [lower, upper] 层的期望赔付（层函数 = LEV 差值）：
        E[Layer_(a,b)] = E[X∧b] − E[X∧a]

    若 upper 为 None（QS），返回 E[X] − E[X∧lower]，近似 E[X∧∞]−E[X∧lower]
    （实际用 1e9 代替 ∞）。
    """
    upper_val = upper if upper is not None else 1e9
    return lev_lognormal(upper_val, mu, sigma) - lev_lognormal(lower, mu, sigma)


# ──────────────────────────────────────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RCATreaty:
    """
    单个再保合同（treaty）的结构定义。

    字段
    ----
    treaty_id    : 合同唯一标识（如 "TERM_HAN_L1"）
    cohort_id    : 对应直接业务 cohort
    reinsurer    : 再保公司名称（如 "Hanover Re"）
    layer_lower  : 层次下限（HKD）；QS 时 = 0
    layer_upper  : 层次上限（HKD）；QS 时 = None
    cession_rate : 该层内分给本再保公司的比例（0–1）
    """
    treaty_id:    str
    cohort_id:    str
    reinsurer:    str
    layer_lower:  float
    layer_upper:  Optional[float]
    cession_rate: float

    @property
    def is_quota_share(self) -> bool:
        return self.layer_upper is None

    @property
    def layer_label(self) -> str:
        if self.is_quota_share:
            return "QS"
        lo = f"{self.layer_lower/1000:.0f}k"
        hi = f"{self.layer_upper/1000:.0f}k"
        return f"XL {lo}–{hi}"


@dataclass
class RCASummary:
    """
    单个再保合同的 RCA 摘要（分出部分）。

    新增字段 treaty_id / reinsurer / layer_label 用于多合同展示。
    """

    cohort_id:    str
    period:       str
    cession_rate: float       # 有效分保比例（已含 LEV 分层权重）
    treaty_id:    str = "QS"
    reinsurer:    str = "Unknown"
    layer_label:  str = "QS"

    # ── 期初 / 期末 RCA（资产端，正数 = 资产）────────────────────────────
    rca_bom_pvfcf: float = 0.0
    rca_bom_ra:    float = 0.0
    rca_bom_csm:   float = 0.0
    rca_bom_icl:   float = 0.0

    rca_eom_pvfcf: float = 0.0
    rca_eom_ra:    float = 0.0
    rca_eom_csm:   float = 0.0
    rca_eom_icl:   float = 0.0

    # ── RCA AOC 各项 ─────────────────────────────────────────────────────
    rca_new_business:        float = 0.0
    rca_expected_cf_release: float = 0.0
    rca_experience_variance: float = 0.0
    rca_csm_amortisation:    float = 0.0
    rca_finance_charge_pl:   float = 0.0
    rca_finance_charge_oci:  float = 0.0
    rca_assumption_chg_pl:   float = 0.0
    rca_assumption_chg_csm:  float = 0.0
    rca_fx_effect:           float = 0.0

    # ── 对账 ─────────────────────────────────────────────────────────────
    reconciliation_ok:   bool  = True
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
        """RCA 端保险收入（从直接业务分出方视角）"""
        return -(self.rca_expected_cf_release + self.rca_csm_amortisation)

    @property
    def rca_ifie_pl(self) -> float:
        return self.rca_finance_charge_pl

    @property
    def rca_ifie_oci(self) -> float:
        return self.rca_finance_charge_oci

    def aoc_summary(self) -> dict:
        tag = f"[{self.reinsurer} / {self.layer_label}]"
        return {
            f"RCA BOM ICL {tag}":             self.rca_bom_icl,
            f"① New Business {tag}":          self.rca_new_business,
            f"② Expected CF Release {tag}":   self.rca_expected_cf_release,
            f"③ Experience Variance {tag}":   self.rca_experience_variance,
            f"④ CSM Amortisation {tag}":      self.rca_csm_amortisation,
            f"⑥ IFIE — P&L {tag}":            self.rca_finance_charge_pl,
            f"⑦ IFIE — OCI {tag}":            self.rca_finance_charge_oci,
            f"⑧ Assumption Chg → P&L {tag}":  self.rca_assumption_chg_pl,
            f"⑨ Assumption Chg → CSM {tag}":  self.rca_assumption_chg_csm,
            f"FX Effect {tag}":               self.rca_fx_effect,
            f"RCA EOM ICL Calc {tag}":        self.rca_bom_icl + self.rca_total_movements,
            f"RCA EOM ICL Input {tag}":       self.rca_eom_icl,
            f"Recon Diff {tag}":              self.reconciliation_diff,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 合同配置加载
# ──────────────────────────────────────────────────────────────────────────────

def load_treaties(yaml_path: str) -> tuple[dict[str, List[RCATreaty]], dict]:
    """
    从 reinsurance_treaties.yaml 加载合同配置。

    返回
    ----
    treaties_by_cohort : {cohort_id: [RCATreaty, ...]}
    claim_dist         : 赔付分布参数字典 {cohort_id: {mu, sigma, max_claim}}
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    treaties_by_cohort: dict[str, List[RCATreaty]] = {}
    for t in cfg.get("treaties", []):
        cid = str(t["cohort_id"])
        treaty = RCATreaty(
            treaty_id=str(t["treaty_id"]),
            cohort_id=cid,
            reinsurer=str(t["reinsurer"]),
            layer_lower=float(t.get("layer_lower", 0)),
            layer_upper=None if t.get("layer_upper") is None else float(t["layer_upper"]),
            cession_rate=float(t["cession_rate"]),
        )
        treaties_by_cohort.setdefault(cid, []).append(treaty)

    claim_dist = cfg.get("claim_distribution", {})
    return treaties_by_cohort, claim_dist


# ──────────────────────────────────────────────────────────────────────────────
# 核心计算：单合同 → RCASummary
# ──────────────────────────────────────────────────────────────────────────────

def _effective_rate(
    treaty: RCATreaty,
    mu: float,
    sigma: float,
    max_claim: float,
) -> float:
    """
    计算单个合同的有效分保比例（已含 LEV 分层权重）：

    pure QS：effective_rate = cession_rate
    XL 分层：effective_rate = cession_rate × E[Layer] / E[Total up to max_claim]

    注：此处的"有效比例"用于按比例缩放直接业务 ICL 分量，
        等价于 Prophet 输出各层预期回收赔付 / 总预期赔付。
    """
    if treaty.is_quota_share:
        return treaty.cession_rate

    e_layer = layer_expected_loss(
        treaty.layer_lower, treaty.layer_upper, mu, sigma
    )
    e_total = layer_expected_loss(0.0, max_claim, mu, sigma)
    if e_total <= 0:
        return 0.0
    return treaty.cession_rate * (e_layer / e_total)


def _build_rca(gross_aoc: "AOCResult", eff_rate: float, treaty: RCATreaty) -> RCASummary:
    """
    按有效分保比例构建 RCASummary。
    逻辑与原 compute_rca 相同：RCA = -eff_rate × gross_ICL 分量（资产方向相反）
    """
    q = eff_rate

    rca_bom_pvfcf = -q * gross_aoc.bom_pvfcf
    rca_bom_ra    = -q * gross_aoc.bom_ra
    rca_bom_csm   = -q * gross_aoc.bom_csm
    rca_bom_icl   = rca_bom_pvfcf + rca_bom_ra + rca_bom_csm

    rca_eom_pvfcf = -q * gross_aoc.eom_pvfcf
    rca_eom_ra    = -q * gross_aoc.eom_ra
    rca_eom_csm   = -q * gross_aoc.eom_csm
    rca_eom_icl   = rca_eom_pvfcf + rca_eom_ra + rca_eom_csm

    rca = RCASummary(
        cohort_id=gross_aoc.cohort_id,
        period=gross_aoc.period,
        cession_rate=q,
        treaty_id=treaty.treaty_id,
        reinsurer=treaty.reinsurer,
        layer_label=treaty.layer_label,
        rca_bom_pvfcf=rca_bom_pvfcf,
        rca_bom_ra=rca_bom_ra,
        rca_bom_csm=rca_bom_csm,
        rca_bom_icl=rca_bom_icl,
        rca_eom_pvfcf=rca_eom_pvfcf,
        rca_eom_ra=rca_eom_ra,
        rca_eom_csm=rca_eom_csm,
        rca_eom_icl=rca_eom_icl,
        rca_new_business=       -q * gross_aoc.new_business,
        rca_expected_cf_release=-q * gross_aoc.expected_cf_release,
        rca_experience_variance=-q * gross_aoc.experience_variance,
        rca_csm_amortisation=   -q * gross_aoc.csm_amortisation,
        rca_finance_charge_pl=  -q * gross_aoc.finance_charge_pl,
        rca_finance_charge_oci= -q * gross_aoc.finance_charge_oci,
        rca_assumption_chg_pl=  -q * gross_aoc.assumption_chg_pl,
        rca_assumption_chg_csm= -q * gross_aoc.assumption_chg_csm,
        rca_fx_effect=          -q * gross_aoc.fx_effect,
    )

    # 对账
    computed_eom = rca.rca_bom_icl + rca.rca_total_movements
    diff = computed_eom - rca.rca_eom_icl
    rca.reconciliation_diff = round(diff, 4)
    rca.reconciliation_ok   = abs(diff) <= 0.01

    return rca


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口
# ──────────────────────────────────────────────────────────────────────────────

def compute_rca_treaties(
    gross_aoc: "AOCResult",
    treaties: List[RCATreaty],
    claim_dist: dict,
) -> List[RCASummary]:
    """
    按 reinsurance_treaties.yaml 定义计算该 cohort 的所有 RCA。

    每个合同独立返回一个 RCASummary（分层 XL 则每层每家再保公司一个）。

    参数
    ----
    gross_aoc   : 直接业务 AOCResult
    treaties    : 该 cohort 的合同列表（由 load_treaties 提供）
    claim_dist  : 赔付分布参数字典（来自 YAML claim_distribution 节）

    返回
    ----
    List[RCASummary]：空列表表示无再保安排
    """
    if not treaties:
        return []

    cid  = gross_aoc.cohort_id
    dist = claim_dist.get(cid) or claim_dist.get("default") or {}
    mu        = float(dist.get("mu",        11.5))
    sigma     = float(dist.get("sigma",     0.96))
    max_claim = float(dist.get("max_claim", 600000))

    results: List[RCASummary] = []
    for treaty in treaties:
        eff_rate = _effective_rate(treaty, mu, sigma, max_claim)
        if eff_rate <= 0.0:
            continue
        results.append(_build_rca(gross_aoc, eff_rate, treaty))

    return results


def compute_rca(
    gross_aoc: "AOCResult",
    cession_rate: Optional[float] = None,
) -> RCASummary:
    """
    原有 Quota Share 接口（向后兼容）。
    当不使用 treaty YAML 时调用此函数。
    """
    q = cession_rate if cession_rate is not None else gross_aoc.cession_rate

    if q <= 0.0:
        return RCASummary(
            cohort_id=gross_aoc.cohort_id,
            period=gross_aoc.period,
            cession_rate=0.0,
        )

    qs_treaty = RCATreaty(
        treaty_id="QS",
        cohort_id=gross_aoc.cohort_id,
        reinsurer="Reinsurer",
        layer_lower=0.0,
        layer_upper=None,
        cession_rate=q,
    )
    return _build_rca(gross_aoc, q, qs_treaty)
