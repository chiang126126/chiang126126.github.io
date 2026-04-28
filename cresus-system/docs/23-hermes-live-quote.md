# P17：Hermes 实时行情工具（绕开 Cloudflare/同意页）

## 背景

Telegram bot `@cresus_h_bot`（Hermes runtime）回答"现在 BTC 适合做空还是做多"时输出：

> 我刚尝试抓实时行情，但公开站点有验证/同意页，没拿到完整数据。

**根因**：Hermes 默认走 WebFetch 抓 binance.com / coinmarketcap / tradingview 等**前端页面**，全部带 Cloudflare/区域同意页/JS 渲染，不带 cookie + 不跑 JS 就只能拿到 interstitial。

**修复**：给 Hermes 一个"行情工具"，调用交易所**公共 REST API**（无鉴权、无 Cloudflare、无同意页）。Hermes 之后被问到任何币的实时行情，先调这个工具，不再走 WebFetch。

---

## 数据流

```
Telegram 用户提问
    ↓
@cresus_h_bot（Telegram bot 进程）
    ↓ subprocess
hermes chat -Q --source tool
    ↓ tool call
~/cresus-bot/scripts/quote.py BTC full
    ↓ HTTPS（公共端点）
fapi.binance.com / api.binance.com
    ↓ JSON
Hermes 拿到结构化行情 → 推理 → 答复
```

---

## 阶段概览

```
Step 1：写 quote.py（5 分钟）
Step 2：本地烟雾测试（2 分钟）
Step 3：注册到 Hermes（按你 Hermes runtime 类型选 A/B/C 三选一）（10 分钟）
Step 4：更新 Hermes prompt 让它优先用 quote.py（5 分钟）
Step 5：重启 Telegram bot + 端到端验证（3 分钟）
Step 6：commit + push 到私有 repo（2 分钟）
```

---

## Step 1 · 写 `scripts/quote.py`

这是一个**单文件、零项目依赖**（除 `httpx`）的 CLI 工具——不 import `src/data_layer/`，从任何目录都能跑。Hermes 的 sandbox 不需要 PYTHONPATH 配置。

```bash
cat > ~/cresus-bot/scripts/quote.py <<'PYEOF'
#!/usr/bin/env python3
"""quote.py — fetch live crypto futures market data from Binance public API.

For Hermes / @cresus_h_bot to answer ad-hoc real-time price/structure questions
without scraping web frontends (which hit Cloudflare/consent walls).

All endpoints are PUBLIC (no API key, no auth header):
  fapi.binance.com/fapi/v1/ticker/24hr
  fapi.binance.com/fapi/v1/klines
  fapi.binance.com/fapi/v1/premiumIndex
  fapi.binance.com/fapi/v1/openInterest
  fapi.binance.com/futures/data/globalLongShortAccountRatio
  fapi.binance.com/futures/data/topLongShortPositionRatio

Usage:
  quote.py BTC                     # default: summary
  quote.py BTC summary
  quote.py BTC klines 1h 24
  quote.py BTC klines 4h 30
  quote.py BTC longshort
  quote.py BTC full                # markdown blob (price + 24h + funding + OI + MA + RSI + L/S + 4H 蜡烛)
  quote.py BTC full --json         # full but JSON instead of markdown
  quote.py 1000PEPE summary        # 自动加 USDT 后缀
  quote.py BTCUSDT summary         # 也接受完整 symbol
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

try:
    import httpx
except ImportError:
    sys.exit(
        "missing dep: pip install httpx\n"
        "  (or: uv pip install httpx, or run via: uv run python scripts/quote.py ...)"
    )

FAPI = "https://fapi.binance.com"
TIMEOUT = 10


def _norm(symbol: str) -> str:
    s = symbol.upper().strip()
    return s if s.endswith("USDT") else s + "USDT"


def _get(path: str, **params) -> Any:
    r = httpx.get(f"{FAPI}{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_summary(symbol: str) -> dict:
    sym = _norm(symbol)
    t = _get("/fapi/v1/ticker/24hr", symbol=sym)
    pi = _get("/fapi/v1/premiumIndex", symbol=sym)
    oi = _get("/fapi/v1/openInterest", symbol=sym)
    price = float(t["lastPrice"])
    return {
        "symbol": sym,
        "price": price,
        "change_24h_pct": float(t["priceChangePercent"]),
        "high_24h": float(t["highPrice"]),
        "low_24h": float(t["lowPrice"]),
        "vol_24h_usd": float(t["quoteVolume"]),
        "funding_rate": float(pi["lastFundingRate"]),
        "next_funding_time": int(pi["nextFundingTime"]),
        "mark_price": float(pi["markPrice"]),
        "open_interest_coins": float(oi["openInterest"]),
        "open_interest_usd": float(oi["openInterest"]) * price,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_klines(symbol: str, interval: str, limit: int) -> list[dict]:
    raw = _get("/fapi/v1/klines", symbol=_norm(symbol), interval=interval, limit=limit)
    return [
        {
            "open_time": int(k[0]),
            "o": float(k[1]),
            "h": float(k[2]),
            "l": float(k[3]),
            "c": float(k[4]),
            "v": float(k[5]),
        }
        for k in raw
    ]


def fetch_longshort(symbol: str) -> dict:
    sym = _norm(symbol)
    glob = _get("/futures/data/globalLongShortAccountRatio", symbol=sym, period="1h", limit=6)
    top = _get("/futures/data/topLongShortPositionRatio", symbol=sym, period="1h", limit=6)
    return {"symbol": sym, "global_account_1h": glob, "top_trader_position_1h": top}


def _ma(klines: list[dict], n: int) -> float | None:
    if len(klines) < n:
        return None
    return sum(k["c"] for k in klines[-n:]) / n


def _rsi(klines: list[dict], n: int = 14) -> float | None:
    if len(klines) < n + 1:
        return None
    closes = [k["c"] for k in klines]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def render_full_markdown(symbol: str) -> str:
    sym = _norm(symbol)
    s = fetch_summary(sym)
    k1h = fetch_klines(sym, "1h", 24)
    k4h = fetch_klines(sym, "4h", 30)
    ls = fetch_longshort(sym)

    ma20_1h = _ma(k1h, 20)
    ma20_4h = _ma(k4h, 20)
    rsi_1h = _rsi(k1h, 14)
    rsi_4h = _rsi(k4h, 14)
    price = s["price"]
    next_fd = datetime.fromtimestamp(s["next_funding_time"] / 1000, tz=timezone.utc)

    glob_latest = ls["global_account_1h"][-1] if ls["global_account_1h"] else {}
    top_latest = ls["top_trader_position_1h"][-1] if ls["top_trader_position_1h"] else {}

    out = [
        f"# {sym} 实时行情（{s['as_of']}）",
        "",
        "## 现价 & 24h",
        f"- 现价：**{price:,.2f}** USDT",
        f"- 24h 涨跌：{s['change_24h_pct']:+.2f}%",
        f"- 24h 高/低：{s['high_24h']:,.2f} / {s['low_24h']:,.2f}",
        f"- 24h 成交额：{s['vol_24h_usd']/1e9:.2f}B USDT",
        "",
        "## 衍生品指标",
        f"- 标记价：{s['mark_price']:,.2f}",
        f"- 资金费率：{s['funding_rate']*100:+.4f}%（下次结算 {next_fd.strftime('%H:%M UTC')}）",
        f"- 持仓量：{s['open_interest_coins']:,.0f} {sym.replace('USDT','')}"
        f"（≈ {s['open_interest_usd']/1e9:.2f}B USDT）",
        "",
        "## 趋势 & 动能",
    ]
    if ma20_1h:
        out.append(f"- 1H MA20：{ma20_1h:,.2f}（偏离 {(price-ma20_1h)/ma20_1h*100:+.2f}%）")
    if ma20_4h:
        out.append(f"- 4H MA20：{ma20_4h:,.2f}（偏离 {(price-ma20_4h)/ma20_4h*100:+.2f}%）")
    if rsi_1h is not None:
        out.append(f"- 1H RSI(14)：{rsi_1h:.1f}")
    if rsi_4h is not None:
        out.append(f"- 4H RSI(14)：{rsi_4h:.1f}")

    out += [
        "",
        "## 多空情绪（1H 最新）",
        f"- 全账户多空比：{glob_latest.get('longShortRatio', '—')}",
        f"- 大户持仓多空比：{top_latest.get('longShortRatio', '—')}",
        "",
        "## 最近 6 根 4H 蜡烛",
        "| 开盘(UTC) | O | H | L | C | 振幅% |",
        "|---|---|---|---|---|---|",
    ]
    for k in k4h[-6:]:
        ts = datetime.fromtimestamp(k["open_time"] / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        amp = (k["h"] - k["l"]) / k["o"] * 100
        out.append(f"| {ts} | {k['o']:,.2f} | {k['h']:,.2f} | {k['l']:,.2f} | {k['c']:,.2f} | {amp:.2f} |")

    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser(description="Live crypto futures quote (Binance public API)")
    p.add_argument("symbol", help="e.g. BTC, ETH, BTCUSDT, 1000PEPE")
    p.add_argument("mode", nargs="?", default="summary",
                   choices=["summary", "klines", "longshort", "full"])
    p.add_argument("interval", nargs="?", default="1h",
                   help="for klines: 1m/5m/15m/1h/4h/1d (default 1h)")
    p.add_argument("limit", nargs="?", type=int, default=24,
                   help="for klines: bar count (default 24)")
    p.add_argument("--json", action="store_true", help="raw JSON instead of markdown")
    args = p.parse_args()

    try:
        if args.mode == "summary":
            data: Any = fetch_summary(args.symbol)
        elif args.mode == "klines":
            data = fetch_klines(args.symbol, args.interval, args.limit)
        elif args.mode == "longshort":
            data = fetch_longshort(args.symbol)
        else:  # full
            if args.json:
                sym = _norm(args.symbol)
                data = {
                    "summary": fetch_summary(sym),
                    "klines_1h_24": fetch_klines(sym, "1h", 24),
                    "klines_4h_30": fetch_klines(sym, "4h", 30),
                    "longshort": fetch_longshort(sym),
                }
            else:
                print(render_full_markdown(args.symbol))
                return
    except httpx.HTTPStatusError as e:
        sys.exit(f"binance api error: {e.response.status_code} {e.response.text[:200]}")
    except Exception as e:
        sys.exit(f"error: {e}")

    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
PYEOF

chmod +x ~/cresus-bot/scripts/quote.py
```

---

## Step 2 · 烟雾测试

确保从命令行能拿到干净数据：

```bash
cd ~/cresus-bot

# 2.1 现价 + 24h 摘要
uv run python scripts/quote.py BTC summary

# 2.2 1H 蜡烛 24 根
uv run python scripts/quote.py BTC klines 1h 24

# 2.3 多空比
uv run python scripts/quote.py BTC longshort

# 2.4 完整 markdown（这就是 Hermes 会拿到的格式）
uv run python scripts/quote.py BTC full
```

预期 2.4 输出（节选）：

```markdown
# BTCUSDT 实时行情（2026-04-28T13:45:12+00:00）

## 现价 & 24h
- 现价：**67,432.50** USDT
- 24h 涨跌：+1.24%
- 24h 高/低：68,012.00 / 66,540.20
- 24h 成交额：32.18B USDT

## 衍生品指标
- 标记价：67,438.10
- 资金费率：+0.0085%（下次结算 16:00 UTC）
- 持仓量：85,432 BTC（≈ 5.76B USDT）

## 趋势 & 动能
- 1H MA20：67,180.45（偏离 +0.37%）
- 4H MA20：66,890.20（偏离 +0.81%）
- 1H RSI(14)：58.3
- 4H RSI(14)：54.1
…
```

**如果 2.1 报 `binance api error: 451`**（Binance 在某些 IP 范围被禁），跳到本文档末尾的 [附录 A：OKX fallback](#附录-aokx-fallback)。

---

## Step 3 · 注册到 Hermes（三选一）

> Hermes 是一个 CLI agent，调用方式 `hermes chat -Q --source tool --max-turns 5 -q <prompt>`。怎么注册新工具取决于你的 Hermes runtime 类型。**先跑这条命令确认你属于哪种**：
>
> ```bash
> hermes --help 2>&1 | head -40
> ls -la ~/.config/hermes/ ~/.hermes/ 2>/dev/null
> grep -rln 'tool\|mcp\|register' ~/.config/hermes/ ~/.hermes/ 2>/dev/null | head -10
> ```
>
> 把输出贴回来，我就能告诉你走 A/B/C 哪条。如果你想直接动手，按下面三种最常见路径试。

### 路径 A · Hermes 已有 `bash`/`shell` 工具（最常见，最简单）

如果 Hermes 默认就带了 shell 调用能力（看 `hermes --help` 是否提到 `bash` / `shell` / `exec`），**完全不用注册新工具**——只要在 prompt 里告诉它"先用 `bash: quote.py`"。直接跳到 [Step 4](#step-4--更新-hermes-prompt)。

### 路径 B · Hermes 用 MCP（Model Context Protocol）

如果 `~/.config/hermes/` 下有 `mcp_servers.json` 或 `config.toml` 提到 `mcp`，写一个最小 MCP server 包装：

```bash
cat > ~/cresus-bot/scripts/quote_mcp.py <<'PYEOF'
"""Minimal MCP server exposing quote.py as a tool for Hermes."""
import asyncio
import json
import subprocess
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

QUOTE_CLI = "/home/USER/cresus-bot/scripts/quote.py"  # ← 改成你的实际路径

server = Server("crypto-quote")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [Tool(
        name="get_live_quote",
        description=(
            "Fetch live crypto futures market data from Binance public API. "
            "Use this INSTEAD OF web search/fetch when asked about current price, "
            "funding rate, open interest, long/short ratio, or recent candles. "
            "No auth required. No Cloudflare/consent issues."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "e.g. BTC, ETH, 1000PEPE"},
                "mode": {
                    "type": "string",
                    "enum": ["summary", "full", "klines", "longshort"],
                    "default": "full",
                },
            },
            "required": ["symbol"],
        },
    )]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "get_live_quote":
        return [TextContent(type="text", text=f"unknown tool: {name}")]
    sym = arguments["symbol"]
    mode = arguments.get("mode", "full")
    r = subprocess.run(
        ["python3", QUOTE_CLI, sym, mode],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        return [TextContent(type="text", text=f"quote error: {r.stderr.strip()}")]
    return [TextContent(type="text", text=r.stdout)]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
PYEOF
```

把 `/home/USER/` 改成你的实际路径，安装依赖：

```bash
uv pip install mcp
```

注册到 Hermes（编辑 `~/.config/hermes/mcp_servers.json` 或等价文件）：

```json
{
  "mcpServers": {
    "crypto-quote": {
      "command": "uv",
      "args": ["run", "--project", "/home/USER/cresus-bot", "python", "/home/USER/cresus-bot/scripts/quote_mcp.py"]
    }
  }
}
```

### 路径 C · Hermes 用声明式 tools 配置（toml/yaml）

如果 `~/.config/hermes/` 下有 `tools.toml` / `tools.yaml`，追加：

```toml
[[tool]]
name = "get_live_quote"
description = """
Fetch live crypto futures market data from Binance public API.
Use INSTEAD OF web search/fetch when asked about current price, funding rate,
open interest, long/short ratio, or recent candles. No auth, no Cloudflare.
"""
command = "/home/USER/cresus-bot/scripts/quote.py"
args = ["{symbol}", "{mode}"]

[tool.parameters]
symbol = { type = "string", required = true, description = "e.g. BTC, ETH, 1000PEPE" }
mode = { type = "string", default = "full", enum = ["summary", "full", "klines", "longshort"] }
```

> 三条路径**只走一条**——你 Hermes 是哪种用哪种。

---

## Step 4 · 更新 Hermes prompt

`~/cresus-bot/prompts/hermes_analyst.txt` 是 P11 文档里写过的 Hermes 系统提示。**追加**（不是替换）一段告诉它优先用新工具：

```bash
cat >> ~/cresus-bot/prompts/hermes_analyst.txt <<'EOF'

---

## 实时行情查询（2026-04 起强制规则）

当用户询问任何币种的**当前价格、行情、做多/做空、入场点位、近期趋势**等需要实时数据的问题时：

1. **必须**先调用 `get_live_quote`（MCP 工具）或 `bash` 执行 `python3 ~/cresus-bot/scripts/quote.py <SYMBOL> full`
2. **禁止**用 WebFetch / WebSearch 抓 binance.com / coinmarketcap / tradingview 等前端页面——它们都有 Cloudflare/同意页，不可用
3. 拿到工具返回的 markdown 行情后，结合 Master Framework v0.7（结构 A–H + 硬规则 H2–H5）给出方向 + 信心度

如果工具调用失败（network error/symbol 不存在），**直接告诉用户失败原因**，不要回退到网页抓取。
EOF
```

---

## Step 5 · 重启 Telegram bot + 端到端验证

### 5.1 重启 Hermes 进程（如果 Hermes 是常驻）

```bash
# macOS launchd 例（按你实际 plist 名调整）
launchctl unload ~/Library/LaunchAgents/com.cresus.hermes-bridge.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.cresus.hermes-bridge.plist

# Linux systemd 例
sudo systemctl restart hermes-bridge 2>/dev/null

# 或者你 Telegram bot 是独立进程
pkill -f cresus_h_bot && sleep 2 && nohup python3 ~/cresus-bot/scripts/cresus_h_bot.py >> ~/cresus-bot/logs/bot.log 2>&1 &
```

### 5.2 在 Telegram 里直接问

打开 `@cresus_h_bot`，发：

```
现在 BTC 适合做空还是做多
```

**预期**：bot 不再说"公开站点有验证/同意页"，而是返回类似：

> **结论：偏中性，倾向做多 1H 反弹**
>
> 当前数据（来自 quote.py / Binance fapi）：
> - 现价 67,432，1H MA20 上方 +0.37%
> - 4H RSI(14) 54.1，未超买
> - 资金费率 +0.0085%（中性）
> - 持仓量 5.76B USDT
> - 大户多空比 1.42（偏多）
>
> 短线方向：1H 站上 MA20 + L/S 偏多 → **做多胜率 > 做空**…

### 5.3 看日志确认工具被调用

```bash
tail -50 ~/cresus-bot/logs/bot.log | grep -E 'quote\.py|get_live_quote|tool_call'
```

应该能看到 `get_live_quote` 或 `quote.py BTC full` 被执行。

---

## Step 6 · commit + push 到私有 repo

```bash
cd ~/cresus-bot
git status

git add scripts/quote.py prompts/hermes_analyst.txt
# 如果走了路径 B：git add scripts/quote_mcp.py
# 如果走了路径 C：git add ~/.config/hermes/tools.toml（或对应路径）

git commit -m "P17: live quote tool for Hermes (Binance public API, no scraping)"
git push
```

---

## ✅ P17 完成检查清单（2026-04-28 全部 PASS）

- [x] `scripts/quote.py` 写入 + `chmod +x`（`-rwxr-xr-x  6744 bytes`）
- [x] `quote.py BTC full` 本地输出干净 markdown（含 MA20/RSI/资金费/多空比/4H 蜡烛）
- [x] Hermes runtime 确认：**HermesAgent**（`~/.local/bin/hermes`），走**路径 B（MCP）**
- [x] `scripts/quote_mcp.py` FastMCP 包装 + `uv pip install mcp`（mcp==1.27.0）
- [x] `hermes mcp add crypto-quote` 注册成功，`hermes mcp test` 1164ms 握手通过
- [x] `hermes tools --summary` 显示 CLI 平台已自动启用 `crypto-quote`
- [x] `--source tool` 经 `hermes chat --help` 确认只是 session 标签、不影响工具可见性 → telegram_bot.py 无需任何改动
- [x] CLI 模拟 Telegram 调用：`hermes chat -Q --source tool --max-turns 3 -t "" -q "现在 BTC 适合做空还是做多？"` 触发 `Processing request of type CallToolRequest`，Hermes 拿到真实数据后给出框架级判断
- [x] 真 Telegram `@cresus_h_bot` 回复包含具体数字（现价 75993 / RSI 37.9 / 大户多空比 0.84），**没有** "Reached maximum iterations" 也**没有** "公开站点有验证/同意页"
- [x] 私有 repo commit `a776078` push 成功

---

## 📌 实测验证（2026-04-28）

### CLI 测试 session

- session ID：`20260428_171806_4e14c6`
- query：`现在 BTC 适合做空还是做多？请用 get_live_quote 工具查实时数据再判断。`
- Hermes 行为：自动调用 `get_live_quote(symbol="BTC", mode="full")`，返回 markdown blob
- 输出关键数据：现价 75,949.9 / 1H MA20 76,687.7 / 4H MA20 77,460.0 / 1H RSI 37.7 / 4H RSI 36.3 / 全账户多空比 0.9585 / 大户多空比 0.8445
- 框架判断：**短线偏空，不建议追空，等反弹做空或破位跟空**

### Telegram 真实回复样本

`@cresus_h_bot` 收到 `现在 BTC 适合做空还是做多`（无任何额外提示），回复：

```
**偏空，短线更适合轻仓做空 / 等反弹空。**

理由：
- 现价 **75993**，低于 **1H/4H MA20**
- **1H RSI 37.9 / 4H RSI 36.4**，弱势未修复
- 大户多空比 **0.84**，偏空
- 资金费率小幅为正，说明多头还在付费，易被继续压制

策略：
- 激进：反弹到 **76600-77400** 区间找空
- 保守：跌破 **75600** 再跟空

若强势站回 **76688** 上方，再考虑转多。
*仅供参考，不构成投资建议。*
```

### 私有 repo 落盘

```
commit a776078 (HEAD -> main, origin/main)
P17: live quote MCP tool for Hermes (绕开 Cloudflare/同意页)

 pyproject.toml         | +1 line  (mcp dep)
 uv.lock                | +N lines
 scripts/quote.py       | +149 lines (new, executable)
 scripts/quote_mcp.py   | +47 lines  (new, executable)
```

### 一行复盘

> WebFetch 抓前端 → Cloudflare 同意页 ❌
> Binance public REST API → 干净 JSON ✅
>
> 治本方案：给 Hermes 一个 MCP 工具直连交易所公共 API，绕过整个网页层。Hermes 看 docstring 里 `USE THIS INSTEAD OF web search/fetch when the user asks about current price...` 自动选对工具，无需改 prompt 或 SOUL.md。

---

## 附录 A：OKX fallback

如果你 VPS 在 Binance 受限地区（return 451），在 `quote.py` 顶部把 `FAPI` 改为 OKX：

```python
# 替换 _get / fetch_summary / fetch_klines 改用 OKX 端点
# OKX 公共端点（无鉴权）：
#   https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP
#   https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=24
#   https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP
#   https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP
```

参考 `cresus-system/docs/10-data-layer.md` Stage C 已有的 `okx_client.py` 实现。需要时叫我，我把 OKX 版的 `quote.py` 完整出给你。

---

## 附录 B：为什么不复用 `src/data_layer/binance_client.py`

故意写成单文件、零项目依赖：

1. **Hermes sandbox 隔离**：Hermes 工具进程不一定在 cresus-bot 的 venv 里；单文件免 PYTHONPATH 困扰
2. **故障隔离**：行情工具挂掉不影响主 bot 扫币流程
3. **可移植**：把 `quote.py` copy 到任何机器（dev Mac、备用 VPS）都直接能跑，不用 clone 整个项目

如果你后续想统一，再做一个轻量包装即可。现在不折腾。

---

## 下一步（可选）

| 阶段 | 内容 |
|------|------|
| **P17b** | 加 `quote.py SCAN` 模式：扫 BTC/ETH/SOL/BNB 4 个主流币摘要，一次返回 |
| **P17c** | Hermes 的"主动止盈止损建议"也走 quote.py 拿现价 |
| **P17d** | 把 quote.py 的 markdown 输出格式同步到 dashboard 信号详情页 |
