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

# Phase 3.3.a 风控参数 (实盘 $100 USDT 起始)
LIVE_STARTING_CAPITAL_USDT = 100.0     # 实盘起始资金 (校准用)
LIVE_DAILY_DD_LIMIT_USDT = 5.0         # 日亏 -$5 → block new opens
LIVE_MAX_DEPLOY_USDT = 60.0            # 总部署上限 $60 (40% 现金保留)

# Phase 3.3.a 控制文件 (在 ~/.cresus-*)
PAUSE_FLAG_PATH = Path.home() / ".cresus-pause"
EMERGENCY_STOP_PATH = Path.home() / ".cresus-emergency-stop"

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
# Phase 3.3.a 风控硬装置 — soft gates (仅 block 新开, 不影响管理已有)
# ============================================================================

def _check_emergency_stop_flag() -> Optional[str]:
    """~/.cresus-emergency-stop 文件存在 → 完全停 (Phase 3.3.b 触发后自动创建).
    人工删文件才能恢复. 优先级最高."""
    if not EMERGENCY_STOP_PATH.exists():
        return None
    try:
        content = EMERGENCY_STOP_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        content = ""
    short = content[:200] if content else "no reason"
    return f"emergency stop flag exists: {short!r}"


def _check_pause_flag() -> Optional[str]:
    """~/.cresus-pause 文件存在 → 手动暂停 (人工新建/删除即可).
    适用场景: 你出门/睡觉/不想 bot 操作时."""
    if not PAUSE_FLAG_PATH.exists():
        return None
    try:
        content = PAUSE_FLAG_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        content = ""
    short = content[:80] if content else ""
    return f"manual pause flag exists: {short!r}" if short else "manual pause"


def _check_cash_reserve(live_state: dict) -> Optional[str]:
    """部署总额 >= LIVE_MAX_DEPLOY_USDT → 不开新仓.
    保护现金缓冲, 防止满仓后任何额外开销/突发情况."""
    deployed = sum(
        float(lt.get("notional_usdt", 0) or 0)
        for lt in (live_state.get("live_open_trades") or [])
    )
    if deployed >= LIVE_MAX_DEPLOY_USDT:
        return (
            f"deployed ${deployed:.2f} >= cap ${LIVE_MAX_DEPLOY_USDT:.2f} "
            f"({LIVE_STARTING_CAPITAL_USDT - LIVE_MAX_DEPLOY_USDT:.0f}% 现金保留触发)"
        )
    return None


def _calculate_daily_realized_pnl(
    live_state: dict, now: datetime,
) -> tuple:
    """汇总 UTC 今日已平仓 trades 的 realized_pnl_usdt.
    Returns: (total_pnl, count, day_start_dt).
    """
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    count = 0
    for lt in (live_state.get("live_closed_trades") or []):
        ca = lt.get("closed_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= day_start:
            total += float(lt.get("realized_pnl_usdt", 0) or 0)
            count += 1
    return total, count, day_start


def _check_daily_dd(live_state: dict, now: datetime) -> Optional[str]:
    """日内已实现亏损达 -LIVE_DAILY_DD_LIMIT_USDT → 不开新仓.
    (不含未实现 PnL — 那是 Phase 3.3.b 用 get_account 才能精确计算)"""
    pnl, count, day_start = _calculate_daily_realized_pnl(live_state, now)
    if pnl <= -LIVE_DAILY_DD_LIMIT_USDT:
        return (
            f"daily realized PnL ${pnl:+.2f} <= -${LIVE_DAILY_DD_LIMIT_USDT:.2f} "
            f"({count} closed trades since {day_start.strftime('%Y-%m-%dT%H:%MZ')})"
        )
    return None


def check_risk_gates(live_state: dict, now: datetime) -> dict:
    """Phase 3.3.a 软门聚合检查. 只 block 新开仓, 不影响已有仓位管理.

    Returns: {
        'block_new_opens': bool,
        'reasons': [str, ...],   # 触发的 gate 列表 (可能多个)
        'daily_pnl': float,      # 当日已实现 PnL (供 logging/dashboard)
        'deployed_usdt': float,  # 当前部署 (供 logging/dashboard)
    }
    """
    reasons = []
    # 优先级 1: emergency stop (Phase 3.3.b 自动写, 人工删)
    msg = _check_emergency_stop_flag()
    if msg: reasons.append(msg)
    # 优先级 2: 手动 pause (人工随时新建/删)
    msg = _check_pause_flag()
    if msg: reasons.append(msg)
    # 优先级 3: 现金保留
    msg = _check_cash_reserve(live_state)
    if msg: reasons.append(msg)
    # 优先级 4: 日 DD
    msg = _check_daily_dd(live_state, now)
    if msg: reasons.append(msg)

    daily_pnl, _, _ = _calculate_daily_realized_pnl(live_state, now)
    deployed = sum(
        float(lt.get("notional_usdt", 0) or 0)
        for lt in (live_state.get("live_open_trades") or [])
    )
    return {
        "block_new_opens": bool(reasons),
        "reasons": reasons,
        "daily_pnl": round(daily_pnl, 4),
        "deployed_usdt": round(deployed, 2),
    }


def _get_current_price(client: BinanceClient, symbol: str) -> Optional[float]:
    """获取 symbol 当前价 (用 1m 最新 kline). 失败返 None."""
    try:
        klines = client.get_klines(symbol, interval="1m", limit=1)
        if not klines:
            return None
        return float(klines[0][4])  # close price
    except (BinanceError, ValueError, IndexError, TypeError) as e:
        log.warning(f"[get_price] {symbol}: {type(e).__name__}: {e}")
        return None


def _check_sl_breach(live_trade: dict, current_price: float) -> bool:
    """检查当前价是否触 SL.
    LONG (side=BUY): current ≤ sl 触发
    SHORT (side=SELL): current ≥ sl 触发
    """
    sl = live_trade.get("sl_price")
    if sl is None:
        return False
    sl = float(sl)
    side = live_trade.get("side", "").upper()
    if side == "BUY":
        return current_price <= sl
    elif side == "SELL":
        return current_price >= sl
    return False


def _sync_live_with_paper(live_trade: dict, paper_open_trade: dict) -> bool:
    """从 paper 同步 sl_price / phase 到 live_trade (mutates in place).
    Returns: True 若有更新.

    设计逻辑: Live trader 是 paper 的执行层. Paper 内部管理 Phase A/B/C 转换
    (TP1 命中 → SL 移 BE; TP2 → trailing), live 只需 mirror paper 当前的 sl.
    """
    updated = False
    new_sl = paper_open_trade.get("sl")
    new_phase = paper_open_trade.get("phase")
    if new_sl is not None:
        try:
            new_sl_f = float(new_sl)
            if abs(new_sl_f - float(live_trade.get("sl_price", 0))) > 1e-9:
                old = live_trade.get("sl_price")
                live_trade["sl_price"] = new_sl_f
                log.info(
                    f"[sl-sync] {live_trade['symbol']}: {old} → {new_sl_f} "
                    f"(paper phase={new_phase})"
                )
                updated = True
        except (ValueError, TypeError):
            pass
    if new_phase and new_phase != live_trade.get("phase"):
        log.info(
            f"[phase-sync] {live_trade['symbol']}: "
            f"{live_trade.get('phase')} → {new_phase}"
        )
        live_trade["phase"] = new_phase
        updated = True
    return updated


def _try_mirror_close(
    client: BinanceClient,
    live_trade: dict,
    *,
    reason: str,
    dry_run: bool,
) -> Optional[dict]:
    """关 live position. Returns updated live_trade (含 close 信息), 失败 None."""
    sym = live_trade.get("symbol", "")
    side = live_trade.get("side", "")
    trade_id = live_trade.get("trade_id", "")
    log.info(f"[mirror-close] {sym} {side} reason={reason} trade_id={trade_id}")
    try:
        result = client.close_position(
            symbol=sym, side=side, trade_id=trade_id,
        )
    except (BinanceError, ValueError) as e:
        log.error(f"[mirror-close FAILED] {sym}: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        log.error(f"[mirror-close UNEXPECTED] {sym}: {type(e).__name__}: {e}",
                  exc_info=True)
        return None

    # 把 close 信息合并进 live_trade
    closed = dict(live_trade)
    closed["closed_at"] = result.get("closed_at")
    closed["close_reason"] = reason
    closed["avg_exit_price"] = float(result.get("avg_exit_price") or 0)
    closed["realized_pnl_usdt"] = float(result.get("realized_pnl_usdt") or 0)
    closed["close_order_id"] = result.get("close_order_id")
    closed["close_qty"] = float(result.get("qty_closed") or 0)
    return closed


def _try_mirror_open(
    client: BinanceClient,
    paper_trade: dict,
    *,
    dry_run: bool,
) -> Optional[dict]:
    """实际 mirror paper trade → live. Plan B (client-side SL).

    Returns: 新的 live trade dict (添加到 live_open_trades 用), 失败返回 None.
    异常完全捕获 (不让单笔失败让 loop 崩).
    """
    paper_id = paper_trade.get("id", "")
    sym = paper_trade.get("symbol", "")
    direction = paper_trade.get("direction", "")
    side = _paper_to_live_side(direction)
    trade_id = _generate_trade_id(paper_id)

    # 提取必需字段
    try:
        sl_price = float(paper_trade["sl"])
        paper_entry = float(paper_trade.get("entry_price", 0))
    except (KeyError, ValueError, TypeError) as e:
        log.error(f"[mirror-open FAILED] {sym}: paper trade 缺关键字段: {e}")
        return None

    log.info(
        f"[mirror-open] {sym} {side} notional=${LIVE_NOTIONAL_USDT} sl={sl_price} "
        f"paper_id={paper_id[:40]} → trade_id={trade_id}"
    )

    try:
        result = client.open_position(
            symbol=sym,
            side=side,
            notional_usdt=LIVE_NOTIONAL_USDT,
            sl_price=sl_price,
            trade_id=trade_id,
            use_exchange_sl=False,   # Plan B: client-side SL (Phase 3.2.b 实现 polling)
        )
    except (BinanceError, ValueError) as e:
        log.error(f"[mirror-open FAILED] {sym} {side}: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        # 兜底: 任何意外异常都不让 loop 崩
        log.error(f"[mirror-open UNEXPECTED] {sym} {side}: {type(e).__name__}: {e}",
                  exc_info=True)
        return None

    # Slippage 计算 (paper_entry 是信号时价格, actual_fill 是真实成交)
    actual_fill = float(result.get("avg_fill_price") or 0)
    slippage_bps = 0.0
    if paper_entry > 0 and actual_fill > 0:
        # LONG: 实际成交 > 预期 = 不利 (正 bps)
        # SHORT: 实际成交 < 预期 = 不利 (取负)
        raw_bps = (actual_fill - paper_entry) / paper_entry * 10000.0
        slippage_bps = raw_bps if side == "BUY" else -raw_bps

    live_trade = {
        "paper_id": paper_id,
        "trade_id": trade_id,
        "symbol": sym,
        "side": side,
        "direction": direction,
        "entry_price_paper": paper_entry,
        "avg_fill_price": actual_fill,
        "slippage_bps": round(slippage_bps, 2),
        "qty": result.get("qty", 0),
        "notional_usdt": result.get("actual_notional", 0),
        "sl_price": result.get("sl_price", sl_price),
        "tp1_price": float(paper_trade.get("tp1") or 0),
        "tp2_price": float(paper_trade.get("tp2") or 0),
        "phase": "A",
        "entry_order_id": result.get("entry_order_id"),
        "entry_client_id": result.get("entry_client_id"),
        "sl_order_id": result.get("sl_order_id"),
        "sl_mode": result.get("sl_mode", "client_side"),
        "conviction_score": paper_trade.get("conviction_score"),
        "alert_type": paper_trade.get("alert_type"),
        "atr_pct": paper_trade.get("atr_pct"),
        "fees_paid_usdt": result.get("fees_paid_usdt", 0),
        "opened_at": result.get("opened_at"),
        "is_dry_run": bool(dry_run or result.get("_dryRun")),
    }
    return live_trade


# ============================================================================
# Main loop
# ============================================================================

def main_loop(client: BinanceClient, *, dry_run: bool = True) -> dict:
    """单次循环 — 读 paper, mirror eligible 的, 持久化 live state.

    Phase 3.2.a: 真实调 open_position (但 dry_run=True 时仍返回 mock).
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

    # Phase 3.3.a: 风控软门检查 (在尝试新开仓前)
    risk = check_risk_gates(live, now)
    log.info(
        f"[risk] daily_pnl=${risk['daily_pnl']:+.2f} "
        f"deployed=${risk['deployed_usdt']:.2f}/{LIVE_MAX_DEPLOY_USDT:.0f} "
        f"block_new_opens={risk['block_new_opens']}"
    )
    if risk["block_new_opens"]:
        for r in risk["reasons"]:
            log.warning(f"🛑 [risk-gate] {r}")

    # 1. 找 eligible candidates (即使风控触发也走 eligibility 检查, 便于日志一致)
    mirror_candidates = []
    skip_log = []
    for pt in paper_open:
        eligible, reason = is_eligible_for_mirror(pt, live, now)
        if eligible:
            mirror_candidates.append(pt)
        else:
            skip_log.append((pt.get("symbol"), pt.get("id"), reason))

    if skip_log:
        for sym, pid, reason in skip_log[:5]:
            log.debug(f"[skip-mirror] {sym} ({pid[:30]}...): {reason}")

    # 2. 对每个 candidate 真实下单 (Plan B: 无 exchange SL)
    #    仅当风控未 block 时执行
    mirrored_count = 0
    if risk["block_new_opens"]:
        if mirror_candidates:
            log.warning(
                f"🛑 {len(mirror_candidates)} eligible paper trade(s) "
                f"NOT mirrored due to risk gate."
            )
    else:
        for pt in mirror_candidates:
            new_trade = _try_mirror_open(client, pt, dry_run=dry_run)
            if new_trade is None:
                # 失败也记入 mirrored_paper_ids 防止下次重试 (避免重复砍腰)
                live.setdefault("mirrored_paper_ids", []).append(pt["id"])
                log.warning(f"[mirror-open] {pt['symbol']} 失败, paper_id 加入 skip 列表")
                continue
            live.setdefault("live_open_trades", []).append(new_trade)
            live.setdefault("mirrored_paper_ids", []).append(pt["id"])
            mirrored_count += 1
            log.info(
                f"[mirrored ✓] {new_trade['symbol']} {new_trade['side']} "
                f"slippage={new_trade['slippage_bps']:+.1f}bps "
                f"({len(live['live_open_trades'])} live open now)"
            )

    # 3. Sync + monitor live opens, 触发 close 条件 (Phase 3.2.b)
    # 三个 close 触发器 (按优先级):
    #   A. paper 已关 (timeout / TP / SL 等) → mirror close
    #   B. paper 还开着但 sl 已更新 (BE move / trailing) → sync sl
    #   C. 当前价触 SL (client-side polling) → close
    paper_open_by_id = {pt.get("id"): pt for pt in paper_open if pt.get("id")}
    paper_closed_ids = {
        pt.get("id") for pt in paper.get("recent_closed", []) or []
        if pt.get("id")
    }
    paper_closed_by_id = {
        pt.get("id"): pt for pt in paper.get("recent_closed", []) or []
        if pt.get("id")
    }

    still_open = []
    closed_now = []
    for lt in (live.get("live_open_trades") or []):
        paper_id = lt.get("paper_id", "")

        # === A. Paper 已关 → mirror close ===
        if paper_id in paper_closed_ids:
            paper_closed = paper_closed_by_id.get(paper_id, {})
            paper_reason = paper_closed.get("close_reason", "paper_closed")
            closed_lt = _try_mirror_close(
                client, lt, reason=f"paper:{paper_reason}", dry_run=dry_run,
            )
            if closed_lt is not None:
                closed_now.append(closed_lt)
            else:
                # close 失败 → 保留 open, 下 tick 重试
                still_open.append(lt)
                log.warning(f"[mirror-close retry] {lt.get('symbol')} 留下 tick 重试")
            continue

        # === B. Sync sl/phase from paper ===
        paper_current = paper_open_by_id.get(paper_id)
        if paper_current is not None:
            _sync_live_with_paper(lt, paper_current)
        else:
            # Paper 关了但还没进 recent_closed (罕见 race)
            log.warning(
                f"[mirror-sync] {lt.get('symbol')} paper_id={paper_id[:30]} "
                f"既不在 paper open 也不在 recent_closed (race?)"
            )

        # === C. Client-side SL polling ===
        current_price = _get_current_price(client, lt.get("symbol", ""))
        if current_price is None:
            # 取价失败 → 保留, 下 tick 重试
            still_open.append(lt)
            continue
        if _check_sl_breach(lt, current_price):
            log.warning(
                f"[SL-BREACH] {lt.get('symbol')} {lt.get('side')}: "
                f"current={current_price} crossed sl={lt.get('sl_price')}"
            )
            closed_lt = _try_mirror_close(
                client, lt, reason="sl_breach_client", dry_run=dry_run,
            )
            if closed_lt is not None:
                closed_now.append(closed_lt)
            else:
                still_open.append(lt)
                log.error(f"[SL-BREACH retry] {lt.get('symbol')} close 失败, 下 tick 重试")
            continue

        # 没触发任何 close → 仍 open
        still_open.append(lt)
        log.debug(
            f"[live-monitor] {lt.get('symbol')} {lt.get('side')} "
            f"phase={lt.get('phase')} entry={lt.get('avg_fill_price')} "
            f"sl={lt.get('sl_price')} current={current_price}"
        )

    # 更新 state
    live["live_open_trades"] = still_open
    live.setdefault("live_closed_trades", []).extend(closed_now)

    # 4. 持久化 state
    save_live_state(live)
    if mirrored_count > 0 or closed_now:
        log.info(
            f"[live_trader tick] +{mirrored_count} opened, "
            f"+{len(closed_now)} closed, "
            f"{len(still_open)} still open"
        )
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
