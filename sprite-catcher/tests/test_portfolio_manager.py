"""
portfolio_manager.py 测试。

覆盖：
- assess_market_regime：BULL / RANGE / BEAR 三种判定路径 + 数据不足降级
- can_admit_intent：模块上限 / 总仓上限 / 池不可交易 / 新仓被禁
"""

from datetime import datetime, timedelta

import pytest

from sprite_catcher.features.portfolio_manager import (
    _mc_momentum,
    assess_market_regime,
    can_admit_intent,
)
from sprite_catcher.models import TimeSeriesPoint
from sprite_catcher.models import (
    AllocationCaps,
    EntryType,
    MarketRegime,
    Pool,
    PositionSize,
    Side,
    TradeIntent,
)


# === assess_market_regime ===


def _mc_series_spanning_days(days: int, start_value: float, end_value: float):
    """生成跨 N 天的总市值序列（首尾两点，确保跨度 ≥ 7d）。"""
    base_ts = datetime(2025, 1, 1)
    return [
        TimeSeriesPoint(ts=base_ts, value=start_value),
        TimeSeriesPoint(ts=base_ts + timedelta(days=days), value=end_value),
    ]


def _btc_candles(make_candles, n: int, base_price: float, slope: float):
    """生成 n 根日线，价格按 base + i * slope 递增/递减。"""
    ohlcv = []
    for i in range(n):
        p = base_price + i * slope
        ohlcv.append((p, p + 1, p - 1, p, 1000.0))
    return make_candles(ohlcv)


def test_regime_bull(make_candles):
    """BTC 持续上涨 + 总市值跨 7d +20% → BULL。"""
    btc = _btc_candles(make_candles, n=80, base_price=50_000, slope=200)
    mc = _mc_series_spanning_days(8, 1.0e12, 1.2e12)  # 跨 8 天 +20%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.BULL
    assert r.btc_above_ema is True
    assert r.total_mc_momentum_7d == pytest.approx(0.20)
    assert r.caps.module_a_max_pct == 0.70
    assert r.caps.module_b_max_pct == 0.10


def test_regime_bear(make_candles):
    btc = _btc_candles(make_candles, n=80, base_price=60_000, slope=-200)
    mc = _mc_series_spanning_days(8, 1.2e12, 0.96e12)  # 跨 8 天 -20%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.BEAR
    assert r.btc_above_ema is False
    assert r.caps.module_a_max_pct == 0.10
    assert r.caps.module_b_max_pct == 0.40


def test_regime_range_btc_up_but_mc_flat(make_candles):
    btc = _btc_candles(make_candles, n=80, base_price=50_000, slope=200)
    mc = _mc_series_spanning_days(8, 1.0e12, 1.02e12)  # +2%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE


def test_regime_range_btc_down_but_mc_not_crashing(make_candles):
    btc = _btc_candles(make_candles, n=80, base_price=60_000, slope=-200)
    mc = _mc_series_spanning_days(8, 1.0e12, 0.95e12)  # -5%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE


def test_regime_insufficient_btc_falls_back_to_range(make_candles):
    btc = _btc_candles(make_candles, n=20, base_price=50_000, slope=100)  # < 50
    mc = _mc_series_spanning_days(8, 1.0e12, 1.2e12)
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE
    assert "insufficient_btc_data" in r.reasons


def test_regime_mc_history_too_short_falls_back_to_range(make_candles):
    """关键回归：mc 数据跨度 < 7 天 → 永远不应判 BULL/BEAR。"""
    btc = _btc_candles(make_candles, n=80, base_price=50_000, slope=200)
    mc = _mc_series_spanning_days(3, 1.0e12, 1.5e12)  # 跨度仅 3 天，动量虽大但不可信
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE
    assert "mc_history_insufficient" in r.reasons


def test_regime_mc_single_point(make_candles):
    btc = _btc_candles(make_candles, n=80, base_price=50_000, slope=200)
    mc = [TimeSeriesPoint(ts=datetime(2025, 1, 1), value=1.0e12)]
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE


def test_mc_momentum_picks_earliest_point_before_cutoff():
    """3 点序列，跨 10 天：动量应该基于第一个点。"""
    base = datetime(2025, 1, 1)
    series = [
        TimeSeriesPoint(ts=base, value=100.0),                      # 10 天前
        TimeSeriesPoint(ts=base + timedelta(days=5), value=110.0),   # 5 天前（在 cutoff 之内）
        TimeSeriesPoint(ts=base + timedelta(days=10), value=120.0),  # 现在
    ]
    momentum, sufficient = _mc_momentum(series, lookback_days=7)
    assert sufficient is True
    # baseline 应该是第一个点（cutoff = 10d-7d=3d 前；只有 base 在 cutoff 之前）
    assert momentum == pytest.approx(0.20)  # 120/100 - 1


def test_mc_momentum_no_old_enough_point():
    """所有点都在 cutoff 之后 → 数据不足。"""
    base = datetime(2025, 1, 1)
    series = [
        TimeSeriesPoint(ts=base + timedelta(days=5), value=100.0),
        TimeSeriesPoint(ts=base + timedelta(days=8), value=120.0),
    ]
    momentum, sufficient = _mc_momentum(series, lookback_days=7)
    assert sufficient is False
    assert momentum == 0.0


def test_mc_momentum_baseline_zero():
    base = datetime(2025, 1, 1)
    series = [
        TimeSeriesPoint(ts=base, value=0.0),
        TimeSeriesPoint(ts=base + timedelta(days=8), value=100.0),
    ]
    momentum, sufficient = _mc_momentum(series, lookback_days=7)
    assert sufficient is False
    assert momentum == 0.0


# === can_admit_intent ===


def _intent_for(pool: Pool, qty_usd: float) -> TradeIntent:
    sizing = PositionSize(
        qty_quote_usd=qty_usd,
        leverage=1.0,
        risk_usd=qty_usd * 0.05,
        capped_by=None,
        reason="test",
    )
    return TradeIntent(
        strategy_id="test",
        symbol="X/USDT",
        side=Side.BUY if pool is Pool.FRIENDLY else Side.SELL,
        pool=pool,
        entry_type=EntryType.MARKET,
        entry_price=100.0,
        stop_loss_price=95.0 if pool is Pool.FRIENDLY else 105.0,
        take_profit_price=None,
        sizing=sizing,
        signal_strength=0.5,
        max_holding_seconds=86400,
        reasons=("test",),
    )


def _caps(a: float, b: float, total: float, allowed: bool = True):
    return AllocationCaps(
        module_a_max_pct=a,
        module_b_max_pct=b,
        total_max_pct=total,
        new_positions_allowed=allowed,
    )


def test_admit_baseline():
    intent = _intent_for(Pool.FRIENDLY, qty_usd=1_000.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=0.0,
    )
    assert d.admitted is True
    assert d.reason is None


def test_admit_rejects_when_module_a_cap_exceeded():
    """Module A 已占 4800，再开 300 会破 5000 上限。"""
    intent = _intent_for(Pool.FRIENDLY, qty_usd=300.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=4_800.0,
        module_b_value_usd=0.0,
    )
    assert d.admitted is False
    assert "module_A_cap_exceeded" in d.reason


def test_admit_rejects_when_module_b_cap_exceeded():
    intent = _intent_for(Pool.OPERATOR, qty_usd=200.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.30, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=2_900.0,  # cap 3000
    )
    assert d.admitted is False
    assert "module_B_cap_exceeded" in d.reason


def test_admit_rejects_when_total_cap_exceeded():
    """A 占 4000, B 占 2900，total cap 7000，再加 200 → 7100 破。"""
    intent = _intent_for(Pool.FRIENDLY, qty_usd=200.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),    # A_cap 5000, B_cap 3000, total_cap 7000
        equity_usd=10_000.0,
        module_a_value_usd=4_000.0,
        module_b_value_usd=2_900.0,
    )
    assert d.admitted is False
    assert "total_cap_exceeded" in d.reason


def test_admit_rejects_blacklist_pool():
    intent = _intent_for(Pool.BLACKLIST, qty_usd=100.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=0.0,
    )
    assert d.admitted is False
    assert "intent_pool_not_tradeable" in d.reason


def test_admit_rejects_neutral_pool():
    intent = _intent_for(Pool.NEUTRAL, qty_usd=100.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=0.0,
    )
    assert d.admitted is False


def test_admit_rejects_when_new_positions_disabled():
    """看门狗熔断时 caps.new_positions_allowed=False → 拒所有开仓。"""
    intent = _intent_for(Pool.FRIENDLY, qty_usd=100.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7, allowed=False),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=0.0,
    )
    assert d.admitted is False
    assert d.reason == "new_positions_disabled"


def test_admit_rejects_zero_equity():
    intent = _intent_for(Pool.FRIENDLY, qty_usd=100.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=0.0,
        module_a_value_usd=0.0,
        module_b_value_usd=0.0,
    )
    assert d.admitted is False
    assert "equity_non_positive" in d.reason


def test_admit_rejects_zero_qty():
    """intent 自己的 sizing 是 0（被 sizing 层卡到 0）→ 不应该到 L7 但要兜底。"""
    intent = _intent_for(Pool.FRIENDLY, qty_usd=0.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=0.0,
    )
    assert d.admitted is False
    assert "intent_qty_non_positive" in d.reason


def test_admit_rejects_when_per_symbol_cap_exceeded():
    """Module B 三套策略同时做空 BTC，单标的暴露上限把第二笔卡住。"""
    intent = _intent_for(Pool.OPERATOR, qty_usd=200.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=200.0,
        existing_exposure_by_symbol={"X/USDT": 200.0},  # 已有 $200
        max_per_symbol_pct=0.03,                         # cap = $300
    )
    assert d.admitted is False
    assert "per_symbol_cap_exceeded" in d.reason
    assert "X/USDT" in d.reason


def test_admit_allows_when_different_symbol():
    """不同标的不互相影响上限。"""
    intent = _intent_for(Pool.OPERATOR, qty_usd=200.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=500.0,
        existing_exposure_by_symbol={"OTHER/USDT": 500.0},  # 其它标的
        max_per_symbol_pct=0.03,
    )
    assert d.admitted is True


def test_admit_per_symbol_cap_default_disabled():
    """
    默认 max_per_symbol_pct=1.0（不限制）。
    生产代码必须显式传 0.03，否则该保护不生效。
    """
    intent = _intent_for(Pool.FRIENDLY, qty_usd=2_000.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=0.0,
        module_b_value_usd=0.0,
        existing_exposure_by_symbol={"X/USDT": 5_000.0},   # 已有 50%
        # 不传 max_per_symbol_pct → 默认 1.0 → 不卡
    )
    assert d.admitted is True


def test_admit_per_symbol_cap_explicit_3pct():
    """显式传 0.03：$10k equity → $300 单标的；新单 $250 + 已有 $100 → 破。"""
    intent = _intent_for(Pool.FRIENDLY, qty_usd=250.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=100.0,
        module_b_value_usd=0.0,
        existing_exposure_by_symbol={"X/USDT": 100.0},
        max_per_symbol_pct=0.03,
    )
    assert d.admitted is False
    assert "per_symbol_cap_exceeded" in d.reason


def test_admit_exact_boundary_is_allowed():
    """剩余预算刚好等于 intent 大小 → 允许（用 <= 而非 <）。"""
    intent = _intent_for(Pool.FRIENDLY, qty_usd=200.0)
    d = can_admit_intent(
        intent,
        _caps(0.5, 0.3, 0.7),
        equity_usd=10_000.0,
        module_a_value_usd=4_800.0,   # cap 5000, 剩 200
        module_b_value_usd=0.0,
    )
    assert d.admitted is True
