"""
portfolio_manager.py 测试。

覆盖：
- assess_market_regime：BULL / RANGE / BEAR 三种判定路径 + 数据不足降级
- can_admit_intent：模块上限 / 总仓上限 / 池不可交易 / 新仓被禁
"""

import pytest

from sprite_catcher.features.portfolio_manager import (
    assess_market_regime,
    can_admit_intent,
)
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


def _btc_candles(make_candles, n: int, base_price: float, slope: float):
    """生成 n 根日线，价格按 base + i * slope 递增/递减。"""
    ohlcv = []
    for i in range(n):
        p = base_price + i * slope
        ohlcv.append((p, p + 1, p - 1, p, 1000.0))
    return make_candles(ohlcv)


def test_regime_bull(make_candles, make_series):
    """BTC 持续上涨 + 总市值 7d +20% → BULL。"""
    btc = _btc_candles(make_candles, n=80, base_price=50_000, slope=200)
    mc = make_series([1.0e12, 1.2e12])  # +20%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.BULL
    assert r.btc_above_ema is True
    assert r.total_mc_momentum_7d == pytest.approx(0.20)
    assert r.caps.module_a_max_pct == 0.70
    assert r.caps.module_b_max_pct == 0.10


def test_regime_bear(make_candles, make_series):
    """BTC 持续下跌 + 总市值 7d -20% → BEAR。"""
    btc = _btc_candles(make_candles, n=80, base_price=60_000, slope=-200)
    mc = make_series([1.2e12, 0.96e12])  # -20%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.BEAR
    assert r.btc_above_ema is False
    assert r.caps.module_a_max_pct == 0.10
    assert r.caps.module_b_max_pct == 0.40


def test_regime_range_btc_up_but_mc_flat(make_candles, make_series):
    """BTC > EMA 但总市值动量没到 +5% → RANGE。"""
    btc = _btc_candles(make_candles, n=80, base_price=50_000, slope=200)
    mc = make_series([1.0e12, 1.02e12])  # +2%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE


def test_regime_range_btc_down_but_mc_not_crashing(make_candles, make_series):
    """BTC < EMA 但总市值跌幅没到 -10% → RANGE。"""
    btc = _btc_candles(make_candles, n=80, base_price=60_000, slope=-200)
    mc = make_series([1.0e12, 0.95e12])  # -5%
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE


def test_regime_insufficient_data_falls_back_to_range(make_candles, make_series):
    btc = _btc_candles(make_candles, n=20, base_price=50_000, slope=100)  # < 50
    mc = make_series([1.0e12, 1.2e12])
    r = assess_market_regime(btc, mc)
    assert r.regime is MarketRegime.RANGE
    assert "insufficient_btc_data" in r.reasons


def test_regime_mc_history_too_short_treated_as_zero(make_candles, make_series):
    btc = _btc_candles(make_candles, n=80, base_price=50_000, slope=200)
    mc = make_series([1.0e12])  # 单点
    r = assess_market_regime(btc, mc)
    # mc_momentum = 0 → 不到 BULL 阈值 → RANGE
    assert r.regime is MarketRegime.RANGE


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
