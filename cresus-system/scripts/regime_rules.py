"""regime_rules.py — P27 体制自适应策略规则

读取 ~/cresus-bot/regime.json,返回当前行情体制对应的策略参数:
  - conf_bonus:        入场信心度门槛调整 (+/-)
  - risk_mult:         风险倍数 (在信心度分级之上再乘)
  - max_pos:           最大并发持仓数
  - long_blocked/short_blocked:  禁止某方向开仓
  - cooldown_*_h:      不同体制下的冷却期 (小时)

bot 模块统一从这里读取规则,确保策略一致性。

设计哲学: 山寨季放手脚 (1.5x risk, 门槛降低), 震荡死水严苛 (0.3x risk, 门槛抬高)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

REGIME_FILE = Path.home() / "cresus-bot" / "regime.json"

REGIME_RULES = {
    "ALT_SEASON_RUNNING": {
        "name_zh": "山寨行情",
        "conf_bonus":  -10,    # 门槛从 70 → 60
        "risk_mult":    1.5,   # 仓位 1.5x
        "max_pos":      5,
        "long_blocked":  False,
        "short_blocked": False,
        "cooldown_post_win_h":  0.25,    # 15min,鼓励顺势加仓
        "cooldown_post_loss_h": 1.0,     # 1h
        "cooldown_7d_loser_h":  12.0,    # 12h (从默认 24h 降)
    },
    "BTC_DOMINATING": {
        "name_zh": "BTC 主导",
        "conf_bonus":   0,
        "risk_mult":    1.0,
        "max_pos":      3,
        "long_blocked":  False,
        "short_blocked": False,
        "cooldown_post_win_h":  1.0,
        "cooldown_post_loss_h": 6.0,
        "cooldown_7d_loser_h":  24.0,
    },
    "PUMP_AND_DUMP_RISK": {
        "name_zh": "拉砸风险",
        "conf_bonus":  +5,
        "risk_mult":    0.5,
        "max_pos":      2,
        "long_blocked":  True,    # 禁开多 (拉完会砸)
        "short_blocked": False,
        "cooldown_post_win_h":  2.0,
        "cooldown_post_loss_h": 12.0,
        "cooldown_7d_loser_h":  36.0,
    },
    "RISK_OFF": {
        "name_zh": "风险释放",
        "conf_bonus":   0,
        "risk_mult":    1.0,
        "max_pos":      3,
        "long_blocked":  True,    # 禁开多 (下行环境)
        "short_blocked": False,
        "cooldown_post_win_h":  1.0,
        "cooldown_post_loss_h": 6.0,
        "cooldown_7d_loser_h":  24.0,
    },
    "RANGE_BORING": {
        "name_zh": "震荡死水",
        "conf_bonus":  +15,    # 门槛从 70 → 85, 几乎不开
        "risk_mult":    0.3,
        "max_pos":      1,
        "long_blocked":  False,
        "short_blocked": False,
        "cooldown_post_win_h":  4.0,
        "cooldown_post_loss_h": 24.0,
        "cooldown_7d_loser_h":  48.0,
    },
}

DEFAULT_RULES = {
    "name_zh": "未知体制",
    "conf_bonus":   0,
    "risk_mult":    1.0,
    "max_pos":      3,
    "long_blocked":  False,
    "short_blocked": False,
    "cooldown_post_win_h":  1.0,
    "cooldown_post_loss_h": 6.0,
    "cooldown_7d_loser_h":  24.0,
}


def get_regime() -> str:
    """读取当前行情体制名 (regime.json), 失败返回空字符串."""
    try:
        data = json.loads(REGIME_FILE.read_text(encoding="utf-8"))
        return data.get("regime", "")
    except Exception:
        return ""


def get_rules(regime: Optional[str] = None) -> dict:
    """返回指定体制 (或当前体制) 对应的策略参数. 未知体制返回 DEFAULT_RULES."""
    if regime is None:
        regime = get_regime()
    return REGIME_RULES.get(regime, DEFAULT_RULES)


def describe_current() -> str:
    """For debug/logging: 当前体制 + 主要参数."""
    regime = get_regime()
    r = get_rules(regime)
    return (f"regime={regime or 'UNKNOWN'} ({r['name_zh']}) | "
            f"conf_bonus={r['conf_bonus']:+d} risk_mult={r['risk_mult']:.1f}x "
            f"max_pos={r['max_pos']} "
            f"L-blocked={r['long_blocked']} S-blocked={r['short_blocked']}")


# ============================================================================
# Self-test
# ============================================================================
if __name__ == "__main__":
    import sys
    print(describe_current())
    print()
    print("=== 五种体制对照表 ===")
    print(f"{'Regime':<35}  {'conf':>5}  {'risk':>6}  {'pos':>3}  blocked")
    for regime in ["ALT_SEASON_RUNNING", "BTC_DOMINATING", "PUMP_AND_DUMP_RISK", "RISK_OFF", "RANGE_BORING"]:
        r = REGIME_RULES[regime]
        blocked = []
        if r["long_blocked"]:  blocked.append("LONG")
        if r["short_blocked"]: blocked.append("SHORT")
        bs = ",".join(blocked) if blocked else "—"
        print(f"{regime + ' (' + r['name_zh'] + ')':<35}  {r['conf_bonus']:>+5d}  {r['risk_mult']:>5.1f}x  {r['max_pos']:>3}  {bs}")
    sys.exit(0)
