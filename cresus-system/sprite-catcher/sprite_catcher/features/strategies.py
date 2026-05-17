"""
L5 策略层：把池决策 + 安全报告 + 信号 + 市场状态 组合成 TradeIntent。

每个策略 = 纯函数 (inputs) → TradeIntent | None：
- 不做 I/O，不维护状态
- 返回 None 表示"该策略不应在此时入场"（被某条规则拒）
- 没有"为什么不入场"的明确字段——调用方应该串联：
    if pool != FRIENDLY: skip
    if not safety.passed: skip
    if not signal.detected: skip
  这里只在所有前提都满足时再判断 strategy-specific 的可入场性。

策略层只决策"开新仓"。"何时平仓"由 L6 执行层根据 intent.stop_loss_price /
take_profit_price / max_holding_seconds 处理。
"""

from .. import config
from ..models import (
    DivergenceSignal,
    EntryType,
    Pool,
    SafetyReport,
    Side,
    ShortVacuumSignal,
    SupportCollapseSignal,
    TradeIntent,
    TrendFollowSignal,
)
from .sizing import size_long_position, size_short_position


# === 公共前提检查 ===


def _gate_pool_and_safety(
    pool: Pool, safety: SafetyReport, expected: Pool
) -> bool:
    """前置过滤：池必须匹配，安全闸必须通过。"""
    return pool == expected and safety.passed


# === Module A: 趋势跟随 ===


def plan_trend_follow(
    *,
    symbol: str,
    pool: Pool,
    safety: SafetyReport,
    signal: TrendFollowSignal,
    current_price: float,
    equity_usd: float,
    open_position_value_usd: float = 0.0,
) -> TradeIntent | None:
    """
    Module A 主力多头入场。

    SL = max(EMA50 × 0.97, entry × (1 - max_loss_pct))
       - 一般情况让 SL 跟着慢线
       - 但如果 EMA50 离 entry 太近，会被 max_loss_pct 兜底防止过紧
    TP = None（用 trailing stop，由 L6 维护）
    """
    if not _gate_pool_and_safety(pool, safety, Pool.FRIENDLY):
        return None
    if not signal.detected:
        return None

    sl_ema = signal.ema50 * config.A_TRENDFOLLOW_SL_EMA_DISCOUNT
    sl_max_loss = current_price * (1.0 - config.A_TRENDFOLLOW_SL_MAX_LOSS_PCT)
    stop_loss = max(sl_ema, sl_max_loss)

    if stop_loss >= current_price:
        # 极端情况：EMA50 ≥ price（理论上 signal.detected 已经排除，但保底）
        return None

    sizing = size_long_position(
        equity_usd=equity_usd,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        open_position_value_usd=open_position_value_usd,
    )
    if sizing.qty_quote_usd <= 0:
        return None

    return TradeIntent(
        strategy_id="trend_follow",
        symbol=symbol,
        side=Side.BUY,
        pool=pool,
        entry_type=EntryType.MARKET,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        take_profit_price=None,
        sizing=sizing,
        signal_strength=signal.strength,
        max_holding_seconds=config.A_TRENDFOLLOW_MAX_HOLDING_DAYS * 86400,
        reasons=(
            signal.reason,
            f"ema20_up_bars={signal.ema20_up_bars}",
            f"holders_growth={signal.holders_growth_pct:.2f}",
        ),
    )


# === Module B: 支撑崩塌 ===


def plan_support_collapse(
    *,
    symbol: str,
    pool: Pool,
    safety: SafetyReport,
    signal: SupportCollapseSignal,
    current_price: float,
    peak_price: float,
    base_low: float,
    equity_usd: float,
    open_position_value_usd: float = 0.0,
) -> TradeIntent | None:
    """
    Module B 慢但确定的空头入场。

    SL = peak × 1.02（让价格回到峰值上方才止损）
    TP = base_low × 0.85（再砸 15% 跌破起点）

    需要调用方提供 peak_price 和 base_low（从 candles 提取）。
    """
    if not _gate_pool_and_safety(pool, safety, Pool.OPERATOR):
        return None
    if not signal.detected:
        return None
    if peak_price <= 0 or base_low <= 0:
        return None

    stop_loss = peak_price * (1.0 + config.B_SUPPORT_COLLAPSE_SL_PEAK_PREMIUM)
    if stop_loss <= current_price:
        # peak 已经低于 current → 信号不合理
        return None

    take_profit = base_low * config.B_SUPPORT_COLLAPSE_TP_TO_BASE_DISCOUNT
    if take_profit >= current_price:
        # TP 比当前价还高 → 空头没有利润空间
        return None

    sizing = size_short_position(
        equity_usd=equity_usd,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        open_position_value_usd=open_position_value_usd,
    )
    if sizing.qty_quote_usd <= 0:
        return None

    return TradeIntent(
        strategy_id="support_collapse",
        symbol=symbol,
        side=Side.SELL,
        pool=pool,
        entry_type=EntryType.MARKET,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        sizing=sizing,
        signal_strength=signal.strength,
        max_holding_seconds=config.B_SUPPORT_COLLAPSE_MAX_HOLDING_HOURS * 3600,
        reasons=(
            signal.reason,
            f"pump_pct={signal.pump_pct:.2f}",
            f"volume_ratio={signal.volume_ratio:.2f}",
        ),
    )


# === Module B: 空头真空 ===


def plan_short_vacuum(
    *,
    symbol: str,
    pool: Pool,
    safety: SafetyReport,
    signal: ShortVacuumSignal,
    current_price: float,
    spike_high: float,
    equity_usd: float,
    open_position_value_usd: float = 0.0,
) -> TradeIntent | None:
    """
    Module B 快速空头入场（V7 风格：插针后真空）。

    SL = spike_high × 1.015（很紧，错就快速止损）
    TP = entry × (1 - 3%)（快进快出）

    需要调用方提供 spike_high（窗口内的最高价）。
    """
    if not _gate_pool_and_safety(pool, safety, Pool.OPERATOR):
        return None
    if not signal.detected:
        return None
    if spike_high <= 0:
        return None

    stop_loss = spike_high * (1.0 + config.B_SHORT_VACUUM_SL_PREMIUM)
    if stop_loss <= current_price:
        return None

    take_profit = current_price * (1.0 - config.B_SHORT_VACUUM_TP_PCT)
    if take_profit <= 0:
        return None

    sizing = size_short_position(
        equity_usd=equity_usd,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        open_position_value_usd=open_position_value_usd,
    )
    if sizing.qty_quote_usd <= 0:
        return None

    return TradeIntent(
        strategy_id="short_vacuum",
        symbol=symbol,
        side=Side.SELL,
        pool=pool,
        entry_type=EntryType.MARKET,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        sizing=sizing,
        signal_strength=signal.strength,
        max_holding_seconds=config.B_SHORT_VACUUM_MAX_HOLDING_HOURS * 3600,
        reasons=(
            signal.reason,
            f"oi_drop_pct={signal.oi_drop_pct:.2f}",
            f"wick_ratio={signal.max_wick_ratio:.3f}",
        ),
    )


# === Module B: 聪明钱撤退（基于价/OI/持有人三层背离）===


def plan_distribution(
    *,
    symbol: str,
    pool: Pool,
    safety: SafetyReport,
    signal: DivergenceSignal,
    current_price: float,
    recent_high: float,
    equity_usd: float,
    open_position_value_usd: float = 0.0,
) -> TradeIntent | None:
    """
    Module B 独立信号空头入场（V8 风格：聪明钱在悄悄出货）。

    SL = recent_high × 1.05（宽，承受更多杂波）
    TP = entry × (1 - 8%)（慢慢跌）

    与其它空头信号互补：发生在操纵周期外也可能触发。
    """
    if not _gate_pool_and_safety(pool, safety, Pool.OPERATOR):
        return None
    if not signal.detected:
        return None
    if recent_high <= 0:
        return None

    stop_loss = recent_high * (1.0 + config.B_DISTRIBUTION_SL_PREMIUM)
    if stop_loss <= current_price:
        return None

    take_profit = current_price * (1.0 - config.B_DISTRIBUTION_TP_PCT)
    if take_profit <= 0:
        return None

    sizing = size_short_position(
        equity_usd=equity_usd,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        open_position_value_usd=open_position_value_usd,
    )
    if sizing.qty_quote_usd <= 0:
        return None

    return TradeIntent(
        strategy_id="distribution",
        symbol=symbol,
        side=Side.SELL,
        pool=pool,
        entry_type=EntryType.MARKET,
        entry_price=current_price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        sizing=sizing,
        signal_strength=signal.strength,
        max_holding_seconds=config.B_DISTRIBUTION_MAX_HOLDING_HOURS * 3600,
        reasons=(
            signal.reason,
            f"price_slope={signal.price_slope:.4f}",
            f"oi_slope={signal.oi_slope:.4f}",
        ),
    )
