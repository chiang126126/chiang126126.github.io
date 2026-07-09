"""MP500 paper 机器人 —— 每次运行执行一个决策+执行周期。
MODE: dry(只决策不下单) | sim(用真实价格模拟撮合) | testnet(币安合约模拟盘真下单)
当前阶段：USDT 本位合约 1 倍杠杆，可做多/做空（在震荡行情双向都能取样）。
数据读写在 $DATA_DIR（默认 ./data），由外层 workflow 负责提交。
"""
import json
import os
from datetime import datetime, timezone

import exchange
import risk
import strategy

CORE = ["BTCUSDT", "ETHUSDT"]   # S0 只做 BTC/ETH
FEE = risk.FEE

# —— 出场管理（P1，依据 S0 首轮审计：19笔亏损中9笔曾浮盈≥1%后回吐）——
BE_TRIGGER_PCT = 1.0     # 浮盈达 1.0% → 止损移到入场价(+手续费)，最差保本
TRAIL_TRIGGER_PCT = 1.5  # 浮盈达 1.5% → 启动跟踪止盈
TRAIL_LOCK = 0.5         # 跟踪止盈锁住最佳浮盈的 50%
MAX_HOLD_CLAMP = (4, 72)  # LLM 行动卡的最大持仓时间被夹在 4~72 小时内


def cfg():
    return {
        "MODE": os.getenv("MODE", "sim").lower(),
        "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "deepseek"),
        "LLM_API_KEY": os.getenv("LLM_API_KEY", ""),
        "LLM_MODEL": os.getenv("LLM_MODEL", "deepseek-chat"),
        "TN_KEY": os.getenv("BINANCE_TESTNET_KEY", ""),
        "TN_SECRET": os.getenv("BINANCE_TESTNET_SECRET", ""),
        # —— 实盘（MODE=live 才生效；与 testnet key 完全隔离，不配则 live 拒绝运行）——
        "LIVE_KEY": os.getenv("BINANCE_LIVE_KEY", ""),
        "LIVE_SECRET": os.getenv("BINANCE_LIVE_SECRET", ""),
        "LIVE_MAX_NOTIONAL": float(os.getenv("LIVE_MAX_NOTIONAL", "120")),  # 实盘单笔名义硬顶(U)
        "LIVE_MAX_EQUITY": float(os.getenv("LIVE_MAX_EQUITY", "200")),      # 合约钱包余额超此值拒绝开新仓
        "LEVERAGE": int(os.getenv("LEVERAGE", "1")),
        "MARKETAUX_KEY": os.getenv("MARKETAUX_KEY", ""),
        "DATA_DIR": os.getenv("DATA_DIR", "./data"),
    }


def make_client(c):
    """按 MODE 构造交易客户端。返回 (client|None, fatal_err|None)。
    live 的任何配置缺失都是致命错误——宁可不跑，绝不带病碰真钱。"""
    if c["MODE"] == "live":
        if not (c["LIVE_KEY"] and c["LIVE_SECRET"]):
            return None, "MODE=live 但未配置 BINANCE_LIVE_KEY/SECRET，拒绝运行"
        return exchange.Futures(c["LIVE_KEY"], c["LIVE_SECRET"], base=exchange.FUTURES_LIVE), None
    if c["MODE"] == "testnet" and c["TN_KEY"]:
        return exchange.Futures(c["TN_KEY"], c["TN_SECRET"], base=exchange.FUTURES_TESTNET), None
    return None, None


def _apply_live_cap(plan, cap):
    """实盘单笔名义硬顶：超出则等比缩小 qty/notional/risk（風险随名义线性下降，方向与价位不变）。"""
    if plan["notional"] <= cap:
        return plan
    k = cap / plan["notional"]
    plan = dict(plan)
    plan["notional"] = round(plan["notional"] * k, 2)
    plan["qty"] = plan["qty"] * k
    plan["risk_usdt"] = round(plan["risk_usdt"] * k, 2)
    return plan


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def fresh_state():
    return {"mode": "sim", "equity0": 500.0, "equity": 500.0,
            "day": "", "day_start_equity": 500.0, "peak_equity": 500.0,
            "positions": [], "updated_at": now_iso()}


def _mfe_pct(pos):
    """开仓以来最佳浮盈%（对做多=最高价、做空=最低价 相对入场的有利距离）。"""
    e = pos["entry"]
    m = pos.get("mfe_price", e)
    return max(0.0, (m / e - 1) * 100 if pos.get("side", "LONG") == "LONG" else (e / m - 1) * 100)


def _apply_stop_upgrade(pos):
    """按当前 MFE 上移止损（只朝有利方向、永不放松）：≥1% 保本，≥1.5% 锁住 MFE 的一半。"""
    long = pos.get("side", "LONG") == "LONG"
    e = pos["entry"]
    mfe = _mfe_pct(pos)
    new_stop, kind = None, None
    if mfe >= TRAIL_TRIGGER_PCT:
        lock = mfe * TRAIL_LOCK / 100
        new_stop, kind = (e * (1 + lock) if long else e * (1 - lock)), "TRAIL"
    elif mfe >= BE_TRIGGER_PCT:
        new_stop, kind = (e * (1 + 2 * FEE) if long else e * (1 - 2 * FEE)), "BE"
    if new_stop is None:
        return
    if (long and new_stop > pos["stop"]) or (not long and new_stop < pos["stop"]):
        pos["stop"] = round(new_stop, 2)
        pos["stop_kind"] = kind


def time_exit(pos, kl, now):
    """行动卡的最大持仓时间：超时即按最新价离场（超时说明原判断未按预期兑现）。"""
    mh = pos.get("max_hold_hours")
    if not mh or not kl:
        return None, None
    if _hold_hours(pos.get("opened_at", now), now) >= float(mh):
        return kl[-1]["c"], "TIME"
    return None, None


def manage_position(pos, kl):
    """逐根K线按时间顺序推进持仓管理。返回 (exit_price, reason) 或 (None, None)。
    每根K线：先用【进入该K线时】的止损/止盈检查出场（同根K线止损优先，保守），
    再用该K线（仅已收盘的）更新 MFE 并上移止损——绝不用"未来"的高点触发"过去"的K线。
    managed_t 游标：已消化的K线下轮跳过，防止止损上移后被旧K线重复触发。
    出场那根K线不推进游标 → testnet 平仓失败时下轮会重试。"""
    long = pos.get("side", "LONG") == "LONG"
    last_t = kl[-1]["t"] if kl else None
    _apply_stop_upgrade(pos)                 # 旧仓升级/跨轮持仓：先按已持久化的 MFE 对齐止损
    for k in kl:
        if k["t"] < pos["opened_kline_t"] or k["t"] <= pos.get("managed_t", 0):
            continue
        stop_reason = {"BE": "STOP_BE", "TRAIL": "STOP_TRAIL"}.get(pos.get("stop_kind"), "STOP")
        if long:
            if k["l"] <= pos["stop"]:
                return pos["stop"], stop_reason
            if k["h"] >= pos["target"]:
                return pos["target"], "TARGET"
        else:
            if k["h"] >= pos["stop"]:
                return pos["stop"], stop_reason
            if k["l"] <= pos["target"]:
                return pos["target"], "TARGET"
        if k["t"] != last_t:                 # 最后一根是未收盘K线：只查出场，不用于移动止损
            best = pos.get("mfe_price", pos["entry"])
            pos["mfe_price"] = max(best, k["h"]) if long else min(best, k["l"])
            _apply_stop_upgrade(pos)
            pos["managed_t"] = k["t"]
    return None, None


def main():
    c = cfg()
    ddir = c["DATA_DIR"]
    # live 用独立数据文件（_live 后缀），与 testnet 历史完全隔离、互不污染
    sfx = "_live" if c["MODE"] == "live" else ""
    fpath = lambda name: os.path.join(ddir, f"{name}{sfx}.json")
    state = load_json(fpath("bot_state"), fresh_state())
    trades = load_json(fpath("bot_trades"), [])
    state["mode"] = c["MODE"]
    tn, fatal = make_client(c)
    if fatal:
        print(f"[fatal] {fatal}")
        save_json(fpath("bot_log"), {"ts": now_iso(), "mode": c["MODE"], "fatal": fatal, "items": []})
        return

    entries_blocked = None   # 非 None = 本轮禁止开新仓的原因（已有持仓的出场管理照常）
    if c["MODE"] == "live":
        # 实盘前置自检：连接/持仓模式/余额护栏，任何一步失败都绝不交易
        try:
            if tn.position_mode_dual():
                print("[fatal] 实盘账户为双向持仓(hedge)模式，请在币安合约设置改为【单向持仓】后再跑")
                return
            bal = tn.wallet_balance("USDT")
        except Exception as e:
            print(f"[fatal] 实盘连接自检失败，本轮不交易: {e}")
            return
        if not state.get("live_init"):
            # 首次实盘运行：以真实钱包余额为起点建账
            state.update({"live_init": True, "equity0": bal, "equity": bal,
                          "day_start_equity": bal, "peak_equity": bal, "day": ""})
            print(f"[live] 首次运行建账，起始权益 {bal:.2f}U")
        state["equity"] = bal   # 实盘权益始终以真实余额为准（资金费率/滑点自然计入）
        log_bal = round(bal, 2)
        if bal > c["LIVE_MAX_EQUITY"]:
            entries_blocked = f"合约钱包余额 {bal:.0f}U > 上限 {c['LIVE_MAX_EQUITY']:.0f}U，仅管理持仓、不开新仓（请把多余资金移出合约钱包）"
            print(f"[guard] {entries_blocked}")
        print(f"[live] 已连接主网，钱包余额 {bal:.2f}U")

    today = now_iso()[:10]
    if state.get("day") != today:
        state["day"] = today
        state["day_start_equity"] = state["equity"]

    day_pnl = (state["equity"] / state["day_start_equity"] - 1) * 100 if state["day_start_equity"] else 0
    total_dd = (state["peak_equity"] - state["equity"]) / state["peak_equity"] * 100 if state["peak_equity"] else 0
    open_syms = {p["symbol"] for p in state["positions"]}
    log = {"ts": now_iso(), "mode": c["MODE"], "equity": round(state["equity"], 2),
           "day_pnl_pct": round(day_pnl, 2), "total_dd_pct": round(total_dd, 2), "items": []}

    if c["MODE"] == "live":
        log["live_usdt"] = log_bal
        if entries_blocked:
            log["guard"] = entries_blocked

    # testnet 连接自检：确认 key 可用 + 打印模拟盘 USDT 余额
    if c["MODE"] == "testnet":
        if tn:
            try:
                usdt = tn.free_balance("USDT")
                log["testnet_usdt"] = round(usdt, 2)
                print(f"[testnet] 已连接，USDT 可用余额 {usdt:.2f}")
            except Exception as e:
                log["testnet_error"] = str(e)
                print(f"[warn] testnet 连接失败（检查 key/时间）: {e}")
        else:
            log["testnet_error"] = "未配置 BINANCE_TESTNET_KEY/SECRET"
            print("[warn] MODE=testnet 但未配置 testnet key，本次不会真下单")

    # 1) 管理现有持仓（止损/止盈）
    still_open = []
    closed_this_run = set()
    for pos in state["positions"]:
        try:
            kl = exchange.klines(pos["symbol"], "1h", 48)   # 48根覆盖 cron 断档
        except Exception as e:
            print(f"[warn] {pos['symbol']} 取K线失败: {e}")
            still_open.append(pos)
            continue
        exit_price, reason = manage_position(pos, kl)       # 含保本/跟踪止盈的逐K线管理
        if exit_price is None:
            exit_price, reason = time_exit(pos, kl, now_iso())   # 行动卡最大持仓时间
        if exit_price is None:
            still_open.append(pos)
            continue
        if tn:
            try:
                filt = exchange.futures_filters(pos["symbol"], tn.base)
                close_qty = exchange.round_step(pos["qty"], filt["step"])
                if close_qty <= 0:
                    raise Exception("可平数量为 0")
                res = tn.market_close(pos["symbol"], pos.get("side", "LONG"),
                                      exchange.fmt_qty(close_qty, filt["step"]))
                exit_price = _avg_fill(res, exit_price)
            except Exception as e:
                print(f"[warn] {c['MODE']} 平仓失败，保留持仓下次重试: {e}")
                still_open.append(pos)
                continue
        trade = settle_close(pos, exit_price, reason)
        state["equity"] += trade["pnl"]
        trades.append(trade)
        closed_this_run.add(pos["symbol"])
        log["items"].append({"symbol": pos["symbol"], "action": f"CLOSE {reason}",
                             "price": round(exit_price, 2), "pnl": round(trade["pnl"], 2)})
        print(f"[exit] {pos['symbol']} {reason} @ {exit_price:.2f} pnl={trade['pnl']:.2f}")
    state["positions"] = still_open
    open_syms = {p["symbol"] for p in state["positions"]}

    # 2) 整合信息面（每轮抓一次，喂给 LLM 做小时级判断）
    news_text = ""
    try:
        news_text = strategy.fetch_news(c)
        log["news_lines"] = len([x for x in news_text.split("\n") if x.startswith("-")])
    except Exception as e:
        print(f"[warn] 新闻抓取失败: {e}")
    xm = strategy.fetch_cross_market()          # 第一层·领先信息（纳指期指/美债/美元/MSTR…）
    if xm.get("nq_chg") is not None or xm.get("mstr_chg") is not None:
        log["global_risk"] = strategy.classify_global_risk(xm)[0]
        log["xm"] = xm                          # 完整快照给看板「一日节奏」盘前卡复用
    log["phase"] = strategy.session_phase()

    # 3) 寻找新入场
    for sym in CORE:
        if sym in open_syms or sym in closed_this_run:   # 同周期刚平仓则冷却，不立即回补
            continue
        if entries_blocked:                              # live 余额护栏触发：只出不进
            log["items"].append({"symbol": sym, "action": "skip", "reason": entries_blocked})
            continue
        try:
            decision, evidence, ind = strategy.analyze(sym, c, news_text, xm)
        except Exception as e:
            print(f"[warn] {sym} 分析失败: {e}")
            continue
        open_notional = sum(p.get("notional", 0) for p in state["positions"])
        ok, reason, plan = risk.vet(sym, decision, state["equity"], ind,
                                    len(state["positions"]), day_pnl, total_dd,
                                    open_notional=open_notional)
        if ok and c["MODE"] == "live":
            plan = _apply_live_cap(plan, c["LIVE_MAX_NOTIONAL"])   # 实盘单笔名义硬顶
        # 行动卡完整记录（看板"最近一次决策"渲染成行动卡）
        item = {"symbol": sym, "source": decision.get("_source"), "bias": decision.get("bias"),
                "confidence": decision.get("confidence"), "vetted": ok, "reason": reason,
                "rationale": decision.get("rationale", ""),
                "market_state": decision.get("market_state"),
                "entry_low": decision.get("entry_low"), "entry_high": decision.get("entry_high"),
                "invalidation": decision.get("invalidation"),
                "max_hold_hours": decision.get("max_hold_hours"),
                "risk_flags": decision.get("risk_flags")}
        if ok and c["MODE"] != "dry":
            kl_t = exchange.klines(sym, "1h", 1)[-1]["t"]
            entry = plan["entry"]
            side = plan["side"]
            if tn:
                try:
                    tn.set_leverage(sym, c["LEVERAGE"])
                    filt = exchange.futures_filters(sym, tn.base)
                    qty = exchange.round_step(plan["qty"], filt["step"])
                    if qty < filt["min_qty"] or qty * entry < filt["min_notional"]:
                        raise Exception(f"低于交易所最小下单量/名义({filt['min_qty']}/{filt['min_notional']})")
                    res = tn.market_open(sym, side, exchange.fmt_qty(qty, filt["step"]))
                    entry = _avg_fill(res, entry)
                    plan["qty"] = float(res.get("executedQty", qty)) or qty
                except Exception as e:
                    print(f"[warn] {c['MODE']} 开仓失败，跳过本次: {e}")
                    item["action"] = "open-failed"
                    item["reason"] = str(e)
                    log["items"].append(item)
                    continue
            pos = {"symbol": sym, "side": side, "entry": entry,
                   "stop": plan["stop"], "target": plan["target"], "qty": plan["qty"],
                   "notional": plan["notional"], "risk_usdt": plan["risk_usdt"],
                   "fee_in": plan["notional"] * FEE, "confidence": plan["confidence"],
                   "opened_at": now_iso(), "opened_kline_t": kl_t,
                   "stop_kind": "INIT", "snapshot": _snapshot(ind, decision)}
            mh = decision.get("max_hold_hours")
            if isinstance(mh, (int, float)) and mh > 0:
                pos["max_hold_hours"] = min(MAX_HOLD_CLAMP[1], max(MAX_HOLD_CLAMP[0], float(mh)))
            state["positions"].append(pos)
            item["action"] = f"OPEN {side}"
            item["price"] = round(entry, 2)
            print(f"[open] {sym} {side} @ {entry:.2f} stop={plan['stop']} target={plan['target']}")
        else:
            item["action"] = "skip" if not ok else "dry"
        log["items"].append(item)

    state["peak_equity"] = max(state["peak_equity"], state["equity"])
    state["updated_at"] = now_iso()
    save_json(fpath("bot_state"), state)
    save_json(fpath("bot_trades"), trades[-500:])
    save_json(fpath("bot_log"), log)
    print(f"[done] mode={c['MODE']} equity={state['equity']:.2f} "
          f"open={len(state['positions'])} trades={len(trades)}")


def _fmt_qty(q):
    return f"{q:.5f}".rstrip("0").rstrip(".")


def _snapshot(ind, decision):
    """定格决策时刻的市场指标（随持仓存档、平仓时进 trades），下次复盘可按条件分组统计。"""
    r2 = lambda x, d=2: (round(x, d) if isinstance(x, (int, float)) else None)
    return {
        "dev_pct": r2(ind.get("dev_pct")),            # 30小时均线偏离%
        "rsi14": r2(ind.get("rsi14"), 1),             # RSI
        "atr_pct": r2(ind.get("atr_pct")),            # 小时波动率%
        "funding_pct": r2(ind.get("funding_pct"), 4), # 资金费率%/8h
        "fng": ind.get("fng"),                        # 恐惧贪婪指数
        "fng_label": ind.get("fng_label"),
        "regime": ind.get("regime"),                  # 日线状态 risk-on/off/neutral
        "daily_dev_pct": r2(ind.get("daily_dev_pct")),# 30日均线偏离%
        "source": decision.get("_source"),            # llm / rule
        "confidence": decision.get("confidence"),
        # —— 第一层·领先信息（跨市场）——
        "global_risk": ind.get("global_risk"),        # risk-on/off/mixed/unknown
        "nq_chg": r2((ind.get("xm") or {}).get("nq_chg")),
        "dxy_chg": r2((ind.get("xm") or {}).get("dxy_chg")),
        "mstr_chg": r2((ind.get("xm") or {}).get("mstr_chg")),
        "ethbtc_chg": r2((ind.get("xm") or {}).get("ethbtc_chg")),
        "solbtc_chg": r2((ind.get("xm") or {}).get("solbtc_chg")),
        "oi_chg_pct": r2(ind.get("oi_chg_pct")),
        "market_state": decision.get("market_state"), # LLM 行动卡判定
    }


def _hold_hours(opened_at, closed_at):
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        o = datetime.strptime(opened_at, fmt)
        c = datetime.strptime(closed_at, fmt)
        return max(0.0, (c - o).total_seconds() / 3600)
    except Exception:
        return 0.0


def settle_close(pos, exit_price, reason, tag=""):
    """平仓记账（小时主循环与分钟级守护层共用同一本账）。返回完整 trade 记录。"""
    long = pos.get("side", "LONG") == "LONG"
    gross = (exit_price - pos["entry"]) * pos["qty"] if long else (pos["entry"] - exit_price) * pos["qty"]
    fee_out = exit_price * pos["qty"] * FEE
    fee_total = pos.get("fee_in", 0) + fee_out
    pnl = gross - pos.get("fee_in", 0) - fee_out
    closed = now_iso()
    mfe_price = pos.get("mfe_price", pos["entry"])
    mfe_pct = max(0.0, (mfe_price / pos["entry"] - 1) * 100 if long else (pos["entry"] / mfe_price - 1) * 100)
    hold_h = _hold_hours(pos.get("opened_at"), closed)
    analysis = _trade_analysis(pos, reason, pnl, mfe_pct) + (tag or "")
    return {**pos, "exit": exit_price, "exit_reason": reason, "pnl": round(pnl, 4),
            "r": round(pnl / pos["risk_usdt"], 2) if pos.get("risk_usdt") else 0,
            "outcome": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BE",
            "fee_total": round(fee_total, 4), "mfe_pct": round(mfe_pct, 2),
            "hold_hours": round(hold_h, 1), "analysis": analysis,
            "closed_at": closed}


def _trade_analysis(pos, reason, pnl, mfe_pct):
    """生成一句话盈亏归因，便于复盘。"""
    side = "做多" if pos.get("side", "LONG") == "LONG" else "做空"
    if reason == "TARGET":
        return f"{side}·顺利止盈（最佳浮盈 {mfe_pct:.1f}%，达标）"
    if reason == "STOP_TRAIL":
        return f"{side}·跟踪止盈离场（最佳浮盈 {mfe_pct:.1f}%，锁定过半利润）"
    if reason == "STOP_BE":
        return f"{side}·曾浮盈 {mfe_pct:.1f}% 后回落，保本离场（免于 -1R）"
    if reason == "TIME":
        w = "小赚" if pnl > 0 else "小亏" if pnl < 0 else "平手"
        return f"{side}·超过最大持仓时间离场({w})——原判断未按预期兑现，释放资金"
    if reason == "STOP":
        if pnl >= 0:
            return f"{side}·保本/微利离场"
        return f"{side}·入场后即走反，触发止损（方向判断偏差）"
    return f"{side}·{reason}"


def _avg_fill(res, fallback):
    try:
        # 合约 RESULT 响应直接带 avgPrice
        if res.get("avgPrice") and float(res["avgPrice"]) > 0:
            return float(res["avgPrice"])
        fills = res.get("fills") or []
        if fills:
            tot = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            qty = sum(float(f["qty"]) for f in fills)
            return tot / qty if qty else fallback
        if res.get("cummulativeQuoteQty") and res.get("executedQty"):
            return float(res["cummulativeQuoteQty"]) / float(res["executedQty"])
    except Exception:
        pass
    return fallback


if __name__ == "__main__":
    main()
