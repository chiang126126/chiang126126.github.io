"""
候选池预筛选。

从 Binance USDT-M 永续合约中，快速过滤出值得深度分析的妖币候选。
目标：把 ~300 个合约缩减到 10-30 个，再交给 pipeline.py 深度分析。

预筛条件（满足任一即入选）：
  A. 24h 价格涨幅 >= PRICE_CHANGE_PCT（捕捉多头妖币）
  B. 24h OI 涨幅   >= OI_SPIKE_PCT     （捕捉操纵建仓）
  C. 24h 价格跌幅 <= -PRICE_DROP_PCT   （捕捉崩塌做空候选）

排除条件：
  - 24h 成交额 < MIN_QUOTE_VOL（流动性不足，信号不可信）
  - 大币（市值 top 20），它们不是妖币目标
"""

from __future__ import annotations

import os

from sprite.l1_data.binance import get_oi_change_24h, list_usdt_perpetuals

# 可通过 .env 覆盖
_PRICE_CHANGE_PCT = float(os.getenv("PREFILTER_PRICE_CHANGE_PCT", "20"))
_OI_SPIKE_PCT = float(os.getenv("PREFILTER_OI_SPIKE_PCT", "30"))
_PRICE_DROP_PCT = float(os.getenv("PREFILTER_PRICE_DROP_PCT", "15"))
_MIN_QUOTE_VOL = float(os.getenv("PREFILTER_MIN_QUOTE_VOL", "5_000_000"))

# 排除的大币（它们不是妖币，不会有妖币信号）
_EXCLUDE_SYMBOLS: frozenset[str] = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT", "LINKUSDT",
    "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT",
    "OPUSDT", "ARBUSDT", "SUIUSDT", "SEIUSDT", "INJUSDT",
})


def screen_candidates() -> list[dict]:
    """
    返回通过预筛的候选列表。每个元素：
      {
        "symbol": "MYXUSDT",
        "price_change_pct": 45.2,
        "quote_vol": 38_000_000,
        "reason": "price_pump"   # price_pump | oi_spike | price_drop
      }

    注意：OI 变化需要额外 API 调用（每个候选一次），所以只对
    价格满足条件的币再查 OI。对纯 OI 候选（价格平，OI 暴增），
    采用按批次扫描策略（每次扫 50 个随机采样）。
    """
    tickers = list_usdt_perpetuals()
    candidates: list[dict] = []

    for t in tickers:
        sym = t["symbol"]
        if sym in _EXCLUDE_SYMBOLS:
            continue

        quote_vol = float(t.get("quoteVolume", 0))
        if quote_vol < _MIN_QUOTE_VOL:
            continue

        pct = float(t.get("priceChangePercent", 0))

        if pct >= _PRICE_CHANGE_PCT:
            candidates.append({
                "symbol": sym,
                "price_change_pct": pct,
                "quote_vol": quote_vol,
                "reason": "price_pump",
            })
            continue

        if pct <= -_PRICE_DROP_PCT:
            candidates.append({
                "symbol": sym,
                "price_change_pct": pct,
                "quote_vol": quote_vol,
                "reason": "price_drop",
            })

    # OI 候选：对价格变化 -10% ~ +10% 的币，抽查 OI 涨幅
    # 限制扫描数量，避免 API 调用过多
    neutral = [
        t for t in tickers
        if t["symbol"] not in _EXCLUDE_SYMBOLS
        and float(t.get("quoteVolume", 0)) >= _MIN_QUOTE_VOL
        and -10 < float(t.get("priceChangePercent", 0)) < 10
        and t["symbol"] not in {c["symbol"] for c in candidates}
    ]
    # 按成交量排序，只查 top-50
    neutral.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
    for t in neutral[:50]:
        sym = t["symbol"]
        try:
            oi_chg = get_oi_change_24h(sym)
        except Exception:
            continue
        if oi_chg >= _OI_SPIKE_PCT:
            candidates.append({
                "symbol": sym,
                "price_change_pct": float(t.get("priceChangePercent", 0)),
                "quote_vol": float(t.get("quoteVolume", 0)),
                "reason": "oi_spike",
            })

    # 按成交量降序（流动性好的优先）
    candidates.sort(key=lambda c: c["quote_vol"], reverse=True)
    return candidates
