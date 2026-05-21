"""CEX Futures 持续扫描守护进程。

每 N 分钟扫 Binance USDT-M 永续合约，运行 L2→L3→L5 管道，
结果 append 到 data/cex_scans/YYYY-MM-DD.jsonl。
paper 模式下自动追踪持仓，每轮检查 SL / TP 是否触发。

Usage:
    uv run python -m scripts.cex_daemon              # 默认 15min
    uv run python -m scripts.cex_daemon --interval 30m
    uv run python -m scripts.cex_daemon --once       # 单次扫描即退出
    uv run python -m scripts.cex_daemon --max-candidates 30
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sprite.l1_data.binance import _BASE_FAPI, _get
from sprite.scanner.pipeline import run as run_pipeline
from sprite.scanner.screener import screen_candidates
from sprite import registry

log = logging.getLogger("cex_daemon")

DEFAULT_INTERVAL = "15m"
DEFAULT_DATA_DIR = Path("./data")
DEFAULT_MAX_CANDIDATES = 20

_should_stop = False


# ── 시간 / IO 유틸 ──────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def _save_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    tmp.replace(path)


def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _parse_interval(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    if s.endswith("s"):
        return int(float(s[:-1]))
    return int(s)


def _signal_handler(signum, _frame):
    global _should_stop
    _should_stop = True
    log.info("Signal %d received — stopping after current scan", signum)


# ── Binance 价格 ──────────────────────────────────────────────────────────────

def _current_price(symbol: str) -> float | None:
    try:
        data = _get(f"{_BASE_FAPI}/fapi/v1/ticker/price", params={"symbol": symbol})
        return float(data["price"])
    except Exception:
        return None


# ── Paper 持仓管理 ────────────────────────────────────────────────────────────

def _monitor_positions(data_dir: Path) -> None:
    """检查所有开仓是否触发 SL 或 TP，触发则平仓。"""
    pos_path = data_dir / "cex_paper_positions.json"
    positions = _load_json(pos_path, default=[])
    if not positions:
        return

    remaining = []
    for pos in positions:
        symbol = pos["symbol"]
        side = pos["side"]
        sl = pos["stop_loss"]
        tp = pos.get("take_profit")
        entry = pos["entry_price"]
        qty = pos["qty_usd"]

        price = _current_price(symbol)
        if price is None:
            remaining.append(pos)
            continue

        exit_reason: str | None = None
        if side == "long":
            if price <= sl:
                exit_reason = "stop_loss"
            elif tp and price >= tp:
                exit_reason = "take_profit"
        else:  # short
            if price >= sl:
                exit_reason = "stop_loss"
            elif tp and price <= tp:
                exit_reason = "take_profit"

        if exit_reason:
            pnl_pct = (price - entry) / entry * (1 if side == "long" else -1)
            pnl_usd = qty * pnl_pct
            opened_at = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
            hold_h = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600

            trade_record = {
                "v": 1, "type": "close", "ts": _utcnow(),
                "symbol": symbol, "side": side,
                "entry_price": entry, "exit_price": price,
                "pnl_pct": round(pnl_pct, 6),
                "pnl_usd": round(pnl_usd, 4),
                "qty_usd": qty,
                "exit_reason": exit_reason,
                "hold_hours": round(hold_h, 2),
                "strategy_id": pos.get("strategy_id", "?"),
            }
            _append_jsonl(
                data_dir / "cex_paper_trades" / f"{_today()}.jsonl",
                trade_record,
            )
            log.info(
                "CLOSED %s %s @ %.6g (%s) P&L: %+.1f%%",
                symbol, side, price, exit_reason, pnl_pct * 100,
            )
        else:
            remaining.append(pos)

    _save_atomic(pos_path, remaining)


def _open_paper_position(intent, data_dir: Path, scan_id: str) -> None:
    pos_path = data_dir / "cex_paper_positions.json"
    positions = _load_json(pos_path, default=[])

    # 同一币种不重复开仓
    if any(p["symbol"] == intent.symbol for p in positions):
        log.info("Already holding %s, skip", intent.symbol)
        return

    pos = {
        "id": str(uuid.uuid4())[:8],
        "symbol": intent.symbol,
        "side": intent.side.value,
        "entry_price": intent.entry_price,
        "stop_loss": intent.stop_loss_price,
        "take_profit": intent.take_profit_price,
        "qty_usd": intent.sizing.qty_quote_usd,
        "opened_at": _utcnow(),
        "strategy_id": intent.strategy_id,
        "scan_id": scan_id,
    }
    positions.append(pos)
    _save_atomic(pos_path, positions)
    _append_jsonl(
        data_dir / "cex_paper_trades" / f"{_today()}.jsonl",
        {"v": 1, "type": "open", "ts": _utcnow(), **pos},
    )
    log.info(
        "OPENED %s %s @ %.6g  SL:%.6g  TP:%s  $%.0f",
        intent.side.value.upper(), intent.symbol, intent.entry_price,
        intent.stop_loss_price,
        f"{intent.take_profit_price:.6g}" if intent.take_profit_price else "trailing",
        intent.sizing.qty_quote_usd,
    )


# ── 单次扫描 ──────────────────────────────────────────────────────────────────

def run_one_scan(data_dir: Path, max_candidates: int) -> dict:
    scan_id = _utcnow()
    log_path = data_dir / "cex_scans" / f"{_today()}.jsonl"

    # 预筛
    try:
        candidates = screen_candidates()[:max_candidates]
    except Exception as e:
        log.error("screener failed: %s", e)
        meta = {"v": 1, "type": "scan_meta", "scan_id": scan_id,
                "ts": _utcnow(), "skipped": True, "reason": f"screener_error:{e}"}
        _append_jsonl(log_path, meta)
        return meta

    analyzed = 0
    intents_fired = 0
    errors = 0

    for c in candidates:
        sym = c["symbol"]
        token_info = registry.lookup(sym)

        if not token_info:
            # 没有链上信息，只记录预筛结果，不做深度分析
            _append_jsonl(log_path, {
                "v": 1, "type": "signal", "scan_id": scan_id, "ts": _utcnow(),
                "symbol": sym, "result": "registry_miss",
                "price_change_pct": c["price_change_pct"],
                "screener_reason": c["reason"],
            })
            continue

        try:
            result = run_pipeline(
                futures_symbol=sym,
                token_id=token_info.token_id,
                daily_pump_pct=c["price_change_pct"],
                open_intents=[],
            )
            analyzed += 1

            row: dict = {
                "v": 1, "type": "signal", "scan_id": scan_id, "ts": _utcnow(),
                "symbol": sym,
                "pool": result.pool,
                "rejection": result.rejection_reason,
                "has_intent": result.intent is not None,
                "price_change_pct": c["price_change_pct"],
                "screener_reason": c["reason"],
            }
            if result.intent:
                i = result.intent
                row["intent"] = {
                    "strategy_id": i.strategy_id,
                    "side": i.side.value,
                    "entry_price": i.entry_price,
                    "stop_loss": i.stop_loss_price,
                    "take_profit": i.take_profit_price,
                    "qty_usd": i.sizing.qty_quote_usd,
                }
                _open_paper_position(i, data_dir, scan_id)
                intents_fired += 1

            _append_jsonl(log_path, row)

        except Exception as e:
            log.error("%s pipeline crashed: %s", sym, e)
            errors += 1

    meta = {
        "v": 1, "type": "scan_meta", "scan_id": scan_id, "ts": _utcnow(),
        "prescreened": len(candidates),
        "analyzed": analyzed,
        "intents_fired": intents_fired,
        "errors": errors,
    }
    _append_jsonl(log_path, meta)
    return meta


# ── 状态文件 ──────────────────────────────────────────────────────────────────

def _update_status(data_dir: Path, meta: dict | None = None, error: str | None = None) -> None:
    p = data_dir / "cex_daemon_status.json"
    s = _load_json(p, default={"v": 1, "scans_completed": 0, "errors": 0})
    s["running"] = True
    s["last_update_at"] = _utcnow()
    if meta and not meta.get("skipped"):
        s["last_scan_at"] = meta.get("ts", _utcnow())
        s["last_scan_meta"] = meta
        s["scans_completed"] = s.get("scans_completed", 0) + 1
    if error:
        s["last_error"] = error
        s["errors"] = s.get("errors", 0) + 1
    _save_atomic(p, s)


def _mark_stopped(data_dir: Path) -> None:
    p = data_dir / "cex_daemon_status.json"
    s = _load_json(p, default={})
    s["running"] = False
    s["stopped_at"] = _utcnow()
    _save_atomic(p, s)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", default=DEFAULT_INTERVAL,
                        help="扫描间隔 (15m / 1h / 30s)")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES,
                        help="每轮最多深度分析多少候选币")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--once", action="store_true", help="单次扫描后退出")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    interval_s = _parse_interval(args.interval)
    args.data_dir.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info("cex_daemon starting  interval=%ds  max_candidates=%d  data=%s",
             interval_s, args.max_candidates, args.data_dir)

    try:
        while not _should_stop:
            # 先检查持仓（SL/TP）
            try:
                _monitor_positions(args.data_dir)
            except Exception as e:
                log.error("position monitor failed: %s", e)

            # 再扫描新信号
            try:
                meta = run_one_scan(args.data_dir, args.max_candidates)
                _update_status(args.data_dir, meta=meta)
                log.info(
                    "Scan %s  prescreened=%d  analyzed=%d  intents=%d  errors=%d",
                    meta["scan_id"],
                    meta.get("prescreened", 0),
                    meta.get("analyzed", 0),
                    meta.get("intents_fired", 0),
                    meta.get("errors", 0),
                )
            except Exception as e:
                log.exception("Scan cycle crashed: %s", e)
                _update_status(args.data_dir, error=f"{type(e).__name__}: {e}")

            if args.once or _should_stop:
                break

            for _ in range(interval_s):
                if _should_stop:
                    break
                time.sleep(1)

    finally:
        _mark_stopped(args.data_dir)
        log.info("cex_daemon stopped cleanly")

    return 0


if __name__ == "__main__":
    sys.exit(main())
