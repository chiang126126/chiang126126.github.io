# -*- coding: utf-8 -*-
"""pons.py — Pons 发射台识别（Robinhood Chain 的 pump.fun 类平台）。

不依赖精确 ABI：
  1) 代币合约创建者 ∈ {Pons V1/V2 工厂、部署器}  → pons_v1 / pons_v2
  2) 已验证合约名含 PonsV2LauncherToken / PonsLauncherToken → 同上
  3) 持有人里出现名为 *BondingCurve* 的合约且仍持仓 → on_curve；出现 *Locker* → graduated
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..models import HolderInfo
from ..util import norm_addr


class Pons:
    def __init__(self, chain_cfg: Dict[str, Any]):
        lp = (chain_cfg.get("launchpads") or {}).get("pons") or {}
        self.v1 = {norm_addr(lp.get("v1_factory", ""))} - {""}
        self.v2 = {norm_addr(lp.get(k, "")) for k in ("v2_factory", "v2_deployer", "v2_router")} - {""}
        self.hook = norm_addr(lp.get("v2_meme_hook", ""))
        pt = (chain_cfg.get("launchpads") or {}).get("pools_trade") or {}
        self.pools_trade = {norm_addr(pt.get("factory", ""))} - {""}

    def detect(self, creator: str, contract_name: str = "") -> str:
        c = norm_addr(creator)
        n = (contract_name or "").lower()
        if c in self.v2 or "ponsv2" in n:
            return "pons_v2"
        if c in self.v1 or "ponslaunchertoken" in n:
            return "pons_v1"
        if c in self.pools_trade:
            return "pools_trade"
        return ""

    @staticmethod
    def curve_status(holders: List[HolderInfo]) -> str:
        names = [(h.label or "").lower() for h in holders if h.is_contract]
        if any("bondingcurve" in n or "curve" in n for n in names):
            return "on_curve"
        if any("locker" in n or "graduat" in n for n in names):
            return "graduated"
        return ""

    @staticmethod
    def classify_holder(h: HolderInfo, pool_addresses: set, burn: set, creator: str) -> str:
        a = norm_addr(h.address)
        n = (h.label or "").lower()
        if a in burn:
            return "burn"
        if a in pool_addresses or "pool" in n or "uniswap" in n or "poolmanager" in n:
            return "pool"
        if "curve" in n:
            return "curve"
        if "locker" in n or "vault" in n or "lock" in n:
            return "locker"
        if creator and a == norm_addr(creator):
            return "creator"
        if h.is_contract:
            return "contract"
        return "eoa"
