from __future__ import annotations


def _base_risk_percent(equity: float) -> float:
    """
    分段风险映射（与激进模式 tiers 保持一致）:
    - TURBO   : equity < $1 000  -> 8%
    - HIGH    : equity < $2 000  -> 6%
    - GROWTH  : equity < $5 000  -> 3.5%
    - MODERATE: equity < $20 000 -> 1.5%
    - SAFE    : equity >= $20 000 -> 0.8%
    """
    if equity < 1_000:
        return 8.0
    if equity < 2_000:
        return 6.0
    if equity < 5_000:
        return 3.5
    if equity < 20_000:
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
        # 创新高时轻度收缩（不要过早锁定利润，保持进攻性）
        risk *= 0.85
    elif drawdown > 15.0:
        # 回撤较大时适度加仓（追回亏损），但上限 10%
        risk *= 1.2

    return max(0.3, min(10.0, round(risk, 3)))
