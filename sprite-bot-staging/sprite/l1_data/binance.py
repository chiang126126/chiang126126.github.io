"""
L1 Binance Futures 数据适配器。

实现 sprite_catcher.interfaces.OIProvider Protocol。
只用公开端点，不需要 API key（只读行情数据）。
所有 HTTP 调用包含重试 + 超时，失败抛 RuntimeError，调用方负责降级。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from sprite_catcher.models import TimeSeriesPoint

_BASE_FAPI = "https://fapi.binance.com"
_BASE_DAPI = "https://dapi.binance.com"  # Coin-M，暂不使用

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "sprite-bot/0.1"

_TIMEOUT = 10  # seconds
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 2, 4]  # seconds


def _get(url: str, params: dict | None = None) -> Any:
    """带重试的 GET，失败时抛 RuntimeError。"""
    last_exc: Exception | None = None
    for attempt, wait in enumerate([0] + _RETRY_BACKOFF):
        if wait:
            time.sleep(wait)
        try:
            resp = _SESSION.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Binance GET {url} failed after {_MAX_RETRIES} retries: {last_exc}") from last_exc


# ── 候选池筛选 ─────────────────────────────────────────────────────────────────

def list_usdt_perpetuals() -> list[dict]:
    """返回所有 USDT-M 永续合约的 24h ticker 数据。

    每个元素包含：symbol, priceChangePercent, quoteVolume, lastPrice。
    """
    data = _get(f"{_BASE_FAPI}/fapi/v1/ticker/24hr")
    return [t for t in data if t["symbol"].endswith("USDT") and "PERP" not in t["symbol"]]


def get_oi_change_24h(symbol: str) -> float:
    """返回过去 24h OI 变化百分比（正数 = OI 增加）。"""
    hist = _get(
        f"{_BASE_FAPI}/futures/data/openInterestHist",
        params={"symbol": symbol, "period": "1h", "limit": 25},
    )
    if len(hist) < 2:
        return 0.0
    oldest = float(hist[0]["sumOpenInterestValue"])
    newest = float(hist[-1]["sumOpenInterestValue"])
    if oldest <= 0:
        return 0.0
    return (newest - oldest) / oldest * 100.0


# ── OIProvider Protocol 实现 ──────────────────────────────────────────────────

class BinanceFuturesOIProvider:
    """
    实现 sprite_catcher.interfaces.OIProvider。

    用途：为 L2 特征工程（stratify_oi）提供数据。
    注意：get_oi_by_exchange 目前只返回 Binance 一家；
         多所聚合数据需要接入 Coinglass API（可在后续版本补充）。
    """

    def get_oi_by_exchange(self, symbol: str) -> dict[str, float]:
        """返回 {exchange_name: oi_usd}。当前只有 Binance。"""
        data = _get(f"{_BASE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol})
        mark = self._get_mark_price(symbol)
        oi_usd = float(data["openInterest"]) * mark
        return {"binance": oi_usd}

    def get_vol_24h(self, symbol: str) -> float:
        data = _get(f"{_BASE_FAPI}/fapi/v1/ticker/24hr", params={"symbol": symbol})
        return float(data["quoteVolume"])

    def get_oi_series(self, symbol: str, *, hours: int) -> list[TimeSeriesPoint]:
        """5 分钟间隔 OI 时间序列，最多返回 500 个点（约 41 小时）。"""
        limit = min(hours * 12, 500)
        data = _get(
            f"{_BASE_FAPI}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": "5m", "limit": limit},
        )
        return [
            TimeSeriesPoint(
                ts=datetime.fromtimestamp(int(item["timestamp"]) / 1000, tz=timezone.utc),
                value=float(item["sumOpenInterestValue"]),
            )
            for item in data
        ]

    def get_price_series(self, symbol: str, *, hours: int) -> list[TimeSeriesPoint]:
        """5 分钟 K 线收盘价序列，与 get_oi_series 时间对齐（同样 5m 间隔）。"""
        limit = min(hours * 12, 1000)
        data = _get(
            f"{_BASE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": "5m", "limit": limit},
        )
        return [
            TimeSeriesPoint(
                ts=datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),
                value=float(k[4]),  # close price
            )
            for k in data
        ]

    def get_orderbook_large_order_ratio(self, symbol: str) -> float:
        """
        大单（USD 金额 > 中位数×10）在订单簿中的占比。

        ⚠️ 已知限制：Binance 永续合约订单簿不含隐藏单（iceberg order），
           大单比例可能被低估。这是可接受的 v0 简化。
        """
        data = _get(
            f"{_BASE_FAPI}/fapi/v1/depth",
            params={"symbol": symbol, "limit": 500},
        )
        mark = self._get_mark_price(symbol)
        all_sides = data["bids"] + data["asks"]
        if not all_sides:
            return 0.5

        amounts = [float(price) * float(qty) for price, qty in all_sides]
        amounts.sort()
        median = amounts[len(amounts) // 2]
        threshold = median * 10
        large_count = sum(1 for a in amounts if a >= threshold)
        return large_count / len(amounts)

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _get_mark_price(self, symbol: str) -> float:
        data = _get(f"{_BASE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol})
        return float(data["markPrice"])


# ── K 线工具（供 L5 策略层使用）──────────────────────────────────────────────

def get_candles(symbol: str, interval: str = "1h", limit: int = 100) -> list[dict]:
    """
    返回原始 K 线列表，每个元素：
      {"ts": datetime, "open": float, "high": float, "low": float,
       "close": float, "volume": float}
    """
    data = _get(
        f"{_BASE_FAPI}/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    return [
        {
            "ts": datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in data
    ]
