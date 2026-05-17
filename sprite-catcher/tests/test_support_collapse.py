"""
support_collapse.py 测试。

覆盖：
- 数据不足
- 峰值刚发生（不够时间确认）
- 没有 pump（横盘）
- pump 后支撑还在
- pump + 破位但量能不够
- 完整触发
- 边界：base_low / volume 中位数 ≤ 0
"""

import pytest

from sprite_catcher.features.support_collapse import detect_support_collapse


def _flat_candles(make_candles, n: int, price: float = 1.0, volume: float = 100.0):
    """工厂：n 根全等的 candle。"""
    return make_candles([(price, price, price, price, volume)] * n)


def test_sc_insufficient_data(make_candles):
    sig = detect_support_collapse(make_candles([(1, 1, 1, 1, 100)] * 5))
    assert sig.detected is False
    assert sig.reason == "insufficient_data"


def test_sc_no_pump_returns_pump_too_small(make_candles):
    """横盘市场不会触发。"""
    candles = _flat_candles(make_candles, 30, price=1.0, volume=100.0)
    sig = detect_support_collapse(candles)
    assert sig.detected is False
    # 因为 base_low == peak.high，pump_pct = 0
    assert "pump_too_small" in sig.reason


def test_sc_peak_too_recent(make_candles):
    """peak 就在最后一根 → 距离 = 0，距离不够。"""
    ohlcv: list[tuple[float, float, float, float, float]] = []
    # 前 23 根低位横盘
    for _ in range(23):
        ohlcv.append((1.0, 1.0, 1.0, 1.0, 100.0))
    # 第 24 根是 peak
    ohlcv.append((1.0, 5.0, 1.0, 5.0, 500.0))
    candles = make_candles(ohlcv)
    sig = detect_support_collapse(candles)
    assert sig.detected is False
    assert "peak_too_recent" in sig.reason


def test_sc_support_holds(make_candles):
    """有 pump 也有回撤，但当前 close 仍高于派发区低点 → 不触发。"""
    ohlcv: list[tuple[float, float, float, float, float]] = []
    # 0-9: 低位 1.0
    for _ in range(10):
        ohlcv.append((1.0, 1.0, 1.0, 1.0, 100.0))
    # 10: pump 到 2.0
    ohlcv.append((1.0, 2.0, 1.0, 2.0, 800.0))
    # 11-23: 在 1.5-1.9 之间震荡（不破 1.5）
    for i in range(13):
        ohlcv.append((1.7, 1.9, 1.5, 1.7, 200.0))
    # 当前: close 1.6 ≥ support 1.5
    ohlcv.append((1.7, 1.8, 1.6, 1.6, 500.0))
    candles = make_candles(ohlcv)
    sig = detect_support_collapse(candles)
    assert sig.detected is False
    assert "support_holds" in sig.reason


def test_sc_volume_too_low(make_candles):
    """破位发生但量能没放大。"""
    ohlcv: list[tuple[float, float, float, float, float]] = []
    for _ in range(10):
        ohlcv.append((1.0, 1.0, 1.0, 1.0, 100.0))
    ohlcv.append((1.0, 2.0, 1.0, 2.0, 800.0))  # peak
    for _ in range(12):
        ohlcv.append((1.7, 1.9, 1.5, 1.7, 200.0))
    # 当前破位但量能只有 150（低于 1.5x 中位）
    ohlcv.append((1.6, 1.6, 1.3, 1.3, 150.0))
    candles = make_candles(ohlcv)
    sig = detect_support_collapse(candles)
    assert sig.detected is False
    assert "volume_too_low" in sig.reason


def test_sc_full_trigger(make_candles):
    """完整触发：pump + 破位 + 放量。"""
    ohlcv: list[tuple[float, float, float, float, float]] = []
    # 0-9: 低位 base 1.0
    for _ in range(10):
        ohlcv.append((1.0, 1.0, 1.0, 1.0, 100.0))
    # 10: pump 到 2.0（涨幅 100%）
    ohlcv.append((1.0, 2.0, 1.0, 2.0, 1500.0))
    # 11-22: 派发区在 1.5-1.9 震荡
    for _ in range(12):
        ohlcv.append((1.7, 1.9, 1.5, 1.7, 200.0))
    # 23 (current): 破位 + 放量
    ohlcv.append((1.6, 1.6, 1.2, 1.2, 600.0))  # close 1.2 < support 1.5
    candles = make_candles(ohlcv)
    sig = detect_support_collapse(candles)
    assert sig.detected is True
    assert sig.reason == "SUPPORT_COLLAPSE"
    assert sig.pump_pct == pytest.approx(1.0)  # 1.0 → 2.0 = +100%
    assert sig.bars_since_peak == 13
    assert sig.support_level == pytest.approx(1.5)
    assert sig.volume_ratio > 1.5
    assert 0.0 < sig.strength <= 1.0


def test_sc_invalid_base_low(make_candles):
    """base_low <= 0 → 拒绝（防除零）。

    需要构造让 `recent` 窗口（最后 pump_lookback_bars=24 根）的第一根 low=0，
    否则那根坏数据会被切掉无法触发这条防御。
    """
    ohlcv: list[tuple[float, float, float, float, float]] = []
    # 整个数组就是 24 根，recent[0] = ohlcv[0]
    ohlcv.append((0.5, 0.5, 0.0, 0.5, 100.0))  # 坏数据，low = 0
    for _ in range(10):
        ohlcv.append((1.0, 1.0, 1.0, 1.0, 100.0))
    ohlcv.append((1.0, 2.0, 1.0, 2.0, 500.0))  # peak (index 11)
    for _ in range(12):
        ohlcv.append((1.5, 1.5, 1.4, 1.4, 200.0))
    # 共 24 根
    candles = make_candles(ohlcv)
    sig = detect_support_collapse(candles)
    assert sig.detected is False
    assert sig.reason == "invalid_base_low"


def test_sc_zero_median_volume(make_candles):
    """中位量 = 0 → 不触发，防除零。"""
    ohlcv: list[tuple[float, float, float, float, float]] = []
    for _ in range(10):
        ohlcv.append((1.0, 1.0, 1.0, 1.0, 0.0))    # 量全是 0
    ohlcv.append((1.0, 2.0, 1.0, 2.0, 0.0))         # peak
    for _ in range(12):
        ohlcv.append((1.7, 1.9, 1.5, 1.7, 0.0))
    ohlcv.append((1.6, 1.6, 1.2, 1.2, 0.0))          # current
    candles = make_candles(ohlcv)
    sig = detect_support_collapse(candles)
    assert sig.detected is False
    assert sig.reason == "zero_median_volume"


def test_sc_pump_too_small(make_candles):
    """涨幅 < 40% 不触发。"""
    ohlcv: list[tuple[float, float, float, float, float]] = []
    for _ in range(10):
        ohlcv.append((1.0, 1.0, 1.0, 1.0, 100.0))
    ohlcv.append((1.0, 1.20, 1.0, 1.20, 500.0))   # 只涨 20%
    for _ in range(12):
        ohlcv.append((1.10, 1.15, 1.05, 1.10, 200.0))
    ohlcv.append((1.05, 1.05, 0.95, 0.95, 600.0))
    candles = make_candles(ohlcv)
    sig = detect_support_collapse(candles)
    assert sig.detected is False
    assert "pump_too_small" in sig.reason
