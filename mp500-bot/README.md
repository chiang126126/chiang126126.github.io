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
- 无论哪种,**`risk.py` 风控引擎都会二次否决**:单笔风险≤1%、必须有止损、盈亏比≥1.5、成本闸门、持仓上限、日亏/总回撤熔断。

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
- 每小时决策一次,**没有秒级插针防护**(那需常驻进程/VPS,属 S2 后期)。止损用 K 线高低价检测,保证不漏。
- Actions 定时**可能延迟数分钟或偶尔跳过**,对小时级策略无碍。
- Testnet 是假钱,撮合/滑点与真实盘有差异,仅作流程与策略验证。
