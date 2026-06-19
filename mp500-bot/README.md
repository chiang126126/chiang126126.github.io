# MP500 Paper 机器人

每小时(GitHub Actions)自动:取数据 → **DeepSeek/ChatGPT 分析出决策** → **确定性风控引擎一票否决** → 执行(模拟撮合 / 币安模拟盘) → 把交易与权益写进 `million-path/data/`,看板自动展示。

> ⚠️ 用于 **S0 验证**,非盈利保证。论文显示多数 LLM 回测是亏的。风险设到最小、风控有否决权、可随时 kill。绝非投资建议。

## 三种模式(由安全到真实,逐步上)
| MODE | 行为 | 需要的 key |
|---|---|---|
| `dry` | 只决策、只记录,**不开任何仓** | LLM key(可选) |
| `sim`(默认) | 用**真实价格模拟撮合**开/平仓,算 PnL | LLM key(可选) |
| `testnet` | 在**币安现货模拟盘**真下单 | LLM key + 币安 Testnet key |

**建议路径**:先 `dry` 看几次决策合不合理 → `sim` 跑 1–2 周看期望 → 再上 `testnet`。

## 决策大脑
- 配了 `LLM_API_KEY` → 用 LLM(DeepSeek 推荐,极便宜,每月几美分)。
- 没配 → 自动用**确定性规则兜底**(30日线+RSI+资金费率/情绪),完全免费。
- 无论哪种,**`risk.py` 风控引擎都会二次否决**:单笔风险≤1%、必须有止损、盈亏比≥1.5、成本闸门、持仓上限、日亏/总回撤熔断。

## 一次性配置(GitHub Secrets)
在大仓库 `chiang126126.github.io` → Settings → Secrets and variables → Actions:
- `SYNC_TOKEN` — 已有(同步用,Contents:write on million-path)。
- `LLM_API_KEY` — DeepSeek key(https://platform.deepseek.com)或 OpenAI key。**留空则用规则,不报错。**
- (可选,testnet 才需)`BINANCE_TESTNET_KEY` / `BINANCE_TESTNET_SECRET` — 从 https://testnet.binance.vision 用 GitHub 登录后 Generate HMAC Key。
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

## 边界(诚实)
- 每小时决策一次,**没有秒级插针防护**(那需常驻进程/VPS,属 S2 后期)。止损用 K 线高低价检测,保证不漏。
- Actions 定时**可能延迟数分钟或偶尔跳过**,对小时级策略无碍。
- Testnet 是假钱,撮合/滑点与真实盘有差异,仅作流程与策略验证。
