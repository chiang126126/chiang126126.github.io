"""V4 Backtest Engine — 历史数据跑 V4 策略, 输出 PF / 胜率 / drawdown.

主循环 (跨 symbol portfolio, time-stepped 15min):
  for t in time_range (15min):
    1. update_position(open_positions, bar_15m[t])  # 收 SL/TP, phase 推进
    2. 仅 1h 整点 (t.minute == 0): 检新信号
       - if concurrent < MAX: check signals; if conv ≥ 6: open_position

跨 symbol 共享: 1 个 portfolio state (max 5 concurrent across all symbols).
同 symbol 不重复开 (avoid double exposure).

性能预估: 17280 个 15min bar × 251 symbol × 信号检查(每 1h 整点 720 次) ≈ 30-60 min run.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import logging
from typing import Iterable, Optional
import pandas as pd

from v4_data_fetcher import load_klines, load_funding, load_oi, load_taker
from v4_regime import regime_series
from v4_signals import check_all_signals
from v4_conviction import compute_conviction, DIAMOND_THRESHOLD
from v4_paper_engine import (
    V4Position, open_position, update_position, compute_final_pnl, _close_remaining,
)

log = logging.getLogger(__name__)


# Portfolio 配置 (跟 V4_SPEC 一致)
MAX_CONCURRENT = 5                 # 跨所有 symbol 总并发上限
MIN_CONVICTION = DIAMOND_THRESHOLD  # 6


@dataclass
class BacktestResult:
    """回测产出: 全部 trades + 日 PnL + stats helper."""
    trades: list[V4Position] = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)         # date → cumulative pnl this day
    skipped_symbols: list = field(default_factory=list)

    def stats(self) -> dict:
        """计算 PF / 胜率 / drawdown / Sharpe."""
        pnls = [compute_final_pnl(t) for t in self.trades]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        win_rate = wins / n if n > 0 else 0.0
        total_pnl = sum(pnls)
        sum_wins = sum(p for p in pnls if p > 0)
        sum_losses = abs(sum(p for p in pnls if p < 0))
        if sum_losses > 0:
            pf = sum_wins / sum_losses
        elif sum_wins > 0:
            pf = float("inf")
        else:
            pf = 0.0

        # Max drawdown from cumulative daily PnL (regardless of trade count)
        max_dd = 0.0
        if self.daily_pnl:
            cumulative = 0.0
            peak = 0.0
            for day in sorted(self.daily_pnl.keys()):
                cumulative += self.daily_pnl[day]
                peak = max(peak, cumulative)
                dd = peak - cumulative
                max_dd = max(max_dd, dd)

        return {
            "n_trades": n,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / n if n > 0 else 0.0,
            "pf": pf,
            "max_dd": max_dd,
            "wins": wins,
            "losses": losses,
        }


# ── 辅助 ───────────────────────────────────────────────────────

def _compute_regime_durations(regime: pd.Series) -> pd.Series:
    """At each t, 当前 regime 已持续多少 bar (1h)."""
    if regime.empty:
        return pd.Series([], dtype=int, index=regime.index)
    durations = []
    current = None
    streak = 0
    for r in regime:
        if r == current:
            streak += 1
        else:
            current = r
            streak = 1
        durations.append(streak)
    return pd.Series(durations, index=regime.index, dtype=int)


def _compute_symbol_history(closed_trades: list[V4Position],
                            symbol: str, t: pd.Timestamp,
                            lookback_days: int = 30) -> dict:
    """At time t, 该 symbol 最近 30d closed 的 win rate."""
    cutoff = t - pd.Timedelta(days=lookback_days)
    sym_trades = [tr for tr in closed_trades
                  if tr.signal.symbol == symbol
                  and tr.closed_at is not None
                  and tr.closed_at >= cutoff
                  and tr.closed_at <= t]
    n = len(sym_trades)
    if n == 0:
        return {"win_rate_30d": 0.0, "total_n": 0}
    wins = sum(1 for tr in sym_trades if compute_final_pnl(tr) > 0)
    return {"win_rate_30d": wins / n, "total_n": n}


def _load_symbol_data(symbol: str) -> Optional[dict]:
    """加载单 symbol 所有 timeframes + funding/oi/taker. 返回 None 如果核心 K 线缺失."""
    klines_15m = load_klines(symbol, "15m")
    klines_1h = load_klines(symbol, "1h")
    klines_4h = load_klines(symbol, "4h")
    klines_1d = load_klines(symbol, "1d")
    if klines_15m.empty or klines_1h.empty or klines_4h.empty or klines_1d.empty:
        return None
    return {
        "1d": klines_1d,
        "4h": klines_4h,
        "1h": klines_1h,
        "15m": klines_15m,
        "funding": load_funding(symbol),
        "oi": load_oi(symbol),
        "taker": load_taker(symbol),
    }


# ── 主入口 ──────────────────────────────────────────────────────

def run_backtest(
    symbols: Iterable[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    max_concurrent: int = MAX_CONCURRENT,
    min_conviction: int = MIN_CONVICTION,
    log_every: int = 2000,
) -> BacktestResult:
    """Run V4 backtest on given symbols over [start, end].

    Args:
        symbols: list of symbol strings (BTCUSDT 单独加载, 不在 trading universe)
        start, end: time range (UTC)
        max_concurrent: cross-symbol position 并发上限
        min_conviction: signal score 下限
        log_every: 每 N 个 15min bar 打 progress log
    """
    # 1. Load BTC + regime
    log.info("Loading BTC data...")
    btc_1h = load_klines("BTCUSDT", "1h")
    btc_1d = load_klines("BTCUSDT", "1d")
    if btc_1h.empty or btc_1d.empty:
        raise RuntimeError("BTC data missing — required for regime detection")
    log.info(f"BTC 1h: {len(btc_1h)} bars, 1d: {len(btc_1d)} bars")

    log.info("Computing BTC regime series...")
    regime = regime_series(btc_1h)
    durations = _compute_regime_durations(regime)

    # 2. Pre-load symbol data
    log.info(f"Pre-loading {len(list(symbols))} symbols...")
    symbol_data: dict[str, dict] = {}
    skipped: list[str] = []
    for sym in symbols:
        if sym == "BTCUSDT":
            continue
        data = _load_symbol_data(sym)
        if data is None:
            skipped.append(sym)
            continue
        symbol_data[sym] = data
    log.info(f"Loaded {len(symbol_data)} symbols, skipped {len(skipped)} (insufficient data)")

    # 3. Build time index
    time_index = pd.date_range(start, end, freq="15min", tz="UTC")
    log.info(f"Main loop: {len(time_index)} 15min bars from {start} to {end}")

    # 4. State
    open_positions: list[V4Position] = []
    closed_trades: list[V4Position] = []
    daily_pnl: dict = {}

    # 5. Sorted symbol list for fairness (alphabetical)
    sorted_symbols = sorted(symbol_data.keys())

    # 6. Main loop
    for i, t in enumerate(time_index):
        if i % log_every == 0:
            log.info(f"  [{i/len(time_index)*100:.1f}%] t={t.strftime('%Y-%m-%d %H:%M')} "
                     f"open={len(open_positions)} closed={len(closed_trades)}")

        # 6a. Update all open positions with 15m bar at t
        for pos in list(open_positions):
            sym = pos.signal.symbol
            try:
                bar_15m = symbol_data[sym]["15m"].loc[t]
            except KeyError:
                continue   # no bar at this t for this symbol
            update_position(pos, bar_15m, t)
            if not pos.is_open:
                pnl = compute_final_pnl(pos)
                closed_trades.append(pos)
                open_positions.remove(pos)
                day = pos.closed_at.date()
                daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl

        # 6b. Signal check (only at 1h boundary)
        if t.minute != 0:
            continue
        if len(open_positions) >= max_concurrent:
            continue

        # BTC regime at t (use asof for non-exact alignment)
        btc_regime = regime.asof(t) if not regime.empty else "chop"
        if pd.isna(btc_regime) or btc_regime not in ("up", "down", "chop"):
            continue
        regime_duration = int(durations.asof(t)) if not durations.empty else 0

        open_symbols = {p.signal.symbol for p in open_positions}

        for sym in sorted_symbols:
            if len(open_positions) >= max_concurrent:
                break
            if sym in open_symbols:
                continue

            data = symbol_data[sym]
            signals = check_all_signals(
                data["1d"], data["4h"], data["1h"], t, sym, btc_regime
            )
            if not signals:
                continue

            sig = signals[0]
            symbol_history = _compute_symbol_history(closed_trades, sym, t)
            score, _ = compute_conviction(
                sig, data["1d"], data["4h"], btc_1d,
                data["funding"], data["oi"], data["taker"],
                symbol_history, regime_duration,
            )
            if score < min_conviction:
                continue

            pos = open_position(sig, t)
            open_positions.append(pos)
            open_symbols.add(sym)

    # 7. Force close any still-open positions at end
    log.info(f"Backtest done. Force-closing {len(open_positions)} positions still open")
    for pos in list(open_positions):
        sym = pos.signal.symbol
        try:
            last_bar = symbol_data[sym]["15m"].iloc[-1]
            update_position(pos, last_bar, time_index[-1])
        except (KeyError, IndexError):
            pass
        if pos.is_open:
            _close_remaining(pos, time_index[-1], pos.signal.entry_price, "forced_close")
        pnl = compute_final_pnl(pos)
        closed_trades.append(pos)
        day = pos.closed_at.date()
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
        open_positions.remove(pos)

    return BacktestResult(
        trades=closed_trades,
        daily_pnl=daily_pnl,
        skipped_symbols=skipped,
    )


# ── 导出 / 输出 ───────────────────────────────────────────────

def export_trades_csv(result: BacktestResult, output_path: str) -> None:
    """每笔 trade 导 CSV."""
    rows = []
    for tr in result.trades:
        sig = tr.signal
        pnl = compute_final_pnl(tr)
        rows.append({
            "symbol": sig.symbol,
            "direction": sig.direction,
            "sub_strategy": sig.sub_strategy,
            "btc_regime": sig.btc_regime,
            "entry_time": sig.entry_time.isoformat() if sig.entry_time else "",
            "entry_price": sig.entry_price,
            "closed_at": tr.closed_at.isoformat() if tr.closed_at else "",
            "close_reason": tr.close_reason or "",
            "atr_4h": sig.atr_4h,
            "n_legs": len(tr.legs),
            "pnl_usdt": pnl,
            "hold_hours": (tr.closed_at - tr.opened_at).total_seconds() / 3600.0 if tr.closed_at else 0,
        })
    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    log.info(f"Exported {len(rows)} trades to {output_path}")


def group_metrics(result: BacktestResult, by: str = "btc_regime") -> dict:
    """按维度切片 metrics. by = 'btc_regime' | 'sub_strategy' | 'direction' | 'symbol'."""
    if not result.trades:
        return {}
    rows = []
    for tr in result.trades:
        pnl = compute_final_pnl(tr)
        rows.append({"key": getattr(tr.signal, by, "unknown"), "pnl": pnl})
    df = pd.DataFrame(rows)
    grouped = df.groupby("key").agg(
        n=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        avg_pnl=("pnl", "mean"),
        win_rate=("pnl", lambda x: (x > 0).sum() / len(x)),
    )
    return grouped.to_dict(orient="index")


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="V4 backtest runner")
    parser.add_argument("--symbols", nargs="*", help="symbol list (default: 全部 v4_klines/ 里有的)")
    parser.add_argument("--start", default="2025-11-25", help="start date YYYY-MM-DD")
    parser.add_argument("--end", default="2026-05-24", help="end date YYYY-MM-DD")
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT)
    parser.add_argument("--min-conviction", type=int, default=MIN_CONVICTION)
    parser.add_argument("--output", default="v4_backtest_result.csv")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 若没 --symbols, 用 v4_klines/ 里有 1d/4h/1h/15m 的全部 symbol
    if args.symbols:
        symbols = args.symbols
    else:
        from v4_data_fetcher import V4_KLINES_DIR
        all_files = list(V4_KLINES_DIR.glob("*_15m.parquet"))
        symbols = sorted({f.stem.rsplit("_", 1)[0] for f in all_files})
        log.info(f"Auto-detected {len(symbols)} symbols from {V4_KLINES_DIR}")

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")

    result = run_backtest(symbols, start, end,
                          max_concurrent=args.max_concurrent,
                          min_conviction=args.min_conviction)

    export_trades_csv(result, args.output)

    stats = result.stats()
    print("\n=== Overall ===")
    print(json.dumps(stats, indent=2, default=str))

    print("\n=== By BTC regime ===")
    print(json.dumps(group_metrics(result, by="btc_regime"), indent=2, default=str))

    print("\n=== By sub-strategy ===")
    print(json.dumps(group_metrics(result, by="sub_strategy"), indent=2, default=str))

    print(f"\nSkipped symbols ({len(result.skipped_symbols)}): {result.skipped_symbols[:10]}...")
