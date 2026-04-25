# P5：OKX Agent TradeKit 集成（Demo 纸交易）

本文档覆盖 Crésus 信号系统第五阶段：将高信心信号通过 **OKX Agent TradeKit CLI** 自动在 OKX **演示账户**执行纸交易，实现完整的「信号 → 下单 → 持仓」闭环。

---

## 架构概览

```
main_loop.py
    ↓
scan_top_coins()          ← P2 数据层
    ↓ (34 维快照)
judge(snapshot)           ← P3 AI 层（DeepSeek）
    ↓ (decision dict)
route_decision()          ← P4 路由层 + Discord 通知
    ↓
execute_signal()          ← P5 OKX 纸交易执行层（本文档）
    ↓
okx swap place --demo     ← OKX Agent TradeKit CLI
    ↓
OKX Demo Account          ← 演示账户持仓 / P&L
```

---

## OKX Agent TradeKit 简介

- **官网**：[okx.com/zh-hans/agent-tradekit](https://www.okx.com/zh-hans/agent-tradekit)
- **GitHub**：[github.com/okx/agent-trade-kit](https://github.com/okx/agent-trade-kit)
- **特点**：MCP server + CLI，API key 存本地，凭证不经过云端
- **demo 模式**：`--demo` flag，所有命令映射到 OKX 演示账户，零风险测试

---

## 安装（Mac/VPS 通用）

### 前置条件

- Node.js 18+（`node --version` 确认）
- OKX 账号（免费注册，演示账户无需入金）

### 安装 CLI

```bash
npm install -g @okx_ai/okx-trade-cli
okx --version   # 验证安装
```

### 初始化配置（演示账户）

```bash
okx config init
```

交互式向导，填入以下内容（演示 API key 在 OKX 网页后台 → 「API 管理」→「演示账户」创建）：

```
API Key:    <your demo api key>
Secret Key: <your demo secret key>
Passphrase: <your passphrase>
```

配置保存到 `~/.okx/config.toml`，不上传任何云端。

### 验证连通性

```bash
# 行情（无需 API key）
okx market ticker BTC-USDT-SWAP --json

# 账户余额（演示账户）
okx account balance --demo --json
```

---

## 文件结构

```
src/
└── execution/
    ├── __init__.py
    ├── signal_router.py     # P4（已有）→ P5 新增 execute_signal 调用
    └── okx_executor.py      # P5 新增
```

---

## `src/execution/okx_executor.py`

```python
"""OKX Agent TradeKit paper-trading executor.

Requires:
  npm install -g @okx_ai/okx-trade-cli
  okx config init  (use demo API keys)

Enable in .env:
  OKX_DEMO_TRADING_ENABLED=true
  OKX_DEMO_CONTRACT_SIZE=1
"""
import json
import shutil
import subprocess

from loguru import logger

from common.config import settings
from data_layer.scanner import CoinSnapshot

_OKX_CLI = shutil.which("okx") or "okx"


def _to_okx_instid(symbol: str) -> str:
    """Convert Binance symbol to OKX perpetual swap instId.

    BTCUSDT       → BTC-USDT-SWAP
    1000PEPEUSDT  → 1000PEPE-USDT-SWAP
    """
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    return f"{base}-USDT-SWAP"


def _run(args: list[str]) -> dict | None:
    cmd = [_OKX_CLI] + args + ["--demo", "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return json.loads(r.stdout)
        logger.warning(f"[okx-demo] ✗ {' '.join(args[:3])} | {r.stderr.strip()}")
    except FileNotFoundError:
        logger.error("[okx-demo] okx CLI not found — run: npm install -g @okx_ai/okx-trade-cli")
    except Exception as e:
        logger.error(f"[okx-demo] {e}")
    return None


def _set_leverage(inst_id: str, lever: int) -> None:
    _run(["swap", "leverage", "--instId", inst_id, "--lever", str(lever), "--mgnMode", "cross"])


def _place_market(inst_id: str, side: str, pos_side: str, sz: str) -> dict | None:
    return _run([
        "swap", "place",
        "--instId", inst_id,
        "--side", side,
        "--ordType", "market",
        "--sz", sz,
        "--posSide", pos_side,
        "--tdMode", "cross",
    ])


def execute_signal(decision: dict, snap: CoinSnapshot) -> bool:
    """Execute a paper trade on OKX demo account for high-confidence signals.

    Returns True if order placed successfully.
    No-op (returns False) when OKX_DEMO_TRADING_ENABLED != true.
    """
    if not getattr(settings, "okx_demo_trading_enabled", False):
        return False

    direction = decision["direction"]
    conf      = decision["confidence"]
    lever     = int(decision.get("leverage") or 3)

    if direction not in ("LONG", "SHORT") or conf < settings.confidence_open_threshold:
        return False

    inst_id  = _to_okx_instid(snap.symbol)
    side     = "buy"  if direction == "LONG"  else "sell"
    pos_side = "long" if direction == "LONG"  else "short"
    sz       = str(getattr(settings, "okx_demo_contract_size", 1))

    _set_leverage(inst_id, lever)
    result = _place_market(inst_id, side, pos_side, sz)

    if result:
        logger.info(
            f"[okx-demo] ✅ {snap.symbol} {direction} {lever}x "
            f"sz={sz} conf={conf} → {inst_id}"
        )
        return True

    logger.warning(f"[okx-demo] ❌ {snap.symbol} {direction} order failed")
    return False
```

---

## `src/execution/signal_router.py` 修改

在现有 `route_decision` 函数末尾追加一行（import 也需要新增）：

```python
# 新增 import（文件顶部）
from execution.okx_executor import execute_signal

# route_decision 末尾，send_embed 之后追加：
def route_decision(decision: dict, snap: CoinSnapshot) -> None:
    direction = decision["direction"]
    conf = decision["confidence"]
    if direction in ("LONG", "SHORT") and conf >= settings.confidence_open_threshold:
        channel = settings.discord_channel_high_confidence
        tag = "high-confidence"
    elif direction == "WATCH" or (conf >= settings.confidence_watch_threshold and direction in ("LONG", "SHORT")):
        channel = settings.discord_channel_watch_list
        tag = "watch-list"
    else:
        return

    embed = build_signal_embed(decision, getattr(snap, "anomaly_reasons", []))
    send_embed(channel, embed)
    _append_signal(decision, snap, tag)
    logger.info(f"[route] {snap.symbol} {direction}@{conf} → {tag}")

    # P5：OKX 纸交易（仅 high-confidence 且 enabled）
    execute_signal(decision, snap)
```

---

## `src/common/config.py` 新增字段

```python
# OKX Agent TradeKit（纸交易）
okx_demo_trading_enabled: bool = False   # 改为 true 即启动自动下单
okx_demo_contract_size: int = 1          # 每笔纸交易合约数量
```

---

## `.env` 新增变量

```env
# ── OKX Agent TradeKit ─────────────────
OKX_DEMO_TRADING_ENABLED=false       # 调试确认后改为 true
OKX_DEMO_CONTRACT_SIZE=1             # 每次开仓 1 张合约
```

---

## OKX 演示 API Key 获取步骤

1. 登录 [okx.com](https://www.okx.com)
2. 右上角头像 → **API**
3. 切换到「**演示交易**」标签
4. 点击「**创建 V5 API Key**」
5. 权限勾选：**交易**（Trade）
6. 保存 API Key / Secret Key / Passphrase
7. 本地运行 `okx config init` 填入上述信息

---

## 验证流程

### Step 1：CLI 连通

```bash
# 无需 API key 的行情测试
okx market ticker ETH-USDT-SWAP --json

# 演示账户余额
okx account balance --demo --json
```

### Step 2：手动下单测试

```bash
# 演示做多 1 张 ETH 永续（市价）
okx swap leverage --instId ETH-USDT-SWAP --lever 5 --mgnMode cross --demo
okx swap place --instId ETH-USDT-SWAP --side buy --ordType market \
    --sz 1 --posSide long --tdMode cross --demo --json
```

预期返回：

```json
{"code":"0","data":[{"clOrdId":"","ordId":"123456789","sCode":"0","sMsg":""}],"msg":""}
```

### Step 3：启用自动执行

确认 Step 1/2 成功后，修改 `.env`：

```env
OKX_DEMO_TRADING_ENABLED=true
```

重启 bot：

```bash
launchctl unload ~/Library/LaunchAgents/com.cresus.bot.plist
launchctl load ~/Library/LaunchAgents/com.cresus.bot.plist
```

---

## 日志示例

启用后，`bot.log` 将出现：

```
[okx-demo] ✅ BTCUSDT LONG 5x sz=1 conf=82 → BTC-USDT-SWAP
[okx-demo] ✅ ETHUSDT SHORT 3x sz=1 conf=75 → ETH-USDT-SWAP
[okx-demo] ✗ swap leverage | instrument not available in demo
[okx-demo] ❌ LABUSDT LONG order failed
```

> 部分小市值山寨币 OKX 演示账户不支持，会静默跳过，不影响主流程。

---

## 完整信号流（P5 后）

```
Binance 扫描 20 coins
    ↓ 异常筛选（H1 过滤）
DeepSeek 判断 (direction/confidence/leverage/entry/SL/TP)
    ↓
Discord 通知 (#high-confidence / #watch-list)
    ↓
signals.jsonl 追加
    ↓
Hermes 二次分析 → Obsidian 笔记
    ↓
OKX Demo 下单 ← 新增（P5）
    ↓
OKX 演示账户持仓 → 实时 P&L 验证
```

---

## 已知限制

| 限制 | 说明 |
|------|------|
| 小市值币 | OKX 演示账户不支持所有 Binance 上的山寨币，不支持的会 skip |
| 合约规格 | OKX 合约面值各不同（BTC=0.01BTC/张），`sz=1` 不等于相同名义价值 |
| 止损 | 当前版本只下市价单，止损需手动或后续版本添加条件单 |
| 持仓跟踪 | 未集成平仓逻辑，需手动在 OKX 演示界面平仓 |

---

## ✅ P5 完成检查清单

- [ ] `npm install -g @okx_ai/okx-trade-cli` 安装成功
- [ ] `okx config init` 演示 API key 配置完成
- [ ] `okx account balance --demo --json` 返回正常
- [ ] `src/execution/okx_executor.py` 代码写入
- [ ] `signal_router.py` 追加 `execute_signal(decision, snap)`
- [ ] `common/config.py` 新增两个字段
- [ ] `.env` 新增 `OKX_DEMO_TRADING_ENABLED=false`
- [ ] 手动下单测试通过（ETH-USDT-SWAP）
- [ ] `.env` 改为 `OKX_DEMO_TRADING_ENABLED=true`，bot 重启
- [ ] `bot.log` 出现 `[okx-demo] ✅` 记录

全部打勾 = P5 OKX 纸交易集成完成。

---

## 下一步（可选）

| 阶段 | 内容 |
|------|------|
| **P5b** | 止损/止盈条件单：`okx swap place --slTriggerPx` + `--tpTriggerPx` |
| **P5c** | 持仓跟踪：`okx account positions --demo --json` 定期轮询，信号反转时自动平仓 |
| **P6** | PostgreSQL 存储层：持久化每笔纸交易 P&L，生成胜率统计 |
| **P7** | GitHub Pages 看板：信号历史 + 纸交易 P&L 可视化 |
