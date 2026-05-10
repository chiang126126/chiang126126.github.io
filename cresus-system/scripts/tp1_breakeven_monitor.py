"""tp1_breakeven_monitor.py — P27-B-Ext

每 60s 轮询: 对 pending_opens.json 中尚未触发 TP1 的仓位,
查询 OKX 实时持仓, 检测仓位规模缩减 (≥40% 减少 → 判定 TP1 已触发),
随即下一道新的 reduce-only SL 算法单, 触发价 = 入场价 (breakeven),
覆盖剩余仓位.

效果: 一旦 TP1 触发后, 即使原 hard SL 仍在, 新 breakeven SL 距离更近,
价格回头先触发 BE → 剩余 50% 不亏损平掉 → 实现 "1R 后立于不败" 军规.

依赖: okx CLI (会自动 --demo --json), pending_opens.json (P27-Q4 写入)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PENDING_OPENS = Path.home() / "cresus-bot" / "pending_opens.json"
LOG_FILE = Path.home() / "cresus-bot" / "logs" / "tp1_breakeven_monitor.log"

# OKX CLI 调用
OKX_CLI = "okx"
TIMEOUT = 15

# 触发判定: 仓位减少 ≥40% 视为 TP1 触发 (留 10% 容差,因为 50% 实际成交会有少量滑点)
TP1_DETECTION_THRESHOLD = 0.40


def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, file=sys.stderr)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _run_okx(args: list) -> Optional[object]:
    cmd = [OKX_CLI] + args + ["--demo", "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        if r.returncode == 0:
            return json.loads(r.stdout)
        # OKX CLI 把错误也打到 stdout
        raw = r.stderr.strip() or r.stdout.strip()
        _log(f"okx fail {' '.join(args[:3])} | {' '.join(raw.split())[:300]}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        _log(f"okx exception {e}")
    except Exception as e:
        _log(f"okx unexpected {e}")
    return None


def _load_state() -> dict:
    if not PENDING_OPENS.exists():
        return {}
    try:
        return json.loads(PENDING_OPENS.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"load_state failed: {e}")
        return {}


def _save_state(state: dict) -> None:
    try:
        PENDING_OPENS.parent.mkdir(parents=True, exist_ok=True)
        tmp = PENDING_OPENS.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PENDING_OPENS)
    except Exception as e:
        _log(f"save_state failed: {e}")


def _get_position(inst_id: str) -> Optional[dict]:
    """Query current OKX position for inst_id. Return None if no position."""
    resp = _run_okx(["swap", "positions", "--instId", inst_id])
    if not resp:
        return None
    data = resp if isinstance(resp, list) else resp.get("data", [])
    for p in data:
        if p.get("instId") == inst_id:
            return p
    return None


def _place_breakeven_sl(inst_id: str, side: str, breakeven_px: float, size_remaining: float) -> bool:
    """Place reduce-only SL at breakeven price for remaining size."""
    close_side = "sell" if side == "LONG" else "buy"
    args = [
        "swap", "algo", "place",
        "--instId", inst_id,
        "--side", close_side,
        "--ordType", "conditional",
        "--sz", str(round(size_remaining, 4)),
        "--tdMode", "cross",
        "--reduceOnly",
        f"--slTriggerPx={breakeven_px}",
        "--slOrdPx=-1",
    ]
    resp = _run_okx(args)
    return resp is not None


def process() -> int:
    state = _load_state()
    if not state:
        return 0

    changed = False
    handled = 0

    for inst_id in list(state.keys()):
        info = state[inst_id]
        # 已处理过 BE 提升的跳过
        if info.get("breakeven_set"):
            continue
        # 必要字段
        side = info.get("side")
        open_px = info.get("open_px")
        size_quote = info.get("size_quote")
        if not side or not open_px or not size_quote:
            continue
        try:
            open_px_f = float(open_px)
            size_quote_f = float(size_quote)
        except (TypeError, ValueError):
            continue

        # 估算原始合约张数 = quote_ccy 名义 / 入场价
        original_contracts = size_quote_f / open_px_f if open_px_f > 0 else 0
        if original_contracts <= 0:
            continue

        # 查询当前持仓
        pos = _get_position(inst_id)
        if not pos:
            # 仓位已不存在 → 整笔已平 (不必再设 BE)
            info["fully_closed"] = True
            changed = True
            continue

        try:
            current_contracts = abs(float(pos.get("pos", 0) or 0))
        except (TypeError, ValueError):
            continue

        # 检测 TP1 是否触发: 当前张数 < 原始 * (1 - 阈值) 视为触发
        reduction_ratio = 1.0 - (current_contracts / original_contracts)
        if reduction_ratio < TP1_DETECTION_THRESHOLD:
            continue   # TP1 未触发,继续等

        # TP1 已触发! 下 BE SL
        _log(f"{inst_id} TP1 detected (reduction={reduction_ratio*100:.1f}%) → 下 BE SL @ {open_px_f}")
        ok = _place_breakeven_sl(inst_id, side, open_px_f, current_contracts)
        if ok:
            info["breakeven_set"] = True
            info["breakeven_set_ts"] = datetime.now(timezone.utc).isoformat()
            info["tp1_detected_size"] = current_contracts
            changed = True
            handled += 1
            _log(f"{inst_id} ✅ BE SL placed at {open_px_f} for {current_contracts} contracts")
        else:
            _log(f"{inst_id} ❌ BE SL place failed (will retry next cycle)")

    if changed:
        _save_state(state)

    if handled > 0:
        _log(f"cycle done: {handled} positions moved to breakeven")
    return 0


def cmd_show() -> int:
    state = _load_state()
    if not state:
        print("(pending_opens.json 空)")
        return 0
    for inst_id, info in state.items():
        flag = "✅ BE 已设" if info.get("breakeven_set") else "⏳ 等 TP1"
        print(f"  {flag}  {inst_id}  {info.get('side')} @{info.get('open_px')}  "
              f"size_quote=${info.get('size_quote')}  ts={info.get('open_ts','')[:16]}")
    return 0


def main(argv) -> int:
    if argv and argv[0] == "show":
        return cmd_show()
    return process()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
