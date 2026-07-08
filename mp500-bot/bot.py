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


def cfg():
    return {
        "MODE": os.getenv("MODE", "sim").lower(),
        "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "deepseek"),
        "LLM_API_KEY": os.getenv("LLM_API_KEY", ""),
        "LLM_MODEL": os.getenv("LLM_MODEL", "deepseek-chat"),
        "TN_KEY": os.getenv("BINANCE_TESTNET_KEY", ""),
        "TN_SECRET": os.getenv("BINANCE_TESTNET_SECRET", ""),
        "LEVERAGE": int(os.getenv("LEVERAGE", "1")),
        "MARKETAUX_KEY": os.getenv("MARKETAUX_KEY", ""),
        "DATA_DIR": os.getenv("DATA_DIR", "./data"),
    }


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
    state = load_json(os.path.join(ddir, "bot_state.json"), fresh_state())
    trades = load_json(os.path.join(ddir, "bot_trades.json"), [])
    state["mode"] = c["MODE"]
    tn = exchange.Futures(c["TN_KEY"], c["TN_SECRET"]) if c["MODE"] == "testnet" and c["TN_KEY"] else None

    today = now_iso()[:10]
    if state.get("day") != today:
        state["day"] = today
        state["day_start_equity"] = state["equity"]

    day_pnl = (state["equity"] / state["day_start_equity"] - 1) * 100 if state["day_start_equity"] else 0
    total_dd = (state["peak_equity"] - state["equity"]) / state["peak_equity"] * 100 if state["peak_equity"] else 0
    open_syms = {p["symbol"] for p in state["positions"]}
    log = {"ts": now_iso(), "mode": c["MODE"], "equity": round(state["equity"], 2),
           "day_pnl_pct": round(day_pnl, 2), "total_dd_pct": round(total_dd, 2), "items": []}

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
            still_open.append(pos)
            continue
        if tn:
            try:
                filt = exchange.futures_filters(pos["symbol"])
                close_qty = exchange.round_step(pos["qty"], filt["step"])
                if close_qty <= 0:
                    raise Exception("可平数量为 0")
                res = tn.market_close(pos["symbol"], pos.get("side", "LONG"),
                                      exchange.fmt_qty(close_qty, filt["step"]))
                exit_price = _avg_fill(res, exit_price)
            except Exception as e:
                print(f"[warn] testnet 平仓失败，保留持仓下次重试: {e}")
                still_open.append(pos)
                continue
        long = pos.get("side", "LONG") == "LONG"
        gross = (exit_price - pos["entry"]) * pos["qty"] if long else (pos["entry"] - exit_price) * pos["qty"]
        fee_out = exit_price * pos["qty"] * FEE
        fee_total = pos.get("fee_in", 0) + fee_out
        pnl = gross - pos.get("fee_in", 0) - fee_out
        closed = now_iso()
        mfe_price = pos.get("mfe_price", pos["entry"])
        mfe_pct = max(0.0, (mfe_price / pos["entry"] - 1) * 100 if long else (pos["entry"] / mfe_price - 1) * 100)
        hold_h = _hold_hours(pos.get("opened_at"), closed)
        analysis = _trade_analysis(pos, reason, pnl, mfe_pct)
        state["equity"] += pnl
        trades.append({**pos, "exit": exit_price, "exit_reason": reason, "pnl": round(pnl, 4),
                       "r": round(pnl / pos["risk_usdt"], 2) if pos.get("risk_usdt") else 0,
                       "outcome": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BE",
                       "fee_total": round(fee_total, 4), "mfe_pct": round(mfe_pct, 2),
                       "hold_hours": round(hold_h, 1), "analysis": analysis,
                       "closed_at": closed})
        closed_this_run.add(pos["symbol"])
        log["items"].append({"symbol": pos["symbol"], "action": f"CLOSE {reason}",
                             "price": round(exit_price, 2), "pnl": round(pnl, 2)})
        print(f"[exit] {pos['symbol']} {reason} @ {exit_price:.2f} pnl={pnl:.2f}")
    state["positions"] = still_open
    open_syms = {p["symbol"] for p in state["positions"]}

    # 2) 整合新闻信息面（每轮抓一次，喂给 LLM 做小时级判断）
    news_text = ""
    try:
        news_text = strategy.fetch_news(c)
        log["news_lines"] = len([x for x in news_text.split("\n") if x.startswith("-")])
    except Exception as e:
        print(f"[warn] 新闻抓取失败: {e}")

    # 3) 寻找新入场
    for sym in CORE:
        if sym in open_syms or sym in closed_this_run:   # 同周期刚平仓则冷却，不立即回补
            continue
        try:
            decision, evidence, ind = strategy.analyze(sym, c, news_text)
        except Exception as e:
            print(f"[warn] {sym} 分析失败: {e}")
            continue
        open_notional = sum(p.get("notional", 0) for p in state["positions"])
        ok, reason, plan = risk.vet(sym, decision, state["equity"], ind,
                                    len(state["positions"]), day_pnl, total_dd,
                                    open_notional=open_notional)
        item = {"symbol": sym, "source": decision.get("_source"), "bias": decision.get("bias"),
                "confidence": decision.get("confidence"), "vetted": ok, "reason": reason,
                "rationale": decision.get("rationale", "")}
        if ok and c["MODE"] != "dry":
            kl_t = exchange.klines(sym, "1h", 1)[-1]["t"]
            entry = plan["entry"]
            side = plan["side"]
            if tn:
                try:
                    tn.set_leverage(sym, c["LEVERAGE"])
                    filt = exchange.futures_filters(sym)
                    qty = exchange.round_step(plan["qty"], filt["step"])
                    if qty < filt["min_qty"] or qty * entry < filt["min_notional"]:
                        raise Exception(f"低于交易所最小下单量/名义({filt['min_qty']}/{filt['min_notional']})")
                    res = tn.market_open(sym, side, exchange.fmt_qty(qty, filt["step"]))
                    entry = _avg_fill(res, entry)
                    plan["qty"] = float(res.get("executedQty", qty)) or qty
                except Exception as e:
                    print(f"[warn] testnet 开仓失败，跳过本次: {e}")
                    item["action"] = "testnet-open-failed"
                    item["reason"] = str(e)
                    log["items"].append(item)
                    continue
            pos = {"symbol": sym, "side": side, "entry": entry,
                   "stop": plan["stop"], "target": plan["target"], "qty": plan["qty"],
                   "notional": plan["notional"], "risk_usdt": plan["risk_usdt"],
                   "fee_in": plan["notional"] * FEE, "confidence": plan["confidence"],
                   "opened_at": now_iso(), "opened_kline_t": kl_t,
                   "stop_kind": "INIT", "snapshot": _snapshot(ind, decision)}
            state["positions"].append(pos)
            item["action"] = f"OPEN {side}"
            item["price"] = round(entry, 2)
            print(f"[open] {sym} {side} @ {entry:.2f} stop={plan['stop']} target={plan['target']}")
        else:
            item["action"] = "skip" if not ok else "dry"
        log["items"].append(item)

    state["peak_equity"] = max(state["peak_equity"], state["equity"])
    state["updated_at"] = now_iso()
    save_json(os.path.join(ddir, "bot_state.json"), state)
    save_json(os.path.join(ddir, "bot_trades.json"), trades[-500:])
    save_json(os.path.join(ddir, "bot_log.json"), log)
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
    }


def _hold_hours(opened_at, closed_at):
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        o = datetime.strptime(opened_at, fmt)
        c = datetime.strptime(closed_at, fmt)
        return max(0.0, (c - o).total_seconds() / 3600)
    except Exception:
        return 0.0


def _trade_analysis(pos, reason, pnl, mfe_pct):
    """生成一句话盈亏归因，便于复盘。"""
    side = "做多" if pos.get("side", "LONG") == "LONG" else "做空"
    if reason == "TARGET":
        return f"{side}·顺利止盈（最佳浮盈 {mfe_pct:.1f}%，达标）"
    if reason == "STOP_TRAIL":
        return f"{side}·跟踪止盈离场（最佳浮盈 {mfe_pct:.1f}%，锁定过半利润）"
    if reason == "STOP_BE":
        return f"{side}·曾浮盈 {mfe_pct:.1f}% 后回落，保本离场（免于 -1R）"
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
