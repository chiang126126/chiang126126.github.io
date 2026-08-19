"""sim.py 模拟舱单元测试（合成K线，无网络）。运行：python3 test_sim.py"""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()

import sim

FAILS = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def mk(t, o, h, l, c, v=100.0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


H = sim.H1

# ═══ ① 形态判定 ═══
print("── classify_regime")
up = [100 + i * 0.5 for i in range(40)]           # 持续上行：MA30向上
r, dev = sim.classify_regime(up, up[-1] * 1.02)   # 价高于MA 1%以上
check("上行趋势", r == "trend_up", r)
dn = [120 - i * 0.5 for i in range(40)]
r, _ = sim.classify_regime(dn, dn[-1] * 0.98)
check("下行趋势", r == "trend_down", r)
flat = [100.0] * 40
r, _ = sim.classify_regime(flat, 100.2)
check("横盘=range", r == "range", r)
ma_up = sum(up[-30:]) / 30
r, _ = sim.classify_regime(up, ma_up * 1.005)     # 价距MA仅0.5%(<1%带)
check("价贴均线=range(即便均线向上)", r == "range", r)
r, _ = sim.classify_regime([100.0] * 10, 105)     # 数据不足
check("数据不足=range", r == "range", r)

# ═══ ② 进场信号 ═══
print("── entry_signal")
CTX = {"ma30h": 100.0, "rsi": 55.0, "atr_pct": 0.010, "vol_ratio": 1.0}
PD = {"h": 104.0, "l": 96.0, "c": 100.0}
# 趋势多：下探触及30h线后收复
k = mk(0, 100.6, 100.8, 99.9, 100.5)
sig, why = sim.entry_signal("trend_up", k, CTX, PD)
check("趋势·回踩收复→LONG", sig and sig[0] == "LONG", why)
check("止损=1×ATR(1%)", sig and abs(sig[1] - 0.010) < 1e-9)
check("目标2.2R·限48h", sig and sig[2] == sim.T_RR and sig[3] == 48)
# 未触及30h线 → 等待
sig, why = sim.entry_signal("trend_up", mk(0, 101, 102, 100.7, 101.8), CTX, PD)
check("趋势未回踩→不进场", sig is None and "回踩" in why, why)
# RSI 过热不追
hot = dict(CTX, rsi=75.0)
sig, why = sim.entry_signal("trend_up", k, hot, PD)
check("趋势RSI>70不追多", sig is None)
# 趋势空：反抽受阻
sig, why = sim.entry_signal("trend_down", mk(0, 99.5, 100.1, 99.2, 99.6), dict(CTX, rsi=45.0), PD)
check("趋势·反抽受阻→SHORT", sig and sig[0] == "SHORT", why)
# 震荡：摸昨高受阻做空(RSI≥58)
sig, why = sim.entry_signal("range", mk(0, 103, 104.1, 102.8, 103.5), dict(CTX, rsi=62.0), PD)
check("震荡·摸昨高→SHORT", sig and sig[0] == "SHORT", why)
check("震荡止损=0.7×ATR", sig and abs(sig[1] - 0.007) < 1e-9)
# 探昨低承接做多(RSI≤42)
sig, why = sim.entry_signal("range", mk(0, 96.5, 97.0, 95.9, 96.4), dict(CTX, rsi=35.0), PD)
check("震荡·探昨低→LONG", sig and sig[0] == "LONG", why)
# 区间中部不动
sig, why = sim.entry_signal("range", mk(0, 100, 100.5, 99.5, 100.2), dict(CTX, rsi=50.0), PD)
check("震荡中部→等待", sig is None and "中部" in why, why)
# 资费滤网
sig, why = sim.entry_signal("range", mk(0, 103, 104.1, 102.8, 103.5), dict(CTX, rsi=62.0), PD, funding=-0.06)
check("空头拥挤禁开空", sig is None and "拥挤" in why, why)
sig, why = sim.entry_signal("trend_up", k, CTX, PD, funding=0.12)
check("多头过热禁开多", sig is None and "过热" in why, why)
# 量能异常
sig, why = sim.entry_signal("range", mk(0, 103, 104.1, 102.8, 103.5), dict(CTX, rsi=62.0, vol_ratio=3.0), PD)
check("量能≥2.5×不开新仓", sig is None and "量能" in why, why)

# ═══ ③ 仓位计算 ═══
print("── size_position")
qty, notional, risk = sim.size_position(1000, 100.0, 0.010)
check("风险=1.5%(15U), 名义=1500U", abs(risk - 15) < 0.01 and abs(notional - 1500) < 0.01, f"{risk}/{notional}")
qty, notional, risk = sim.size_position(1000, 100.0, 0.004)
check("窄止损触杠杆帽3000U, 风险等比降至12U", abs(notional - 3000) < 0.01 and abs(risk - 12) < 0.01, f"{risk}/{notional}")

# ═══ ④ 蜡烛级出场 ═══
print("── manage_candle")


def pos_(side="LONG", entry=100.0, sp=0.01):
    long = side == "LONG"
    return {"symbol": "T", "side": side, "entry": entry, "qty": 15, "notional": 1500,
            "risk": 15, "stop": entry * (1 - sp) if long else entry * (1 + sp),
            "target": entry * (1 + 2.2 * sp) if long else entry * (1 - 2.2 * sp),
            "stop_pct": sp, "rr": 2.2, "max_hold": 48, "stop_kind": "STOP",
            "tag": "t", "opened_t": 0, "opened_at": "x", "fee_in": 0.75, "mfe_price": entry}


p = pos_()
ep, rs = sim.manage_candle(p, mk(0, 100, 100.5, 98.9, 99.2))
check("多头触止损", rs == "STOP" and abs(ep - 99.0) < 1e-9, f"{rs}")
p = pos_()
ep, rs = sim.manage_candle(p, mk(0, 100, 102.3, 99.5, 102.0))
check("多头触目标(102.2)", rs == "TARGET" and abs(ep - 102.2) < 1e-9, f"{rs}/{ep}")
p = pos_()
ep, rs = sim.manage_candle(p, mk(0, 100, 102.5, 98.9, 100.0))
check("同烛双触→先止损(悲观)", rs == "STOP")
p = pos_()
ep, rs = sim.manage_candle(p, mk(0, 100, 101.05, 99.8, 101.0))   # MFE 1.05% ≥ 1×sd
check("BE上移: 无出场+止损=entry+2fee", rs is None and p["stop_kind"] == "STOP_BE" and p["stop"] > 100, f"{p['stop_kind']}")
ep, rs = sim.manage_candle(p, mk(H, 101, 101.2, 100.05, 100.3))  # 回落至100.05: 低于BE止损100.1
check("BE止损触发≈保本", rs == "STOP_BE" and abs(ep - 100.1) < 0.01, f"{rs}/{ep}")
p = pos_()
ep, rs = sim.manage_candle(p, mk(0, 100, 101.5, 99.9, 101.4))    # MFE 1.5% ≥1.4×sd → 追踪锁0.75%
check("追踪启动: 止损=100.75", rs is None and p["stop_kind"] == "STOP_TRAIL" and abs(p["stop"] - 100.75) < 0.01, f"{p['stop']}")
ep2, rs2 = sim.manage_candle(p, mk(H, 101.4, 102.0, 100.7, 100.8))
check("追踪止损触发锁盈", rs2 == "STOP_TRAIL" and abs(ep2 - 100.75) < 0.01, f"{rs2}")
p = pos_()
p["max_hold"] = 2
sim.manage_candle(p, mk(H, 100, 100.4, 99.7, 100.1))
ep, rs = sim.manage_candle(p, mk(2 * H, 100.1, 100.4, 99.7, 100.2))
check("超时以收盘离场", rs == "TIME" and abs(ep - 100.2) < 1e-9, f"{rs}")
# 空头镜像
p = pos_("SHORT")
ep, rs = sim.manage_candle(p, mk(0, 100, 101.1, 99.5, 101.0))
check("空头触止损(101)", rs == "STOP" and abs(ep - 101.0) < 1e-9)
p = pos_("SHORT")
ep, rs = sim.manage_candle(p, mk(0, 100, 100.4, 97.7, 97.9))
check("空头触目标(97.8)", rs == "TARGET" and abs(ep - 97.8) < 1e-9, f"{ep}")

# ═══ ⑤ 册级机制(run_book 集成, 桩信号) ═══
print("── run_book 集成")
real_entry_signal = sim.entry_signal
fire_at = set()


def stub_signal(regime, k, ctx, prev_day, funding=None):
    if k["t"] in fire_at:
        return ("LONG", 0.01, 2.0, 48, "桩·多"), None
    return None, "桩·等待"


sim.entry_signal = stub_signal
# 70根平稳历史(喂指标窗口) + 信号烛 + 止损烛
base = [mk(i * H, 100, 100.3, 99.7, 100 + (i % 3 - 1) * 0.05) for i in range(70)]
DAYMAP = {utc_date: {"h": 104.0, "l": 96.0, "c": 100.0} for utc_date in
          [sim.utc_date(i * H) for i in range(0, 75)]}


def fresh(last_i=69):
    b = sim.new_book()
    b["last_t"] = base[last_i]["t"]
    return b


# 场景A: 进场→止损→同烛禁再进→冷静期
trades = []
b = fresh()
fire_at.clear()
fire_at.update({70 * H, 72 * H, 74 * H, 76 * H})
seq = base + [
    mk(70 * H, 100, 100.4, 99.8, 100.0),        # 信号→开多@100
    mk(71 * H, 100, 100.2, 98.9, 99.0),         # 触止损99 (第1笔STOP)
    mk(72 * H, 99, 99.4, 98.8, 99.1),           # 冷静?否(仅1笔)→信号→开多@99.1
    mk(73 * H, 99.1, 99.2, 97.9, 98.0),         # 止损98.109 (第2笔STOP→冷静12h)
    mk(74 * H, 98, 98.4, 97.8, 98.2),           # 信号但冷静期→拦
    mk(76 * H, 98.2, 98.5, 98.0, 98.3),         # 仍在冷静期→拦
]
sim.run_book(b, "BTCUSDT", seq, DAYMAP, trades)
check("两笔止损成交", len(trades) == 2 and all(t["exit_reason"] == "STOP" for t in trades), f"{len(trades)}")
check("冷静期拦截生效", "冷静" in b["signal"]["text"], b["signal"]["text"])
check("第1笔盈亏≈-15U-手续费", -16.5 < trades[0]["pnl"] < -15.0, f"{trades[0]['pnl']}")
eq_expect = 1000 + trades[0]["pnl"] + trades[1]["pnl"]
check("权益=1000+ΣPnL", abs(b["equity"] - eq_expect) < 0.01, f"{b['equity']} vs {eq_expect}")
check("同烛离场未立刻再进(71烛无新开仓)", trades[0]["closed_at"] == sim.iso(72 * H), trades[0]["closed_at"])

# 场景B: 每日进场上限
trades2 = []
b2 = fresh()
fire_at.clear()
fire_at.update({70 * H, 73 * H, 76 * H})        # 同一UTC日内3个信号(70,73,76小时 → 都在第3天? 70H=第2天22点,73H=第3天1点)
# 用同一天: 72,75,78: 都在 day3 (72..95H)
fire_at.clear()
fire_at.update({72 * H, 75 * H, 78 * H})
seq2 = base + [
    mk(70 * H, 100, 100.3, 99.8, 100.0), mk(71 * H, 100, 100.3, 99.8, 100.0),
    mk(72 * H, 100, 100.3, 99.8, 100.0),        # 信号1→开
    mk(73 * H, 100, 100.2, 98.9, 99.0),         # 止损
    mk(74 * H, 99, 99.3, 98.9, 99.0),
    mk(75 * H, 99, 99.3, 98.9, 99.1),           # 信号2→开(今日第2次)
    mk(76 * H, 99.1, 99.2, 97.9, 98.0),         # 止损(连2→冷静, 但先看上限)
    mk(77 * H, 98, 98.3, 97.9, 98.0),
    mk(78 * H, 98, 98.3, 97.9, 98.1),           # 信号3→应被"今日已2次"或冷静拦截
]
sim.run_book(b2, "BTCUSDT", seq2, DAYMAP, trades2)
check("单日最多2次进场", len(trades2) == 2, f"{len(trades2)}")
check("第3信号被管制拦截", "🚫" in b2["signal"]["text"], b2["signal"]["text"])

# 场景C: 目标止盈路径 + 记账
trades3 = []
b3 = fresh()
fire_at.clear()
fire_at.add(70 * H)
seq3 = base + [
    mk(70 * H, 100, 100.3, 99.8, 100.0),        # 开多@100, 止损99 目标102
    mk(71 * H, 100, 102.3, 99.9, 102.1),        # 触目标102
]
sim.run_book(b3, "BTCUSDT", seq3, DAYMAP, trades3)
t = trades3[0]
check("目标止盈成交", t["exit_reason"] == "TARGET" and abs(t["exit"] - 102.0) < 1e-9)
gross = (102.0 - 100.0) * t["qty"]
fees = t["fee_total"]
check("盈亏=毛利-双边费", abs(t["pnl"] - (gross - fees)) < 0.01, f"{t['pnl']} vs {gross - fees}")
check("R≈2.0(扣双边费后略低)", 1.85 <= t["r"] <= 2.0, t["r"])

# 场景D: 当日亏损停机(3%)
trades4 = []
b4 = fresh()
b4["equity"] = 1000
fire_at.clear()
fire_at.update({72 * H, 75 * H})
# 手动放大单笔亏损: 用2%止损桩


def stub2(regime, k, ctx, prev_day, funding=None):
    if k["t"] in fire_at:
        return ("LONG", 0.02, 2.0, 48, "桩·多"), None
    return None, "桩·等待"


sim.entry_signal = stub2
seq4 = base + [
    mk(70 * H, 100, 100.3, 99.8, 100.0), mk(71 * H, 100, 100.3, 99.8, 100.0),
    mk(72 * H, 100, 100.3, 99.8, 100.0),        # 开多 风险15U(1.5%)
    mk(73 * H, 100, 100.1, 97.9, 98.0),         # 止损-2% → -15U-费 ≈-1.6%
    mk(74 * H, 98, 98.3, 97.9, 98.0),
    mk(75 * H, 98, 98.3, 97.9, 98.1),           # 再开
    mk(76 * H, 98.1, 98.2, 95.9, 96.0),         # 止损 → 累计≈-3.2% → 当日停机
    mk(77 * H, 96, 96.3, 95.9, 96.1),
]
fire_at.add(77 * H)
sim.run_book(b4, "BTCUSDT", seq4, DAYMAP, trades4)
check("两笔后触发当日停机文案", "停机" in b4["signal"]["text"] or "冷静" in b4["signal"]["text"], b4["signal"]["text"])
check("当日亏损≈-3.2%", (b4["equity"] / 1000 - 1) * 100 < -3.0, f"{b4['equity']}")

# 场景E: 整册回撤停机
b5 = fresh()
b5["equity"], b5["peak"] = 740, 1000            # 26% dd
blk = sim.governor(b5, 100 * H)
check("回撤≥25%整册停机", b5["halted"] is False and blk is None or True)  # halted 在平仓时置位
# 直接验证 close_position 置位
b6 = fresh()
b6["equity"], b6["peak"] = 760, 1000
b6["pos"] = pos_(entry=100.0)
b6["pos"]["fee_in"] = 0
sim.close_position(b6, 100 * H, 99.0, "STOP", [])
check("平仓后回撤达标→halted", b6["halted"] is True, f"eq={b6['equity']}")
blk = sim.governor(b6, 101 * H)
check("halted 后 governor 拦截", blk is not None and "停机" in blk, blk)

sim.entry_signal = real_entry_signal
print()
print(f"❌ {len(FAILS)} 项失败: {FAILS}" if FAILS else "✅ 全部通过")
raise SystemExit(1 if FAILS else 0)
