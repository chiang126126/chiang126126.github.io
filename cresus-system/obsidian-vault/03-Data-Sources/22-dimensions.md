---
type: data-source
title: 22 维数据清单
---

# 22 维数据清单

> 来源：用户原图。扫币 bot 每次对异常币会拉完整 22 维，组装成结构化 prompt 喂给 AI。

## 状态说明

- ✅ Binance/OKX/Coinglass API 可直接取
- ⚠️ 需要组合多个源 / 有数据质量问题
- ❌ 暂无现成数据源，需要自建

## 22 维完整列表

| # | 维度 | 状态 | 主要数据源 | 接口 / 说明 |
|---|---|---|---|---|
| 1 | 价格 + K 线 | ✅ | Binance `/api/v3/klines` | 日/4H/1H 多周期 |
| 2 | OI + 历史 | ✅ | Binance `/fapi/v1/openInterestHist` | 持仓量时间序列 |
| 3 | vol/OI | ✅ | 自己计算 | 24h 成交量 ÷ 当前 OI |
| 4 | 各所费率 | ✅ | Binance `/fapi/v1/premiumIndex`, OKX `/api/v5/public/funding-rate` | 合约资金费率 |
| 5 | 费率历史 | ✅ | Binance `/futures/data/fundingRate` | 8h 费率历史 |
| 6 | Basis（现货-合约价差） | ✅ | 自己计算 | 现货价 vs 合约价 |
| 7 | 散户/大户多空比 | ✅ | Binance `/futures/data/globalLongShortAccountRatio`, `/futures/data/topLongShortPositionRatio` | |
| 8 | Taker 比 | ✅ | Binance `/futures/data/takerlongshortRatio` | 主动买/卖单比 |
| 9 | Binance OI 占比 | ✅ | Binance OI ÷ 全网 OI（Coinglass） | 跨所市场占比 |
| 10 | 各所 OI 分布 | ✅ | Coinglass `/public/v2/open_interest` | 各交易所 OI 拆分 |
| 11 | 订单簿 | ✅ | Binance `/fapi/v1/depth`, OKX `/api/v5/market/books` | 大单堆积 |
| 12 | 爆仓 | ✅ | Coinglass 爆仓数据（**你订阅的最低档 API**） | 24h 爆仓总额 + 多空分布 |
| 13 | 市值 | ✅ | CoinGecko 免费 API | FDV / 流通市值 |
| 14 | OI/市值 | ✅ | 自己计算 | 衡量杠杆比率 |
| 15 | 类型/链 | ✅ | CoinGecko `coins/{id}` | Layer1/Layer2/meme/defi... |
| 16 | 恐惧贪婪 | ✅ | alternative.me Fear & Greed API（免费） | 全市场情绪 |
| 17 | BTC（大盘环境） | ✅ | Binance BTCUSDT | 价格、24h 变化 |
| 18 | 流动性 | ✅ | Binance 24h 成交量 + 订单簿深度 | 综合指标 |
| 19 | 交易历史 | ✅ | Binance `/api/v3/trades` | 最近成交 |
| 20 | Top10 筹码集中度 | ✅ | CoinGecko / 项目方官网 | 链上数据（可选 Etherscan） |
| 21 | 聪明钱 / KOL | ❌→⚠️ | 币安 Web3 + OKX OnchainOS | **P1 后期补** |
| 22 | 上线时间 | ⚠️ | Binance / OKX listing 公告抓取 | 区分新币（易拉盘）vs 老币 |

## 硬过滤规则（原图）

扫 100 币时，直接剔除的情况：

- vol/OI > 20x → **排除**（说明 OI 太小，波动不反映合约结构）

## 异常币筛选条件（原图）

满足 **任一** 就入选（5-10 个/次）：

- OI 24h 变化大（>20%）
- 费率异常（|funding| > 0.05%）
- 价格乖离（与 MA20 偏离 > 阈值）

## 实际用法

P2 阶段 bot 工作流：

```
扫 100 币 [维度 1, 2, 3, 9]  →  硬过滤 vol/OI > 20x
        ↓
筛出 5-10 异常币
        ↓
对每个异常币拉完整 22 维
        ↓
组装成 ~6K tokens 的 user prompt
        ↓
DeepSeek 判断
```

## 维度 21、22 的处理（原图标红/黄）

- **维度 21 (聪明钱)**：P0 阶段跳过，P1 后期通过 OKX OnchainOS / Nansen（贵）/ 自建钱包标签库 补
- **维度 22 (上线时间)**：P2 实现时，Binance/OKX 抓一次 listing 列表缓存本地，启动时加载
