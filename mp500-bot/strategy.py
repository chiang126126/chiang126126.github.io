"""战略层：构造 Evidence → 调 LLM 出决策元组（带规则兜底）。"""
import json

import requests

import exchange
import indicators

SYSTEM_PROMPT = (
    "你是 MP500 加密交易系统的战略分析师。综合给定的 Evidence（技术指标 + 资金费率 + 情绪 + "
    "近期新闻/叙事/宏观/地缘），对该标的给出一个保守的短线决策。\n"
    "重视信息面：关键人物发言、AI 基建/芯片叙事、监管、地缘冲突/战争、美联储与宏观数据，"
    "都可能在小时级别快速重定价；但叙事行情来去快，需与技术面/风控共同确认，不可只凭新闻追高。\n"
    "规则：只允许做多或观望（不做空）；行情不明确时必须 FLAT（空仓也是决策）；必须给出止损；风控优先于收益。\n"
    "只返回严格 JSON，字段：\n"
    '{"bias":"LONG|FLAT","confidence":0.0-1.0,"stop_pct":正数(止损距入场的百分比,如2.0表示2%),'
    '"target_pct":正数(止盈距入场的百分比),"rationale":"简述形态+新闻/叙事依据","risk_flags":["风险点"]}'
)


def fetch_crypto_news(n=8):
    """CryptoCompare 加密新闻头条（无需 key）。"""
    try:
        r = requests.get("https://min-api.cryptocompare.com/data/v2/news/?lang=EN", timeout=15)
        r.raise_for_status()
        data = r.json().get("Data", [])[:n]
        return [f"- [{(it.get('source_info') or {}).get('name', '')}] {it.get('title', '')}" for it in data]
    except Exception:
        return []


def fetch_marketaux_news(key, n=6):
    """Marketaux 宏观/AI/地缘新闻（需免费 key，覆盖 NVDA/MRVL 等 AI 基建、美联储、地缘）。"""
    try:
        r = requests.get("https://api.marketaux.com/v1/news/all", params={
            "api_token": key, "language": "en", "filter_entities": "true", "limit": n,
            "search": "bitcoin OR crypto OR ethereum OR AI OR semiconductor OR Nvidia OR Fed OR rate OR war OR geopolitical",
        }, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])[:n]
        return [f"- {it.get('title', '')}" for it in data]
    except Exception:
        return []


def fetch_news(cfg):
    """整合新闻信息面，供 LLM 小时级判断。"""
    blocks = []
    cn = fetch_crypto_news(8)
    if cn:
        blocks.append("加密新闻头条:\n" + "\n".join(cn))
    if cfg.get("MARKETAUX_KEY"):
        mn = fetch_marketaux_news(cfg["MARKETAUX_KEY"], 6)
        if mn:
            blocks.append("宏观 / AI 基建 / 地缘 新闻:\n" + "\n".join(mn))
    return "\n\n".join(blocks) if blocks else "（暂无新闻）"


def build_evidence(symbol, ind, funding, fng, news_text=""):
    fng_v, fng_c = fng
    n2 = lambda x, d=2: ("n/a" if x is None else f"{x:.{d}f}")
    return (
        f"标的: {symbol}\n"
        f"现价: {n2(ind['price'])}\n"
        f"30小时均线偏离: {n2(ind['dev_pct'])}%  (站上为偏多)\n"
        f"EMA21: {n2(ind['ema21'])}  EMA50: {n2(ind['ema50'])}\n"
        f"RSI14: {n2(ind['rsi14'], 1)}\n"
        f"ATR(1h): {n2(ind['atr_pct'])}% (波动率)\n"
        f"资金费率: {('%.4f%%/8h' % funding) if funding is not None else 'n/a'}\n"
        f"恐惧贪婪: {fng_v if fng_v is not None else 'n/a'} ({fng_c or 'n/a'})\n"
        f"\n## 近期新闻 / 叙事 / 宏观 / 地缘\n{news_text or '（暂无新闻）'}\n"
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


def analyze(symbol, cfg, news_text=""):
    """返回 (decision, evidence, indicators)。news_text 由 bot 每轮抓一次后传入。"""
    kl = exchange.klines(symbol, "1h", 200)
    ind = indicators.summarize(kl)
    funding = exchange.funding_rate(symbol)
    fng = exchange.fear_greed()
    evidence = build_evidence(symbol, ind, funding, fng, news_text)

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
