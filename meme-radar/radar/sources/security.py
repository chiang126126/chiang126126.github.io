# -*- coding: utf-8 -*-
"""security.py — 合约安全检查：GoPlus（若支持本链）+ 链上启发式兜底。

Pons 模板代币（固定 10 亿供应、无 owner/mint）本身就是最强的安全信号之一。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..models import SecurityInfo
from ..util import norm_addr, safe_float

GOPLUS = "https://api.gopluslabs.io/api/v1/token_security"
ZERO = "0x0000000000000000000000000000000000000000"


class Security:
    def __init__(self, http_goplus, blockscout, rpc, chain_id: int, pons):
        self.goplus = http_goplus
        self.bs = blockscout
        self.rpc = rpc
        self.chain_id = chain_id
        self.pons = pons
        self.goplus_supported: Optional[bool] = None

    def _goplus(self, token: str) -> Optional[Dict[str, Any]]:
        if self.goplus is None or self.goplus_supported is False:
            return None
        try:
            payload = self.goplus.get_json(f"{GOPLUS}/{self.chain_id}", {"contract_addresses": token}, ttl=1800)
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("code") not in (1, "1"):
            self.goplus_supported = False
            return None
        res = (payload.get("result") or {}).get(norm_addr(token)) or (payload.get("result") or {}).get(token)
        if not res:
            self._empty = getattr(self, "_empty", 0) + 1
            if self._empty >= 3 and self.goplus_supported is None:
                self.goplus_supported = False      # 连续空结果：本链大概率未被 GoPlus 索引，不再浪费调用
            return None
        self.goplus_supported = True
        return res

    def check(self, token: str, address_info: Optional[Dict[str, Any]] = None, launchpad_hint: str = "") -> SecurityInfo:
        token = norm_addr(token)
        info = SecurityInfo(token=token)
        addr = address_info or {}
        launchpad = (self.pons.detect(addr.get("creator", ""), addr.get("name", "")) if self.pons else "") or launchpad_hint
        info.launchpad = launchpad
        info.is_verified = addr.get("is_verified")
        if addr.get("is_scam"):
            info.flags.append("explorer_scam_tag")

        gp = self._goplus(token)
        if gp:
            info.source = "goplus"
            info.is_honeypot = gp.get("is_honeypot") == "1"
            info.buy_tax_pct = (safe_float(gp.get("buy_tax"), 0.0) or 0.0) * 100
            info.sell_tax_pct = (safe_float(gp.get("sell_tax"), 0.0) or 0.0) * 100
            info.is_mintable = gp.get("is_mintable") == "1"
            info.is_proxy = gp.get("is_proxy") == "1"
            owner = norm_addr(gp.get("owner_address") or "")
            info.owner = owner
            info.has_owner = bool(owner) and owner != ZERO
            if gp.get("is_open_source") == "0":
                info.flags.append("closed_source")
            for k in ("cannot_sell_all", "transfer_pausable", "is_blacklisted", "hidden_owner",
                      "can_take_back_ownership", "selfdestruct", "trading_cooldown", "slippage_modifiable",
                      "personal_slippage_modifiable", "owner_change_balance", "cannot_buy"):
                if gp.get(k) == "1":
                    info.flags.append(k)
            if info.is_honeypot:
                info.flags.append("honeypot")
            if info.is_mintable:
                info.flags.append("mintable")
            return info

        # ---- 链上兜底
        if launchpad.startswith("pons"):
            info.source = "pons_template"
            info.is_honeypot = False
            info.is_mintable = False
            info.has_owner = False
            info.buy_tax_pct = 0.0
            info.sell_tax_pct = 0.0
            info.flags.append("pons_template")
            return info

        info.source = "onchain"
        if self.rpc is not None:
            owner = self.rpc.owner(token)
            if owner and owner != ZERO:
                info.has_owner = True
                info.owner = owner
                info.flags.append("has_owner")
            elif owner is not None:
                info.has_owner = False
        if info.is_verified is False:
            info.flags.append("unverified_contract")
        return info
