"""
L1 GoPlus Security API 适配器。

实现以下 Protocol：
  - TokenAuditProvider  → evaluate_long_safety / evaluate_short_safety
  - LiquidityProvider   → L3 安全闸流动性检查
  - HolderProvider      → compute_chip_features（简化版：只有 top10，无 BFS）

GoPlus 免费层：无需 API key，限速 ~10 req/s。
有 key 则高限速，在 .env 里配置 GOPLUS_API_KEY 即可（可留空）。

⚠️ 已知简化（v0）：
  - HolderProvider 只返回 top-10 持有人（GoPlus 只提供这些）
  - chip.py 的 cluster_factor / funder BFS 不可用（无 TransferProvider）
  - 这意味着 compute_chip_features 的 cluster_factor 结果不可信
  - 使用前确认 pool_router 的 cluster 阈值已调宽，或直接跳过 chip，
    只用 top10_pct 作为代理指标
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import requests

from sprite_catcher.models import (
    DevWalletInfo,
    HolderSnapshot,
    LiquidityInfo,
    TokenAuditInfo,
)

_BASE = "https://api.gopluslabs.io/api/v1"
_TIMEOUT = 15
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 2, 4]

# GoPlus chain id 映射
_CHAIN_IDS = {
    "ETH": "1",
    "BSC": "56",
    "ARB": "42161",
    "BASE": "8453",
    "POLYGON": "137",
    "AVAX": "43114",
}


def _get(url: str, params: dict | None = None) -> Any:
    api_key = os.getenv("GOPLUS_API_KEY", "")
    headers = {"Authorization": api_key} if api_key else {}
    last_exc: Exception | None = None
    for wait in ([0] + _RETRY_BACKOFF):
        if wait:
            time.sleep(wait)
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 1:
                raise RuntimeError(f"GoPlus error {body.get('code')}: {body.get('message')}")
            return body["result"]
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"GoPlus GET {url} failed: {last_exc}") from last_exc


def _token_security(chain: str, contract: str) -> dict:
    """拉取 GoPlus token_security 端点，返回第一个合约的数据。"""
    chain_id = _CHAIN_IDS.get(chain.upper())
    if not chain_id:
        raise ValueError(f"Unsupported chain: {chain}")
    result = _get(f"{_BASE}/token_security/{chain_id}", params={"contract_addresses": contract})
    data = result.get(contract.lower()) or result.get(contract)
    if not data:
        raise RuntimeError(f"GoPlus returned no data for {chain}:{contract}")
    return data


# ── TokenAuditProvider ────────────────────────────────────────────────────────

class GoPlusTokenAuditProvider:
    """
    实现 sprite_catcher.interfaces.TokenAuditProvider。
    """

    def get_token_audit(self, token: str) -> TokenAuditInfo:
        """
        token 格式："{CHAIN}:{contract_address}"
        例："BSC:0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
        """
        chain, contract = token.split(":", 1)
        d = _token_security(chain, contract)

        return TokenAuditInfo(
            mintable=_bool(d.get("is_mintable")),
            freezeable=_bool(d.get("transfer_pausable")),  # GoPlus 用 transfer_pausable
            pausable=_bool(d.get("transfer_pausable")),
            buy_tax=float(d.get("buy_tax") or 0),
            sell_tax=float(d.get("sell_tax") or 0),
            is_honeypot=_bool(d.get("is_honeypot")),
            top10_holder_pct=float(d.get("holder_count") and _safe_top10_pct(d) or 0),
        )


def _safe_top10_pct(d: dict) -> float:
    """GoPlus holders 列表里取 top10 的总持仓比例。"""
    holders = d.get("holders") or []
    if not holders:
        return 0.0
    pcts = [float(h.get("percent", 0)) for h in holders[:10]]
    return sum(pcts)


# ── LiquidityProvider ────────────────────────────────────────────────────────

class GoPlusLiquidityProvider:
    """
    实现 sprite_catcher.interfaces.LiquidityProvider。

    ⚠️ 已知限制：GoPlus 提供 LP holder 比例，但不提供精确的
       liquidity_usd 和 lp_lock_remaining_days。
       - liquidity_usd 用 0（调用方 safety_gate 应只在 liquidity_usd > 0 时
         校验流动性下限，否则跳过该检查）。
       - lp_lock_remaining_days 用 0（最保守值，会触发安全闸拒绝长持 token）。
       如果需要精确值，接入 DexScreener API（免费，见 TODO）。
    """

    def get_liquidity_info(self, token: str) -> LiquidityInfo:
        chain, contract = token.split(":", 1)
        d = _token_security(chain, contract)

        lp_holders = d.get("lp_holders") or []
        locked_pct = sum(
            float(h.get("percent", 0))
            for h in lp_holders
            if _bool(h.get("is_locked"))
        )
        pool_age = int(d.get("token_age_days") or 0) if d.get("token_age_days") else 0

        return LiquidityInfo(
            liquidity_usd=0.0,            # GoPlus 不提供，用 DexScreener 补充
            lp_locked_pct=locked_pct,
            lp_lock_remaining_days=0,     # GoPlus 不提供精确剩余天数
            pool_age_days=pool_age,
        )


# ── HolderProvider（简化版）────────────────────────────────────────────────

class GoPlusHolderProvider:
    """
    实现 sprite_catcher.interfaces.HolderProvider。

    ⚠️ 简化版：只返回 GoPlus 提供的 top-10 持有人（非 top-200）。
    chip.py 的 cluster_factor 依赖全量持有人分布，结果不可信。
    建议：只用 top10_pct 作为筹码集中度代理指标，放弃 cluster_factor 检查。
    """

    def get_holders(self, token: str, *, top_n: int = 200) -> list[HolderSnapshot]:
        chain, contract = token.split(":", 1)
        d = _token_security(chain, contract)
        holders = d.get("holders") or []
        total_supply = float(d.get("total_supply") or 1)

        snapshots = []
        for h in holders[:top_n]:
            pct = float(h.get("percent", 0))
            balance = Decimal(str(pct * total_supply))
            snapshots.append(HolderSnapshot(address=h.get("address", ""), balance=balance))
        return snapshots

    def get_circulating_supply(self, token: str) -> float:
        chain, contract = token.split(":", 1)
        d = _token_security(chain, contract)
        supply = float(d.get("total_supply") or 0)
        if supply <= 0:
            raise ValueError(f"GoPlus returned non-positive supply for {token}")
        return supply


# ── DevWalletProvider（最简版）──────────────────────────────────────────────

class GoPlusDevWalletProvider:
    """
    实现 sprite_catcher.interfaces.DevWalletProvider。

    ⚠️ 简化版：GoPlus 提供 owner_address 和 is_open_source，
       不提供 dev 历史部署数或 rug 记录。
       rug_history 和 prior_deploys 暂时置为保守值。
       后续可接入 Blocksec / De.Fi 补充。
    """

    def get_dev_wallet_info(self, token: str) -> DevWalletInfo:
        chain, contract = token.split(":", 1)
        d = _token_security(chain, contract)

        return DevWalletInfo(
            deployer_address=d.get("owner_address") or "",
            prior_deploys=0,          # GoPlus 不提供，暂置 0
            has_rug_history=False,    # GoPlus 不提供，暂置 False（乐观）
            best_prior_market_cap_usd=0.0,
        )


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _bool(val: Any) -> bool:
    """GoPlus 用 "1"/"0" 表示布尔值。"""
    return str(val) == "1"
