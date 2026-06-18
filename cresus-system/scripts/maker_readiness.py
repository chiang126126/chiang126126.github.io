#!/usr/bin/env python3
"""Phase 6.N Maker Mode Readiness — 评估 shadow 数据是否足够启用 canary_10pct.

Phase 6.N 部署 shadow 数据收集后, 此脚本评估是否可以从 default OFF 切到
LIVE_PHASE_6N_MAKER_MODE_ENABLED=True + LIVE_PHASE_6N_MAKER_MODE="canary_10pct".

判定标准 (全部满足才 ✅):
  - n ≥ 30 shadow 样本 (统计意义最低)
  - median spread_bps < 10 (买卖差不大, maker 挂得住)
  - median maker_vs_market_bps > 5 (maker 比 market 省 ≥0.05%)
  - median paper_signal_age_sec < 30 (信号还新, maker 等待延迟可接受)

用法:
    python maker_readiness.py [--history live_trades_history.json] [--min-n 30]
"""
import argparse
import json
import statistics
import sys
from pathlib import Path


def analyze(history_path: Path, min_n: int = 30) -> int:
    """0=READY for canary, 1=NEED MORE DATA, 2=NOT VIABLE."""
    try:
        with open(history_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"❌ 读 {history_path} 失败: {e}", file=sys.stderr)
        return 2
    rc = data.get("recent_closed", [])
    shadows = [t.get("maker_shadow_data") for t in rc if t.get("maker_shadow_data")]
    print("=" * 70)
    print(f"Phase 6.N Maker Mode 启用就绪度评估")
    print(f"数据: {history_path}")
    print(f"shadow 样本: {len(shadows)} / 总 closed {len(rc)}")
    print("=" * 70)

    if len(shadows) == 0:
        print("\n⚪ NO DATA: shadow 字段 0 样本.")
        print("可能原因:")
        print("  - Phase 6.N 代码未部署到 bot (cp 失败 / launchd 没拉新代码)")
        print("  - 部署后还没新开仓 trade 跑过新代码")
        print("  - bookTicker API 失败 (shadow 不强制, 失败时跳过)")
        return 1

    spreads = [s["spread_bps"] for s in shadows if s.get("spread_bps") is not None]
    ages = [s["paper_signal_age_sec"] for s in shadows
            if s.get("paper_signal_age_sec") is not None]
    mvm = [s["maker_vs_market_bps"] for s in shadows
           if s.get("maker_vs_market_bps") is not None]

    print(f"\n[1] spread_bps (bid/ask 价差)")
    if spreads:
        spreads.sort()
        n = len(spreads)
        med = spreads[n // 2]
        print(f"  n={n}  p25={spreads[n//4]:.2f}  median={med:.2f}  "
              f"p75={spreads[3*n//4]:.2f}  p90={spreads[9*n//10]:.2f}")
        spread_ok = med < 10
        print(f"  判定: median={med:.2f} {'✅' if spread_ok else '❌'} "
              f"(健康 <10 bps = 价差小, maker 易挂得住)")
    else:
        print("  无数据"); spread_ok = False

    print(f"\n[2] paper_signal_age_sec (信号到 live 处理延迟)")
    if ages:
        ages.sort()
        n = len(ages)
        med = ages[n // 2]
        print(f"  n={n}  p25={ages[n//4]:.1f}  median={med:.1f}  "
              f"p75={ages[3*n//4]:.1f}  p90={ages[9*n//10]:.1f}")
        age_ok = med < 30
        print(f"  判定: median={med:.1f}s {'✅' if age_ok else '❌'} "
              f"(健康 <30s = maker 等待 timeout 8s 不会让信号过老)")
    else:
        print("  无数据"); age_ok = False

    print(f"\n[3] maker_vs_market_bps (maker 比 market 省多少 bps; >0 = 省)")
    if mvm:
        mvm.sort()
        n = len(mvm)
        med = mvm[n // 2]
        positive = sum(1 for x in mvm if x > 0)
        print(f"  n={n}  p25={mvm[n//4]:+.2f}  median={med:+.2f}  "
              f"p75={mvm[3*n//4]:+.2f}  p90={mvm[9*n//10]:+.2f}")
        print(f"  maker 占优 (>0): {positive}/{n} ({positive/n*100:.0f}%)")
        savings_ok = med > 5
        print(f"  判定: median={med:+.2f} {'✅' if savings_ok else '❌'} "
              f"(健康 >5 bps = 真省钱)")
        # 月化估算
        avg_savings_usd = med / 10000.0 * 100   # $100 notional
        monthly = avg_savings_usd * 50 * 30   # 50 笔/天 × 30 天
        print(f"  月化估算节省 (假设 $100 notional, 50 笔/天): ${monthly:.2f}")
    else:
        print("  无数据"); savings_ok = False

    print("\n" + "=" * 70)
    if len(shadows) < min_n:
        print(f"  🟡 NEED MORE DATA: shadow 样本 {len(shadows)} < min_n={min_n}")
        print(f"     再等 ~48h 让 bot 自然累积 (按 50 笔/天计 = 2-3 天)")
        print(f"     之后重跑本脚本验证")
        return 1
    if spread_ok and age_ok and savings_ok:
        print(f"  ✅ READY: 全部 3 个指标健康, 可以启用 canary_10pct")
        print(f"     启用命令:")
        print(f"       1. 改 LIVE_PHASE_6N_MAKER_MODE_ENABLED = True")
        print(f"       2. LIVE_PHASE_6N_MAKER_MODE = 'canary_10pct'")
        print(f"     ⚠️ 还需要实现真 maker 挂单 binance_client.place_post_only_limit")
        return 0
    print(f"  🔴 NOT VIABLE: 至少 1 项指标不健康")
    print(f"     spread_ok={spread_ok}  age_ok={age_ok}  savings_ok={savings_ok}")
    if not spread_ok:
        print(f"     spread 太大 → maker 挂在 bid/ask 易被前置, 不利")
    if not age_ok:
        print(f"     信号太老 → maker 8s 等待会让信号过期")
    if not savings_ok:
        print(f"     maker vs market 差距太小 → 节省抵不上风险")
    print(f"     建议: 维持 default OFF, 继续观察")
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path,
                    default=Path.home() / "chiang126126.github.io"
                            / "cresus-system" / "dashboard"
                            / "live_trades_history.json")
    ap.add_argument("--min-n", type=int, default=30,
                    help="最少样本数 (默认 30)")
    args = ap.parse_args()
    sys.exit(analyze(args.history, min_n=args.min_n))


if __name__ == "__main__":
    main()
