# 精灵捕手 · 落地操作手册

> 这份是给你（项目主）的"具体下一步该做什么"的步骤清单。
> 每一段命令都可以直接复制运行。每个阶段做完前**不要**进入下一阶段。
>
> 风险提示已在 [`sprite-catcher/AUDIT_FINDINGS.md`](../sprite-catcher/AUDIT_FINDINGS.md) 列清。
> **任何一条 SEVERE 没解决前，不上实盘**。

---

## 阶段 0 · 验证公仓代码（5 分钟）

确认 sprite-catcher 在你本地能跑通。

```bash
cd ~/path/to/chiang126126.github.io/cresus-system/sprite-catcher

# 装最小依赖
python3 -m pip install --user pytest

# 全套测试
python3 -m pytest -v

# 预期最后一行：
# ============================= 207 passed in 0.XXs ==============================
```

如果有失败，**立即停下**，把错误贴回来一起 debug。不要带着失败的测试往下走。

---

## 阶段 1 · 完善样本库（1–2 天，最关键的一件事）

`samples.jsonl` 里 22 个样本的价格是研究估算，**直接用它跑回测会校准出错误的阈值**。
必须用真实数据替换。

### 1.1 准备数据脚本（不入公仓，本地用）

新建文件 `~/sprite-catcher-tools/refine_samples.py`：

```python
"""
从 CoinGecko 拉取每个样本时间窗内的真实 OHLCV，校准 base_low / peak_high。
免费 API 限频 30 req/min，22 个样本 ≈ 1 分钟跑完。
"""
import json
import time
import urllib.request
from pathlib import Path

SAMPLES_PATH = Path("~/path/to/sprite-catcher/sprite_catcher/datasets/samples.jsonl").expanduser()
COINGECKO_IDS = {
    "DOGE": "dogecoin", "SHIB": "shiba-inu", "ORDI": "ordinals",
    "WIF": "dogwifcoin", "PEPE": "pepe", "POPCAT": "popcat",
    "BOME": "book-of-meme", "BONK": "bonk", "VIRTUAL": "virtual-protocol",
    "AI16Z": "ai16z", "GOAT": "goatseus-maximus",
    "MYX": "myx-finance", "COAI": "chainopera-ai",
    # AIA/ZKJ/KOGE/RAVE/LAB 可能要查 CMC 或 Bitget API，
    # 私仓里维护一份完整 mapping
}

def fetch_range(coin_id, from_ts, to_ts):
    url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
           f"?vs_currency=usd&from={from_ts}&to={to_ts}")
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())

# 逐行读取 -> 拉数据 -> 校准 base_low/peak_high -> 写回
# (具体实现略；不要把这个脚本提交到 git，含 API mapping 是私仓的事)
```

**不要把这个脚本和真实数据一起提进公仓**——会让 samples.jsonl 看起来权威但其实只是某个时刻的快照。让它留在你本地。

### 1.2 补齐缺失字段

按 [`AUDIT_FINDINGS.md`](../sprite-catcher/AUDIT_FINDINGS.md) M1 提到的：

- `top10_share_at_peak`：从链上扫（Solana 用 Helius `getTokenLargestAccounts`，EVM 用 Etherscan 的 holders API）
- `binance_oi_share_at_peak`：从 CoinGlass 历史 API 拿（要付费账户，免费 tier 数据有限）
- `vol_oi_ratio_at_peak`：自己算 = `24h_vol / open_interest`

补齐之后，回到测试：

```bash
cd ~/path/to/sprite-catcher
python3 -m pytest tests/test_datasets.py -v
# 应该仍然 14 passed
```

---

## 阶段 2 · 建私仓 cresus-bot（半天）

公仓 sprite-catcher 是纯计算 + spec。所有涉及 API key / LLM 调用 / 实盘下单的代码都进私仓。

### 2.1 建仓 + 装 sprite-catcher

```bash
# 你的 GitHub 上建 private repo: cresus-bot
gh repo create chiang126126/cresus-bot --private --clone
cd cresus-bot

# 基础结构
mkdir -p {src/sprite_bot,tests,config,data}
touch README.md .gitignore

cat > .gitignore <<'EOF'
.env
.env.*
*.pyc
__pycache__/
.pytest_cache/
.venv/
data/snapshots/
data/secrets/
EOF

# venv + 安装 sprite-catcher (editable, 这样改公仓代码立即生效)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ../chiang126126.github.io/cresus-system/sprite-catcher
pip install httpx websockets python-binance solders helius-sdk anthropic pydantic
```

### 2.2 .env 模板

```bash
cat > .env.example <<'EOF'
# Binance — 现货下单 only，关 withdrawal，IP 白名单必开
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Binance Futures — Module B 用，可单独一个 key
BINANCE_FUTURES_API_KEY=
BINANCE_FUTURES_API_SECRET=

# 链上数据
HELIUS_API_KEY=
BITQUERY_API_KEY=
COINGLASS_API_KEY=

# LLM
ANTHROPIC_API_KEY=

# Telegram (/halt 用)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 配置
TRADING_MODE=paper        # paper | live
MAX_DAILY_DRAWDOWN=0.05
EOF
cp .env.example .env
# 然后手动填进去
```

⚠️ **Binance API key 权限设置**：
- ✅ Enable Spot Trading
- ✅ Enable Futures（仅当上线 Module B）
- ❌ **Enable Withdrawals**（永远关）
- ❌ Enable Internal Transfer
- ❌ Enable Margin（除非明确需要）
- IP 白名单：只列你的 VPS 出口 IP

---

## 阶段 3 · 实现 4 个 Protocol（3–5 天）

按 sprite-catcher 的 `interfaces.py`，私仓实现这 4 个 Protocol：

```
src/sprite_bot/
├── providers/
│   ├── binance_holder_provider.py       # HolderProvider (现货可能不支持，留接口)
│   ├── helius_holder_provider.py        # HolderProvider for Solana
│   ├── helius_transfer_provider.py      # TransferProvider
│   ├── cex_wallet_registry.py            # CEXWalletRegistry (硬编码常见 CEX 热钱包)
│   ├── binance_oi_provider.py            # OIProvider
│   ├── token_audit_provider.py           # TokenAuditProvider (GoPlus API)
│   ├── liquidity_provider.py             # LiquidityProvider (DexScreener)
│   ├── dev_wallet_provider.py            # DevWalletProvider (本地 rug 黑名单)
│   └── trade_simulator.py                # TradeSimulator (Solana simulateTransaction)
```

每一个写**单元测试用 mock 数据**，集成测试**用真实 API key**但跑只读端点。

### 3.1 优先级

1. **第一周**只做 Solana 链 + Binance 现货（友好型妖币最集中的两个数据源）
2. 第二周加 EVM (BSC + ETH)
3. 第三周加 OI/Coinglass（Module B 才需要）

不要一开始就全栈，会出 N 个集成 bug。

### 3.2 烟雾测试样本

第一个集成测试：用真实 Helius 拉 `ORDI` 的 holders，调用公仓的 `compute_chip_features`，验证 top10_share 与你阶段 1 补齐的 samples.jsonl 一致（±5%）。

跑通了 = providers 接对了；跑不通 = 接错了或公仓有 bug。

---

## 阶段 4 · 跑历史回放（5–7 天）

私仓建一个 `scripts/backtest.py`，逐个样本回放：

```python
# scripts/backtest.py（伪代码）
from sprite_catcher import (
    compute_chip_features, stratify_oi, route_to_pool,
    evaluate_long_safety, detect_trend_follow,
    plan_trend_follow, can_admit_intent,
    load_samples, SampleLabel,
)

samples = load_samples()
for sample in samples:
    # 在 sample.rally_start_date 到 sample.peak_date 之间逐日推进
    for ts in daily_range(sample.rally_start_date, sample.peak_date):
        # 1. 拉 T 时刻的链上 + CEX 数据（从你本地存档拉）
        snapshot = load_snapshot(sample.token_symbol, ts)
        
        # 2. 走完整管道
        chip = compute_chip_features(...)
        oi = stratify_oi(...)
        decision = route_to_pool(chip, oi, daily_pump_pct=snapshot.daily_pump)
        
        if decision.pool == Pool.FRIENDLY:
            safety = evaluate_long_safety(...)
            signal = detect_trend_follow(...)
            intent = plan_trend_follow(...)
            if intent:
                admission = can_admit_intent(
                    intent, caps,
                    equity_usd=portfolio.equity,
                    module_a_value_usd=portfolio.module_a,
                    module_b_value_usd=portfolio.module_b,
                    existing_exposure_by_symbol=portfolio.exposure_by_symbol,
                    max_per_symbol_pct=0.03,      # ⚠️ 必传
                )
                if admission.admitted:
                    portfolio.open(intent)
        
        portfolio.tick(ts)  # 更新持仓盈亏、触发 SL/TP
    
    # 每个 sample 跑完打分
    print(sample.token_symbol, portfolio.pnl_for(sample))
```

**重点**：你需要"T 时刻可见的链上快照"。如果你只有现在的快照，就只能跑近期样本——这就是 survivorship bias 的根源。

**最低标准**：拿真实 OHLCV 跑出每个友好样本的 PnL 模拟，确认胜率/盈亏比与阶段 5 的门槛对得上。

---

## 阶段 5 · Paper Trade（30 天）

私仓加一个 `paper_broker.py`：消费 TradeIntent，按当前盘口模拟成交（含真实滑点 + 真实手续费 0.1%）。

**升级到实盘的硬门槛**（见 [`docs/29-l8-review-walkforward-spec.md`](./29-l8-review-walkforward-spec.md)）：

- [ ] profit_factor ≥ 1.5
- [ ] win_rate ≥ 45%
- [ ] avg_win_loss_ratio ≥ 2.0
- [ ] max_drawdown_pct < 20%
- [ ] max_consecutive_losses ≤ 5
- [ ] sample_size ≥ 80 笔
- [ ] signal T+72h 平均回报为正且 p_value < 0.1
- [ ] OOS 后 1/3 段单独看也满足以上

**任一不达标 → 回到阶段 4 调阈值或修策略，不实盘。**

---

## 阶段 6 · 小额实盘（30 天，1/10 仓位）

```python
# config/live.yaml
trading_mode: live
position_size_multiplier: 0.1   # 设计仓位 × 1/10
max_open_positions: 5
modules_enabled:
  - module_a_trend_follow
  - module_a_breakout       # 后续加
  # Module B 暂不开，等熟悉一遍多头
```

第 1 周不要碰、不要改参数。只看：
- Paper 与 Live 的 PnL 偏离应 < 30%
- Telegram /halt 测试：故意触发一次，验证 < 5s 全平
- 看门狗触发记录（应该零事件，第一周不应有任何熔断）

第 2–4 周可以观察哪些信号实盘表现更好，但**不改阈值**。等阶段 7 复盘再调。

---

## 阶段 7 · 全仓 + Module B 上线（持续）

**前提**：阶段 6 跑满 30 天，OOS 表现没有显著恶化。

- 仓位 ratio 从 1/10 → 1/3（第 31–60 天）→ 1（第 61 天+）
- Module B 上线：先只用 `support_collapse`（最慢最稳）跑 30 天，再加 vacuum/distribution
- 每周复盘 + AI 周报（见 [`docs/29-l8-review-walkforward-spec.md`](./29-l8-review-walkforward-spec.md)）

---

## 时间预算（单人全职估算）

| 阶段 | 周期 | 关键交付 |
|---|---|---|
| 0 验证 | 5 min | `pytest` 207 passed |
| 1 完善样本 | 1–2 天 | samples.jsonl 真实数据 |
| 2 建私仓 | 半天 | cresus-bot + .env + pip install -e |
| 3 实现 Protocol | 3–5 天 | 4 个 provider + 烟雾测试 |
| 4 历史回放 | 5–7 天 | backtest.py + 22 样本回放报告 |
| 5 Paper Trade | 30 天 | 升级门槛达标 |
| 6 小额实盘 1/10 | 30 天 | OOS 偏离 < 30% |
| 7 逐步放大 + Module B | 60–90 天 | 全仓 + 4 套策略 |

**总投入**：从 0 到全仓运行 = **3–4 个月**。

---

## 每周必做（实盘后）

- 一 / 三 / 五：跑 `walk_forward_report.py`（私仓写）
- 二 / 四：人工 review 上周 AI 周报建议
- 周末：手动把 Binance 利润超额部分提到硬件钱包

**永远不要**：
- 跳过任何阶段
- 实盘期间调阈值不做 review
- API key 勾上 withdraw
- 加杠杆超过 spec 写的（Module A 0x；Module B 上限 2x）
- 把 Simple Earn 当冷库
- 信任 AI 输出的金额/方向（永远走确定性代码）
