"""确定性风控引擎：对 LLM/规则的决策做一票否决 + 仓位计算 + 熔断。"""

MAX_RISK_PCT = 0.01          # 单笔风险 ≤ 1%（现货 paper）
MIN_CONFIDENCE = 0.55        # 置信度门槛
FEE = 0.001                  # 0.1%/边
MIN_RR = 1.5                 # 最小盈亏比（目标/止损）
MAX_OPEN = 2                 # 同时最多持仓数
DAILY_LOSS_STOP = 0.02       # 当日亏损达 2% 停手
TOTAL_DD_KILL = 0.20         # 总回撤 20% kill switch


def vet(symbol, decision, equity, ind, open_count, day_pnl_pct, total_dd_pct):
    """返回 (ok: bool, reason: str, plan: dict|None)。"""
    # 熔断优先
    if total_dd_pct >= TOTAL_DD_KILL * 100:
        return False, f"总回撤 {total_dd_pct:.1f}% ≥ 20%，kill switch", None
    if day_pnl_pct <= -DAILY_LOSS_STOP * 100:
        return False, f"当日亏损 {day_pnl_pct:.1f}% 触发停手", None
    if open_count >= MAX_OPEN:
        return False, f"持仓已达上限 {MAX_OPEN}", None

    if decision.get("bias") != "LONG":
        return False, "决策为 FLAT/非做多，观望", None
    conf = float(decision.get("confidence", 0))
    if conf < MIN_CONFIDENCE:
        return False, f"置信度 {conf:.2f} < {MIN_CONFIDENCE}", None

    # 右交易：必须站上 30 小时均线
    if ind.get("dev_pct") is None or ind["dev_pct"] < 0:
        return False, "价格未站上均线，不接飞刀", None

    stop_pct = float(decision.get("stop_pct", 0)) / 100
    target_pct = float(decision.get("target_pct", 0)) / 100
    if stop_pct <= 0:
        return False, "缺少有效止损", None
    rr = target_pct / stop_pct if stop_pct else 0
    if rr < MIN_RR:
        return False, f"盈亏比 {rr:.2f} < {MIN_RR}", None

    # 成本闸门：预期收益必须覆盖来回手续费且仍达标
    if target_pct <= 2 * FEE:
        return False, "预期收益不足以覆盖手续费", None

    entry = ind["price"]
    risk_usdt = equity * MAX_RISK_PCT
    notional = risk_usdt / stop_pct
    notional = min(notional, equity * 0.95)     # 现货不超过可用资金
    qty = notional / entry
    plan = {
        "symbol": symbol, "side": "LONG", "entry": entry,
        "stop": round(entry * (1 - stop_pct), 2),
        "target": round(entry * (1 + target_pct), 2),
        "notional": round(notional, 2), "qty": qty,
        "risk_usdt": round(risk_usdt, 2),
        "confidence": conf,
    }
    return True, "通过风控", plan
