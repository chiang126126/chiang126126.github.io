# 精灵捕手 · Sprite Catcher

专门做"妖币"的自动化交易体系——多源信号 + 双模块（多头现货 + 空头永续）+ 严格风控。

> 本仓库（公开）只放**纯计算 + 纯接口**的参考实现，不含任何 API 密钥、不发起任何交易。
> 真正接 Binance / Helius / Bitquery 的数据源在配套私仓 `sprite-bot` 实现 Protocol 即可。
>
> 与同 repo 下的 [`../cresus-system/`](../cresus-system/) **完全独立**：不互相 import、
> 不共享文件、不共享部署。两者可以独立运行、独立部署、独立托管资金。

## 模块定位

```
L1 数据采集 (CEX × 多家 + 链上)            ← 私仓 sprite-bot
        │
        ▼
L2 特征工程   ← 本目录 ★
   • 筹码集中度 + Funder 去重         features/chip.py
   • OI 分层 (主力 / Follow)          features/oi.py
   • 候选池分流                       features/pool_router.py
        │
        ▼
L3 安全闸   ← 本目录 ★
   • 多头审计 (LP锁/dev/年龄/top10)    features/safety_gate.py
   • 空头审计 (流动性/期货市场/蜜罐)    features/safety_gate.py
        │
        ▼
入场信号（Module A 多头）  ← 本目录 ★
   • 趋势跟随                          features/trend_follow.py
入场信号（Module B 空头）  ← 本目录 ★
   • 支撑崩塌                          features/support_collapse.py
   • 空头真空                          features/short_vacuum.py
   • 聪明钱撤退（三层背离）             features/divergence.py
        │
        ▼
仓位计算   ← 本目录 ★
   • 固定风险 + 单仓/总仓/杠杆三道上限   features/sizing.py
        │
        ▼
L5 策略层   ← 本目录 ★
   • plan_trend_follow / plan_support_collapse /  features/strategies.py
     plan_short_vacuum / plan_distribution
   • 输入: pool + safety + signal + 市场状态
   • 输出: TradeIntent (含 entry/SL/TP/sizing) 或 None
        │
        ▼
L4 AI 评分 + L6 执行 + L7 组合层 + L8 复盘 ← 私仓 sprite-bot
```

## 设计原则

1. **纯函数**：所有特征计算函数都是纯函数，同样输入永远同样输出。数据 I/O 通过 `interfaces.py` 中的 `Protocol` 注入。
2. **零外部依赖**：运行时不依赖 numpy/scipy/pandas，方便部署到任何 Python 3.10+ 环境。
3. **阈值集中**：所有"魔法数字"都汇集在 `config.py`，可被回测脚本批量扫描。
4. **不可变输出**：所有返回值是 `@dataclass(frozen=True)`，下游不会意外篡改。
5. **错误显式**：边界情况（如 `circulating <= 0`、单一交易所、NaN 相关性）通过 `ValueError` 或 `warnings` 字段显式表达，不静默吞掉。

## 命名约定

不造比喻词，直接说人话：

| 模块 | 它在抓什么 |
|---|---|
| `chip.py` | 筹码集中度 |
| `oi.py` | OI 分层 + 操纵分 |
| `divergence.py` | 价/OI/持有人 三层背离（聪明钱撤退） |
| `pool_router.py` | 候选池分流（友好池 / 操纵池 / 中性 / 拉黑）|
| `safety_gate.py` | 多头/空头 安全审计 |
| `trend_follow.py` | 趋势跟随入场（Module A 主力） |
| `support_collapse.py` | 支撑崩塌入场（Module B） |
| `short_vacuum.py` | 空头真空入场（Module B） |
| `sizing.py` | 仓位计算（固定风险 + 三道上限） |
| `indicators.py` | 通用技术指标（EMA / 连续上行根数） |
| `strategies.py` | 策略编排（plan_* 函数 → TradeIntent） |

## 快速使用

```python
from sprite_catcher import (
    compute_chip_features,
    stratify_oi,
    detect_distribution_divergence,
    route_to_pool,
)

# 1. 用你的数据源（Binance/Helius 等）实现 4 个 Protocol
holder_provider   = MyHolderProvider(...)
transfer_provider = MyTransferProvider(...)
cex_registry      = MyCEXRegistry(...)
oi_provider       = MyOIProvider(...)

# 2. 计算特征
chip = compute_chip_features("MYX", holder_provider, transfer_provider, cex_registry)
oi   = stratify_oi("MYXUSDT", oi_provider, lookback_hours=24)

# 3. 分流
decision = route_to_pool(chip, oi, daily_pump_pct=1.4)  # 1.4 = +140%
print(decision.pool, decision.reasons)
# Pool.OPERATOR, ('chip_concentrated', 'oi_manipulated')

# 4. 单独触发背离信号
price_series = oi_provider.get_price_series("MYXUSDT", hours=6)
oi_series    = oi_provider.get_oi_series("MYXUSDT", hours=6)
sig = detect_distribution_divergence(price_series, oi_series, holders_change_pct=0.01)
if sig.detected:
    print(f"{sig.reason} (strength={sig.strength})")
```

## 运行测试

```bash
cd sprite-catcher
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

零外部数据源依赖；测试用 `tests/conftest.py` 里的 Fake 提供者跑全套用例。

## 自检

每个函数的边界条件、出错路径、关键不变式都在 `tests/` 里有对应测试。
详见 [`AUDIT.md`](./AUDIT.md)。

## 文档索引

| 文件 | 内容 |
|---|---|
| [`docs/overview.md`](docs/overview.md) | 总览 + 公仓/私仓分工 |
| [`docs/playbook.md`](docs/playbook.md) | **落地操作手册**（7 阶段，每步可复制粘贴）|
| [`docs/l4-ai-scoring.md`](docs/l4-ai-scoring.md) | L4 AI 评分（JSON schema + prompt 模板）|
| [`docs/l6-otoco-execution.md`](docs/l6-otoco-execution.md) | L6 OTOCO 执行 + 看门狗 |
| [`docs/l8-walkforward.md`](docs/l8-walkforward.md) | L8 walk-forward + AI 周报 |
| [`AUDIT.md`](AUDIT.md) | 每个函数的正确性证明 |
| [`AUDIT_FINDINGS.md`](AUDIT_FINDINGS.md) | 未决项 / 已知风险 / 私仓必读清单 |

**新手第一步**：直接看 [`docs/playbook.md`](docs/playbook.md)。
