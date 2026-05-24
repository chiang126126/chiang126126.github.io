# V4 Baseline 数据 (2026-05-24)

251 symbol × 6 个月 (2025-11-25 ~ 2026-05-24) Binance perp.

## 内容
- `v4_klines/`  — 15m / 1h / 4h / 1d K 线, 251 × 4 = 1004 parquet 文件 (~354MB)
- `v4_funding/` — funding rate 8h 粒度, 完整 6 月 (540 行/symbol, ~6MB)
- `v4_oi/`      — open interest 4h 粒度, **仅最近 30 日** (180 行/symbol, ~1.9MB)
- `v4_taker/`   — taker buy ratio 4h, **仅最近 30 日** (180 行/symbol, ~2.9MB)

## Binance API 硬限制
OI 和 Taker buy ratio 只有最近 30 日数据 (`/futures/data/*` 端点限制).
V4 回测中:
  - 最近 30 日内的 trade 能用 OI/Taker conviction features
  - 更早的 trade 这 2 个 feature 缺失 (conviction 评分降低 2 分, 还有 7 个 feature)

## 251 symbol 组成
- V3 paper engine 接触过的 237 symbol
- 10 mainstream (BTC ETH SOL BNB XRP ADA DOGE AVAX LINK DOT)
- 5 V3 黑名单 (DODOXUSDT NMRUSDT PLAYUSDT GUAUSDT STABLEUSDT)
- 去重后 251 unique
