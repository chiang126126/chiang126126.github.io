#!/usr/bin/env python3
"""Phase 6.G G3 LIMIT-IOC 监控 — 验证 close-side execution drag 实际效果.

2026-06-16: Bug 2 fix (close_method 持久化) 部署后, 这是第一个**可以测量 G3
真实效果**的工具. 之前 close_method/actual_close_slip_bps 全空, 无法验证.

用法:
    python g3_close_monitor.py [--history live_trades_history.json] [--since YYYY-MM-DD] [--min-n 30]

输出:
    1. close_method 分布 (LIMIT-IOC vs market fallback 占比)
    2. actual_close_slip_bps 中位数 / p25 / p75 (vs paper expected)
    3. 按 tier × regime 分组的 close drag
    4. 健康度判定:
       ✅ HEALTHY: LIMIT-IOC 占比 ≥ 50%, slip 中位数 ≤ 15 bps
       🟡 MARGINAL: 上面任一不满足
       🔴 BROKEN: LIMIT-IOC 占比 < 25% (G3 路径根本没走), 或 slip 中位数 > 25 bps

阈值参考: C1 audit 的 baseline drag 是 $1.79/笔 = ~36 bps (基于 $100 notional 的 round-trip).
G3 目标: 减半至 ≤ 18 bps. 健康度判定按 close-side slip 计 (15 bps 是宽容线).
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def tier(t):
    """同 live_trader 真 tier 分类: A≥10, B 1-10, C 0.1-1, D <0.1."""
    p = float(t.get("avg_fill_price") or t.get("entry_price_paper") or 0)
    if p >= 10: return "A大"
    if p >= 1:  return "B中"
    if p >= 0.1: return "C小"
    return "D微"


def analyze(history_path: Path, since: str = None, min_n: int = 30) -> int:
    """返回 exit code (0=健康, 1=marginal, 2=broken / 数据不够)."""
    try:
        with open(history_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"❌ 读 {history_path} 失败: {e}", file=sys.stderr)
        return 2

    rc = data.get("recent_closed", [])
    if not rc:
        print(f"❌ {history_path} 中 recent_closed 为空", file=sys.stderr)
        return 2

    # Filter by since
    if since:
        rc = [t for t in rc if (t.get("closed_at") or "")[:10] >= since]

    # Only trades with close_method populated (= after Bug 2 fix, 2026-06-15 后开仓)
    g3_tracked = [t for t in rc if t.get("close_method") is not None]

    print("=" * 75)
    print(f"Phase 6.G G3 — Close LIMIT-IOC 监控报告")
    print(f"数据源: {history_path}")
    print(f"过滤: closed_at >= {since or '所有'}, close_method 非空")
    print(f"总 closed: {len(rc)}, 其中 G3 已追踪: {len(g3_tracked)}")
    print("=" * 75)

    if len(g3_tracked) == 0:
        print("\n🔴 BROKEN: 0 笔有 close_method 字段.")
        print("可能原因:")
        print("  - Bug 2 fix (close_method 持久化) 还没部署到生产 bot")
        print("  - 部署后还没新 close 发生")
        print("  - close 都走早退路径 (already_closed_externally, etc) 不经 G3")
        return 2

    if len(g3_tracked) < min_n:
        print(f"\n🟡 INSUFFICIENT: 仅 {len(g3_tracked)} 笔 < min_n={min_n}, 数据太少不可信.")
        print("继续等更多 trade 再 retro. 至少 30 笔 close 才稳.")

    # 1. close_method 分布
    methods = Counter(t.get("close_method", "?") for t in g3_tracked)
    total = sum(methods.values())
    print("\n[1] close_method 分布:")
    for m, n in methods.most_common():
        pct = n / total * 100
        print(f"  {str(m):<25} {n:>4} ({pct:>5.1f}%)")

    limit_ioc_n = sum(n for m, n in methods.items() if m and "limit" in str(m).lower())
    limit_ioc_pct = limit_ioc_n / total * 100
    print(f"\n  → LIMIT-IOC 占比: {limit_ioc_pct:.1f}%  (健康 ≥50%, 警戒 <25%)")

    # 2. close slip 分布
    slips = [float(t["actual_close_slip_bps"]) for t in g3_tracked
             if t.get("actual_close_slip_bps") is not None]
    if slips:
        slips.sort()
        med = statistics.median(slips)
        p25 = slips[len(slips)//4]
        p75 = slips[3*len(slips)//4]
        mean = statistics.mean(slips)
        print(f"\n[2] actual_close_slip_bps (n={len(slips)}):")
        print(f"  p25={p25:>+7.2f}  median={med:>+7.2f}  p75={p75:>+7.2f}  mean={mean:>+7.2f}")
        print(f"  → 中位数 {med:.1f} bps  (健康 ≤15, 警戒 >25)")
    else:
        med = None
        print("\n[2] actual_close_slip_bps: 0 笔有数据 (字段全 null)")

    # 3. close_method × tier
    print("\n[3] close_method × tier (n / pct):")
    tier_method = defaultdict(lambda: defaultdict(int))
    for t in g3_tracked:
        tier_method[tier(t)][t.get("close_method", "?")] += 1
    tiers = ["A大", "B中", "C小", "D微"]
    methods_sorted = sorted({m for ts in tier_method.values() for m in ts.keys()},
                            key=lambda m: -methods.get(m, 0))
    header = f"  {'tier':>6}"
    for m in methods_sorted[:4]:   # top 4 methods
        header += f"{str(m)[:14]:>15}"
    print(header)
    for tr in tiers:
        line = f"  {tr:>6}"
        tot = sum(tier_method[tr].values())
        if tot == 0:
            line += f"  (no data)"
        else:
            for m in methods_sorted[:4]:
                n = tier_method[tr].get(m, 0)
                line += f"{n:>5} ({n/tot*100:>4.0f}%)"
        print(line)

    # 4. slip × tier
    if slips:
        print("\n[4] median close slip × tier (bps):")
        tier_slips = defaultdict(list)
        for t in g3_tracked:
            s = t.get("actual_close_slip_bps")
            if s is not None:
                tier_slips[tier(t)].append(float(s))
        for tr in tiers:
            ss = tier_slips[tr]
            if not ss:
                print(f"  {tr}: (no data)")
            else:
                m = statistics.median(ss)
                print(f"  {tr}: n={len(ss):>3}  median {m:>+7.2f} bps")

    # 健康度判定
    print("\n" + "=" * 75)
    print("HEALTH:")
    rc_code = 0
    if limit_ioc_pct < 25:
        print(f"  🔴 BROKEN: LIMIT-IOC 仅 {limit_ioc_pct:.1f}% — G3 路径没走通,"
              f" 大多是 market fallback. 需查 LIMIT-IOC 挂单失败原因.")
        rc_code = 2
    elif limit_ioc_pct < 50:
        print(f"  🟡 MARGINAL: LIMIT-IOC 仅 {limit_ioc_pct:.1f}% (期望 ≥50%) — 部分起效")
        rc_code = max(rc_code, 1)
    else:
        print(f"  ✅ LIMIT-IOC 占比 {limit_ioc_pct:.1f}% — G3 路径生效")

    if med is not None:
        if med > 25:
            print(f"  🔴 BROKEN: 中位数 slip {med:.1f} bps > 25 — G3 没减 drag,"
                  f" 或 fallback 占比太高")
            rc_code = 2
        elif med > 15:
            print(f"  🟡 MARGINAL: 中位数 slip {med:.1f} bps (期望 ≤15) — 部分改善")
            rc_code = max(rc_code, 1)
        else:
            print(f"  ✅ 中位数 slip {med:.1f} bps — 健康范围")
    print("=" * 75)
    return rc_code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path,
                    default=Path.home() / "chiang126126.github.io"
                            / "cresus-system" / "dashboard"
                            / "live_trades_history.json")
    ap.add_argument("--since", default="2026-06-15",
                    help="只看此日期之后 closed_at (默认 6/15 G3 字段持久化 fix 之后)")
    ap.add_argument("--min-n", type=int, default=30,
                    help="低于此数提示数据不足")
    args = ap.parse_args()
    sys.exit(analyze(args.history, since=args.since, min_n=args.min_n))


if __name__ == "__main__":
    main()
