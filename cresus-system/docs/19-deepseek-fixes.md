# P12：DeepSeek 输出修复 + 信心度校准

P11 给 Hermes 反馈打通后，发现两个 DeepSeek 层的问题：

1. **`structure` 字段永远是 null** — bot 路由层读取的字段名跟 prompt 输出名不匹配
2. **信心度全部 ≥70** — prompt 没指引模型使用全量 0-100 区间

本阶段两个 sed 修一行解决。

---

## 问题 1：字段名 mismatch

### 现象

`signals.jsonl` 每行 `"structure": null`，Hermes 分析出的 structure_judge 也无法和 bot 推理结果对齐。

### 根因

- DeepSeek prompt 让模型输出 `"structure_type": "A|B|C|D|E|F|G|H|none"`
- 但 `signal_router._append_signal` 写入时读的是 `decision.get("structure")` （键名错）
- 结果永远 None

### 修复

`src/execution/signal_router.py` 第 22 行：

```python
# 改前
"structure":   decision.get("structure"),

# 改后
"structure":   decision.get("structure_type"),
```

一行 sed：

```bash
sed -i '' 's|"structure":   decision.get("structure")|"structure":   decision.get("structure_type")|' \
  ~/cresus-bot/src/execution/signal_router.py
```

---

## 问题 2：信心度都 ≥70

### 现象

最近 7 天 `signals.jsonl` 中：
- 高信心度 ≥70 占比 **96%**
- 50-69 watch 占比 4%
- 模型几乎不打 50-65 的「中等机会」

### 根因

system prompt 校准说明里只写了：

```
- Base 40: structure type clearly matched
- +10 each: ... aligns
```

模型默认从 40 起算，只要看到 1-3 条 align 信号就给 70-90，缺乏校准约束。

### 修复

在 `prompt_builder.py` 的 `Confidence Scoring` 段落首行追加 `CALIBRATION` 指令：

```
- Base 25: structure type clearly matched. CALIBRATION: most signals deserve 40-65, only true textbook setups (4+ alignments) deserve 75+. Use the full 0-100 range.
```

一行 sed：

```bash
sed -i '' 's|Base 40: structure type clearly matched|Base 25: structure type clearly matched. CALIBRATION: most signals deserve 40-65, only true textbook setups (4+ alignments) deserve 75+. Use the full 0-100 range.|' \
  ~/cresus-bot/src/ai_layer/prompt_builder.py
```

---

## 验证

修复后重启 bot，5 分钟内看新信号分布：

```bash
launchctl unload ~/Library/LaunchAgents/com.cresus.bot.plist
sleep 3
pkill -9 -f main_loop.py 2>/dev/null
sleep 3
launchctl load ~/Library/LaunchAgents/com.cresus.bot.plist
sleep 300
tail -15 ~/cresus-bot/signals.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    s = d.get('structure') or '—'
    print(f\"{d['symbol']:14s} {d['direction']:5s} conf={d['confidence']:3d} structure={s}\")
"
```

---

## 实测对比

修复前最近一批：

```
SONICUSDT    LONG  conf=70 structure=None
GPSUSDT      LONG  conf=80 structure=None
MSTRUSDT     SHORT conf=70 structure=None
INTCUSDT     SHORT conf=70 structure=None
DAMUSDT      WATCH conf=55 structure=A    ← 唯一一条 structure 填充（修复后第 1 条）
```

修复后最近 15 条：

```
CHZUSDT        SHORT conf=65 structure=C
CRCLUSDT       SHORT conf=55 structure=C
SONICUSDT      LONG  conf=60 structure=A
GPSUSDT        LONG  conf=70 structure=A
DUSDT          SHORT conf=55 structure=D
INTCUSDT       SHORT conf=65 structure=C
MSTRUSDT       SHORT conf=65 structure=C
CHIPUSDT       SHORT conf=55 structure=B
DAMUSDT        SHORT conf=70 structure=C
AIOTUSDT       LONG  conf=65 structure=A
RAVEUSDT       SHORT conf=65 structure=C
1000LUNCUSDT   LONG  conf=65 structure=A
BASEDUSDT      SHORT conf=65 structure=C
NAORISUSDT     LONG  conf=65 structure=A
SWARMSUSDT     LONG  conf=65 structure=A
```

| 维度 | 改前 | 改后 |
|------|------|------|
| `structure` 填充率 | 0% | **100%** |
| confidence 平均 | ~75 | **63** |
| 50-69 中等区间占比 | ~4% | **80%** |
| 70+ 高信心度占比 | ~96% | **20%** |
| 结构多样性 | A/B/C/D 都识别 |

---

## 副作用：bot 下单频率下降

由于大部分信号现在落在 55-69 区间（watch-list，**不自动下单**），bot 实际下单频率下降。这是**期望行为**：质量优先而非数量。

如果想恢复原下单频率，把 `.env` 的 `CONFIDENCE_OPEN_THRESHOLD` 从 70 调到 65：

```bash
sed -i '' 's/CONFIDENCE_OPEN_THRESHOLD=70/CONFIDENCE_OPEN_THRESHOLD=65/' ~/cresus-bot/.env
launchctl unload ~/Library/LaunchAgents/com.cresus.bot.plist
launchctl load ~/Library/LaunchAgents/com.cresus.bot.plist
```

但建议**先观察一周**用 70 阈值的实际胜率，再决定是否下调。

---

## ✅ P12 完成检查清单

- [x] `signal_router.py` 字段名修复
- [x] `prompt_builder.py` 校准指令加入
- [x] bot 重启
- [x] 新 signals.jsonl `structure` 字段填充
- [x] confidence 分布出现 50-69 区间
- [x] 结构识别多样化（A/B/C/D 都看到）

---

## 下一步

| 阶段 | 内容 |
|------|------|
| **P13** | 看板加 structure 分组统计：A/B/C/D 各自的信号数 / 平均信心 / 历史胜率 |
| **P14** | 加 RSI/MACD/ATR/MA20% 偏离到 34 维快照（解决 Hermes 抱怨的"数据缺失"） |
| **P15** | R-单位仓位管理（基于止损距离动态计算 sz） |
| **观察期** | 让 bot 用新校准跑 1 周，再做 attribution 分析 |
