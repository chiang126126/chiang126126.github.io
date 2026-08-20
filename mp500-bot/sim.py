"""MP-Sim 模拟舱：BTC/ETH 双册纯模拟合约（绝不下真实订单）。

为什么另起一套而不是复用主机器人：
  2026-07~08 的实测复盘表明，主循环(LLM行动卡+趋势闸门)在震荡市里的亏损
  几乎全部来自「无趋势时仍按趋势打法进场」——升级后31笔里19笔裸止损、
  平均MFE仅0.30%。模拟舱的核心差异是【先判市场形态、再选打法】：
    · 趋势市(价离30日线≥1%且均线同向) → 顺势回踩30h线进场，追踪止盈拿趋势
    · 震荡市(其余) → 只在昨高/昨低边缘反向进场，小目标快进快出
    · 两种形态都不满足入场条件 → 明确给出"不出手"及原因(信号台可见)
  并配上主循环没有的交易频率管制：连续止损冷静期、每日进场上限、当日亏损停机。

册规则（v1，全部确定性、无LLM，保证信号稳定可复现）：
  · 每册 1000 USDT 起步，各自独立核算，互不影响
  · 单笔风险 = 册权益的 1.5%；名义仓位 ≤ 3×册权益（杠杆帽 3x，常态 1~2x）
  · 手续费按合约 taker 0.05%/边计；回测模式不含资金费(结果会略偏乐观，已知偏差)
  · 出场全部蜡烛级管理：止损 → 目标 → 保本上移 → 追踪锁盈 → 超时离场
  · 同一根K线同时触及止损与目标时按【先止损】计(悲观口径，宁可低估不高估)

运行方式：
  python3 sim.py                  # 前向模式：处理自上次以来的已收盘1h K线(每小时cron)
  python3 sim.py --backfill 95    # 回测模式：用真实K线回放近95天，写 sim_backtest.json
  python3 sim.py --reset          # 清空双册重新从 1000U 开始(前向数据，不动回测报告)

数据文件（$DATA_DIR，与主机器人同目录、互不干扰）：
  sim_state.json / sim_trades.json / sim_log.json / sim_backtest.json / klines_1d_*.json
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

import exchange
from indicators import sma, rsi, atr

# ═══════════════ 册与风控参数 ═══════════════
BOOKS = ["BTCUSDT", "ETHUSDT"]
START_EQUITY = 1000.0
RISK_PCT = 1.5            # 单笔风险 %册权益
LEV_CAP = 3.0             # 名义 ≤ 3×册权益
FEE = 0.0005              # taker 单边

# ── 趋势打法（回踩30h线顺势）──
T_STOP_ATR = 1.0          # 止损 = 1×ATR(1h)
T_STOP_MIN = 0.008        # 但不小于 0.8%
T_STOP_MAX = 0.015        # 不大于 1.5%
T_RR = 2.2                # 硬目标 2.2R
T_BE_TRIG = 1.0           # 浮盈达 1.0×止损距 → 止损移保本(entry±2×FEE)
T_TRAIL_TRIG = 1.4        # 浮盈达 1.4×止损距 → 启动追踪，锁 50% MFE
T_TRAIL_LOCK = 0.5
T_MAX_HOLD = 48           # 小时
T_RSI_LONG = (45, 70)     # 多头回踩时 RSI 允许区(不接刀也不追热)
T_RSI_SHORT = (30, 55)

# ── 震荡打法（昨高/昨低边缘反向）──
R_STOP_ATR = 0.7
R_STOP_MIN = 0.004
R_STOP_MAX = 0.010
R_RR = 1.8
R_MAX_HOLD = 24
R_RSI_HI = 62             # 摸昨高做空需 RSI≥62(确有过热; v2 自 58 收紧)
R_RSI_LO = 38             # 探昨低做多需 RSI≤38(v2 自 42 收紧)
EDGE_TOUCH = 0.002        # 触及边缘容差 0.2%

# ── 形态判定（日线）──
REGIME_BAND = 1.0         # 价距30日线 ≥±1% 才可能算趋势
REGIME_SLOPE_D = 5        # 且30日线相对5天前同向

# ── 滤网与管制 ──
FUNDING_BLOCK_SHORT = -0.05   # 资费≤-0.05% 空头拥挤 → 禁开空
FUNDING_BLOCK_LONG = 0.10     # 资费≥+0.10% 多头过热 → 禁开多
VOL_SPIKE_SKIP = 2.5          # 量能≥2.5×常态 → 事件行情不开新仓
MAX_ENTRIES_PER_DAY = 1       # 每册每UTC日最多进场N次(v2 自 2 收紧)
COOLDOWN_STOPS = 2            # 连续2笔裸止损 → 冷静
COOLDOWN_HOURS = 12
DAY_LOSS_HALT = 3.0           # 当日册亏损≥3% → 当日停机
BOOK_DD_HALT = 25.0           # 距册峰值回撤≥25% → 整册停机待人工检视

# ── v2 信号质量闸（95天回测证据: v1 病根=频率×手续费347U+假趋势回踩+逆漂移边缘反手。
#    v2 在同段回放: 17笔 +117U, BTC PF 1.26 / ETH PF 6.62(小样本), 最大回撤<5%;
#    且参数平台宽阔(TOUCH 8-12h × GAP 4-8h × RSI 60-66 全为正), 非尖峰拟合。
#    警示: 以上为样本内结果, 真正的检验是前向模拟——PF 连续8周>1.3 才谈实盘。）──
REENTRY_GAP_H = 6             # 任意离场后至少隔N小时才可再进场（设1=仅隔一根K线，即v1行为）
TREND_EXT = 0.012             # 趋势打法要求近24h价格曾离30h线≥1.2%（证明真趋势回踩，0=关闭）
TOUCH_FRESH_H = 8             # 30h线此前8小时内未被触碰过才算"第一次回踩"（0=关闭）
RANGE_DRIFT_FILTER = True     # 震荡边缘只做顺日线漂移方向：价在30日线上只接昨低多、线下只空昨高
ENTRY_MODE = "close"          # "close"=信号蜡烛收盘价进场 | "limit"=信号后在被触水平挂限价等回落
LIMIT_TTL_H = 6               # 挂单有效期(小时)，超时未成交撤单

H1 = 3600_000


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def data_dir():
    d = os.getenv("DATA_DIR", "./data")
    os.makedirs(d, exist_ok=True)
    return d


def fpath(name):
    return os.path.join(data_dir(), name + ".json")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj, compact=False):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def utc_date(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def iso(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ═══════════════ 形态与信号（纯函数，可单测）═══════════════

def classify_regime(dcloses, price):
    """日线形态：trend_up / trend_down / range。
    价距30日线 ≥±1% 且 30日线较5天前同向 才算趋势——两个条件缺一即 range，
    宁可错过趋势头两天，也不在均线走平的假突破里按趋势打法进场。"""
    ma = sma(dcloses, 30)
    ma_prev = sma(dcloses[:-REGIME_SLOPE_D], 30) if len(dcloses) > 30 + REGIME_SLOPE_D else None
    if ma is None or not price:
        return "range", None
    dev = (price / ma - 1) * 100
    if ma_prev is not None:
        if dev >= REGIME_BAND and ma > ma_prev:
            return "trend_up", round(dev, 2)
        if dev <= -REGIME_BAND and ma < ma_prev:
            return "trend_down", round(dev, 2)
    return "range", round(dev, 2)


def candle_ctx(kl, i):
    """第 i 根(已收盘)蜡烛时点的指标上下文——只用 i 及之前的数据，杜绝未来函数。"""
    window = kl[max(0, i - 60):i + 1]
    closes = [k["c"] for k in window]
    a = atr(window, 14)
    prevN = window[-(TOUCH_FRESH_H + 1):-1] if TOUCH_FRESH_H > 0 else []
    last24 = window[-24:]
    return {
        "ma30h": sma(closes, 30),
        "rsi": rsi(closes, 14),
        "atr_pct": (a / closes[-1]) if a else None,
        "vol_ratio": _vol_ratio(window),
        "hh24": max(k["h"] for k in last24),      # 近24h最高（趋势延伸证明）
        "ll24": min(k["l"] for k in last24),
        "lo_prevN": min((k["l"] for k in prevN), default=None),   # 此前N小时最低（第一次回踩判定）
        "hi_prevN": max((k["h"] for k in prevN), default=None),
    }


def _vol_ratio(window):
    vols = [k["v"] for k in window]
    if len(vols) < 40:
        return None
    base = sum(vols[:-10]) / len(vols[:-10]) * 10
    return round(sum(vols[-10:]) / base, 2) if base else None


def entry_signal(regime, k, ctx, prev_day, funding=None, dev=None):
    """在已收盘蜡烛 k 上找进场信号。返回 (side, stop_pct, rr, max_hold, tag, level) 或 (None, 原因)。
    level=信号被触发的关键水平(30h线/昨高/昨低)，ENTRY_MODE=limit 时在此挂限价单；
    ENTRY_MODE=close 时进场价取信号蜡烛收盘价(下一小时初成交的诚实近似)。"""
    price, ma30h, r14 = k["c"], ctx["ma30h"], ctx["rsi"]
    if ma30h is None or r14 is None or ctx["atr_pct"] is None:
        return None, "指标窗口未满(数据不足)"
    if ctx["vol_ratio"] is not None and ctx["vol_ratio"] >= VOL_SPIKE_SKIP:
        return None, f"量能异常放大 {ctx['vol_ratio']}×——事件行情不开新仓"

    if regime == "trend_up":
        # 顺势回踩：本蜡烛下探触及30h线(容差0.2%)且收盘收复其上，RSI 处45–70
        if k["l"] <= ma30h * (1 + EDGE_TOUCH) and price > ma30h and T_RSI_LONG[0] <= r14 <= T_RSI_LONG[1]:
            if TREND_EXT > 0 and ctx["hh24"] < ma30h * (1 + TREND_EXT):
                return None, f"趋势↑但近24h未离30h线≥{TREND_EXT*100:.1f}%——贴线磨不算真趋势回踩"
            if TOUCH_FRESH_H > 0 and ctx["lo_prevN"] is not None and ctx["lo_prevN"] <= ma30h * (1 + EDGE_TOUCH):
                return None, f"30h线{TOUCH_FRESH_H}h内已被触过——非第一次回踩，不重复进场"
            if funding is not None and funding >= FUNDING_BLOCK_LONG:
                return None, f"资费 {funding:+.3f}% 多头过热——趋势多信号作废"
            stop_pct = min(max(T_STOP_ATR * ctx["atr_pct"], T_STOP_MIN), T_STOP_MAX)
            return ("LONG", stop_pct, T_RR, T_MAX_HOLD, "趋势·回踩30h线多", ma30h), None
        return None, "趋势↑待回踩：等价格回踩30h线收复(不追第一波)"
    if regime == "trend_down":
        if k["h"] >= ma30h * (1 - EDGE_TOUCH) and price < ma30h and T_RSI_SHORT[0] <= r14 <= T_RSI_SHORT[1]:
            if TREND_EXT > 0 and ctx["ll24"] > ma30h * (1 - TREND_EXT):
                return None, f"趋势↓但近24h未离30h线≥{TREND_EXT*100:.1f}%——贴线磨不算真趋势反抽"
            if TOUCH_FRESH_H > 0 and ctx["hi_prevN"] is not None and ctx["hi_prevN"] >= ma30h * (1 - EDGE_TOUCH):
                return None, f"30h线{TOUCH_FRESH_H}h内已被触过——非第一次反抽，不重复进场"
            if funding is not None and funding <= FUNDING_BLOCK_SHORT:
                return None, f"资费 {funding:+.3f}% 空头拥挤——趋势空信号作废"
            stop_pct = min(max(T_STOP_ATR * ctx["atr_pct"], T_STOP_MIN), T_STOP_MAX)
            return ("SHORT", stop_pct, T_RR, T_MAX_HOLD, "趋势·反抽30h线空", ma30h), None
        return None, "趋势↓待反抽：等价格反抽30h线受阻(不追第一波)"

    # 震荡：只在边缘做反向
    ph, pl = (prev_day or {}).get("h"), (prev_day or {}).get("l")
    if ph is None or pl is None:
        return None, "缺昨高/昨低数据"
    stop_pct = min(max(R_STOP_ATR * ctx["atr_pct"], R_STOP_MIN), R_STOP_MAX)
    # 顺漂移过滤（回测证据: 逆日线漂移的边缘反手是主要亏损源——修复期空昨高/下跌期接昨低都是螳臂当车）
    allow_short = not (RANGE_DRIFT_FILTER and dev is not None and dev >= 0)
    allow_long = not (RANGE_DRIFT_FILTER and dev is not None and dev < 0)
    if k["h"] >= ph * (1 - EDGE_TOUCH) and price < ph and r14 >= R_RSI_HI:
        if not allow_short:
            return None, "摸昨高但价在30日线上方(漂移向上)——不逆漂移做空"
        if funding is not None and funding <= FUNDING_BLOCK_SHORT:
            return None, f"资费 {funding:+.3f}% 空头拥挤——边缘空信号作废"
        return ("SHORT", stop_pct, R_RR, R_MAX_HOLD, "震荡·摸昨高受阻空", ph), None
    if k["l"] <= pl * (1 + EDGE_TOUCH) and price > pl and r14 <= R_RSI_LO:
        if not allow_long:
            return None, "探昨低但价在30日线下方(漂移向下)——不逆漂移接多"
        if funding is not None and funding >= FUNDING_BLOCK_LONG:
            return None, f"资费 {funding:+.3f}% 多头过热——边缘多信号作废"
        return ("LONG", stop_pct, R_RR, R_MAX_HOLD, "震荡·探昨低承接多", pl), None
    return None, f"震荡·区间中部无边缘信号(昨高{ph:.0f}/昨低{pl:.0f}内等待)"


def size_position(equity, price, stop_pct):
    """风险定仓：qty=风险额/止损距；名义受杠杆帽约束(超帽等比缩，风险随之下降)。"""
    risk = equity * RISK_PCT / 100
    stop_dist = price * stop_pct
    qty = risk / stop_dist
    notional = qty * price
    cap = equity * LEV_CAP
    if notional > cap:
        k = cap / notional
        qty *= k
        notional = cap
        risk *= k
    return qty, round(notional, 2), round(risk, 2)


def manage_candle(pos, k):
    """在一根已收盘蜡烛上推进持仓。返回 (exit_price, reason) 或 (None, None)。
    顺序即口径：①按当前止损/目标查触发(悲观:同烛先止损) ②用蜡烛极值更新MFE
    ③按MFE上移止损(保本/追踪，只紧不松) ④超时以收盘价离场。"""
    long = pos["side"] == "LONG"
    stop, target = pos["stop"], pos["target"]
    if long:
        if k["l"] <= stop:
            return stop, pos.get("stop_kind", "STOP")
        if k["h"] >= target:
            return target, "TARGET"
    else:
        if k["h"] >= stop:
            return stop, pos.get("stop_kind", "STOP")
        if k["l"] <= target:
            return target, "TARGET"
    # MFE 更新(出场检查之后——本蜡烛的新高不能反过来抬高本蜡烛的止损)
    if long:
        pos["mfe_price"] = max(pos.get("mfe_price", pos["entry"]), k["h"])
    else:
        pos["mfe_price"] = min(pos.get("mfe_price", pos["entry"]), k["l"])
    _upgrade_stop(pos)
    # 超时
    held_h = (k["t"] + H1 - pos["opened_t"]) / H1
    if held_h >= pos["max_hold"]:
        return k["c"], "TIME"
    return None, None


def _upgrade_stop(pos):
    long = pos["side"] == "LONG"
    entry = pos["entry"]
    mfe = abs(pos.get("mfe_price", entry) / entry - 1)
    sd = pos["stop_pct"]
    if mfe >= T_TRAIL_TRIG * sd:
        lock = mfe * T_TRAIL_LOCK
        new = entry * (1 + lock) if long else entry * (1 - lock)
        if (long and new > pos["stop"]) or (not long and new < pos["stop"]):
            pos["stop"], pos["stop_kind"] = round(new, 2), "STOP_TRAIL"
    elif mfe >= T_BE_TRIG * sd and pos.get("stop_kind", "STOP") == "STOP":
        new = entry * (1 + 2 * FEE) if long else entry * (1 - 2 * FEE)
        if (long and new > pos["stop"]) or (not long and new < pos["stop"]):
            pos["stop"], pos["stop_kind"] = round(new, 2), "STOP_BE"


# ═══════════════ 册引擎 ═══════════════

def new_book():
    return {"equity": START_EQUITY, "peak": START_EQUITY, "pos": None,
            "last_t": None, "day": None, "day_start": START_EQUITY,
            "entries_today": 0, "consec_stops": 0, "cooldown_until": None,
            "halted": False, "signal": None}


def governor(book, t_ms):
    """交易频率与亏损管制。返回 None=放行，否则=拦截原因。"""
    if book["halted"]:
        return f"整册停机(距峰值回撤≥{BOOK_DD_HALT:.0f}%)——待人工检视后 --reset"
    d = utc_date(t_ms)
    if book["day"] != d:
        book["day"], book["day_start"], book["entries_today"] = d, book["equity"], 0
    if (book["equity"] / book["day_start"] - 1) * 100 <= -DAY_LOSS_HALT:
        return f"当日亏损≥{DAY_LOSS_HALT:.0f}%——今日停机保护"
    if book["cooldown_until"] and t_ms < book["cooldown_until"]:
        left = (book["cooldown_until"] - t_ms) / H1
        return f"连续止损冷静期(剩{left:.0f}h)"
    if book["entries_today"] >= MAX_ENTRIES_PER_DAY:
        return f"今日已进场{MAX_ENTRIES_PER_DAY}次——频率上限"
    return None


def open_position(book, sym, side, k, stop_pct, rr, max_hold, tag, entry=None):
    entry = entry if entry is not None else k["c"]
    qty, notional, risk = size_position(book["equity"], entry, stop_pct)
    long = side == "LONG"
    stop = entry * (1 - stop_pct) if long else entry * (1 + stop_pct)
    target = entry * (1 + rr * stop_pct) if long else entry * (1 - rr * stop_pct)
    fee_in = notional * FEE
    book["equity"] -= fee_in
    book["pos"] = {"symbol": sym, "side": side, "entry": entry, "qty": qty,
                   "notional": notional, "risk": risk, "stop": round(stop, 2),
                   "target": round(target, 2), "stop_pct": stop_pct, "rr": rr,
                   "max_hold": max_hold, "stop_kind": "STOP", "tag": tag,
                   "opened_t": k["t"] + H1, "opened_at": iso(k["t"] + H1),
                   "fee_in": round(fee_in, 4), "mfe_price": entry}
    book["entries_today"] += 1


def close_position(book, k_t, exit_price, reason, trades):
    pos = book.pop("pos")
    book["pos"] = None
    long = pos["side"] == "LONG"
    pnl_gross = (exit_price - pos["entry"]) * pos["qty"] * (1 if long else -1)
    fee_out = exit_price * pos["qty"] * FEE
    pnl = pnl_gross - fee_out - pos["fee_in"]
    book["equity"] += pnl_gross - fee_out   # fee_in 已在开仓时扣
    book["peak"] = max(book["peak"], book["equity"])
    mfe_pct = abs(pos["mfe_price"] / pos["entry"] - 1) * 100
    if reason == "STOP":
        book["consec_stops"] += 1
        if book["consec_stops"] >= COOLDOWN_STOPS:
            book["cooldown_until"] = k_t + H1 + COOLDOWN_HOURS * H1
            book["consec_stops"] = 0
    else:
        book["consec_stops"] = 0
    if (1 - book["equity"] / book["peak"]) * 100 >= BOOK_DD_HALT:
        book["halted"] = True
    book["last_exit_t"] = k_t
    trades.append({"symbol": pos["symbol"], "side": pos["side"], "tag": pos["tag"],
                   "entry": pos["entry"], "exit": round(exit_price, 2), "qty": round(pos["qty"], 6),
                   "notional": pos["notional"], "risk": pos["risk"],
                   "pnl": round(pnl, 4), "r": round(pnl / pos["risk"], 2) if pos["risk"] else 0,
                   "fee_total": round(pos["fee_in"] + fee_out, 4),
                   "mfe_pct": round(mfe_pct, 2), "exit_reason": reason,
                   "hold_hours": round((k_t + H1 - pos["opened_t"]) / H1, 1),
                   "opened_at": pos["opened_at"], "closed_at": iso(k_t + H1),
                   "equity_after": round(book["equity"], 2)})


def run_book(book, sym, kl_closed, day_map, trades, funding=None):
    """把一册推进到最新已收盘蜡烛。kl_closed 必须全为已收盘1h蜡烛(升序)。"""
    if book["last_t"] is None:                    # 首次运行：从当下开始，不回放历史
        book["last_t"] = kl_closed[-1]["t"]
        book["signal"] = {"at": iso(kl_closed[-1]["t"] + H1), "text": "模拟舱启动，从下一根K线开始交易", "regime": None}
        return
    start = next((i for i, k in enumerate(kl_closed) if k["t"] > book["last_t"]), None)
    if start is None:
        return
    for i in range(start, len(kl_closed)):
        k = kl_closed[i]
        d = utc_date(k["t"])
        prev_day = day_map.get((datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"))
        dcloses = [day_map[x]["c"] for x in sorted(day_map) if x < d]
        regime, dev = classify_regime(dcloses, k["c"])
        # ① 先管持仓
        if book["pos"]:
            exit_price, reason = manage_candle(book["pos"], k)
            if reason:
                close_position(book, k["t"], exit_price, reason, trades)
        # ①b 挂单生命周期（ENTRY_MODE=limit）：成交/超时/管制复核，同烛悲观止损
        sig_text = None
        if book.get("pending") and book["pos"] is None:
            pd_ = book["pending"]
            long = pd_["side"] == "LONG"
            touched = (k["l"] <= pd_["limit"]) if long else (k["h"] >= pd_["limit"])
            if k["t"] >= pd_["expires_t"]:
                book["pending"] = None
                sig_text = f"⏳ 挂单{LIMIT_TTL_H}h未成交，撤单(价格没回来，让它去)"
            elif touched:
                block = governor(book, k["t"])
                if block:
                    book["pending"] = None
                    sig_text = f"🚫 挂单触及但被管制拦截({block})，撤单"
                else:
                    # 开盘已越过限价则按更优的开盘价成交
                    fill = min(k["o"], pd_["limit"]) if long else max(k["o"], pd_["limit"])
                    open_position(book, sym, pd_["side"], k, pd_["stop_pct"], pd_["rr"],
                                  pd_["max_hold"], pd_["tag"], entry=fill)
                    book["pending"] = None
                    pos = book["pos"]
                    # 悲观口径：成交同蜡烛若也触到止损，直接按止损离场
                    if (long and k["l"] <= pos["stop"]) or (not long and k["h"] >= pos["stop"]):
                        close_position(book, k["t"], pos["stop"], "STOP", trades)
                        sig_text = f"{'🟢' if long else '🔴'} 挂单成交@{fill:.2f}后同K线触止损离场(悲观计)"
                    else:
                        sig_text = f"{'🟢 限价开多' if long else '🔴 限价开空'} @{fill:.2f}｜{pd_['tag']}｜止损{pd_['stop_pct']*100:.2f}% 目标{pd_['rr']}R"
        # ② 再找进场（无持仓且无挂单时）
        if sig_text is None and book["pos"] is None and not book.get("pending"):
            block = governor(book, k["t"])
            if block is None and book.get("last_exit_t") is not None \
                    and k["t"] < book["last_exit_t"] + REENTRY_GAP_H * H1:
                block = f"离场后{REENTRY_GAP_H}h再进场冷却中(防报复性进场)"
            if block:
                sig_text = f"🚫 {block}"
            else:
                ctx = candle_ctx(kl_closed, i)
                sig, why = entry_signal(regime, k, ctx, prev_day, funding, dev)
                if sig:
                    side, stop_pct, rr, max_hold, tag, level = sig
                    if ENTRY_MODE == "limit":
                        book["pending"] = {"side": side, "limit": round(level, 2), "stop_pct": stop_pct,
                                           "rr": rr, "max_hold": max_hold, "tag": tag,
                                           "placed_t": k["t"], "expires_t": k["t"] + LIMIT_TTL_H * H1}
                        sig_text = f"📌 信号确认，挂限价单等回落: {side} @{level:.2f}｜{tag}｜{LIMIT_TTL_H}h内有效"
                    else:
                        open_position(book, sym, side, k, stop_pct, rr, max_hold, tag)
                        sig_text = f"{'🟢 买入开多' if side == 'LONG' else '🔴 卖出开空'} @{k['c']:.2f}｜{tag}｜止损{stop_pct*100:.2f}% 目标{rr}R"
                else:
                    sig_text = f"⏳ {why}"
        elif sig_text is None and book["pos"]:
            p = book["pos"]
            sig_text = f"持仓管理中：{p['side']} @{p['entry']:.2f}，止损 {p['stop']:.2f}({p['stop_kind']})"
        elif sig_text is None:
            pd_ = book.get("pending") or {}
            sig_text = f"📌 挂单等待回落: {pd_.get('side')} @{pd_.get('limit')}（{pd_.get('tag')}）"
        book["signal"] = {"at": iso(k["t"] + H1), "text": sig_text, "regime": regime, "dev": dev}
        book["last_t"] = k["t"]


def guardian_tick(state, trades, prices, now_ms=None):
    """分钟级守护钩子(由 guardian.py 每3分钟调用)：只管已有模拟持仓——
    实时价更新MFE→上移保本/追踪(只紧不松)→触及止损/止盈/超时立即离场。
    绝不开新仓、不碰挂单(挂单按小时蜡烛成交口径,与回测一致)。返回是否有变化。
    成交口径：止损触发按实时价成交(不优于止损价,诚实计滑)；目标触发按目标价成交(限价语义)。"""
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    changed = False
    for sym, book in (state.get("books") or {}).items():
        pos, px = book.get("pos"), prices.get(sym)
        if not pos or not px:
            continue
        long = pos["side"] == "LONG"
        pos["mfe_price"] = max(pos.get("mfe_price", pos["entry"]), px) if long \
            else min(pos.get("mfe_price", pos["entry"]), px)
        old = (pos["stop"], pos.get("stop_kind"))
        _upgrade_stop(pos)
        if (pos["stop"], pos.get("stop_kind")) != old:
            changed = True
            print(f"[guardian][sim] {sym} 锁盈上移 {old[0]} → {pos['stop']} ({pos['stop_kind']})")
        exit_price = reason = None
        if long and px <= pos["stop"]:
            exit_price, reason = px, pos.get("stop_kind", "STOP")
        elif not long and px >= pos["stop"]:
            exit_price, reason = px, pos.get("stop_kind", "STOP")
        elif long and px >= pos["target"]:
            exit_price, reason = pos["target"], "TARGET"
        elif not long and px <= pos["target"]:
            exit_price, reason = pos["target"], "TARGET"
        elif (now_ms - pos["opened_t"]) / H1 >= pos["max_hold"]:
            exit_price, reason = px, "TIME"
        if reason:
            # 守护层的 STOP_BE/STOP_TRAIL 不算裸止损: close_position 只对 "STOP" 计连败
            close_position(book, now_ms - H1, exit_price, reason, trades)
            trades[-1]["via"] = "guardian"
            book["signal"] = {"at": now_iso(), "text": f"🛡 守护层分钟级离场: {reason} @{exit_price:.2f}",
                              "regime": (book.get("signal") or {}).get("regime")}
            changed = True
            print(f"[guardian][sim][exit] {sym} {reason} @ {exit_price:.2f} pnl={trades[-1]['pnl']:.2f}")
    return changed


def write_log(state, trades):
    """写 sim_log.json(看板信号台数据源)。前向与守护层共用同一口径。"""
    save_json(fpath("sim_log"), {"updated_at": now_iso(),
                                 "books": {s: {"equity": round(state["books"][s]["equity"], 2),
                                               "signal": state["books"][s]["signal"],
                                               "stats": book_stats([t for t in trades if t["symbol"] == s],
                                                                   state["books"][s]["equity"])}
                                           for s in state.get("books", {})}})


# ═══════════════ 数据获取 ═══════════════

def closed_only(kl):
    """去掉未收盘的最后一根(收盘时间在未来的)。"""
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    return [k for k in kl if k["t"] + H1 <= now_ms]


def day_map_from(dkl):
    m = {}
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    for k in dkl:
        if k["t"] + 86400_000 <= now_ms:          # 只收已收盘的日线
            m[utc_date(k["t"])] = {"h": k["h"], "l": k["l"], "c": k["c"]}
    return m


def klines_range(symbol, interval, start_ms):
    """分页拉取 startTime 起的全部K线（回测用）。"""
    out, cur = [], start_ms
    while True:
        r = requests.get(f"{exchange.PUBLIC}/api/v3/klines",
                         params={"symbol": symbol, "interval": interval,
                                 "startTime": cur, "limit": 1000}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out += [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
                 "c": float(k[4]), "v": float(k[5])} for k in batch]
        if len(batch) < 1000:
            break
        cur = batch[-1][0] + 1
    return out


def book_stats(trades, equity):
    if not trades:
        return {"n": 0, "net": 0, "wr": None, "pf": None, "max_dd": 0, "equity": round(equity, 2)}
    pnl = [t["pnl"] for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    eq, peak, mdd = START_EQUITY, START_EQUITY, 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, (1 - eq / peak) * 100)
    return {"n": len(trades), "net": round(sum(pnl), 2),
            "wr": round(len(wins) / len(trades) * 100, 1),
            "pf": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
            "max_dd": round(mdd, 2), "equity": round(equity, 2),
            "fees": round(sum(t["fee_total"] for t in trades), 2),
            "by_reason": _by(trades, "exit_reason"), "by_tag": _by(trades, "tag")}


def _by(trades, key):
    out = {}
    for t in trades:
        b = out.setdefault(t.get(key, "?"), {"n": 0, "net": 0})
        b["n"] += 1
        b["net"] = round(b["net"] + t["pnl"], 2)
    return out


# ═══════════════ 入口 ═══════════════

def main_forward():
    state = load_json(fpath("sim_state"), {"books": {}, "started_at": now_iso()})
    trades = load_json(fpath("sim_trades"), [])
    for sym in BOOKS:
        book = state["books"].setdefault(sym, new_book())
        try:
            kl = closed_only(exchange.klines(sym, "1h", 500))
            dkl = exchange.klines(sym, "1d", 60)
            try:
                funding = exchange.funding_rate(sym)
            except Exception:
                funding = None
            run_book(book, sym, kl, day_map_from(dkl), trades, funding)
            save_json(fpath("klines_1d_" + sym), dkl[-400:], compact=True)   # 日线存档供复盘
            # 1h K线增量合并存档(回测写的95天大档只增不缩, 滚动上限≈104天)
            arch = load_json(fpath("klines_1h_" + sym), [])
            last_arch_t = arch[-1]["t"] if arch else 0
            arch += [x for x in kl if x["t"] > last_arch_t]
            save_json(fpath("klines_1h_" + sym), arch[-2500:], compact=True)
        except Exception as e:
            book["signal"] = {"at": now_iso(), "text": f"⚠ 数据获取失败：{e}", "regime": None}
        print(f"[sim] {sym}: 权益 {book['equity']:.2f}  {book['signal']['text'] if book['signal'] else ''}")
    state["updated_at"] = now_iso()
    save_json(fpath("sim_state"), state)
    save_json(fpath("sim_trades"), trades)
    write_log(state, trades)


def main_backfill(days):
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    report = {"period_days": days, "generated_at": now_iso(), "start_equity": START_EQUITY,
              "note": "回测口径：入场=信号蜡烛收盘价；同烛先止损(悲观)；不含资金费；每边0.05%手续费",
              "books": {}}
    for sym in BOOKS:
        kl = closed_only(klines_range(sym, "1h", start_ms))
        dkl = klines_range(sym, "1d", start_ms - 40 * 86400_000)
        # 原始K线存档：让无行情网络的云端环境也能独立回放/调参(证据化优化的前提)
        save_json(fpath("klines_1h_" + sym), kl, compact=True)
        save_json(fpath("klines_1d_" + sym), dkl, compact=True)
        book, trades = new_book(), []
        book["last_t"] = kl[40]["t"]              # 留出指标窗口
        run_book(book, sym, kl, day_map_from(dkl), trades, funding=None)
        st = book_stats(trades, book["equity"])
        st["period"] = f"{iso(kl[41]['t'])[:10]} → {iso(kl[-1]['t'])[:10]}"
        report["books"][sym] = {"stats": st, "trades": trades}
        print(f"[backfill] {sym}: {st['n']}笔 净{st['net']:+.2f}U 胜率{st['wr']}% PF {st['pf']} 最大回撤{st['max_dd']}% → 期末 {st['equity']:.2f}U")
    save_json(fpath("sim_backtest"), report)
    print("[backfill] 已写入 sim_backtest.json")


def main_reset():
    for n in ("sim_state", "sim_trades", "sim_log"):
        try:
            os.remove(fpath(n))
        except FileNotFoundError:
            pass
    print("[sim] 双册已清空，下次运行从 1000U 重新开始")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        main_reset()
    elif "--backfill" in sys.argv:
        i = sys.argv.index("--backfill")
        days = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 95
        main_backfill(days)
    else:
        main_forward()
