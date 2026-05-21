"""
冒烟测试：验证各层可以被 import、Mock 数据能跑通完整管道。

不调用真实 API，全部用 Mock 数据。
运行：pytest tests/test_smoke.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from sprite_catcher.models import (
    AllocationCaps,
    Candle,
    DevWalletInfo,
    HolderSnapshot,
    LiquidityInfo,
    OIStratification,
    Pool,
    PoolDecision,
    SafetyReport,
    Side,
    TimeSeriesPoint,
    TokenAuditInfo,
    TradeIntent,
)
from sprite.l1_data.cex_wallets import StaticCEXWalletRegistry
from sprite.registry import TokenInfo, lookup, register


def _ts(offset_hours: int = 0) -> datetime:
    from datetime import timedelta
    return datetime.now(tz=timezone.utc) - timedelta(hours=offset_hours)


# ── CEXWalletRegistry ──────────────────────────────────────────────────────────

def test_cex_wallet_known_binance():
    reg = StaticCEXWalletRegistry()
    assert reg.is_cex_hot_wallet("0x8894e0a0c962cb723c1976a4421c95949be2d4e3")


def test_burn_address():
    reg = StaticCEXWalletRegistry()
    assert reg.is_burn_or_contract("0x000000000000000000000000000000000000dead")
    assert reg.is_burn_or_contract("0x0000000000000000000000000000000000000000")


def test_random_address_not_cex():
    reg = StaticCEXWalletRegistry()
    assert not reg.is_cex_hot_wallet("0xdeadbeef12345678901234567890abcdef123456")


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registry_lookup_known():
    info = lookup("MYXUSDT")
    assert info is not None
    assert info.chain == "ARB"
    assert info.token_id.startswith("ARB:")


def test_registry_lookup_unknown():
    assert lookup("UNKNOWNUSDT") is None


def test_registry_dynamic_register():
    info = TokenInfo("TESTUSDT", "BSC", "0xdeadbeef" + "0" * 32)
    register(info)
    assert lookup("TESTUSDT") == info


# ── Pipeline 冒烟（完整 Mock）────────────────────────────────────────────────

def _make_mock_oi_provider():
    pts = [TimeSeriesPoint(ts=_ts(i), value=1_000_000.0 + i * 1000) for i in range(30, 0, -1)]
    m = MagicMock()
    m.get_oi_by_exchange.return_value = {"binance": 10_000_000.0}
    m.get_vol_24h.return_value = 50_000_000.0
    m.get_oi_series.return_value = pts
    m.get_price_series.return_value = [TimeSeriesPoint(ts=p.ts, value=1.0 + i * 0.01) for i, p in enumerate(pts)]
    m.get_orderbook_large_order_ratio.return_value = 0.03
    return m


def _make_mock_candles(n: int = 50) -> list[Candle]:
    base = 1.0
    candles = []
    for i in range(n):
        c = base * (1 + i * 0.005)
        candles.append(Candle(ts=_ts(n - i), open=c * 0.99, high=c * 1.01, low=c * 0.98, close=c, volume=1_000_000.0))
    return candles


@patch("sprite.scanner.pipeline._oi_provider")
@patch("sprite.scanner.pipeline._audit_provider")
@patch("sprite.scanner.pipeline._liquidity_provider")
@patch("sprite.scanner.pipeline._dev_provider")
@patch("sprite.scanner.pipeline._holder_provider")
@patch("sprite.scanner.pipeline.get_candles")
@patch("sprite.scanner.pipeline._get_account_equity", return_value=10_000.0)
def test_pipeline_smoke(mock_equity, mock_candles, mock_holder, mock_dev, mock_liq, mock_audit, mock_oi):
    mock_oi.get_oi_by_exchange.return_value = {"binance": 10_000_000.0}
    mock_oi.get_vol_24h.return_value = 50_000_000.0

    n = 30
    pts = [TimeSeriesPoint(ts=_ts(n - i), value=1_000_000.0 + i * 5000) for i in range(n)]
    mock_oi.get_oi_series.return_value = pts
    mock_oi.get_price_series.return_value = [TimeSeriesPoint(ts=p.ts, value=0.9 + i * 0.005) for i, p in enumerate(pts)]
    mock_oi.get_orderbook_large_order_ratio.return_value = 0.03

    mock_audit.get_token_audit.return_value = TokenAuditInfo(
        mintable=False, freezeable=False, pausable=False,
        buy_tax=0.01, sell_tax=0.01, is_honeypot=False, top10_holder_pct=0.15,
    )
    mock_liq.get_liquidity_info.return_value = LiquidityInfo(
        liquidity_usd=500_000.0, lp_locked_pct=0.95,
        lp_lock_remaining_days=365, pool_age_days=90,
    )
    mock_dev.get_dev_wallet_info.return_value = DevWalletInfo(
        deployer_address="0xabc", prior_deploys=0,
        has_rug_history=False, best_prior_market_cap_usd=0.0,
    )
    mock_holder.get_holders.return_value = [
        HolderSnapshot(address=f"0x{i:040x}", balance=Decimal("1000")) for i in range(10)
    ]
    mock_holder.get_circulating_supply.return_value = 1_000_000.0

    raw_candles = [
        {"ts": _ts(n - i), "open": 1.0 + i * 0.005, "high": 1.05 + i * 0.005,
         "low": 0.98 + i * 0.005, "close": 1.02 + i * 0.005, "volume": 1_000_000.0}
        for i in range(n)
    ]
    mock_candles.return_value = raw_candles

    from sprite.scanner.pipeline import run
    result = run(
        futures_symbol="MYXUSDT",
        token_id="ARB:0x2129f36f07604c8a8c9a61804e01c6d9e9fe7f7f",
        daily_pump_pct=45.0,
        open_intents=[],
    )

    # 管道跑完，不抛异常
    assert result.symbol == "MYXUSDT"
    # intent 可能 None（取决于信号是否触发），但管道本身应该成功走完
    assert result.rejection_reason != "l2_oi_error"
    assert result.rejection_reason != "candle_error"
