"""
strategies.py 测试。

每个策略测试：
- 池不匹配 → None
- 安全闸未过 → None
- 信号未触发 → None
- 完整触发 → 返回正确的 TradeIntent
- SL/TP 合理性（多头 SL 在 entry 下，空头 SL 在 entry 上等）
- 异常边界（peak ≤ 0 等）
"""

import pytest

from sprite_catcher.features.strategies import (
    plan_distribution,
    plan_short_vacuum,
    plan_support_collapse,
    plan_trend_follow,
)
from sprite_catcher.models import (
    DivergenceSignal,
    EntryType,
    Pool,
    SafetyReport,
    Side,
    ShortVacuumSignal,
    SupportCollapseSignal,
    TrendFollowSignal,
)


# === 通用工厂 ===


def _ok_safety() -> SafetyReport:
    return SafetyReport(passed=True, rejected_reasons=(), warnings=())


def _bad_safety() -> SafetyReport:
    return SafetyReport(
        passed=False, rejected_reasons=("audit_mintable",), warnings=()
    )


def _tf_signal(detected: bool = True, ema20: float = 100.0, ema50: float = 95.0):
    return TrendFollowSignal(
        detected=detected,
        strength=0.6,
        reason="TREND_FOLLOW" if detected else "not_bullish_stack",
        ema20=ema20,
        ema50=ema50,
        ema20_up_bars=5,
        daily_breakout=True,
        holders_growth_pct=0.4,
    )


def _sc_signal(detected: bool = True):
    return SupportCollapseSignal(
        detected=detected,
        strength=0.7,
        reason="SUPPORT_COLLAPSE" if detected else "support_holds",
        pump_pct=1.5,
        bars_since_peak=8,
        support_level=1.5,
        volume_ratio=2.0,
    )


def _sv_signal(detected: bool = True):
    return ShortVacuumSignal(
        detected=detected,
        strength=0.8,
        reason="SHORT_VACUUM" if detected else "oi_drop_too_small",
        oi_drop_pct=0.30,
        max_wick_ratio=0.05,
        recent_pump_pct=0.40,
    )


def _div_signal(detected: bool = True):
    return DivergenceSignal(
        price_slope=0.1,
        oi_slope=-0.05,
        holders_change_pct=0.02,
        detected=detected,
        strength=0.6 if detected else 0.0,
        reason="DISTRIBUTION_LIKELY" if detected else "NO_DIVERGENCE",
    )


# === plan_trend_follow ===


def test_tf_strategy_baseline():
    intent = plan_trend_follow(
        symbol="X/USDT",
        pool=Pool.FRIENDLY,
        safety=_ok_safety(),
        signal=_tf_signal(),
        current_price=105.0,        # > ema20=100 > ema50=95
        equity_usd=10_000.0,
    )
    assert intent is not None
    assert intent.strategy_id == "trend_follow"
    assert intent.side is Side.BUY
    assert intent.entry_type is EntryType.MARKET
    assert intent.stop_loss_price < intent.entry_price
    assert intent.take_profit_price is None  # trailing stop
    assert intent.sizing.qty_quote_usd > 0


def test_tf_strategy_wrong_pool_returns_none():
    intent = plan_trend_follow(
        symbol="X/USDT",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_tf_signal(),
        current_price=105.0,
        equity_usd=10_000.0,
    )
    assert intent is None


def test_tf_strategy_safety_failed_returns_none():
    intent = plan_trend_follow(
        symbol="X/USDT",
        pool=Pool.FRIENDLY,
        safety=_bad_safety(),
        signal=_tf_signal(),
        current_price=105.0,
        equity_usd=10_000.0,
    )
    assert intent is None


def test_tf_strategy_signal_not_detected_returns_none():
    intent = plan_trend_follow(
        symbol="X/USDT",
        pool=Pool.FRIENDLY,
        safety=_ok_safety(),
        signal=_tf_signal(detected=False),
        current_price=105.0,
        equity_usd=10_000.0,
    )
    assert intent is None


def test_tf_strategy_sl_at_least_max_loss():
    """SL 不能低于 -8%（max_loss_pct 兜底）。"""
    # 如果 EMA50 离 entry 很远（比如 ema50=50, entry=105），SL_ema = 48.5
    # 而 entry × 0.92 = 96.6 → 取较大的 → SL = 96.6
    intent = plan_trend_follow(
        symbol="X/USDT",
        pool=Pool.FRIENDLY,
        safety=_ok_safety(),
        signal=_tf_signal(ema20=100.0, ema50=50.0),
        current_price=105.0,
        equity_usd=10_000.0,
    )
    assert intent is not None
    assert intent.stop_loss_price == pytest.approx(105.0 * 0.92)


# === plan_support_collapse ===


def test_sc_strategy_baseline():
    intent = plan_support_collapse(
        symbol="MYX/USDT",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_sc_signal(),
        current_price=1.30,         # 已破支撑 1.5
        peak_price=2.00,
        base_low=1.00,
        equity_usd=10_000.0,
    )
    assert intent is not None
    assert intent.strategy_id == "support_collapse"
    assert intent.side is Side.SELL
    assert intent.stop_loss_price == pytest.approx(2.00 * 1.02)
    assert intent.take_profit_price == pytest.approx(1.00 * 0.85)
    assert intent.stop_loss_price > intent.entry_price
    assert intent.take_profit_price < intent.entry_price
    assert intent.sizing.qty_quote_usd > 0


def test_sc_strategy_wrong_pool_returns_none():
    intent = plan_support_collapse(
        symbol="X",
        pool=Pool.FRIENDLY,            # 错池
        safety=_ok_safety(),
        signal=_sc_signal(),
        current_price=1.30,
        peak_price=2.00,
        base_low=1.00,
        equity_usd=10_000.0,
    )
    assert intent is None


def test_sc_strategy_signal_not_detected_returns_none():
    intent = plan_support_collapse(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_sc_signal(detected=False),
        current_price=1.30,
        peak_price=2.00,
        base_low=1.00,
        equity_usd=10_000.0,
    )
    assert intent is None


def test_sc_strategy_peak_below_current_returns_none():
    """peak ≤ current → SL 会落在 current 下方，不合理 → None。"""
    intent = plan_support_collapse(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_sc_signal(),
        current_price=2.50,            # 当前价比 peak 还高
        peak_price=2.00,
        base_low=1.00,
        equity_usd=10_000.0,
    )
    assert intent is None


def test_sc_strategy_tp_above_current_returns_none():
    """TP 比当前价还高 → 空头无利润空间 → None。"""
    intent = plan_support_collapse(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_sc_signal(),
        current_price=1.20,            # < base_low × 0.85 = 1.275
        peak_price=2.00,
        base_low=1.50,
        equity_usd=10_000.0,
    )
    # base × 0.85 = 1.275; current = 1.20 → TP 1.275 > entry → 不合理
    assert intent is None


def test_sc_strategy_zero_peak_returns_none():
    intent = plan_support_collapse(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_sc_signal(),
        current_price=1.30,
        peak_price=0.0,
        base_low=1.00,
        equity_usd=10_000.0,
    )
    assert intent is None


# === plan_short_vacuum ===


def test_sv_strategy_baseline():
    intent = plan_short_vacuum(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_sv_signal(),
        current_price=1.00,
        spike_high=1.15,
        equity_usd=10_000.0,
    )
    assert intent is not None
    assert intent.strategy_id == "short_vacuum"
    assert intent.side is Side.SELL
    assert intent.stop_loss_price == pytest.approx(1.15 * 1.015)
    assert intent.take_profit_price == pytest.approx(1.00 * 0.97)
    assert intent.max_holding_seconds == 12 * 3600


def test_sv_strategy_spike_below_current_returns_none():
    intent = plan_short_vacuum(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_sv_signal(),
        current_price=1.20,            # > spike_high
        spike_high=1.00,
        equity_usd=10_000.0,
    )
    assert intent is None


def test_sv_strategy_holding_time_is_shortest():
    """空头真空持仓最短（12h），符合"快进快出"设计。"""
    sv = plan_short_vacuum(
        symbol="X", pool=Pool.OPERATOR, safety=_ok_safety(),
        signal=_sv_signal(), current_price=1.0, spike_high=1.1,
        equity_usd=10_000.0,
    )
    sc = plan_support_collapse(
        symbol="X", pool=Pool.OPERATOR, safety=_ok_safety(),
        signal=_sc_signal(), current_price=1.3, peak_price=2.0,
        base_low=1.0, equity_usd=10_000.0,
    )
    dist = plan_distribution(
        symbol="X", pool=Pool.OPERATOR, safety=_ok_safety(),
        signal=_div_signal(), current_price=1.0, recent_high=1.1,
        equity_usd=10_000.0,
    )
    assert sv.max_holding_seconds < sc.max_holding_seconds
    assert sv.max_holding_seconds < dist.max_holding_seconds


# === plan_distribution ===


def test_dist_strategy_baseline():
    intent = plan_distribution(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_div_signal(),
        current_price=1.00,
        recent_high=1.10,
        equity_usd=10_000.0,
    )
    assert intent is not None
    assert intent.strategy_id == "distribution"
    assert intent.side is Side.SELL
    assert intent.stop_loss_price == pytest.approx(1.10 * 1.05)
    assert intent.take_profit_price == pytest.approx(1.00 * 0.92)


def test_dist_strategy_has_widest_sl():
    """聪明钱撤退 SL 最宽（5% vs vacuum 1.5%），承受更多波动。"""
    sv = plan_short_vacuum(
        symbol="X", pool=Pool.OPERATOR, safety=_ok_safety(),
        signal=_sv_signal(), current_price=1.0, spike_high=1.1,
        equity_usd=10_000.0,
    )
    dist = plan_distribution(
        symbol="X", pool=Pool.OPERATOR, safety=_ok_safety(),
        signal=_div_signal(), current_price=1.0, recent_high=1.1,
        equity_usd=10_000.0,
    )
    # 两者都用 recent high ~1.1 + premium，但 distribution 的 premium 更大
    assert dist.stop_loss_price > sv.stop_loss_price


def test_dist_strategy_recent_high_below_current_returns_none():
    intent = plan_distribution(
        symbol="X",
        pool=Pool.OPERATOR,
        safety=_ok_safety(),
        signal=_div_signal(),
        current_price=1.20,
        recent_high=1.00,
        equity_usd=10_000.0,
    )
    assert intent is None


# === 跨策略一致性 ===


def test_all_strategies_reject_blacklist_pool():
    for plan_fn, kwargs in [
        (
            plan_trend_follow,
            dict(signal=_tf_signal(), current_price=105.0, equity_usd=10_000.0),
        ),
        (
            plan_support_collapse,
            dict(
                signal=_sc_signal(), current_price=1.30,
                peak_price=2.0, base_low=1.0, equity_usd=10_000.0,
            ),
        ),
        (
            plan_short_vacuum,
            dict(
                signal=_sv_signal(), current_price=1.0,
                spike_high=1.1, equity_usd=10_000.0,
            ),
        ),
        (
            plan_distribution,
            dict(
                signal=_div_signal(), current_price=1.0,
                recent_high=1.1, equity_usd=10_000.0,
            ),
        ),
    ]:
        intent = plan_fn(
            symbol="X",
            pool=Pool.BLACKLIST,
            safety=_ok_safety(),
            **kwargs,
        )
        assert intent is None


def test_all_strategies_pass_safety_warnings_through():
    """安全闸 passed=True 但有 warnings → 仍然能开仓。"""
    safety_with_warnings = SafetyReport(
        passed=True, rejected_reasons=(),
        warnings=("dev_first_time", "liquidity_thin"),
    )
    intent = plan_trend_follow(
        symbol="X", pool=Pool.FRIENDLY, safety=safety_with_warnings,
        signal=_tf_signal(), current_price=105.0, equity_usd=10_000.0,
    )
    assert intent is not None
