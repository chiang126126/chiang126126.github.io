"""
indicators.py 测试。

ema + consecutive_up_bars 是被多个信号模块复用的纯数学工具。
"""

import math

import pytest

from sprite_catcher.features.indicators import consecutive_up_bars, ema


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
