# P14：metrics 字段闭环（Hermes 不再"缺数据"）

之前 Hermes 复盘经常打 `MA20 偏离>30%：缺数据`，因为 `signals.jsonl` 只记录决策结果，没保存底层关键指标。本阶段在 `signals.jsonl` 每条记录追加 `metrics` 字段，包含 9 个核心数值。

---

## 数据流

```
DeepSeek 决策（带 reasoning + 价格）
    ↓
route_decision(decision, snap, data)        ← 新增 data 参数
    ↓
_append_signal(decision, snap, tag, data)   ← 新增 data 参数
    ↓
signals.jsonl 每行追加 metrics 字段
    ↓
hermes_bridge.py 读取 → Hermes prompt
    ↓
Hermes 看到真实数值，不再"缺数据"
```

---

## metrics 字段定义

```json
"metrics": {
    "price_vs_ma20_pct":     37.20,        // MA20 偏离百分比
    "funding_rate_binance":  4.05e-06,     // Binance 资金费率
    "funding_rate_okx":      5e-05,        // OKX 资金费率
    "oi_change_24h_pct":     10.58,        // OI 24h 变化
    "oi_concentration_pct":  63.6,         // Binance OI 占全市场比
    "vol_oi_ratio":          9.50,         // 24h 成交量 / OI
    "fut_spot_vol_ratio":    0.0,          // 期货 / 现货成交比
    "v4a_flash": {                          // V4A-Flash 结构详情
        "signal":              true,
        "upper_shadow_pct":    0.806,
        "retracement_pct":     4.719,
        "direction":           "SHORT"
    },
    "is_beijing_peak":       false         // 是否北京高峰时段
}
```

---

## 修改清单（4 处 sed）

### 1. `main_loop.py` — 传 data

```bash
sed -i '' 's|route_decision(decision, snap)|route_decision(decision, snap, data)|' \
  ~/cresus-bot/scripts/main_loop.py
```

### 2-4. `signal_router.py` — 三处签名 + 新字段

```bash
# 2) _append_signal 签名
sed -i '' 's|def _append_signal(decision: dict, snap: CoinSnapshot, channel: str) -> None:|def _append_signal(decision: dict, snap: CoinSnapshot, channel: str, data: dict) -> None:|' \
  ~/cresus-bot/src/execution/signal_router.py

# 3) 在 anomaly 行后插入 metrics
sed -i '' '/"anomaly":     getattr(snap, "anomaly_reasons", \[\]),/a\
        "metrics":   {\
            "price_vs_ma20_pct":     data.get("price_vs_ma20_pct"),\
            "funding_rate_binance":  data.get("funding_rate_binance"),\
            "funding_rate_okx":      data.get("funding_rate_okx"),\
            "oi_change_24h_pct":     data.get("oi_change_24h_pct"),\
            "oi_concentration_pct":  data.get("oi_concentration_binance_pct"),\
            "vol_oi_ratio":          data.get("vol_oi_ratio"),\
            "fut_spot_vol_ratio":    data.get("spot_futures_vol_ratio"),\
            "v4a_flash":             data.get("v4a_flash"),\
            "is_beijing_peak":       data.get("is_beijing_peak"),\
        },
' ~/cresus-bot/src/execution/signal_router.py

# 4) route_decision 签名 + 调用
sed -i '' 's|def route_decision(decision: dict, snap: CoinSnapshot) -> None:|def route_decision(decision: dict, snap: CoinSnapshot, data: dict) -> None:|' \
  ~/cresus-bot/src/execution/signal_router.py

sed -i '' 's|_append_signal(decision, snap, tag)|_append_signal(decision, snap, tag, data)|' \
  ~/cresus-bot/src/execution/signal_router.py
```

---

## 重启 + 验证

```bash
launchctl unload ~/Library/LaunchAgents/com.cresus.bot.plist
sleep 3
pkill -9 -f main_loop.py 2>/dev/null
sleep 3
launchctl load ~/Library/LaunchAgents/com.cresus.bot.plist
sleep 600
tail -1 ~/cresus-bot/signals.jsonl | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
print('metrics field:', 'metrics' in d)
print(json.dumps(d.get('metrics', {}), indent=2, ensure_ascii=False))
"
```

预期输出：

```json
metrics field: True
{
  "price_vs_ma20_pct": 37.2,
  "funding_rate_binance": 4.05e-06,
  ...
  "v4a_flash": {"signal": true, ...},
  "is_beijing_peak": false
}
```

---

## 实测样本

P14 后第一条带 metrics 的信号（BASEDUSDT SHORT@65 structure=C）：

```json
{
  "ts": "2026-04-27T14:01:08.521496+00:00",
  "symbol": "BASEDUSDT",
  "direction": "SHORT",
  "confidence": 65,
  "structure": "C",
  "metrics": {
    "price_vs_ma20_pct": 37.20485641934161,
    "funding_rate_binance": 4.05e-06,
    "oi_change_24h_pct": 10.579362269935078,
    "oi_concentration_pct": 63.6,
    "v4a_flash": {
      "signal": true,
      "upper_shadow_pct": 0.806,
      "retracement_pct": 4.719,
      "direction": "SHORT"
    },
    "is_beijing_peak": false
  }
}
```

Hermes 现在可以直接读这些数值打分，不再说"缺数据"。

---

## 副作用

每条信号 record size 增加约 350 字节。880 条 → 约 +300 KB。看板 GitHub Pages 拉取慢一点，但可忽略。

---

## ✅ P14 完成检查清单

- [x] `main_loop.py` 传 data
- [x] `signal_router.py` 4 处签名 + metrics 字段
- [x] bot 重启
- [x] 新信号带完整 metrics
- [x] Hermes 之后能读到真实数值

---

## 下一步

| 阶段 | 内容 |
|------|------|
| **P15** | 添加 RSI/MACD/ATR/BB 技术指标到 metrics（基于现有 klines 计算） |
| **P16** | 看板信号详情页：点击行展开 reasoning 全文 + Hermes 分析 + metrics 可视化 |
| **P17** | 信号 ↔ 持仓 attribution（哪条信号变成了哪笔单子） |
| **观察期** | 跑 1 周新数据，做胜率归因 |
