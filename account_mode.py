from __future__ import annotations


def get_account_mode(equity: float) -> str:
    """根据净值划分账户模式。"""
    if equity < 10_000:
        return "GROWTH"
    if equity < 50_000:
        return "BALANCED"
    return "DEFENSIVE"


def get_mode_controls(mode: str) -> dict:
    """返回账户模式控制参数。"""
    mode = (mode or "").upper()

    controls = {
        "GROWTH": {"max_positions": 6, "allow_pyramiding": True},
        "BALANCED": {"max_positions": 4, "allow_pyramiding": True},
        "DEFENSIVE": {"max_positions": 2, "allow_pyramiding": False},
    }

    return controls.get(mode, controls["BALANCED"])
