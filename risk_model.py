from __future__ import annotations


def _base_risk_percent(equity: float) -> float:
    """小资金高风险，大资金低风险（分段映射）。"""
    if equity < 5_000:
        return 6.0
    if equity < 20_000:
        return 3.0
    if equity < 100_000:
        return 1.5
    return 0.8


def get_risk_percent(equity: float, drawdown: float = 0.0, is_new_peak: bool = False) -> float:
    """
    返回风险百分比（单位：%）。

    规则：
    - 基础风险：小资金高风险，大资金低风险
    - 创新高：风险 * 0.7
    - 回撤 > 10%：风险 * 1.3
    - 最终限制在 [0.3, 10.0]
    """
    risk = _base_risk_percent(max(0.0, float(equity)))

    if is_new_peak:
        risk *= 0.7
    elif drawdown > 10.0:
        risk *= 1.3

    return max(0.3, min(10.0, round(risk, 3)))
