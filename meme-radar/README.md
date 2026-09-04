# meme-radar · Robinhood Chain 早期 MEME 资金雷达

> 一个人的投资团队：AI 做 80% 的重复研究（信息收集、链上取证、初筛、记账、复盘），人只做判断、资金配置和风控。
> 它**不自动下单**。它的产出是：候选名单、可解释的证据、决策记录，以及最终能回答"这套筛选是否真的优于随机"的统计。

看板：`https://chiang126126.github.io/meme-radar/`（GitHub Pages，读 `data/*.json`，每 2 小时由 Actions 刷新）

⚠️ 研究与验证工具。所有仓位都是模拟；小市值/MEME 代币大多归零，任何真实资金都只应是能承受全损的实验资金。不构成投资建议。

---

## 0. 这套系统在解决什么问题

Robinhood Chain（2026-07-01 主网，Arbitrum Orbit 架构的以太坊 L2，Chain ID 4663）上的 MEME 发行平台 Pons 每天创建上万个代币，人不可能逐个研究。
同时，链上市场有一个传统金融没有的优势：**资金路径本身是公开的**——K 线是最晚出现的信息，钱包的创建时间、资金来源、彼此转账，比价格更早说明"这到底是不是一个真实的市场"。

所以系统的目标不是"猜中百倍币"，而是：

1. 先判断市场环境允许冒多大风险（BTC 是水库，BTC 稳了资金才会向 ETH → 大市值 → 小市值/MEME 扩散）；
2. 在每天几千个新币里，先剔掉七八十个明显有问题的；
3. 对剩下的做钱包级取证与交叉验证，找出少数"真实市场 + 资金正在进入但价格还没启动"的非对称机会；
4. 用极小的模拟仓 + 随机基线对照，跑满 50–100 个样本，再决定要不要投入真实小额资金。

## 1. 五层架构

```
GeckoTerminal 新池/趋势池 ─┐
DexScreener 补充 ──────────┤
Blockscout 持有人/钱包历史 ─┼─► L2 初筛 ─► L3 取证 + 聪明钱 ─► L4 交叉验证 ─► 评分/决策 ─► L5 账本
Robinhood Chain RPC ───────┤            ▲                                     │
OKX/Coinbase/CoinGecko/F&G ┴─► L1 体制 ──┘（风险预算、每日新仓上限）            └─► 1h/6h/24h/7d 回填 → 与随机基线对照
```

| 层 | 文件 | 做什么 | 关键输出 |
|---|---|---|---|
| L1 市场环境 | `radar/regime.py` | BTC 日线 EMA20/50/200 结构、7d/30d、BTC 占比及其 7 日变化、前 100 币跑赢 BTC 的比例（山寨季代理）、ETH/BTC、恐惧贪婪、本链头部池成交 | `regime ∈ {RISK_OFF, BTC_ONLY, ROTATION, ALT_SEASON}` + `risk_budget 0~1` + 每日新仓上限 |
| L2 新币初筛 | `radar/screen.py` | 硬过滤（流动性、年龄 20min–72h、成交笔数、独立买家、量/池比防洗量与死池、税率/蜜罐、前十集中度、创建者持仓、sybil 分）+ 七维评分 | `killed_by[]`、`score 0~100`、`score_breakdown` |
| L3 钱包取证 | `radar/forensics.py` | 前排持有人 → 剔除池子/曲线/锁仓/销毁 → 每个 EOA 查最早交易（年龄 + 首个打款方）与交易计数 → 并查集聚类（同一打款方 / 打款方是另一持有人 / 互相转账 / 同批创建的新钱包）→ 早期买家仍持仓比例 | `sybil_score`、`clusters[]`、`fresh_wallet_pct`、`holder_map`（看板星图） |
| L3 聪明钱 | `radar/smartmoney.py` | 不机械跟单一大户：赢家代币的最早买家 +1 win、归零代币的最早买家 +1 loss，score=(w+1)/(w+l+2)；可手动导入 GMGN/FOMO 整理的地址；看多少个聪明钱同时在买 | `smart_count`、`weighted`、`net_buy_usd` |
| L4 交叉验证 | `radar/crossval.py` | 价涨无钱、买盘集中、流动性抽走、关联簇控盘（红）；卖压主导、追高、洗量、付费推广、新钱包多、创建者持仓大、狙击手未走（黄）；吸筹未启动、广泛参与、买卖健康、持有人独立、毕业锁池（绿） | `flags{red,yellow,green}` |
| AI 审查 | `radar/ai.py` | 只回答"是否真实市场"，输出 `REAL_MARKET / MIXED / SUSPICIOUS / MANIPULATED` + 置信 + 证据 + 什么会改变判断。**只有否决权**（MANIPULATED 且置信 ≥ 0.7 → 不开仓），没有发起权。无 key 时用确定性规则版 | `ai.verdict` |
| L5 账本与验证 | `radar/ledger.py`、`radar/evaluate.py` | 每个候选（含被剔除的和随机基线）记录发现时全部特征 + 决策；回填 1h/6h/24h/7d 收益、最大涨幅/跌幅、流动性变化；模拟仓：翻倍收回一半本金、4 倍再收 1/4、剩余 40% 回撤止盈、−50% 止损、72h 时间止损、抽池即出；筛选组 vs 随机基线的命中率差 + bootstrap 置信区间；按特征分桶看哪些指标真的有效 | `ledger.jsonl`、`positions.json`、`evaluation.json` |

决策规则（`config/rules.json` → `decision`）：
- 被硬过滤 → **SKIP**
- 评分 ≥ 60 → **WATCH**
- 评分 ≥ 72 且无红旗、体制预算 > 0、今日新仓未满、AI 未否决 → **PAPER_BUY**，仓位 = 实验账户 × 2% × risk_budget（现在 BTC_ONLY 体制下 $500 × 2% × 0.35 = $3.5）

## 2. 运行方式

**云端（默认）**：`.github/workflows/meme-radar.yml` 每 2 小时跑 `python meme-radar/run.py cycle`，有变化才提交 `meme-radar/data/`。也可以在 Actions 页面手动 *Run workflow*（可选 `cmd`、`max_forensics`）。

**本地**：
```bash
cd meme-radar
cp .env.example .env && export $(grep -v '^#' .env | xargs)   # 全部可选
python run.py cycle --verbose            # 全流程
python run.py scan --max-forensics 10    # 只扫一轮
python run.py outcomes                   # 只回填结果、管理模拟仓
python run.py evidence 0x<token>         # 打印某代币的证据文档（贴给任意 LLM）
python run.py import-wallets data/smart_wallets.manual.json   # 导入手工整理的聪明钱
python run.py selftest                   # 离线合成链上跑全流程 + 单测（不需要网络）
```
运行时零第三方依赖（Python 3.11 stdlib）。看板本地预览：仓库根目录 `python3 -m http.server 8000` → `http://localhost:8000/meme-radar/`。

**可选密钥（GitHub Secrets / Variables）**——缺失只降级功能，不会报错：

| 名称 | 作用 | 不配会怎样 |
|---|---|---|
| `BLOCKSCOUT_API_KEY` | Blockscout PRO 免费档（5 rps / 10 万次每天，dev.blockscout.com 申请） | 走公共实例，取证层限流更严，`forensics_quality` 更多 partial |
| `COINGECKO_API_KEY` | CoinGecko demo key | 公共限流，偶尔缺 dominance/breadth |
| `LLM_API_KEY` + Variables `MEME_RADAR_LLM_PROVIDER`（deepseek/openai/anthropic）、`MEME_RADAR_LLM_MODEL` | AI 审查 | 用规则版审查，同样有否决权 |
| `GMGN_API_KEY` + `GMGN_BASE_URL` | 自动拉聪明钱 | 只用自动发现 + 手动导入 |

## 3. 验证协议（什么时候才算"有用"）

1. 连续跑，直到 **筛选组（WATCH+PAPER_BUY）与随机基线各 ≥ 50 个样本**且都回填了 24h 结果；
2. 看板 L5 的结论：
   - **有优势**：命中率差的 95% 置信区间下界 > 0 → 可以考虑投入真实小额（仍按同样的仓位规则）；
   - **无优势 / 尚不清楚**：改规则（bump `rules.version`，评估层按版本分组，不会把新旧规则的样本混在一起），继续跑；
3. 同时看"哪些指标真的有效"分桶表：如果 `sybil_score < 0.2` 的桶命中率没有更高，说明取证信号在这条链上没用，要换特征；
4. 模拟组合的盈亏因子与最大回撤是第二道门槛：命中率高但被归零拖垮，仓位规则就要改。

被剔除的样本（SKIP）也回填结果——用来检验有没有**错杀赢家**。

## 4. 目录

```
meme-radar/
├── index.html · assets/          看板（纯静态）
├── run.py                        入口
├── config/rules.json             所有阈值与权重（改动请 bump version）
├── config/chains/robinhood.json  链参数：4663、RPC、Blockscout、GeckoTerminal/DexScreener slug、Pons 合约
├── radar/                        五层代码 + sources/ 数据源适配器
├── data/                         Actions 生成：summary.json（看板主数据）、candidates.json、watchlist.json、regime.json、
│                                 ledger.jsonl（样本账本）、positions.json（模拟仓）、evaluation.json、smart_wallets.json、
│                                 wallet_cache.json（钱包画像缓存）、reports/YYYY-MM-DD.md（日报）
└── tests/                        离线合成链（fakechain.py）+ 单测
```

## 5. 已知边界与下一步

- **Pons 曲线阶段**（未毕业到 Uniswap V4 的代币）目前只在 GeckoTerminal 收录时可见；直接解析 Pons 工厂事件（`TokenLaunched / PoolGraduated`）的 RPC 适配器已留接口（`sources/evm_rpc.py`、`sources/pons.py`），需要补 ABI。
- **GoPlus** 对 4663 的支持未确认：不支持时用链上兜底（Pons 模板代币视为无税无 owner；非 Pons 代币查 `owner()` 与验证状态）。
- **X/Twitter 讨论**没有免费接口，叙事/社交维度只用 DexScreener/GeckoTerminal 的社交链接与付费推广标记（付费推广是减分项）。
- 聪明钱库从零开始积累，前几周 `smart_money` 维度按中性处理（库 < 10 个钱包不参与打分）。
- 取证对 Blockscout 调用量约 55 次/代币，默认每轮深挖 ≤ 25–30 个；`wallet_cache.json` 跨运行复用钱包画像。
- 时间点风险：小币一分钟和十分钟差别很大，2 小时一轮的节奏适合"资金开始迁移但价格未启动"的观察名单，不适合狙击。真要更快，需要常驻进程（不适合 GitHub Pages/Actions）。

参考：Robinhood Chain 官方连接文档（Chain ID 4663，RPC `rpc.mainnet.chain.robinhood.com`，浏览器 `robinhoodchain.blockscout.com`）；Pons V2（2026-08-04，ETH 定价曲线，毕业进 Uniswap V4 永久锁定池，V2 工厂 `0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e`）；Blockscout PRO API（`api.blockscout.com/4663/api/v2`）；GeckoTerminal 网络 slug `robinhood`；DexScreener 链 slug `robinhood`；GMGN 官方 CLI/OpenAPI 支持 `robinhood`。
