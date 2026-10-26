# -*- coding: utf-8 -*-
"""ai.py — AI 只回答一个问题：『这到底是不是一个真实的市场？』

不预测明天涨多少。输入是结构化证据文档（快照 + 取证 + 交叉验证 + 聪明钱 + 体制），
输出严格 JSON：verdict ∈ {REAL_MARKET, MIXED, SUSPICIOUS, MANIPULATED}、confidence、关键证据、红旗、
"什么证据会改变判断"。AI 只有否决权（MANIPULATED 且 confidence ≥ 阈值 → 不开模拟仓），没有发起权。

Provider（全部可选，缺 key 自动退回规则版）：
  LLM_PROVIDER=deepseek  LLM_API_KEY=...  [LLM_MODEL=deepseek-chat]
  LLM_PROVIDER=openai    LLM_API_KEY=...  [LLM_MODEL=gpt-4o-mini] [LLM_BASE_URL=...]
  LLM_PROVIDER=anthropic LLM_API_KEY=...  [LLM_MODEL=claude-sonnet-5]
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from .models import AiVerdict, Candidate
from .util import CACHE_DIR, day_key, load_json, save_json

SYSTEM_PROMPT = (
    "你是链上市场真实性审查员。你的任务不是预测价格，而是判断一个新发行的代币背后是否存在真实、分散、独立的市场参与者，"
    "还是由少数关联钱包制造出的虚假繁荣。只依据给定证据，不要臆造链上事实。"
    "严格输出 JSON（不要 markdown），字段：verdict(REAL_MARKET|MIXED|SUSPICIOUS|MANIPULATED)、"
    "confidence(0~1)、key_evidence(字符串数组，≤5 条)、red_flags(字符串数组)、what_would_change_mind(一句话)。"
)

DEFAULT_MODELS = {"deepseek": "deepseek-chat", "openai": "gpt-4o-mini", "anthropic": "claude-sonnet-5"}
DEFAULT_BASE = {"deepseek": "https://api.deepseek.com", "openai": "https://api.openai.com/v1",
                "anthropic": "https://api.anthropic.com"}


def evidence_document(c: Candidate, regime: Dict[str, Any], cross_metrics: Optional[Dict[str, Any]] = None) -> str:
    s = c.snapshot
    fo, sm, se = c.forensics, c.smart_money, c.security
    lines = [
        f"# 候选代币 {c.symbol} ({c.name}) — {s.chain} / {s.dex}",
        f"token: {c.token}",
        f"pool: {c.pool}",
        f"发现时间: {s.observed_at}；池子年龄: {s.age_hours:.1f}h" if s.age_hours is not None else f"发现时间: {s.observed_at}",
        "",
        "## 市场环境",
        f"体制: {regime.get('regime')}（{regime.get('regime_zh')}），风险预算 {regime.get('risk_budget')}；{regime.get('judgment', '')}",
        "",
        "## 交易快照",
        f"价格 ${s.price_usd}；流动性 ${s.liquidity_usd:,.0f}；FDV ${(s.fdv_usd or 0):,.0f}" if s.liquidity_usd else f"价格 ${s.price_usd}",
        f"成交量 1h ${s.vol('h1'):,.0f} / 6h ${s.vol('h6'):,.0f} / 24h ${s.vol('h24'):,.0f}",
        f"涨跌 1h {s.chg('h1')}% / 6h {s.chg('h6')}% / 24h {s.chg('h24')}%",
        f"交易 1h 买{s.tx('h1', 'buys')}/卖{s.tx('h1', 'sells')}（独立买家 {s.txns.get('h1', {}).get('buyers')}）；"
        f"24h 买{s.tx('h24', 'buys')}/卖{s.tx('h24', 'sells')}（独立买家 {s.txns.get('h24', {}).get('buyers')}）",
        f"社交/网站: {(s.info or {}).get('socials') or []} {(s.info or {}).get('websites') or []}；付费推广 boosts: {(s.info or {}).get('boosts_active') or 0}",
    ]
    if cross_metrics:
        lines += ["", "## 最近成交（钱包级）",
                  f"样本 {cross_metrics.get('trades_n')} 笔；1h 净流入 ${cross_metrics.get('net_flow_1h_usd')}（占流动性 {cross_metrics.get('net_flow_1h_to_liq')}）；"
                  f"独立买家(1h) {cross_metrics.get('unique_buyers_1h')}，独立买家(全部) {cross_metrics.get('unique_buyers_trades')}；"
                  f"前 5 买家占买入额 {cross_metrics.get('top5_buyer_share')}；中位单笔 ${cross_metrics.get('median_trade_usd')}"]
    if se:
        lines += ["", "## 合约安全", f"来源 {se.source}；发射台 {se.launchpad or '-'}；honeypot {se.is_honeypot}；买/卖税 {se.buy_tax_pct}/{se.sell_tax_pct}%；"
                                     f"可增发 {se.is_mintable}；owner {se.has_owner}；已验证 {se.is_verified}；标记 {se.flags}"]
    if fo:
        lines += ["", "## 钱包取证",
                  f"质量 {fo.quality}；持有人总数 {fo.holders_total}；检查前 {fo.inspected} 个 EOA（合计 {fo.inspected_pct}% 供应）",
                  f"合约/池子持有 {fo.contract_held_pct}%；销毁 {fo.burn_pct}%；创建者 {fo.creator_pct}%；前 10 EOA {fo.top10_eoa_pct}%；最大单一 {fo.top1_eoa_pct}%",
                  f"关联簇 {len(fo.clusters)} 个，合计 {fo.clustered_pct}%，最大簇 {fo.largest_cluster_pct}%；新钱包 {fo.fresh_wallet_count} 个（{fo.fresh_wallet_pct}%）；"
                  f"最早买家仍持有 {fo.early_buyers_holding_pct}%；sybil_score {fo.sybil_score}；发射台 {fo.launchpad or '-'} {fo.curve_status}"]
        for cl in fo.clusters[:5]:
            lines.append(f"  - 簇: {cl['size']} 个钱包，持 {cl['pct']}%，原因 {cl['reasons']}")
        for n in fo.notes:
            lines.append(f"  - {n}")
    if sm:
        lines += ["", "## 聪明钱", f"库规模 {sm.registry_size}；共振 {sm.count} 个（加权 {sm.weighted}），净买入 ${sm.net_buy_usd}"]
        for w in sm.wallets[:5]:
            lines.append(f"  - {w['address'][:10]}… score {w['score']} {w['side']} ${w['net_usd']} label {w.get('label')}")
    lines += ["", "## 规则层结论",
              f"评分 {c.score_total} = {c.score_breakdown}",
              f"红旗 {c.flags.get('red')}；黄旗 {c.flags.get('yellow')}；绿旗 {c.flags.get('green')}",
              f"硬过滤 {c.killed_by or '通过'}；决策 {c.decision}：{c.decision_reasons}"]
    return "\n".join(lines)


def rule_verdict(c: Candidate) -> AiVerdict:
    """无 LLM 时的确定性替代：完全由旗帜与取证推导。"""
    red, yellow, green = c.flags.get("red", []), c.flags.get("yellow", []), c.flags.get("green", [])
    fo = c.forensics
    v = AiVerdict(provider="rules")
    score = 0.0
    score += 0.35 * len(red) + 0.12 * len(yellow) - 0.12 * len(green)
    if fo and fo.quality != "none":
        score += fo.sybil_score * 0.8
    if score >= 0.9:
        v.verdict, v.confidence = "MANIPULATED", min(0.95, 0.6 + 0.15 * len(red))
    elif score >= 0.5:
        v.verdict, v.confidence = "SUSPICIOUS", 0.6
    elif score <= 0.0 and (fo is None or fo.quality == "none"):
        v.verdict, v.confidence = "MIXED", 0.4
    elif score <= 0.0:
        v.verdict, v.confidence = "REAL_MARKET", 0.65
    else:
        v.verdict, v.confidence = "MIXED", 0.5
    v.red_flags = list(red) + list(yellow)
    v.key_evidence = list(green)[:5]
    v.what_would_change_mind = "取证质量为 full 且无关联簇、独立买家持续增加，或出现流动性抽走/买盘集中。"
    return v


class AiJudge:
    def __init__(self, env, http, max_calls: int = 8):
        self.provider = env.llm_provider
        self.key = env.llm_api_key
        self.model = env.llm_model or DEFAULT_MODELS.get(self.provider, "")
        self.base = (env.llm_base_url or DEFAULT_BASE.get(self.provider, "")).rstrip("/")
        self.http = http
        self.max_calls = max_calls
        self.calls = 0
        self.cache_dir = CACHE_DIR / "ai"

    @property
    def enabled(self) -> bool:
        return self.provider in DEFAULT_BASE and bool(self.key) and self.http is not None

    def _cache_path(self, token: str):
        return self.cache_dir / f"{day_key()}_{token}.json"

    def _chat(self, system: str, user: str) -> str:
        if self.provider == "anthropic":
            body = {"model": self.model, "max_tokens": 800, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            resp = self.http.post_json(f"{self.base}/v1/messages", body,
                                       headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"})
            parts = (resp or {}).get("content") or []
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        body = {"model": self.model, "temperature": 0.1, "max_tokens": 800,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.provider in ("openai", "deepseek"):
            body["response_format"] = {"type": "json_object"}
        resp = self.http.post_json(f"{self.base}/chat/completions", body, headers={"Authorization": f"Bearer {self.key}"})
        return (((resp or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    @staticmethod
    def parse(text: str) -> Optional[AiVerdict]:
        if not text:
            return None
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return None
        verdict = str(d.get("verdict") or "").upper()
        if verdict not in ("REAL_MARKET", "MIXED", "SUSPICIOUS", "MANIPULATED"):
            return None
        conf = d.get("confidence")
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.5
        return AiVerdict(verdict=verdict, confidence=round(conf, 2),
                         key_evidence=[str(x) for x in (d.get("key_evidence") or [])][:5],
                         red_flags=[str(x) for x in (d.get("red_flags") or [])][:8],
                         what_would_change_mind=str(d.get("what_would_change_mind") or "")[:300])

    def judge(self, c: Candidate, regime: Dict[str, Any], cross_metrics: Optional[Dict[str, Any]] = None) -> AiVerdict:
        cached = load_json(self._cache_path(c.token))
        if isinstance(cached, dict) and cached.get("verdict"):
            try:
                return AiVerdict(**{k: cached.get(k) for k in AiVerdict.__dataclass_fields__ if k in cached})
            except TypeError:
                pass
        if not self.enabled or self.calls >= self.max_calls:
            return rule_verdict(c)
        doc = evidence_document(c, regime, cross_metrics)
        try:
            self.calls += 1
            text = self._chat(SYSTEM_PROMPT, doc)
            v = self.parse(text)
        except Exception:
            v = None
        if v is None:
            v = rule_verdict(c)
            v.provider = f"rules(fallback:{self.provider})"
        else:
            v.provider, v.model = self.provider, self.model
        try:
            save_json(self._cache_path(c.token), v.to_dict())
        except OSError:
            pass
        return v
