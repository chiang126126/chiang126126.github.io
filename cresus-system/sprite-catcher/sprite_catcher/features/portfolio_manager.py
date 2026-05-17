"""
L7 组合层：市场态势分级 + 仓位准入。

两个公开函数：

1. assess_market_regime(btc_1d, total_mc_history) → RegimeAssessment
   - 用 BTC 1D 价 vs EMA50 + 总市值 7d 动量，给出 BULL / RANGE / BEAR
   - 返回该 regime 下的 AllocationCaps（Module A/B/total 三个上限）

2. can_admit_intent(intent, caps, current state) → AdmissionDecision
   - 检查这笔新单加进去会不会突破 Module A/B 或 total 任一上限
   - 通过 → admitted=True，否则给出明确的拒绝原因

两个都是纯函数；regime 转换没有滞后或滤波（v0 简化），调用方负责采样频率。
"""

from .. import config
from ..models import (
    AdmissionDecision,
    AllocationCaps,
    Candle,
    MarketRegime,
    Pool,
    RegimeAssessment,
    TimeSeriesPoint,
    TradeIntent,
)
from .indicators import ema


def _caps_for(regime: MarketRegime) -> AllocationCaps:
    """单点映射：regime → 仓位上限。"""
    if regime is MarketRegime.BULL:
        return AllocationCaps(
            module_a_max_pct=config.REGIME_BULL_MODULE_A_PCT,
            module_b_max_pct=config.REGIME_BULL_MODULE_B_PCT,
            total_max_pct=config.REGIME_BULL_TOTAL_PCT,
            new_positions_allowed=True,
        )
    if regime is MarketRegime.BEAR:
        return AllocationCaps(
            module_a_max_pct=config.REGIME_BEAR_MODULE_A_PCT,
            module_b_max_pct=config.REGIME_BEAR_MODULE_B_PCT,
            total_max_pct=config.REGIME_BEAR_TOTAL_PCT,
            new_positions_allowed=True,
        )
    # RANGE (默认)
    return AllocationCaps(
        module_a_max_pct=config.REGIME_RANGE_MODULE_A_PCT,
        module_b_max_pct=config.REGIME_RANGE_MODULE_B_PCT,
        total_max_pct=config.REGIME_RANGE_TOTAL_PCT,
        new_positions_allowed=True,
    )


def _mc_momentum(history: list[TimeSeriesPoint], lookback_days: int) -> float:
    """7d 总市值变化百分比。history 按时间升序，最后一个点是当前。"""
    if len(history) < 2:
        return 0.0
    # 取最早可比较的点（≥ lookback_days 前）作为基线
    # 简化：直接拿首尾两个点；调用方应保证 history 覆盖 lookback_days
    base = history[0].value
    now = history[-1].value
    if base <= 0:
        return 0.0
    return (now - base) / base


def assess_market_regime(
    btc_1d_candles: list[Candle],
    total_mc_history: list[TimeSeriesPoint],
    *,
    btc_ema_period: int = config.REGIME_BTC_EMA_PERIOD,
    bull_mc_momentum: float = config.REGIME_BULL_MC_MOMENTUM,
    bear_mc_momentum: float = config.REGIME_BEAR_MC_MOMENTUM,
) -> RegimeAssessment:
    """
    用 BTC 1D 价格 vs EMA + 总市值动量 给出态势分级。

    规则（按优先级）：
    - BULL : BTC > EMA50  且  7d 总市值动量 ≥ +5%
    - BEAR : BTC < EMA50  且  7d 总市值动量 ≤ -10%
    - 其余 : RANGE

    数据不足时统一返回 RANGE（最保守）。
    """
    reasons: list[str] = []

    if len(btc_1d_candles) < btc_ema_period:
        return RegimeAssessment(
            regime=MarketRegime.RANGE,
            caps=_caps_for(MarketRegime.RANGE),
            btc_above_ema=False,
            total_mc_momentum_7d=0.0,
            reasons=("insufficient_btc_data",),
        )

    closes = [c.close for c in btc_1d_candles]
    ema_series = ema(closes, btc_ema_period)
    if not ema_series:
        return RegimeAssessment(
            regime=MarketRegime.RANGE,
            caps=_caps_for(MarketRegime.RANGE),
            btc_above_ema=False,
            total_mc_momentum_7d=0.0,
            reasons=("ema_empty",),
        )

    btc_now = closes[-1]
    ema_now = ema_series[-1]
    btc_above_ema = btc_now > ema_now
    reasons.append(
        f"btc_{'above' if btc_above_ema else 'below'}_ema:"
        f"close={btc_now:.0f},ema={ema_now:.0f}"
    )

    mc_momentum = _mc_momentum(total_mc_history, lookback_days=7)
    reasons.append(f"mc_7d_momentum={mc_momentum:+.3f}")

    if btc_above_ema and mc_momentum >= bull_mc_momentum:
        regime = MarketRegime.BULL
    elif (not btc_above_ema) and mc_momentum <= bear_mc_momentum:
        regime = MarketRegime.BEAR
    else:
        regime = MarketRegime.RANGE

    return RegimeAssessment(
        regime=regime,
        caps=_caps_for(regime),
        btc_above_ema=btc_above_ema,
        total_mc_momentum_7d=mc_momentum,
        reasons=tuple(reasons),
    )


def can_admit_intent(
    intent: TradeIntent,
    caps: AllocationCaps,
    *,
    equity_usd: float,
    module_a_value_usd: float,
    module_b_value_usd: float,
) -> AdmissionDecision:
    """
    判断这笔新 intent 加进去会不会突破任一上限。

    检查顺序：
    1. caps.new_positions_allowed 必须为 True（系统级熔断时可关）
    2. intent 所属的 Module 仓位上限
    3. 总仓位上限

    Module 归属：FRIENDLY → A，OPERATOR → B；其它 pool 拒。
    """
    if not caps.new_positions_allowed:
        return AdmissionDecision(
            admitted=False, reason="new_positions_disabled"
        )

    if equity_usd <= 0:
        return AdmissionDecision(
            admitted=False, reason=f"equity_non_positive:{equity_usd}"
        )

    add_usd = intent.sizing.qty_quote_usd
    if add_usd <= 0:
        return AdmissionDecision(
            admitted=False, reason=f"intent_qty_non_positive:{add_usd}"
        )

    if intent.pool is Pool.FRIENDLY:
        module_value = module_a_value_usd
        module_cap = equity_usd * caps.module_a_max_pct
        module_name = "A"
    elif intent.pool is Pool.OPERATOR:
        module_value = module_b_value_usd
        module_cap = equity_usd * caps.module_b_max_pct
        module_name = "B"
    else:
        return AdmissionDecision(
            admitted=False,
            reason=f"intent_pool_not_tradeable:{intent.pool.value}",
        )

    new_module_value = module_value + add_usd
    if new_module_value > module_cap:
        return AdmissionDecision(
            admitted=False,
            reason=(
                f"module_{module_name}_cap_exceeded:"
                f"{new_module_value:.0f}>{module_cap:.0f}"
            ),
        )

    new_total = module_a_value_usd + module_b_value_usd + add_usd
    total_cap = equity_usd * caps.total_max_pct
    if new_total > total_cap:
        return AdmissionDecision(
            admitted=False,
            reason=f"total_cap_exceeded:{new_total:.0f}>{total_cap:.0f}",
        )

    return AdmissionDecision(admitted=True, reason=None)
