"""V4 backtest tests — pipeline 端到端验证 + stats / 辅助函数."""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v4_backtest import (
    BacktestResult,
    _compute_regime_durations,
    _compute_symbol_history,
    run_backtest,
    group_metrics,
    MAX_CONCURRENT, MIN_CONVICTION,
)
from v4_paper_engine import V4Position, V4PositionLeg
from v4_signals import V4Signal


# ── fixture ──────────────────────────────────────────────────────

def _make_trade(symbol, direction, entry, close_price, closed_at, frac=1.0, reason="hit_sl"):
    """构造一个 V4Position 用于 stats 测试."""
    opened_at = closed_at - pd.Timedelta(hours=2)
    sig = V4Signal(
        symbol=symbol, direction=direction, sub_strategy="breakout_long",
        entry_time=opened_at, entry_price=entry,
        sl_price=entry - 2, tp1_price=entry + 2, tp2_price=entry + 4, tp3_price=entry + 6,
        atr_4h=1.0, btc_regime="up", features={},
    )
    pos = V4Position(
        signal=sig, opened_at=opened_at, qty=400.0 / entry,
        phase="A", sl_price=sig.sl_price, high_water_mark=entry,
        closed_at=closed_at, close_reason=reason,
    )
    pos.legs.append(V4PositionLeg(closed_at, close_price, frac, reason))
    return pos


# ── _compute_regime_durations ────────────────────────────────────

def test_regime_durations_basic():
    idx = pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC")
    regime = pd.Series(["chop", "chop", "up", "up", "up", "chop", "down", "down", "down", "down"], index=idx)
    dur = _compute_regime_durations(regime)
    assert dur.tolist() == [1, 2, 1, 2, 3, 1, 1, 2, 3, 4]


def test_regime_durations_empty():
    dur = _compute_regime_durations(pd.Series([], dtype="object"))
    assert dur.empty


def test_regime_durations_constant():
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    regime = pd.Series(["up"] * 5, index=idx)
    dur = _compute_regime_durations(regime)
    assert dur.tolist() == [1, 2, 3, 4, 5]


# ── _compute_symbol_history ──────────────────────────────────────

def test_symbol_history_no_trades():
    t = pd.Timestamp("2026-02-01", tz="UTC")
    h = _compute_symbol_history([], "BTCUSDT", t)
    assert h == {"win_rate_30d": 0.0, "total_n": 0}


def test_symbol_history_filters_by_symbol_and_date():
    t = pd.Timestamp("2026-02-01", tz="UTC")
    trades = [
        # BTC win 5 days ago — in window
        _make_trade("BTCUSDT", "LONG", 100, 105, t - pd.Timedelta(days=5)),
        # BTC loss 10 days ago — in window
        _make_trade("BTCUSDT", "LONG", 100, 95, t - pd.Timedelta(days=10)),
        # BTC win 60 days ago — OUT of window
        _make_trade("BTCUSDT", "LONG", 100, 110, t - pd.Timedelta(days=60)),
        # ETH win — wrong symbol
        _make_trade("ETHUSDT", "LONG", 100, 110, t - pd.Timedelta(days=5)),
    ]
    h = _compute_symbol_history(trades, "BTCUSDT", t)
    assert h["total_n"] == 2     # only first 2 (BTC within 30d)
    assert h["win_rate_30d"] == 0.5    # 1 win / 2 total


# ── BacktestResult.stats() ──────────────────────────────────────

def test_stats_empty():
    r = BacktestResult()
    s = r.stats()
    assert s["n_trades"] == 0
    assert s["win_rate"] == 0
    assert s["total_pnl"] == 0


def test_stats_with_trades_pf_and_winrate():
    t = pd.Timestamp("2026-02-01", tz="UTC")
    # 3 wins (+10 each), 2 losses (-5 each)
    trades = [
        _make_trade("BTCUSDT", "LONG", 100, 110, t),    # +10 - fees
        _make_trade("BTCUSDT", "LONG", 100, 110, t),    # +10
        _make_trade("BTCUSDT", "LONG", 100, 110, t),    # +10
        _make_trade("BTCUSDT", "LONG", 100, 95, t),     # -5
        _make_trade("BTCUSDT", "LONG", 100, 95, t),     # -5
    ]
    r = BacktestResult(trades=trades)
    s = r.stats()
    assert s["n_trades"] == 5
    assert s["wins"] == 3
    assert s["losses"] == 2
    assert s["win_rate"] == 0.6
    # PF = sum_wins / sum_losses
    # 各 leg pnl 含 fees, 计算近似
    assert s["pf"] > 1.0   # 3×10 > 2×5
    assert s["total_pnl"] > 0


def test_stats_max_dd_from_daily_pnl():
    r = BacktestResult(daily_pnl={
        pd.Timestamp("2026-01-01").date(): 10.0,
        pd.Timestamp("2026-01-02").date(): 5.0,    # cum 15 (peak)
        pd.Timestamp("2026-01-03").date(): -8.0,   # cum 7 (dd 8)
        pd.Timestamp("2026-01-04").date(): -5.0,   # cum 2 (dd 13)
        pd.Timestamp("2026-01-05").date(): 3.0,    # cum 5
    })
    s = r.stats()
    assert s["max_dd"] == pytest.approx(13.0)


# ── group_metrics ───────────────────────────────────────────────

def test_group_metrics_by_regime():
    t = pd.Timestamp("2026-02-01", tz="UTC")
    trades = []
    for sym, win in [("A", True), ("A", True), ("A", False), ("B", True), ("B", False)]:
        tr = _make_trade(sym, "LONG", 100, 110 if win else 95, t)
        trades.append(tr)

    r = BacktestResult(trades=trades)
    g = group_metrics(r, by="symbol")
    assert "A" in g
    assert "B" in g
    assert g["A"]["n"] == 3
    assert g["B"]["n"] == 2
    assert g["A"]["win_rate"] == pytest.approx(2/3)
    assert g["B"]["win_rate"] == pytest.approx(0.5)


# ── 常量 ──────────────────────────────────────────────────────

def test_constants():
    assert MAX_CONCURRENT == 5
    assert MIN_CONVICTION == 6


# ── 端到端 mock 测试 (run_backtest 不挂掉) ─────────────────────
# 这部分在 sandbox 环境没真 parquet 数据所以 skip.
# 用户在 Mac 上跑真数据时, CLI 一条命令验证.

@pytest.mark.skip(reason="needs real parquet data in ~/cresus-bot/v4_klines/, run on user Mac")
def test_run_backtest_smoke():
    """Smoke test — 用 monkeypatched 加载函数, 跑端到端."""
    pass
