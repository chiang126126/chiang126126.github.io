# 精灵捕手 Sprite Catcher · 总览

> 公仓里的纯计算参考实现：[`cresus-system/sprite-catcher/`](../sprite-catcher/)
> 真实数据源接入 + LLM 调用 + 实盘下单：私仓 `cresus-bot`

## 分层总览

```
L1 数据采集         (Binance Skills + 链上节点)        ── 私仓 cresus-bot
   ↓
L2 特征工程         chip / oi / divergence / pool_router ── 公仓 ★
   ↓
L3 安全闸           safety_gate (long/short)             ── 公仓 ★
   ↓
入场信号             trend_follow / support_collapse /    ── 公仓 ★
                    short_vacuum / divergence
   ↓
L4 AI 评分          (Claude / DeepSeek)                  ── 私仓 (spec 在 27)
   ↓
L5 策略编排         strategies → TradeIntent             ── 公仓 ★
   ↓
L6 执行             OTOCO + 看门狗 + 风控                ── 私仓 (spec 在 28)
   ↓
L7 组合层           portfolio_manager (regime + caps)    ── 公仓 ★
   ↓
L8 复盘             walk-forward + AI 周报               ── 私仓 (spec 在 29)
```

## 公仓 vs 私仓 分工

| 责任 | 公仓 (本仓库) | 私仓 cresus-bot |
|---|---|---|
| 纯计算特征 | ✅ | 引用公仓 |
| Protocol 接口定义 | ✅ | 实现 |
| 历史样本数据 | ✅ samples.jsonl | 引用 |
| 阈值参数 | ✅ config.py | 可覆盖 |
| Binance/Helius API 调用 | ❌ | ✅ |
| API key / 密钥 | ❌ 永不进 | ✅ .env |
| LLM 调用 | ❌ | ✅ |
| 实盘下单 | ❌ | ✅ |
| Discord / Telegram bot | ❌ | ✅ |
| 持仓状态 / PnL 数据 | ❌ | ✅ |

**判断标准**：公仓的代码必须能在**没有任何外部连接**的情况下用 `pytest` 跑通。任何需要 API key / 网络 / LLM 的代码 → 私仓。

## 公仓当前已覆盖

| 模块 | 测试数 |
|---|---|
| L2 特征 (chip, oi, divergence, pool_router) | 53 |
| L3 安全闸 (long/short) | 26 |
| 入场信号 (trend_follow, support_collapse, short_vacuum) | 27 |
| 工具 (indicators, sizing) | 27 |
| L5 策略 (4 套 plan_*) | 19 |
| L7 组合层 (regime + admission) | 16 |
| 历史样本库 (22 case) | 14 |
| **合计** | **182** |

## 私仓待实现（spec 见对应 doc）

- L4 AI 评分层 → [`27-l4-ai-scoring-spec.md`](./27-l4-ai-scoring-spec.md)
- L6 OTOCO 执行 + 风控看门狗 → [`28-l6-otoco-execution-spec.md`](./28-l6-otoco-execution-spec.md)
- L8 walk-forward 复盘 + AI 周报 → [`29-l8-review-walkforward-spec.md`](./29-l8-review-walkforward-spec.md)

## 命名约定

不造比喻词，直接说人话。例如：

- ✅ 支撑崩塌 / 空头真空 / 聪明钱撤退
- ❌ 断脊 / 真空 / 退潮（需要二次翻译，不要）

新增策略/信号都按这个标准命名。
