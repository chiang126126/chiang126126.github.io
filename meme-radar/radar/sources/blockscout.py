# -*- coding: utf-8 -*-
"""blockscout.py — Robinhood Chain 浏览器 API（Blockscout）。

两种入口：
  • 公共实例  https://robinhoodchain.blockscout.com/api/v2/...   （无 key，best-effort 限流）
  • PRO API  https://api.blockscout.com/4663/api/v2/...?apikey=  （免费档 5 rps / 10 万次每天，去 dev.blockscout.com 申请）

取证层需要的：代币持有人、地址是否合约/创建者、地址计数器、最早交易（钱包年龄 + 首个打款方）、
代币早期转账日志（早期买家）。注意：本链 getLogs / txlistinternal 单次最多 1000 条。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models import HolderInfo
from ..util import norm_addr, safe_int, topic0

TRANSFER_TOPIC = topic0("Transfer(address,address,uint256)")


def _hex_or_int(v: Any) -> int:
    s = str(v or "0")
    try:
        return int(s, 16) if s.startswith("0x") else int(float(s))
    except ValueError:
        return 0


class Blockscout:
    def __init__(self, http, chain_cfg: Dict[str, Any], api_key: str = ""):
        self.http = http
        ex = chain_cfg.get("explorer") or {}
        self.chain_id = chain_cfg.get("chain_id")
        self.api_key = api_key
        if api_key:
            base = (ex.get("pro_api_base") or "https://api.blockscout.com").rstrip("/")
            self.rest = f"{base}/{self.chain_id}/api/v2"
            self.legacy = f"{base}/v2/api"
            self.legacy_params = {"chain_id": self.chain_id, "apikey": api_key}
            self.rest_params = {"apikey": api_key}
        else:
            web = (ex.get("web") or "").rstrip("/")
            self.rest = f"{web}/api/v2"
            self.legacy = f"{web}/api"
            self.legacy_params = {}
            self.rest_params = {}
        self.web = (ex.get("web") or "").rstrip("/")

    # ---------------------------------------------------------------- raw
    def _rest(self, path: str, params: Optional[dict] = None, ttl: int = 60) -> Any:
        p = dict(self.rest_params)
        if params:
            p.update(params)
        return self.http.get_json(self.rest + path, params=p or None, ttl=ttl)

    def _legacy(self, params: dict, ttl: int = 60) -> Any:
        p = dict(self.legacy_params)
        p.update(params)
        payload = self.http.get_json(self.legacy, params=p, ttl=ttl)
        if isinstance(payload, dict):
            res = payload.get("result")
            if isinstance(res, list):
                return res
            if payload.get("status") == "0" and isinstance(res, str):
                return []           # "No transactions found"
            return res
        return payload

    # ---------------------------------------------------------------- token
    def token(self, token: str) -> Dict[str, Any]:
        d = self._rest(f"/tokens/{norm_addr(token)}", ttl=300) or {}
        return {
            "address": norm_addr(token),
            "name": d.get("name"), "symbol": d.get("symbol"),
            "decimals": safe_int(d.get("decimals"), 18),
            "total_supply": safe_int(d.get("total_supply")),
            "holders_count": safe_int(d.get("holders") or d.get("holders_count")),
            "type": d.get("type"),
        }

    def token_counters(self, token: str) -> Dict[str, int]:
        d = self._rest(f"/tokens/{norm_addr(token)}/counters", ttl=120) or {}
        return {"holders": safe_int(d.get("token_holders_count")), "transfers": safe_int(d.get("transfers_count"))}

    def token_holders(self, token: str, limit: int = 50, total_supply: Optional[int] = None) -> List[HolderInfo]:
        out: List[HolderInfo] = []
        params: Dict[str, Any] = {}
        supply = total_supply
        while len(out) < limit:
            payload = self._rest(f"/tokens/{norm_addr(token)}/holders", params or None, ttl=120) or {}
            items = payload.get("items") or []
            if not items:
                break
            for it in items:
                a = it.get("address") or {}
                bal = safe_int(it.get("value"))
                if not supply:
                    supply = safe_int(((it.get("token") or {}).get("total_supply")))
                pct = (bal / supply * 100.0) if supply else 0.0
                out.append(HolderInfo(
                    address=norm_addr(a.get("hash") or ""),
                    balance_raw=bal, pct_supply=round(pct, 4),
                    is_contract=bool(a.get("is_contract")),
                    label=(a.get("name") or a.get("implementation_name") or "") or "",
                ))
                if len(out) >= limit:
                    break
            nxt = payload.get("next_page_params")
            if not nxt or len(out) >= limit:
                break
            params = dict(nxt)
        return out

    def token_transfer_logs_asc(self, token: str, from_block: int, to_block: Any = "latest", limit: int = 1000) -> List[Dict[str, Any]]:
        """按区块升序的 Transfer 日志（本链最多 1000 条）——用于识别最早的买家/接收方。"""
        rows = self._legacy({"module": "logs", "action": "getLogs", "fromBlock": int(from_block),
                             "toBlock": to_block, "address": norm_addr(token), "topic0": TRANSFER_TOPIC}, ttl=600) or []
        out = []
        for r in rows[:limit]:
            topics = r.get("topics") or []
            if len(topics) < 3:
                continue
            out.append({
                "block": _hex_or_int(r.get("blockNumber")),
                "ts": _hex_or_int(r.get("timeStamp")),
                "from": "0x" + topics[1][-40:],
                "to": "0x" + topics[2][-40:],
                "value": _hex_or_int(r.get("data")),
                "tx": r.get("transactionHash"),
            })
        return out

    # ---------------------------------------------------------------- address
    def address(self, addr: str) -> Dict[str, Any]:
        d = self._rest(f"/addresses/{norm_addr(addr)}", ttl=3600) or {}
        return {
            "address": norm_addr(addr),
            "is_contract": bool(d.get("is_contract")),
            "is_verified": d.get("is_verified"),
            "name": d.get("name") or d.get("implementation_name") or "",
            "creator": norm_addr(d.get("creator_address_hash") or ""),
            "creation_tx": d.get("creation_transaction_hash") or d.get("creation_tx_hash") or "",
            "is_scam": bool(d.get("is_scam")),
            "tags": [t.get("display_name") for t in (d.get("public_tags") or []) if isinstance(t, dict)],
        }

    def address_counters(self, addr: str) -> Dict[str, int]:
        d = self._rest(f"/addresses/{norm_addr(addr)}/counters", ttl=600) or {}
        return {"transactions": safe_int(d.get("transactions_count")),
                "token_transfers": safe_int(d.get("token_transfers_count"))}

    def first_txs(self, addr: str, n: int = 3) -> List[Dict[str, Any]]:
        rows = self._legacy({"module": "account", "action": "txlist", "address": norm_addr(addr),
                             "sort": "asc", "page": 1, "offset": n}, ttl=86400) or []
        out = []
        for r in rows[:n]:
            out.append({
                "hash": r.get("hash"), "ts": safe_int(r.get("timeStamp")),
                "from": norm_addr(r.get("from") or ""), "to": norm_addr(r.get("to") or ""),
                "value": safe_int(r.get("value")), "block": safe_int(r.get("blockNumber")),
            })
        return out

    def smart_contract(self, addr: str) -> Dict[str, Any]:
        d = self._rest(f"/smart-contracts/{norm_addr(addr)}", ttl=3600) or {}
        return {"is_verified": bool(d.get("is_verified") or d.get("is_fully_verified") or d.get("abi")),
                "name": d.get("name") or "", "is_proxy": bool(d.get("proxy_type")),
                "compiler": d.get("compiler_version") or ""}

    def tx(self, tx_hash: str) -> Dict[str, Any]:
        d = self._rest(f"/transactions/{tx_hash}", ttl=86400) or {}
        return {"block": safe_int(d.get("block") or d.get("block_number")),
                "from": norm_addr(((d.get("from") or {}).get("hash")) or ""),
                "timestamp": d.get("timestamp")}

    def url_token(self, token: str) -> str:
        return f"{self.web}/token/{token}"

    def url_address(self, addr: str) -> str:
        return f"{self.web}/address/{addr}"
