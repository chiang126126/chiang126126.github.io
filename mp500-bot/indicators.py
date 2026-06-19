"""轻量技术指标，纯 Python，无第三方依赖。"""


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def ema(values, n):
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / n
    al = sum(losses) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def atr(kl, n=14):
    """Average True Range，输入 kline dict 列表。"""
    if len(kl) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        h, l, pc = kl[i]["h"], kl[i]["l"], kl[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def summarize(kl):
    """从 1h K线算一组指标，供 Evidence 与规则使用。"""
    closes = [k["c"] for k in kl]
    price = closes[-1]
    sma30 = sma(closes, 30)
    return {
        "price": price,
        "sma30": sma30,
        "dev_pct": (price / sma30 - 1) * 100 if sma30 else None,
        "ema21": ema(closes, 21),
        "ema50": ema(closes, 50),
        "rsi14": rsi(closes, 14),
        "atr14": atr(kl, 14),
        "atr_pct": (atr(kl, 14) / price * 100) if atr(kl, 14) else None,
        "high_since": kl[-1]["h"],
        "low_since": kl[-1]["l"],
    }
