# -*- coding: utf-8 -*-
"""dexscreener.py — DexScreener 公共 API（免费，300 req/min；profiles/boosts 60 req/min）。

用途：交易对补充数据（社交链接、boost 付费推广、图片）、按代币查所有池、搜索。
注意：DexScreener 没有『按链列出新池』的公开接口，新币发现以 GeckoTerminal 为主。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models import PoolSnapshot
from ..util import iso, now_utc, parse_iso, safe_float, safe_int, norm_addr

DS_BASE = "https://api.dexscreener.com"


class DexScreener:
    def __init__(self, http, chain: str = "robinhood"):
        self.http = http
        self.chain = chain

    def _get(self, path: str, params: Optional[dict] = None, ttl: int = 60) -> Any:
        return self.http.get_json(DS_BASE + path, params=params, ttl=ttl)

    def parse_pair(self, p: dict) -> Optional[PoolSnapshot]:
        if not isinstance(p, dict) or not p.get("pairAddress"):
            return None
        base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
        txns = {}
        for w, v in (p.get("txns") or {}).items():
            if isinstance(v, dict):
                txns[w] = {"buys": safe_int(v.get("buys")), "sells": safe_int(v.get("sells")),
                           "buyers": None, "sellers": None}
        created = parse_iso(p.get("pairCreatedAt"))
        age = (now_utc() - created).total_seconds() / 3600.0 if created else None
        info = p.get("info") or {}
        socials = [s.get("type") or s.get("url") for s in info.get("socials") or [] if isinstance(s, dict)]
        websites = [w.get("url") for w in info.get("websites") or [] if isinstance(w, dict)]
        return PoolSnapshot(
            chain=p.get("chainId") or self.chain,
            pool_address=norm_addr(p.get("pairAddress")),
            dex=p.get("dexId") or "",
            base_token=norm_addr(base.get("address") or ""),
            base_symbol=base.get("symbol") or "",
            base_name=base.get("name") or "",
            quote_token=norm_addr(quote.get("address") or ""),
            quote_symbol=quote.get("symbol") or "",
            price_usd=safe_float(p.get("priceUsd")),
            fdv_usd=safe_float(p.get("fdv")),
            market_cap_usd=safe_float(p.get("marketCap")),
            liquidity_usd=safe_float((p.get("liquidity") or {}).get("usd")),
            volume_usd={w: safe_float(v) for w, v in (p.get("volume") or {}).items()},
            price_change_pct={w: safe_float(v) for w, v in (p.get("priceChange") or {}).items()},
            txns=txns,
            pool_created_at=iso(created) if created else None,
            age_hours=round(age, 3) if age is not None else None,
            source="dexscreener",
            url=p.get("url") or "",
            info={"image": info.get("imageUrl"), "websites": websites, "socials": socials,
                  "boosts_active": safe_int((p.get("boosts") or {}).get("active")),
                  "labels": p.get("labels") or []},
            observed_at=iso(),
        )

    def _parse_list(self, payload: Any) -> List[PoolSnapshot]:
        if isinstance(payload, dict):
            payload = payload.get("pairs") or payload.get("pair") or []
            if isinstance(payload, dict):
                payload = [payload]
        out = []
        for p in payload or []:
            s = self.parse_pair(p)
            if s and s.chain == self.chain:
                out.append(s)
        return out

    def token_pairs(self, token_address: str) -> List[PoolSnapshot]:
        return self._parse_list(self._get(f"/token-pairs/v1/{self.chain}/{token_address}", ttl=60))

    def tokens(self, addresses: List[str]) -> List[PoolSnapshot]:
        out: List[PoolSnapshot] = []
        for i in range(0, len(addresses), 30):
            chunk = ",".join(addresses[i:i + 30])
            out += self._parse_list(self._get(f"/tokens/v1/{self.chain}/{chunk}", ttl=60))
        return out

    def pair(self, pair_address: str) -> Optional[PoolSnapshot]:
        lst = self._parse_list(self._get(f"/latest/dex/pairs/{self.chain}/{pair_address}", ttl=30))
        return lst[0] if lst else None

    def search(self, q: str) -> List[PoolSnapshot]:
        return self._parse_list(self._get("/latest/dex/search", {"q": q}, ttl=60))

    def latest_boosts(self) -> List[Dict[str, Any]]:
        payload = self._get("/token-boosts/latest/v1", ttl=300)
        return [b for b in (payload or []) if isinstance(b, dict) and b.get("chainId") == self.chain]

    def latest_profiles(self) -> List[Dict[str, Any]]:
        payload = self._get("/token-profiles/latest/v1", ttl=300)
        return [b for b in (payload or []) if isinstance(b, dict) and b.get("chainId") == self.chain]

    def best_pair_for_token(self, token_address: str) -> Optional[PoolSnapshot]:
        pairs = self.token_pairs(token_address)
        pairs = [p for p in pairs if p.base_token == norm_addr(token_address)]
        if not pairs:
            return None
        pairs.sort(key=lambda p: (p.liquidity_usd or 0), reverse=True)
        return pairs[0]
