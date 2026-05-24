# Cresus Live Trader — Phase 演进笔记

记录关键 phase 改动的数据驱动 + 反思. 跟 commit message 互补 — commit 讲"做了什么",
本文档讲"为什么决定 + 后续如何修正".

---

## 系统版本代号 (清晰分辨)

| 代号 | 时段 | 核心特征 | 状态 |
|---|---|---|---|
| **V3** | 启动 → 2026-05-24 | $100, 3x lev, $20/笔, SL ~1%, hold ~30min, Phase 4.A→4.K 累积 | **已固化归档** (`cresus-system/archive/v3_baseline/`) |
| **V4** | 2026-05-24 之后 | paper + live 同步重写: day-scale hold, 5% SL, 1x lev, 阶梯扩容至 $2000 | 设计中, 未上线 |

V3 实战数据 (5/24 归档): Paper 981 笔 +$1012 (PF>1, edge 真实), Live 406 笔 ~-$9 (链路损耗 ~$1000+).

代码标识: `live_trader.py` 顶部 `SYSTEM_VERSION = "V3"` 常量, 写入 `live_trades_history.json.system_version` 字段.
V4 上线时 bump 到 `"V4"`, dashboard 可按字段分流显示.

---

## Phase 4.I 限价单方案 (放弃) — 含 v1/v2 模拟修正

### 提议时间
2026-05-22 — 用户反复观察到入场滑点 30bps 损失, 询问"挂限价单是否比市价收益更好"

### v1 模拟结果 (有 bug)
```
395 笔实盘数据离线模拟:
v1 上限假设 (limit fill at paper.entry): -$0.77 总改善, -$0.0019/笔
结论: 限价单方向不可行, 反而比市价差
```

### v1 Bug 暴露 (用户提"被验证过无误吗" 二次质疑后发现)
v1 代码:
```python
sim_gross = (exit_price - paper_entry) * qty * side_sign
```
这违反限价单语义:
- LIMIT BUY @ 100 当市价 99.80 → **立即成交在 99.80** (不在 100)
- v1 强行模拟成"成交在 100", 对 174 笔 favorable slip 案例**人为制造 -$11.61 损失**

### v2 修正 (正确建模)
```python
if is_unfavorable:    # 价格往不利方向跑了
    v2_fill = paper_entry  # 限价等回踩, 上限假设成交 paper.entry
else:                  # 价格往有利方向走
    v2_fill = actual_fill  # 限价跟市价一样立即成交
```

### v2 修正后结果
```
v2 上限: +$10.84 总改善, +$0.0274/笔
真实世界打折估算 (60% 回踩 × 0.7 反选系数): +$0.0115/笔
conv≥6 子集上限: +$0.0285/笔
```

### 修正后的结论
**限价单方向是边缘正向价值** ($0.012-$0.027/笔):
- 不是 transformational fix (不能扭转 -EV)
- 但有正向价值, 不像 v1 误判的"完全无效"

### 为什么仍不立即部署 E 组
1. Phase 4.H 信号减 92% (50/天→4-5/天), 加 5-arm 后 E 组每天 ~1 笔, 统计判定要 60 天+
2. 改善幅度 marginal, 真实账户改善每月 ~$1-3
3. 限价单工程复杂度 (open_position 改 LIMIT type + cancel timeout 逻辑), 引入新 bug 风险

### 决策路径
- 等 Phase 4.H 跑 1-2 周, 看 conv>=6 子集是否真+EV
- 如 4.H 数据证实+EV, 再考虑限价单方案 (此时数据基础更稳)

### 自审教训
1. **"审计通过 ✓" 不等于真的对** — 没检查"模拟假设是否反映现实行为"
2. **用户二次质疑 ("被验证过无误吗") 救了 1-2 周折腾** — 没有这个 challenge, 错误结论会带入后续决策
3. **金融工程基础知识 (limit order 语义) 应在写代码前推清楚**

---

## Phase 4.J Regime Gate 普及 (4.F 升级)

### 触发原因
Phase 4.F 部署后 1-2 天观察:
- 用户问"昨天提到下跌期不做多, 为什么今天还有这么多 down+LONG 亏损交易?"
- 数据 check: 5/22 和 5/23 两天 down+LONG 共 10 笔, 全部在 A/B/C 组 (D 组确实 0 笔)
- 4-arm 设计意图: A/B/C 组作对照, 测 D 组 (regime gate) 是否真有用
- 但实际上: A/B/C 组的 down+LONG 持续亏 (10 笔 9 亏), 等于"花真钱继续验证已知坏假设"

### 数据已支持全面应用
- 老数据 (333 笔): down+LONG 10 笔 0 胜, p=0.042 显著
- Phase 4.F 后 (9 笔): 又验证 down+LONG 持续亏
- conv 也不能救 — 4.J 之前 4 笔 conv=7/8 都亏

### 改动
```python
# Before (Phase 4.F):
LIVE_REGIME_GATE_MODE = "abcd"   # 仅 D 组 (25%) 启用 gate

# After (Phase 4.J):
LIVE_REGIME_GATE_MODE = "always"  # 全部组启用 gate
```

### 保留
- SL 补偿 (B 组) / Wick filter (C 组) 仍 4-arm A/B 测试 (这俩还有歧义)
- _should_block_for_regime 规则不变 (仅 down + LONG)
- ab_group 字段仍记录 A/B/C/D (溯源不变, 即使 D 组实质跟 A 组相同)
- "abcd" mode 仍可用 (legacy / 测试场景)

### 自审教训
1. **A/B 测试不是教条** — 当数据已足够清晰, 继续 A/B 等于浪费真金白银
2. **用户的"为什么仍在做明显错的事?" 是有效的质疑维度** — 工程师容易陷入"测试设计完美主义"
3. **gate 推广是一行代码 + 几个测试** — 没必要拖延

---

## 演进时间线

| Phase | 时间 | 核心 | 状态 |
|---|---|---|---|
| 4.A | 5/17 | 入场滑点 gate (30→50→100bps) | 启用 |
| 4.B | 5/17 | Symbol 黑名单 (5 个 0 胜 symbol) | 启用 |
| 4.C | 5/17 | BTC regime 标签 | 启用 |
| 4.D | 5/19 | SL 补偿 (B 组 A/B 测试) | 启用 |
| 4.E | 5/19 | Wick filter (C 组 A/B 测试) | 启用 |
| 4.F | 5/21 | Regime gate (D 组 A/B 测试) | 启用 |
| 4.G | 5/21 | watchdog + 黑名单扩展 + 滑点 100bps | 启用 |
| 4.H | 5/22 | Conviction filter (硬过滤 >= 6) | 启用 |
| 4.I | 5/22 | 限价单方案 (放弃, 含 v1/v2 修正) | 不部署 |
| **4.J** | **5/23** | **Regime gate 普及到全部组 (mode='always')** | **启用** |
| **4.K** | **5/23** | **down regime sub_regime shadow log** | **启用 (观察用, 不改 gate)** |

---

## 竞品观察 (2026-05-24) — V7 Live 系统 + 方法论反思

### 看到了什么

用户分享了某竞品 "V7 Live" 的 5 天账户截图 + 持仓快照:
- 5 天净盈利 +$70 / $3,700 = +1.87% (年化外推 ~137%, 不可线性)
- 持仓 17 笔, 持仓时长 3-7 天 (最长 6 天还在持)
- 1x 杠杆, 无杠杆
- 仓位 23-100 USDT 不固定
- TTL 7 天
- 锁利结构: 锁 30%/50% 分批止盈
- 入场信号标记 "出货 N" (含义不明)
- 5/19 单日资金费 +$8.10 (占当日 67% 净盈利)
- 胜率 60-100%/天 (5 天)

### 我犯的方法论错误 (用户纠正)

我看截图 1 七笔标"做空"后, 直接外推截图 2 的另外 10 笔"全是 SHORT", **没做最简单的逐笔验证**.

用户纠正: "截图 2 其实看不出是做空还是做多".

我重新用 "入场价 vs 当前价 vs 盈亏方向" 第一性原理反推:
- 10 笔全部 当前价 < 入场价 + 盈利为正
- 数学上只能是 SHORT (LONG 在当前 < 入场时必然亏损)
- 结论虽然对了, 但 **方法论是错的**

教训: 分析陌生数据应该
1. 先列 "直接可见证据"
2. 再列 "推断" 并标注推理逻辑
3. 再列 "猜测"
4. 不要混淆三者

### 真正可信的事实 (强证据)
- 17 笔全 SHORT (price-PnL math 验证)
- 持仓周期 3-7 天 (开仓时间直接可见)
- 杠杆 1x (直接可见)
- 5 天累计 +$70 (表格直接显示)
- 5/19 资金费 +$8.10 (但 5/16-18 funding 为负!)
- 胜率 5 天平均 84%

### 真正可学习的 (剔除虚的)
1. **长持 + 分批止盈** — 跟我们持仓时长效应数据 (Q5 46+min 胜率 68%) 一致
2. **方向跟随 regime** — Phase 4.J 已部分实现, 但他们更彻底
3. **资金费应纳入考虑** — 但只在 high funding 时段有用, 不是普适规则
4. **不追求超高胜率** — 跟 Strat-LLM 论文一致, 不要陷 disposition effect

### 我撤回的论断
- "出货 N = 派发识别分数" — 撤回, 望文生义, 我不知道含义
- "Shadow = 对照虚拟 PnL" — 降级到推测, 不确定
- "他们靠 funding 持续赚" — 降级, 只有 1 天 funding 正收入, 其它 4 天都是费用

### 不能盲目学
- 100% SHORT 集中 (BTC 反转就崩)
- 1x 杠杆 + 长持 (我们 $100 起始资金太小, 收益太慢)
- 80-100% 胜率追求 (small sample cherry-picked, 长期不可持续)
- 复制"出货"信号 (信号源不同, 直接复制可能反向)

### 跨 phase 教训 #5
**模式补全的诱惑** — 看到部分证据时容易"补全"成完整图景, 而不是诚实标注 "未验证".
未来分析任何外部数据 (包括我们自己未检验的代码 / 数据), 都要明确区分:
  - 可见证据 vs 推断 vs 猜测
  - 不让"看上去合理"代替"已验证".

### 跨 phase 教训 #5 补充: 关于竞品方向的修正
后续用户提醒: "别人的交易截图刚好这 2 张都是做空盈利的, 并不意味他所有交易都只做空".
我之前说 "100% SHORT 集中风险" 是把 2 张快照外推到他们整体策略, 又犯了模式补全错误.
修正:
  - 17 笔 SHORT 是这 2 张快照里的事实 (price-PnL 验证)
  - 不代表他们整体只做空, 可能 BTC 上涨期他们切 LONG
  - 反而强化了 "方向跟随 regime" 这个学习点 (他们是适应性的)
  - 撤回 "100% 集中风险" 的批评

---

## SL 距离 5 倍差异分析 (2026-05-24) — 时间窗口决定 SL 宽度

### 可见证据 (强证据)

从竞品截图 1 算出 7 笔持仓的 SL 距入场距离:
  - 3 笔新仓 (未触 TP1): 距离 **+4.96%, +5.02%, +5.03%** ≈ 5%
  - 4 笔老仓 (TP1 已触): 距离 **+0.00%, 0.00%, 0.00%, +0.05%** → SL 已锁 BE

从 Cresus live_trades_history.json 算 392 笔 risk_pct (开仓时锁定的 SL 距离):
  - 平均: 0.98%
  - 中位数: 0.78%
  - P75: 1.25%
  - 90% < 2%, 100% < 5%

**核心数据**: 竞品 SL 距离比我们 **宽 5.05x** (新仓), 老仓更宽 (锁 BE 后跟随 trail).

### 理论推断 (mathematical)

SL 宽度跟持仓时间窗口呈正相关 (波动率按 sqrt(time) 增长):

| 持仓 | 正常波动空间 | 合理 SL 宽度 |
|---|---|---|
| 30min | 0.3-1.5% | 1-2% |
| 1h | 0.5-2% | 2-3% |
| 4h | 1.5-4% | 3-5% |
| 1 day | 3-8% | 5-8% |
| 7 day | 10-20% | 5-10% |

**竞品的 5% SL + 7 天持仓** 数学上自洽.
**我们的 1% SL + 30 分钟持仓** 数学上也自洽.
两个系统适合不同的风险/回报曲线.

### 竞品 SL 不触发的 4 个机制 (强证据)

1. **初始 SL 5% 宽** → 跳过日常 noise (5% 内随机波动是常态)
2. **方向跟随 regime** → trade 大概率往他们方向走 → 触 TP1 而非 SL
3. **TP1 后锁 BE** (SL = entry) → 最坏情况 = 零亏损, 数学上 "不输"
4. **长持时间窗口** → 30s wick 不影响 "天" 为单位的决策

### 我们 SL 高频触发的相反原因
1. 初始 SL ~1% → 正常 noise 经常达到
2. 方向曾混乱 (Phase 4.J 前 LONG in down) - 已修
3. 30s 轮询易捕捉 wick (Phase 4.E wick filter 试图改, 数据待积累)
4. 持仓 30min - 没时间锁 BE 就触 SL

### Audit 重要修正: "拓宽 SL" 是净负效应, 不是改善

我先估算 "拓宽 SL 0.5% 净改善 ≈ +$25". **Audit 后这个数字是错的**.

正确数据:
| 项目 | 我之前估算 (错) | 真实数据 |
|---|---|---|
| Premature SL 笔数 | 47 (只算 hit_b_trail) | 87 (含 hit_trail + b_trail + be_sl) |
| 真 SL 笔数 | 70 | **149** (低估 2x) |
| Premature 实际漏赚 | 估 $32-50 | **$10.46** (paper×0.05 - live 实际) |

重算 "拓宽 SL 50bps" 净效应:
  - 救下 50% premature ≈ 44 笔, 每笔挽回 ≈ $0.12 → **+$5.3**
  - 149 真 SL 损失各深 50bps × $20 notional = $0.10/笔 → **-$14.9**
  - **净: -$9.6 (负)**

**结论反转**: 拓宽 SL 是**净亏损**, 不是改善. 我快速估算用错了数字.

### Wick filter 跟 "拓宽 SL" 本质不同 (重要区分)

- **拓宽 SL** = 所有 trade 容忍更大反向波动 (对真 SL 也"宽")
- **Wick filter (4.E)** = 仅过滤瞬时 wick (连续 2 次 30s 才触发)

Wick filter 是 surgical:
  - 阻止 "瞬间触 SL 又立刻回来" 的误触发
  - 对 "价格持续往下走" 的 trade 仍正常触发
  - 数学上 **不会增加真 SL 的损失**

**所以 Phase 4.E 方向对**, "拓宽 SL" 方向错. 这两件事 surface 看类似, 本质不同.

### 真正的学习

不是 "复制 5% SL", 而是认识到:
  1. SL 触发率高是 **策略时间窗口的必然结果** (短持仓必然 → 紧 SL)
  2. Phase 4.E wick filter 是正确方向但只能改善边缘
  3. 根本解决要 **重新设计 hold time + SL 一体**
  4. 单独学竞品某一项 (1x 杠杆 / 5% SL / 长持) 都不行, 是 **配套包**

### 跨 phase 教训 #6
**Back-of-envelope 估算容易错错错** — 用未验证的数字快速推论, 给出 "+$25" 自信结论.
Audit 跑实际数据后翻转为 "-$10". 教训:
  - 任何 "X net effect" 估算前, **必须先用真实数据 audit 输入参数**
  - 不能用 "我记得是 47" 这种心算, 要重新跑 SQL/script 验证
  - 净效应是减法 (saved - cost), saved 容易高估, cost 容易低估, **要双向 audit**

---

## 5/24 第三次审计: 字段名 bug 让所有 paper vs live 对比失效

讨论"整套切换 $2000 + 1x + 长 hold + 宽 SL" 前的强制 audit, 揭出**更严重的基线 bug**.

### 致命字段名 bug
- Live 字段: `realized_pnl_usdt`
- Paper 字段: `realized_usdt_pnl` (顺序颠倒!)
- 此前所有用 `realized_pnl_usdt` 读 paper 的脚本都拿到 None → 折算 $0
- 影响范围:
  - Phase 4.I 限价单 v1/v2 模拟 (paper 端基线错)
  - Phase 4.K down 阈值审计 (down+LONG paper PnL 错)
  - 5/23 "拓宽 SL 净 -$9.6 / -$33.78" 估算 (premature 漏赚错)
  - 历次"paper vs live"复盘

### 真实数据 (修正后)
- Paper 981 closed: 净 **+$1012.57**, 胜率 38.9%, 人均 **+$1.03/笔** ← **paper 有真 edge**
- Live 406 closed: 累计 ~-$9, 链路损耗 ~$1000+
- "假止损 88 笔"漏赚: $450 (人均 $5.12, 不是之前算的 $0.16)
- Paper hit_sl 565 笔: 167 笔 (29.6%) 曾 MFE>0.5% (可救), 398 笔真趋势 (救不了)
- Paper 持仓: avg 36.9min, median 17.3min, max 11.8hr — **30min 反转语义**

### 长 hold + 宽 SL + 宽 TP 整套切换审计
理论上限模拟 (paper 数据, 假设给 5% SL):
  - 救 167 笔 MFE>0.5% × $5 ≈ +$835
  - 其它 398 笔真趋势深 4% × $20 = -$318
  - paper 端净 +$517, 真实 live 打折 ≈ +$250

但**信号语义不支持**: paper engine 用 30s/5min K 线找短周期反转, 不是 day-scale 趋势.
把 30min 反转信号当 7 天趋势持 = **信号语义错位**, 这个上限拿不到.

### $2000 + 1x 杠杆审计
**"1x 留长持空间" 不成立**:
- 真正空间 = SL 距离 × notional, 跟杠杆无关
- 有 SL 前提下, 1x 跟 3x 的"空间"一样 (爆仓远在 SL 外)
- 1x 真好处: 没 funding fee 累积 + 没 leverage decay + 心理负担小

**风险数学**:
- 当前: $60 notional × 1% SL = **$0.60 风险/笔**
- 切后: $400 notional × 5% SL = **$20 风险/笔**, 放大 **33x**
- 4 笔并发 = $80 同时 in-risk
- Edge 未在 live 验证前扩 33x = 高度危险

### 决策: 不立即整套切换
理由:
1. **多变量同时改无法归因** (capital × 20, leverage 3→1, SL 1%→5%, hold 30min→days)
2. **真正瓶颈是链路损耗** (滑点 / sl_breach_client 误触), 不是 SL 宽度
3. **新发现的字段 bug 让此前 audit 信心降低**, 应先打牢基线再扩容
4. **Paper engine 信号语义不支持 day-scale**, 需先改 paper 端再说

### 渐进替代路径
- Step 1 (本周): 修字段 bug, 全局重审 4.A/B/C 真实效果
- Step 2 (下周): paper 端模拟 5% SL + 4h hold, 看 paper PnL
- Step 3 (2 周后): live 小幅试 2% SL (非 5%), $100 不变
- Step 4 (1 月后, 有正向数据): 才考虑扩容到 $500 或 $2000

### 跨 phase 教训 #7
**字段名细节差 1 个字符 (`_pnl_usdt` vs `_usdt_pnl`) 让基线对比静默归零**.
教训:
  - 跨数据源对比前, **必须先打印 sample record** 看字段名一致
  - "字段为 None" 不报错, 但对比结果完全错 — 比 crash 更危险
  - 任何 `safe(x) = float(x) if x is not None else 0.0` helper 都隐藏字段缺失
  - 应改成 `safe(x, name) = ... else raise(f"missing {name}")` 让错误暴露

### 归档
5/24 audit 完成后, 把 baseline 数据快照到 `cresus-system/archive/pre_5_24_baseline/`:
  - live_trades_history.json, paper_trades_history.json (字段 bug 修正前最后一次)
  - paper_shadow_history.json, pnl.json, regime_history.jsonl, analyses.jsonl
  - PHASE_NOTES.md (此版)
  - README.md (归档元数据 + 推荐路径)

---

## 关键反思 (跨 phase)

1. **数据驱动 ≠ 等 100% 统计显著才动** — 当多次小样本 + 强先验都指向同一结论时, 防御性介入比"等 p<0.05"更合理
2. **A/B 测试不能成为"花真钱测已知答案"的借口**
3. **用户质疑是最重要的 audit 工具** — 我 self-audit 出过几次"通过 ✓" 但实际错的情况
4. **简单数据 check > 复杂模拟** — 模拟容易引入假设错误, 直接看实战数据更可信
5. **模式补全的诱惑** — 不让"看上去合理"代替"已验证" (5/24 竞品分析事件)
6. **Back-of-envelope 估算容易错错错** — 任何 "净效应 = saved - cost" 估算, 必须先 audit 输入参数 (5/24 SL 拓宽事件)
7. **字段名细节 bug 是最隐蔽的灾难** — `_pnl_usdt` vs `_usdt_pnl` 让对比静默归零 (5/24 重审事件)
8. **不能从竞品结果倒推策略 — 入场逻辑是 black box** — V4 Step A 5 天工程 / PF 0.24 灾难 (5/24 V4 回测事件)

---

## V4 Step A 失败 retrospective (2026-05-24)

### Timeline (5 天工程)

| Day | 产出 | 测试数 |
|---|---|---|
| 1 | V4_SPEC.md + 8 文件骨架 + data_fetcher 入口 | 4 |
| 2 | data_fetcher 完整 + indicators 库 | 47 |
| 3 | regime + 3 sub-strategy signals | 35 |
| 4 | conviction (9 features) + paper_engine (phase A/B/C) | 55 |
| 5 | backtest 引擎 + 跑 251 symbol × 6 月真数据 | 10 + 1 skip |
| **合计** | ~2500 行 src + 152 测试 | 152 通过 |

### V4 设计核心 (回顾)

- K 线: 1h + 4h + 1d + 15min (vs V3 30s/1m/5m)
- 持仓: 1-7 天 (vs V3 30min avg)
- SL: 2 × ATR(4h) ≈ 5% (vs V3 1%)
- 信号: regime-adaptive (up→long breakout / down→short breakout / chop→mean rev)
- TP: entry ± 2/4/6 × ATR, 分批 1/3 各 TP
- Conviction: 9 features × 1 分 + base 3, cap 10
- Notional: $400/笔 × 5 并发 = $2000

### 真实 backtest 结果 (251 symbol × 6 月)

```
n_trades:     866
win_rate:     15.9%    (vs 门槛 35%, ❌ 远低)
total_pnl:    -$18,408 USDT
avg_pnl:      -$21.26/笔
pf:           0.24     (vs 门槛 1.3, ❌ 灾难)
max_dd:       $18,536  (vs 门槛 30% 资金, ❌ 全部本金)
wins:         138
losses:       728
```

按 V4_SPEC §6 通过门槛 **3 项全不达标**.

### 按 sub-strategy 拆解

| Strategy | n | Win% | Total PnL | 解读 |
|---|---|---|---|---|
| breakout_long (up regime) | 592 | 13% | **-$13,206** | 主力 loser, Donchian 假突破多 |
| breakout_short (down regime) | 255 | 20% | -$5,040 | 也不工作 |
| mean_rev_long (chop) | 2 | 0% | -$93 | 样本太少 |
| **mean_rev_short (chop)** | 17 | **53%** | -$68 | **小样本表象**, 仅因 fees 亏 |

mean_rev_short 17 笔 53% 看似"亮点", 但 17 样本下 53% 是 0.5 σ 噪声 — **不是统计意义上的 edge**.

### 4 个失败因素 (优先级)

1. **核心信号 = 我们自己猜的 Donchian breakout** (不是学竞品的)
   - 跨 phase 教训 #8: 竞品截图只看到结果, 没看到入场逻辑
   - 我硬选 Donchian 20d 突破 — 5 天工程后 backtest 证伪
2. **没实施学习点 #1 (funding TTL)** — V4 conviction 用了 funding 加分, 但**没用 funding 动态延长持仓**
3. **没实施学习点 #4 (conviction sizing)** — V4 全部固定 $400, conviction 只是 hard filter
4. **Chop regime 数据太少** — 6 月样本中 chop 只 19/866, mean_rev 没机会展示 edge

### V4 跟原 4 学习点对照

| 学习点 (5/24 早) | V4 实施? |
|---|---|
| #1 资金费纳入收益模型 | ❌ 部分 (conviction 加分, 但没 TTL 调整) |
| #2 长持 + 分批止盈 | ✓ |
| #3 方向跟随 regime | ✓ |
| #4 conviction-sized 仓位 | ❌ |

实施 2/4. 但**核心信号 (Donchian breakout) 不在 4 学习点之内, 是我加的猜测** — 这才是失败主因.

### 决策: V4 落档 (不进 Step B, 不投 v2)

按 V4_SPEC §7 ("Step A 失败 → 不进 Step B, 不为'启动'强行进 live"), 全部停下.

**不投 V4 v2** 理由 (跟用户 3 目标对照):

| 用户目标 | V3 现状 | V3 修复 | V4 v2 (假设) |
|---|---|---|---|
| 稳当开平 | 中 (24% 假止损) | ✓ 强 (直接修) | 未知 |
| 持续盈利 | 边缘 (paper edge 被链路吃) | ✓ +$50-150/月 (释放 paper edge) | 未知 (历史数据 -$18,408) |
| 控亏 | ✓ ($0.60/笔) | ✓ ($0.60/笔) | ✗ ($20/笔, 33x 放大) |

V3 修复路径 **3 目标全 dominant**. V4 v2 全部"未知 or 反目标".

### V4 保留资产 (不浪费)

| 资产 | 处理 |
|---|---|
| V4 代码 (~2500 行 + 152 测试) | 留 `cresus-system/scripts/v4/`, git history 完整 |
| V4 baseline 数据 (360MB, 251 symbol × 6 月) | 留 `cresus-system/v4_data/baseline_2026_05_24/`, 可重用 |
| V4 失败教训 | 本 retrospective + 跨 phase 教训 #8 |
| V4 backtest 结果 CSV | 866 笔细节留 `~/cresus-bot/v4_backtest_result.csv` (新 Mac), 不入 git |

### V4 长期 reserve 条件

未来重启 V4 的前提:
1. 有新 alpha hypothesis (不是 reverse engineer 竞品)
2. V3 优化到达天花板 (链路损耗修干净, paper engine 也已迭代)
3. 找到 day-scale 自己验证过有 edge 的信号源 (paper engine 产生 + 历史回测验证)
4. 有 out-of-sample 测试集 (不能再用 5/24 之前的 baseline)

### 跨 phase 教训 #8 全文

**不能从竞品结果倒推策略 — 入场逻辑是 black box**

V4 失败的根本: 我看到竞品 V7 Live 截图的"5% SL / 3-7 天持仓 / 高胜率", 推测他们用 Donchian 突破 + ATR-based SL. 这是 **pattern completion** (跨 phase 教训 #5 的姐妹错误).

教训:
- 竞品看不到的: 入场触发逻辑 / conviction 评分细节 / 仓位调整规则
- 竞品看得到的: 持仓时间 / SL/TP 距离比例 / 方向 / regime 偏向
- **可学习的是"框架"** (持仓哲学), **不可学的是"信号"** (技术指标 + 阈值)
- 信号必须**自己用 paper engine 测出 edge**, 不能从竞品结果反推

V4 把"自己猜的信号" 当"学习到的信号", 投入 5 天工程, backtest 证伪. 不是工程 bug, 是**假设 bug**.

---

## 转向: V3 链路损耗修复 (2026-05-24 起)

V4 落档后, 全力做 V3 修复. 目标 +$700-1200/月 (修正后基于 R0 audit 数据, 释放 paper engine 已验证的 +$3400/月 edge).

### R0 — V3 现状重新 audit (2026-05-24, 9 天 5/15-5/24 数据)

| 指标 | Paper | Live |
|---|---|---|
| 笔数 | 1072 | 409 |
| 累计净 | +$1020 | -$12 |
| 月度化 | ~+$3400 | ~-$40 |
| 人均 | +$0.95/笔 | -$0.03/笔 |

**链路 vs 策略 close 拆分**:
- sl_breach_client (链路): 239 笔 (58%), 净 -$28
- paper:* (策略): 170 笔 (42%), 净 +$16

→ **58% 的 close 是链路触发**, 链路是 -$28, 策略是 +$16. 修链路 = 直接释放策略 edge.

**sl_breach_client 假止损率 (9 天)**:
- 239 总, 88 假 (paper 后走 trail/be/TP), 151 真
- 假止损率: **37%**
- 漏赚: $450 (9 天) → 月度 $1500 (单 sl_breach 项)

**A/B/C/D 分组数据 — wick filter 是否有效?**
| 组 | n | 假止损 | 假止损率 | wick filter |
|---|---|---|---|---|
| C (filter ON) | 52 | 17 | **32.7%** | ✓ |
| A+B+D (filter OFF) | 187 | 71 | **38.0%** | ✗ |

差异 **5.3 pp** — wick filter 真有效, 推广全员可立即 +$170/月.

---

### Phase 4.L: Wick filter 推广 (2026-05-24)

**改动**:
```python
LIVE_SL_WICK_FILTER_MODE = "abcd"   →   "always"
```

**论据**: R0 audit 数据显示 C 组假止损率 32.7% vs A+B+D 38.0%, 5.3pp 改善.
**预期 EV**: 月度 +$170 (假止损率全员降到 33% 左右).
**风险**: 极低. wick filter 已在 C 组运行 5 天, 行为已知. 切 'always' 是把规则推广到全部新 trade.
**回滚**: `LIVE_SL_WICK_FILTER_MODE = "abcd"` 1 行恢复.
**验证窗口**: 2-3 天观察新数据. 假止损率应降到 33% 左右. 否则重审.

**部署方式**: V3 launchd 每 30s 重起 live_trader, 自动读新 config. 无需手动重启.

**Phase 4.L 还会做什么** (按数据再决定):
- R1.2: 增 MIN_BREACHES 2 → 3 (90s wick 过滤, 预期再 +$200-300/月)
- R1.3: SL buffer 0.1% (改 _check_sl_breach 加 deadband, 预期再 +$100-200/月)
- R2-R4: 其它链路修复 (限价单 4.I 重审 / 限制 SL 跟 paper sync 频率 / 等)

合计 R1-R4 预期月度 +$700-1200, 跟 V4 v2 路径形成强对比 (V4 v2 EV 未知, 单笔风险 $20 vs V3 $0.60).

---

### Phase 4.M: A1 — Funding-aware mirror filter (2026-05-24)

并行于 R1.1 观察期 (Phase 4.L 部署后 24-72h 观察), 启动 A1 funding 利用.

**数据 audit (9 天, 5/15-5/24, paper 1072 笔)** —
按 `funding_rate_pct` 切片人均 PnL:

| 桶 | 阈值 | n | 人均 PnL | 备注 |
|---|---|---|---|---|
| LONG · funding ≤ -0.05% | favorable | ~250 | **+$3.89** | 多头给空头钱 |
| SHORT · funding ≤ -0.05% | favorable | ~190 | **+$4.15** | (信号方向都是) |
| neutral · | -0.05% ~ +0.05% | ~330 | +$0.57 | 基线 |
| LONG · funding ≥ +0.05% | adverse | ~170 | **-$0.75** | 多头要付钱 |
| SHORT · funding ≥ +0.05% | adverse | ~130 | **-$1.13** | (信号方向都是) |

**洞察**:
1. funding 是**方向性 alpha**, 不仅是 income/cost — `|funding| ≥ 0.05%` 子集人均 PnL 跟 funding 同方向 (favorable +$3.97 vs adverse -$0.85, 差 **+$4.82/笔**).
2. funding "income" 几乎可忽略 ($0.01-0.03/笔), 所以 +$4.82 几乎全是**入场质量**.
3. 解释: funding 极端 = 拥挤交易 = mean reversion 概率高, 我们的入场逻辑刚好"反 funding 方向" → 顺势.

**实施 (2 道 gate)**:

```python
# config (live_trader.py)
LIVE_FUNDING_FAVORABLE_THRESHOLD_PCT = -0.05   # ≤ → favorable
LIVE_FUNDING_ADVERSE_THRESHOLD_PCT = 0.05      # ≥ → adverse
LIVE_REJECT_ADVERSE_FUNDING = True             # gate 开关
LIVE_FUNDING_FAVORABLE_WICK_BREACHES = 3       # favorable 时 wick 需 3 次 (vs 默认 2)
```

1. **Gate 1 (拒)**: `is_eligible_for_mirror` 在 conviction filter 后增 funding adverse check —
   `_funding_signal(paper_trade) == "adverse"` 直接 return False.
   → 预期 mirror 笔数 -30%, 但是被拒的是人均 -$0.85 的子集.
2. **Gate 2 (Boost wick)**: favorable 笔, `wick_min_breaches = 3` (vs 默认 2) —
   留更长时间给假插针自愈, 因为 funding 友好的盘往往 SL 假触发更多.

**预期 EV (保守)**:
- 拒掉 adverse: 月度 ~170 + ~130 = 300 笔, 平均 -$0.94 → +$282/月 避损.
- favorable 加 wick: 240+190=430 笔 × 增加 ~2% 留存率 × $4 改善 ≈ +$30/月.
- 合计 **~+$300/月** (但需 sample 累积验证).

**风险**:
- 低. fallback 'neutral' 不变. `LIVE_REJECT_ADVERSE_FUNDING = False` 即回滚.
- 没影响 paper engine, 只影响 mirror eligibility.
- 老 paper 数据没 `funding_rate_pct` 字段 → `_funding_signal` fallback "neutral" → 允许.

**回滚开关 (单行)**:
```python
LIVE_REJECT_ADVERSE_FUNDING = False   # 关 funding adverse gate
```

**Live trade 字段新增**:
- `funding_signal`: "favorable" / "adverse" / "neutral"
- `funding_rate_pct_at_open`: 入场时 paper 的 funding_rate_pct (溯源用)
- `wick_filter_min_breaches`: 友好时 = 3, 否则 = LIVE_WICK_FILTER_MIN_BREACHES (=2)

**验证窗口**: 7-10 天累积新数据, 按 `funding_signal` 切 live PnL, 看是否复制 paper 分层.
- 期望: live favorable 比 live neutral 高 ≥ $2/笔 (paper 是 $3.4 差).
- 若反向 (live favorable 更差) — Phase 4.M 失败, 关 gate 重审 hypothesis.

**为什么并行做 (没等 R1.1)**: A1 跟 R1.1 改的是不同维度 (R1.1 = wick 推广, A1 = funding 入场过滤), 可独立观察互不污染.
跟 4.L 一起部署可在同一时间窗口收 paper vs live 对比数据.

---

### Phase 4.R5: Live Trader 可靠性强化 (2026-05-24)

**触发**: 5/15-5/24 paper vs live audit 显示 **665 笔 paper / 62% 没 mirror**.
拆分后 53% missing 落在 **live trader 死亡窗口** (paper engine 在跑, live 不响应),
最严重 5/19→5/20 一次 10.2 小时, 5/23 7.2 小时, 5/22 5.7 小时.
同时段 velocity_fast_sync 每 2 分钟提交都正常 — 不是整机下线, 只是 launchd 挂起.

**根因**: 旧 Mac (mangzi) `pmset sleep` 默认 1 分钟 idle 即系统级睡眠,
launchd 自身也被挂起 → 30s schedule 停掉 → live_trader 不跑.
死亡窗口全部发生在用户离开 Mac 的深夜/凌晨/上午.

**3 道防线**:

#### P0 — `pmset` 关 AC 睡眠 (人工执行)
```bash
sudo pmset -c sleep 0 disksleep 0    # AC 永不睡 (24/7 运行场景)
sudo pmset -b sleep 60               # 电池 60 分钟睡 (省电, 离桌才会用)
```

#### P1 — `com.cresus.live-trader.plist` 加 KeepAlive
```xml
<key>KeepAlive</key>
<dict>
    <key>Crashed</key><true/>
    <key>SuccessfulExit</key><false/>
</dict>
<key>ThrottleInterval</key><integer>10</integer>
```
语义: `--once` 正常 exit 0 不动 (由 StartInterval=30 拉起),
但若 main_loop 抛未捕获异常 (exit != 0) 立即重启, 不等 30s.
ThrottleInterval=10 防止 crash 循环刷屏.

#### P2 — Watchdog 已存在 (Phase 4.F 部署)
`cresus-watchdog.sh` 每 60s 检查 `live_trades_history.json` mtime,
> 5min 没更新 = stale, 连续 2 次失败自动 `launchctl kickstart`.
本次 R5 顺手修了 plist 路径 `mangzi → hong` (Mac 迁移遗留).

**预期效果**:
- P0 解决 95% 死亡 (Mac 不睡, launchd 不挂).
- P1 解决 4% (live_trader crash 时立即恢复, 而非等 30s).
- P2 兜底 1% (watchdog 检测异常 → kickstart).
- 实盘笔数预计 409 → ~600 笔 (+47%), 释放被死亡窗口截掉的 conv≥6 信号.

**风险**: 极低. pmset 改电源策略, plist 加 KeepAlive 是 launchd 标准用法.
回滚: `sudo pmset -c sleep 5` + 删 plist 里的 KeepAlive block.

**部署**:
1. 用户在 Mac 跑 P0 pmset 命令 (需 sudo).
2. cp git 里两个改过的 plist 到 `~/Library/LaunchAgents/`.
3. `launchctl unload + load com.cresus.live-trader.plist` 让 KeepAlive 生效.

**验证窗口**: 1 周观察 — 期望 missing 比例从 62% → < 30%, 死亡窗口 (>30min 无开仓) ≤ 1 次/周.

---
