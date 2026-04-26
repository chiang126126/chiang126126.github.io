# P10：OCO 止损止盈（attached TP/SL）

P5 OKX 自动下单只下了**裸市价单**——无止损保护，被极端波动一单吃光的风险。本阶段在入场时**同步附加 SL/TP**：

- 入场：`okx swap place --ordType market --sz 1`
- 同时附加：`--slTriggerPx <stop> --slOrdPx -1 --tpTriggerPx <profit> --tpOrdPx -1`
- `-1` = 触发后市价平仓，不滑点等限价

DeepSeek 决策里已经包含 `stop_loss` 和 `take_profit`（多目标列表），直接复用，不需重算。

---

## 数据流变化

```
DeepSeek 决策
  {direction: LONG, confidence: 75,
   entry_price: 0.5, stop_loss: 0.45, take_profit: [0.55, 0.60]}
    ↓
风控检查（P9）
    ↓
okx swap leverage ...
okx swap place \
    --instId X-USDT-SWAP --side buy --ordType market --sz 1 \
    --tdMode cross \
    --slTriggerPx 0.45 --slOrdPx -1 \      ← 新增
    --tpTriggerPx 0.55 --tpOrdPx -1        ← 新增（取 take_profit[0]）
    ↓
OKX 演示账户：
    主仓 + 已挂止损止盈 OCO（任一触发自动平仓）
```

---

## `okx_executor.py` 修改点

仅 2 处：

### `_place_market` 签名加 sl/tp 参数

```python
def _place_market(inst_id, side, sz, sl=None, tp=None):
    args = [
        "swap", "place",
        "--instId", inst_id,
        "--side", side,
        "--ordType", "market",
        "--sz", sz,
        "--tdMode", "cross",
    ]
    if sl is not None:
        args.extend(["--slTriggerPx", str(sl), "--slOrdPx", "-1"])
    if tp is not None:
        args.extend(["--tpTriggerPx", str(tp), "--tpOrdPx", "-1"])
    return _run(args)
```

### `execute_signal` 提取 SL/TP

```python
def execute_signal(decision, snap):
    # ... 前面的开关 + 风控检查不变 ...

    inst_id = _to_okx_instid(snap.symbol)
    side    = "buy" if direction == "LONG" else "sell"
    sz      = str(getattr(settings, "okx_demo_contract_size", 1))

    # 提取 SL / TP（take_profit 可能是 list，取第一个目标）
    sl = decision.get("stop_loss")
    tp_raw = decision.get("take_profit")
    if isinstance(tp_raw, list) and tp_raw:
        tp = tp_raw[0]
    else:
        tp = tp_raw or None

    if reverse_close:
        logger.info(f"[risk] {snap.symbol} reversing: close -> reopen")
        _close_position(inst_id)

    _set_leverage(inst_id, lever)
    result = _place_market(inst_id, side, sz, sl=sl, tp=tp)

    # ... 日志 + return 不变 ...
```

---

## 部署：完整重写 `okx_executor.py`

由于改动跨多个函数，最稳妥是整体替换。完整文件内容见下面 `cat` 命令。

```bash
cat > ~/cresus-bot/src/execution/okx_executor.py << 'PYEOF'
"""OKX Agent TradeKit paper-trading executor with risk controls + OCO SL/TP (P10)."""
import json
import shutil
import subprocess
from pathlib import Path

from loguru import logger

from common.config import settings
from data_layer.scanner import CoinSnapshot

_OKX_CLI = shutil.which("okx") or "okx"
_PNL_FILE = Path.home() / "cresus-bot" / "pnl.json"


def _to_okx_instid(symbol):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    return f"{base}-USDT-SWAP"


def _run(args):
    cmd = [_OKX_CLI] + args + ["--demo", "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return json.loads(r.stdout)
        logger.warning(f"[okx-demo] fail {' '.join(args[:3])} | {r.stderr.strip()}")
    except FileNotFoundError:
        logger.error("[okx-demo] okx CLI not found")
    except Exception as e:
        logger.error(f"[okx-demo] {e}")
    return None


def _load_pnl():
    if not _PNL_FILE.exists():
        return {"summary": {}, "positions": []}
    try:
        return json.loads(_PNL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"summary": {}, "positions": []}


def _set_leverage(inst_id, lever):
    _run(["swap", "leverage", "--instId", inst_id, "--lever", str(lever), "--mgnMode", "cross"])


def _place_market(inst_id, side, sz, sl=None, tp=None):
    args = [
        "swap", "place",
        "--instId", inst_id,
        "--side", side,
        "--ordType", "market",
        "--sz", sz,
        "--tdMode", "cross",
    ]
    if sl is not None:
        args.extend(["--slTriggerPx", str(sl), "--slOrdPx", "-1"])
    if tp is not None:
        args.extend(["--tpTriggerPx", str(tp), "--tpOrdPx", "-1"])
    return _run(args)


def _close_position(inst_id):
    return _run(["swap", "close", "--instId", inst_id, "--mgnMode", "cross"])


def _risk_check(decision, snap, pnl):
    direction = decision["direction"]
    inst_id   = _to_okx_instid(snap.symbol)

    today_realized = pnl.get("summary", {}).get("today_realized_pnl", 0)
    loss_limit = getattr(settings, "risk_daily_loss_limit", 50)
    if today_realized < -abs(loss_limit):
        return False, f"daily loss circuit ({today_realized} < -{loss_limit})", False

    positions = pnl.get("positions", [])
    existing = next((p for p in positions if p.get("symbol") == inst_id), None)

    if existing:
        size = existing.get("size", 0)
        existing_long = (existing.get("side") == "long") or \
                        (existing.get("side") == "net" and size > 0)
        new_long = direction == "LONG"
        if existing_long == new_long:
            return False, f"same direction already open ({existing.get('side')})", False
        return True, "reverse signal: closing existing first", True

    max_pos = getattr(settings, "risk_max_positions", 5)
    if len(positions) >= max_pos:
        return False, f"max positions reached ({len(positions)}/{max_pos})", False

    return True, "ok", False


def execute_signal(decision, snap):
    if not getattr(settings, "okx_demo_trading_enabled", False):
        return False

    direction = decision["direction"]
    conf      = decision["confidence"]
    lever     = int(decision.get("leverage") or 3)

    if direction not in ("LONG", "SHORT") or conf < settings.confidence_open_threshold:
        return False

    pnl = _load_pnl()
    allowed, reason, reverse_close = _risk_check(decision, snap, pnl)

    if not allowed:
        logger.info(f"[risk] {snap.symbol} {direction}@{conf} blocked: {reason}")
        return False

    inst_id = _to_okx_instid(snap.symbol)
    side    = "buy" if direction == "LONG" else "sell"
    sz      = str(getattr(settings, "okx_demo_contract_size", 1))

    sl = decision.get("stop_loss")
    tp_raw = decision.get("take_profit")
    if isinstance(tp_raw, list) and tp_raw:
        tp = tp_raw[0]
    else:
        tp = tp_raw or None

    if reverse_close:
        logger.info(f"[risk] {snap.symbol} reversing: close -> reopen")
        _close_position(inst_id)

    _set_leverage(inst_id, lever)
    result = _place_market(inst_id, side, sz, sl=sl, tp=tp)

    if result:
        sltp_info = ""
        if sl or tp:
            sltp_info = f" SL={sl} TP={tp}"
        logger.info(
            f"[okx-demo] OK {snap.symbol} {direction} {lever}x "
            f"sz={sz} conf={conf}{sltp_info} -> {inst_id}"
            f"{' (reversed)' if reverse_close else ''}"
        )
        return True

    logger.warning(f"[okx-demo] FAIL {snap.symbol} {direction} order")
    return False
PYEOF
ls -la ~/cresus-bot/src/execution/okx_executor.py
```

---

## 重启 bot

```bash
launchctl unload ~/Library/LaunchAgents/com.cresus.bot.plist
launchctl load ~/Library/LaunchAgents/com.cresus.bot.plist
sleep 60
tail -30 ~/cresus-bot/logs/bot.err | grep -E "okx-demo|risk"
```

新的下单日志格式：

```
[okx-demo] OK BTCUSDT LONG 5x sz=1 conf=78 SL=44500 TP=46200 -> BTC-USDT-SWAP
```

---

## 验证 OKX 后台

下单成功后，去 OKX 演示账户的「持仓」页面，每笔仓位下面应该显示：
- 1 个止损单（红色，触发价 SL）
- 1 个止盈单（绿色，触发价 TP）

任一触发，整笔仓位自动平仓，另一单自动撤销（OCO 行为）。

---

## ✅ P10 完成检查清单

- [ ] `okx_executor.py` 完整重写
- [ ] bot 重启 exit 0
- [ ] `bot.err` 出现 `SL=... TP=...` 日志
- [ ] OKX 后台持仓下挂着 SL+TP 两个 algo 单
- [ ] 模拟一笔触发：等价格波动到 SL 或 TP，确认自动平仓 + 已实现盈亏到 pnl.json

---

## 边界情况

| 场景 | 处理 |
|------|------|
| `stop_loss` 缺失 | 不挂 SL，只挂 TP（或都不挂） |
| `take_profit` 是 list | 取第一个目标作为 TP |
| `take_profit = null` | 不挂 TP |
| 反向平仓 + 重开 | 旧仓的 SL/TP 会随平仓自动撤销，新仓重新挂 |

---

## 下一步

| 阶段 | 内容 |
|------|------|
| **P11** | 看板加风控状态卡 + Discord 风控告警 |
| **P12** | 信号 ↔ 持仓 attribution（哪个信号变成了哪笔单子，胜率归因） |
| **P13** | 多目标分批止盈：take_profit list 改成阶梯出场（30% @ TP1, 70% @ TP2） |
