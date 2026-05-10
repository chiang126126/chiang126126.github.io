#!/bin/bash
# P27-A 一键应用脚本: 把 regime_rules.py 装到 bot, 并 patch signal_router + okx_executor
#
# 用法 (在 Mac 上):
#   cd ~/chiang126126.github.io && git pull
#   bash cresus-system/scripts/apply_p27a.sh
#   pkill -f main_loop.py    # KeepAlive 重启使新代码生效

set -e

BOT=~/cresus-bot
REPO=~/chiang126126.github.io

echo "=== P27-A 体制自适应应用 ==="

# 1. 复制 regime_rules.py 到 bot
mkdir -p "$BOT/src/risk"
cp "$REPO/cresus-system/scripts/regime_rules.py" "$BOT/src/risk/regime_rules.py"
echo "✓ regime_rules.py → bot/src/risk/"

# 2. 复制 regime_backtest.py 到 bot
mkdir -p "$BOT/scripts"
cp "$REPO/cresus-system/scripts/regime_backtest.py" "$BOT/scripts/regime_backtest.py"
echo "✓ regime_backtest.py → bot/scripts/"

# 3. patch signal_router.py + okx_executor.py
cd "$BOT" && python3 - <<'PATCH'
import ast, sys
from pathlib import Path

# ============================================
# patch signal_router.py
# ============================================
sr = Path("src/execution/signal_router.py")
code = sr.read_text()

# 添加 regime_rules import (跟 macro_calendar 同位置)
old1 = '''# P21: 宏观事件黑名单(模块不存在则降级 no-op)
try:
    from risk.macro_calendar import get_blackout_decision
except ImportError:
    def get_blackout_decision():
        return {"blocked": False, "tier": None, "threshold_bonus": 0, "reason": None}'''
new1 = '''# P21: 宏观事件黑名单(模块不存在则降级 no-op)
try:
    from risk.macro_calendar import get_blackout_decision
except ImportError:
    def get_blackout_decision():
        return {"blocked": False, "tier": None, "threshold_bonus": 0, "reason": None}

# P27-A: 体制自适应策略规则
try:
    from risk.regime_rules import get_rules as get_regime_rules, get_regime
except ImportError:
    def get_regime_rules(*a, **kw):
        return {"conf_bonus": 0, "risk_mult": 1.0, "max_pos": 3,
                "long_blocked": False, "short_blocked": False}
    def get_regime():
        return ""'''

# 修改 route_decision 入口逻辑: 加 regime 读取 + conf_bonus + 方向锁定
old2 = '''    # P21: 宏观事件黑名单 (CORE → 强制阻塞, OBSERVE → 入场门槛 +10)
    macro = get_blackout_decision()
    effective_open_threshold = settings.confidence_open_threshold + macro["threshold_bonus"]

    if direction in ("LONG", "SHORT") and conf >= effective_open_threshold:'''
new2 = '''    # P21+P27-A: 宏观事件 + 体制自适应 共同决定有效门槛
    macro = get_blackout_decision()
    regime = get_regime()
    regime_rules = get_regime_rules(regime)
    effective_open_threshold = (settings.confidence_open_threshold
                                + macro["threshold_bonus"]
                                + regime_rules["conf_bonus"])

    # P27-A: 体制锁定方向 (拉砸风险/风险释放禁开多)
    if direction == "LONG" and regime_rules.get("long_blocked"):
        logger.info(f"[regime] {snap.symbol} LONG@{conf} blocked by regime={regime}")
        return
    if direction == "SHORT" and regime_rules.get("short_blocked"):
        logger.info(f"[regime] {snap.symbol} SHORT@{conf} blocked by regime={regime}")
        return

    if direction in ("LONG", "SHORT") and conf >= effective_open_threshold:'''

for label, old in [("import", old1), ("route_decision", old2)]:
    if old not in code:
        print(f"❌ signal_router patch [{label}] target not found", file=sys.stderr)
        sys.exit(1)
code = code.replace(old1, new1).replace(old2, new2)
ast.parse(code)
sr.write_text(code)
print(f"✓ {sr} patched")

# ============================================
# patch okx_executor.py: _compute_size 加 regime risk_mult
# ============================================
ox = Path("src/execution/okx_executor.py")
code = ox.read_text()

old3 = '''    base_risk    = float(getattr(settings, "risk_per_trade_usdt", 100))
    max_notional = float(getattr(settings, "risk_max_notional_usdt", 2000))

    # P24: 信心度分级 (90+: 1.5x / 80-89: 1.0x / 70-79: 0.5x / <70: 0.25x)
    conf = decision.get("confidence", 0) or 0
    if   conf >= 90: risk_mult = 1.5
    elif conf >= 80: risk_mult = 1.0
    elif conf >= 70: risk_mult = 0.5
    else:            risk_mult = 0.25
    risk_usdt = base_risk * risk_mult
    cap = max_notional * risk_mult'''
new3 = '''    base_risk    = float(getattr(settings, "risk_per_trade_usdt", 100))
    max_notional = float(getattr(settings, "risk_max_notional_usdt", 2000))

    # P24: 信心度分级 (90+: 1.5x / 80-89: 1.0x / 70-79: 0.5x / <70: 0.25x)
    conf = decision.get("confidence", 0) or 0
    if   conf >= 90: risk_mult = 1.5
    elif conf >= 80: risk_mult = 1.0
    elif conf >= 70: risk_mult = 0.5
    else:            risk_mult = 0.25

    # P27-A: 体制叠加倍数 (山寨季 1.5x, 震荡死水 0.3x)
    try:
        from risk.regime_rules import get_rules as _get_rr
        regime_mult = float(_get_rr().get("risk_mult", 1.0))
    except Exception:
        regime_mult = 1.0

    risk_usdt = base_risk * risk_mult * regime_mult
    cap = max_notional * risk_mult * regime_mult'''

# 加 regime max_pos 到 _risk_check
old4 = '''    # P24: 收紧并发持仓 — 默认 3
    max_pos = getattr(settings, "risk_max_concurrent_positions",
                      getattr(settings, "risk_max_positions", 3))
    if len(positions) >= max_pos:
        return False, f"max concurrent positions ({len(positions)}/{max_pos})", False'''
new4 = '''    # P24+P27-A: 并发持仓上限 — 取 settings 与 regime 中较小的
    base_max_pos = getattr(settings, "risk_max_concurrent_positions",
                           getattr(settings, "risk_max_positions", 3))
    try:
        from risk.regime_rules import get_rules as _get_rr
        regime_max_pos = int(_get_rr().get("max_pos", 3))
    except Exception:
        regime_max_pos = base_max_pos
    max_pos = min(base_max_pos, regime_max_pos)
    if len(positions) >= max_pos:
        return False, f"max concurrent positions ({len(positions)}/{max_pos})", False'''

for label, old in [("compute_size_riskmult", old3), ("risk_check_maxpos", old4)]:
    if old not in code:
        print(f"❌ okx_executor patch [{label}] target not found", file=sys.stderr)
        sys.exit(1)
code = code.replace(old3, new3).replace(old4, new4)
ast.parse(code)
ox.write_text(code)
print(f"✓ {ox} patched")

# ============================================
# 确保 risk/__init__.py 存在
# ============================================
Path("src/risk/__init__.py").touch(exist_ok=True)
print("✓ src/risk/__init__.py")

print("\n✅ P27-A 全部应用完毕")
print("\n下一步:")
print("  python3 ~/cresus-bot/src/risk/regime_rules.py    # 查看 5 种体制策略对照")
print("  pkill -f main_loop.py                            # 重启 bot 使新代码生效")
PATCH
