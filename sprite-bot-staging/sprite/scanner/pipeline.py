"""
单币深度分析管道：L2 → L3 → L5 → TradeIntent。

对每个候选币依次执行：
  1. L2 OI 特征工程（stratify_oi）
  2. L2 筹码特征（compute_chip_features）—— 简化版，只有 top10
  3. L2 候选池分流（route_to_pool）
  4. L3 安全闸（evaluate_long_safety 或 evaluate_short_safety）
  5. L5 策略编排（plan_* → TradeIntent）

设计原则：
  - 任一步骤抛异常 → 记录 warning，返回 None，不停整轮扫描
  - 数据不足（< 3 个时间点）→ 返回 None
  - 信号日志全量落库，无论是否产生 TradeIntent

需要调用方注入：
  - token_id: "{CHAIN}:{contract}"，用于链上 API
  - futures_symbol: "MYXUSDT"，用于 Binance Futures API
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sprite_catcher import (
    compute_chip_features,
    evaluate_long_safety,
    evaluate_short_safety,
    route_to_pool,
    stratify_oi,
)
from sprite_catcher.features.strategies import (
    plan_distribution,
    plan_short_vacuum,
    plan_support_collapse,
    plan_trend_follow,
)
from sprite_catcher.models import (
    Pool,
    TradeIntent,
)

from sprite.l1_data.binance import BinanceFuturesOIProvider, get_candles
from sprite.l1_data.cex_wallets import NullTransferProvider, StaticCEXWalletRegistry
from sprite.l1_data.goplus import (
    GoPlusDevWalletProvider,
    GoPlusHolderProvider,
    GoPlusLiquidityProvider,
    GoPlusTokenAuditProvider,
)

log = logging.getLogger(__name__)

# 共享实例（无状态，可复用）
_oi_provider = BinanceFuturesOIProvider()
_audit_provider = GoPlusTokenAuditProvider()
_liquidity_provider = GoPlusLiquidityProvider()
_dev_provider = GoPlusDevWalletProvider()
_holder_provider = GoPlusHolderProvider()
_transfer_provider = NullTransferProvider()   # v0 无 BFS；cluster_factor ≈ 1
_cex_registry = StaticCEXWalletRegistry()

_MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))


@dataclass
class PipelineResult:
    symbol: str
    token_id: str
    intent: TradeIntent | None
    rejection_reason: str | None   # 被哪一步拒绝
    pool: str | None
    scanned_at: datetime


def run(
    futures_symbol: str,
    token_id: str,               # "{CHAIN}:{contract}"
    daily_pump_pct: float,       # 预筛时已拿到的 24h 价格涨幅（绝对值）
    open_intents: list[TradeIntent],  # 当前已有持仓/意图，用于 L7 准入控制
) -> PipelineResult:
    """
    对单个候选币运行完整分析管道。

    参数：
      futures_symbol:  如 "MYXUSDT"
      token_id:        如 "BSC:0x..."
      daily_pump_pct:  24h 价格变化 %（正 = 涨，负 = 跌）
      open_intents:    当前已有的 TradeIntent 列表，供 L7 用
    """
    now = datetime.now(tz=timezone.utc)

    def _reject(reason: str) -> PipelineResult:
        return PipelineResult(
            symbol=futures_symbol, token_id=token_id,
            intent=None, rejection_reason=reason,
            pool=None, scanned_at=now,
        )

    # ── 步骤 1：L2 OI 特征工程 ────────────────────────────────────────────────
    try:
        oi_strat = stratify_oi(futures_symbol, _oi_provider, lookback_hours=24)
    except Exception as e:
        log.warning("%s: L2 OI 失败: %s", futures_symbol, e)
        return _reject(f"l2_oi_error: {e}")

    # ── 步骤 2：L2 筹码特征（简化版） ─────────────────────────────────────────
    try:
        chip = compute_chip_features(token_id, _holder_provider, _transfer_provider, _cex_registry)
    except Exception as e:
        log.warning("%s: L2 chip 失败（可跳过）: %s", futures_symbol, e)
        chip = None  # chip 不可用时继续，用 OI 做主要指标

    # ── 步骤 3：候选池分流 ────────────────────────────────────────────────────
    if chip is None:
        # chip 拿不到时无法做池分类，只能跳过该币
        return _reject("chip_unavailable")

    try:
        pool_decision = route_to_pool(
            chip=chip,
            oi=oi_strat,
            daily_pump_pct=daily_pump_pct / 100.0,  # route_to_pool 期望 1.0=+100%
        )
    except Exception as e:
        log.warning("%s: pool_router 失败: %s", futures_symbol, e)
        return _reject(f"pool_router_error: {e}")

    pool = pool_decision.pool
    log.info("%s: pool=%s reasons=%s", futures_symbol, pool, pool_decision.reasons)

    if pool == Pool.BLACKLIST:
        return _reject("blacklist")

    # ── 步骤 4：L3 安全闸 ─────────────────────────────────────────────────────
    try:
        audit = _audit_provider.get_token_audit(token_id)
        liquidity = _liquidity_provider.get_liquidity_info(token_id)
        dev = _dev_provider.get_dev_wallet_info(token_id)
    except Exception as e:
        log.warning("%s: L3 数据获取失败: %s", futures_symbol, e)
        return _reject(f"l3_data_error: {e}")

    if pool == Pool.FRIENDLY:
        safety = evaluate_long_safety(audit=audit, dev=dev, liquidity=liquidity, chip=chip)
    else:
        safety = evaluate_short_safety(audit=audit, liquidity=liquidity)

    if not safety.passed:
        log.info("%s: L3 安全闸拒绝: %s", futures_symbol, safety.failure_reasons)
        return _reject(f"safety_gate: {','.join(safety.failure_reasons)}")

    # ── 步骤 5：L5 策略编排 ───────────────────────────────────────────────────
    try:
        candles = get_candles(futures_symbol, interval="1h", limit=100)
        current_price = candles[-1]["close"] if candles else None
    except Exception as e:
        log.warning("%s: K 线获取失败: %s", futures_symbol, e)
        return _reject(f"candle_error: {e}")

    if not current_price:
        return _reject("no_price")

    from sprite_catcher.models import Candle
    candle_objs = [
        Candle(
            ts=c["ts"], open=c["open"], high=c["high"],
            low=c["low"], close=c["close"], volume=c["volume"],
        )
        for c in candles
    ]

    intent: TradeIntent | None = None
    account_equity = _get_account_equity()  # 从 Binance 账户获取

    # 按 pool 类型选策略
    if pool == Pool.FRIENDLY:
        intent = plan_trend_follow(
            pool=pool_decision, safety=safety, chip=chip,
            candles=candle_objs, current_price=current_price,
            account_equity=account_equity,
        )
    elif pool == Pool.OPERATOR:
        # 空头策略按优先级逐一尝试
        intent = (
            plan_support_collapse(
                pool=pool_decision, safety=safety,
                candles=candle_objs, current_price=current_price,
                account_equity=account_equity,
            )
            or plan_short_vacuum(
                pool=pool_decision, safety=safety, oi=oi_strat,
                candles=candle_objs, current_price=current_price,
                account_equity=account_equity,
            )
            or plan_distribution(
                pool=pool_decision, safety=safety, oi=oi_strat,
                candles=candle_objs, current_price=current_price,
                account_equity=account_equity,
            )
        )

    if intent is None:
        return PipelineResult(
            symbol=futures_symbol, token_id=token_id,
            intent=None, rejection_reason="no_strategy_triggered",
            pool=pool.value, scanned_at=now,
        )

    # ── 步骤 6：简化版 L7 准入（v0：只检查开仓数上限）────────────────────────
    # assess_market_regime 需要 BTC 1D K 线 + 总市值历史，v0 暂不接入
    if len(open_intents) >= _MAX_OPEN_POSITIONS:
        log.info("%s: L7 拒绝（持仓已满 %d）", futures_symbol, _MAX_OPEN_POSITIONS)
        return PipelineResult(
            symbol=futures_symbol, token_id=token_id,
            intent=None, rejection_reason=f"l7_max_positions:{_MAX_OPEN_POSITIONS}",
            pool=pool.value, scanned_at=now,
        )

    return PipelineResult(
        symbol=futures_symbol, token_id=token_id,
        intent=intent, rejection_reason=None,
        pool=pool.value, scanned_at=now,
    )


def _get_account_equity() -> float:
    """
    从 Binance Futures 账户获取当前净值（USDT）。

    paper 模式下从 .env 读取 PAPER_ACCOUNT_EQUITY（默认 1000 USDT）。
    live 模式下调用 /fapi/v2/account。
    """
    import os
    run_mode = os.getenv("RUN_MODE", "paper")
    if run_mode == "paper":
        return float(os.getenv("PAPER_ACCOUNT_EQUITY", "1000"))

    import time
    import hmac
    import hashlib
    import requests

    api_key = os.environ["BINANCE_API_KEY"]
    api_secret = os.environ["BINANCE_API_SECRET"]
    ts = int(time.time() * 1000)
    qs = f"timestamp={ts}"
    sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    resp = requests.get(
        "https://fapi.binance.com/fapi/v2/account",
        params={"timestamp": ts, "signature": sig},
        headers={"X-MBX-APIKEY": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()["totalWalletBalance"])
