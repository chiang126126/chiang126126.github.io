# 精灵捕手 L2 — 代码自审

这份文档对每个公开函数列出：
- 输入约束
- 关键不变式
- 边界情况
- 对应的测试

目的：让"我检查过了"这件事是**可验证的**而不是嘴上说说。

---

## `compute_chip_features`

**职责**：计算筹码集中度 + funder 去重 → `ChipFeatures`。

| 输入约束 | 检查 | 行为 | 测试 |
|---|---|---|---|
| `circulating > 0` | 强校验 | 否则 `raise ValueError` | `test_chip_circulating_zero_raises` |
| holders 非空 | 软检查 | 全部被排除时返回 inf cluster | `test_chip_all_excluded_returns_inf_cluster` |
| holders 排序 | 强制 | 内部按 balance 降序 | `test_chip_top10_share_basic` |

**关键不变式**
- top10/top50 计算前 **必须** 排除 CEX/burn/合约 ⇒ `test_chip_excludes_cex_and_burn`
- `excluded_count` 等于排除数 ⇒ 同上
- `cluster_factor = len(filtered) / clusters`，clusters=0 时为 `inf` ⇒ `test_chip_all_excluded_returns_inf_cluster`

---

## `funder_dedupe` / `_find_funder`

**职责**：沿入金链回溯，把同源地址合并成同一 cluster。

| 边界 | 行为 | 测试 |
|---|---|---|
| 空列表 | 返回 0 | `test_funder_empty` |
| 无入金记录 | 自己即 funder | `test_funder_no_incoming_self_funded` |
| 共同 root | 合并为 1 cluster | `test_funder_same_root_merges` |
| 链路经过 CEX 热钱包 | 在 CEX 处停止 | `test_funder_stops_at_cex` |
| 起点本身是 CEX | 自己作为 funder | `test_funder_starts_at_cex_address` |
| 形成环路 (A↔B) | visited 集合保护，不死循环 | `test_funder_cycle_protection` |
| 链路 > max_hops | 在 max_hops 处停下 | `test_funder_max_hops_respected` |
| 不同链路 | 分别计 cluster | `test_funder_two_independent_chains` |

**已知简化**（生产版需补强）
- 每跳只取 "第一个非 visited 的入金源"，未按金额加权 → 可能误判 dust funder 为主源
- BFS 是单线程的深度优先；多源情况下应改为图遍历 + 金额排序

---

## `stratify_oi`

**职责**：把 OI 拆成主力/Follow 两部分，计算综合操纵分（0-100）。

| 边界 | 行为 | 测试 |
|---|---|---|
| `total_oi <= 0` | 早返回 + `total_oi_zero` warning | `test_stratify_total_oi_zero` |
| 单一交易所 | `single_exchange_only` warning | `test_stratify_single_exchange_warning` |
| 不在 Binance（占比 = 0） | `no_binance_oi` warning，**不**触发 W_BINANCE_LOW | `test_stratify_not_on_binance_no_low_warning` |
| 相关性不可用（NaN） | `corr_undefined` warning，**不**触发 W_OPERATOR_HIGH | `test_stratify_nan_corr_does_not_penalize` |
| OI 与价格反向 | operator_pct = 1，触发 W_OPERATOR_HIGH | `test_stratify_operator_when_oi_anti_correlated` |
| 4 项全中 | 操纵分 = 100 | `test_stratify_full_manipulation_score` |
| 4 项全无 | 操纵分 = 0 | `test_stratify_friendly_score_zero` |

**关键修复**（实现时发现的 bug）
- **bug**：相关性 NaN 时把 corr 设为 0 → follow_pct=0 → operator_pct=1 → 错误地扣 W_OPERATOR_HIGH (25 分)
- **修复**：增加 `corr_available` 标志，NaN 时跳过该项的判定
- **教训**："数据不够"绝不能等同于"信号成立"——是测试逼出来的，不是看代码看出来的

---

## `_pearson_of_diff`

**职责**：用一阶差分计算 Pearson 相关性，避免趋势造成的虚假相关。

| 输入 | 输出 | 测试 |
|---|---|---|
| 差分成比例（同向） | r = +1 | `test_pearson_perfectly_positive` |
| 差分成反比 | r = -1 | `test_pearson_perfectly_negative` |
| **线性趋势序列** | NaN（差分常量，方差 0） | `test_pearson_linear_trend_yields_nan` |
| 一边常量 | NaN | `test_pearson_constant_series_returns_nan` |
| 长度不一致 | NaN | `test_pearson_length_mismatch_nan` |
| 时间戳错位 | NaN | `test_pearson_ts_mismatch_nan` |
| 样本不足（< 3） | NaN | `test_pearson_insufficient_samples` |

**重要不变式**
- "两个线性递增序列" → NaN 是**预期行为**而非 bug。这正是为什么用 diff 而不是 levels：避免"BTC 和 ETH 都涨"被解读成"两者强相关"。

---

## `detect_distribution_divergence`

**职责**：检测"价涨 + OI 跌 + holders 持平"的三层背离。

| 条件组合 | 输出 | 测试 |
|---|---|---|
| 三条全中 | `DISTRIBUTION_CONFIRMED` (strength=1.0) | `test_divergence_confirmed` |
| 价 + OI 中，holders 不可用 | `DISTRIBUTION_LIKELY` (strength=0.6) | `test_divergence_likely_when_holders_unknown` |
| 价 + OI 中，holders 增长太多 | `DISTRIBUTION_LIKELY` | `test_divergence_likely_when_holders_growing` |
| 价跌 OR OI 涨 | `NO_DIVERGENCE` | `test_divergence_no_divergence_*` |
| 样本不足 | `insufficient_data` | `test_divergence_insufficient_data` |
| 时间戳错位 | `ts_mismatch` | `test_divergence_ts_mismatch` |
| `operator_oi_fraction = 0` | OI 斜率被抹零 → `NO_DIVERGENCE` | `test_divergence_operator_oi_fraction_dampens` |

**纯函数承诺**
- 不做任何 I/O；所有数据从参数传入
- holders_change_pct 可选，None 时最高只能给 LIKELY

---

## `_linear_slope`

**职责**：OLS 线性回归斜率，纯 Python 无依赖。

| 输入 | 输出 | 测试 |
|---|---|---|
| 常量序列 | 0 | `test_slope_constant_is_zero` |
| 递增 [1,2,3,4] | +1 | `test_slope_ascending_positive` |
| 递减 [4,3,2,1] | -1 | `test_slope_descending_negative` |
| 单点 / 空 | 0（安全降级） | `test_slope_single_value_zero`, `test_slope_empty_zero` |

**手算验证**：
对 [1,2,3,4]：n=4, Σx=6, Σy=10, Σxy=20, Σx²=14
slope = (4×20 − 6×10) / (4×14 − 36) = 20/20 = 1 ✓

---

## `route_to_pool`

**职责**：把标的分到 4 个池中的一个，规则互斥，按优先级返回。

| 优先级 | 规则 | 触发条件 | 测试 |
|---|---|---|---|
| 1 (最高) | BLACKLIST: 极端 top10 | top10 > 0.85 | `test_pool_blacklist_extreme_top10` |
| 1 | BLACKLIST: 极端 manipulation | level > 85 | `test_pool_blacklist_extreme_manipulation` |
| 1 | BLACKLIST: 加速期 | daily_pump > 5.0 (=+500%) | `test_pool_blacklist_daily_pump` |
| 1 | BLACKLIST: Binance 占比过低 | 0 < binance_share < 0.10 | `test_pool_blacklist_binance_share_too_low` |
| 1 | BLACKLIST: 刷量 | vol/oi > 20 | `test_pool_blacklist_vol_oi_wash` |
| 2 | FRIENDLY (AND) | top10 ≤ 0.20 ∧ cluster_factor ≤ 3 ∧ level < 40 | `test_pool_friendly_baseline` |
| 3 | OPERATOR (AND) | top10 ≥ 0.50 ∧ level ≥ 50 | `test_pool_operator_baseline` |
| 4 | NEUTRAL | 默认 | `test_pool_neutral_when_between` |

**关键不变式**
- BLACKLIST 优先于 FRIENDLY：哪怕筹码很分散，操纵分极高时也必须拉黑 ⇒ `test_pool_priority_blacklist_over_friendly`
- "不在 Binance" (occupation = 0) ≠ "占比过低" ⇒ `test_pool_not_on_binance_is_not_blacklist`
- 友好需要 *所有* 条件同时满足 ⇒ `test_pool_friendly_requires_low_manipulation`
- 操纵池需要筹码 *和* OI 同时满足 ⇒ `test_pool_operator_requires_both_chip_and_oi`

---

## 整体审计反思

### 已修正的逻辑错误（实现过程中暴露的）
1. **NaN 当 0 处理**导致"数据缺失"被错误判定为"操纵存在" → `stratify_oi` 已加 `corr_available` 标志。

### 已知简化（v0 故意留的）
1. `_find_funder` 单线 BFS，没按金额加权 → 多源资金的 holder 可能错判 funder。
2. `_linear_slope` 不返回 R²，没法判断"斜率显著性" → 真实生产应改为带 p-value 的回归。
3. `detect_distribution_divergence` 用整段窗口的 OLS 斜率 → 对突变不敏感（例如最后 1h 的剧烈转向会被前 5h 的平稳稀释）。
4. `_pearson_of_diff` 用等步长假设 → 时间戳必须严格对齐，缺一个点都会失败而非插值。

### 安全 / 风格
- 全部公开 API 都是 `frozen=True` dataclass → 下游不能意外篡改。
- 全部 I/O 走 Protocol 接口 → 测试无需 mock 任何真实数据源。
- 阈值集中在 `config.py` → 调参只需改一个文件。
- 零外部运行时依赖（仅测试需要 pytest）→ 可在任何 Python 3.10+ 环境运行。

### 仍需在 L3+ 中处理（不在本模块职责内）
- 数据源限频 / 重试 / 缓存
- 历史快照存档（防 survivorship bias）
- AI 评分层与确定性下单代码的隔离
- OTOCO 挂单 / 三级熔断 / Telegram /halt
