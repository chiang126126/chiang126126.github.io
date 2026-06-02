"""Phase 5.R 审计脚本 — 回测 BTC sub_regime 下 paper LONG 的真实 PnL.

目的:
    Phase 4.F/4.J 把 BTC down regime + LONG 一刀切拒了 (live 0/10 胜 p=0.042).
    Phase 4.K 把 down 内细分了 down_acute / down_stable / down_rebound, 但 gate
    不分子状态 — 所以 live 没有任何 down_rebound + LONG 数据.

    本脚本用 paper 端的历史 LONG 信号 + 回算 BTC 1h regime, 看是否真的所有
    down 子状态的 LONG 都该拒. 若 down_rebound + LONG 的 paper EV 显著为正,
    那 Phase 5.R 应该把 down_rebound 加入 LIVE_REGIME_GATE_SUB_REGIME_ALLOW.

用法:
    cd cresus-system/scripts
    python3 audit_sub_regime_paper_outcomes.py
    # 或指定 paper 数据文件:
    python3 audit_sub_regime_paper_outcomes.py --paper ../dashboard/paper_trades_history.json

输出:
    每个 (regime, sub_regime) 组合的 paper LONG: count, win_rate, avg_pnl, total_pnl
    最后给出 Phase 5.R 部署建议 (down_rebound 是否值得放开)

设计:
    - 一次性拉 BTC 1h K 线覆盖全 paper trade 时间段 (1 次 API 调用)
    - 本地复用 live_trader._compute_btc_regime 的数学逻辑 (确保与生产一致)
    - 仅看 closed paper trades (要有 realized PnL)
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, median


# 与 live_trader.LIVE_BTC_REGIME_THRESHOLD_PCT 一致
BTC_REGIME_THRESHOLD_PCT = 0.5
# 与 _compute_btc_regime 的 3h 切分一致
SUB_REGIME_ACUTE_THRESHOLD_PCT = -1.0   # change_3h < -1% → down_acute
SUB_REGIME_REBOUND_THRESHOLD_PCT = 0.5  # change_3h > +0.5% → down_rebound

BINANCE_FAPI_BASE = "https://fapi.binance.com"


def fetch_btc_klines_1h(start_ms: int, end_ms: int) -> list[list]:
    """拉 BTCUSDT 1h K 线, 覆盖 [start_ms, end_ms]. 单次最多 1500 根.

    Returns: list of [openTime, open, high, low, close, ...].
    """
    all_klines = []
    cursor = start_ms
    # Binance 单次最多 1500 根, 1h * 1500 = 62.5 天
    while cursor < end_ms:
        params = {
            "symbol": "BTCUSDT",
            "interval": "1h",
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        }
        url = f"{BINANCE_FAPI_BASE}/fapi/v1/klines?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            batch = json.loads(resp.read())
        if not batch:
            break
        all_klines.extend(batch)
        last_ts = int(batch[-1][0])
        # 下一批从最后一根的下一小时开始 (1h = 3,600,000 ms)
        next_cursor = last_ts + 3_600_000
        if next_cursor <= cursor:  # 防御无限循环
            break
        cursor = next_cursor
        if len(batch) < 1500:
            break
    return all_klines


def index_klines_by_hour(klines: list[list]) -> dict[int, float]:
    """{open_time_hour_ms: close_price}. open_time 必然对齐到小时."""
    return {int(k[0]): float(k[4]) for k in klines}


def compute_regime_at(ts_ms: int, hour_close_map: dict[int, float]) -> dict | None:
    """复刻 live_trader._compute_btc_regime — 用 ts_ms 时刻往前 25 根 1h close.

    返回 {'regime': 'up/chop/down', 'sub_regime': '...'/None, 'pct_vs_ma25', 'change_3h_pct'}.
    任何缺失返 None.

    重要 — 避免前视偏差 (audit fix):
        Binance kline openTime=H 的 close = H+1 时刻的价 (该小时收盘价).
        若 ts_ms=14:47, 用 hour_close_map[14:00] = 15:00 的价, 是入场之后的未来数据.
        本函数用 "最近一根已收完整 1h" 作 closes[-1], 即 openTime ≤ floor(ts_ms) - 1h
        的最后一根 — 入场时 live 实际能看到的最近完成 close (虽然 live 还能看到当前小时
        的 running close, 但 audit 用上一小时 close 是无 leak 的稳健近似).
    """
    # 对齐 ts 到小时 (向下取整) — 这是当前正在开仓的小时
    current_hour_ms = (ts_ms // 3_600_000) * 3_600_000
    # 最近一根 "已完成" 的 1h: openTime = current_hour_ms - 1h (其 close = 当前小时初的价)
    latest_complete_open = current_hour_ms - 3_600_000
    # 取这一根 + 往前 24 根 = 25 根
    closes = []
    for i in range(24, -1, -1):
        t = latest_complete_open - i * 3_600_000
        c = hour_close_map.get(t)
        if c is None:
            return None
        closes.append(c)
    if len(closes) < 25:
        return None
    current = closes[-1]
    if current <= 0:
        return None
    ma25 = sum(closes) / 25.0
    if ma25 <= 0:
        return None
    pct_vs_ma = (current - ma25) / ma25 * 100.0

    if pct_vs_ma >= BTC_REGIME_THRESHOLD_PCT:
        regime = "up"
    elif pct_vs_ma <= -BTC_REGIME_THRESHOLD_PCT:
        regime = "down"
    else:
        regime = "chop"

    sub_regime = None
    change_3h_pct = None
    if regime == "down":
        first_3h = closes[-4]  # 3h 前
        if first_3h > 0:
            change_3h_pct = (current - first_3h) / first_3h * 100.0
            if change_3h_pct < SUB_REGIME_ACUTE_THRESHOLD_PCT:
                sub_regime = "down_acute"
            elif change_3h_pct > SUB_REGIME_REBOUND_THRESHOLD_PCT:
                sub_regime = "down_rebound"
            else:
                sub_regime = "down_stable"

    return {
        "regime": regime,
        "sub_regime": sub_regime,
        "pct_vs_ma25": round(pct_vs_ma, 3),
        "change_3h_pct": round(change_3h_pct, 3) if change_3h_pct is not None else None,
    }


def parse_entered_at(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_pnl_usdt(trade: dict) -> float | None:
    """优先 realized_usdt_pnl (paper_trades_history.json 实际字段名),
    退路 realized_pnl_usdt / realized_pnl / pnl_usdt, 最后才用 unrealized
    (后者是开仓后某时刻快照, 非平仓 PnL — 仅在数据缺失时退化).
    都不存在返 None (跳过).
    """
    for k in (
        "realized_usdt_pnl",   # ← paper_trades_history.json 实际字段 (audit fix)
        "realized_pnl_usdt",
        "realized_pnl",
        "pnl_usdt",
        "unrealized_usdt_pnl",
    ):
        v = trade.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def summarize(bucket: list[float]) -> dict:
    n = len(bucket)
    if n == 0:
        return {"count": 0}
    wins = sum(1 for p in bucket if p > 0)
    return {
        "count": n,
        "wins": wins,
        "win_rate_pct": round(wins / n * 100.0, 1),
        "avg_pnl_usdt": round(mean(bucket), 3),
        "median_pnl_usdt": round(median(bucket), 3),
        "total_pnl_usdt": round(sum(bucket), 2),
        "min": round(min(bucket), 2),
        "max": round(max(bucket), 2),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--paper",
        default=str(Path(__file__).resolve().parent.parent / "dashboard" / "paper_trades_history.json"),
        help="paper_trades_history.json 路径",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="只看最近 N 天的 paper trades (默认 60)",
    )
    parser.add_argument(
        "--min-conviction",
        type=int,
        default=0,
        help="只看 conviction_score >= 此值的 trade (默认 0 = 全部)",
    )
    parser.add_argument(
        "--direction",
        choices=["LONG", "SHORT", "BOTH"],
        default="LONG",
        help="审计方向 (默认 LONG; SHORT 用于找盈利金矿; BOTH 一次看完)",
    )
    parser.add_argument(
        "--show-trades",
        action="store_true",
        help="额外打印 down_rebound + LONG 的逐笔列表",
    )
    args = parser.parse_args(argv)

    paper_path = Path(args.paper)
    if not paper_path.exists():
        print(f"[error] paper file not found: {paper_path}", file=sys.stderr)
        return 2
    with paper_path.open() as f:
        pdata = json.load(f)
    closed = pdata.get("recent_closed") or []
    if not closed:
        print("[error] no closed paper trades", file=sys.stderr)
        return 2

    # Sanity: 检测使用的 pnl 字段 (transparency — 用户能看到我们用了哪个)
    from collections import Counter
    field_used = Counter()
    for t in closed[:200]:  # 抽样 200 笔够看哪个字段最先命中
        for k in ("realized_usdt_pnl", "realized_pnl_usdt", "realized_pnl",
                  "pnl_usdt", "unrealized_usdt_pnl"):
            if t.get(k) is not None:
                field_used[k] += 1
                break
    print(f"[audit] PnL field used (sample 200): {dict(field_used)}")

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    # 过滤: closed + direction match + 在窗口内 + 有 entered_at + (可选) conviction 阈值
    if args.direction == "BOTH":
        allowed_dirs = {"LONG", "SHORT"}
    else:
        allowed_dirs = {args.direction}

    trades_filt = []
    for t in closed:
        d = (t.get("direction") or "").upper()
        if d not in allowed_dirs:
            continue
        dt = parse_entered_at(t.get("entered_at"))
        if dt is None or dt < cutoff:
            continue
        if args.min_conviction:
            try:
                if int(t.get("conviction_score") or 0) < args.min_conviction:
                    continue
            except (TypeError, ValueError):
                continue
        trades_filt.append((t, dt, d))
    if not trades_filt:
        print(f"[error] no {args.direction} trades in last {args.days} days", file=sys.stderr)
        return 2

    earliest = min(d for _, d, _ in trades_filt)
    latest = max(d for _, d, _ in trades_filt)
    # 拉 BTC 1h 多 25h buffer 才能算 entered_at 那刻的 MA25
    start_ms = int((earliest - timedelta(hours=30)).timestamp() * 1000)
    end_ms = int((latest + timedelta(hours=1)).timestamp() * 1000)
    dir_label = args.direction
    print(f"[audit] paper {dir_label}: {len(trades_filt)} | window: {earliest.date()} → {latest.date()}")
    print(f"[audit] fetching BTC 1h klines {(end_ms - start_ms)/3_600_000:.0f}h...")
    try:
        klines = fetch_btc_klines_1h(start_ms, end_ms)
    except Exception as e:
        print(f"[error] BTC kline fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    print(f"[audit] fetched {len(klines)} BTC 1h candles")
    hour_close_map = index_klines_by_hour(klines)

    # Bucket: (direction, regime, sub_regime) → [pnl, pnl, ...]
    buckets: dict[tuple[str, str, str | None], list[float]] = defaultdict(list)
    rebound_trades_detail: list[dict] = []
    skipped_no_pnl = 0
    skipped_no_regime = 0

    for trade, dt, direction in trades_filt:
        ts_ms = int(dt.timestamp() * 1000)
        rr = compute_regime_at(ts_ms, hour_close_map)
        if rr is None:
            skipped_no_regime += 1
            continue
        pnl = extract_pnl_usdt(trade)
        if pnl is None:
            skipped_no_pnl += 1
            continue
        key = (direction, rr["regime"], rr["sub_regime"])
        buckets[key].append(pnl)
        if direction == "LONG" and rr["regime"] == "down" and rr["sub_regime"] == "down_rebound":
            rebound_trades_detail.append({
                "id": trade.get("id"),
                "symbol": trade.get("symbol"),
                "entered_at": trade.get("entered_at"),
                "pnl": pnl,
                "conviction": trade.get("conviction_score"),
                "change_3h_pct": rr["change_3h_pct"],
            })

    print(f"[audit] tagged {sum(len(b) for b in buckets.values())} trades | "
          f"skipped: no_regime={skipped_no_regime}, no_pnl={skipped_no_pnl}")

    # Print summary by (direction, regime, sub_regime)
    rows = []
    for key in sorted(buckets.keys(), key=lambda k: (k[0], k[1], k[2] or "")):
        d, regime, sub = key
        s = summarize(buckets[key])
        s["direction"] = d
        s["regime"] = regime
        s["sub_regime"] = sub or "—"
        rows.append(s)
    print()
    print("=" * 100)
    print(f"{'dir':<6} {'regime':<8} {'sub_regime':<14} {'n':>4} {'wins':>5} {'win%':>6} "
          f"{'avg$':>8} {'med$':>8} {'total$':>10} {'min':>8} {'max':>8}")
    print("=" * 100)
    for r in rows:
        print(f"{r['direction']:<6} {r['regime']:<8} {r['sub_regime']:<14} {r['count']:>4} "
              f"{r['wins']:>5} {r['win_rate_pct']:>6.1f} "
              f"{r['avg_pnl_usdt']:>8.2f} {r['median_pnl_usdt']:>8.2f} "
              f"{r['total_pnl_usdt']:>10.2f} {r['min']:>8.2f} {r['max']:>8.2f}")
    print("=" * 100)

    # 重点关注 down + LONG 的子状态拆分 (Phase 5.R 决策依据)
    if "LONG" in allowed_dirs:
        print()
        print(">>> Phase 5.R 决策依据: down + LONG 按 sub_regime 拆分 <<<")
        down_long_keys = [k for k in buckets if k[0] == "LONG" and k[1] == "down"]
        if not down_long_keys:
            print("    无 down regime 的 LONG paper 数据 — 无法判断 Phase 5.R 是否值得部署")
        else:
            for k in sorted(down_long_keys, key=lambda x: x[2] or ""):
                s = summarize(buckets[k])
                sub_label = k[2] or "(无 sub_regime — 数据不全)"
                print(f"    [{sub_label:<14}] n={s['count']:>3} 胜率={s['win_rate_pct']:>5.1f}% "
                      f"avg=${s['avg_pnl_usdt']:>6.2f} total=${s['total_pnl_usdt']:>7.2f}")

        # 决策建议
        print()
        print(">>> Phase 5.R 建议 <<<")
        rebound_bucket = buckets.get(("LONG", "down", "down_rebound"), [])
        if not rebound_bucket:
            print("    ⚠️  down_rebound + LONG paper 样本 = 0 → 数据不足, 不建议 flip flag")
        else:
            n = len(rebound_bucket)
            avg = mean(rebound_bucket)
            total = sum(rebound_bucket)
            wins = sum(1 for p in rebound_bucket if p > 0)
            win_rate = wins / n * 100
            if n < 20:
                print(f"    ⚠️  down_rebound + LONG paper 样本 n={n} < 20 (统计不显著)")
                print(f"        avg=${avg:.2f} 仅供参考, 暂不建议 flip")
            elif avg <= 0:
                print(f"    ❌  down_rebound + LONG paper avg=${avg:.2f} ≤ 0 (n={n})")
                print(f"        与 Phase 4.J 结论一致 — 维持当前 default, 不放开")
            elif avg < 0.5:
                print(f"    🟡  down_rebound + LONG paper avg=${avg:.2f} (n={n}, 胜率 {win_rate:.1f}%)")
                print(f"        EV 偏弱 — live 端 ~$2-3/笔执行摩擦可能吃光, 谨慎评估")
            else:
                print(f"    ✅  down_rebound + LONG paper EV 显著为正:")
                print(f"        n={n} 胜率={win_rate:.1f}% avg=${avg:.2f} total=${total:.2f}")
                print(f"        建议: 在 live_trader.py flip:")
                print(f"          LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {{'down_rebound'}}")
                print(f"        观察 24-48h live 实际表现再决定是否继续放开其它 sub_regime")

    # SHORT 端高 EV 桶 — 找盈利金矿
    if "SHORT" in allowed_dirs:
        print()
        print(">>> SHORT 端 (regime, sub_regime) 拆分 — 找盈利金矿 <<<")
        short_keys = [k for k in buckets if k[0] == "SHORT"]
        # 按 avg PnL 降序排
        scored = []
        for k in short_keys:
            b = buckets[k]
            if len(b) < 5:
                continue
            avg = mean(b)
            scored.append((k, len(b), avg, sum(b)))
        scored.sort(key=lambda x: -x[2])
        if not scored:
            print("    无足够 SHORT 数据 (每桶 < 5 笔)")
        else:
            for k, n, avg, total in scored:
                label = f"{k[1]}/{k[2] or '—'}"
                tag = "✅" if avg > 0.5 else ("🟡" if avg > 0 else "❌")
                print(f"    {tag} [{label:<22}] n={n:>3} avg=${avg:>+6.2f} total=${total:>+7.2f}")
        # 给出 SHORT 端的行动建议
        print()
        print(">>> SHORT 端建议 <<<")
        positive = [(k, n, avg, total) for k, n, avg, total in scored if avg > 0.5]
        if positive:
            print("    ✅ 以下 SHORT 桶是 paper 真实盈利金矿 (avg > +$0.5, n ≥ 5):")
            for k, n, avg, total in positive:
                print(f"        {k[1]}/{k[2] or '—'}: n={n} avg=${avg:.2f} total=${total:.2f}")
            print("    → 考虑在 live_trader 给这些桶单独的 size_multiplier (>1.0) 倾斜资金")
        else:
            print("    ⚠️  无 SHORT 桶 paper EV > +$0.5 — 整体 SHORT 不够强, 不建议加杠杆")

    if args.show_trades and rebound_trades_detail:
        print()
        print(">>> down_rebound + LONG 逐笔明细 <<<")
        for t in sorted(rebound_trades_detail, key=lambda x: x["entered_at"]):
            ch3h = t["change_3h_pct"] if t["change_3h_pct"] is not None else 0.0
            print(f"    {t['entered_at']}  {t['symbol']:<14} "
                  f"conv={t['conviction']} 3h={ch3h:>+6.2f}%  pnl=${t['pnl']:>+7.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
