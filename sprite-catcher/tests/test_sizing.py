"""
sizing.py 测试。

覆盖：
- 风险模型基本计算
- 各上限分别"被卡"
- capped_by 字段正确性
- 边界（除零 / 负值 / sl == entry）
- long vs short 默认配置差异
- 长短方向（sl 在 entry 上方/下方）都能算
"""

import pytest

from sprite_catcher.features.sizing import (
    size_long_position,
    size_position,
    size_short_position,
)


# === 基本计算 ===


def test_sizing_risk_based():
    """
    equity 10000, risk 1%, entry 100, sl 95 → 5% 风险/单位
    risk_usd = 100, qty = 100 / 0.05 = 2000
    所有上限都足够大 → 不被卡
    """
    result = size_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=95.0,
        risk_per_trade_pct=0.01,
        max_single_position_pct=1.0,    # 故意放大
        max_portfolio_pct=1.0,
        max_leverage=1.0,
    )
    # 但 leverage_cap = equity × 1 = 10000，而 risk-based = 2000，未到杠杆上限
    assert result.qty_quote_usd == pytest.approx(2000.0)
    assert result.capped_by is None
    assert result.risk_usd == pytest.approx(100.0)


def test_sizing_capped_by_single_position():
    """单仓上限把风险-based 仓位卡住。"""
    result = size_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=95.0,
        risk_per_trade_pct=0.01,        # → risk_based 2000
        max_single_position_pct=0.05,   # → single_cap 500
        max_portfolio_pct=1.0,
        max_leverage=1.0,
    )
    assert result.qty_quote_usd == pytest.approx(500.0)
    assert result.capped_by == "single_position_cap"
    # 实际 risk = 500 × 0.05 = 25
    assert result.risk_usd == pytest.approx(25.0)


def test_sizing_capped_by_portfolio():
    """已开仓占用大部分预算，剩余仓位卡了新单。"""
    result = size_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=95.0,
        risk_per_trade_pct=0.01,
        max_single_position_pct=1.0,
        max_portfolio_pct=0.50,          # 总仓 ≤ 5000
        open_position_value_usd=4_800.0, # 剩余 200
        max_leverage=1.0,
    )
    assert result.qty_quote_usd == pytest.approx(200.0)
    assert result.capped_by == "portfolio_cap"


def test_sizing_capped_by_leverage():
    """杠杆上限把仓位卡住（spot, max_leverage=1.0）。"""
    result = size_position(
        equity_usd=1_000.0,
        entry_price=100.0,
        stop_loss_price=99.5,            # 仅 0.5% 风险密度 → qty 巨大
        risk_per_trade_pct=0.01,         # risk_based = 10 / 0.005 = 2000
        max_single_position_pct=1.0,
        max_portfolio_pct=1.0,
        max_leverage=1.0,
    )
    # leverage_cap = 1000, single_cap = 1000, portfolio_cap = 1000
    # risk_based = 2000 → 被这三个 1000 任一卡住
    assert result.qty_quote_usd == pytest.approx(1000.0)
    # 三者并列时 min() 取第一个；这里取决于实现 — 验证 capped_by 是这三个之一
    assert result.capped_by in {
        "single_position_cap",
        "portfolio_cap",
        "leverage_cap",
    }


def test_sizing_short_with_leverage_2x():
    """Module B 允许 2x → notional 可以是 2 × equity。"""
    result = size_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=103.0,           # short：sl 在上方
        risk_per_trade_pct=0.005,
        max_single_position_pct=0.50,    # 单仓上限放大
        max_portfolio_pct=1.0,
        max_leverage=2.0,
    )
    # risk_usd = 50, risk_per_unit = 0.03 → risk_based = 1666.67
    # single_cap = 10000 × 0.5 × 2 = 10000
    # leverage_cap = 20000
    # → 1666 是 winner, 不被卡
    assert result.qty_quote_usd == pytest.approx(1666.67, abs=0.5)
    assert result.capped_by is None
    assert result.leverage <= 2.0


# === 边界 ===


def test_sizing_zero_equity_raises():
    with pytest.raises(ValueError):
        size_position(
            equity_usd=0.0,
            entry_price=100.0,
            stop_loss_price=95.0,
            risk_per_trade_pct=0.01,
            max_single_position_pct=0.02,
            max_portfolio_pct=0.5,
        )


def test_sizing_negative_entry_raises():
    with pytest.raises(ValueError):
        size_position(
            equity_usd=10_000.0,
            entry_price=-100.0,
            stop_loss_price=95.0,
            risk_per_trade_pct=0.01,
            max_single_position_pct=0.02,
            max_portfolio_pct=0.5,
        )


def test_sizing_entry_equals_sl_raises():
    with pytest.raises(ValueError):
        size_position(
            equity_usd=10_000.0,
            entry_price=100.0,
            stop_loss_price=100.0,
            risk_per_trade_pct=0.01,
            max_single_position_pct=0.02,
            max_portfolio_pct=0.5,
        )


def test_sizing_leverage_below_1_raises():
    with pytest.raises(ValueError):
        size_position(
            equity_usd=10_000.0,
            entry_price=100.0,
            stop_loss_price=95.0,
            risk_per_trade_pct=0.01,
            max_single_position_pct=0.02,
            max_portfolio_pct=0.5,
            max_leverage=0.5,
        )


def test_sizing_works_for_short_direction():
    """sl 在 entry 上方（做空场景） → 风险计算用 abs。"""
    long_r = size_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=95.0,
        risk_per_trade_pct=0.01,
        max_single_position_pct=1.0,
        max_portfolio_pct=1.0,
    )
    short_r = size_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=105.0,
        risk_per_trade_pct=0.01,
        max_single_position_pct=1.0,
        max_portfolio_pct=1.0,
    )
    # 风险密度一致（都是 5%）→ qty 一致
    assert long_r.qty_quote_usd == pytest.approx(short_r.qty_quote_usd)


def test_sizing_portfolio_already_full():
    """已开仓正好用满预算 → portfolio_remaining = 0 → 不开新仓。"""
    result = size_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=95.0,
        risk_per_trade_pct=0.01,
        max_single_position_pct=1.0,
        max_portfolio_pct=0.50,
        open_position_value_usd=5_000.0,
    )
    assert result.qty_quote_usd == 0.0
    assert result.capped_by == "portfolio_cap"
    assert result.risk_usd == 0.0


# === Module A / Module B 默认 wrapper ===


def test_size_long_default_caps():
    """A_* 默认值：risk 1%, single 2%, portfolio 70%, leverage 1x。"""
    result = size_long_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=92.0,        # 8% 风险密度
    )
    # risk_usd = 100, qty = 100/0.08 = 1250
    # single_cap = 10000 × 0.02 × 1 = 200
    # → 被 single_cap 卡到 200
    assert result.qty_quote_usd == pytest.approx(200.0)
    assert result.capped_by == "single_position_cap"
    assert result.leverage <= 1.0


def test_size_short_default_caps():
    """B_* 默认值：risk 0.5%, single 1%, portfolio 20%, leverage 2x。"""
    result = size_short_position(
        equity_usd=10_000.0,
        entry_price=100.0,
        stop_loss_price=103.0,        # 3% 风险密度
    )
    # risk_usd = 50, qty = 50/0.03 = 1666.67
    # single_cap = 10000 × 0.01 × 2 = 200
    # → 被 single_cap 卡到 200
    assert result.qty_quote_usd == pytest.approx(200.0)
    assert result.capped_by == "single_position_cap"
