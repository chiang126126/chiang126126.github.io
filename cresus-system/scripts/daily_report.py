#!/usr/bin/env python3
"""Crésus 每日报告生成器 — 每天运行一次，输出 markdown 并保存到 ~/cresus-bot/reports/"""

import json
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

BOT_DIR    = Path.home() / "cresus-bot"
REPORT_DIR = BOT_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None

def load_jsonl(path):
    try:
        return [json.loads(l) for l in Path(path).read_text().strip().splitlines() if l.strip()]
    except Exception:
        return []

def fmt_usdt(v):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f} USDT"

def fmt_pct(v):
    if v is None:
        return "—"
    return f"{v*100:.1f}%"

def age_str(ts_str):
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        mins = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
        if mins < 60:
            return f"{mins}m 前"
        return f"{mins//60}h{mins%60}m 前"
    except Exception:
        return ts_str[:16]

def main():
    today     = date.today()
    today_str = str(today)
    yesterday = str(today - timedelta(days=1))
    report_path = REPORT_DIR / f"{today_str}.md"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    add = lines.append

    add(f"# Crésus 每日报告 · {today_str}")
    add(f"生成时间：{now_str}\n")
    add("---\n")

    # ── Regime ───────────────────────────────────────────────────────────────
    regime = load_json(BOT_DIR / "regime.json")
    REGIME_ZH = {
        "ALT_SEASON_RUNNING": "山寨行情",
        "BTC_DOMINATING":     "BTC 主导",
        "RISK_OFF":           "风险释放",
        "PUMP_AND_DUMP_RISK": "拉砸风险",
        "RANGE_BORING":       "盘整死水",
    }
    if regime:
        rname = regime.get("regime", "—")
        add("## 📊 行情体制（V3 Regime）")
        add(f"| 体制 | 信心值 | 更新 |")
        add(f"|------|--------|------|")
        add(f"| **{REGIME_ZH.get(rname, rname)}** `{rname}` | {regime.get('confidence','—')} | {age_str(regime.get('ts',''))} |")
        add(f"\n> {regime.get('judgment','—')}\n")
    else:
        add("## 📊 行情体制（V3 Regime）\n_未加载_\n")

    # ── PnL ──────────────────────────────────────────────────────────────────
    pnl = load_json(BOT_DIR / "pnl.json")
    if pnl:
        s = pnl.get("summary", {})
        add("## 💰 今日 PnL")
        today_real  = s.get("today_realized_pnl")
        today_fees  = s.get("today_fees")
        net_today   = (today_real or 0) + (today_fees or 0)
        total_real  = s.get("total_realized_pnl")
        add(f"| 指标 | 今日 | 累计 |")
        add(f"|------|------|------|")
        add(f"| 已实现 PnL | {fmt_usdt(today_real)} | {fmt_usdt(total_real)} |")
        add(f"| 手续费     | {fmt_usdt(today_fees)} | — |")
        add(f"| 净收益     | **{fmt_usdt(net_today)}** | — |")
        add(f"| 开仓/平仓  | {s.get('today_opened','—')} / {s.get('today_closed','—')} | {s.get('all_time_closed','—')} 次 |")
        tw, tl = s.get("today_wins",0), s.get("today_losses",0)
        aw, al = s.get("all_time_wins",0), s.get("all_time_losses",0)
        add(f"| 胜率       | {fmt_pct(s.get('win_rate'))} ({tw}W/{tl}L) | {fmt_pct(s.get('all_time_win_rate'))} ({aw}W/{al}L) |")

        # 今日最佳/最差
        closes = pnl.get("closed_recent", [])
        today_closes = [c for c in closes if (c.get("ts") or "").startswith(today_str)]
        if today_closes:
            best  = max(today_closes, key=lambda c: c.get("net", 0))
            worst = min(today_closes, key=lambda c: c.get("net", 0))
            add(f"\n**今日最佳**：`{best['symbol']}` {best.get('side','?')} {fmt_usdt(best.get('net'))}")
            add(f"**今日最差**：`{worst['symbol']}` {worst.get('side','?')} {fmt_usdt(worst.get('net'))}")

        # 当前持仓 — net 模式下方向看 size 正负
        positions = pnl.get("positions", [])
        if positions:
            add(f"\n**当前持仓（{len(positions)} 个）**")
            add(f"| Symbol | 方向 | 数量 | 入场 | 现价 | 未实现 PnL | 收益率 |")
            add(f"|--------|------|-----:|-----:|-----:|-----------:|-------:|")
            for p in positions:
                size = p.get("size", 0) or 0
                direction = "LONG" if size >= 0 else "SHORT"
                upl = p.get("upl")
                upl_ratio = p.get("upl_ratio")
                ratio_str = f"{upl_ratio*100:+.2f}%" if upl_ratio is not None else "—"
                add(f"| `{p.get('symbol','?')}` | {direction} | {abs(size):.0f} | {p.get('entry','—')} | {p.get('mark','—')} | {fmt_usdt(upl)} | {ratio_str} |")
        else:
            add("\n当前持仓：无")
        add("")

    # ── Signals ───────────────────────────────────────────────────────────────
    all_signals = load_jsonl(BOT_DIR / "signals.jsonl")
    today_sigs  = [s for s in all_signals if (s.get("ts") or "").startswith(today_str)]

    dir_cnt = defaultdict(int)
    for s in today_sigs:
        dir_cnt[s.get("direction","?")] += 1

    add("## 🔔 今日信号")
    add(f"总数 **{len(today_sigs)}** 条  ·  多 {dir_cnt['LONG']} · 空 {dir_cnt['SHORT']} · 观察 {dir_cnt['WATCH']} · 跳过 {dir_cnt['SKIP']}")

    # 路由分布
    routed = defaultdict(int)
    for s in today_sigs:
        routed[s.get("routed_to") or "—"] += 1
    if routed:
        route_parts = "  ·  ".join(f"{k} {v}" for k,v in sorted(routed.items(), key=lambda x:-x[1]))
        add(f"路由分布：{route_parts}")
    add("")

    # 高信心信号 — 按 symbol 去重，只看每个标的最佳一条
    high = [s for s in today_sigs if (s.get("confidence") or 0) >= 70]
    if high:
        by_sym = {}  # symbol → {best_conf, count, latest_signal}
        for s in high:
            sym = s.get("symbol", "?")
            entry = by_sym.get(sym)
            if not entry:
                by_sym[sym] = {"best_conf": s.get("confidence", 0), "count": 1, "latest": s, "best": s, "directions": {s.get("direction"): 1}}
            else:
                entry["count"] += 1
                entry["directions"][s.get("direction")] = entry["directions"].get(s.get("direction"), 0) + 1
                if (s.get("confidence", 0)) > entry["best_conf"]:
                    entry["best_conf"] = s.get("confidence", 0)
                    entry["best"] = s
                if (s.get("ts") or "") > (entry["latest"].get("ts") or ""):
                    entry["latest"] = s

        unique = sorted(by_sym.items(), key=lambda kv: (-kv[1]["best_conf"], -kv[1]["count"]))
        add(f"### ⭐ 高信心标的（≥70，共 {len(unique)} 个独特标的 / {len(high)} 条信号）")
        add(f"| Symbol | 最高信心 | 触发次数 | 主方向 | 最新入场 | 止损 | 止盈 | 路由 |")
        add(f"|--------|---------:|---------:|--------|---------:|-----:|------|------|")
        for sym, info in unique[:20]:
            best = info["best"]
            latest = info["latest"]
            tp = latest.get("take_profit", "—")
            if isinstance(tp, list):
                tp = " / ".join(str(x) for x in tp[:2])
            main_dir = max(info["directions"].items(), key=lambda x: x[1])[0]
            route = latest.get("routed_to") or "—"
            add(f"| `{sym}` | {info['best_conf']} | {info['count']} | {main_dir} | {latest.get('entry_price','—')} | {latest.get('stop_loss','—')} | {tp} | {route} |")
        if len(unique) > 20:
            add(f"\n_…另有 {len(unique)-20} 个标的省略_")
        add("")

    # 被阻断信号 — 按原因类型分组聚合
    blocked = [s for s in today_sigs if s.get("block_reason")]
    if blocked:
        # 按原因类别分组
        cat_cnt = defaultdict(int)
        sym_cat = defaultdict(lambda: defaultdict(int))  # symbol → cat → count
        for s in blocked:
            reason = s.get("block_reason", "")
            cat = "其他"
            if "7d-loser" in reason:        cat = "7天败者锁定 (24h)"
            elif "post-win-cooldown" in reason:  cat = "盈后冷却 (1h)"
            elif "post-loss-cooldown" in reason: cat = "亏后冷却 (6h)"
            elif "blacklist" in reason:     cat = "黑名单"
            elif "regime" in reason:        cat = "行情过滤"
            cat_cnt[cat] += 1
            sym_cat[s.get("symbol","?")][cat] += 1

        add(f"### 🚫 被阻断信号（{len(blocked)} 条）")
        add(f"| 阻断原因 | 次数 |")
        add(f"|----------|-----:|")
        for cat, n in sorted(cat_cnt.items(), key=lambda x: -x[1]):
            add(f"| {cat} | {n} |")

        # Top 5 最常被阻断的标的
        sym_total = sorted(sym_cat.items(), key=lambda kv: -sum(kv[1].values()))[:5]
        if sym_total:
            add(f"\n**最常被阻断的标的（Top 5）**：")
            for sym, cats in sym_total:
                total = sum(cats.values())
                cat_str = ", ".join(f"{c}×{n}" for c,n in sorted(cats.items(), key=lambda x:-x[1]))
                add(f"- `{sym}` 共 {total} 次（{cat_str}）")
        add("")

    # ── 错误日志摘要 ──────────────────────────────────────────────────────────
    err_file = BOT_DIR / "bot.err"
    if err_file.exists():
        add("## ⚠️ 今日错误摘要")
        try:
            err_lines = err_file.read_text().splitlines()
            today_errs = [l for l in err_lines if today_str in l or yesterday in l]
            # 只取包含 ERROR/WARNING/fail/sCode 的关键行
            keywords = ("ERROR", "WARNING", "fail", "sCode", "51", "exception", "Exception")
            key_errs = [l for l in today_errs if any(k in l for k in keywords)]
            shown = key_errs[-15:] if key_errs else []
            if shown:
                add("```")
                lines.extend(shown)
                add("```")
            else:
                add("今日无关键错误 ✅")
        except Exception as e:
            add(f"读取失败：{e}")
        add("")

    # ── 手动备注 ──────────────────────────────────────────────────────────────
    add("## 📝 今日观察（手动填写）")
    add("")
    add("- 行情感受：")
    add("- 信号质量：")
    add("- 策略问题：")
    add("- 明日关注：")
    add("")
    add("---")
    add(f"_Crésus daily_report.py · {now_str}_")

    report = "\n".join(lines)
    report_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n✅ 已保存：{report_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
