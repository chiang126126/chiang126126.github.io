"""MP500 守护层（tactical risk monitor，对应 WebCryptoAgent 双层架构的战术层）。

每 3 分钟由 cron 调一次（run_guardian.sh）。职责【只有一个】：用实时价管理已有持仓——
  1. 用实时价更新 MFE 并上移保本/跟踪止损（分钟级锁盈，只紧不松）；
  2. 实时价触及 止损/止盈/超时 → 立即市价平仓（把止损延迟从 ≤59分钟 压到 ≤3分钟）；
  3. 绝不开新仓、绝不调 LLM、无持仓时秒退——开仓决策永远属于小时级战略层(bot.py)。
覆盖两套系统：主机器人持仓(bot_state) + 模拟舱双册持仓(sim_state, 经 sim.guardian_tick)。

退出码：0=无实质变化；10=状态有变(平仓/移损)，外层脚本据此提交推送。
与 bot.py 通过 run_*.sh 的 mkdir 原子锁互斥，绝不并发写同一份数据。
"""
import os
import sys

import bot
import exchange
import sim


def run():
    c = bot.cfg()
    if c["MODE"] == "dry":
        return 0
    changed_bot = _bot_positions(c)
    try:
        changed_sim = _sim_books()
    except Exception as e:          # 模拟舱任何异常都不能影响主机器人守护
        print(f"[guardian][warn] 模拟舱守护出错(跳过): {e}")
        changed_sim = False
    return 10 if (changed_bot or changed_sim) else 0


def _sim_books():
    """模拟舱持仓的分钟级守护：只对已有持仓做锁盈上移/触线离场，逻辑在 sim.guardian_tick。"""
    state = sim.load_json(sim.fpath("sim_state"), None)
    if not state:
        return False
    holding = [s for s, b in (state.get("books") or {}).items() if b.get("pos")]
    if not holding:
        return False
    prices = {}
    for s in holding:
        try:
            prices[s] = exchange.last_price(s)
        except Exception as e:
            print(f"[guardian][warn] sim {s} 取实时价失败: {e}")
    if not prices:
        return False
    trades = sim.load_json(sim.fpath("sim_trades"), [])
    if not sim.guardian_tick(state, trades, prices):
        return False
    state["guardian_at"] = sim.now_iso()    # 独立心跳，不动 updated_at(小时层断档检测锚)
    sim.save_json(sim.fpath("sim_state"), state)
    sim.save_json(sim.fpath("sim_trades"), trades)
    sim.write_log(state, trades)
    return True


def _bot_positions(c):
    sfx = "_live" if c["MODE"] == "live" else ""
    fpath = lambda name: os.path.join(c["DATA_DIR"], f"{name}{sfx}.json")
    state = bot.load_json(fpath("bot_state"), None)
    if not state or not state.get("positions"):
        return False

    tn, fatal = bot.make_client(c)
    if fatal:                       # live 配置不全：宁可不动，也绝不带病碰真钱
        print(f"[guardian][fatal] {fatal}")
        return False

    trades = bot.load_json(fpath("bot_trades"), [])
    changed = False
    still = []
    for pos in state["positions"]:
        try:
            live = exchange.last_price(pos["symbol"])
        except Exception as e:
            print(f"[guardian][warn] {pos['symbol']} 取实时价失败: {e}")
            still.append(pos)
            continue
        long = pos.get("side", "LONG") == "LONG"

        # 1) 实时价更新 MFE（只朝有利方向），并上移保本/跟踪止损（只紧不松）
        best = pos.get("mfe_price", pos["entry"])
        new_best = max(best, live) if long else min(best, live)
        if new_best != best:
            pos["mfe_price"] = new_best
        old_stop = pos["stop"]
        bot._apply_stop_upgrade(pos)
        if pos["stop"] != old_stop:
            changed = True
            print(f"[guardian] {pos['symbol']} 锁盈上移 {old_stop} → {pos['stop']} ({pos.get('stop_kind')})")

        # 2) 实时价触线判定（同一实时价先移损后判触发，不存在时序穿越：
        #    移损后的止损位按定义仍在当前价的不利侧，不会被同一价自触发）
        stop_reason = {"BE": "STOP_BE", "TRAIL": "STOP_TRAIL"}.get(pos.get("stop_kind"), "STOP")
        exit_price, reason = None, None
        if long and live <= pos["stop"]:
            exit_price, reason = live, stop_reason
        elif long and live >= pos["target"]:
            exit_price, reason = live, "TARGET"
        elif (not long) and live >= pos["stop"]:
            exit_price, reason = live, stop_reason
        elif (not long) and live <= pos["target"]:
            exit_price, reason = live, "TARGET"
        else:
            mh = pos.get("max_hold_hours")
            if mh and bot._hold_hours(pos.get("opened_at", bot.now_iso()), bot.now_iso()) >= float(mh):
                exit_price, reason = live, "TIME"
        if exit_price is None:
            still.append(pos)
            continue

        # 3) 平仓：testnet/live 真下单（实际成交价入账）；sim 按实时价模拟
        if tn:
            try:
                filt = exchange.futures_filters(pos["symbol"], tn.base)
                q = exchange.round_step(pos["qty"], filt["step"])
                if q <= 0:
                    raise Exception("可平数量为 0")
                res = tn.market_close(pos["symbol"], pos.get("side", "LONG"),
                                      exchange.fmt_qty(q, filt["step"]))
                exit_price = bot._avg_fill(res, exit_price)
            except Exception as e:
                print(f"[guardian][warn] {c['MODE']} 平仓失败，下轮重试: {e}")
                still.append(pos)
                continue
        trade = bot.settle_close(pos, exit_price, reason, tag="（守护层分钟级执行）")
        state["equity"] += trade["pnl"]
        trades.append(trade)
        changed = True
        print(f"[guardian][exit] {pos['symbol']} {reason} @ {exit_price:.2f} pnl={trade['pnl']:.2f}")

    if not changed:
        return False
    state["positions"] = still
    state["peak_equity"] = max(state.get("peak_equity", state["equity"]), state["equity"])
    state["guardian_at"] = bot.now_iso()     # 守护层独立心跳；不动 updated_at（那是小时层的断档检测锚）
    bot.save_json(fpath("bot_state"), state)
    bot.save_json(fpath("bot_trades"), trades[-500:])
    return True


if __name__ == "__main__":
    sys.exit(run())
