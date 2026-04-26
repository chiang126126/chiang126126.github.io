# P9：风控层（Risk Control）

P5 OKX 自动下单 + P7 P&L 追踪到位后，需要硬编码风控防止黑天鹅与无限亏损。本阶段在 `okx_executor.py` 下单前增加 4 条规则。

---

## 4 条风控规则

| 规则 | env | 默认 | 行为 |
|------|-----|------|------|
| 日亏损熔断 | `RISK_DAILY_LOSS_LIMIT` | 50 | 当日已实现亏损 > 50 USDT → 当日停所有新单 |
| 同时持仓上限 | `RISK_MAX_POSITIONS` | 5 | 持仓数 ≥ 5 → 拒新单（反向除外） |
| 反向信号自动平仓 | 内置 | on | 已 LONG 收到 SHORT → 先平后开 |
| 同向重复跳过 | 内置 | on | 已 LONG 收到 LONG → 跳过 |

风控数据从 `~/cresus-bot/pnl.json` 读取（P7 已部署）。

---

## 数据流

```
信号到达 signal_router.route_decision
    ↓
execute_signal(decision, snap)
    ├─ load pnl.json
    ├─ Rule 1: 日亏损熔断？→ skip
    ├─ Rule 2: 持仓上限？→ skip (除反向)
    ├─ Rule 3: 同向重复？→ skip
    ├─ Rule 4: 反向信号？→ close existing first
    └─ place_market_order
```

---

## `src/execution/okx_executor.py`（完整重写）

```python
"""OKX Agent TradeKit paper-trading executor with risk controls (P9)."""
import json
import shutil
import subprocess
from pathlib import Path

from loguru import logger

from common.config import settings
from data_layer.scanner import CoinSnapshot

_OKX_CLI = shutil.which("okx") or "okx"
_PNL_FILE = Path.home() / "cresus-bot" / "pnl.json"


def _to_okx_instid(symbol: str) -> str:
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


def _place_market(inst_id, side, sz):
    return _run([
        "swap", "place",
        "--instId", inst_id,
        "--side", side,
        "--ordType", "market",
        "--sz", sz,
        "--tdMode", "cross",
    ])


def _close_position(inst_id):
    """Close existing position via OKX close-position command."""
    return _run([
        "swap", "close",
        "--instId", inst_id,
        "--mgnMode", "cross",
    ])


def _risk_check(decision, snap, pnl):
    """Returns (allowed: bool, reason: str, reverse_close: bool)."""
    direction = decision["direction"]
    inst_id   = _to_okx_instid(snap.symbol)

    # Rule 1: daily loss circuit
    today_realized = pnl.get("summary", {}).get("today_realized_pnl", 0)
    loss_limit = getattr(settings, "risk_daily_loss_limit", 50)
    if today_realized < -abs(loss_limit):
        return False, f"daily loss circuit ({today_realized} < -{loss_limit})", False

    # Find existing position for this symbol
    positions = pnl.get("positions", [])
    existing = next((p for p in positions if p.get("symbol") == inst_id), None)

    # Rule 3 & 4: same-direction skip vs reverse-close
    if existing:
        existing_long = (existing.get("side") == "long") or \
                        (existing.get("side") == "net" and existing.get("size", 0) > 0)
        new_long = direction == "LONG"
        if existing_long == new_long:
            return False, f"same direction already open ({existing.get('side')})", False
        return True, "reverse signal: closing existing first", True

    # Rule 2: max concurrent positions
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

    if reverse_close:
        logger.info(f"[risk] {snap.symbol} reversing: close → reopen")
        _close_position(inst_id)

    _set_leverage(inst_id, lever)
    result = _place_market(inst_id, side, sz)

    if result:
        logger.info(
            f"[okx-demo] OK {snap.symbol} {direction} {lever}x "
            f"sz={sz} conf={conf} → {inst_id}"
            f"{' (reversed)' if reverse_close else ''}"
        )
        return True

    logger.warning(f"[okx-demo] FAIL {snap.symbol} {direction} order")
    return False
```

---

## `src/common/config.py` 新增字段

```python
risk_daily_loss_limit: float = 50.0    # 日亏损熔断阈值（USDT）
risk_max_positions: int     = 5        # 同时持仓上限
```

---

## `.env` 新增变量

```env
RISK_DAILY_LOSS_LIMIT=50
RISK_MAX_POSITIONS=5
```

---

## 部署步骤

### 1. 用以下命令重写 `okx_executor.py`

```bash
cat > ~/cresus-bot/src/execution/okx_executor.py << 'PYEOF'
"""OKX Agent TradeKit paper-trading executor with risk controls (P9)."""
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


def _place_market(inst_id, side, sz):
    return _run([
        "swap", "place",
        "--instId", inst_id,
        "--side", side,
        "--ordType", "market",
        "--sz", sz,
        "--tdMode", "cross",
    ])


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
        existing_long = (existing.get("side") == "long") or \
                        (existing.get("side") == "net" and existing.get("size", 0) > 0)
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

    if reverse_close:
        logger.info(f"[risk] {snap.symbol} reversing: close → reopen")
        _close_position(inst_id)

    _set_leverage(inst_id, lever)
    result = _place_market(inst_id, side, sz)

    if result:
        logger.info(
            f"[okx-demo] OK {snap.symbol} {direction} {lever}x "
            f"sz={sz} conf={conf} → {inst_id}"
            f"{' (reversed)' if reverse_close else ''}"
        )
        return True

    logger.warning(f"[okx-demo] FAIL {snap.symbol} {direction} order")
    return False
PYEOF
ls -la ~/cresus-bot/src/execution/okx_executor.py
```

### 2. 添加 .env 变量

```bash
echo "RISK_DAILY_LOSS_LIMIT=50" >> ~/cresus-bot/.env
echo "RISK_MAX_POSITIONS=5"     >> ~/cresus-bot/.env
tail -3 ~/cresus-bot/.env
```

### 3. 在 `src/common/config.py` 加 2 个字段

打开文件：

```bash
nano ~/cresus-bot/src/common/config.py
```

在 `okx_demo_contract_size: int = 1` 那一行下面加：

```python
    risk_daily_loss_limit: float = 50.0
    risk_max_positions: int = 5
```

注意 4 个空格缩进。保存：Ctrl+O 回车 Ctrl+X。

验证：

```bash
grep -n "risk_" ~/cresus-bot/src/common/config.py
```

应该看到 2 行。

### 4. 重启 bot

```bash
launchctl unload ~/Library/LaunchAgents/com.cresus.bot.plist
launchctl load  ~/Library/LaunchAgents/com.cresus.bot.plist
```

---

## 验证日志

下个周期触发时，bot.log 会出现新的风控日志：

```
[risk] BTCUSDT LONG@75 blocked: same direction already open (long)
[risk] ETHUSDT reversing: close → reopen
[risk] DOGEUSDT SHORT@72 blocked: max positions reached (5/5)
[risk] PEPEUSDT LONG@70 blocked: daily loss circuit (-52.3 < -50)
```

正常下单时仍然是：

```
[okx-demo] OK ARBUSDT LONG 3x sz=1 conf=78 → ARB-USDT-SWAP
```

---

## ✅ P9 完成检查清单

- [ ] `src/execution/okx_executor.py` 重写完成
- [ ] `.env` 新增 `RISK_DAILY_LOSS_LIMIT=50` + `RISK_MAX_POSITIONS=5`
- [ ] `common/config.py` 新增 2 个字段
- [ ] bot 重启成功（`launchctl list` exit 0）
- [ ] `bot.log` 出现 `[risk]` 或 `[okx-demo] OK` 日志
- [ ] OKX 持仓数稳定在 ≤ 5 笔

---

## 下一步

| 阶段 | 内容 |
|------|------|
| **P10** | OCO 止损：进场同时挂止损/止盈条件单 |
| **P11** | 看板加风控状态卡（今日已实现 / 是否熔断 / 持仓数 / 容量） |
| **P12** | 反向信号成功率统计：复盘反向操作 vs 直接平仓哪个更优 |
