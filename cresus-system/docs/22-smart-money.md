# P16：聪明钱共识聚合（Smart Money Consensus）

通过 OKX `okx smartmoney` 模块拉 Top 50 聪明钱的持仓，聚合成「每个币种被多少聪明钱多/空、净敞口多少」，作为 Crésus 的独立 alpha 输入。

---

## 数据流

```
launchd 每 60 分钟
    ↓
fetch_smart_money.py
    ├─ okx smartmoney traders --limit 50 --period 7  (1 调用)
    ├─ for each trader (filter: win≥45%, asset≥10K, drawdown>-80%):
    │     okx smartmoney trader --authorId X --period 7  (~50 调用)
    └─ 聚合每个 instId
        ↓
~/cresus-bot/smart_money.json
{
  "BTC-USDT-SWAP": {
    "long_count":  23,
    "short_count": 5,
    "long_notional_usd":  50_000_000,
    "short_notional_usd": 8_000_000,
    "net_notional_usd":   42_000_000,
    "total_holders":      28,
    "avg_win_ratio":      0.55,
    "score":              +0.64,    // -1 ~ +1
    "direction":          "STRONG_LONG"
  },
  ...
}
```

---

## 共识 score 算法

```
score = (long_count - short_count) / (long_count + short_count)
```

| score 区间 | direction       | 含义 |
|-----------|----------------|------|
| ≥ +0.5   | `STRONG_LONG`  | 聪明钱重度做多 |
| +0.2 ~ +0.5 | `LONG`        | 偏多 |
| -0.2 ~ +0.2 | `NEUTRAL`     | 分歧 |
| -0.2 ~ -0.5 | `SHORT`       | 偏空 |
| ≤ -0.5   | `STRONG_SHORT` | 聪明钱重度做空 |

---

## 质量过滤（过滤掉低质 trader）

```python
- win_ratio   ≥ 0.45   # 至少 45% 胜率
- asset       ≥ 10_000 # 至少 1 万美金管理
- max_retreat > -0.8   # 最大回撤 < 80%
```

---

## `scripts/fetch_smart_money.py`

```python
"""Fetch OKX smart money traders + positions, aggregate consensus per symbol."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OKX = shutil.which("okx") or "okx"
OUT_FILE = Path.home() / "cresus-bot" / "smart_money.json"


def _run(args):
    cmd = [OKX] + args + ["--demo", "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def fetch_top_traders(limit=50, period=7):
    raw = _run(["smartmoney", "traders", "--limit", str(limit), "--period", str(period)])
    return raw if isinstance(raw, list) else []


def fetch_trader_positions(author_id, period=7):
    raw = _run(["smartmoney", "trader", "--authorId", author_id, "--period", str(period)])
    if not isinstance(raw, dict):
        return []
    pos_groups = raw.get("positions", [])
    return pos_groups[0].get("posData", []) if pos_groups else []


def aggregate(traders, min_win=0.45, min_asset=10000, max_drawdown=-0.8):
    consensus = {}
    sampled = 0
    for t in traders:
        author_id = t.get("authorId")
        if not author_id:
            continue
        try:
            win = float(t.get("winRatio", 0))
            asset = float(t.get("asset", 0))
            retreat = float(t.get("maxRetreat", 0))
        except (ValueError, TypeError):
            continue
        if win < min_win or asset < min_asset or retreat < max_drawdown:
            continue

        positions = fetch_trader_positions(author_id)
        sampled += 1

        for p in positions:
            inst_id = p.get("instId")
            side = p.get("posSide")
            if not inst_id or not side:
                continue
            try:
                notional = float(p.get("notionalUsd", 0))
            except (ValueError, TypeError):
                continue

            if inst_id not in consensus:
                consensus[inst_id] = {
                    "long_count": 0, "short_count": 0,
                    "long_notional_usd": 0.0, "short_notional_usd": 0.0,
                    "win_ratios": [],
                }
            c = consensus[inst_id]
            if side == "long":
                c["long_count"] += 1
                c["long_notional_usd"] += notional
            elif side == "short":
                c["short_count"] += 1
                c["short_notional_usd"] += notional
            c["win_ratios"].append(win)

    for c in consensus.values():
        total = c["long_count"] + c["short_count"]
        c["total_holders"] = total
        c["score"] = round((c["long_count"] - c["short_count"]) / total, 4) if total else 0
        c["avg_win_ratio"] = round(sum(c["win_ratios"]) / len(c["win_ratios"]), 4) if c["win_ratios"] else 0
        c["net_notional_usd"] = round(c["long_notional_usd"] - c["short_notional_usd"], 2)
        s = c["score"]
        if s > 0.5: c["direction"] = "STRONG_LONG"
        elif s > 0.2: c["direction"] = "LONG"
        elif s < -0.5: c["direction"] = "STRONG_SHORT"
        elif s < -0.2: c["direction"] = "SHORT"
        else: c["direction"] = "NEUTRAL"
        del c["win_ratios"]

    return consensus, sampled


def main():
    print("[smart-money] fetching top 50 traders...")
    traders = fetch_top_traders(limit=50, period=7)
    print(f"[smart-money] got {len(traders)} traders")
    if not traders:
        print("[smart-money] empty result, abort")
        return

    consensus, sampled = aggregate(traders)
    sorted_consensus = dict(sorted(
        consensus.items(),
        key=lambda kv: kv[1]["total_holders"],
        reverse=True,
    ))

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": 7,
        "traders_sampled": sampled,
        "consensus": sorted_consensus,
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[smart-money] sampled {sampled} traders, {len(consensus)} symbols")
    print("Top 10:")
    for inst_id, c in list(sorted_consensus.items())[:10]:
        net_m = c["net_notional_usd"] / 1e6
        print(
            f"  {inst_id:22s} {c['direction']:13s} score={c['score']:+.2f} "
            f"({c['long_count']}多/{c['short_count']}空) net=${net_m:+.2f}M"
        )


if __name__ == "__main__":
    main()
```

---

## launchd 每 60 分钟

`~/Library/LaunchAgents/com.cresus.smart-money.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.cresus.smart-money</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>export PATH="/Users/mangzi/.npm-global/bin:/Users/mangzi/.local/bin:/usr/local/bin:/usr/bin:/bin"; cd /Users/mangzi/cresus-bot; exec uv run python scripts/fetch_smart_money.py</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>StandardOutPath</key>
  <string>/Users/mangzi/cresus-bot/logs/smart_money.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/mangzi/cresus-bot/logs/smart_money.err</string>
</dict>
</plist>
```

---

## ✅ P16 步骤 1 检查清单

- [ ] `~/cresus-bot/scripts/fetch_smart_money.py` 写入
- [ ] 手动测试：`uv run python scripts/fetch_smart_money.py`（约 60 秒，会调 51 次 OKX API）
- [ ] `~/cresus-bot/smart_money.json` 生成（含 Top 10 币种聚合）
- [ ] `~/Library/LaunchAgents/com.cresus.smart-money.plist` 加载
- [ ] 每小时自动刷新 cache

---

## 下一步（P16 步骤 2-4）

| 阶段 | 内容 |
|------|------|
| 步骤 2 | `assembler.py` 读 cache 注入 4 字段（`sm_consensus_dir/score/long_count/short_count`）到 34 维快照 |
| 步骤 3 | `prompt_builder.py` 加这 4 字段，DeepSeek system prompt 加结构 I「聪明钱跟单」 |
| 步骤 4 | 看版加列「🐋 聪明钱方向」 + 过滤「与聪明钱同向」 |
| 步骤 5 | sync_signals.sh 加 smart_money.json 同步 |
