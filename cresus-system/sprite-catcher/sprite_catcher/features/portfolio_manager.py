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

from datetime import timedelta

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


def _mc_momentum(
    history: list[TimeSeriesPoint], lookback_days: int
) -> tuple[float, bool]:
    """
    返回 (动量百分比, 数据充足性)。

    动量定义：以"最早覆盖到 lookback_days 之前的点"为基线。
    如果没有任何点足够老（history 跨度 < lookback_days），返回 (0.0, False)
    并由调用方决定怎么处理（一般不应判 BULL/BEAR）。

    history 必须按 ts 升序。
    """
    if len(history) < 2:
        return 0.0, False

    now_point = history[-1]
    cutoff = now_point.ts - timedelta(days=lookback_days)

    # 找最早 ≤ cutoff 的点作为基线（即至少 lookback_days 之前）
    baseline: TimeSeriesPoint | None = None
    for p in history:
        if p.ts <= cutoff:
            baseline = p
        else:
            break

    if baseline is None:
        # history 全在 cutoff 之后，跨度不够
        return 0.0, False

    if baseline.value <= 0:
        return 0.0, False

    return (now_point.value - baseline.value) / baseline.value, True


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

    mc_momentum, mc_data_sufficient = _mc_momentum(
        total_mc_history, lookback_days=7
    )
    reasons.append(f"mc_7d_momentum={mc_momentum:+.3f}")
    if not mc_data_sufficient:
        reasons.append("mc_history_insufficient")

    # mc 数据不足时禁止判 BULL/BEAR（防止 momentum=0 错被理解成"中性"）
    if not mc_data_sufficient:
        regime = MarketRegime.RANGE
    elif btc_above_ema and mc_momentum >= bull_mc_momentum:
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
    existing_exposure_by_symbol: dict[str, float] | None = None,
    max_per_symbol_pct: float = 1.0,
) -> AdmissionDecision:
    """
    判断这笔新 intent 加进去会不会突破任一上限。

    检查顺序：
    1. caps.new_positions_allowed 必须为 True（系统级熔断时可关）
    2. 单标的累计暴露上限（max_per_symbol_pct × equity）
       防止 Module B 三套策略同时做空同一币种造成 3x 暴露
    3. intent 所属的 Module 仓位上限
    4. 总仓位上限

    ⚠️ max_per_symbol_pct 默认 1.0（不限制），仅为了向后兼容旧调用方。
    **生产代码必须传 0.03 (= 单标的累计暴露 ≤ 3% 总资金)**，
    否则 Module B 三套策略可能同时做空同一币种造成 3x 风险暴露。

    existing_exposure_by_symbol: symbol → 当前对该标的的 USD 累计名义暴露
       （多空头都算正数；这是"风险暴露"而非"净头寸"）。
       None 视为空 dict。

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

    # 单标的暴露上限 (防 Module B 多策略同时做空同一币)
    existing_exposure = (existing_exposure_by_symbol or {}).get(intent.symbol, 0.0)
    new_symbol_exposure = existing_exposure + add_usd
    symbol_cap = equity_usd * max_per_symbol_pct
    if new_symbol_exposure > symbol_cap:
        return AdmissionDecision(
            admitted=False,
            reason=(
                f"per_symbol_cap_exceeded:{intent.symbol}:"
                f"{new_symbol_exposure:.0f}>{symbol_cap:.0f}"
            ),
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
