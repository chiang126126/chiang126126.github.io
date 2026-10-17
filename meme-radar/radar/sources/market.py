# -*- coding: utf-8 -*-
"""market.py — 市场环境数据：BTC/ETH 日线（OKX 主，Coinbase 备）、CoinGecko 全局占比与前 100 币、恐惧贪婪。

GitHub 美国 runner 访问 Binance 会 451，所以这里不用 Binance。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..util import safe_float

OKX = "https://www.okx.com/api/v5"
COINBASE = "https://api.exchange.coinbase.com"
COINGECKO = "https://api.coingecko.com/api/v3"
FNG = "https://api.alternative.me/fng/"

STABLES = {"usdt", "usdc", "dai", "fdusd", "usde", "tusd", "usds", "pyusd", "usdd", "frax", "busd", "gusd",
           "usd1", "rlusd", "usdp", "lusd", "eurc", "eurt", "usdb", "usd0", "usdy", "usdtb", "bfusd"}
WRAPPED = {"wbtc", "weth", "steth", "wsteth", "cbbtc", "reth", "weeth", "wbeth", "bnsol", "jitosol", "msol",
           "cbeth", "rseth", "ezeth", "sfrxeth", "tbtc", "lbtc", "solvbtc", "eeth", "mbtc", "wbnb", "wtrx"}


class MarketData:
    def __init__(self, http_okx, http_cb, http_cg, http_fng, coingecko_key: str = ""):
        self.okx, self.cb, self.cg, self.fng = http_okx, http_cb, http_cg, http_fng
        self.cg_headers = {"x-cg-demo-api-key": coingecko_key} if coingecko_key else None

    # ---------------------------------------------------------------- candles
    def daily_closes(self, inst: str = "BTC-USDT", n: int = 300) -> List[float]:
        """升序收盘价列表。OKX 优先，Coinbase 兜底。"""
        try:
            payload = self.okx.get_json(f"{OKX}/market/candles", {"instId": inst, "bar": "1D", "limit": min(n, 300)}, ttl=1800)
            rows = (payload or {}).get("data") or []
            closes = [safe_float(r[4]) for r in rows if isinstance(r, list) and len(r) > 4]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 30:
                return list(reversed(closes))
        except Exception:
            pass
        product = {"BTC-USDT": "BTC-USD", "ETH-USDT": "ETH-USD", "ETH-BTC": "ETH-BTC"}.get(inst, inst.replace("USDT", "USD"))
        try:
            rows = self.cb.get_json(f"{COINBASE}/products/{product}/candles", {"granularity": 86400}, ttl=1800)
            closes = [safe_float(r[4]) for r in rows or [] if isinstance(r, list) and len(r) > 4]
            closes = [c for c in closes if c is not None]
            return list(reversed(closes))
        except Exception:
            return []

    def daily_ohlc(self, inst: str = "BTC-USDT", n: int = 60) -> List[List[float]]:
        """升序 [ts, o, h, l, c]。"""
        try:
            payload = self.okx.get_json(f"{OKX}/market/candles", {"instId": inst, "bar": "1D", "limit": min(n, 300)}, ttl=1800)
            rows = (payload or {}).get("data") or []
            out = []
            for r in rows:
                try:
                    out.append([int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4])])
                except (TypeError, ValueError, IndexError):
                    continue
            out.sort(key=lambda r: r[0])
            return out
        except Exception:
            return []

    # ---------------------------------------------------------------- coingecko
    def global_metrics(self) -> Dict[str, Optional[float]]:
        try:
            payload = self.cg.get_json(f"{COINGECKO}/global", ttl=900, headers=self.cg_headers)
            d = (payload or {}).get("data") or {}
            return {
                "btc_dominance": safe_float((d.get("market_cap_percentage") or {}).get("btc")),
                "eth_dominance": safe_float((d.get("market_cap_percentage") or {}).get("eth")),
                "total_mcap_usd": safe_float((d.get("total_market_cap") or {}).get("usd")),
                "mcap_change_24h_pct": safe_float(d.get("market_cap_change_percentage_24h_usd")),
            }
        except Exception:
            return {"btc_dominance": None, "eth_dominance": None, "total_mcap_usd": None, "mcap_change_24h_pct": None}

    def top_coins(self, per_page: int = 100) -> List[Dict[str, Any]]:
        try:
            rows = self.cg.get_json(f"{COINGECKO}/coins/markets",
                                    {"vs_currency": "usd", "order": "market_cap_desc", "per_page": per_page,
                                     "page": 1, "sparkline": "false", "price_change_percentage": "7d,30d"},
                                    ttl=900, headers=self.cg_headers)
            return [r for r in rows or [] if isinstance(r, dict)]
        except Exception:
            return []

    @staticmethod
    def alt_breadth(coins: List[Dict[str, Any]], window: str = "30d") -> Optional[Dict[str, Any]]:
        """前 100 币里（剔除稳定币/包装币/BTC）在 window 内跑赢 BTC 的比例 —— 山寨季代理指标。"""
        key = f"price_change_percentage_{window}_in_currency"
        btc = next((c for c in coins if (c.get("id") == "bitcoin")), None)
        if not btc or btc.get(key) is None:
            return None
        btc_chg = safe_float(btc.get(key))
        alts = [c for c in coins if c.get("id") != "bitcoin"
                and (c.get("symbol") or "").lower() not in STABLES
                and (c.get("symbol") or "").lower() not in WRAPPED
                and safe_float(c.get(key)) is not None]
        if len(alts) < 20:
            return None
        beat = sum(1 for c in alts if safe_float(c.get(key)) > btc_chg)
        return {"window": window, "n": len(alts), "beat_btc": beat, "breadth": round(beat / len(alts), 4),
                "btc_change_pct": btc_chg}

    def fear_greed(self) -> Dict[str, Any]:
        try:
            payload = self.fng.get_json(FNG, {"limit": 1}, ttl=1800)
            d = ((payload or {}).get("data") or [{}])[0]
            return {"value": safe_float(d.get("value")), "classification": d.get("value_classification") or ""}
        except Exception:
            return {"value": None, "classification": ""}
