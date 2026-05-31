"""Binance Futures (USDT-M) API 客户端 — 实盘交易订单层基建 Phase 2.1.

只读 API + HMAC 签名 + retry + rate limit. 不含订单下单 (Phase 2.2).

安全设计:
- API secret 永不出现在 __repr__ / __str__ / log / error message
- 默认 testnet, 切 mainnet 必须显式 testnet=False
- 仅使用 stdlib (urllib / hmac / hashlib) 避免外部依赖供应链风险
- HTTPS only, HMAC-SHA256 标准签名, recvWindow 5000ms 防 replay

凭证读取:
    通过 env: BINANCE_API_KEY / BINANCE_API_SECRET
    或文件: ~/.cresus-bot/binance_keys.json {api_key, api_secret, testnet}

使用:
    client = BinanceClient(api_key, api_secret, testnet=True)
    drift = client.check_time_drift()        # 必须先校时
    if abs(drift) > 3.0: raise SystemExit("clock drift")
    account = client.get_account()
    positions = client.get_positions()
    open_orders = client.get_open_orders()

CLI 连通性测试:
    python3 binance_client.py --testnet --check-time --account
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

# ============================================================================
# 配置常量
# ============================================================================

MAINNET_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"

# Rate limit (Binance USDT-M Futures: 2400 weight per minute, UID-based)
RATE_LIMIT_WEIGHT_PER_MIN = 2400
RATE_LIMIT_WINDOW_SEC = 60

# 时间同步
# Phase 5.I (5/31): RECV_WINDOW_MS 5000 → 30000 — Mac 唤醒/NTP 漂移容忍.
# 之前 5s 窗口下, Mac 短暂休眠唤醒后系统时钟偏 > 5s 直接 -1021 拒绝.
# 5/31 实例: 10:00-10:30 30min 空窗后唤醒, recvWindow 不足导致后续多次失败.
# 30000 是 Binance 推荐上限 60000 的一半, 平衡防 replay 和漂移容忍.
RECV_WINDOW_MS = 30000            # 容忍 NTP 漂移 (was 5000)
MAX_TIME_DRIFT_SEC = 3.0          # 漂移 >3s 拒绝下单 (Phase 2.2 用)

# Retry
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_SEC = (2, 4, 8, 16)   # 指数退避

# Default keys file (本地开发用, 生产推荐 env)
DEFAULT_KEYS_FILE = Path.home() / ".cresus-bot" / "binance_keys.json"

# Logger (注意: 永远不要打 secret)
log = logging.getLogger(__name__)


# ============================================================================
# Exceptions (按重试策略分层)
# ============================================================================

class BinanceError(Exception):
    """Base for all Binance API errors."""


class BinanceAuthError(BinanceError):
    """API key 无效 / 签名错 / 权限不足. FATAL - 不可重试, 必须人工介入."""


class BinanceRateLimitError(BinanceError):
    """超频. 可重试, 长退避."""


class BinanceNetworkError(BinanceError):
    """网络 / 连接 / 5xx. 可重试, 短退避."""


class BinanceTimeError(BinanceError):
    """时钟漂移超出 recvWindow. 必须重新校时."""


# ============================================================================
# 凭证格式校验 (在签名前发现误粘贴的 placeholder, 比 401 更友好)
# ============================================================================

def _validate_credential(value: str, name: str) -> None:
    """检测常见的误粘贴模式. 抛 ValueError + 修复建议.
    实际 Binance key/secret 是 64 位 alphanumeric. 我们用宽松检测避免误杀.
    """
    if value.startswith("<") or value.endswith(">"):
        raise ValueError(
            f"{name} 看起来包含尖括号 (起始='<' 或结尾='>'). "
            f"你可能复制了文档里的占位符 <...>. 请只粘贴 key 本身, 不要带尖括号."
        )
    if value in ("...", "...your_key...", "your_key", "your_secret"):
        raise ValueError(
            f"{name} 是字面占位符 '{value}'. "
            f"请去 Binance API 管理页面生成真实 key 后填入."
        )
    # 含空格或换行 (通常 copy-paste 把回车带进来)
    if any(c in value for c in (" ", "\n", "\t", "\r")):
        raise ValueError(
            f"{name} 包含空格/换行 (长度={len(value)}). "
            f"请去掉所有不可见字符后重试."
        )
    # 长度异常 (Binance 通常 64, 容许 40-128 范围)
    if len(value) < 30 or len(value) > 200:
        raise ValueError(
            f"{name} 长度异常 (实际={len(value)}, Binance 标准 64). "
            f"请确认 key 完整无截断."
        )
    # 非 ASCII 可见字符 (可能 copy-paste 引入 unicode 字符)
    if not all(0x20 <= ord(c) < 0x7F for c in value):
        raise ValueError(
            f"{name} 包含非 ASCII 字符. "
            f"请确认在终端正确粘贴 (建议先粘到记事本看是否有奇怪字符)."
        )


# ============================================================================
# 订单参数格式化 helpers (Binance 要求 decimal string, 不接受 sci notation)
# ============================================================================

# Binance newClientOrderId 规则: 1-36 chars, [a-zA-Z0-9-_]
_CLIENT_ORDER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,36}$")


def _validate_client_order_id(coid: str) -> None:
    """验证 client_order_id 符合 Binance 规范.
    1-36 chars, 仅 [a-zA-Z0-9-_]. 不合规则会被 Binance 拒.
    """
    if not isinstance(coid, str) or not coid:
        raise ValueError("client_order_id 必须是非空字符串")
    if not _CLIENT_ORDER_ID_RE.match(coid):
        raise ValueError(
            f"client_order_id {coid!r} 不合规: "
            f"必须 1-36 字符 [a-zA-Z0-9_-], 实际 len={len(coid)}"
        )


# Phase 2.2.2: trade_id 是高阶接口入参, 会嵌进 client_order_id.
# 规则: cresus_{trade_id}_{role}, role 最多 3 字符 (E/SL/RB...).
# 所以 trade_id 最多 36 - 7 (prefix) - 4 (suffix) = 25 chars.
_TRADE_ID_MAX_LEN = 25


def _validate_trade_id(trade_id: str) -> None:
    """验证 trade_id 用于构造 client_order_id."""
    if not isinstance(trade_id, str) or not trade_id:
        raise ValueError("trade_id 必须是非空字符串")
    if len(trade_id) > _TRADE_ID_MAX_LEN:
        raise ValueError(
            f"trade_id 太长 (max {_TRADE_ID_MAX_LEN} chars), got {len(trade_id)}"
        )
    if not _CLIENT_ORDER_ID_RE.match(trade_id):
        raise ValueError(
            f"trade_id {trade_id!r} 不合规: 只能含 [a-zA-Z0-9_-]"
        )


def _format_quantity(qty: float) -> str:
    """格式化 quantity. Binance 不接 sci notation (e.g. '1e-05').
    用 decimal string. 实际精度由 LOT_SIZE filter 限定, 上层负责 round.
    """
    if qty <= 0:
        raise ValueError(f"quantity 必须 > 0, got {qty}")
    # 用 :f 强制 decimal, 然后去尾随 0 (保留至少 1 位小数 / 整数)
    s = f"{float(qty):.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _format_price(price: float) -> str:
    """格式化 price. 同 _format_quantity."""
    if price <= 0:
        raise ValueError(f"price 必须 > 0, got {price}")
    s = f"{float(price):.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def round_qty_down_to_step(qty: float, step_size: float) -> float:
    """把 qty round DOWN 到 step_size 倍数 (满足 Binance LOT_SIZE filter).
    例: round_qty_down_to_step(0.000246, 0.001) → 0.0 (太小, 上层应改用 min_qty)
    例: round_qty_down_to_step(0.0035, 0.001)   → 0.003
    例: round_qty_down_to_step(1.23, 0.01)      → 1.23
    """
    if step_size <= 0:
        raise ValueError(f"step_size 必须 > 0, got {step_size}")
    if qty < 0:
        raise ValueError(f"qty 不能为负, got {qty}")
    # 用 round 避免 floating point 精度问题
    n_steps = math.floor(qty / step_size + 1e-9)  # 防 0.3/0.1 = 2.99999...
    result = n_steps * step_size
    # round 到与 step_size 同样的小数位避免 0.30000000004
    # step_size = "0.001" → 3 decimal places
    s = f"{step_size:.10f}".rstrip("0").rstrip(".")
    decimals = len(s.split(".")[-1]) if "." in s else 0
    return round(result, decimals)


def round_price_to_tick(price: float, tick_size: float) -> float:
    """把 price round 到 tick_size 倍数 (满足 PRICE_FILTER)."""
    if tick_size <= 0:
        raise ValueError(f"tick_size 必须 > 0, got {tick_size}")
    if price < 0:
        raise ValueError(f"price 不能为负, got {price}")
    n_ticks = round(price / tick_size)
    result = n_ticks * tick_size
    s = f"{tick_size:.10f}".rstrip("0").rstrip(".")
    decimals = len(s.split(".")[-1]) if "." in s else 0
    return round(result, decimals)


# ============================================================================
# Token Bucket (rate limiting)
# ============================================================================

class _TokenBucket:
    """单线程 token bucket. Binance USDT-M Futures 每分钟 2400 weight,
    我们 floor 在 2400/60 = 40 weight/sec 平均速率.

    线程不安全 — 单 BinanceClient 实例只能由单线程使用.
    """

    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def acquire(self, weight: int = 1, blocking: bool = True) -> bool:
        """消耗 weight 个 token. blocking=True 则会 sleep 直到拿到."""
        if weight <= 0:
            return True
        self._refill()
        if self.tokens >= weight:
            self.tokens -= weight
            return True
        if not blocking:
            return False
        # 计算需要等多久才能补到 weight
        need = weight - self.tokens
        wait = need / self.refill_per_sec
        time.sleep(wait)
        # 等完后强制补满 (实际上时间应该已经够)
        self._refill()
        self.tokens = max(0.0, self.tokens - weight)
        return True

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self.last_refill = now


# ============================================================================
# BinanceClient
# ============================================================================

class BinanceClient:
    """Binance Futures (USDT-M) 客户端 — Phase 2.1 (只读).

    线程不安全 — 单实例只能由一个线程持有.
    所有方法均带 max 4 次重试 + 指数退避 (2/4/8/16s).

    使用 _signed_request 的方法 (account/positions/orders) 会自动加 timestamp/signature.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        timeout: float = 10.0,
        recv_window_ms: int = RECV_WINDOW_MS,
        dry_run: bool = True,
    ):
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key 必须是非空字符串")
        if not api_secret or not isinstance(api_secret, str):
            raise ValueError("api_secret 必须是非空字符串")
        if recv_window_ms <= 0 or recv_window_ms > 60000:
            raise ValueError("recv_window_ms 必须在 (0, 60000] 范围")
        # 防御: 检测常见的占位符 / 误拷贝模式 (在签名前发现, 比 401 友好)
        _validate_credential(api_key, "api_key")
        _validate_credential(api_secret, "api_secret")
        self._api_key = api_key
        # 保留 bytes 形式给 HMAC 用. 不暴露 __dict__ 外.
        self._api_secret_bytes = api_secret.encode("utf-8")
        self.testnet = bool(testnet)
        self.base_url = TESTNET_BASE if self.testnet else MAINNET_BASE
        self.timeout = float(timeout)
        self.recv_window_ms = int(recv_window_ms)
        # Phase 2.2: dry_run 模式. 默认 True (即使 testnet, 防误操作).
        # 必须显式 dry_run=False 才下真单. mainnet 还要 ~/.allow-live 文件.
        self.dry_run = bool(dry_run)
        self._bucket = _TokenBucket(
            capacity=RATE_LIMIT_WEIGHT_PER_MIN,
            refill_per_sec=RATE_LIMIT_WEIGHT_PER_MIN / RATE_LIMIT_WINDOW_SEC,
        )
        # 时钟漂移 (本地 + offset ≈ 服务器). 单位 ms.
        self._time_offset_ms: int = 0
        self._last_time_sync_monotonic: float = 0.0

    # --- 安全: 永不打 secret ---

    def __repr__(self) -> str:
        # 仅显示 API key 前 6 位 + 总长度, 永不显示 secret
        masked = self._api_key[:6] + "***" if len(self._api_key) > 6 else "***"
        return f"<BinanceClient testnet={self.testnet} api_key={masked}>"

    def __str__(self) -> str:
        return self.__repr__()

    # ============================================================
    # Public API methods (不需要签名)
    # ============================================================

    def get_server_time(self) -> int:
        """返回 Binance 服务器时间 (ms)."""
        data = self._public_request("GET", "/fapi/v1/time", {})
        return int(data["serverTime"])

    def check_time_drift(self, sync: bool = True) -> float:
        """检查本地 vs 服务器时钟漂移. 返回漂移秒数 (正值=本地超前).

        如果 sync=True, 自动更新 _time_offset_ms 用于后续签名请求.
        """
        before = self._now_ms()
        server_ms = self.get_server_time()
        after = self._now_ms()
        # round-trip 中点估计本地时刻 (RTT 修正)
        local_mid_ms = (before + after) // 2
        drift_ms = local_mid_ms - server_ms
        if sync:
            self._time_offset_ms = -drift_ms  # local + offset = server
            self._last_time_sync_monotonic = time.monotonic()
        return drift_ms / 1000.0

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> list:
        """获取历史 K 线 (无需签名).
        interval: 1m/5m/15m/1h/4h/1d ...
        """
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": int(limit),
        }
        return self._public_request("GET", "/fapi/v1/klines", params)

    def get_book_ticker(self, symbol: str) -> dict:
        """获取盘口最优买卖价 (实时, <1s 延迟). 用于开仓前精确预滑点检测.
        相比 get_klines(1m).close (可滞后 0-60s), 盘口价能在快速行情里给出真实成交价基准.
        Returns: {"symbol": "...", "bidPrice": "...", "bidQty": "...",
                  "askPrice": "...", "askQty": "...", "time": ..., "lastUpdateId": ...}
        """
        params = {"symbol": symbol.upper()}
        return self._public_request("GET", "/fapi/v1/ticker/bookTicker", params)

    def get_exchange_info(self) -> dict:
        """获取所有 symbol 的交易规则 (minNotional / minQty / stepSize / 等)."""
        return self._public_request("GET", "/fapi/v1/exchangeInfo", {})

    def get_symbol_filters(self, symbol: str) -> dict:
        """提取该 symbol 的交易精度规则. Returns dict:
            {
              'step_size': 0.001,      # LOT_SIZE 数量步长
              'min_qty': 0.001,        # LOT_SIZE 最小数量
              'max_qty': 1000.0,       # LOT_SIZE 最大 (limit order)
              'market_max_qty': 100.0, # MARKET_LOT_SIZE 最大 (market order, 通常 < max_qty)
              'min_notional': 100.0,   # MIN_NOTIONAL 最小订单总额
              'tick_size': 0.10,       # PRICE_FILTER 价格步长
              'quantity_precision': 3, # 数量小数位
              'price_precision': 2,    # 价格小数位
            }
        测试网 vs 主网过滤器可能不同, 启动时必查.

        Phase 5.A-fix (5/28): 新增 market_max_qty. DYMUSDT 案例显示 MARKET_LOT_SIZE
        maxQty 10000 远小于 LOT_SIZE maxQty 1000000. 用 MARKET_LOT_SIZE 来防 -4005.
        """
        ei = self.get_exchange_info()
        sym = symbol.upper()
        sym_info = next((s for s in ei.get("symbols", []) if s.get("symbol") == sym), None)
        if sym_info is None:
            raise BinanceError(f"symbol {sym} 不在 exchangeInfo 中 (可能下架或拼写错)")
        if sym_info.get("status") != "TRADING":
            log.warning(f"⚠️ {sym} status={sym_info.get('status')} 非 TRADING")
        filters = {f["filterType"]: f for f in sym_info.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        market_lot = filters.get("MARKET_LOT_SIZE", {})
        notional = filters.get("MIN_NOTIONAL", {}) or filters.get("NOTIONAL", {})
        price_f = filters.get("PRICE_FILTER", {})
        # market_max_qty: 若 MARKET_LOT_SIZE 不存在 fallback 到 LOT_SIZE.
        market_max = float(market_lot.get("maxQty", 0)) or float(lot.get("maxQty", 0))
        return {
            "step_size":           float(lot.get("stepSize", 0.001)),
            "min_qty":             float(lot.get("minQty", 0.001)),
            "max_qty":             float(lot.get("maxQty", 0)),
            "market_max_qty":      market_max,
            "min_notional":        float(notional.get("notional", 0) or notional.get("minNotional", 0)),
            "tick_size":           float(price_f.get("tickSize", 0.01)),
            "quantity_precision":  int(sym_info.get("quantityPrecision", 3)),
            "price_precision":     int(sym_info.get("pricePrecision", 2)),
            "status":              sym_info.get("status"),
        }

    # ============================================================
    # Signed API methods (需要签名)
    # ============================================================

    def get_account(self) -> dict:
        """获取账户信息 (余额 / 总仓位 / 杠杆等)."""
        return self._signed_request("GET", "/fapi/v2/account", {})

    def get_positions(self) -> list:
        """获取当前持仓 (positionRisk endpoint).

        返回所有 symbol 的持仓信息. 空仓 positionAmt = "0".
        客户端应过滤 positionAmt != 0 的项.
        """
        return self._signed_request("GET", "/fapi/v2/positionRisk", {})

    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """获取挂单. symbol=None 时返回所有."""
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._signed_request("GET", "/fapi/v1/openOrders", params)

    def get_balance(self) -> list:
        """获取每个币种的余额 (USDT 等)."""
        return self._signed_request("GET", "/fapi/v2/balance", {})

    # ============================================================
    # Phase 2.2.1: 订单操作 (写) + 账户配置
    # ============================================================

    def _check_live_authorization(self, dry_run: bool) -> None:
        """实下单前的安全闸门. dry_run=True 直接放行.

        非 dry_run 时:
        - testnet: 放行 (testnet 没真钱)
        - mainnet: 必须存在 ~/.allow-live 文件 (二级授权)
        """
        if dry_run:
            return
        if self.testnet:
            return
        allow_file = Path.home() / ".allow-live"
        if not allow_file.exists():
            raise BinanceError(
                "🛑 主网实盘下单需要 ~/.allow-live 文件存在 (二级安全闸门). "
                "明确创建: touch ~/.allow-live (创建后随时 rm 可一键禁用)."
            )

    def _is_dry_run(self, override: Optional[bool]) -> bool:
        """方法级 dry_run 参数 None 时回退到实例级配置."""
        return self.dry_run if override is None else bool(override)

    @staticmethod
    def _dry_run_response(method_name: str, params: dict) -> dict:
        """构造与真实响应类似的 mock dict, status='DRY_RUN' 便于上层识别."""
        # 真实响应 orderId 是 long int; 用负数防混淆
        return {
            "orderId": -1,
            "status": "DRY_RUN",
            "clientOrderId": params.get("newClientOrderId", ""),
            "symbol": params.get("symbol", ""),
            "side": params.get("side", ""),
            "type": params.get("type", ""),
            "origQty": str(params.get("quantity", "0")),
            "stopPrice": str(params.get("stopPrice", "0")),
            "reduceOnly": params.get("reduceOnly", False),
            "closePosition": params.get("closePosition", False),
            "_method": method_name,
            "_dryRun": True,
            "_params": params,
        }

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        *,
        client_order_id: Optional[str] = None,
        reduce_only: bool = False,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """市价单. side: 'BUY' 开多 / 'SELL' 开空 (one-way mode 下 positionSide=BOTH).
        reduce_only=True 表示仅平仓不开新仓.
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须是 BUY 或 SELL, got {side!r}")
        if quantity <= 0:
            raise ValueError(f"quantity 必须 > 0, got {quantity}")
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        params = {
            "symbol": symbol.upper(),
            "side": side,
            "type": "MARKET",
            "quantity": _format_quantity(quantity),
            "newOrderRespType": "RESULT",  # 返回 fill 信息
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            _validate_client_order_id(client_order_id)
            params["newClientOrderId"] = client_order_id
        if is_dry:
            log.info(f"[DRY_RUN] place_market_order {symbol} {side} qty={quantity}")
            return self._dry_run_response("place_market_order", params)
        return self._signed_request("POST", "/fapi/v1/order", params)

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        *,
        time_in_force: str = "IOC",
        client_order_id: Optional[str] = None,
        reduce_only: bool = False,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """限价单 (默认 IOC: 立即成交否则自动取消).

        IOC 模式下发单挂 limit @ price: 若当前 ask ≤ price 立即成交;
        若价格已超出则取消 (status=EXPIRED) — 上层捕获后记 missed_signal 重试.
        相比市价单: 入场价有硬上限, 不会在快速行情里吃到超预期的价格.

        time_in_force: "IOC" (默认) | "GTC" | "FOK" | "GTX"
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须 BUY 或 SELL, got {side!r}")
        if quantity <= 0:
            raise ValueError(f"quantity 必须 > 0, got {quantity}")
        if price <= 0:
            raise ValueError(f"price 必须 > 0, got {price}")
        if time_in_force not in ("GTC", "IOC", "FOK", "GTX"):
            raise ValueError(f"time_in_force 无效: {time_in_force!r}")
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        params = {
            "symbol":       symbol.upper(),
            "side":         side,
            "type":         "LIMIT",
            "timeInForce":  time_in_force,
            "quantity":     _format_quantity(quantity),
            "price":        _format_price(price),
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            _validate_client_order_id(client_order_id)
            params["newClientOrderId"] = client_order_id
        if is_dry:
            log.info(
                f"[DRY_RUN] place_limit_order {symbol} {side} "
                f"qty={quantity} price={price} tif={time_in_force}"
            )
            return self._dry_run_response("place_limit_order", params)
        return self._signed_request("POST", "/fapi/v1/order", params)

    def place_stop_market_order(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        *,
        quantity: Optional[float] = None,
        close_position: bool = False,
        client_order_id: Optional[str] = None,
        working_type: Optional[str] = None,
        price_protect: bool = False,
        response_type: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """STOP_MARKET 止损单.

        默认参数极简 — 只发送必需字段, 避免 Binance 对某些可选字段的严格校验.

        close_position 模式 (close_position=True): 不需 quantity, 触发平整仓.
        quantity 模式 (close_position=False, 默认): 需指定 quantity + reduceOnly=true.

        side 是 *平仓方向*: LONG 持仓的 SL = SELL; SHORT 持仓的 SL = BUY.

        可选参数:
        - working_type: None (默认 = 不发送, 让 Binance 用 CONTRACT_PRICE)
          可显式 'CONTRACT_PRICE' / 'MARK_PRICE'
        - price_protect: 防插针 (默认 False)
        - response_type: None (默认 = 不发送, 让 Binance 用 ACK 适合 pending 单)
          可显式 'ACK' / 'RESULT' / 'FULL'
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须是 BUY 或 SELL, got {side!r}")
        if stop_price <= 0:
            raise ValueError(f"stop_price 必须 > 0, got {stop_price}")
        if working_type is not None and working_type not in ("CONTRACT_PRICE", "MARK_PRICE"):
            raise ValueError(f"working_type 必须是 CONTRACT_PRICE 或 MARK_PRICE 或 None")
        if response_type is not None and response_type not in ("ACK", "RESULT", "FULL"):
            raise ValueError(f"response_type 必须是 ACK/RESULT/FULL 或 None")
        # close_position=True 和 quantity 互斥
        if close_position and quantity is not None:
            raise ValueError("close_position=True 时不能同时指定 quantity")
        if not close_position and quantity is None:
            raise ValueError("close_position=False 时必须指定 quantity")
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        # 极简参数集 — 只放必需字段, 其他全部 opt-in
        params = {
            "symbol": symbol.upper(),
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": _format_price(stop_price),
        }
        if working_type is not None:
            params["workingType"] = working_type
        if price_protect:
            params["priceProtect"] = "true"
        if response_type is not None:
            params["newOrderRespType"] = response_type
        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = _format_quantity(quantity)
            params["reduceOnly"] = "true"  # 非 closePosition 时必须 reduceOnly
        if client_order_id:
            _validate_client_order_id(client_order_id)
            params["newClientOrderId"] = client_order_id
        if is_dry:
            log.info(f"[DRY_RUN] place_stop_market {symbol} {side} stop={stop_price} "
                     f"closePos={close_position}")
            return self._dry_run_response("place_stop_market_order", params)
        return self._signed_request("POST", "/fapi/v1/order", params)

    def cancel_order(
        self,
        symbol: str,
        *,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """撤单. 用 order_id 或 client_order_id 任一."""
        if order_id is None and client_order_id is None:
            raise ValueError("必须提供 order_id 或 client_order_id 之一")
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        params = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        if is_dry:
            log.info(f"[DRY_RUN] cancel_order {symbol} order_id={order_id} "
                     f"client_id={client_order_id}")
            return self._dry_run_response("cancel_order", params)
        return self._signed_request("DELETE", "/fapi/v1/order", params)

    def get_order(
        self,
        symbol: str,
        *,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """查询单笔订单状态. 用 order_id 或 client_order_id 任一. 只读, 无 dry_run."""
        if order_id is None and client_order_id is None:
            raise ValueError("必须提供 order_id 或 client_order_id 之一")
        params = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        return self._signed_request("GET", "/fapi/v1/order", params)

    def get_user_trades(
        self,
        symbol: str,
        *,
        order_id: Optional[int] = None,
        limit: int = 500,
    ) -> list:
        """查询账户实际成交记录 (含真实 commission/手续费). 只读, 无 dry_run.

        Binance Futures /fapi/v1/userTrades. 每条 fill 含 commission +
        commissionAsset (USDT 或 BNB-discount 模式下 BNB).

        order_id 给定时只返该订单的 fills, 否则返该 symbol 最近 N 条.
        """
        if limit < 1 or limit > 1000:
            raise ValueError(f"limit 必须在 [1, 1000], got {limit}")
        params = {"symbol": symbol.upper(), "limit": int(limit)}
        if order_id is not None:
            params["orderId"] = int(order_id)
        result = self._signed_request("GET", "/fapi/v1/userTrades", params)
        # API 返 list (空 list 合法 — 订单尚未 settle / 无该 symbol fill)
        if not isinstance(result, list):
            log.warning(f"get_user_trades unexpected resp type: {type(result)}")
            return []
        return result

    def _actual_commission_usdt(
        self, symbol: str, order_id: int,
    ) -> Optional[float]:
        """汇总指定订单的真实 commission (USDT). 返回 None 表示取不到 (调用方应 fallback).

        BNB-discount (commissionAsset=BNB) 情况: 本版本暂不换算, 返回 None
        让调用方 fallback 到估算. 实盘启用 BNB 折扣前再扩展.
        """
        try:
            fills = self.get_user_trades(symbol, order_id=order_id)
        except (BinanceError, ValueError) as e:
            log.warning(
                f"[fee] get_user_trades failed for "
                f"{symbol} order_id={order_id}: {e}"
            )
            return None
        if not fills:
            log.warning(
                f"[fee] no fills for {symbol} order_id={order_id} "
                f"(订单未 settle 或 API 延迟?)"
            )
            return None
        total = 0.0
        for f in fills:
            try:
                asset = (f.get("commissionAsset") or "").upper()
                comm = float(f.get("commission") or 0)
            except (ValueError, TypeError):
                continue
            if asset == "USDT":
                total += comm
            else:
                # 非 USDT 计费 (e.g. BNB-discount). 本版本不换算 → 整单回退估算.
                log.warning(
                    f"[fee] {symbol} order_id={order_id}: "
                    f"commissionAsset={asset} (非 USDT), 暂不支持换算, 回退估算"
                )
                return None
        return round(total, 6)

    # --- 账户配置 (启动前检查) ---

    def set_leverage(self, symbol: str, leverage: int,
                     dry_run: Optional[bool] = None) -> dict:
        """设置 symbol 杠杆 (1-125). 推荐 3x."""
        if not (1 <= int(leverage) <= 125):
            raise ValueError(f"leverage 必须在 [1, 125], got {leverage}")
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        params = {"symbol": symbol.upper(), "leverage": int(leverage)}
        if is_dry:
            log.info(f"[DRY_RUN] set_leverage {symbol}={leverage}x")
            return {"_dryRun": True, "leverage": leverage, "symbol": symbol.upper()}
        return self._signed_request("POST", "/fapi/v1/leverage", params)

    def set_margin_type(self, symbol: str, margin_type: str,
                        dry_run: Optional[bool] = None) -> dict:
        """设置保证金模式. margin_type ∈ {'ISOLATED', 'CROSSED'}.
        注意: 该 symbol 必须无持仓时才能切.
        """
        margin_type = margin_type.upper()
        if margin_type not in ("ISOLATED", "CROSSED"):
            raise ValueError(f"margin_type ∈ {{ISOLATED, CROSSED}}, got {margin_type!r}")
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        params = {"symbol": symbol.upper(), "marginType": margin_type}
        if is_dry:
            log.info(f"[DRY_RUN] set_margin_type {symbol}={margin_type}")
            return {"_dryRun": True, "marginType": margin_type, "symbol": symbol.upper()}
        # Binance 在已是该模式时返回 code -4046 "No need to change margin type"
        # 我们把这视为成功 (幂等)
        try:
            return self._signed_request("POST", "/fapi/v1/marginType", params)
        except BinanceError as e:
            if "-4046" in str(e):
                return {"alreadySet": True, "marginType": margin_type, "symbol": symbol.upper()}
            raise

    def get_position_mode(self) -> dict:
        """查询持仓模式. dualSidePosition=true 是 Hedge mode, false 是 One-way (推荐)."""
        return self._signed_request("GET", "/fapi/v1/positionSide/dual", {})

    def set_position_mode(self, dual_side: bool,
                          dry_run: Optional[bool] = None) -> dict:
        """设置持仓模式. dual_side=False = One-way (推荐, 匹配 paper trader 模型).
        注意: 必须所有 symbol 无持仓 + 无挂单时才能切.
        """
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        params = {"dualSidePosition": "true" if dual_side else "false"}
        if is_dry:
            log.info(f"[DRY_RUN] set_position_mode dual_side={dual_side}")
            return {"_dryRun": True, "dualSidePosition": dual_side}
        try:
            return self._signed_request("POST", "/fapi/v1/positionSide/dual", params)
        except BinanceError as e:
            if "-4059" in str(e):  # No need to change
                return {"alreadySet": True, "dualSidePosition": dual_side}
            raise

    def verify_setup(self, symbol: str, *, expected_leverage: int = 3,
                     expected_margin: str = "ISOLATED",
                     expected_dual_side: bool = False) -> dict:
        """启动 preflight: 检查账户配置是否符合预期. 返回问题列表 (空=OK).
        不修改任何设置, 只读检查.

        返回示例:
            {"ok": True, "issues": [], "current": {...}}
            {"ok": False, "issues": ["leverage 20x != 3x", ...], "current": {...}}
        """
        issues = []
        current = {}
        # 1. 持仓模式 (账户级)
        pm = self.get_position_mode()
        current["dualSidePosition"] = pm.get("dualSidePosition")
        if pm.get("dualSidePosition") != expected_dual_side:
            issues.append(
                f"持仓模式 dualSidePosition={pm.get('dualSidePosition')} "
                f"!= 期望 {expected_dual_side} (One-way mode)"
            )
        # 2. 该 symbol 的杠杆 + 保证金模式 (在 positionRisk 里)
        positions = self.get_positions()
        sym_info = next((p for p in positions if p.get("symbol") == symbol.upper()), None)
        if sym_info is None:
            issues.append(f"{symbol} 不在 positionRisk 列表里 (symbol 不存在?)")
        else:
            current["leverage"] = sym_info.get("leverage")
            current["marginType"] = sym_info.get("marginType", "").upper()
            try:
                lev = int(sym_info.get("leverage", 0))
                if lev != expected_leverage:
                    issues.append(f"{symbol} leverage={lev}x != 期望 {expected_leverage}x")
            except (TypeError, ValueError):
                issues.append(f"{symbol} leverage 字段无法解析: {sym_info.get('leverage')}")
            mt = (sym_info.get("marginType") or "").upper()
            # Binance 在 positionRisk 里返回 'isolated'/'cross', 不是 'CROSSED'
            mt_normalized = "ISOLATED" if mt == "ISOLATED" else "CROSSED"
            if mt_normalized != expected_margin.upper():
                issues.append(f"{symbol} marginType={mt} != 期望 {expected_margin}")
        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "current": current,
            "symbol": symbol.upper(),
        }

    # ============================================================
    # Phase 2.2.2: 高阶 trade lifecycle (open / update_stop / close)
    # ============================================================

    def open_position(
        self,
        symbol: str,
        side: str,
        notional_usdt: float,
        sl_price: float,
        *,
        trade_id: str,
        limit_price: Optional[float] = None,
        use_exchange_sl: bool = True,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """开仓 + (可选) 立挂 SL 的原子操作.

        side='BUY' → LONG, side='SELL' → SHORT (one-way mode)

        limit_price (Phase 4.V): 若提供, 用 IOC 限价单代替市价单.
            填写调用方已拿到的盘口 ask/bid 价, 实现"成交则在此价, 超出则取消重试".
            IOC 未成交 (EXPIRED) → 返 None → 上层记 missed_signal 下 tick 重试.
            None (默认): 回退到市价单 (旧行为, 完全向后兼容).

        use_exchange_sl=True (默认): 挂 STOP_MARKET 到 exchange. 失败 → rollback.
        use_exchange_sl=False: 只开仓, sl_price 仅供上层客户端轮询使用.

        失败保护 (use_exchange_sl=True 时):
        - SL 挂单失败 → 立刻反向 market close (rollback)
        - rollback 失败 → raise CRITICAL, 要求人工介入

        Returns dict (见函数末尾), 或 None (IOC 未成交).
        """
        # 1. 入参验证
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须 BUY 或 SELL, got {side!r}")
        if notional_usdt <= 0:
            raise ValueError(f"notional_usdt 必须 > 0, got {notional_usdt}")
        if sl_price <= 0:
            raise ValueError(f"sl_price 必须 > 0, got {sl_price}")
        _validate_trade_id(trade_id)
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        symbol = symbol.upper()

        # 2. 拉 symbol filters
        filters = self.get_symbol_filters(symbol)

        # 3. 拉当前价 (limit_price 已提供时跳过 kline 调用, 直接用盘口价)
        if limit_price is not None and limit_price > 0:
            last_close = limit_price
        else:
            klines = self.get_klines(symbol, interval="1m", limit=1)
            if not klines:
                raise BinanceError(f"无法获取 {symbol} 当前价")
            last_close = float(klines[0][4])

        # 4. 验证 SL 价位于持仓方向相反侧
        if side == "BUY":  # LONG: SL 必须 < 当前价
            if sl_price >= last_close:
                raise ValueError(
                    f"LONG sl_price={sl_price} 必须 < 当前价 {last_close} "
                    f"(否则立即触发)"
                )
        else:              # SHORT: SL 必须 > 当前价
            if sl_price <= last_close:
                raise ValueError(
                    f"SHORT sl_price={sl_price} 必须 > 当前价 {last_close}"
                )

        # 5. 计算 qty (round to step + 满足 min_qty / min_notional)
        raw_qty = notional_usdt / last_close
        qty = round_qty_down_to_step(raw_qty, filters["step_size"])
        if qty < filters["min_qty"]:
            qty = filters["min_qty"]
        actual_notional = qty * last_close
        if filters["min_notional"] > 0 and actual_notional < filters["min_notional"]:
            need = filters["min_notional"] / last_close
            qty = math.ceil(need / filters["step_size"]) * filters["step_size"]
            qty = round(qty, filters["quantity_precision"])
            actual_notional = qty * last_close
        # Phase 5.A-fix (5/28): cap qty 不超过 MARKET_LOT_SIZE maxQty.
        # DYMUSDT 案例: $800 / $0.044 = 18075, 超 maxQty 10000 → 平仓时 -4005 死循环.
        # 截断到 maxQty (= 实际 notional 缩水), 比开成无法平的仓更安全.
        market_max = float(filters.get("market_max_qty") or 0)
        if market_max > 0 and qty > market_max:
            old_qty = qty
            qty = round_qty_down_to_step(market_max, filters["step_size"])
            actual_notional = qty * last_close
            log.warning(
                f"open_position: {symbol} qty {old_qty} > market_max {market_max}, "
                f"截断到 {qty} (实际 notional ${actual_notional:.2f}, 原 ${notional_usdt:.2f}). "
                f"防止后续平仓 -4005 死循环."
            )

        # 6. Round SL 到 tick_size; 若用限价单也对入场价做 tick 对齐
        sl_price = round_price_to_tick(sl_price, filters["tick_size"])
        if limit_price is not None and limit_price > 0:
            limit_price = round_price_to_tick(limit_price, filters["tick_size"])

        # 7. 构造 client_order_ids
        entry_coid = f"cresus_{trade_id}_E"
        sl_coid = f"cresus_{trade_id}_SL"
        _validate_client_order_id(entry_coid)
        _validate_client_order_id(sl_coid)
        sl_side = "SELL" if side == "BUY" else "BUY"

        # 8. Dry-run: 模拟返回
        if is_dry:
            log.info(
                f"[DRY_RUN] open_position {symbol} {side} qty={qty} "
                f"~${actual_notional:.2f} sl={sl_price}"
            )
            return {
                "trade_id": trade_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "avg_fill_price": last_close,   # mock (实盘时是真实 fill)
                "requested_notional": notional_usdt,
                "actual_notional": actual_notional,
                "entry_order_id": -1,
                "entry_client_id": entry_coid,
                "sl_price": sl_price,
                "sl_side": sl_side,
                "sl_order_id": -1,
                "sl_client_id": sl_coid,
                "sl_mode": "exchange" if use_exchange_sl else "client_side",
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "fees_paid_usdt": 0.0,
                "fees_are_actual": False,
                "_dryRun": True,
            }

        # ====== 实盘路径 ======

        # 9. 开仓单: IOC 限价 (limit_price 已提供) 或市价单 (默认)
        if limit_price is not None:
            log.info(
                f"open_position step 1/2: IOC LIMIT {entry_coid} "
                f"{side} {qty}@{limit_price} {symbol}"
            )
            entry_resp = self.place_limit_order(
                symbol=symbol, side=side, quantity=qty,
                price=limit_price, time_in_force="IOC",
                client_order_id=entry_coid, dry_run=False,
            )
            entry_status = entry_resp.get("status")
            executed_qty = float(entry_resp.get("executedQty") or 0)
            if entry_status == "EXPIRED":
                if executed_qty > 0:
                    # Phase 5.H (5/30) CRITICAL FIX: IOC 部分成交孤儿仓 root cause.
                    # status=EXPIRED + executedQty>0 = 一部分成交了, 剩余 expire.
                    # 之前直接 return None 让 caller "认为没开仓", 但 exchange 上
                    # 有 partial position → 持续累积成孤儿 (5/28-5/30 14 个孤儿 root).
                    # 修复: 立即用 close_position (reduce_only market) 平 partial,
                    # 然后再 return None 保持 caller 语义不变.
                    log.warning(
                        f"[open_position] IOC partial fill: executed {executed_qty}/{qty} "
                        f"@ avgPrice={entry_resp.get('avgPrice')}. 立即应急平 partial position "
                        f"防孤儿."
                    )
                    try:
                        self.close_position(symbol=symbol, side=side, dry_run=False)
                        log.info(
                            f"[open_position] IOC partial fill 已应急平仓: {symbol}"
                        )
                    except (BinanceError, ValueError) as ce:
                        log.error(
                            f"[open_position] CRITICAL: IOC partial fill 应急平失败 "
                            f"({type(ce).__name__}: {ce}). 需要手动平 {symbol} "
                            f"executed_qty={executed_qty}!"
                        )
                else:
                    log.info(
                        f"[open_position] IOC limit EXPIRED — 价格已越过 {limit_price}, "
                        f"将由上层下 tick 重试"
                    )
                return None
        else:
            log.info(f"open_position step 1/2: MARKET {entry_coid} {side} {qty} {symbol}")
            entry_resp = self.place_market_order(
                symbol=symbol, side=side, quantity=qty,
                client_order_id=entry_coid, dry_run=False,
            )
        entry_status = entry_resp.get("status")
        if entry_status not in ("FILLED", "PARTIALLY_FILLED"):
            raise BinanceError(
                f"Entry 未成交 status={entry_status} resp={entry_resp}"
            )
        avg_fill_price = float(entry_resp.get("avgPrice") or 0)
        executed_qty = float(entry_resp.get("executedQty") or 0)
        cum_quote = float(entry_resp.get("cumQuote") or 0)
        entry_order_id_int = int(entry_resp.get("orderId") or 0)
        # 实际 commission — 调 /fapi/v1/userTrades. 失败回退到估算 (0.04% taker).
        fee_estimate = round(cum_quote * 0.0004, 4)
        actual_fee = None
        if entry_order_id_int > 0:
            actual_fee = self._actual_commission_usdt(symbol, entry_order_id_int)
        entry_fee = actual_fee if actual_fee is not None else fee_estimate
        fee_is_actual = actual_fee is not None

        if executed_qty <= 0:
            raise BinanceError(f"Entry executed_qty={executed_qty}, abnormal")

        # 10. 下 STOP_MARKET SL (除非 use_exchange_sl=False)
        if not use_exchange_sl:
            # Client-side SL 模式: 不挂 exchange, 仅返回 sl_price 供上层轮询
            log.info(
                f"open_position done (no exchange SL): {symbol} {side} qty={executed_qty} "
                f"@ {avg_fill_price} SL(client-side)={sl_price}"
            )
            return {
                "trade_id": trade_id,
                "symbol": symbol,
                "side": side,
                "qty": executed_qty,
                "avg_fill_price": avg_fill_price,
                "requested_notional": notional_usdt,
                "actual_notional": cum_quote,
                "entry_order_id": int(entry_resp.get("orderId") or 0),
                "entry_client_id": entry_coid,
                "sl_price": sl_price,             # 上层负责 monitor
                "sl_side": sl_side,
                "sl_order_id": None,              # 没挂 exchange
                "sl_client_id": None,
                "sl_mode": "client_side",         # 标记上层用 polling
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "fees_paid_usdt": entry_fee,
                "fees_are_actual": fee_is_actual,
            }

        log.info(
            f"open_position step 2/2: SL {sl_coid} {sl_side} qty={executed_qty} @ {sl_price}"
        )
        try:
            sl_resp = self.place_stop_market_order(
                symbol=symbol,
                side=sl_side,
                stop_price=sl_price,
                quantity=executed_qty,           # 精确平实际持仓
                close_position=False,            # reduceOnly + quantity 模式
                client_order_id=sl_coid,
                dry_run=False,
            )
        except BinanceError as sl_err:
            # ⚠️ ROLLBACK: SL 失败, 立刻反向 market close
            log.error(
                f"🚨 SL placement failed for {trade_id}: {sl_err}. "
                f"Initiating rollback (reverse market close)..."
            )
            rb_coid = f"cresus_{trade_id}_RB"
            try:
                rb_resp = self.place_market_order(
                    symbol=symbol,
                    side=sl_side,                # 反向
                    quantity=executed_qty,
                    client_order_id=rb_coid,
                    reduce_only=True,
                    dry_run=False,
                )
                log.info(
                    f"Rollback OK: closed at {rb_resp.get('avgPrice')} "
                    f"(entry was {avg_fill_price})"
                )
            except BinanceError as rb_err:
                # 灾难情景: rollback 也失败, 持仓裸奔
                raise BinanceError(
                    f"🚨🚨 CRITICAL: SL failed AND rollback failed for {trade_id}. "
                    f"POSITION IS NAKED, manual intervention required. "
                    f"entry_order={entry_resp.get('orderId')} "
                    f"sl_err={sl_err} rb_err={rb_err}"
                )
            # rollback 成功, 抛原始 SL 错
            raise BinanceError(
                f"SL placement failed for {trade_id}, position auto-closed. "
                f"trade_id={trade_id} err={sl_err}"
            )

        # 11. 一切成功, 返回 trade dict
        result = {
            "trade_id": trade_id,
            "symbol": symbol,
            "side": side,
            "qty": executed_qty,
            "avg_fill_price": avg_fill_price,
            "requested_notional": notional_usdt,
            "actual_notional": cum_quote,
            "entry_order_id": int(entry_resp.get("orderId") or 0),
            "entry_client_id": entry_coid,
            "sl_price": sl_price,
            "sl_side": sl_side,
            "sl_order_id": int(sl_resp.get("orderId") or 0),
            "sl_client_id": sl_coid,
            "sl_mode": "exchange",
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "fees_paid_usdt": entry_fee,
            "fees_are_actual": fee_is_actual,
        }
        log.info(
            f"✓ open_position done: {symbol} {side} qty={executed_qty} "
            f"@ {avg_fill_price} SL={sl_price}"
        )
        return result

    def update_stop_order(
        self,
        symbol: str,
        new_stop_price: float,
        *,
        old_sl_client_order_id: str,
        new_sl_client_order_id: str,
        sl_side: str,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """原子地把 SL 从一个价格移到另一个价格 (place-then-cancel).

        sl_side: 平仓方向 ('SELL' for LONG position, 'BUY' for SHORT).

        核心原则: 新 SL 挂上之前永远不撤旧 SL — 不允许"无 SL 时刻".

        Race window:
        - T1: 新 SL 挂上, 旧 SL 仍在 → 双重保护
        - T2: cancel 旧 SL
        - 若 cancel 失败: 新 SL 已在保护, 仅日志 warning (不抛错)
        """
        # 验证
        sl_side = sl_side.upper()
        if sl_side not in ("BUY", "SELL"):
            raise ValueError(f"sl_side 必须 BUY 或 SELL, got {sl_side!r}")
        if new_stop_price <= 0:
            raise ValueError(f"new_stop_price 必须 > 0")
        if old_sl_client_order_id == new_sl_client_order_id:
            raise ValueError("new_sl_client_order_id 必须与 old 不同")
        _validate_client_order_id(old_sl_client_order_id)
        _validate_client_order_id(new_sl_client_order_id)
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        symbol = symbol.upper()

        # round 到 tick
        filters = self.get_symbol_filters(symbol)
        new_stop_price = round_price_to_tick(new_stop_price, filters["tick_size"])

        # 验证 new_stop_price 不会立即触发 (相对当前价)
        klines = self.get_klines(symbol, interval="1m", limit=1)
        last_close = float(klines[0][4])
        if sl_side == "SELL":   # LONG SL: stop < current
            if new_stop_price >= last_close:
                raise ValueError(
                    f"SELL SL new={new_stop_price} 必须 < 当前价 {last_close}"
                )
        else:                    # SHORT SL: stop > current
            if new_stop_price <= last_close:
                raise ValueError(
                    f"BUY SL new={new_stop_price} 必须 > 当前价 {last_close}"
                )

        if is_dry:
            log.info(
                f"[DRY_RUN] update_stop_order {symbol} {sl_side} "
                f"new_sl={new_stop_price} (old: {old_sl_client_order_id} → "
                f"new: {new_sl_client_order_id})"
            )
            return {
                "symbol": symbol,
                "new_sl_price": new_stop_price,
                "new_sl_order_id": -1,
                "new_sl_client_id": new_sl_client_order_id,
                "old_sl_client_id": old_sl_client_order_id,
                "old_sl_canceled": True,
                "sl_side": sl_side,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "_dryRun": True,
            }

        # ====== 实盘: place-then-cancel ======

        # 1. 先 place 新 SL (用 quantity-based 模式, 查实际持仓 size)
        positions = self.get_positions()
        pos = next((p for p in positions if p.get("symbol") == symbol), None)
        if pos is None or float(pos.get("positionAmt", 0)) == 0:
            raise BinanceError(
                f"update_stop_order {symbol}: 无开仓, 无法移 SL"
            )
        pos_qty = abs(float(pos.get("positionAmt", 0)))
        log.info(
            f"update_stop_order step 1/2: place NEW SL {new_sl_client_order_id} "
            f"qty={pos_qty} @ {new_stop_price}"
        )
        new_sl_resp = self.place_stop_market_order(
            symbol=symbol,
            side=sl_side,
            stop_price=new_stop_price,
            quantity=pos_qty,
            close_position=False,
            client_order_id=new_sl_client_order_id,
            dry_run=False,
        )

        # 2. cancel 旧 SL (容忍失败 — 新 SL 已生效)
        old_canceled = False
        try:
            log.info(
                f"update_stop_order step 2/2: cancel OLD SL "
                f"{old_sl_client_order_id}"
            )
            self.cancel_order(
                symbol, client_order_id=old_sl_client_order_id, dry_run=False,
            )
            old_canceled = True
        except BinanceError as e:
            # 旧 SL cancel 失败: 可能已经被外部撤掉, 或者 race 触发了
            # 新 SL 在保护, 不影响安全, 只 warning
            log.warning(
                f"⚠️ Old SL {old_sl_client_order_id} cancel 失败: {e}. "
                f"新 SL 已在保护, 继续."
            )

        return {
            "symbol": symbol,
            "new_sl_price": new_stop_price,
            "new_sl_order_id": int(new_sl_resp.get("orderId") or 0),
            "new_sl_client_id": new_sl_client_order_id,
            "old_sl_client_id": old_sl_client_order_id,
            "old_sl_canceled": old_canceled,
            "sl_side": sl_side,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def close_position(
        self,
        symbol: str,
        side: str,
        *,
        trade_id: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> dict:
        """主动平仓 (timeout / 手动 kill / TP2 触发 / 等).

        side: 持仓的开仓方向 (BUY=LONG, SELL=SHORT). 平仓方向自动反.

        流程:
        1. 拉持仓, 验证存在且方向匹配
        2. 撤该 symbol 所有挂单 (清理残留 SL/TP)
        3. reduceOnly market 平仓
        4. 计算 realized PnL
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须 BUY 或 SELL")
        if trade_id is not None:
            _validate_trade_id(trade_id)
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        symbol = symbol.upper()
        close_side = "SELL" if side == "BUY" else "BUY"

        if is_dry:
            log.info(f"[DRY_RUN] close_position {symbol} {side} (close_side={close_side})")
            return {
                "symbol": symbol,
                "side": side,
                "qty_closed": 0.0,
                "entry_price": 0.0,
                "avg_exit_price": 0.0,
                "realized_pnl_usdt": 0.0,
                "fees_paid_usdt": 0.0,
                "fees_are_actual": False,
                "close_order_id": -1,
                "open_orders_canceled": 0,
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "_dryRun": True,
            }

        # 1. 拉持仓
        positions = self.get_positions()
        pos = next(
            (p for p in positions if p.get("symbol") == symbol),
            None,
        )
        if pos is None:
            raise BinanceError(f"无 {symbol} 持仓记录")
        pos_amt = float(pos.get("positionAmt") or 0)
        if pos_amt == 0:
            raise BinanceError(f"{symbol} 当前无持仓 (positionAmt=0)")
        # 方向校验
        is_long = pos_amt > 0
        expected_long = (side == "BUY")
        if is_long != expected_long:
            raise BinanceError(
                f"{symbol} 持仓方向不符: 期望 side={side} "
                f"(LONG={expected_long}), 实际 positionAmt={pos_amt}"
            )
        qty = abs(pos_amt)
        entry_price = float(pos.get("entryPrice") or 0)

        # 2. 撤该 symbol 所有挂单 (避免和平仓 race)
        canceled = 0
        try:
            open_orders = self.get_open_orders(symbol)
            for o in open_orders:
                try:
                    self.cancel_order(symbol, order_id=int(o["orderId"]),
                                      dry_run=False)
                    canceled += 1
                except BinanceError as e:
                    log.warning(f"cancel order {o.get('orderId')} 失败: {e}")
        except BinanceError as e:
            log.warning(f"get_open_orders 失败 (继续平仓): {e}")

        # 3. reduceOnly market 平仓
        # Phase 5.A-fix (5/28): 自动 chunking if qty > MARKET_LOT_SIZE maxQty.
        # 案例: DYMUSDT 18075 > maxQty 10000 触发 -4005, 死循环重试. 拆 2 笔可平.
        coid = f"cresus_{trade_id}_C" if trade_id else None
        if coid:
            _validate_client_order_id(coid)
        try:
            filters = self.get_symbol_filters(symbol)
            market_max = float(filters.get("market_max_qty") or 0)
        except (BinanceError, ValueError):
            market_max = 0  # 取不到 fallback 单笔尝试 (老行为)
        # 准备分块列表 (qty <= market_max 直接单笔, 否则按 market_max 拆)
        chunks: list = []
        if market_max > 0 and qty > market_max:
            remaining = qty
            while remaining > market_max:
                chunks.append(market_max)
                remaining = round(remaining - market_max, 8)
            if remaining > 0:
                chunks.append(remaining)
            log.warning(
                f"close_position: {symbol} qty={qty} > market_max={market_max}, "
                f"拆 {len(chunks)} 笔 chunked close: {chunks}"
            )
        else:
            chunks = [qty]

        # 逐笔执行 close (失败立即抛, 上层 _try_mirror_close 处理)
        log.info(
            f"close_position: market {close_side} {qty} {symbol} "
            f"(reduceOnly, canceled {canceled} pending orders, chunks={len(chunks)})"
        )
        # 聚合统计 (跨多笔)
        total_qty_closed = 0.0
        total_cum_quote = 0.0
        last_order_id = 0
        last_resp: dict = {}
        for i, chunk_qty in enumerate(chunks):
            # 多笔时只第一笔用原 coid (Binance 不允许重复 coid)
            chunk_coid = coid if i == 0 else None
            chunk_resp = self.place_market_order(
                symbol=symbol,
                side=close_side,
                quantity=chunk_qty,
                reduce_only=True,
                client_order_id=chunk_coid,
                dry_run=False,
            )
            total_qty_closed += float(chunk_resp.get("executedQty") or chunk_qty)
            total_cum_quote += float(chunk_resp.get("cumQuote") or 0)
            last_order_id = int(chunk_resp.get("orderId") or last_order_id)
            last_resp = chunk_resp
        close_resp = last_resp  # 保持原 close_resp 变量名兼容下面计算
        exit_price = (total_cum_quote / total_qty_closed
                       if total_qty_closed > 0 else float(close_resp.get("avgPrice") or 0))
        close_cum_quote = total_cum_quote
        close_order_id_int = last_order_id
        # 重写 qty 为实际成交总量 (chunking 后)
        qty = total_qty_closed
        # PnL: LONG = (exit - entry) * qty; SHORT = (entry - exit) * qty
        if side == "BUY":
            pnl = (exit_price - entry_price) * qty
        else:
            pnl = (entry_price - exit_price) * qty

        # 实际 close commission (回退到估算)
        close_fee_estimate = round(close_cum_quote * 0.0004, 4)
        actual_close_fee = None
        if close_order_id_int > 0:
            actual_close_fee = self._actual_commission_usdt(
                symbol, close_order_id_int,
            )
        close_fee = (actual_close_fee
                     if actual_close_fee is not None else close_fee_estimate)
        close_fee_is_actual = actual_close_fee is not None

        result = {
            "symbol": symbol,
            "side": side,
            "qty_closed": qty,
            "entry_price": entry_price,
            "avg_exit_price": exit_price,
            "realized_pnl_usdt": round(pnl, 4),
            "fees_paid_usdt": close_fee,
            "fees_are_actual": close_fee_is_actual,
            "close_order_id": close_order_id_int,
            "open_orders_canceled": canceled,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        log.info(
            f"✓ close_position done: {symbol} {side} qty={qty} "
            f"entry={entry_price} exit={exit_price} pnl={pnl:+.4f} "
            f"fee={close_fee:.4f} ({'actual' if close_fee_is_actual else 'estimate'})"
        )
        return result

    # ============================================================
    # Internal: HTTP plumbing
    # ============================================================

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _server_time_ms(self) -> int:
        """估计的服务器时刻 (本地 + offset)."""
        return self._now_ms() + self._time_offset_ms

    def _sign(self, query_string: str) -> str:
        """HMAC-SHA256 签名 query string. 返回 hex digest."""
        return hmac.new(
            self._api_secret_bytes,
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _build_signed_query(self, params: dict) -> str:
        """加入 timestamp + recvWindow + signature, 返回完整 query string."""
        params = dict(params)  # 防御性拷贝
        params["timestamp"] = self._server_time_ms()
        params["recvWindow"] = self.recv_window_ms
        # URL-encode (Python urllib 自动排序保证决定性签名)
        query = urllib.parse.urlencode(params)
        signature = self._sign(query)
        return f"{query}&signature={signature}"

    def _public_request(self, method: str, path: str, params: dict) -> Union[dict, list]:
        self._bucket.acquire(weight=1)
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._do_request(method, url, signed=False)

    def _signed_request(self, method: str, path: str, params: dict) -> Union[dict, list]:
        self._bucket.acquire(weight=1)
        # DEBUG 打印发送的 params (不含 signature, signature 是 HMAC hash 不泄露 secret)
        if log.isEnabledFor(logging.DEBUG):
            safe_params = {k: v for k, v in params.items()}
            log.debug(f"→ {method} {path}  params={safe_params}")
        query = self._build_signed_query(params)
        url = f"{self.base_url}{path}?{query}"
        return self._do_request(method, url, signed=True)

    def _do_request(self, method: str, url: str, signed: bool) -> Union[dict, list]:
        """执行 HTTP, 带 retry 和错误分级.
        重要: 任何错误消息都不应包含 secret (我们的代码本来就没把 secret 加进 URL).
        """
        headers = {
            "User-Agent": "cresus-bot/0.1",
            "Accept": "application/json",
        }
        if signed:
            headers["X-MBX-APIKEY"] = self._api_key

        last_exc: Optional[BaseException] = None
        for attempt in range(DEFAULT_MAX_ATTEMPTS):
            try:
                req = urllib.request.Request(url, method=method, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body)

            except urllib.error.HTTPError as e:
                # 读 error body 决定怎么处理
                code, msg = None, ""
                try:
                    err_body = e.read().decode("utf-8")
                    err_json = json.loads(err_body)
                    code = err_json.get("code")
                    msg = err_json.get("msg", "")
                except Exception:
                    msg = str(e)

                http_status = e.code
                # FATAL 类: 401 unauthorized, 403 forbidden, signature error
                if http_status in (401, 403) or code in (-2014, -2015, -1022):
                    raise BinanceAuthError(
                        f"auth failed http={http_status} code={code} msg={msg}"
                    )
                # 时间漂移
                if code == -1021:
                    # Phase 5.I (5/31): -1021 时主动 resync 时钟, 让下次请求 (launchd
                    # 下一 tick 或同 process 后续调用) 用 fresh offset, 不再连续失败.
                    # 当前请求仍抛错 — URL 已签好不能改时间戳.
                    try:
                        drift = self.check_time_drift(sync=True)
                        log.warning(
                            f"[time-resync] 收到 -1021 → 重新校时, drift={drift*1000:+.0f}ms, "
                            f"下次请求将用 fresh offset"
                        )
                    except Exception as resync_err:
                        log.error(f"[time-resync] 重校时失败: {resync_err}")
                    raise BinanceTimeError(
                        f"time drift / recvWindow http={http_status} msg={msg}"
                    )
                # Rate limit (retriable, 长退避)
                if http_status in (418, 429) or code == -1003:
                    last_exc = BinanceRateLimitError(
                        f"rate limit http={http_status} code={code} msg={msg}"
                    )
                # 5xx server error (retriable)
                elif 500 <= http_status < 600:
                    last_exc = BinanceNetworkError(
                        f"server error http={http_status} code={code} msg={msg}"
                    )
                # 其他 4xx — 业务错 (e.g. minNotional 不够), 不可重试
                else:
                    raise BinanceError(
                        f"http {http_status} code={code} msg={msg}"
                    )

            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_exc = BinanceNetworkError(f"network: {type(e).__name__}: {e}")

            except json.JSONDecodeError as e:
                last_exc = BinanceNetworkError(f"json decode: {e}")

            # 退避后重试 (最后一次不退避, 直接 raise)
            if attempt < DEFAULT_MAX_ATTEMPTS - 1:
                wait = DEFAULT_BACKOFF_SEC[attempt]
                log.warning(
                    f"binance request failed (attempt {attempt+1}/{DEFAULT_MAX_ATTEMPTS}): "
                    f"{type(last_exc).__name__}: {last_exc}, retry in {wait}s"
                )
                time.sleep(wait)

        # 所有重试用尽
        raise last_exc or BinanceNetworkError("max retries exceeded")


# ============================================================================
# 凭证加载 helper
# ============================================================================

def load_credentials(keys_file: Optional[Path] = None) -> tuple:
    """加载 API 凭证. 优先 env vars, 其次文件.

    Returns: (api_key, api_secret, testnet_bool)
    Raises: SystemExit 如果没找到.
    """
    # 1. 环境变量优先
    env_key = os.environ.get("BINANCE_API_KEY", "").strip()
    env_secret = os.environ.get("BINANCE_API_SECRET", "").strip()
    env_testnet = os.environ.get("BINANCE_TESTNET", "1").strip().lower()
    env_testnet_bool = env_testnet not in ("0", "false", "no", "")
    if env_key and env_secret:
        return env_key, env_secret, env_testnet_bool

    # 2. 文件 fallback
    kf = keys_file or DEFAULT_KEYS_FILE
    if kf.exists():
        try:
            data = json.loads(kf.read_text(encoding="utf-8"))
            k = (data.get("api_key") or "").strip()
            s = (data.get("api_secret") or "").strip()
            t = bool(data.get("testnet", True))
            if k and s:
                # 检查文件权限 (推荐 600)
                mode = kf.stat().st_mode & 0o777
                if mode != 0o600:
                    log.warning(
                        f"⚠️ {kf} 权限是 {oct(mode)}, 推荐 chmod 600"
                    )
                return k, s, t
        except Exception as e:
            raise SystemExit(f"读取 {kf} 失败: {e}")

    raise SystemExit(
        "未找到 Binance API 凭证. 请设置环境变量:\n"
        "  export BINANCE_API_KEY=<key>\n"
        "  export BINANCE_API_SECRET=<secret>\n"
        "  export BINANCE_TESTNET=1   # 1=testnet (默认), 0=mainnet\n"
        f"或创建 {DEFAULT_KEYS_FILE} (chmod 600):\n"
        '  {"api_key": "...", "api_secret": "...", "testnet": true}'
    )


# ============================================================================
# CLI: 连通性测试
# ============================================================================

def _cli_main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Binance Futures API 连通性测试 (Phase 2.1)")
    p.add_argument("--mainnet", action="store_true",
                   help="使用主网 (默认 testnet). 慎用!")
    p.add_argument("--check-time", action="store_true", help="检查时钟漂移")
    p.add_argument("--account", action="store_true", help="拉取账户信息")
    p.add_argument("--positions", action="store_true", help="拉取持仓")
    p.add_argument("--balance", action="store_true", help="拉取每币种余额")
    p.add_argument("--exchange-info", action="store_true", help="拉取 exchange info (大)")
    p.add_argument("--symbol", type=str, default="BTCUSDT", help="指定 symbol (默认 BTCUSDT)")
    p.add_argument("--verify-setup", action="store_true",
                   help="检查 leverage / margin type / position mode 是否符合预期")
    p.add_argument("--auto-setup", action="store_true",
                   help="自动修正配置 (set isolated + 3x + one-way mode), 需 --live")
    p.add_argument("--place-test-order", action="store_true",
                   help="下一个测试市价单 (默认 dry-run, 默认 BUY, 默认 ~$100)")
    p.add_argument("--side", type=str, default="BUY", choices=["BUY", "SELL"],
                   help="测试订单方向 (BUY=开多, SELL=开空), 默认 BUY")
    p.add_argument("--notional", type=float, default=100.0,
                   help="测试单 notional USDT (默认 100, 会按 LOT_SIZE 和 minNotional 调整)")
    p.add_argument("--open-test-position", action="store_true",
                   help="Phase 2.2.2: open_position 端到端 (开仓 + 挂 SL). 默认 dry-run")
    p.add_argument("--update-test-stop", action="store_true",
                   help="Phase 2.2.2: 移 SL (需先 open-test-position)")
    p.add_argument("--close-test-position", action="store_true",
                   help="Phase 2.2.2: 主动平仓 + 撤挂单. 默认 dry-run")
    p.add_argument("--sl-pct", type=float, default=1.0,
                   help="测试 SL 距 entry 的 % (默认 1.0 = 1%)")
    p.add_argument("--new-sl-pct", type=float, default=0.5,
                   help="update-test-stop 用: 新 SL 距 entry 的 % (默认 0.5)")
    p.add_argument("--trade-id", type=str, default=None,
                   help="trade_id (默认自动生成 test_<timestamp>)")
    p.add_argument("--no-exchange-sl", action="store_true",
                   help="open-test-position 用: 跳过 exchange-side SL "
                        "(workaround for -4120 errors / 客户端轮询模式)")
    p.add_argument("--cancel-all", action="store_true",
                   help="撤销该 symbol 所有挂单 (默认 dry-run)")
    p.add_argument("--live", action="store_true",
                   help="🛑 关闭 dry-run, 真下单. 主网还需 ~/.allow-live 文件")
    p.add_argument("--leverage", type=int, default=3, help="期望/设置杠杆 (默认 3)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="DEBUG 级日志 (打印每个 API 请求的 params, 不含 secret)")
    args = p.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(message)s")

    key, secret, env_testnet = load_credentials()
    use_testnet = not args.mainnet  # CLI 优先级最高
    if args.mainnet and env_testnet:
        log.warning("⚠️ env 配置是 testnet, 但 --mainnet flag 已切到主网")

    # dry_run 默认 True (--live 显式关闭). 这是 Phase 2.2 的核心安全机制.
    dry_run = not args.live
    client = BinanceClient(key, secret, testnet=use_testnet, dry_run=dry_run)
    print(f"Connected: {client}")
    print(f"Base URL: {client.base_url}")
    if dry_run:
        print(f"🟢 Mode: DRY-RUN (won't place real orders, use --live to disable)")
    else:
        if not use_testnet:
            print(f"🔴 Mode: LIVE TRADING ON MAINNET — REAL MONEY")
        else:
            print(f"🟡 Mode: LIVE on testnet (no real money)")
    print()

    # 默认都跑一遍 check-time
    drift = client.check_time_drift()
    print(f"⏱  Time drift: {drift:+.3f}s  (offset={client._time_offset_ms}ms)")
    if abs(drift) > MAX_TIME_DRIFT_SEC:
        print(f"   ⚠️ 漂移 > {MAX_TIME_DRIFT_SEC}s, 实盘下单会被 Binance 拒绝!")
        print(f"   修复: sudo sntp -sS time.apple.com  (macOS)")
    else:
        print(f"   ✓ 漂移正常 (容忍 ±{MAX_TIME_DRIFT_SEC}s)")
    print()

    if args.account:
        acct = client.get_account()
        print(f"💰 Account:")
        for k in ("totalWalletBalance", "totalUnrealizedProfit",
                  "totalMarginBalance", "availableBalance"):
            print(f"   {k}: {acct.get(k)}")
        print()

    if args.balance:
        bal = client.get_balance()
        print(f"🪙 Balances ({len(bal)} 项, 非零的):")
        for b in bal:
            if float(b.get("balance", 0)) != 0:
                print(f"   {b.get('asset')}: balance={b.get('balance')} "
                      f"available={b.get('availableBalance')}")
        print()

    if args.positions:
        pos = client.get_positions()
        active = [p for p in pos if float(p.get("positionAmt", 0)) != 0]
        print(f"📊 Active positions: {len(active)} / {len(pos)} symbols tracked")
        for p in active:
            print(f"   {p['symbol']} {p.get('positionSide','BOTH')} "
                  f"amt={p['positionAmt']} entry={p['entryPrice']} "
                  f"unRealizedProfit={p['unRealizedProfit']}")
        print()

    if args.exchange_info:
        ei = client.get_exchange_info()
        syms = ei.get("symbols", [])
        print(f"📚 Exchange info: {len(syms)} symbols, "
              f"server={ei.get('serverTime')}")
        if args.symbol:
            target = [s for s in syms if s.get("symbol") == args.symbol.upper()]
            if target:
                s = target[0]
                print(f"   {s['symbol']}: status={s.get('status')} "
                      f"pricePrecision={s.get('pricePrecision')} "
                      f"quantityPrecision={s.get('quantityPrecision')}")
                # 关键 filter
                for f in s.get("filters", []):
                    if f.get("filterType") in ("MIN_NOTIONAL", "LOT_SIZE", "PRICE_FILTER"):
                        print(f"   filter[{f['filterType']}]: {f}")
        print()

    if args.verify_setup:
        sym = args.symbol.upper()
        print(f"🔧 Verify setup for {sym} (期望 isolated + {args.leverage}x + one-way):")
        result = client.verify_setup(sym,
                                      expected_leverage=args.leverage,
                                      expected_margin="ISOLATED",
                                      expected_dual_side=False)
        if result["ok"]:
            print(f"   ✓ 全部符合预期. current={result['current']}")
        else:
            print(f"   ✗ 发现 {len(result['issues'])} 处不符:")
            for issue in result["issues"]:
                print(f"     - {issue}")
            print(f"   current={result['current']}")
            print(f"   修复方法: 加 --auto-setup --live (或在 Binance 网页手动改)")
        print()

    if args.auto_setup:
        sym = args.symbol.upper()
        print(f"⚙️  Auto setup {sym} → ISOLATED + {args.leverage}x + one-way mode")
        r1 = client.set_position_mode(dual_side=False)
        print(f"   position mode: {r1}")
        r2 = client.set_margin_type(sym, "ISOLATED")
        print(f"   margin type: {r2}")
        r3 = client.set_leverage(sym, args.leverage)
        print(f"   leverage: {r3}")
        print()

    if args.place_test_order:
        sym = args.symbol.upper()
        # 1. 拉 symbol filters (LOT_SIZE / MIN_NOTIONAL / PRICE_FILTER)
        filters = client.get_symbol_filters(sym)
        print(f"📋 {sym} filters: step_size={filters['step_size']} "
              f"min_qty={filters['min_qty']} min_notional={filters['min_notional']} "
              f"qty_precision={filters['quantity_precision']}")
        # 2. 拉当前价
        klines = client.get_klines(sym, interval="1m", limit=1)
        if not klines:
            print(f"   ✗ 无法获取 {sym} 当前价, 取消")
            return 1
        last_close = float(klines[0][4])
        # 3. 按 LOT_SIZE round 数量
        raw_qty = args.notional / last_close
        qty = round_qty_down_to_step(raw_qty, filters["step_size"])
        # 不足 min_qty 提到 min_qty
        if qty < filters["min_qty"]:
            qty = filters["min_qty"]
        # 不足 min_notional 提到能满足的最小档
        actual_notional = qty * last_close
        if filters["min_notional"] > 0 and actual_notional < filters["min_notional"]:
            need_qty = filters["min_notional"] / last_close
            # 向上 round 到 step 倍数
            qty = math.ceil(need_qty / filters["step_size"]) * filters["step_size"]
            qty = round(qty, filters["quantity_precision"])
            actual_notional = qty * last_close
            print(f"   ⚠️ 请求 ${args.notional:.2f} 不足 minNotional ${filters['min_notional']:.2f}, "
                  f"调整为 ${actual_notional:.2f}")
        coid = f"cresus_test_{int(time.time())}"[:36]
        print(f"📝 Place test order: {args.side} {qty} {sym} @ market "
              f"(实际 ~${actual_notional:.2f}, last_close=${last_close:.2f}), coid={coid}")
        try:
            resp = client.place_market_order(sym, args.side, qty, client_order_id=coid)
            print(f"   响应: {json.dumps(resp, indent=2, ensure_ascii=False)}")
            if not dry_run and resp.get("status") not in ("DRY_RUN",):
                print(f"   ⚠️ 真的下单了! 记得用 --cancel-all 或手动平仓")
        except BinanceError as e:
            print(f"   ✗ 下单失败: {e}")
            return 1

    if args.cancel_all:
        sym = args.symbol.upper()
        open_orders = client.get_open_orders(sym)
        print(f"📋 Cancel all {len(open_orders)} open orders on {sym}:")
        for o in open_orders:
            print(f"   - {o.get('orderId')} {o.get('side')} {o.get('type')} "
                  f"@ {o.get('stopPrice') or o.get('price')}")
            try:
                resp = client.cancel_order(sym, order_id=int(o["orderId"]))
                print(f"     ✓ canceled (status={resp.get('status')})")
            except BinanceError as e:
                print(f"     ✗ 失败: {e}")

    # Phase 2.2.2 CLI: open_position
    if args.open_test_position:
        sym = args.symbol.upper()
        klines = client.get_klines(sym, interval="1m", limit=1)
        last_close = float(klines[0][4])
        # SL 计算: LONG → entry * (1 - sl_pct/100); SHORT → entry * (1 + sl_pct/100)
        if args.side == "BUY":
            sl_price = last_close * (1 - args.sl_pct / 100.0)
        else:
            sl_price = last_close * (1 + args.sl_pct / 100.0)
        trade_id = args.trade_id or f"test{int(time.time())}"
        print(f"📝 open_position: {args.side} {sym} notional=${args.notional} "
              f"sl={sl_price:.2f} ({-args.sl_pct if args.side=='BUY' else args.sl_pct:+}% "
              f"vs {last_close:.2f}) trade_id={trade_id}")
        try:
            result = client.open_position(
                symbol=sym, side=args.side,
                notional_usdt=args.notional,
                sl_price=sl_price,
                trade_id=trade_id,
                use_exchange_sl=not args.no_exchange_sl,
            )
            print(f"   响应: {json.dumps(result, indent=2, ensure_ascii=False, default=str)}")
            if not dry_run:
                print(f"   ⚠️ 真开仓了! trade_id={trade_id}")
                print(f"   后续: --update-test-stop --trade-id {trade_id} --new-sl-pct ...")
                print(f"   或:   --close-test-position --trade-id {trade_id} --side {args.side}")
        except BinanceError as e:
            print(f"   ✗ 失败: {e}")
            return 1

    if args.update_test_stop:
        sym = args.symbol.upper()
        if not args.trade_id:
            print(f"   ✗ --update-test-stop 必须提供 --trade-id")
            return 1
        klines = client.get_klines(sym, interval="1m", limit=1)
        last_close = float(klines[0][4])
        if args.side == "BUY":
            new_sl = last_close * (1 - args.new_sl_pct / 100.0)
            sl_side = "SELL"
        else:
            new_sl = last_close * (1 + args.new_sl_pct / 100.0)
            sl_side = "BUY"
        old_coid = f"cresus_{args.trade_id}_SL"
        new_coid = f"cresus_{args.trade_id}_SLB"   # B for "moved to BE/Better"
        print(f"📝 update_stop_order: {sym} {sl_side} new_sl={new_sl:.2f} "
              f"(old={old_coid} → new={new_coid})")
        try:
            result = client.update_stop_order(
                symbol=sym,
                new_stop_price=new_sl,
                old_sl_client_order_id=old_coid,
                new_sl_client_order_id=new_coid,
                sl_side=sl_side,
            )
            print(f"   响应: {json.dumps(result, indent=2, ensure_ascii=False, default=str)}")
        except BinanceError as e:
            print(f"   ✗ 失败: {e}")
            return 1

    if args.close_test_position:
        sym = args.symbol.upper()
        print(f"📝 close_position: {sym} {args.side} trade_id={args.trade_id}")
        try:
            result = client.close_position(
                symbol=sym, side=args.side, trade_id=args.trade_id,
            )
            print(f"   响应: {json.dumps(result, indent=2, ensure_ascii=False, default=str)}")
        except BinanceError as e:
            print(f"   ✗ 失败: {e}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
