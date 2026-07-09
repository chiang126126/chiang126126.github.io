"""战略层：构造 Evidence → 调 LLM 出决策元组（带规则兜底）。"""
import json
from datetime import datetime, timezone

import requests

import exchange
import indicators


def classify_global_risk(xm):
    """确定性判定：今天全球资金在加风险还是降风险？返回 (label, reasons)。
    信号源：纳指期货、美元、MSTR相对BTC的超额弱势（杠杆温度计）、NVDA(AI风向)。
    数据缺失的信号自动跳过——判定基于可得信号，全缺则 unknown。"""
    score, reasons = 0, []
    nq = xm.get("nq_chg")
    if nq is not None:
        if nq <= -0.5: score -= 1; reasons.append(f"纳指期货 {nq:+.1f}%")
        elif nq >= 0.5: score += 1; reasons.append(f"纳指期货 {nq:+.1f}%")
    dxy = xm.get("dxy_chg")
    if dxy is not None:
        if dxy >= 0.3: score -= 1; reasons.append(f"美元走强 {dxy:+.1f}%")
        elif dxy <= -0.3: score += 1; reasons.append(f"美元走弱 {dxy:+.1f}%")
    nvda = xm.get("nvda_chg")
    if nvda is not None and abs(nvda) >= 1.5:
        score += 1 if nvda > 0 else -1
        reasons.append(f"NVDA {nvda:+.1f}%(AI风向)")
    m, b = xm.get("mstr_chg"), xm.get("btc_chg")
    if m is not None and b is not None and (m - b) <= -2:
        score -= 1; reasons.append(f"MSTR超额弱势 {m - b:+.1f}pp(降杠杆信号)")
    if not reasons:
        return "unknown", ["跨市场数据不可用"]
    label = "risk-off" if score <= -1 else "risk-on" if score >= 1 else "mixed"
    return label, reasons


def mstr_divergence(xm):
    """MSTR 三维解读（价格代理/杠杆温度计/融资压力），返回一句给 LLM 的提示。"""
    m, b, nq = xm.get("mstr_chg"), xm.get("btc_chg"), xm.get("nq_chg")
    if m is None or b is None:
        return "MSTR数据不可用"
    gap = m - b
    if gap <= -2 and (nq is not None and nq <= -0.3) and b < 0:
        return f"BTC{b:+.1f}%·MSTR{m:+.1f}%·纳指走弱 → 三者共振＝系统性risk-off，顺势做空可考虑"
    if gap <= -2 and abs(b) < 1:
        return f"BTC稳({b:+.1f}%)但MSTR单独大跌({m:+.1f}%) → 或为Strategy自身融资/增发担忧，【不能】据此做空BTC"
    if gap >= 1.5 and (nq is None or nq >= 0):
        return f"MSTR率先转强({m:+.1f}% vs BTC{b:+.1f}%)＋纳指企稳 → 机构风险偏好恢复的早期信号"
    return f"MSTR{m:+.1f}% vs BTC{b:+.1f}%，无显著背离"


def session_phase():
    """当前处于'一人投资体系'的哪个时段（巴黎时间，边界与看板一日节奏面板一致）。"""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        now = datetime.now(timezone.utc)
        now = now.replace(hour=(now.hour + 2) % 24)      # 夏令时近似
    t = now.hour * 60 + now.minute
    if 420 <= t < 840:  return "欧洲上午·复盘隔夜/亚洲盘，形成初步假设，不急于交易"
    if 840 <= t < 930:  return "美股盘前·观察期指/美债/美元/MSTR盘前，按三套情景准备预案"
    if 930 <= t < 1320: return "美股盘中·只做确认不追第一波，警惕开盘噪音与插针"
    return "美股盘后/亚洲时段·关注加密独立行情与夜间异动"

SYSTEM_PROMPT = (
    "你是 MP500 加密交易系统的战略分析师。BTC 已是全球风险资产体系的一员——分析它之前，"
    "先回答：今天全球资金是在加风险，还是在降风险？\n"
    "Evidence 按三层组织，请按序分析：\n"
    "【第一层·领先信息】纳指期货/美债收益率/美元/NVDA(AI风向)/MSTR，判断风险偏好往哪走。"
    "MSTR是三维信号：BTC价格代理、市场杠杆温度计、公司融资压力——Evidence 已给出背离解读，"
    "MSTR单独走弱≠BTC更差，不能据此做空BTC。\n"
    "【第二层·加密确认】检查传统市场的变化是否真正传导到币圈：BTC关键位、ETH/BTC与SOL/BTC相对强弱、"
    "OI变化（下跌时OI升=新空进场，OI降=杠杆已清理、追空危险）、资金费率、情绪。\n"
    "【第三层·执行条件】方向对了也要回答：是否已涨跌过多不宜追？入场位在哪？什么价位证明判断错误？"
    "空间能否覆盖成本？答不全任何一项 → 必须 FLAT。\n"
    "进场纪律：做空需等『跌破支撑→反抽无法收复→MSTR/科技股未恢复→ETH/SOL无相对走强→资费未极端偏空』；"
    "做多更严格：『重新站回关键位→回踩不破→纳指风险偏好改善→MSTR不再弱于BTC→资费未过热』。"
    "禁止因纳指下跌/跌破均线就立即追空，禁止把下跌中的快速反弹当底部。\n"
    "信号冲突时（如美股弱但BTC稳且OI大降=去杠杆尾声；MSTR弱但BTC纳指稳=公司自身问题；"
    "BTC涨但ETH/SOL不跟=局部轮动非全面risk-on）→ 主动降低 confidence 或 FLAT，不强行选方向。\n"
    "【高周期优先】日线偏空禁做多、偏多禁做空、震荡双向但更谨慎。1倍杠杆USDT合约。风控优先于收益。\n"
    "只返回严格 JSON（行动卡）：\n"
    '{"market_state":"risk-on|risk-off|mixed","bias":"LONG|SHORT|FLAT","confidence":0.0-1.0,'
    '"entry_low":数字(入场区间下沿,现价可入则围绕现价),"entry_high":数字(入场区间上沿),'
    '"stop_pct":正数(止损距离%,即判断失效位),"target_pct":正数(目标距离%),'
    '"max_hold_hours":整数(最大持仓小时,4-72,超时无进展应离场),'
    '"invalidation":"什么情况出现即证明判断错误","rationale":"三层分析各一句",'
    '"risk_flags":["风险点/禁止交易条件"]}'
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
    cn = fetch_crypto_news(15)
    if cn:
        blocks.append("加密新闻头条:\n" + "\n".join(cn))
    if cfg.get("MARKETAUX_KEY"):
        mn = fetch_marketaux_news(cfg["MARKETAUX_KEY"], 8)
        if mn:
            blocks.append("宏观 / AI 基建 / 地缘 新闻:\n" + "\n".join(mn))
    return "\n\n".join(blocks) if blocks else "（暂无新闻）"


def build_evidence(symbol, ind, funding, fng, news_text=""):
    fng_v, fng_c = fng
    n2 = lambda x, d=2: ("n/a" if x is None else f"{x:.{d}f}")
    pc = lambda x: ("n/a" if x is None else f"{x:+.2f}%")
    regime_cn = {"risk-on": "偏多(站上30日线)", "risk-off": "偏空(跌破30日线)",
                 "neutral": "震荡(贴近30日线)"}.get(ind.get("regime", "neutral"), "n/a")
    xm = ind.get("xm") or {}
    grisk, greasons = ind.get("global_risk", "unknown"), ind.get("global_risk_reasons", [])
    return (
        f"标的: {symbol} ｜ 当前时段: {session_phase()}\n"
        f"\n## 第一层·领先信息(全球风险偏好)\n"
        f"全球风险判定: {grisk}  依据: {'; '.join(greasons) or 'n/a'}\n"
        f"纳指期货: {pc(xm.get('nq_chg'))} ｜ 美债10Y: {n2(xm.get('tnx'))}%({pc(xm.get('tnx_chg'))}) ｜ "
        f"美元DXY: {pc(xm.get('dxy_chg'))}\n"
        f"NVDA: {pc(xm.get('nvda_chg'))} ｜ COIN: {pc(xm.get('coin_chg'))} ｜ MSTR: {pc(xm.get('mstr_chg'))}\n"
        f"MSTR三维解读: {mstr_divergence(xm)}\n"
        f"\n## 第二层·加密市场确认\n"
        f"现价: {n2(ind['price'])} ｜ BTC 24h: {pc(xm.get('btc_chg'))}\n"
        f"日线趋势(30日线法): {regime_cn}  偏离 {n2(ind.get('daily_dev_pct'), 2)}%  ← 高周期，决定可做的方向\n"
        f"30小时均线偏离: {n2(ind['dev_pct'])}%  (站上为偏多)\n"
        f"EMA21: {n2(ind['ema21'])}  EMA50: {n2(ind['ema50'])} ｜ RSI14: {n2(ind['rsi14'], 1)} ｜ "
        f"ATR(1h): {n2(ind['atr_pct'])}%\n"
        f"ETH/BTC 24h: {pc(xm.get('ethbtc_chg'))} ｜ SOL/BTC 24h: {pc(xm.get('solbtc_chg'))} "
        f"(相对走强=资金敢下沉，全面risk-on确认)\n"
        f"OI(持仓量)24h: {pc(ind.get('oi_chg_pct'))} (下跌时OI升=新空进场；OI降=杠杆已清理，追空危险)\n"
        f"资金费率: {('%.4f%%/8h' % funding) if funding is not None else 'n/a'} ｜ "
        f"恐惧贪婪: {fng_v if fng_v is not None else 'n/a'} ({fng_c or 'n/a'})\n"
        f"\n## 第三层·执行条件(由你在行动卡中回答)\n"
        f"是否追高/追空？入场区间？失效位？空间是否覆盖成本(来回0.1%+滑点)？近期有无重大数据事件？\n"
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
    dev = ind["dev_pct"]; rsi = ind["rsi14"]
    regime = ind.get("regime", "neutral")     # 高周期(日线)趋势闸门
    bias, conf, flags = "FLAT", 0.3, []
    if dev is not None and rsi is not None:
        if dev >= 1 and rsi < 72 and regime != "risk-off":   # 站上均线+未超买，且日线不向下 → 做多
            bias, conf = "LONG", min(0.7, 0.5 + abs(dev) / 50)
        elif dev <= -1 and rsi > 28 and regime != "risk-on":  # 跌破均线+未超卖，且日线不向上 → 做空
            bias, conf = "SHORT", min(0.7, 0.5 + abs(dev) / 50)
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


def fetch_cross_market():
    """跨市场领先信息（每轮抓一次，多标的共用）。失败返回空 dict，Evidence 自动降级。"""
    try:
        return exchange.cross_market()
    except Exception as e:
        print(f"[warn] 跨市场数据获取失败: {e}")
        return {}


def analyze(symbol, cfg, news_text="", xm=None):
    """返回 (decision, evidence, indicators)。news_text/xm 由 bot 每轮抓一次后传入。"""
    kl = exchange.klines(symbol, "1h", 200)
    ind = indicators.summarize(kl)
    # 高周期(日线)趋势：口径与看板「30日均线法」完全一致，用作开仓方向闸门
    try:
        dcloses = [k["c"] for k in exchange.klines(symbol, "1d", 40)]
        ind["regime"], ind["daily_dev_pct"] = indicators.daily_regime(dcloses, ind["price"])
    except Exception as e:
        print(f"[warn] {symbol} 日线趋势获取失败，按震荡处理: {e}")
        ind["regime"], ind["daily_dev_pct"] = "neutral", None
    funding = exchange.funding_rate(symbol)
    fng = exchange.fear_greed()
    # 第一层·领先信息 + 第二层确认素材
    ind["xm"] = xm or {}
    ind["global_risk"], ind["global_risk_reasons"] = classify_global_risk(ind["xm"])
    ind["oi_chg_pct"] = exchange.oi_change_24h(symbol)
    # 决策时刻快照素材：一并挂到 ind 上，供 bot 在开仓时定格存档（复盘用）
    ind["funding_pct"] = funding
    ind["fng"], ind["fng_label"] = fng
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
