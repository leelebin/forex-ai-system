from __future__ import annotations

NORMAL = "NORMAL"
HIGH_VOL = "HIGH_VOL"
EXTREME = "EXTREME"


def get_market_permissions(state: str) -> dict:
    """
    根据市场状态返回交易行为权限。
    """
    state = (state or NORMAL).upper()
    if state == EXTREME:
        return {"allow_open": False, "allow_add": False, "allow_close": True, "force_reduce": True}
    if state == HIGH_VOL:
        return {"allow_open": False, "allow_add": False, "allow_close": True, "force_reduce": False}
    return {"allow_open": True, "allow_add": True, "allow_close": True, "force_reduce": False}
