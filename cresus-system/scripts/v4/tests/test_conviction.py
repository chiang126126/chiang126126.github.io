"""V4 conviction tests — 9 个 feature 各自正反面 + 总分 cap."""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v4_conviction import (
    compute_conviction, ConvictionBreakdown,
    _check_vol_strong, _check_macd_align, _check_weekly_trend,
    _check_btc_align, _check_regime_stable, _check_funding_extreme,
    _check_oi_align, _check_taker_align, _check_history_winrate,
    DIAMOND_THRESHOLD, CAP,
    VOLUME_STRONG_MULT, BTC_ALIGN_PCT, REGIME_STABLE_HOURS,
    FUNDING_EXTREME_PCT, OI_DELTA_PCT,
    TAKER_RATIO_LONG_THRESH, TAKER_RATIO_SHORT_THRESH,
    HISTORY_WIN_RATE_THRESH, HISTORY_MIN_SAMPLES,
)
from v4_signals import V4Signal


# ── 测试 fixture ───────────────────────────────────────────────

def _make_signal(direction="LONG", t=None):
    if t is None:
        t = pd.Timestamp("2026-02-01", tz="UTC")
    return V4Signal(
        symbol="ETHUSDT", direction=direction, sub_strategy="breakout_long",
        entry_time=t, entry_price=100.0,
        sl_price=98.0, tp1_price=102.0, tp2_price=104.0, tp3_price=106.0,
        atr_4h=1.0, btc_regime="up", features={},
    )


def _make_1d_df(n=30, price_func=lambda i: 100.0, vol_func=lambda i: 1000.0,
                 start="2026-01-01"):
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    prices = [price_func(i) for i in range(n)]
    return pd.DataFrame({
        "open": prices, "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices], "close": prices,
        "volume": [vol_func(i) for i in range(n)],
    }, index=idx)


def _make_4h_df(n=100, price_func=lambda i: 100.0, start="2026-01-01"):
    idx = pd.date_range(start, periods=n, freq="4h", tz="UTC")
    prices = [price_func(i) for i in range(n)]
    return pd.DataFrame({
        "open": prices, "high": [p * 1.005 for p in prices],
        "low": [p * 0.995 for p in prices], "close": prices,
        "volume": [1000.0] * n,
    }, index=idx)


def _empty_df():
    return pd.DataFrame()


# ── vol_strong ────────────────────────────────────────────────

def test_vol_strong_triggers():
    """1d 最后 vol > 2× MA → +1."""
    df = _make_1d_df(n=30, vol_func=lambda i: 2500.0 if i == 29 else 1000.0)
    assert _check_vol_strong(df) == 1


def test_vol_strong_not_triggered():
    """1d vol < 2× MA → 0."""
    df = _make_1d_df(n=30, vol_func=lambda i: 1500.0 if i == 29 else 1000.0)
    assert _check_vol_strong(df) == 0


def test_vol_strong_insufficient_data():
    df = _make_1d_df(n=10)
    assert _check_vol_strong(df) == 0


# ── macd_align ────────────────────────────────────────────────

def test_macd_align_long_with_pos_hist():
    """单调涨 → MACD line > signal → hist > 0 → LONG aligned."""
    # 100 根 4h 上涨, 让 MACD 稳定 pos
    df = _make_4h_df(n=100, price_func=lambda i: 100 + i * 0.5)
    assert _check_macd_align(df, "LONG") == 1


def test_macd_align_short_with_neg_hist():
    df = _make_4h_df(n=100, price_func=lambda i: 200 - i * 0.5)   # 单调下跌
    assert _check_macd_align(df, "SHORT") == 1


def test_macd_align_wrong_direction():
    df = _make_4h_df(n=100, price_func=lambda i: 100 + i * 0.5)
    assert _check_macd_align(df, "SHORT") == 0   # 涨势, SHORT 不 align


def test_macd_align_insufficient_data():
    df = _make_4h_df(n=20)   # < 26+9
    assert _check_macd_align(df, "LONG") == 0


# ── weekly_trend ──────────────────────────────────────────────

def test_weekly_trend_long_5d_above_20d():
    """5d MA > 20d MA → LONG aligned."""
    df = _make_1d_df(n=30, price_func=lambda i: 100 + i * 1.0)   # 单调涨, 5d MA > 20d MA
    assert _check_weekly_trend(df, "LONG") == 1


def test_weekly_trend_short_5d_below_20d():
    df = _make_1d_df(n=30, price_func=lambda i: 200 - i * 1.0)
    assert _check_weekly_trend(df, "SHORT") == 1


def test_weekly_trend_wrong_direction():
    df = _make_1d_df(n=30, price_func=lambda i: 100 + i * 1.0)
    assert _check_weekly_trend(df, "SHORT") == 0


# ── btc_align ─────────────────────────────────────────────────

def test_btc_align_long_btc_up_15pct():
    """BTC 1d 涨 2% → LONG +1."""
    closes = [100.0, 102.0]
    idx = pd.date_range("2026-01-01", periods=2, freq="1D", tz="UTC")
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000, 1000]}, index=idx)
    assert _check_btc_align(df, "LONG") == 1


def test_btc_align_short_btc_down():
    closes = [100.0, 97.5]   # -2.5%
    idx = pd.date_range("2026-01-01", periods=2, freq="1D", tz="UTC")
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000, 1000]}, index=idx)
    assert _check_btc_align(df, "SHORT") == 1


def test_btc_align_too_small_change():
    closes = [100.0, 100.5]   # +0.5% < 1.5%
    idx = pd.date_range("2026-01-01", periods=2, freq="1D", tz="UTC")
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000, 1000]}, index=idx)
    assert _check_btc_align(df, "LONG") == 0


# ── regime_stable ─────────────────────────────────────────────

def test_regime_stable_pass_at_threshold():
    assert _check_regime_stable(REGIME_STABLE_HOURS) == 1
    assert _check_regime_stable(REGIME_STABLE_HOURS + 1) == 1


def test_regime_stable_fail_below():
    assert _check_regime_stable(REGIME_STABLE_HOURS - 1) == 0
    assert _check_regime_stable(0) == 0


# ── funding_extreme ───────────────────────────────────────────

def test_funding_extreme_long_with_neg_funding():
    """LONG: funding 极端负 → +1 (空方付钱多, 多方有利)."""
    idx = pd.date_range("2026-01-01", periods=2, freq="8h", tz="UTC")
    df = pd.DataFrame({"funding_rate": [0.0, -0.0010], "mark_price": [100, 100]}, index=idx)   # -0.1%
    assert _check_funding_extreme(df, "LONG") == 1


def test_funding_extreme_short_with_pos_funding():
    idx = pd.date_range("2026-01-01", periods=2, freq="8h", tz="UTC")
    df = pd.DataFrame({"funding_rate": [0.0, 0.0010], "mark_price": [100, 100]}, index=idx)   # +0.1%
    assert _check_funding_extreme(df, "SHORT") == 1


def test_funding_extreme_long_with_pos_funding_no_award():
    """LONG: funding 正 → 不 align (空方少付, 多方相对劣势)."""
    idx = pd.date_range("2026-01-01", periods=2, freq="8h", tz="UTC")
    df = pd.DataFrame({"funding_rate": [0.0, 0.0010], "mark_price": [100, 100]}, index=idx)
    assert _check_funding_extreme(df, "LONG") == 0


def test_funding_extreme_below_threshold():
    idx = pd.date_range("2026-01-01", periods=2, freq="8h", tz="UTC")
    df = pd.DataFrame({"funding_rate": [0.0, -0.0001], "mark_price": [100, 100]}, index=idx)   # -0.01%, < 0.05%
    assert _check_funding_extreme(df, "LONG") == 0


def test_funding_extreme_empty():
    assert _check_funding_extreme(pd.DataFrame(), "LONG") == 0


# ── oi_align ──────────────────────────────────────────────────

def test_oi_align_long_with_oi_increase():
    """LONG: 24h OI 增加 3% → +1."""
    t_now = pd.Timestamp("2026-01-02 12:00", tz="UTC")
    t_24h_ago = t_now - pd.Timedelta(hours=24)
    idx = [t_24h_ago, t_now]
    df = pd.DataFrame({"sum_oi": [10000.0, 10300.0], "sum_oi_value": [1e8, 1e8]}, index=idx)
    assert _check_oi_align(df, t_now, "LONG") == 1


def test_oi_align_short_with_oi_decrease():
    t_now = pd.Timestamp("2026-01-02 12:00", tz="UTC")
    t_24h_ago = t_now - pd.Timedelta(hours=24)
    idx = [t_24h_ago, t_now]
    df = pd.DataFrame({"sum_oi": [10000.0, 9700.0], "sum_oi_value": [1e8, 1e8]}, index=idx)   # -3%
    assert _check_oi_align(df, t_now, "SHORT") == 1


def test_oi_align_long_with_oi_decrease_no_award():
    t_now = pd.Timestamp("2026-01-02 12:00", tz="UTC")
    t_24h_ago = t_now - pd.Timedelta(hours=24)
    idx = [t_24h_ago, t_now]
    df = pd.DataFrame({"sum_oi": [10000.0, 9700.0], "sum_oi_value": [1e8, 1e8]}, index=idx)
    assert _check_oi_align(df, t_now, "LONG") == 0


def test_oi_align_empty():
    t = pd.Timestamp("2026-01-01", tz="UTC")
    assert _check_oi_align(pd.DataFrame(), t, "LONG") == 0


# ── taker_align ───────────────────────────────────────────────

def test_taker_align_long_ratio_high():
    """LONG: taker_buy_ratio = 0.60 ≥ 0.55 → +1."""
    idx = pd.date_range("2026-01-01", periods=2, freq="4h", tz="UTC")
    df = pd.DataFrame({"buy_sell_ratio": [1.0, 1.5], "buy_vol": [500, 600],
                       "sell_vol": [500, 400], "taker_buy_ratio": [0.5, 0.60]}, index=idx)
    assert _check_taker_align(df, "LONG") == 1


def test_taker_align_short_ratio_low():
    idx = pd.date_range("2026-01-01", periods=2, freq="4h", tz="UTC")
    df = pd.DataFrame({"buy_sell_ratio": [1.0, 0.67], "buy_vol": [500, 400],
                       "sell_vol": [500, 600], "taker_buy_ratio": [0.5, 0.40]}, index=idx)
    assert _check_taker_align(df, "SHORT") == 1


def test_taker_align_long_ratio_low_no_award():
    idx = pd.date_range("2026-01-01", periods=2, freq="4h", tz="UTC")
    df = pd.DataFrame({"buy_sell_ratio": [1.0, 1.0], "buy_vol": [500, 500],
                       "sell_vol": [500, 500], "taker_buy_ratio": [0.5, 0.50]}, index=idx)
    assert _check_taker_align(df, "LONG") == 0


def test_taker_align_empty():
    assert _check_taker_align(pd.DataFrame(), "LONG") == 0


# ── history_winrate ──────────────────────────────────────────

def test_history_winrate_pass():
    assert _check_history_winrate({"win_rate_30d": 0.55, "total_n": 10}) == 1


def test_history_winrate_too_few_samples():
    assert _check_history_winrate({"win_rate_30d": 0.80, "total_n": 3}) == 0


def test_history_winrate_below_threshold():
    assert _check_history_winrate({"win_rate_30d": 0.45, "total_n": 10}) == 0


def test_history_winrate_no_data():
    assert _check_history_winrate({}) == 0


# ── compute_conviction 集成测试 ─────────────────────────────

def test_compute_conviction_base_only_when_all_features_fail():
    """全部 feature fail → 只 base=3."""
    sig = _make_signal()
    empty = pd.DataFrame()
    score, bd = compute_conviction(
        sig, df_1d=empty, df_4h=empty, btc_1d=empty,
        funding_df=empty, oi_df=empty, taker_df=empty,
        symbol_history={}, regime_duration_hours=0,
    )
    assert score == 3
    assert bd.base == 3
    assert bd.vol_strong == 0
    assert bd.macd_align == 0


def test_compute_conviction_max_at_cap():
    """所有 feature 都 align → cap at 10."""
    t = pd.Timestamp("2026-02-15 12:00", tz="UTC")
    sig = _make_signal(direction="LONG", t=t)

    # df_1d: 45+ 根, 最后 vol 高 + 上升趋势 (5d > 20d MA)
    df_1d = _make_1d_df(
        n=45,
        price_func=lambda i: 100 + i * 1.0,
        vol_func=lambda i: 2500.0 if i == 44 else 1000.0,
        start="2026-01-01",
    )

    # df_4h: 单调涨 → MACD hist > 0
    df_4h = _make_4h_df(n=300, price_func=lambda i: 100 + i * 0.1, start="2026-01-01")

    # btc_1d: 涨 2%
    btc_idx = pd.date_range("2026-01-01", periods=45, freq="1D", tz="UTC")
    btc_close = [100 + i * 1.0 for i in range(45)]
    # 最后一根 +2% from prev
    btc_close[-1] = btc_close[-2] * 1.02
    btc_1d = pd.DataFrame({"open": btc_close, "high": btc_close, "low": btc_close,
                           "close": btc_close, "volume": [1000]*45}, index=btc_idx)

    # funding: 极端负 -0.1%
    funding_idx = pd.date_range("2026-01-01", periods=100, freq="8h", tz="UTC")
    funding = pd.DataFrame({"funding_rate": [-0.0010]*100, "mark_price": [100]*100}, index=funding_idx)

    # oi: t-24h=10000, t-1h=10300 → 24h delta 3%
    oi_idx = [t - pd.Timedelta(hours=24), t - pd.Timedelta(hours=1)]
    oi = pd.DataFrame({"sum_oi": [10000.0, 10300.0],
                       "sum_oi_value": [1e8, 1e8]}, index=oi_idx)

    # taker: 0.60 ≥ 0.55
    taker_idx = pd.date_range("2026-01-01", periods=100, freq="4h", tz="UTC")
    taker = pd.DataFrame({"buy_sell_ratio": [1.5]*100, "buy_vol": [600]*100,
                          "sell_vol": [400]*100, "taker_buy_ratio": [0.60]*100}, index=taker_idx)

    history = {"win_rate_30d": 0.65, "total_n": 20}

    score, bd = compute_conviction(
        sig, df_1d=df_1d, df_4h=df_4h, btc_1d=btc_1d,
        funding_df=funding, oi_df=oi, taker_df=taker,
        symbol_history=history,
        regime_duration_hours=200,   # > 168 (7d)
    )

    # base 3 + 9 features = 12, cap at 10
    assert score == CAP, f"expected {CAP}, got {score}, breakdown: {bd.to_dict()}"
    assert bd.vol_strong == 1
    assert bd.macd_align == 1
    assert bd.weekly_trend == 1
    assert bd.btc_align == 1
    assert bd.regime_stable == 1
    assert bd.funding_extreme == 1
    assert bd.oi_align == 1
    assert bd.taker_align == 1
    assert bd.history_winrate == 1


def test_breakdown_to_dict_includes_total():
    bd = ConvictionBreakdown(base=3, vol_strong=1, macd_align=1)
    d = bd.to_dict()
    assert d["base"] == 3
    assert d["vol_strong"] == 1
    assert d["total"] == 5


# ── 常量验证 ────────────────────────────────────────────────

def test_constants():
    assert DIAMOND_THRESHOLD == 6
    assert CAP == 10
    assert VOLUME_STRONG_MULT == 2.0
    assert BTC_ALIGN_PCT == 1.5
    assert REGIME_STABLE_HOURS == 7 * 24
    assert FUNDING_EXTREME_PCT == 0.05
    assert OI_DELTA_PCT == 2.0
    assert TAKER_RATIO_LONG_THRESH == 0.55
    assert TAKER_RATIO_SHORT_THRESH == 0.45
    assert HISTORY_WIN_RATE_THRESH == 0.50
    assert HISTORY_MIN_SAMPLES == 5
