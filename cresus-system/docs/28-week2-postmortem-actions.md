# 第二周复盘 + 行动总结（2026-05-04）

> 复盘 + 6 项行动 一次会话搞定。本文档作为可追溯档案。
> 关联私有 repo commits: `7043135` `e984417` `0daea67` `a04e94e` `2cc6ad1` `930c2cc` `e255bb7`

---

## 复盘起因

第二周末做一次"全面彻查"——看板跑了 9.6 天、10119 条信号、13 笔 demo 平仓。
表面上一切正常，但**业务结果（净 +88 USDT）背后有多少是 alpha、多少是运气**？是否还有未发现的子系统问题？

---

## 5 批次审计 — 关键发现

### 批次 ① 数据健康度

```
9.6 天运行 · 10,119 信号 · 198 unique symbol · 平均 51 信号/symbol
方向: SHORT 49.3% > LONG 44.9% > WATCH 5.8%
路由: high-confidence 45% / watch-list 55%
结构分布: A 38% > none 28% > C 20% > B 13% > D 1%
```

**发现 1**：28% 信号 `structure="none"`——DeepSeek 还有相当一部分判断不到结构。
**发现 2**：信号重复度极高（NAORIS 284 次 / AIOT 256 次 / CL 239 次……）。
**发现 3**：Hermes 二次分析覆盖 70%，但 ~50% 关键字段为 None。

### 批次 ② 错误事件复盘

```
错误源排序：
  3289 OKX Instrument doesn't exist  ← 已知设计问题
   148 OKX HTTP 401 Invalid Sign     ← 4-26 一天单事件，已自愈
    22 OKX --slOrdPx ambiguous       ← 4-26 一天单事件，已自愈
   ~10 Hermes timeout / 配额          ← 第一周已修
```

**发现 4**：除"Instrument 不存在"外的所有真实错误**全部集中在 4-26 一天**——之后 8 天系统极其稳定。
**发现 5**：22 次 slOrdPx 错误**没有产生裸奔仓位**（OKX `swap place` 是原子操作，主仓 + OCO 一起 fail）。

### 批次 ③ 交易执行层（最重要）

```
按 symbol 拆 PnL（13 笔已平仓）:

symbol           笔数  胜负   净 PnL
─────────────────────────────────────
BIO-USDT-SWAP     9    5/4   +128.51   ⭐ 最大盈利来源
AXS-USDT-SWAP     1    0/1    -20.83
DOGE-USDT-SWAP    1    0/1    -19.29
XAU-USDT-SWAP     1    0/1     -0.07
XPL-USDT-SWAP     1    1/0     +0.04
─────────────────────────────────────
TOTAL                          +88.36
```

**反直觉发现 6**：**BIO 不是亏损源，是最大盈利来源**（56% 胜率）。第一周给 BIO 加 cooldown 是基于早期 8 笔小样本的误判。
**反直觉发现 7**：剔除 BIO 后体系 1W/3L 净 -40 USDT——**alpha 高度集中在一只 symbol**。
**反直觉发现 8**：**5-02 那一笔 +100 USDT 单独贡献了体系 78% 利润**。剔除它后整体亏 -12 USDT。

### 批次 ④ 系统状态体检

```
✓ 7 个 launchd 服务全健康
⚠️ 4 个 WIP 文件 6-7 天未 commit  ← 唯一警告
✓ 风控 6 字段齐全
✓ 磁盘 170MB 占用合理
```

**发现 9**：`scripts/main_loop.py / src/ai_layer/prompt_builder.py / src/common/config.py / src/data_layer/assembler.py` 已经在生产里跑了 6+ 天但没 commit。其中 `prompt_builder.py + assembler.py` 是 **P19 BTC regime gating**（自动减熊市追多/牛市追空信心）——一个早就实现但没意识到的"反 bot"机制。

### 批次 ⑤ 跨维度反思

**8 个改动效果对照**：

| 改动 | 真实价值 |
|---|---|
| P17 Hermes 行情工具 | ⭐⭐⭐⭐⭐ |
| P0 收口（git 入库 + 安全） | ⭐⭐⭐⭐⭐ |
| 看板状态机 | ⭐⭐⭐⭐ |
| 决策视图 panel | ⭐⭐⭐⭐ |
| closes_all.jsonl 持久化 | ⭐⭐⭐⭐ |
| 累计胜率字段 | ⭐⭐⭐ |
| Hermes 阈值 60 | ⭐⭐⭐ |
| **cooldown + 亏损连击降级** | **⭐⭐**（数据反转，可能误伤 BIO） |

→ 5⭐ 全在基础设施类，业务层最弱。

---

## 6 项行动 — 数据驱动的修正

### 优先级 1：4 个 WIP 入库（清债）

3 个清晰 commit：
- `7043135` route_decision data passthrough（P14 metrics 闭环必需）
- `e984417` BTC regime gating + 信心度校准（base 40→25）
- `0daea67` config.py 补 6 个 settings + knowledge note 清理

意外发现：**BTC regime gating** 是早就实现的"反 bot"机制（熊市自动减 LONG、牛市自动减 SHORT），只是没记录。

### 优先级 2：cooldown 差异化重写（P19c-v2）

**复盘反转后的纠错**。原 P19c 一刀切 6h cooldown → 新规则按 7d 净盈亏分流：
```
7d 净亏 + <24h         → BLOCKED (24h-loser)
上次盈利 (net > 0)     → 1h cooldown
上次亏损 (net < 0)     → 6h cooldown
```

让赢家继续触发（BIO 类），给输家更严约束（AXS/DOGE 类）。

文档：[`26-cooldown-v2-differentiated.md`](26-cooldown-v2-differentiated.md)

### 优先级 3：5-02 +100 那一笔的 anatomy

**审计而非修改**。还原那笔交易的真相：
- 18:02 SHORT@70 失败 → 32 分钟亏 -7.44
- 18:34 LONG@70 (A 型) 反手 → 1.5 小时 +32% (BIO 0.05725 → 0.07585) → +100.24
- bot 没有"预测"，是被动跟单方向反转

特征四件套：
```
MA20 偏离 +78%  +  OI 24h +37%  +  4H 涨 +12%  +  1H RSI 高
```

**结论**：A 型 OI 累积本身没显著 alpha（剔除特例后 EV ≈ 0），那笔 +100 是**短期轧空 setup**叠加。

### 优先级 5：I 型 Squeeze 检测（P19f）

**把不可重复的 lucky shot 变成可识别 setup**。

新增 `_detect_squeeze` 函数 + H7 硬规则：
```
4 条件全满足 → 标记 I 型，LONG +10 信心，SHORT -15 信心：
  ① MA20 偏离 > 60%
  ② OI 24h > 25%
  ③ 4H 涨幅 > 10%
  ④ 1H RSI > 65
```

文档：[`27-i-structure-squeeze-detection.md`](27-i-structure-squeeze-detection.md)

### 优先级 4：OKX 合约白名单（P19g）

**根除 3289 次 instrument 不存在的浪费**。

新增 `okx_whitelist.py` + scanner.py 过滤：
- 启动时拉 OKX 304 个 USDT-SWAP 合约
- scan_top_coins 仅选 Binance ∩ OKX 的 ~250 个 symbol
- 缓存 7 天，失败回退

预期：信号产出量 1060/天 → ~700-800/天，DeepSeek + Hermes 调用同比降。

### 优先级 6：parse_review 容错（P19h）

**修复 ~10% 由格式漂移导致的解析失败**。

旧正则强制要求冒号：`匹配度：高` → 漂移格式 `匹配度 **中**` 失败。
新正则容忍 0-15 字符任意分隔符 + bold + 等号 + 空格。

剩余 ~40% 解析失败是 ChatGPT 配额错误（不可恢复），但 P19d 阈值 60 + 配额恢复后会持续下降。

---

## 反直觉的 4 条教训

1. **样本偏差是策略最大的敌人**。第一周用 8 笔 demo 早期数据判断 BIO 是亏损源——全量 13 笔反转。**任何基于小样本的"显著"结论都要警惕**。

2. **alpha 高度集中是常态而非异常**。13 笔里 1 笔贡献 78% 利润，9 笔来自 BIO 一只。**真正的盈利策略需要多 symbol、长样本验证**。

3. **错误日志 ≠ 错误**。bot.err 每天 10000 行让人觉得问题严重，实际上 99% 是 INFO 级输出。**真正错误极少且集中在单一日（4-26）**。

4. **"我以为正确的改动"经常需要数据反转**。cooldown 看似合理，但用了一周后才发现误伤了真盈利来源。**任何防御性改动都要有"6 周后回测验证"的机制**。

---

## 下一阶段 — 2 周观察期

**不再加新功能**。让今天 7 个 commit 在 demo 里跑足 14 天，观察：

| 指标 | 当前 | 14 天后期望 |
|---|---|---|
| 累计胜率 | 46.2% | ≥ 50% 且**剔除单笔最大赢家**仍正期望 |
| 信号产出量/天 | 1060 | 700-900（白名单生效后） |
| Hermes 解析覆盖率 | ~50% | ≥ 70%（配额恢复 + parse_review 容错） |
| BLOCKED 频次 | cooldown 17 次 | 看 7d-loser 实际触发次数 |
| I 型 squeeze 触发数 | 0（刚上线） | ≥ 5 次（牛市更多） |

**2 周后的真正决策点**：
- 如果新规则下胜率/盈亏比真的稳定 → 考虑 mini 真盘
- 如果 I 型在实战中胜率 > A 型 → 加大权重，逐步替代 A 型
- 如果数据显示 alpha 还是不稳健 → 转向"半自动 + 人决策" or 产品化（认知工具方向）

---

## 一行价值总结

> **第二周做的不是"加功能"，是用全量数据校准上一周的判断错误**。
>
> 6 个优先级行动里，**3 个是修复（cooldown / parse / 白名单），3 个是清债（WIP / I 型 / doc）**。
> 没有一个新功能，但**整体策略的诚实度大幅提升**——下次决策不会再被 8 笔小样本带偏。

---

## 关联文档

- [`26-cooldown-v2-differentiated.md`](26-cooldown-v2-differentiated.md) — P19c-v2 差异化 cooldown
- [`27-i-structure-squeeze-detection.md`](27-i-structure-squeeze-detection.md) — P19f I 型 Squeeze 检测
- [`23-hermes-live-quote.md`](23-hermes-live-quote.md) — P17 Hermes 实时行情
- [`20-metrics-feedback.md`](20-metrics-feedback.md) — P14 metrics 闭环（route_decision data 参数）

---

## 私有 repo 7 个 commit 清单

```
e255bb7  feat(hermes-bridge): P19h parse_review 容错增强
930c2cc  feat(P19g): OKX 合约白名单过滤
2cc6ad1  feat(P19f): I 型 Squeeze 轧空检测
a04e94e  feat(routing): P19c-v2 cooldown 差异化（盈亏分流）
0daea67  chore: 补 settings 字段 + 清理 X 浏览器错误页 note
e984417  feat(P19): BTC regime gating + 信心度分数校准
7043135  fix(main_loop): route_decision 接收 data 参数（P14 metrics 闭环）
```
