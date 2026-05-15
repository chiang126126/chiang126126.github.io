"""Live Trader — Mirror paper trader's decisions on real Binance Futures.

Phase 3.1: DRY-RUN 骨架. 读 paper trader state, 输出 "would-mirror" 日志.
不真下单. 为 Phase 3.2+ 接入真交易铺路.

架构:
    paper trader (scanner cron)
        ↓ writes
    ~/cresus-bot/paper_trades_history.json
        ↓ reads
    live_trader (this script, separate cron / loop)
        ↓ writes
    ~/cresus-bot/.live_trades.json (private state)
    ~/cresus-bot/live_trades_history.json (public view for dashboard)

设计原则:
- 进程独立: live_trader 崩溃绝不影响 paper trader
- 共享信号源: 通过 paper 的 published state 读取
- DRY-RUN 默认: 需显式 --live + 实例 dry_run=False 才真下单
- Filter 链式: symbol whitelist → max concurrent → already mirrored
- 状态原子写 (.tmp → rename)

Phase 3.1 范围 (本文件):
- 加载 paper / live state
- 输出 would-mirror 日志
- 状态持久化 (mirrored_paper_ids tracking)
- CLI: --once (默认) / --loop (持续) / --live (关闭 dry-run)

Phase 3.2+ 范围 (后续):
- 实际调 binance_client.open_position
- Client-side SL polling
- Reconciliation with exchange
- Publish to live_trades_history.json
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Import binance_client (sibling module)
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from binance_client import BinanceClient, BinanceError, load_credentials

# ============================================================================
# 配置
# ============================================================================

# 文件路径
PAPER_HISTORY = Path.home() / "cresus-bot" / "paper_trades_history.json"
LIVE_STATE = Path.home() / "cresus-bot" / ".live_trades.json"
LIVE_HISTORY = Path.home() / "cresus-bot" / "live_trades_history.json"

# Live 交易配置 (小心调整)
LIVE_NOTIONAL_USDT = 20.0              # 每笔 $20 (paper 是 $400, 等比缩放 1/20)
LIVE_MAX_CONCURRENT = 3                # 实盘并发上限 (比 paper 5 严格)
LIVE_SYMBOL_WHITELIST = [              # Phase 6 第 1 周限主流币
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
]
LIVE_MIRROR_MAX_AGE_SEC = 600          # 仅 mirror 10min 内开的 paper trade
                                       # (防止启动时把陈年 paper open 全部 mirror)

# 状态管理
MIRRORED_IDS_KEEP_LAST_N = 500          # mirrored_paper_ids 滚动窗口

# 主循环
POLL_INTERVAL_SEC = 30                  # --loop 模式 poll 间隔

# 状态文件 schema 版本
STATE_VERSION = "1.0"

log = logging.getLogger(__name__)


# ============================================================================
# State I/O
# ============================================================================

def _empty_live_state() -> dict:
    return {
        "version": STATE_VERSION,
        "live_open_trades": [],
        "live_closed_trades": [],
        "mirrored_paper_ids": [],
        "last_update": None,
        "session_started_at": datetime.now(timezone.utc).isoformat(),
    }


def load_paper_state() -> dict:
    """读 paper trader 发布的 state. 返回最小可用 dict."""
    if not PAPER_HISTORY.exists():
        log.warning(f"paper state file not found: {PAPER_HISTORY}")
        return {"open_trades": [], "recent_closed": []}
    try:
        data = json.loads(PAPER_HISTORY.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            log.error("paper state is not a dict, ignoring")
            return {"open_trades": [], "recent_closed": []}
        data.setdefault("open_trades", [])
        data.setdefault("recent_closed", [])
        return data
    except Exception as e:
        log.error(f"failed to load paper state: {e}")
        return {"open_trades": [], "recent_closed": []}


def load_live_state() -> dict:
    """读 live trader 自己的持久化 state. 缺失/损坏返回空 state."""
    if not LIVE_STATE.exists():
        log.info(f"live state not found, creating: {LIVE_STATE}")
        return _empty_live_state()
    try:
        data = json.loads(LIVE_STATE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            log.error("live state is not a dict, starting fresh")
            return _empty_live_state()
        # 容错 schema migration
        data.setdefault("version", STATE_VERSION)
        data.setdefault("live_open_trades", [])
        data.setdefault("live_closed_trades", [])
        data.setdefault("mirrored_paper_ids", [])
        data.setdefault("last_update", None)
        data.setdefault("session_started_at",
                        datetime.now(timezone.utc).isoformat())
        return data
    except Exception as e:
        log.error(f"failed to load live state, starting fresh: {e}")
        return _empty_live_state()


def save_live_state(state: dict) -> None:
    """原子写 state. .tmp → rename pattern."""
    LIVE_STATE.parent.mkdir(parents=True, exist_ok=True)
    state["last_update"] = datetime.now(timezone.utc).isoformat()
    # 滚动 mirrored_paper_ids (保留最近 N)
    mids = state.get("mirrored_paper_ids", [])
    if len(mids) > MIRRORED_IDS_KEEP_LAST_N:
        state["mirrored_paper_ids"] = mids[-MIRRORED_IDS_KEEP_LAST_N:]
    tmp = LIVE_STATE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(LIVE_STATE)


# ============================================================================
# Mirror logic
# ============================================================================

def _trade_age_sec(paper_trade: dict, now: datetime) -> Optional[float]:
    """Paper trade 开仓至今多久 (秒). 解析失败返回 None."""
    entered = paper_trade.get("entered_at") or paper_trade.get("opened_at")
    if not entered:
        return None
    try:
        dt = datetime.fromisoformat(str(entered).replace("Z", "+00:00"))
        return (now - dt).total_seconds()
    except Exception:
        return None


def is_eligible_for_mirror(
    paper_trade: dict, live_state: dict, now: datetime,
) -> tuple:
    """检查 paper trade 是否应在 live mirror.
    Returns (eligible: bool, reason: str).
    """
    paper_id = paper_trade.get("id", "")
    if not paper_id:
        return False, "paper trade missing id"
    # 1. 已 mirror 过
    if paper_id in live_state.get("mirrored_paper_ids", []):
        return False, "already mirrored"
    # 2. Symbol 白名单
    sym = paper_trade.get("symbol", "")
    if sym not in LIVE_SYMBOL_WHITELIST:
        return False, f"symbol {sym} not in live whitelist {LIVE_SYMBOL_WHITELIST}"
    # 3. 并发上限
    current_open = len(live_state.get("live_open_trades", []))
    if current_open >= LIVE_MAX_CONCURRENT:
        return False, f"max_concurrent reached ({current_open}/{LIVE_MAX_CONCURRENT})"
    # 4. Trade 太旧 (启动时陈年 paper trades 不 mirror)
    age = _trade_age_sec(paper_trade, now)
    if age is not None and age > LIVE_MIRROR_MAX_AGE_SEC:
        return False, f"paper trade too old ({age:.0f}s > {LIVE_MIRROR_MAX_AGE_SEC}s)"
    # 5. Direction 有效
    direction = paper_trade.get("direction", "").upper()
    if direction not in ("LONG", "SHORT"):
        return False, f"invalid direction {direction!r}"
    return True, "ok"


def _paper_to_live_side(direction: str) -> str:
    """Paper 用 LONG/SHORT, Binance 用 BUY/SELL."""
    return "BUY" if direction.upper() == "LONG" else "SELL"


def _generate_trade_id(paper_id: str) -> str:
    """从 paper_id 生成 live trade_id, 满足 binance_client _validate_trade_id
    (1-25 chars, [a-zA-Z0-9_-]).

    paper_id 例: "BTCUSDT|LONG|2026-05-14T11:08:07.253118+00:00"
    → "live_BTCUSDT_L_1715472487" (use symbol prefix + dir char + timestamp)
    """
    # 解析 paper_id (容错)
    parts = paper_id.split("|")
    if len(parts) >= 3:
        sym = parts[0][:8]   # 截断保险
        dir_char = parts[1][:1].upper()  # L or S
        try:
            dt = datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
            ts = int(dt.timestamp())
        except Exception:
            ts = int(time.time())
    else:
        sym = "X"
        dir_char = "?"
        ts = int(time.time())
    # 控制总长度 < 25
    trade_id = f"L{ts}_{sym[:7]}_{dir_char}"
    # 去掉非 [a-zA-Z0-9_-]
    trade_id = "".join(c for c in trade_id if c.isalnum() or c in ("_", "-"))
    return trade_id[:25]


# ============================================================================
# Main loop
# ============================================================================

def main_loop(client: BinanceClient, *, dry_run: bool = True) -> dict:
    """单次循环 — 读 paper, 决策, 持久化 live state.

    Phase 3.1: 仅输出 would-mirror 日志, 不真下单.
    Returns: live state dict (for testing/inspection).
    """
    paper = load_paper_state()
    live = load_live_state()
    now = datetime.now(timezone.utc)

    paper_open = paper.get("open_trades", []) or []
    live_open = live.get("live_open_trades", []) or []
    log.info(
        f"[live_trader] paper_open={len(paper_open)} "
        f"live_open={len(live_open)} "
        f"mode={'DRY-RUN' if dry_run else 'LIVE'} "
        f"client_dry_run={client.dry_run}"
    )

    # 1. 检查 paper 新开的 trade, 是否要 mirror
    mirror_candidates = []
    skip_log = []
    for pt in paper_open:
        eligible, reason = is_eligible_for_mirror(pt, live, now)
        if eligible:
            mirror_candidates.append(pt)
        else:
            skip_log.append((pt.get("symbol"), pt.get("id"), reason))

    if skip_log:
        for sym, pid, reason in skip_log[:5]:  # 前 5 条避免太冗长
            log.debug(f"[skip-mirror] {sym} ({pid[:30]}...): {reason}")

    # 2. 对 candidate 输出 would-mirror (Phase 3.2 将真下单)
    for pt in mirror_candidates:
        live_trade_id = _generate_trade_id(pt.get("id", ""))
        side = _paper_to_live_side(pt.get("direction", ""))
        log.info(
            f"[WOULD-MIRROR] {pt['symbol']} {side} "
            f"entry≈{pt.get('entry_price')} sl≈{pt.get('sl')} "
            f"paper_id={pt.get('id', '')[:40]} "
            f"→ live_trade_id={live_trade_id}"
        )
        # Phase 3.2 在这里调 client.open_position(...)

    # 3. 监控 live open trades (Phase 3.4 会做 SL polling)
    for lt in live_open:
        log.info(
            f"[live-monitor] {lt.get('symbol')} {lt.get('side')} "
            f"phase={lt.get('phase')} entry={lt.get('entry_price')} "
            f"sl={lt.get('sl_price')}"
        )

    # 4. 持久化 state
    save_live_state(live)
    return live


# ============================================================================
# CLI
# ============================================================================

def _cli_main() -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Live Trader — mirror paper decisions on real Binance Futures",
    )
    p.add_argument("--once", action="store_true",
                   help="跑一次循环 (默认行为)")
    p.add_argument("--loop", action="store_true",
                   help=f"持续循环 (每 {POLL_INTERVAL_SEC}s 一次, Ctrl+C 退出)")
    p.add_argument("--live", action="store_true",
                   help="🛑 关闭 dry-run, Phase 3.2+ 才有实际效果. "
                        "Phase 3.1 仍仅输出 would-mirror 日志.")
    p.add_argument("--mainnet", action="store_true",
                   help="使用主网 (默认 testnet, 慎用!)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="DEBUG 级日志 (含 skip-mirror 细节)")
    args = p.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 加载凭证
    key, secret, env_testnet = load_credentials()
    use_testnet = not args.mainnet
    dry_run = not args.live

    client = BinanceClient(key, secret, testnet=use_testnet, dry_run=dry_run)
    log.info(
        f"live_trader started: {client} dry_run={dry_run} "
        f"testnet={use_testnet} once={not args.loop}"
    )
    log.info(
        f"config: notional=${LIVE_NOTIONAL_USDT} max_concurrent={LIVE_MAX_CONCURRENT} "
        f"whitelist={LIVE_SYMBOL_WHITELIST} max_age={LIVE_MIRROR_MAX_AGE_SEC}s"
    )

    if not args.loop:
        main_loop(client, dry_run=dry_run)
        return 0

    while True:
        try:
            main_loop(client, dry_run=dry_run)
        except KeyboardInterrupt:
            log.info("Interrupted, exiting cleanly")
            return 0
        except Exception as e:
            log.error(f"main_loop error (will retry in {POLL_INTERVAL_SEC}s): {e}",
                      exc_info=True)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(_cli_main())
