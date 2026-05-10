"""regime_backtest.py — P27-C: 按行情体制分桶回测,生成 regime_stats.json

读取:
  ~/cresus-bot/pnl.json           closed_recent (已平仓交易)
  ~/cresus-bot/regime_history.jsonl  历史体制快照

对每个已平仓交易,找到开仓时刻 (open_ts) 对应的体制,
分桶统计每个体制下的: 样本数 / 胜率 / 平均盈亏 / 总盈亏 / 平均持仓时长.

输出: ~/cresus-bot/regime_stats.json

未来 bot 可读这个文件,用真实数据校准 regime_rules 的预设参数.

CLI:
  python3 regime_backtest.py        # 运行回测,输出 stats + 表格
  python3 regime_backtest.py --quiet # 仅写文件,不打印
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

PNL_FILE     = Path.home() / "cresus-bot" / "pnl.json"
REGIME_HIST  = Path.home() / "cresus-bot" / "regime_history.jsonl"
OUTPUT       = Path.home() / "cresus-bot" / "regime_stats.json"

REGIME_ZH = {
    "ALT_SEASON_RUNNING": "山寨行情",
    "BTC_DOMINATING":     "BTC 主导",
    "RISK_OFF":           "风险释放",
    "PUMP_AND_DUMP_RISK": "拉砸风险",
    "RANGE_BORING":       "震荡死水",
}


def parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def load_regime_history() -> List[Tuple[datetime, str]]:
    """Return list of (ts, regime) sorted by ts."""
    if not REGIME_HIST.exists():
        return []
    out = []
    for line in REGIME_HIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            t = parse_ts(r.get("ts"))
            if t and r.get("regime"):
                out.append((t, r["regime"]))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def find_regime_at(history: List[Tuple[datetime, str]], ts: datetime) -> Optional[str]:
    """Return regime active at ts (latest entry with t <= ts)."""
    if not history:
        return None
    best = None
    for t, regime in history:
        if t > ts:
            break
        best = regime
    return best


def run_backtest(verbose: bool = True) -> dict:
    history = load_regime_history()
    if not history:
        print("[regime_backtest] ⚠️ 未找到 regime_history.jsonl, 无法回测", file=sys.stderr)
        return {}

    try:
        pnl = json.loads(PNL_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[regime_backtest] ❌ pnl.json 读取失败: {e}", file=sys.stderr)
        return {}

    closes = pnl.get("closed_recent", [])
    if not closes:
        print("[regime_backtest] ⚠️ pnl.json 无已平仓交易", file=sys.stderr)
        return {}

    if verbose:
        print(f"[regime_backtest] 加载 {len(closes)} 条平仓记录 + {len(history)} 条体制快照")

    stats = {}
    unmatched = 0
    for c in closes:
        # 优先用 open_ts, 否则用 ts (近似)
        ot = parse_ts(c.get("open_ts")) or parse_ts(c.get("ts"))
        ct = parse_ts(c.get("ts"))
        if not ot:
            continue
        regime = find_regime_at(history, ot)
        if not regime:
            unmatched += 1
            continue

        s = stats.setdefault(regime, {
            "samples": 0, "wins": 0, "losses": 0,
            "total_pnl": 0.0, "total_hold_sec": 0.0, "hold_count": 0,
        })
        s["samples"] += 1
        net = float(c.get("net", 0) or 0)
        s["total_pnl"] += net
        if net > 0:
            s["wins"] += 1
        elif net < 0:
            s["losses"] += 1
        if ot and ct and ct > ot:
            s["total_hold_sec"] += (ct - ot).total_seconds()
            s["hold_count"] += 1

    # 派生指标
    out = {}
    for regime, s in stats.items():
        total = s["wins"] + s["losses"]
        wr = s["wins"] / total if total > 0 else 0
        avg_pnl = s["total_pnl"] / s["samples"] if s["samples"] > 0 else 0
        avg_hold_min = (s["total_hold_sec"] / s["hold_count"] / 60) if s["hold_count"] > 0 else None
        out[regime] = {
            "name_zh":           REGIME_ZH.get(regime, regime),
            "samples":           s["samples"],
            "wins":              s["wins"],
            "losses":            s["losses"],
            "win_rate":          round(wr, 4),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "total_pnl":         round(s["total_pnl"], 2),
            "avg_holding_min":   round(avg_hold_min, 1) if avg_hold_min is not None else None,
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_range": {
            "regime_snapshots": len(history),
            "closed_trades":    len(closes),
            "matched_trades":   sum(s["samples"] for s in stats.values()),
            "unmatched":        unmatched,
        },
        "regimes": out,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUTPUT)

    if verbose:
        print(f"\n[regime_backtest] ✅ 输出: {OUTPUT}")
        if not out:
            print("[regime_backtest] (无有效匹配)")
            return payload
        print()
        print(f"{'体制':<10}  {'样本':>5}  {'胜率':>7}  {'均盈亏':>9}  {'总盈亏':>10}  {'平均持仓':>10}")
        print("─" * 60)
        # 按总盈亏降序
        rows = sorted(out.items(), key=lambda kv: -kv[1]["total_pnl"])
        for regime, s in rows:
            avg_hold = f"{s['avg_holding_min']:.0f}m" if s["avg_holding_min"] else "—"
            print(f"{s['name_zh']:<10}  {s['samples']:>5}  {s['win_rate']*100:>6.1f}%  "
                  f"{s['avg_pnl_per_trade']:>+9.2f}  {s['total_pnl']:>+10.2f}  {avg_hold:>10}")
        print()
        # 策略建议
        print("=== 策略校准建议 ===")
        for regime, s in rows:
            wr = s["win_rate"]
            if s["samples"] < 5:
                hint = "样本太少,继续积累"
            elif wr >= 0.60:
                hint = "💚 可放手脚 (建议门槛 -10, risk 1.5x)"
            elif wr >= 0.50:
                hint = "🟡 中性,维持默认"
            elif wr >= 0.40:
                hint = "🟠 收紧 (建议门槛 +10, risk 0.5x)"
            else:
                hint = "🔴 严控 (建议门槛 +20, risk 0.3x, 或考虑禁开)"
            print(f"  {s['name_zh']:<10}  胜率 {wr*100:.0f}%  →  {hint}")
    return payload


def main(argv) -> int:
    p = argparse.ArgumentParser(description="P27-C 按行情体制分桶回测")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    out = run_backtest(verbose=not args.quiet)
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
