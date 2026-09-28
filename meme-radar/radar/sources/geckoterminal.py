# -*- coding: utf-8 -*-
"""geckoterminal.py — GeckoTerminal 公共 API v2（免费，约 30 req/min）。

用途：新池发现（new_pools）、趋势池、单池详情、OHLCV（回填结果用）、最近成交（含买方地址，
用于聪明钱共振与取证）、代币信息（社交/持仓分布）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models import PoolSnapshot
from ..util import iso, now_utc, parse_iso, safe_float, safe_int, norm_addr

GT_BASE = "https://api.geckoterminal.com/api/v2"
GT_HEADERS = {"Accept": "application/json;version=20230302"}
WINDOWS = ("m5", "m15", "m30", "h1", "h6", "h24")


def _strip_network(token_id: str, network: str) -> str:
    # "robinhood_0xabc..." -> "0xabc..."
    if isinstance(token_id, str) and token_id.startswith(network + "_"):
        return norm_addr(token_id[len(network) + 1:])
    return norm_addr(token_id)


class GeckoTerminal:
    def __init__(self, http, network: str = "robinhood"):
        self.http = http
        self.network = network

    # ------------------------------------------------------------ raw
    def _get(self, path: str, params: Optional[dict] = None, ttl: int = 60) -> Any:
        return self.http.get_json(GT_BASE + path, params=params, ttl=ttl, headers=GT_HEADERS)

    # ------------------------------------------------------------ parsing
    def parse_pools(self, payload: Any) -> List[PoolSnapshot]:
        if not isinstance(payload, dict):
            return []
        included = {}
        for inc in payload.get("included") or []:
            if isinstance(inc, dict) and inc.get("id"):
                included[inc["id"]] = inc
        data = payload.get("data")
        if isinstance(data, dict):
            data = [data]
        out = []
        for item in data or []:
            snap = self.parse_pool(item, included)
            if snap:
                out.append(snap)
        return out

    def parse_pool(self, item: dict, included: Optional[dict] = None) -> Optional[PoolSnapshot]:
        if not isinstance(item, dict):
            return None
        a = item.get("attributes") or {}
        rel = item.get("relationships") or {}
        included = included or {}

        def rel_id(key: str) -> str:
            d = (rel.get(key) or {}).get("data") or {}
            return d.get("id") or ""

        base_id, quote_id = rel_id("base_token"), rel_id("quote_token")
        base_inc = (included.get(base_id) or {}).get("attributes") or {}
        quote_inc = (included.get(quote_id) or {}).get("attributes") or {}
        name = a.get("name") or ""
        parts = [p.strip() for p in name.split("/")]
        base_sym = base_inc.get("symbol") or (parts[0] if parts else "")
        quote_sym = quote_inc.get("symbol") or (parts[1] if len(parts) > 1 else "")
        # 名称里可能带 " 0.3%" 之类的费率后缀
        quote_sym = quote_sym.split(" ")[0] if quote_sym else quote_sym

        txns: Dict[str, Dict[str, Optional[int]]] = {}
        for w, v in (a.get("transactions") or {}).items():
            if isinstance(v, dict):
                txns[w] = {k: safe_int(v.get(k)) for k in ("buys", "sells", "buyers", "sellers")}
        created = a.get("pool_created_at")
        cdt = parse_iso(created)
        age = (now_utc() - cdt).total_seconds() / 3600.0 if cdt else None
        dex_id = rel_id("dex")
        snap = PoolSnapshot(
            chain=self.network,
            pool_address=norm_addr(a.get("address") or ""),
            dex=dex_id.replace("-" + self.network, "") if dex_id else "",
            base_token=_strip_network(base_id, self.network),
            base_symbol=base_sym or "",
            base_name=base_inc.get("name") or base_sym or "",
            quote_token=_strip_network(quote_id, self.network),
            quote_symbol=quote_sym or "",
            price_usd=safe_float(a.get("base_token_price_usd")),
            fdv_usd=safe_float(a.get("fdv_usd")),
            market_cap_usd=safe_float(a.get("market_cap_usd")),
            liquidity_usd=safe_float(a.get("reserve_in_usd")),
            volume_usd={w: safe_float((a.get("volume_usd") or {}).get(w)) for w in WINDOWS},
            price_change_pct={w: safe_float((a.get("price_change_percentage") or {}).get(w)) for w in WINDOWS},
            txns=txns,
            pool_created_at=iso(cdt) if cdt else None,
            age_hours=round(age, 3) if age is not None else None,
            source="geckoterminal",
            url=f"https://www.geckoterminal.com/{self.network}/pools/{norm_addr(a.get('address') or '')}",
            observed_at=iso(),
        )
        return snap if snap.pool_address else None

    # ------------------------------------------------------------ endpoints
    def new_pools(self, page: int = 1) -> List[PoolSnapshot]:
        return self.parse_pools(self._get(f"/networks/{self.network}/new_pools",
                                          {"page": page, "include": "base_token,quote_token,dex"}, ttl=90))

    def trending_pools(self, duration: str = "1h", page: int = 1) -> List[PoolSnapshot]:
        return self.parse_pools(self._get(f"/networks/{self.network}/trending_pools",
                                          {"page": page, "duration": duration,
                                           "include": "base_token,quote_token,dex"}, ttl=90))

    def top_pools(self, page: int = 1, sort: str = "h24_volume_usd_desc") -> List[PoolSnapshot]:
        return self.parse_pools(self._get(f"/networks/{self.network}/pools",
                                          {"page": page, "sort": sort,
                                           "include": "base_token,quote_token,dex"}, ttl=120))

    def pool(self, pool_address: str) -> Optional[PoolSnapshot]:
        pools = self.parse_pools(self._get(f"/networks/{self.network}/pools/{pool_address}",
                                           {"include": "base_token,quote_token,dex"}, ttl=30))
        return pools[0] if pools else None

    def pools_multi(self, addresses: List[str]) -> List[PoolSnapshot]:
        out: List[PoolSnapshot] = []
        for i in range(0, len(addresses), 30):
            chunk = ",".join(addresses[i:i + 30])
            out += self.parse_pools(self._get(f"/networks/{self.network}/pools/multi/{chunk}",
                                              {"include": "base_token,quote_token,dex"}, ttl=30))
        return out

    def token_pools(self, token_address: str, page: int = 1) -> List[PoolSnapshot]:
        return self.parse_pools(self._get(f"/networks/{self.network}/tokens/{token_address}/pools",
                                          {"page": page, "include": "base_token,quote_token,dex"}, ttl=60))

    def ohlcv(self, pool_address: str, timeframe: str = "hour", aggregate: int = 1, limit: int = 100,
              before_timestamp: Optional[int] = None, currency: str = "usd") -> List[List[float]]:
        """返回 [[ts_sec, o, h, l, c, vol_usd], ...]，按时间升序。"""
        params: Dict[str, Any] = {"aggregate": aggregate, "limit": limit, "currency": currency}
        if before_timestamp:
            params["before_timestamp"] = int(before_timestamp)
        payload = self._get(f"/networks/{self.network}/pools/{pool_address}/ohlcv/{timeframe}", params, ttl=300)
        rows = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        out = []
        for r in rows:
            try:
                out.append([int(r[0])] + [float(x) for x in r[1:6]])
            except (TypeError, ValueError, IndexError):
                continue
        out.sort(key=lambda r: r[0])
        return out

    def trades(self, pool_address: str, min_usd: float = 0) -> List[Dict[str, Any]]:
        """最近成交（最多 300 条 / 24h），含 tx_from_address —— 这是钱包级信号的来源。"""
        payload = self._get(f"/networks/{self.network}/pools/{pool_address}/trades",
                            {"trade_volume_in_usd_greater_than": min_usd}, ttl=60)
        out = []
        for item in (payload or {}).get("data") or []:
            a = item.get("attributes") or {}
            kind = a.get("kind")
            out.append({
                "tx_hash": a.get("tx_hash"),
                "wallet": norm_addr(a.get("tx_from_address") or ""),
                "kind": kind,
                "volume_usd": safe_float(a.get("volume_in_usd"), 0.0),
                "price_usd": safe_float(a.get("price_to_in_usd") if kind == "buy" else a.get("price_from_in_usd")),
                "ts": a.get("block_timestamp"),
                "block": safe_int(a.get("block_number")),
            })
        return out

    def token_info(self, token_address: str) -> Dict[str, Any]:
        payload = self._get(f"/networks/{self.network}/tokens/{token_address}/info", ttl=600)
        return ((payload or {}).get("data") or {}).get("attributes") or {}

    def token(self, token_address: str) -> Dict[str, Any]:
        payload = self._get(f"/networks/{self.network}/tokens/{token_address}", ttl=120)
        return ((payload or {}).get("data") or {}).get("attributes") or {}
