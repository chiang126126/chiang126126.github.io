"""MP500 paper 机器人 —— 每次运行执行一个决策+执行周期。
MODE: dry(只决策不下单) | sim(用真实价格模拟撮合) | testnet(币安现货模拟盘真下单)
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


def cfg():
    return {
        "MODE": os.getenv("MODE", "sim").lower(),
        "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "deepseek"),
        "LLM_API_KEY": os.getenv("LLM_API_KEY", ""),
        "LLM_MODEL": os.getenv("LLM_MODEL", "deepseek-chat"),
        "TN_KEY": os.getenv("BINANCE_TESTNET_KEY", ""),
        "TN_SECRET": os.getenv("BINANCE_TESTNET_SECRET", ""),
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


def manage_exit(pos, kl):
    """检查开仓后是否触及止损/止盈。返回 (exit_price, reason) 或 (None, None)。"""
    for k in kl:
        if k["t"] < pos["opened_kline_t"]:
            continue
        if k["l"] <= pos["stop"]:           # 止损优先（保守）
            return pos["stop"], "STOP"
        if k["h"] >= pos["target"]:
            return pos["target"], "TARGET"
    return None, None


def main():
    c = cfg()
    ddir = c["DATA_DIR"]
    state = load_json(os.path.join(ddir, "bot_state.json"), fresh_state())
    trades = load_json(os.path.join(ddir, "bot_trades.json"), [])
    state["mode"] = c["MODE"]
    tn = exchange.Testnet(c["TN_KEY"], c["TN_SECRET"]) if c["MODE"] == "testnet" and c["TN_KEY"] else None

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
            kl = exchange.klines(pos["symbol"], "1h", 6)
        except Exception as e:
            print(f"[warn] {pos['symbol']} 取K线失败: {e}")
            still_open.append(pos)
            continue
        exit_price, reason = manage_exit(pos, kl)
        if exit_price is None:
            still_open.append(pos)
            continue
        if tn:
            try:
                base = pos["symbol"].replace("USDT", "")
                filt = exchange.symbol_filters(pos["symbol"])
                sell_qty = exchange.round_step(min(pos["qty"], tn.free_balance(base)), filt["step"])
                if sell_qty <= 0:
                    raise Exception("可卖余额为 0")
                res = tn.market_sell_qty(pos["symbol"], exchange.fmt_qty(sell_qty, filt["step"]))
                exit_price = _avg_fill(res, exit_price)
            except Exception as e:
                print(f"[warn] testnet 平仓失败，保留持仓下次重试: {e}")
                still_open.append(pos)
                continue
        gross = (exit_price - pos["entry"]) * pos["qty"]
        fee_out = exit_price * pos["qty"] * FEE
        pnl = gross - pos.get("fee_in", 0) - fee_out
        state["equity"] += pnl
        trades.append({**pos, "exit": exit_price, "exit_reason": reason, "pnl": round(pnl, 4),
                       "r": round(pnl / pos["risk_usdt"], 2) if pos.get("risk_usdt") else 0,
                       "outcome": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BE",
                       "closed_at": now_iso()})
        closed_this_run.add(pos["symbol"])
        log["items"].append({"symbol": pos["symbol"], "action": f"CLOSE {reason}",
                             "price": round(exit_price, 2), "pnl": round(pnl, 2)})
        print(f"[exit] {pos['symbol']} {reason} @ {exit_price:.2f} pnl={pnl:.2f}")
    state["positions"] = still_open
    open_syms = {p["symbol"] for p in state["positions"]}

    # 2) 寻找新入场
    for sym in CORE:
        if sym in open_syms or sym in closed_this_run:   # 同周期刚平仓则冷却，不立即回补
            continue
        try:
            decision, evidence, ind = strategy.analyze(sym, c)
        except Exception as e:
            print(f"[warn] {sym} 分析失败: {e}")
            continue
        ok, reason, plan = risk.vet(sym, decision, state["equity"], ind,
                                    len(state["positions"]), day_pnl, total_dd)
        item = {"symbol": sym, "source": decision.get("_source"), "bias": decision.get("bias"),
                "confidence": decision.get("confidence"), "vetted": ok, "reason": reason,
                "rationale": decision.get("rationale", "")}
        if ok and c["MODE"] != "dry":
            kl_t = exchange.klines(sym, "1h", 1)[-1]["t"]
            entry = plan["entry"]
            if tn:
                try:
                    res = tn.market_buy_quote(sym, plan["notional"])
                    entry = _avg_fill(res, entry)
                    plan["qty"] = float(res.get("executedQty", plan["qty"]))
                except Exception as e:
                    print(f"[warn] testnet 开仓失败，跳过本次: {e}")
                    item["action"] = "testnet-open-failed"
                    item["reason"] = str(e)
                    log["items"].append(item)
                    continue
            pos = {"symbol": sym, "side": "LONG", "entry": entry,
                   "stop": plan["stop"], "target": plan["target"], "qty": plan["qty"],
                   "notional": plan["notional"], "risk_usdt": plan["risk_usdt"],
                   "fee_in": plan["notional"] * FEE, "confidence": plan["confidence"],
                   "opened_at": now_iso(), "opened_kline_t": kl_t}
            state["positions"].append(pos)
            item["action"] = "OPEN LONG"
            item["price"] = round(entry, 2)
            print(f"[open] {sym} LONG @ {entry:.2f} stop={plan['stop']} target={plan['target']}")
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


def _avg_fill(res, fallback):
    try:
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
