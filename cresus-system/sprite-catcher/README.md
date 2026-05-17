# 精灵捕手 · Sprite Catcher

Crésus 体系下专门做"妖币"的特征工程层（L2）。

> 本目录只包含**纯计算 + 纯接口**的参考实现，不含任何 API 密钥、不发起任何交易。
> 真正接 Binance / Helius / Bitquery 的数据源在私仓 `cresus-bot` 里实现 Protocol 即可。

## 模块定位

```
L1 数据采集 (CEX × 多家 + 链上)            ← 私仓 cresus-bot
        │
        ▼
L2 特征工程   ← 本目录 ★
   • 筹码集中度 + Funder 去重         features/chip.py
   • OI 分层 (主力 / Follow)          features/oi.py
   • 三层背离 (价 / OI / 持有人)       features/divergence.py
   • 候选池分流                       features/pool_router.py
        │
        ▼
L3 安全闸 + L4 AI 评分 + L5 策略层 …
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
| `divergence.py` | 价/OI/持有人 三层背离（"聪明钱撤退"信号）|
| `pool_router.py` | 候选池分流（友好池 / 操纵池 / 中性 / 拉黑）|

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
cd cresus-system/sprite-catcher
pip install -e ".[dev]"
pytest -v
```

零外部数据源依赖；测试用 `tests/conftest.py` 里的 Fake 提供者跑全套用例。

## 自检

每个函数的边界条件、出错路径、关键不变式都在 `tests/` 里有对应测试。
详见 [`AUDIT.md`](./AUDIT.md)。
