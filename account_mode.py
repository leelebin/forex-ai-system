from __future__ import annotations


def get_account_mode(equity: float) -> str:
    """
    账户模式（与风险分段保持一致）:
    - TURBO      : equity < $2 000   — 最高激进，冲刺翻倍目标
    - GROWTH     : equity < $5 000   — 积极成长，开始适度保护
    - BALANCED   : equity < $20 000  — 均衡配置，降仓稳健运行
    - DEFENSIVE  : equity >= $20 000 — 保守模式，资金保值为主
    """
    if equity < 2_000:
        return "TURBO"
    if equity < 5_000:
        return "GROWTH"
    if equity < 20_000:
        return "BALANCED"
    return "DEFENSIVE"


def get_mode_controls(mode: str) -> dict:
    """返回各账户模式对应的持仓管理参数。"""
    mode = (mode or "").upper()

    controls = {
        # 最多5个仓位，允许加仓 — 充分利用小账户灵活性
        "TURBO":     {"max_positions": 5, "allow_pyramiding": True},
        # 4个仓位，允许加仓 — 仍处于成长阶段
        "GROWTH":    {"max_positions": 4, "allow_pyramiding": True},
        # 3个仓位，允许有限加仓
        "BALANCED":  {"max_positions": 3, "allow_pyramiding": True},
        # 最多2个仓位，禁止加仓
        "DEFENSIVE": {"max_positions": 2, "allow_pyramiding": False},
    }

    return controls.get(mode, controls["BALANCED"])
