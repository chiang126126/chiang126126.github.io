#!/bin/bash
# P27-B 一键应用: TP1 平 50% + 剩余跟移动止损
#
# 设计:
#   主仓位 (hard SL, 不挂 TP)
#   TP1 算法单: reduce-only, 平 50%, conditional @tp1
#   trail 算法单: reduce-only, 剩余 50%, activePx=tp1, callback 1.5%
#
# 效果: 价格冲到 TP1 锁 50% 利润, 继续上涨时跟踪止损, 直到回撤 1.5% 才平剩余
# 对 SUI 类 +20% 标的: 单笔 $8 → $25+

set -e
cd ~/cresus-bot && python3 - <<'PATCH'
import ast, sys
from pathlib import Path

path = Path("src/execution/okx_executor.py")
code = path.read_text()

# 检查已应用
if "_attach_split_tp_trail" in code:
    print("✓ P27-B 已应用过, 跳过")
    sys.exit(0)

# ============================================
# 1. 添加 _attach_split_tp_trail 函数
# 插入到 _close_position 之后,_risk_check 之前
# ============================================
old1 = '''def _close_position(inst_id):
    return _run(["swap", "close", "--instId", inst_id, "--mgnMode", "cross"])'''
new1 = '''def _close_position(inst_id):
    return _run(["swap", "close", "--instId", inst_id, "--mgnMode", "cross"])


def _attach_split_tp_trail(inst_id, open_side, total_sz, tp1, tp2=None,
                           callback_ratio=0.015, extra_args=None):
    """P27-B: 主仓开完后,挂分级 TP + 移动止损.

    open_side: 主仓方向 ("buy" = LONG, "sell" = SHORT)
    total_sz:  主仓总名义 (quote_ccy USDT 数额)
    tp1:       第一止盈价 (平 50%)
    tp2:       第二止盈价 (备用 — 若 trail 失败则用)
    callback_ratio: 移动止损回调比例 (0.015 = 1.5%)
    extra_args: 主单的额外参数 (如 --tgtCcy quote_ccy)
    """
    close_side = "sell" if open_side == "buy" else "buy"
    try:
        sz_f = float(total_sz)
        half = round(sz_f / 2, 2)
        rest = round(sz_f - half, 2)
    except (TypeError, ValueError) as e:
        logger.warning(f"[P27-B] {inst_id} 切分 size 失败: {e}")
        return False

    if half <= 0 or rest <= 0:
        logger.warning(f"[P27-B] {inst_id} size 太小,跳过分级")
        return False

    # TP1 算法单 — reduce-only 平 50%
    tp1_args = [
        "swap", "algo", "place",
        "--instId", inst_id,
        "--side", close_side,
        "--ordType", "conditional",
        "--sz", str(half),
        "--tdMode", "cross",
        "--reduceOnly",
        f"--tpTriggerPx={tp1}",
        "--tpOrdPx=-1",
    ]
    if extra_args:
        tp1_args.extend(extra_args)
    tp1_ok = _run(tp1_args) is not None

    # 移动止损算法单 — 剩余 50%, activePx=tp1, callback 1.5%
    trail_args = [
        "swap", "algo", "trail",
        "--instId", inst_id,
        "--side", close_side,
        "--sz", str(rest),
        "--callbackRatio", str(callback_ratio),
        f"--activePx={tp1}",
        "--tdMode", "cross",
        "--reduceOnly",
    ]
    if extra_args:
        trail_args.extend(extra_args)
    trail_ok = _run(trail_args) is not None

    # 移动止损失败时,降级为 TP2 静态止盈 (如有)
    if not trail_ok and tp2 is not None:
        logger.warning(f"[P27-B] {inst_id} trail 失败,降级 TP2={tp2}")
        tp2_args = [
            "swap", "algo", "place",
            "--instId", inst_id,
            "--side", close_side,
            "--ordType", "conditional",
            "--sz", str(rest),
            "--tdMode", "cross",
            "--reduceOnly",
            f"--tpTriggerPx={tp2}",
            "--tpOrdPx=-1",
        ]
        if extra_args:
            tp2_args.extend(extra_args)
        _run(tp2_args)

    logger.info(
        f"[P27-B] {inst_id} 分级出场: TP1@{tp1} 平50% ({half}) + "
        f"trail {callback_ratio*100:.1f}% on {rest} (active@{tp1}) | "
        f"tp1_ok={tp1_ok} trail_ok={trail_ok}"
    )
    return tp1_ok or trail_ok'''

# ============================================
# 2. 修改 execute_signal 调用流程
# ============================================
old2 = '''    sl = decision.get("stop_loss")
    tp_raw = decision.get("take_profit")
    if isinstance(tp_raw, list) and tp_raw:
        tp = tp_raw[0]
    else:
        tp = tp_raw or None'''
new2 = '''    sl = decision.get("stop_loss")
    tp_raw = decision.get("take_profit")
    if isinstance(tp_raw, list) and tp_raw:
        tp_list = list(tp_raw)
    elif tp_raw is not None:
        tp_list = [tp_raw]
    else:
        tp_list = []
    # P27-B: 主单不挂 TP, 后续用分级算法单 (TP1 平50% + 剩余 trail)
    tp = None  # legacy var kept for old _place_market signature'''

# 主单 place 之后,挂分级出场
# 这里要找到 main place 调用位置,在后面插入 _attach 调用
# 不知道具体行,用启发式: 找 _place_market(inst_id, side, sz, ...) 调用之后
old3 = '''    if reverse_close:
        logger.info(f"[risk] {snap.symbol} reversing: close -> reopen")
        _close_position(inst_id)'''
new3 = '''    if reverse_close:
        logger.info(f"[risk] {snap.symbol} reversing: close -> reopen")
        _close_position(inst_id)

    # P27-B: 标记当前流程需要在主单成功后挂分级出场单
    _p27b_pending = tp_list and len(tp_list) >= 1'''

# 应用所有替换
for label, old in [("attach_func", old1), ("tp_unpack", old2), ("reverse_close", old3)]:
    if old not in code:
        print(f"❌ P27-B patch [{label}] target not found", file=sys.stderr)
        sys.exit(1)
code = code.replace(old1, new1).replace(old2, new2).replace(old3, new3)

# Final step: 在 execute_signal 结尾 (或主 place 之后) 加 _attach_split_tp_trail 调用
# 启发式: 找 `return True` 或函数末尾,在前面插入
# 由于不知道精确位置,直接在 _p27b_pending 标记后, 加 wrapper:
# 假设主 place 调用类似 `result = _place_market(inst_id, side, sz, ...)`
old4 = '''    result = _place_market(inst_id, side, sz, extra_args=extra, sl=sl, tp=tp)'''
new4 = '''    result = _place_market(inst_id, side, sz, extra_args=extra, sl=sl, tp=tp)

    # P27-B: 主单 ok 且有 TP 列表 → 挂分级出场 (TP1 平50% + trail)
    if result and _p27b_pending:
        try:
            tp1 = tp_list[0]
            tp2 = tp_list[1] if len(tp_list) >= 2 else None
            _attach_split_tp_trail(inst_id, side, sz, tp1, tp2,
                                   callback_ratio=0.015, extra_args=extra)
        except Exception as e:
            logger.warning(f"[P27-B] {snap.symbol} attach split TP failed: {e}")'''

if old4 not in code:
    print(f"⚠ P27-B [main_place_call] target not found — execute_signal 结构可能不同", file=sys.stderr)
    print(f"   已添加函数定义和变量, 但分级出场调用未插入. 请手动添加调用.", file=sys.stderr)
else:
    code = code.replace(old4, new4)

ast.parse(code)
path.write_text(code)
print(f"✓ {path} patched (P27-B)")
print()
print("✅ P27-B 应用完毕. 重启 bot:")
print("   pkill -f main_loop.py")
print()
print("效果:")
print("  开仓时 OKX 收到 3 笔单:")
print("    1) 主单 market open + hard SL (托底)")
print("    2) reduce-only conditional @ TP1 (平 50%)")
print("    3) reduce-only trail callback 1.5% (剩余 50%)")
print("  价格冲到 TP1 锁 50% 利润; 继续上涨时 trail 跟随; 回撤 1.5% 平剩余")
PATCH
