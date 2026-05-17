# L8 复盘层 · 实现规范

> 代码实现位置：私仓 `cresus-bot/sprite/review/`
> 公仓提供历史样本库 `sprite_catcher.datasets`，私仓做 walk-forward + AI 报告。

## 核心职责

1. **信号后验**：每个信号触发后 T+5m / T+1h / T+24h / T+72h 的真实回报记录
2. **Walk-Forward 验证**：滚动训练 + 永远未见过的验证窗
3. **AI 周报**：自动总结 + 参数建议
4. **门槛检测**：Paper → Live 升级的硬指标

## Walk-Forward 流程

```
训练窗 (90 天)   验证窗 (30 天)
[──────────────][──────]
                    ↓ slide
       [──────────────][──────]
                            ↓ slide
              [──────────────][──────]
```

每次只用训练窗调参，**验证窗只看结果**。永远不能用未来数据修改阈值。

### 实现

```python
def walk_forward_run(
    samples: list[HistoricalSample],
    train_days: int = 90,
    validate_days: int = 30,
    step_days: int = 7,
) -> list[WalkForwardSlice]:
    """对每个滑动窗口跑一次策略，记录验证窗的真实 PnL。"""
    ...

@dataclass(frozen=True)
class WalkForwardSlice:
    train_start: datetime
    train_end: datetime
    validate_start: datetime
    validate_end: datetime
    trades: list[Trade]
    metrics: WalkForwardMetrics

@dataclass(frozen=True)
class WalkForwardMetrics:
    profit_factor: float       # 毛盈 / 毛亏
    win_rate: float
    avg_win_loss_ratio: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    total_pnl_pct: float
    sample_size: int
```

## Paper → Live 升级门槛

**全部满足**才允许：

| 指标 | 阈值 | 备注 |
|---|---|---|
| profit_factor | ≥ 1.5 | 毛盈/毛亏 |
| win_rate | ≥ 45% | 妖币策略不追求高胜率 |
| avg_win_loss_ratio | ≥ 2.0 | 让赢的覆盖输的 |
| max_drawdown_pct | < 20% | |
| max_consecutive_losses | ≤ 5 | 心理可承受 |
| sample_size | ≥ 80 笔 | 统计意义 |
| signal_T+72h_avg_return | > 0 且 p_value < 0.1 | 信号本身有效 |
| OOS 后 1/3 段单独满足以上 | ✅ | 防过拟合 |

**任一不满足** → 回到 Paper，找原因。

## 信号后验记录

每个触发的信号要落库：

```python
@dataclass(frozen=True)
class SignalAudit:
    ts: datetime
    symbol: str
    strategy_id: str           # "trend_follow" / "support_collapse" / ...
    signal_strength: float
    entry_price_at_signal: float

    # 后续真实回报（多个时间点）
    return_t5m_pct: float | None
    return_t1h_pct: float | None
    return_t24h_pct: float | None
    return_t72h_pct: float | None

    # 是否真的交易了（被 L7 admission 卡的也要记录，便于分析卡了多少机会）
    intent_admitted: bool
    admission_reject_reason: str | None
```

存到 ClickHouse `signal_audits` 表。

## 实盘上线后的逐步放量

```
第 1 个月: 实盘 = 设计仓位 × 1/10
第 2 个月: × 1/3 (前提：OOS 表现与 Paper 偏离 < 30%)
第 3 个月: × 1 (全仓)
第 4 个月+: 持续监控；任一阶段 OOS 显著恶化 → 回到 Paper
```

## 每周 AI 复盘报告（用 Claude Opus）

### 输入

- 过去 7 天所有交易（含被 admission 拒的 intent）
- 每个 SignalAudit 的 T+72h 回报
- 各策略的 PnL 分解
- 当前 Walk-Forward Metrics
- BTC / 总市值的态势变化

### Prompt 模板（私仓里维护）

```
你是 Sprite Catcher 的复盘分析师。基于以下 7 天数据，输出一份 markdown 报告：

# 本周复盘 ({date})

## 1. PnL 总览
[一段话总结]

## 2. 各策略表现
- trend_follow: PnL X，胜率 Y，平均盈亏比 Z
- support_collapse: ...
- short_vacuum: ...
- distribution: ...

## 3. 信号后验
- 哪些信号 T+72h 回报最好/最差
- 是否有信号阈值需要调整（给出**具体的**建议数值）

## 4. 被卡的机会
- 多少 intent 被 L7 admission 卡了
- 卡的原因分布
- 是否有真正错失的机会（被卡之后那个 token 后来涨/跌了多少）

## 5. 风控触发记录
- 看门狗触发了几次
- 每次的原因
- 是否需要调整阈值

## 6. 下周建议
[不超过 3 条具体可执行的建议]

要求：
- 数字必须直接引用，不能编
- 建议必须可执行（"调 X 阈值从 Y 到 Z"），不要"加强监控"这种空话
- 任何参数建议都要给出 reasoning
```

### 输出处理

- 报告以 Markdown 直接发到 Telegram + 存到 dashboard
- 参数建议**不自动应用**——必须人工 review 后改 `config.py` 才生效
- 三周内连续给出同一条建议 → Telegram 显眼提示"建议执行率 0%"

## Survivorship Bias 防御

回测 / Walk-Forward 时绝不能用"今天还活着的池子"做样本。

- 历史样本快照：每个候选池在 T 时刻看到的样子（含已 rug 的）必须存档
- 训练样本 = T 时刻可见的 token，不是后来才有的
- 私仓必须每天对全市场打快照，永久保存（ClickHouse 时序表 + S3 冷存）

## 失败模式 + 应对

| 失败 | 应对 |
|---|---|
| Paper 跑很好但 Live 完全不一样 | 立即停 Live，回到 Paper；最可能原因：撮合模型不真实 |
| 某策略连续 4 周亏 | 自动停该策略，触发深度复盘 |
| AI 周报输出格式错误 | 自动跑 schema 校验；失败则发原始数据 + 错误信息 |
| 历史快照磁盘满 | 告警；不能因此停采集（survivorship bias 会复发）|

## 测试要求

- Walk-Forward runner 用历史样本库（`sprite_catcher.datasets`）跑过一次
- Mock 一份"完美策略" → 验证 metrics 计算正确
- Mock 一份"全亏策略" → 验证门槛检测能拦住
- AI 周报 prompt 在 LLM 上跑过 3 个不同输入 → 输出稳定
