"""V4 indicators tests — 用手算值验证每个公式."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v4_indicators import (
    true_range, atr, rsi, ema, sma,
    donchian, macd, volume_ma, wick_ratio, slope_pct,
)


def _ohlcv(opens, highs, lows, closes, vols=None):
    """构造 OHLCV DataFrame, index 为 UTC datetime."""
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    vols = vols if vols is not None else [1000] * n
    return pd.DataFrame({"open": opens, "high": highs, "low": lows,
                          "close": closes, "volume": vols}, index=idx)


# ── true_range ──────────────────────────────────────────────────────

def test_true_range_first_row_is_high_minus_low():
    df = _ohlcv([100], [105], [99], [102])
    tr = true_range(df)
    assert tr.iloc[0] == 6.0  # 105 - 99


def test_true_range_uses_prev_close_when_gap():
    """K2: H=110, L=105, prev_close=102 → TR = max(5, |110-102|, |105-102|) = 8."""
    df = _ohlcv([100, 106], [105, 110], [99, 105], [102, 108])
    tr = true_range(df)
    assert tr.iloc[0] == 6.0
    assert tr.iloc[1] == 8.0  # max(110-105, |110-102|, |105-102|) = max(5,8,3) = 8


def test_true_range_gap_down():
    """K2: H=98, L=95, prev_close=102 → TR = max(3, |98-102|, |95-102|) = 7."""
    df = _ohlcv([100, 96], [105, 98], [99, 95], [102, 97])
    tr = true_range(df)
    assert tr.iloc[1] == 7.0


# ── ATR ─────────────────────────────────────────────────────────────

def test_atr_wilder_smoothing():
    """ATR 用 Wilder (alpha = 1/N), 不是标准 EMA.

    用 4 行数据, period=3 验证.
    TR: [6, 8, 5, 4]
    Wilder smooth alpha = 1/3:
      ATR[0] = 6
      ATR[1] = 6 + 1/3 × (8-6) = 6.667
      ATR[2] = 6.667 + 1/3 × (5-6.667) = 6.111
      ATR[3] = 6.111 + 1/3 × (4-6.111) = 5.407
    """
    df = _ohlcv(
        opens=[100, 106, 109, 105],
        highs=[105, 110, 110, 106],
        lows=[99,  105, 105, 102],
        closes=[102, 108, 106, 104],
    )
    a = atr(df, period=3)
    # TR sequence
    tr = true_range(df)
    assert tr.iloc[0] == 6.0
    assert tr.iloc[1] == 8.0   # max(5, |110-102|=8, |105-102|=3) = 8
    assert tr.iloc[2] == 5.0   # max(5, |110-108|=2, |105-108|=3) = 5
    assert tr.iloc[3] == 4.0   # max(4, |106-106|=0, |102-106|=4) = 4
    # ATR via Wilder
    assert a.iloc[0] == pytest.approx(6.0)
    assert a.iloc[1] == pytest.approx(6.0 + (8.0 - 6.0) / 3)
    assert a.iloc[2] == pytest.approx(6.667 + (5.0 - 6.667) / 3, abs=0.01)


def test_atr_empty():
    df = _ohlcv([], [], [], [])
    a = atr(df)
    assert a.empty


# ── RSI ─────────────────────────────────────────────────────────────

def test_rsi_all_up_approaches_100():
    """连续上涨 → RSI 应趋近 100."""
    closes = list(range(1, 30))
    df = _ohlcv(opens=closes, highs=closes, lows=closes, closes=closes)
    r = rsi(df["close"], period=14)
    # 后期 RSI 应该接近 100 (avg_loss → 0)
    assert r.iloc[-1] > 99


def test_rsi_all_down_approaches_0():
    closes = list(range(30, 1, -1))
    df = _ohlcv(opens=closes, highs=closes, lows=closes, closes=closes)
    r = rsi(df["close"], period=14)
    assert r.iloc[-1] < 1


def test_rsi_alternating_around_50():
    """涨跌相等 → RSI 应该接近 50."""
    closes = []
    for i in range(30):
        closes.append(100 + (i % 2))   # 100, 101, 100, 101, ...
    df = _ohlcv(opens=closes, highs=closes, lows=closes, closes=closes)
    r = rsi(df["close"], period=14)
    # 后期 RSI 应该在 50 附近
    assert 30 < r.iloc[-1] < 70


# ── EMA ─────────────────────────────────────────────────────────────

def test_ema_first_value_equals_first_close():
    """pandas .ewm(adjust=False).mean() 首值 = 首 close."""
    s = pd.Series([10, 12, 14, 16, 18])
    e = ema(s, period=3)
    assert e.iloc[0] == 10
    # alpha = 2/(3+1) = 0.5
    # e[1] = 0.5*12 + 0.5*10 = 11
    assert e.iloc[1] == pytest.approx(11)
    # e[2] = 0.5*14 + 0.5*11 = 12.5
    assert e.iloc[2] == pytest.approx(12.5)


def test_ema_smooths_noise():
    s = pd.Series([100, 200, 100, 200, 100, 200])
    e = ema(s, period=3)
    # EMA 振幅应 < raw 振幅
    assert e.max() - e.min() < 100


# ── SMA ─────────────────────────────────────────────────────────────

def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5])
    m = sma(s, period=3)
    # 前 2 个 NaN (min_periods=3)
    assert pd.isna(m.iloc[0])
    assert pd.isna(m.iloc[1])
    assert m.iloc[2] == 2.0  # (1+2+3)/3
    assert m.iloc[3] == 3.0  # (2+3+4)/3
    assert m.iloc[4] == 4.0


# ── Donchian ────────────────────────────────────────────────────────

def test_donchian_basic():
    df = _ohlcv(
        opens=[100, 101, 102, 103, 104],
        highs=[105, 106, 107, 110, 109],
        lows=[95, 96, 98, 100, 102],
        closes=[100, 101, 102, 103, 104],
    )
    upper, lower, mid = donchian(df, period=3)
    # 前 2 NaN, 第 3 行: max([105,106,107])=107, min([95,96,98])=95
    assert pd.isna(upper.iloc[0])
    assert pd.isna(upper.iloc[1])
    assert upper.iloc[2] == 107
    assert lower.iloc[2] == 95
    assert mid.iloc[2] == 101
    # 第 4 行: max([106,107,110])=110, min([96,98,100])=96
    assert upper.iloc[3] == 110
    assert lower.iloc[3] == 96


# ── MACD ────────────────────────────────────────────────────────────

def test_macd_returns_three_series_aligned():
    s = pd.Series(np.linspace(100, 200, 50))
    line, signal, hist = macd(s)
    assert len(line) == len(signal) == len(hist) == 50
    # 单调上升 → MACD line 应该 > 0 (fast EMA > slow EMA)
    assert line.iloc[-1] > 0


def test_macd_zero_when_constant():
    s = pd.Series([100.0] * 50)
    line, signal, hist = macd(s)
    # 常数 → 所有 EMA 都等于 100 → line = 0
    assert line.iloc[-1] == pytest.approx(0)
    assert hist.iloc[-1] == pytest.approx(0)


# ── Volume MA ───────────────────────────────────────────────────────

def test_volume_ma():
    v = pd.Series([100, 200, 300, 400, 500])
    m = volume_ma(v, period=3)
    assert pd.isna(m.iloc[0])
    assert pd.isna(m.iloc[1])
    assert m.iloc[2] == 200
    assert m.iloc[4] == 400


# ── Wick ratio ──────────────────────────────────────────────────────

def test_wick_ratio_lower_bullish_reversal():
    """长下影 (mean-rev LONG 信号): open=100, close=101, low=95, high=102.
    body = 1, lower_wick = 100-95 = 5, ratio = 5.
    """
    df = _ohlcv(opens=[100], highs=[102], lows=[95], closes=[101])
    r = wick_ratio(df, side="lower")
    assert r.iloc[0] == 5.0


def test_wick_ratio_upper():
    """长上影 (mean-rev SHORT 信号): open=100, close=99, low=98, high=110.
    body = 1, upper_wick = 110-100 = 10, ratio = 10.
    """
    df = _ohlcv(opens=[100], highs=[110], lows=[98], closes=[99])
    r = wick_ratio(df, side="upper")
    assert r.iloc[0] == 10.0


def test_wick_ratio_zero_body_returns_nan():
    """doji: open = close → body = 0 → ratio NaN."""
    df = _ohlcv(opens=[100], highs=[105], lows=[95], closes=[100])
    r = wick_ratio(df, side="lower")
    assert pd.isna(r.iloc[0])


def test_wick_ratio_invalid_side_raises():
    df = _ohlcv(opens=[100], highs=[105], lows=[95], closes=[100])
    with pytest.raises(ValueError):
        wick_ratio(df, side="bogus")


# ── slope_pct ───────────────────────────────────────────────────────

def test_slope_pct_basic():
    s = pd.Series([100, 102, 104, 110])
    sl = slope_pct(s, lookback=2)
    # 前 2 NaN; sl[2] = (104-100)/100*100 = 4.0
    assert pd.isna(sl.iloc[0])
    assert pd.isna(sl.iloc[1])
    assert sl.iloc[2] == pytest.approx(4.0)
    # sl[3] = (110-102)/102*100 ≈ 7.84
    assert sl.iloc[3] == pytest.approx(8 / 102 * 100, abs=0.01)


def test_slope_pct_negative():
    s = pd.Series([100, 100, 90])
    sl = slope_pct(s, lookback=2)
    assert sl.iloc[2] == pytest.approx(-10.0)
