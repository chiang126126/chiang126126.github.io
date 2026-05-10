#!/bin/bash
# P27-Q4: bot 端开仓记录 (修 Q4 根因)
#
# 修改: 在 okx_executor.execute_signal 主单成功后,把
#       {symbol, side, open_px, open_ts, size} 写到 ~/cresus-bot/pending_opens.json
#
# 未来 pnl tracker / 任何其他模块都能查表补全 close 记录里缺失的 open 数据.

set -e
cd ~/cresus-bot && python3 - <<'PATCH'
import ast, sys
from pathlib import Path

path = Path("src/execution/okx_executor.py")
code = path.read_text()

if "_record_open" in code:
    print("✓ P27-Q4 已应用过, 跳过")
    sys.exit(0)

# ============================================
# 1. 加入 datetime/timezone 导入 (如已有则跳)
# ============================================
if "from datetime import datetime, timezone" not in code:
    # 在文件开头 import 区追加
    code = code.replace(
        "from loguru import logger",
        "from loguru import logger\nfrom datetime import datetime, timezone",
        1
    )

# ============================================
# 2. 添加 _record_open 函数 (放在 _close_position 之后)
# ============================================
old1 = '''def _close_position(inst_id):
    return _run(["swap", "close", "--instId", inst_id, "--mgnMode", "cross"])'''
new1 = '''def _close_position(inst_id):
    return _run(["swap", "close", "--instId", inst_id, "--mgnMode", "cross"])


# P27-Q4: 开仓数据记录 — pnl tracker 平仓时可查表补全 open_ts/open_px
_PENDING_OPENS = Path.home() / "cresus-bot" / "pending_opens.json"

def _record_open(inst_id, direction, entry_px, size_quote):
    """主单成功开仓后调用,把开仓信息写到 pending_opens.json."""
    import json
    state = {}
    try:
        if _PENDING_OPENS.exists():
            state = json.loads(_PENDING_OPENS.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    try:
        px = float(entry_px) if entry_px not in (None, "") else None
    except (TypeError, ValueError):
        px = None
    state[inst_id] = {
        "side":       direction,
        "open_px":    px,
        "open_ts":    datetime.now(timezone.utc).isoformat(),
        "size_quote": str(size_quote),
    }
    try:
        _PENDING_OPENS.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PENDING_OPENS.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PENDING_OPENS)
    except Exception as e:
        logger.warning(f"[P27-Q4] write pending_opens failed: {e}")


def _consume_open(inst_id):
    """平仓时调用,弹出该 inst_id 的开仓记录 (返回 dict 或 None)."""
    import json
    if not _PENDING_OPENS.exists():
        return None
    try:
        state = json.loads(_PENDING_OPENS.read_text(encoding="utf-8"))
    except Exception:
        return None
    rec = state.pop(inst_id, None)
    if rec is None:
        return None
    try:
        _PENDING_OPENS.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return rec'''

if old1 not in code:
    print(f"❌ P27-Q4 [func_definition] target not found", file=sys.stderr)
    sys.exit(1)
code = code.replace(old1, new1)

# ============================================
# 3. 在主单 place 成功后调用 _record_open
# ============================================
old2 = '''    result = _place_market(inst_id, side, sz, extra_args=extra, sl=sl, tp=tp)'''
new2 = '''    result = _place_market(inst_id, side, sz, extra_args=extra, sl=sl, tp=tp)

    # P27-Q4: 主单成功 → 记录开仓数据 (用于补全 pnl 历史的 open_ts/open_px)
    if result:
        try:
            _record_open(inst_id, direction, decision.get("entry_price"), sz)
        except Exception as e:
            logger.warning(f"[P27-Q4] {inst_id} record_open failed: {e}")'''

if old2 not in code:
    print(f"❌ P27-Q4 [main_place_call] target not found", file=sys.stderr)
    sys.exit(1)
code = code.replace(old2, new2)

# 重要: Path import 是否已在文件顶部?
if "from pathlib import Path" not in code:
    code = code.replace(
        "from loguru import logger",
        "from loguru import logger\nfrom pathlib import Path",
        1
    )

ast.parse(code)
path.write_text(code)
print(f"✓ {path} patched (P27-Q4)")
print()
print("✅ P27-Q4 应用完毕. 重启 bot:")
print("   pkill -f main_loop.py")
print()
print("效果:")
print("  开仓后 ~/cresus-bot/pending_opens.json 即时记录:")
print('    {"BERA-USDT-SWAP": {"side":"LONG","open_px":5.78,"open_ts":"...","size_quote":"1500"}}')
print("  pnl tracker / 任何模块都能查表补全平仓记录的 open 数据,")
print("  从此 dashboard 仓位历史"持仓时长""入场"等列将显示真实值.")
PATCH
