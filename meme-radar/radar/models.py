# -*- coding: utf-8 -*-
"""models.py — 贯穿各层的数据结构（全部可 JSON 序列化）。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


def _d(obj) -> dict:
    return asdict(obj)


@dataclass
class PoolSnapshot:
    """一个交易对在某一时刻的快照（GeckoTerminal / DexScreener 归一化后）。"""
    chain: str
    pool_address: str
    dex: str = ""
    base_token: str = ""
    base_symbol: str = ""
    base_name: str = ""
    quote_token: str = ""
    quote_symbol: str = ""
    price_usd: Optional[float] = None
    fdv_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_usd: Dict[str, Optional[float]] = field(default_factory=dict)      # m5/h1/h6/h24
    price_change_pct: Dict[str, Optional[float]] = field(default_factory=dict)
    txns: Dict[str, Dict[str, Optional[int]]] = field(default_factory=dict)   # h1: {buys,sells,buyers,sellers}
    pool_created_at: Optional[str] = None
    age_hours: Optional[float] = None
    source: str = ""
    url: str = ""
    info: Dict[str, Any] = field(default_factory=dict)
    observed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return _d(self)

    # 便捷取数 ----------------------------------------------------------
    def vol(self, w: str) -> float:
        return float(self.volume_usd.get(w) or 0.0)

    def chg(self, w: str) -> Optional[float]:
        return self.price_change_pct.get(w)

    def tx(self, w: str, k: str) -> int:
        return int((self.txns.get(w) or {}).get(k) or 0)


@dataclass
class WalletProfile:
    address: str
    is_contract: bool = False
    first_tx_at: Optional[str] = None
    age_hours: Optional[float] = None
    first_funder: Optional[str] = None
    first_funder_kind: str = ""          # eoa / contract / bridge / unknown
    tx_count: Optional[int] = None
    token_transfers_count: Optional[int] = None
    label: str = ""
    fetched_at: Optional[str] = None
    quality: str = "none"               # full / partial / none

    def to_dict(self) -> dict:
        return _d(self)


@dataclass
class HolderInfo:
    address: str
    balance_raw: int = 0
    pct_supply: float = 0.0
    is_contract: bool = False
    label: str = ""
    kind: str = "eoa"                   # eoa / pool / curve / locker / burn / contract / creator

    def to_dict(self) -> dict:
        return _d(self)


@dataclass
class ForensicsResult:
    token: str
    quality: str = "none"                # full / partial / none
    holders_total: Optional[int] = None
    inspected: int = 0
    profiled: int = 0                    # 拿到完整画像（年龄+打款方+计数）的钱包数
    contract_held_pct: float = 0.0
    burn_pct: float = 0.0
    creator_pct: float = 0.0
    creator_address: str = ""
    top10_eoa_pct: float = 0.0
    top1_eoa_pct: float = 0.0
    clusters: List[Dict[str, Any]] = field(default_factory=list)
    clustered_pct: float = 0.0
    largest_cluster_pct: float = 0.0
    fresh_wallet_pct: float = 0.0
    fresh_wallet_count: int = 0
    inspected_pct: float = 0.0           # 被检查的 EOA 合计持仓占比
    early_buyers_holding_pct: Optional[float] = None
    early_buyers: List[str] = field(default_factory=list)
    holder_map: List[Dict[str, Any]] = field(default_factory=list)   # 看板星图：前排 EOA [{a,p,c,f,age}]
    sybil_score: float = 0.0             # 0 = 健康, 1 = 几乎肯定一人多号
    launchpad: str = ""                  # pons_v2 / pons_v1 / pools_trade / ""
    curve_status: str = ""               # on_curve / graduated / ""
    notes: List[str] = field(default_factory=list)
    calls_used: int = 0

    def to_dict(self) -> dict:
        return _d(self)


@dataclass
class SmartMoneySignal:
    count: int = 0
    weighted: float = 0.0
    net_buy_usd: float = 0.0
    wallets: List[Dict[str, Any]] = field(default_factory=list)
    registry_size: int = 0

    def to_dict(self) -> dict:
        return _d(self)


@dataclass
class SecurityInfo:
    token: str
    source: str = "none"                 # goplus / onchain / pons_template / none
    is_honeypot: Optional[bool] = None
    buy_tax_pct: Optional[float] = None
    sell_tax_pct: Optional[float] = None
    is_mintable: Optional[bool] = None
    is_proxy: Optional[bool] = None
    is_verified: Optional[bool] = None
    has_owner: Optional[bool] = None
    owner: str = ""
    launchpad: str = ""
    flags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _d(self)


@dataclass
class AiVerdict:
    verdict: str = "UNKNOWN"             # REAL_MARKET / MIXED / SUSPICIOUS / MANIPULATED / UNKNOWN
    confidence: float = 0.0
    key_evidence: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    what_would_change_mind: str = ""
    provider: str = "rules"
    model: str = ""

    def to_dict(self) -> dict:
        return _d(self)


@dataclass
class Candidate:
    token: str
    symbol: str
    name: str
    pool: str
    snapshot: PoolSnapshot
    security: Optional[SecurityInfo] = None
    forensics: Optional[ForensicsResult] = None
    smart_money: Optional[SmartMoneySignal] = None
    flags: Dict[str, List[str]] = field(default_factory=lambda: {"red": [], "yellow": [], "green": []})
    score_total: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    killed_by: List[str] = field(default_factory=list)
    decision: str = "SKIP"               # SKIP / WATCH / PAPER_BUY / BASELINE
    decision_reasons: List[str] = field(default_factory=list)
    ai: Optional[AiVerdict] = None
    position_size_usd: float = 0.0
    features: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "token": self.token, "symbol": self.symbol, "name": self.name, "pool": self.pool,
            "snapshot": self.snapshot.to_dict(),
            "security": self.security.to_dict() if self.security else None,
            "forensics": self.forensics.to_dict() if self.forensics else None,
            "smart_money": self.smart_money.to_dict() if self.smart_money else None,
            "flags": self.flags, "score_total": round(self.score_total, 1),
            "score_breakdown": {k: round(v, 1) for k, v in self.score_breakdown.items()},
            "killed_by": self.killed_by, "decision": self.decision,
            "decision_reasons": self.decision_reasons,
            "ai": self.ai.to_dict() if self.ai else None,
            "position_size_usd": round(self.position_size_usd, 2),
            "features": self.features,
        }
