#!/bin/bash
# P27-D: cooldown.py 体制化 — 山寨季快冷却 (1h post-win 改 15min), 震荡死水严控
#
# 改动:
#   LOSER_COOLDOWN_HOURS         → regime_rules.cooldown_7d_loser_h
#   POST_WIN_COOLDOWN_HOURS      → regime_rules.cooldown_post_win_h
#   POST_LOSS_COOLDOWN_HOURS     → regime_rules.cooldown_post_loss_h
#
# 山寨季效果: 上次盈利 15min 就能继续触发同标的, 让 alpha 标的连续吃
# 震荡死水效果: 各类冷却倍增, 减少无效交易

set -e
cd ~/cresus-bot && python3 - <<'PATCH'
import ast, sys
from pathlib import Path

path = Path("src/execution/cooldown.py")
code = path.read_text()

# 1. Import regime_rules (放在文件顶部 import 区)
old1 = '''import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple, List

CLOSES_LOG = Path.home() / "cresus-bot" / "closes_all.jsonl"'''
new1 = '''import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple, List

# P27-D: 体制自适应冷却期 (山寨季快冷却, 震荡死水慢冷却)
try:
    from risk.regime_rules import get_rules as _regime_rules
except ImportError:
    def _regime_rules():
        return {"cooldown_post_win_h": 1.0, "cooldown_post_loss_h": 6.0, "cooldown_7d_loser_h": 24.0}

CLOSES_LOG = Path.home() / "cresus-bot" / "closes_all.jsonl"'''

# 2. check_block_reason 用 regime 规则替换硬编码
old2 = '''    closes_7d = _load_recent_closes(symbol, hours=LOOKBACK_DAYS * 24)
    if not closes_7d:
        return None  # 没历史，正常开仓

    last_close = closes_7d[-1]
    last_ts = datetime.fromisoformat(last_close["ts"].replace("Z", "+00:00"))
    elapsed_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600

    # 7d 累计净盈亏
    net_7d = sum((c.get("net") or 0) for c in closes_7d)

    # 层 1：7d-loser 严格保护
    if net_7d < 0 and elapsed_hours < LOSER_COOLDOWN_HOURS:
        return (f"7d-loser: 7天净亏 {net_7d:+.2f} USDT, "
                f"上次平仓 {last_close['ts'][:16]} 距今 {elapsed_hours:.1f}h < {LOSER_COOLDOWN_HOURS}h")

    # 层 2：上次平仓状态决定短/长冷却
    last_net = last_close.get("net") or 0
    if last_net > 0:
        cd_hours = POST_WIN_COOLDOWN_HOURS
        label    = f"win {last_net:+.2f}"
    else:
        cd_hours = POST_LOSS_COOLDOWN_HOURS
        label    = f"loss {last_net:+.2f}"'''
new2 = '''    closes_7d = _load_recent_closes(symbol, hours=LOOKBACK_DAYS * 24)
    if not closes_7d:
        return None  # 没历史，正常开仓

    last_close = closes_7d[-1]
    last_ts = datetime.fromisoformat(last_close["ts"].replace("Z", "+00:00"))
    elapsed_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600

    # 7d 累计净盈亏
    net_7d = sum((c.get("net") or 0) for c in closes_7d)

    # P27-D: 读取当前体制对应的冷却参数
    rr = _regime_rules()
    loser_h    = float(rr.get("cooldown_7d_loser_h",  LOSER_COOLDOWN_HOURS))
    post_win_h = float(rr.get("cooldown_post_win_h",  POST_WIN_COOLDOWN_HOURS))
    post_loss_h= float(rr.get("cooldown_post_loss_h", POST_LOSS_COOLDOWN_HOURS))

    # 层 1：7d-loser 严格保护 (按体制调整: 山寨季 12h, 震荡死水 48h)
    if net_7d < 0 and elapsed_hours < loser_h:
        return (f"7d-loser: 7天净亏 {net_7d:+.2f} USDT, "
                f"上次平仓 {last_close['ts'][:16]} 距今 {elapsed_hours:.1f}h < {loser_h:.0f}h")

    # 层 2：上次平仓状态决定短/长冷却 (按体制调整)
    last_net = last_close.get("net") or 0
    if last_net > 0:
        cd_hours = post_win_h
        label    = f"win {last_net:+.2f}"
    else:
        cd_hours = post_loss_h
        label    = f"loss {last_net:+.2f}"'''

for tag, old in [("import", old1), ("check_logic", old2)]:
    if old not in code:
        print(f"❌ P27-D patch [{tag}] target not found", file=sys.stderr)
        sys.exit(1)

code = code.replace(old1, new1).replace(old2, new2)
ast.parse(code)
path.write_text(code)
print(f"✓ {path} patched (P27-D)")
print()
print("效果示例 (山寨季 cooldown):")
print("  7d-loser:  24h → 12h")
print("  post-win:   1h → 15min  ← 上次赚钱后, 15min 就能再开同标的")
print("  post-loss:  6h →  1h")
print()
print("震荡死水 cooldown 反之全部加倍.")
print()
print("重启 bot 让新代码生效:")
print("  pkill -f main_loop.py")
PATCH
