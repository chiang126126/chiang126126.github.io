"""
indicators.py 测试。

ema + consecutive_up_bars 是被多个信号模块复用的纯数学工具。
"""

import math

import pytest

from sprite_catcher.features.indicators import (
    atr,
    consecutive_up_bars,
    ema,
    true_range,
)
from sprite_catcher.models import Candle
from datetime import datetime, timedelta


# === ema ===


def test_ema_invalid_period():
    with pytest.raises(ValueError):
        ema([1.0, 2.0, 3.0], 0)
    with pytest.raises(ValueError):
        ema([1.0, 2.0, 3.0], -1)


def test_ema_too_few_values():
    assert ema([1.0, 2.0], 5) == []


def test_ema_constant_series():
    """常量序列的 EMA 应保持常量。"""
    out = ema([5.0] * 10, period=3)
    assert len(out) == 8     # 10 - 3 + 1 = 8
    for v in out:
        assert v == pytest.approx(5.0)


def test_ema_seed_is_sma():
    """第一个 EMA 值 = 前 period 个值的 SMA。"""
    values = [2.0, 4.0, 6.0, 8.0, 10.0]
    out = ema(values, period=3)
    # SMA of [2,4,6] = 4
    assert out[0] == pytest.approx(4.0)


def test_ema_monotonic_for_monotonic_input():
    """递增输入的 EMA 也应严格递增。"""
    values = [float(i) for i in range(1, 21)]
    out = ema(values, period=5)
    for i in range(1, len(out)):
        assert out[i] > out[i - 1]


def test_ema_smoothing_factor():
    """验证递推公式 EMA[t] = α·V[t] + (1-α)·EMA[t-1]。"""
    values = [1.0, 1.0, 1.0, 10.0]  # period=3, 第 4 个值跳到 10
    out = ema(values, period=3)
    alpha = 2.0 / (3 + 1)            # 0.5
    expected_t1 = alpha * 10.0 + (1 - alpha) * 1.0
    assert out[1] == pytest.approx(expected_t1)


# === consecutive_up_bars ===


# === true_range / atr ===


def _c(open_, high, low, close, ts_offset=0):
    return Candle(
        ts=datetime(2025, 1, 1) + timedelta(minutes=ts_offset),
        open=open_, high=high, low=low, close=close, volume=1.0,
    )


def test_true_range_first_bar_no_prev():
    """第一根：TR = high - low。"""
    assert true_range(_c(10, 12, 9, 11), prev_close=None) == 3.0


def test_true_range_includes_prev_close_gap():
    """跳空向上：上影 + 上跳超过当根 H-L → 取最大。"""
    # bar: open 10, high 12, low 9, close 11
    # prev_close = 5 (大跳空)
    # candidates: 12-9=3, |12-5|=7, |9-5|=4 → max = 7
    assert true_range(_c(10, 12, 9, 11), prev_close=5) == 7.0


def test_true_range_invalid_high_low():
    """high < low（坏数据）→ 0。"""
    assert true_range(_c(10, 5, 9, 8), prev_close=8) == 0.0


def test_atr_invalid_period():
    with pytest.raises(ValueError):
        atr([], period=0)


def test_atr_insufficient_data():
    assert atr([_c(10, 11, 9, 10)] * 5, period=14) == []


def test_atr_constant_candles():
    """所有 K 线一模一样 → TR 全相等 → ATR 永远等于 TR。"""
    candles = [_c(10, 12, 8, 10, i) for i in range(20)]
    out = atr(candles, period=14)
    # 第一根 TR = 4 (12-8)，prev_close=10 后续 TR = max(4, |12-10|, |8-10|) = 4
    assert len(out) == 20 - 14 + 1   # 7
    for v in out:
        assert v == pytest.approx(4.0)


def test_atr_responds_to_volatility_spike():
    """突然一根大波动 → ATR 应该立即上行（虽然被 Wilder 平滑）。"""
    candles = [_c(10, 11, 9, 10, i) for i in range(15)]   # 安静
    candles.append(_c(10, 15, 5, 10, 15))                  # 大波动
    out = atr(candles, period=14)
    assert out[-1] > out[0]


def test_atr_length():
    """20 根 + period=14 → 输出 7 个 ATR 值。"""
    candles = [_c(10, 11, 9, 10, i) for i in range(20)]
    out = atr(candles, period=14)
    assert len(out) == 7


# === consecutive_up_bars ===


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], 0),
        ([5.0], 0),
        ([1.0, 2.0, 3.0, 4.0], 3),
        ([1.0, 2.0, 3.0, 3.0], 0),      # 末尾持平，第 0
        ([1.0, 2.0, 2.0, 3.0], 1),      # 最后涨，再往前没涨
        ([5.0, 4.0, 3.0, 2.0], 0),      # 单调下降
        ([1.0, 1.0, 1.0, 1.0], 0),      # 全等
        ([3.0, 1.0, 2.0, 3.0, 4.0], 3), # [1→2→3→4]
    ],
)
def test_consecutive_up_bars(values, expected):
    assert consecutive_up_bars(values) == expected
