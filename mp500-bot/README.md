# MP500 Paper 机器人

每小时(GitHub Actions)自动:取数据 → **DeepSeek/ChatGPT 分析出决策** → **确定性风控引擎一票否决** → 执行(模拟撮合 / 币安模拟盘) → 把交易与权益写进 `million-path/data/`,看板自动展示。

> ⚠️ 用于 **S0 验证**,非盈利保证。论文显示多数 LLM 回测是亏的。风险设到最小、风控有否决权、可随时 kill。绝非投资建议。

## 三种模式(由安全到真实,逐步上)
| MODE | 行为 | 需要的 key |
|---|---|---|
| `dry` | 只决策、只记录,**不开任何仓** | LLM key(可选) |
| `sim`(默认) | 用**真实价格模拟撮合**开/平仓,算 PnL | LLM key(可选) |
| `testnet` | 在**币安 USDT 合约模拟盘**真下单（1x，可多可空）| LLM key + 币安合约 Testnet key |

**建议路径**:先 `dry` 看几次决策合不合理 → `sim` 跑 1–2 周看期望 → 再上 `testnet`。

> 现阶段用 **USDT 本位合约 1 倍杠杆**：可做多(LONG)/做空(SHORT)，震荡行情双向取样、不浪费时间；1x 不放大风险，单笔风险仍由止损距离 ≤1% 控制。后续要上更高杠杆，只需改 `.env` 的 `LEVERAGE`（但请先在 paper 充分验证）。

## 信息面（喂给 LLM 做小时级判断）
每轮决策前，机器人会抓**新闻信息面**并并入 Evidence 一起喂给 LLM（借鉴 WebCryptoAgent 的 web informatics）：
- **加密新闻**：CryptoCompare 头条（无需 key）。
- **宏观 / AI 基建 / 地缘**（可选，配 `MARKETAUX_KEY`）：覆盖美联储、NVDA/MRVL 等 AI 算力叙事、地缘冲突——正是"黄仁勋发言带飞 MRVL"这类跨市场信号。
系统提示词已要求 LLM 结合新闻/叙事判断，但叙事行情来去快，仍须与技术面 + 风控共同确认。

## 决策大脑
- 配了 `LLM_API_KEY` → 用 LLM(DeepSeek 推荐,极便宜,每月几美分)。
- 没配 → 自动用**确定性规则兜底**(30日线+RSI+资金费率/情绪),完全免费。
- 无论哪种,**`risk.py` 风控引擎都会二次否决**:单笔风险≤1%、必须有止损、盈亏比≥1.5、成本闸门、持仓上限、**全组合名义≤95%本金**、日线趋势闸门、**RSI极值闸门**(超卖不追空/超买不追多)、日亏/总回撤熔断。

## 实盘(MODE=live)切换清单 —— 代码已就绪、默认休眠
**先决条件(2026-07-08 定,不达标不切):** 新出场规则在 testnet 跑约 2 周 / ≥15 笔新平仓,且
**PF > 1.2**、"曾浮盈≥1%回吐触损"类亏损占比明显下降、无风控违规。由第二轮复盘确认。

达标后的切换步骤:
1. 币安主账户:把首期资金(建议 **100–150U**)划入 **U 本位合约钱包**(其余资金不放这里——余额超 `LIVE_MAX_EQUITY` 机器人会拒绝开新仓);合约设置确认为**单向持仓**(双向会被拒绝运行)。
2. 创建 API Key:**只勾『允许合约』、绝不勾『允许提现』**、设 IP 白名单(你家公网 IP)。
3. `.env`:填 `BINANCE_LIVE_KEY/SECRET`,`MODE=live`,按需调 `LIVE_MAX_NOTIONAL/LIVE_MAX_EQUITY`。
4. 手动跑一轮 `bash run_local.sh`,确认打印 `[live] 已连接主网,钱包余额 …` 且无 guard/fatal。
5. 看板切换到 `*_live` 数据文件(一行配置,到时由 Claude 改)。

内建护栏:live 缺 key 拒绝运行;双向持仓拒绝运行;**权益以真实钱包余额为准**(资金费率/滑点自然计入,熔断按真钱算);单笔名义硬顶;余额超限只平不开;live 数据独立存 `bot_*_live.json`,与 testnet 历史互不污染。

## 双层节奏(战略层 + 守护层)
- **战略层** `run_local.sh`(每小时:07)：DeepSeek 行动卡 → 风控闸门 → 开新仓 + K线级持仓管理；
- **守护层** `run_guardian.sh`(每3分钟)：只管已有持仓——实时价更新MFE/上移保本与跟踪止损、
  触及止损/止盈/超时**立即**市价平仓(把止损延迟从≤59分钟压到≤3分钟)。绝不开新仓、不调LLM、
  无持仓秒退。两者通过 `lock.sh` 的 mkdir 原子锁互斥(macOS 无 flock)。
- crontab 两条:
  `7 * * * * /bin/bash /Users/hong/mp500/mp500-bot/run_local.sh >> $HOME/mp500-bot.log 2>&1`
  `*/3 * * * * /bin/bash /Users/hong/mp500/mp500-bot/run_guardian.sh >> $HOME/mp500-guardian.log 2>&1`
- 注：守护层平仓后，下一个整点战略层可能重新开同标的仓（同小时冷却只在战略层内生效）。

## 模拟舱 sim.py（BTC/ETH 双册 · 纯模拟 · 2026-08 复盘后加入）
7~8月实测复盘发现：主循环在震荡市里仍按趋势打法进场，31笔亏61U、19笔裸止损平均MFE仅0.30%。
模拟舱是并行的对照策略，核心是**先判形态、再选打法**（全确定性、无LLM，信号稳定可复现）：
- **形态判定(日线)**：价距30日线≥±1% 且 30日线较5天前同向 → 趋势；否则震荡。
- **趋势打法**：只做顺势回踩——回踩30h线收复(RSI 45–70)进场，止损1×ATR(0.8~1.5%)，
  目标2.2R + 保本(1×止损距)/追踪(1.4×启动锁50%)/48h超时。
- **震荡打法**：只在边缘反手——摸昨高受阻(RSI≥58)做空 / 探昨低承接(RSI≤42)做多，
  止损0.7×ATR(0.4~1%)，目标1.8R，24h超时。区间中部一律不进场。
- **管制**（主循环没有、复盘证明最缺的部分）：连续2笔止损→冷静12h；每日每册最多进场2次；
  当日亏≥3%当日停机；距峰值回撤≥25%整册停机待人工检视；离场后至少隔一根K线才可再进场。
- **册**：BTC/ETH 各1000U独立核算，单笔风险1.5%，名义≤3×册权益(杠杆帽3x)，taker 0.05%/边。
- **口径**：入场=信号蜡烛收盘价；同一根K线双触先算止损(悲观)；回测不含资金费(已知略偏乐观)。
- 运行：`run_local.sh` 每小时自动带起(失败不影响主循环)；`python3 sim.py --backfill 95` 生成
  近95天规则回测(sim_backtest.json，看板展示)；`python3 sim.py --reset` 双册清零重来。
- 绝不下真实订单：不接任何交易所私有接口，只读公开K线。

## 出场管理(S0 首轮复盘后加入)
- **保本**:浮盈达 1.0% → 止损自动移到入场价(+手续费缓冲),最差≈0亏损;
- **跟踪止盈**:浮盈达 1.5% → 止损锁住最佳浮盈的 50%,随行情逐小时上移、只紧不松;
- 逐根K线按时间顺序管理(无"未来函数"),testnet 平仓失败自动重试;
- 开仓时定格**决策快照**(均线偏离/RSI/资金费率/情绪/日线状态)存入记录,供复盘分组统计。

## 一次性配置(GitHub Secrets)
在大仓库 `chiang126126.github.io` → Settings → Secrets and variables → Actions:
- `SYNC_TOKEN` — 已有(同步用,Contents:write on million-path)。
- `LLM_API_KEY` — DeepSeek key(https://platform.deepseek.com)或 OpenAI key。**留空则用规则,不报错。**
- (可选,testnet 才需)`BINANCE_TESTNET_KEY` / `BINANCE_TESTNET_SECRET` — ⚠️ 现为合约模式，须从 **https://testnet.binancefuture.com** 用 GitHub 登录后生成（与现货 testnet.binance.vision 的 key 不通用）。
- (可选)Variables 里设 `LLM_PROVIDER`(deepseek/openai)、`LLM_MODEL`。

默认 `MODE=sim`。想换模式:Actions → MP500 paper bot → **Run workflow** → 选 mode。

## 本地试跑
```bash
cd mp500-bot
pip install -r requirements.txt
cp .env.example .env   # 填 LLM_API_KEY（或留空用规则）
export $(grep -v '^#' .env | xargs)
mkdir -p data && MODE=dry python bot.py
```

## 文件
- `bot.py` 主循环(管理持仓→止损止盈→新入场)
- `strategy.py` Evidence + LLM 调用 + 规则兜底
- `risk.py` 确定性风控引擎(一票否决 + 仓位 + 熔断)
- `exchange.py` 公开行情 + 币安 Testnet 客户端
- `indicators.py` EMA/RSI/ATR/SMA
- 输出:`data/bot_state.json`(权益/持仓)、`data/bot_trades.json`(已平仓)、`data/bot_log.json`(每次决策)

## 本地运行(连币安 testnet 真下单,绕开 GitHub 地区封锁)

GitHub 美国服务器被币安封(451),但**你自己电脑能连币安**,所以在本地跑就能真下单。

**一次性准备:**
```bash
# 1) 只取 mp500-bot 代码（不下载 4.6GB 历史）
git clone --depth=1 --filter=blob:none --sparse https://github.com/chiang126126/chiang126126.github.io.git mp500
cd mp500 && git sparse-checkout set mp500-bot && cd mp500-bot

# 2) clone 独立仓库(给看板看的小仓库)
git clone https://github.com/chiang126126/million-path.git ~/mp-data

# 3) 依赖
pip3 install -r requirements.txt

# 4) 配置
cp .env.example .env
#   编辑 .env：MODE=testnet
#             LLM_API_KEY=你的DeepSeek key
#             BINANCE_TESTNET_KEY / BINANCE_TESTNET_SECRET
#             DATA_REPO=/Users/你的用户名/mp-data
```

**手动跑一次验证:**
```bash
bash run_local.sh
```
应打印 `[testnet] 已连接，USDT 可用余额 ...`，FLAT 则观望、LONG/SHORT 则真下单(合约 1x),然后推数据、看板更新。

**每小时自动(macOS / Linux,crontab):**
```bash
crontab -e
# 加一行（把路径换成你的真实路径）：
7 * * * * /bin/bash /Users/你的用户名/mp500/mp500-bot/run_local.sh >> ~/mp500-bot.log 2>&1
```
> ⚠️ 笔记本要**保持开机且不休眠**才会按时跑(cron 不会唤醒睡眠的电脑)。要真 24/7 就用一台常开的小主机/VPS。
> 时间需准(签名校验):若报 timestamp 错误,先校准系统时钟(NTP)。

## 边界(诚实)
- 决策每小时一次;持仓保护由守护层每 3 分钟执行——**仍没有秒级插针防护**(那需常驻进程/交易所条件单,属 S2 后期)。极端行情下实际亏损可能超过计划止损最多约 3 分钟的滑点。
- Actions 定时**可能延迟数分钟或偶尔跳过**,对小时级策略无碍。
- Testnet 是假钱,撮合/滑点与真实盘有差异,仅作流程与策略验证。
