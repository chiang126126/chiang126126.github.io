"""
trend_follow.py 测试。

覆盖：
- 数据不足
- 不满足多头排列
- EMA20 未持续向上
- 1D 无突破
- 持有人增速不够（提供时）
- 持有人 None（不阻塞但 strength 打折）
- 完整触发
"""

import pytest

from sprite_catcher.features.trend_follow import detect_trend_follow


def _ascending_4h(make_candles, n: int):
    """生成 n 根递增 K 线，足以让 EMA20 < EMA50 < price 持续。"""
    ohlcv = []
    for i in range(n):
        v = 1.0 + i * 0.02   # 缓涨
        ohlcv.append((v, v + 0.01, v - 0.005, v + 0.005, 100.0))
    return make_candles(ohlcv)


def _ascending_1d_with_breakout(make_candles, breakout: bool):
    """生成 21 根日线；最后一根的 close 是否突破前 20 根的 high 视参数。"""
    ohlcv = []
    for i in range(20):
        # 前 20 根：high 在 2.0 附近
        ohlcv.append((1.95, 2.00, 1.90, 1.95, 100.0))
    last_close = 2.10 if breakout else 1.95
    ohlcv.append((1.95, max(last_close, 2.00), 1.95, last_close, 100.0))
    return make_candles(ohlcv)


def test_tf_insufficient_4h(make_candles):
    sig = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 30),  # < 50+3
        candles_1d=_ascending_1d_with_breakout(make_candles, True),
        holders_growth_7d_pct=0.5,
    )
    assert sig.detected is False
    assert sig.reason == "insufficient_4h_data"


def test_tf_insufficient_1d(make_candles):
    sig = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 60),
        candles_1d=_ascending_1d_with_breakout(make_candles, True)[:10],
        holders_growth_7d_pct=0.5,
    )
    assert sig.detected is False
    assert sig.reason == "insufficient_1d_data"


def test_tf_not_bullish_stack(make_candles):
    """递减的 4H 序列 → 价格 < EMA20，不满足多头排列。"""
    ohlcv = []
    for i in range(60):
        v = 2.0 - i * 0.02
        ohlcv.append((v, v + 0.01, v - 0.005, v - 0.005, 100.0))
    candles_4h = make_candles(ohlcv)
    sig = detect_trend_follow(
        candles_4h=candles_4h,
        candles_1d=_ascending_1d_with_breakout(make_candles, True),
        holders_growth_7d_pct=0.5,
    )
    assert sig.detected is False
    assert "not_bullish_stack" in sig.reason


def test_tf_no_daily_breakout(make_candles):
    sig = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 60),
        candles_1d=_ascending_1d_with_breakout(make_candles, False),  # 未突破
        holders_growth_7d_pct=0.5,
    )
    assert sig.detected is False
    assert "no_daily_breakout" in sig.reason


def test_tf_holders_growth_too_low(make_candles):
    sig = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 60),
        candles_1d=_ascending_1d_with_breakout(make_candles, True),
        holders_growth_7d_pct=0.10,   # < 30%
    )
    assert sig.detected is False
    assert "holders_growth_too_low" in sig.reason


def test_tf_holders_none_does_not_block(make_candles):
    """持有人不可用时不阻塞，但 strength 会被打折。"""
    sig_known = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 60),
        candles_1d=_ascending_1d_with_breakout(make_candles, True),
        holders_growth_7d_pct=0.50,
    )
    sig_unknown = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 60),
        candles_1d=_ascending_1d_with_breakout(make_candles, True),
        holders_growth_7d_pct=None,
    )
    assert sig_known.detected is True
    assert sig_unknown.detected is True
    assert sig_unknown.strength <= sig_known.strength
    assert sig_unknown.holders_growth_pct == 0.0  # placeholder


def test_tf_full_trigger(make_candles):
    sig = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 80),
        candles_1d=_ascending_1d_with_breakout(make_candles, True),
        holders_growth_7d_pct=0.50,
    )
    assert sig.detected is True
    assert sig.reason == "TREND_FOLLOW"
    assert sig.ema20 > sig.ema50
    assert sig.ema20_up_bars >= 3
    assert sig.daily_breakout is True
    assert sig.holders_growth_pct == pytest.approx(0.50)
    assert 0.0 < sig.strength <= 1.0


def test_tf_ema_relations_correct(make_candles):
    """完整触发时，price > ema20 > ema50。"""
    sig = detect_trend_follow(
        candles_4h=_ascending_4h(make_candles, 80),
        candles_1d=_ascending_1d_with_breakout(make_candles, True),
        holders_growth_7d_pct=0.50,
    )
    assert sig.detected is True
    assert sig.ema20 > sig.ema50
