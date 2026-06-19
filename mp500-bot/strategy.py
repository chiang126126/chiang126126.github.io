"""战略层：构造 Evidence → 调 LLM 出决策元组（带规则兜底）。"""
import json

import requests

import exchange
import indicators

SYSTEM_PROMPT = (
    "你是 MP500 加密交易系统的战略分析师。基于给定的 Evidence（指标/资金费率/情绪），"
    "对该标的给出一个保守的短线决策。规则：只允许做多或观望（不做空）；"
    "行情不明确时必须 FLAT（空仓也是决策）；必须给出止损；风控优先于收益。\n"
    "只返回严格 JSON，字段：\n"
    '{"bias":"LONG|FLAT","confidence":0.0-1.0,"stop_pct":正数(止损距入场的百分比,如2.0表示2%),'
    '"target_pct":正数(止盈距入场的百分比),"rationale":"简述形态/理由","risk_flags":["风险点"]}'
)


def build_evidence(symbol, ind, funding, fng):
    fng_v, fng_c = fng
    return (
        f"标的: {symbol}\n"
        f"现价: {ind['price']:.2f}\n"
        f"30小时均线偏离: {ind['dev_pct']:.2f}%  (站上为偏多)\n"
        f"EMA21: {ind['ema21']:.2f}  EMA50: {ind['ema50']:.2f}\n"
        f"RSI14: {ind['rsi14']:.1f}\n"
        f"ATR(1h): {ind['atr_pct']:.2f}% (波动率)\n"
        f"资金费率: {('%.4f%%/8h' % funding) if funding is not None else 'n/a'}\n"
        f"恐惧贪婪: {fng_v if fng_v is not None else 'n/a'} ({fng_c or 'n/a'})\n"
    )


def llm_decide(provider, api_key, model, evidence):
    base = "https://api.deepseek.com" if provider == "deepseek" else "https://api.openai.com/v1"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": evidence},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(f"{base}/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                      json=body, timeout=60)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def rule_decide(ind, funding, fng):
    """确定性兜底：LLM 不可用或失败时使用。与看板 computeDecision 同源逻辑。"""
    dev = ind["dev_pct"]
    bias, conf, flags = "FLAT", 0.3, []
    if dev is not None and dev >= 1 and ind["rsi14"] and ind["rsi14"] < 72:
        bias, conf = "LONG", min(0.7, 0.5 + abs(dev) / 50)
    fng_v = fng[0]
    if fng_v is not None and fng_v >= 78:
        flags.append("极度贪婪，警惕追高")
        conf -= 0.1
    if funding is not None and abs(funding) > 0.05:
        flags.append("资金费率偏高")
        conf -= 0.1
    conf = max(0.1, min(0.9, conf))
    stop_pct = max(1.5, (ind["atr_pct"] or 2) * 1.5)
    return {"bias": bias, "confidence": round(conf, 2), "stop_pct": round(stop_pct, 2),
            "target_pct": round(stop_pct * 1.8, 2), "rationale": "规则引擎兜底", "risk_flags": flags}


def analyze(symbol, cfg):
    """返回 (decision, evidence, indicators)。"""
    kl = exchange.klines(symbol, "1h", 200)
    ind = indicators.summarize(kl)
    funding = exchange.funding_rate(symbol)
    fng = exchange.fear_greed()
    evidence = build_evidence(symbol, ind, funding, fng)

    decision = None
    if cfg.get("LLM_API_KEY"):
        try:
            decision = llm_decide(cfg["LLM_PROVIDER"], cfg["LLM_API_KEY"], cfg["LLM_MODEL"], evidence)
            decision["_source"] = "llm"
        except Exception as e:
            print(f"[warn] LLM 调用失败，改用规则兜底: {e}")
    if decision is None:
        decision = rule_decide(ind, funding, fng)
        decision["_source"] = "rule"
    return decision, evidence, ind
