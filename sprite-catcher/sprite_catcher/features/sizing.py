"""
仓位计算。

基于固定风险（Fixed Fractional）模型：
  risk_usd = equity × risk_per_trade_pct
  risk_per_unit = |entry - sl| / entry
  qty_quote = risk_usd / risk_per_unit

然后应用 3 道上限：
  1. 单仓上限   = equity × max_single_position_pct × max_leverage
  2. 总仓上限   = equity × max_portfolio_pct − 已开仓总市值
  3. 杠杆上限   = equity × max_leverage

最终 qty = min(以上 4 个候选)。

设计原则：纯函数，没有副作用，所有上限来自参数。
"""

from .. import config
from ..models import PositionSize


def size_position(
    *,
    equity_usd: float,
    entry_price: float,
    stop_loss_price: float,
    risk_per_trade_pct: float,
    max_single_position_pct: float,
    max_portfolio_pct: float,
    open_position_value_usd: float = 0.0,
    max_leverage: float = 1.0,
) -> PositionSize:
    """
    计算单笔仓位的 USD 名义价值。

    边界：
    - equity / entry / sl ≤ 0 → ValueError
    - entry == sl → ValueError（无法计算风险密度）
    - max_leverage < 1.0 → ValueError
    """
    if equity_usd <= 0:
        raise ValueError(f"equity_usd must be positive, got {equity_usd}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be positive, got {entry_price}")
    if stop_loss_price <= 0:
        raise ValueError(f"stop_loss_price must be positive, got {stop_loss_price}")
    if entry_price == stop_loss_price:
        raise ValueError("entry_price and stop_loss_price cannot be equal")
    if max_leverage < 1.0:
        raise ValueError(
            f"max_leverage must be >= 1.0, got {max_leverage}"
        )

    risk_usd = equity_usd * risk_per_trade_pct
    risk_per_unit = abs(entry_price - stop_loss_price) / entry_price
    risk_based_qty = risk_usd / risk_per_unit

    single_cap = equity_usd * max_single_position_pct * max_leverage
    portfolio_remaining = max(
        0.0, equity_usd * max_portfolio_pct - open_position_value_usd
    )
    leverage_cap = equity_usd * max_leverage

    candidates: list[tuple[float, str]] = [
        (risk_based_qty, "risk_based"),
        (single_cap, "single_position_cap"),
        (portfolio_remaining, "portfolio_cap"),
        (leverage_cap, "leverage_cap"),
    ]
    final_qty, winner = min(candidates, key=lambda x: x[0])
    capped_by = None if winner == "risk_based" else winner

    # 实际预计亏损 = qty × risk_per_unit （注意：被卡仓时小于 risk_usd 上限）
    actual_risk_usd = final_qty * risk_per_unit

    leverage_used = final_qty / equity_usd if equity_usd > 0 else 0.0
    # 杠杆字段含义："notional / equity"，1.0 表示无杠杆
    # 现货策略 max_leverage=1.0 时，final_qty 永远 ≤ equity → leverage_used ≤ 1
    leverage_used = min(leverage_used, max_leverage)
    leverage_used = max(leverage_used, 0.0)

    reason = (
        f"risk_usd={risk_usd:.2f},"
        f"risk_per_unit={risk_per_unit:.4f},"
        f"risk_based_qty={risk_based_qty:.2f},"
        f"single_cap={single_cap:.2f},"
        f"portfolio_cap_remaining={portfolio_remaining:.2f},"
        f"leverage_cap={leverage_cap:.2f}"
    )

    return PositionSize(
        qty_quote_usd=final_qty,
        leverage=leverage_used,
        risk_usd=actual_risk_usd,
        capped_by=capped_by,
        reason=reason,
    )


def size_long_position(
    *,
    equity_usd: float,
    entry_price: float,
    stop_loss_price: float,
    open_position_value_usd: float = 0.0,
) -> PositionSize:
    """Module A 多头仓位计算（用 config 中的 A_* 默认值）。"""
    return size_position(
        equity_usd=equity_usd,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        risk_per_trade_pct=config.A_RISK_PER_TRADE_PCT,
        max_single_position_pct=config.A_MAX_SINGLE_POSITION_PCT,
        max_portfolio_pct=config.A_MAX_PORTFOLIO_PCT,
        open_position_value_usd=open_position_value_usd,
        max_leverage=config.A_MAX_LEVERAGE,
    )


def size_short_position(
    *,
    equity_usd: float,
    entry_price: float,
    stop_loss_price: float,
    open_position_value_usd: float = 0.0,
) -> PositionSize:
    """Module B 空头仓位计算（用 config 中的 B_* 默认值，允许 ≤ 2x 杠杆）。"""
    return size_position(
        equity_usd=equity_usd,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        risk_per_trade_pct=config.B_RISK_PER_TRADE_PCT,
        max_single_position_pct=config.B_MAX_SINGLE_POSITION_PCT,
        max_portfolio_pct=config.B_MAX_PORTFOLIO_PCT,
        open_position_value_usd=open_position_value_usd,
        max_leverage=config.B_MAX_LEVERAGE,
    )
