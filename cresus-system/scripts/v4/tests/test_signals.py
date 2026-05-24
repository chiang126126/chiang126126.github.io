"""V4 signals tests — 验证 4 个 sub-strategy 触发逻辑.

测试覆盖:
- 各 sub-strategy positive case (信号触发)
- 各 sub-strategy negative case (regime 不匹配 / 量能不够 / 数据不足)
- check_all_signals 按 regime 正确分派
- SL/TP 数学验证 (entry ∓ N × ATR)
- V4Signal dataclass 字段完整
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v4_signals import (
    check_long_breakout, check_short_breakout,
    check_mean_rev_long, check_mean_rev_short,
    check_all_signals,
    V4Signal,
    BREAKOUT_DONCHIAN_PERIOD, BREAKOUT_VOLUME_MULT,
    MEANREV_RSI_OVERSOLD, MEANREV_RSI_OVERBOUGHT, MEANREV_WICK_BODY_RATIO,
    SL_ATR_MULT, TP1_ATR_MULT, TP2_ATR_MULT, TP3_ATR_MULT,
)


# ── 构造测试 K 线 ─────────────────────────────────────────────────

def _make_df(opens, highs, lows, closes, volumes=None, freq="1D", start="2026-01-01"):
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes if volumes is not None else [1000.0] * n,
    }, index=idx)


def _make_uniform_4h(n=200, price=50000.0, start="2026-01-01"):
    """构造平稳 4h K 线 (用于 ATR 计算)."""
    idx = pd.date_range(start, periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "open": [price] * n, "high": [price * 1.005] * n, "low": [price * 0.995] * n,
        "close": [price] * n, "volume": [1000.0] * n,
    }, index=idx)


def _aligned_dfs(n_days=30, base_price=100.0):
    """构造跨 n_days 的 3 时间框对齐 dfs (1d / 4h / 1h, end 同时刻)."""
    start = "2026-01-01"
    df_1d = pd.DataFrame({
        "open": [base_price]*n_days, "high": [base_price]*n_days,
        "low": [base_price]*n_days, "close": [base_price]*n_days,
        "volume": [1000.0]*n_days,
    }, index=pd.date_range(start, periods=n_days, freq="1D", tz="UTC"))
    df_4h = pd.DataFrame({
        "open": [base_price]*(n_days*6), "high": [base_price*1.005]*(n_days*6),
        "low": [base_price*0.995]*(n_days*6), "close": [base_price]*(n_days*6),
        "volume": [1000.0]*(n_days*6),
    }, index=pd.date_range(start, periods=n_days*6, freq="4h", tz="UTC"))
    df_1h = pd.DataFrame({
        "open": [base_price]*(n_days*24), "high": [base_price*1.001]*(n_days*24),
        "low": [base_price*0.999]*(n_days*24), "close": [base_price]*(n_days*24),
        "volume": [1000.0]*(n_days*24),
    }, index=pd.date_range(start, periods=n_days*24, freq="1h", tz="UTC"))
    return df_1d, df_4h, df_1h


# ── Long Breakout ────────────────────────────────────────────────

def test_long_breakout_not_triggered_wrong_regime():
    """btc_regime != 'up' 不触发."""
    df_1d = _make_df([100]*30, [100]*30, [100]*30, [100]*30)
    df_4h = _make_uniform_4h()
    t = df_1d.index[-1]
    assert check_long_breakout(df_1d, df_4h, t, "BTCUSDT", "down") is None
    assert check_long_breakout(df_1d, df_4h, t, "BTCUSDT", "chop") is None


def test_long_breakout_not_triggered_insufficient_data():
    """< 21 根 daily 不触发."""
    df_1d = _make_df([100]*10, [100]*10, [100]*10, [100]*10)
    df_4h = _make_uniform_4h()
    t = df_1d.index[-1]
    assert check_long_breakout(df_1d, df_4h, t, "BTCUSDT", "up") is None


def test_long_breakout_triggers_on_breakout():
    """前 20 日 high 都是 100, 第 21 日 close=110 (突破), volume=2000 (~1.9x MA) → 触发."""
    highs = [100.0] * 20 + [110.5]
    lows = [99.0] * 20 + [100.0]
    closes = [100.0] * 20 + [110.0]
    opens = [99.5] * 21
    vols = [1000.0] * 20 + [2000.0]
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    t = df_1d.index[-1]
    s = check_long_breakout(df_1d, df_4h, t, "BTCUSDT", "up")
    assert s is not None
    assert s.symbol == "BTCUSDT"
    assert s.direction == "LONG"
    assert s.sub_strategy == "breakout_long"
    assert s.entry_price == 110.0
    assert s.features["donchian_upper"] == 100.0
    # vol_ma 用 rolling(20) 含今日 = (19×1000 + 2000)/20 = 1050, ratio = 2000/1050 ≈ 1.905
    assert s.features["volume_ratio"] > BREAKOUT_VOLUME_MULT  # > 1.5


def test_long_breakout_not_triggered_low_volume():
    """突破价格, 但 volume 不到 1.5x MA → 不触发."""
    highs = [100.0] * 20 + [110.5]
    lows = [99.0] * 20 + [100.0]
    closes = [100.0] * 20 + [110.0]
    opens = [99.5] * 21
    vols = [1000.0] * 20 + [1200.0]   # 1.2x, 不足 1.5x
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    t = df_1d.index[-1]
    assert check_long_breakout(df_1d, df_4h, t, "BTCUSDT", "up") is None


def test_long_breakout_not_triggered_no_breakout():
    """volume 大但 close 没破 Donchian 上轨."""
    highs = [100.0] * 21
    lows = [99.0] * 21
    closes = [100.0] * 21   # close 都等于上轨, 不算"突破" (要求 strict >)
    opens = [99.5] * 21
    vols = [1000.0] * 20 + [2000.0]
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    t = df_1d.index[-1]
    assert check_long_breakout(df_1d, df_4h, t, "BTCUSDT", "up") is None


def test_long_breakout_sl_tp_math():
    """SL/TP = entry ∓ N × ATR(4h)."""
    highs = [100.0] * 20 + [110.5]
    lows = [99.0] * 20 + [100.0]
    closes = [100.0] * 20 + [110.0]
    opens = [99.5] * 21
    vols = [1000.0] * 20 + [2000.0]
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    t = df_1d.index[-1]
    s = check_long_breakout(df_1d, df_4h, t, "BTCUSDT", "up")
    assert s is not None
    # ATR ≈ 0.001 (4h K high-low 是 100*0.01=1), Wilder smoothing → ATR ≈ 1
    assert s.sl_price == pytest.approx(110.0 - SL_ATR_MULT * s.atr_4h)
    assert s.tp1_price == pytest.approx(110.0 + TP1_ATR_MULT * s.atr_4h)
    assert s.tp2_price == pytest.approx(110.0 + TP2_ATR_MULT * s.atr_4h)
    assert s.tp3_price == pytest.approx(110.0 + TP3_ATR_MULT * s.atr_4h)
    assert s.sl_price < s.entry_price
    assert s.tp1_price > s.entry_price
    assert s.tp2_price > s.tp1_price
    assert s.tp3_price > s.tp2_price


# ── Short Breakout ───────────────────────────────────────────────

def test_short_breakout_triggers_on_breakdown():
    """前 20 日 low 都是 100, 第 21 日 close=90 (跌破), volume=2000 → 触发."""
    highs = [101.0] * 20 + [100.5]
    lows = [100.0] * 20 + [89.0]
    closes = [100.5] * 20 + [90.0]
    opens = [100.5] * 21
    vols = [1000.0] * 20 + [2000.0]
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    t = df_1d.index[-1]
    s = check_short_breakout(df_1d, df_4h, t, "BTCUSDT", "down")
    assert s is not None
    assert s.direction == "SHORT"
    assert s.sub_strategy == "breakout_short"
    assert s.entry_price == 90.0
    assert s.features["donchian_lower"] == 100.0


def test_short_breakout_sl_tp_math():
    """SHORT: SL above entry, TP below entry."""
    highs = [101.0] * 20 + [100.5]
    lows = [100.0] * 20 + [89.0]
    closes = [100.5] * 20 + [90.0]
    opens = [100.5] * 21
    vols = [1000.0] * 20 + [2000.0]
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    t = df_1d.index[-1]
    s = check_short_breakout(df_1d, df_4h, t, "BTCUSDT", "down")
    assert s is not None
    assert s.sl_price > s.entry_price
    assert s.tp1_price < s.entry_price
    assert s.tp2_price < s.tp1_price
    assert s.tp3_price < s.tp2_price


def test_short_breakout_wrong_regime():
    df_1d = _make_df([100]*30, [100]*30, [100]*30, [100]*30)
    df_4h = _make_uniform_4h()
    t = df_1d.index[-1]
    assert check_short_breakout(df_1d, df_4h, t, "BTCUSDT", "up") is None


# ── Mean Reversion LONG ─────────────────────────────────────────

def test_mean_rev_long_triggers():
    """RSI < 30 (1d 单调跌) + 4h 大下影 + 1h 量能足 → 触发."""
    n_days = 30
    # 1d: 30 根单调下跌 (RSI 接近 0)
    closes_1d = [100.0 - i * 0.5 for i in range(n_days)]
    df_1d = _make_df(closes_1d, [c * 1.001 for c in closes_1d],
                     [c * 0.999 for c in closes_1d], closes_1d, freq="1D")

    # 4h: n_days × 6 = 180 根, 最后一根 open=100, close=101 (body=1), low=95 (下影 5)
    n_4h = n_days * 6
    o = [100.0] * n_4h
    h = [101.5] * n_4h
    l = [99.0] * (n_4h - 1) + [95.0]
    c = [101.0] * n_4h
    df_4h = _make_df(o, h, l, c, freq="4h")

    # 1h: n_days × 24 根, 最后一根 volume spike
    n_1h = n_days * 24
    vols = [1000.0] * (n_1h - 1) + [2500.0]
    df_1h = _make_df([100.0] * n_1h, [100.5] * n_1h, [99.5] * n_1h, [100.0] * n_1h, vols, freq="1h")

    t = df_1h.index[-1]
    s = check_mean_rev_long(df_1d, df_4h, df_1h, t, "ETHUSDT", "chop")
    assert s is not None, "应该触发"
    assert s.direction == "LONG"
    assert s.sub_strategy == "mean_rev_long"
    assert s.features["rsi_14_1d"] < MEANREV_RSI_OVERSOLD
    assert s.features["lower_wick_ratio"] >= MEANREV_WICK_BODY_RATIO


def test_mean_rev_long_wrong_regime():
    df_1d = _make_df([100]*30, [101]*30, [99]*30, [100]*30)
    df_4h = _make_uniform_4h()
    df_1h = _make_df([100]*30, [101]*30, [99]*30, [100]*30, freq="1h")
    t = df_1h.index[-1]
    assert check_mean_rev_long(df_1d, df_4h, df_1h, t, "ETHUSDT", "up") is None
    assert check_mean_rev_long(df_1d, df_4h, df_1h, t, "ETHUSDT", "down") is None


def test_mean_rev_long_no_extreme_rsi():
    """RSI 50 (不极端) → 不触发."""
    n = 30
    closes = [100.0 + (i % 2) * 0.1 for i in range(n)]   # alternating 100, 100.1
    df_1d = _make_df(closes, [c*1.001 for c in closes], [c*0.999 for c in closes], closes)

    # 4h 有大下影
    df_4h = _make_df([100]*50, [101.5]*50, [99]*49 + [95], [101]*50, freq="4h")
    df_1h = _make_df([100]*50, [101]*50, [99]*50, [100]*50, [1000]*49 + [2500], freq="1h")

    t = df_1h.index[-1]
    s = check_mean_rev_long(df_1d, df_4h, df_1h, t, "ETHUSDT", "chop")
    assert s is None  # RSI 不够极端


def test_mean_rev_long_no_wick():
    """RSI 极端但 4h 无大下影 → 不触发."""
    closes_1d = [100.0 - i * 0.5 for i in range(30)]
    df_1d = _make_df(closes_1d, [c * 1.001 for c in closes_1d],
                     [c * 0.999 for c in closes_1d], closes_1d)
    # 4h K body 大, 下影几乎没 — body=10, wick=0
    df_4h = _make_df([100]*50, [101]*50, [99.5]*50, [110]*50, freq="4h")
    df_1h = _make_df([100]*50, [101]*50, [99]*50, [100]*50, [1000]*49 + [2500], freq="1h")
    t = df_1h.index[-1]
    s = check_mean_rev_long(df_1d, df_4h, df_1h, t, "ETHUSDT", "chop")
    assert s is None


# ── Mean Reversion SHORT ────────────────────────────────────────

def test_mean_rev_short_triggers():
    """RSI > 70 (1d 单调涨) + 4h 大上影 + 1h 量能足 → 触发."""
    n_days = 30
    closes_1d = [100.0 + i * 0.5 for i in range(n_days)]
    df_1d = _make_df(closes_1d, [c * 1.001 for c in closes_1d],
                     [c * 0.999 for c in closes_1d], closes_1d, freq="1D")

    # 4h: 最后一根 open=100, close=99 (body=1), high=105 (上影 5)
    n_4h = n_days * 6
    df_4h = _make_df([100]*n_4h, [101]*(n_4h-1) + [105], [98.5]*n_4h, [99]*n_4h, freq="4h")

    # 1h: volume 在尾部 spike
    n_1h = n_days * 24
    df_1h = _make_df([100]*n_1h, [101]*n_1h, [99]*n_1h, [100]*n_1h, [1000]*(n_1h-1) + [2500], freq="1h")

    t = df_1h.index[-1]
    s = check_mean_rev_short(df_1d, df_4h, df_1h, t, "ETHUSDT", "chop")
    assert s is not None
    assert s.direction == "SHORT"
    assert s.sub_strategy == "mean_rev_short"
    assert s.features["rsi_14_1d"] > MEANREV_RSI_OVERBOUGHT
    assert s.features["upper_wick_ratio"] >= MEANREV_WICK_BODY_RATIO


# ── check_all_signals 调度 ──────────────────────────────────────

def test_check_all_signals_up_regime_calls_long_breakout():
    """up regime → 只调 long_breakout, 不调其它."""
    highs = [100.0] * 20 + [110.5]
    lows = [99.0] * 20 + [100.0]
    closes = [100.0] * 20 + [110.0]
    opens = [99.5] * 21
    vols = [1000.0] * 20 + [2000.0]
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    df_1h = _make_df([100]*30, [101]*30, [99]*30, [100]*30, freq="1h")

    t = df_1d.index[-1]
    signals = check_all_signals(df_1d, df_4h, df_1h, t, "BTCUSDT", "up")
    assert len(signals) == 1
    assert signals[0].sub_strategy == "breakout_long"


def test_check_all_signals_down_regime():
    highs = [101.0] * 20 + [100.5]
    lows = [100.0] * 20 + [89.0]
    closes = [100.5] * 20 + [90.0]
    opens = [100.5] * 21
    vols = [1000.0] * 20 + [2000.0]
    df_1d = _make_df(opens, highs, lows, closes, vols)
    df_4h = _make_uniform_4h(price=100.0)
    df_1h = _make_df([100]*30, [101]*30, [99]*30, [100]*30, freq="1h")

    t = df_1d.index[-1]
    signals = check_all_signals(df_1d, df_4h, df_1h, t, "BTCUSDT", "down")
    assert len(signals) == 1
    assert signals[0].sub_strategy == "breakout_short"


def test_check_all_signals_chop_regime_tries_both_mean_rev():
    """chop regime → 试 mean_rev_long + mean_rev_short, 都触发不了应 0 个."""
    df_1d = _make_df([100]*30, [101]*30, [99]*30, [100]*30)
    df_4h = _make_uniform_4h()
    df_1h = _make_df([100]*30, [101]*30, [99]*30, [100]*30, freq="1h")

    t = df_1h.index[-1]
    signals = check_all_signals(df_1d, df_4h, df_1h, t, "ETHUSDT", "chop")
    # 横盘中 RSI = 50, 不极端, 不触发任何
    assert len(signals) == 0


def test_check_all_signals_no_regime_returns_empty():
    df_1d = _make_df([100]*30, [101]*30, [99]*30, [100]*30)
    df_4h = _make_uniform_4h()
    df_1h = _make_df([100]*30, [101]*30, [99]*30, [100]*30, freq="1h")
    t = df_1d.index[-1]
    assert check_all_signals(df_1d, df_4h, df_1h, t, "BTCUSDT", "unknown") == []


# ── 常量验证 ────────────────────────────────────────────────────

def test_atr_multipliers():
    assert SL_ATR_MULT == 2.0
    assert TP1_ATR_MULT == 2.0
    assert TP2_ATR_MULT == 4.0
    assert TP3_ATR_MULT == 6.0


def test_thresholds():
    assert BREAKOUT_DONCHIAN_PERIOD == 20
    assert BREAKOUT_VOLUME_MULT == 1.5
    assert MEANREV_RSI_OVERSOLD == 30.0
    assert MEANREV_RSI_OVERBOUGHT == 70.0
    assert MEANREV_WICK_BODY_RATIO == 2.0
