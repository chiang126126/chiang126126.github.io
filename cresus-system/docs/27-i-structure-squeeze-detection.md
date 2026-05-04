# P19f：I 型 Squeeze 检测 — 把 5-02 +100 USDT 那一笔变成可重复 alpha

> 关联 commit: 私有 repo `2cc6ad1`
> 起源：第二周复盘的「优先级 5」实施

## 起因 — 一笔 +100 USDT 让我重新审视整个策略

第二周复盘按 symbol 拆 PnL 时发现一个反转：

```
BIO 全部 9 笔交易：5W/4L 净 +128.51 USDT
其中关键一笔：5-02 18:34 → 20:02
  open  0.05725 → close  0.07585
  1.5 小时内涨 32.49%
  5x 杠杆 → 净 +100.24 USDT
```

**这一笔单独贡献了体系全部利润的 78%**。剔除它，全体系 12 笔 5W/7L、净 -11.88 USDT — **实际是亏损的**。

→ 体系真正的 alpha 不在「日常的 A 型 OI 累积做多」（剔除大单后 EV 接近 0），而在**「捕获到这种 1.5 小时 +32% 的快速 setup」**。

问题：这种 setup **能否被识别成可重复的结构信号**，而不是听天由命？

---

## 那一笔的 anatomy — 为什么能赚 +100？

复盘 5-02 18:34 BIO 的 entry signal 数据：

| 维度 | 值 | 意义 |
|---|---|---|
| direction | LONG | 触发做多 |
| confidence | 70 | 高信心 |
| structure | A (OI 积累) | 看似常规 A 型 |
| anomaly | OI 24h +37.4%, MA20 dev +78% | **MA20 偏离极高** |
| BTC regime | RANGE | 大盘没影响 |
| **此前 32 分钟** | **触发 SHORT@70 失败** | **关键** |

**真正的 setup 特征是这 4 个条件叠加**：

```
① MA20 偏离 > 70%      ← 价格已经远离均线，超买极端
② OI 24h > 30%         ← 空头还在加仓累积
③ 4H 涨幅显著          ← 短期急拉
④ 1H RSI 高（>65）     ← 动量强未衰
```

这正是经典的**短期轧空（short squeeze）setup**：散户和空头双方共同加压，任何一个继续上涨的触发都可能引发反向爆仓潮。

---

## 实现 — 单次快照可计算

为了让检测**不依赖信号历史**（数据获取复杂、易过时），改用**4 个市场快照特征**同时满足才触发：

```python
def _detect_squeeze(snap, data):
    """检测 I 型 Squeeze (轧空) setup。

    全部 4 个条件满足才触发：
      ① MA20 偏离 > 60%   - 价格远离均线，超买极端
      ② OI 24h > 25%       - 空头加仓累积
      ③ 4H 涨幅 > 10%      - 短期急拉
      ④ 1H RSI > 65        - 动量强未衰
    """
    ma20_dev = abs(data.get("price_vs_ma20_pct") or 0)
    if ma20_dev < 60: return None

    oi_chg = abs(snap.oi_change_24h_pct or 0)
    if oi_chg < 25: return None

    klines_4h = data.get("klines_4h") or []
    if not klines_4h: return None
    last_4h_open = float(klines_4h[-1].get("o", 0))
    h4_change_pct = (snap.price - last_4h_open) / last_4h_open * 100
    if h4_change_pct < 10: return None

    rsi_1h = (data.get("metrics") or {}).get("rsi_1h")
    if rsi_1h is None:
        klines_1h = data.get("klines_1h") or []
        if len(klines_1h) >= 15:
            rsi_1h = _calc_rsi([float(k["c"]) for k in klines_1h], 14)
    if rsi_1h is None or rsi_1h < 65: return None

    return {
        "detected": True,
        "ma20_dev_pct": round(ma20_dev, 2),
        "oi_24h_pct": round(oi_chg, 2),
        "h4_change_pct": round(h4_change_pct, 2),
        "rsi_1h": round(rsi_1h, 2),
        "rationale": "MA20 远离 + OI 累积 + 4H 急拉 + RSI 强 → 轧空候选",
    }
```

阈值（**比理论值略宽**，给 DeepSeek 更多触发空间）：

| 阈值 | 5-02 实际值 | 当前阈值 | 设计裕度 |
|---|---|---|---|
| MA20 偏离 | 78% | **60%** | -18%，宽松 |
| OI 24h | 37.4% | **25%** | -12%，宽松 |
| 4H 涨幅 | 12.25% | **10%** | -2%，紧 |
| 1H RSI | 高（具体值未记录）| **65** | 中性偏宽松 |

宽松阈值的目的：**先让 I 型频繁触发，收集数据后再调紧**。

---

## DeepSeek prompt 集成

`prompt_builder.py` 加 H7 硬规则 + I 型结构定义：

```text
## Structure Types
- A: OI 积累做多
- B: 资金费反转做空
- C: V4A-Flash 做空
- D: 分布出货做空
- E: 强控盘回避
- F: 财库公司做多 BTC
- G: 盘前套利
- H: 新闻事件
- I: Squeeze (轧空，新增) — squeeze_setup.detected=True 时优先使用此分类

## Hard Rules
- H7: Squeeze (I) detection — if data.squeeze_setup.detected is True:
    * For LONG signal: ADD +10 to confidence (max 90)
    * For SHORT signal: SUBTRACT 15 from confidence (squeezes punish shorts)
    * Mark structure_type as "I" (Squeeze) instead of A/C if all conditions match
```

User prompt 包含 `squeeze_setup` 字段（含 detected + 4 个数值 + rationale），让 DeepSeek 直接看到。

---

## 期望效果（2 周观察期）

### 直接收益
- **更频繁捕获快速轧空 setup**：理论上 5-02 那种机会每月会出现 2-5 次（牛市更多）
- **避开下跌阶段做空**：H7 在 squeeze 时 SHORT -15，downgrade 到 watch-list

### 风险 — 假阳性
- 4 个条件叠加在牛市中可能频繁满足，导致**所有山寨币都被判为 squeeze**
- 缓解：等数据下来后看实际胜率，必要时把任一阈值收紧

### 验证指标
2 周后跑这条对照：

```bash
# I 型信号在产生后 2-6 小时的实际胜率
python3 scripts/eval_squeeze.py
  → expected output:
    - I 型触发总次数: N
    - 触发后 LONG@70+ 实际开仓数: M
    - M 笔实际平仓 W 胜 L 负
    - 平均盈亏比: ?
    - 对照同期 A 型 LONG@70+ 实际胜率: ?
```

如果 **I 型胜率 > A 型 + 10%**：alpha 成立，进一步降低 4H 涨幅阈值（如 8%）多触发。
如果 **I 型胜率 < A 型 - 5%**：阈值过松，提高（如 MA20 dev 70%、OI 30%）。
如果 **I 型很少触发**（<5 次/2周）：阈值过严或市场缺乏 squeeze 行情，降阈值。

---

## 与 P19c-v2 cooldown 的协同

I 型 Squeeze 检测 + 差异化 cooldown 是**配套设计**：

- I 型在 BIO 这种持续上涨的 symbol 上会高频触发
- P19c-v2 让 BIO 这种 7d 净盈利的 symbol 在 1h cooldown 后即可重开
- 两者结合 → bot 能在 BIO 趋势中持续抓机会，不被 6h 一刀切错过

如果只有 I 型没有 cooldown 改造：BIO 检测到 squeeze 但因为 6h cooldown 被 BLOCKED → 检测无效。
如果只有 cooldown 改造没有 I 型：BIO 重开了但仍按 A 型评分 → 没有 +10 信心 boost → 可能被路由到 watch-list 不开仓。

---

## 单元测试

复盘时构造 5-02 BIO 的快照 — 触发 ✅
构造 ETHUSDT 正常 A 型快照 — 不误报 ✅

```
BIOUSDT 5-02 18:34 模拟检测结果:
  detected           = True
  ma20_dev_pct       = 78.0
  oi_24h_pct         = 37.4
  h4_change_pct      = 12.25
  rsi_1h             = 100.0   (单测构造的 closes 全单调上涨)
  rationale          = MA20 远离 + OI 累积 + 4H 急拉 + RSI 强 → 轧空候选
  ✅ 这正是 5-02 +100 USDT 那一笔的特征 → I 型成功识别

ETHUSDT 正常 A 型信号:
  ✅ 没误报，正确跳过
```

---

## 一行价值总结

> **不是让 AI 预测涨跌，是让 AI 识别"市场进入轧空临界状态"这种客观可计算的 setup。
> 5-02 +100 USDT 这种事不能靠运气复制，但能靠**特征工程**让 bot 主动寻找。**

---

## 后续优化方向

| 阶段 | 内容 |
|---|---|
| **P19g** | 加 I 型胜率回测 script（基于 closes_all.jsonl 算 I 型 vs A 型胜率） |
| **P19h** | I 型独立 OCO 比例（赢家放更宽止盈，让 1.5h +32% 这种全部跑出来）|
| **P20**  | dashboard 加 I 型角标（决策视图里 I 型用紫色徽章突出）|
| **P21**  | 反向 I 型：检测 short squeeze cooldown 后的 long → short 反转点 |
