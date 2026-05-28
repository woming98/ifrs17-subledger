"""
IFRS 17 — 分层超额赔付再保险（Layered Excess-of-Loss）+ LEV 支持

新功能（Phase 3）：
  - RCATreaty dataclass：合同结构（层次边界、再保人、分保比例）
  - lev_lognormal()：对数正态 LEV 公式（纯 Python，无需 scipy）
  - layer_expected_loss()：层函数 = LEV(upper) − LEV(lower)
  - load_treaties()：从 reinsurance_treaties.yaml 加载合同配置
  - compute_rca_treaties()：一个 cohort → 多个 RCASummary（每家再保人一个）

注意：在实际生产中，Prophet/MoSes 直接输出各层 PVFCF，
      LEV 计算由精算系统内部完成，subledger 只读已算好的数字。
      本模块的 LEV 实现仅用于 demo 演示分层逻辑。

IFRS 17 引用：§60–70A（再保合同计量）
"""

import math
from dataclasses import dataclass
from typing import List, Optional

import yaml

from .reinsurance import RCASummary, _build_rca


# ──────────────────────────────────────────────────────────────────────────────
# LEV（Limited Expected Value）
# ──────────────────────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """标准正态 CDF（无需 scipy）"""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def lev_lognormal(d: float, mu: float, sigma: float) -> float:
    """
    对数正态分布的有限期望值 E[X ∧ d]：

        E[X ∧ d] = e^(μ+σ²/2) · Φ((ln d − μ − σ²)/σ)
                 + d · [1 − Φ((ln d − μ)/σ)]

    参数
    ----
    d     : 截断上限（赔付金额）
    mu    : 对数均值（ln 赔付 的均值）
    sigma : 对数标准差
    """
    if d <= 0.0:
        return 0.0
    mean = math.exp(mu + 0.5 * sigma * sigma)
    z1   = (math.log(d) - mu - sigma * sigma) / sigma
    z2   = (math.log(d) - mu) / sigma
    return mean * _norm_cdf(z1) + d * (1.0 - _norm_cdf(z2))


def layer_expected_loss(
    lower: float,
    upper: Optional[float],
    mu: float,
    sigma: float,
) -> float:
    """
    [lower, upper] 层的期望赔付（层函数）：
        E[Layer_(a,b)] = E[X∧b] − E[X∧a]

    upper=None 表示纯 QS（无上限），用 1e9 近似 ∞。
    """
    upper_val = upper if upper is not None else 1e9
    return lev_lognormal(upper_val, mu, sigma) - lev_lognormal(lower, mu, sigma)


# ──────────────────────────────────────────────────────────────────────────────
# RCATreaty 数据结构
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RCATreaty:
    """
    单个再保合同的结构定义。

    字段
    ----
    treaty_id    : 合同唯一标识（如 "TERM_HAN_L1"）
    cohort_id    : 对应直接业务 cohort
    reinsurer    : 再保公司名称
    layer_lower  : 层次下限（HKD）
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
        lo = f"{self.layer_lower / 1000:.0f}k"
        hi = f"{self.layer_upper / 1000:.0f}k"
        return f"XL {lo}–{hi}"


# ──────────────────────────────────────────────────────────────────────────────
# 合同配置加载
# ──────────────────────────────────────────────────────────────────────────────

def load_treaties(yaml_path: str):
    """
    从 reinsurance_treaties.yaml 加载合同配置。

    返回
    ----
    treaties_by_cohort : dict[str, List[RCATreaty]]
    claim_dist         : dict（赔付分布参数）
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    treaties_by_cohort: dict = {}
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
# 有效分保比例计算（LEV 分层权重）
# ──────────────────────────────────────────────────────────────────────────────

def _effective_rate(
    treaty: RCATreaty,
    mu: float,
    sigma: float,
    max_claim: float,
) -> float:
    """
    计算单个合同的有效分保比例：
      QS:  effective_rate = cession_rate
      XL:  effective_rate = cession_rate × E[Layer] / E[Total up to max_claim]
    """
    if treaty.is_quota_share:
        return treaty.cession_rate

    e_layer = layer_expected_loss(treaty.layer_lower, treaty.layer_upper, mu, sigma)
    e_total = layer_expected_loss(0.0, max_claim, mu, sigma)
    if e_total <= 0:
        return 0.0
    return treaty.cession_rate * (e_layer / e_total)


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口
# ──────────────────────────────────────────────────────────────────────────────

def compute_rca_treaties(
    gross_aoc,
    treaties: List[RCATreaty],
    claim_dist: dict,
) -> List[RCASummary]:
    """
    按 reinsurance_treaties.yaml 计算该 cohort 的所有 RCA。

    每个合同独立返回一个 RCASummary（分层 XL 则每层每家再保人一个）。

    参数
    ----
    gross_aoc   : 直接业务 AOCResult
    treaties    : 该 cohort 的合同列表（由 load_treaties 提供）
    claim_dist  : 赔付分布参数字典（来自 YAML claim_distribution 节）
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
        results.append(
            _build_rca(gross_aoc, eff_rate, treaty.treaty_id, treaty.reinsurer, treaty.layer_label)
        )
    return results
