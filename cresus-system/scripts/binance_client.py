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
RECV_WINDOW_MS = 5000             # 短窗口防 replay attack
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

    def get_exchange_info(self) -> dict:
        """获取所有 symbol 的交易规则 (minNotional / minQty / stepSize / 等)."""
        return self._public_request("GET", "/fapi/v1/exchangeInfo", {})

    def get_symbol_filters(self, symbol: str) -> dict:
        """提取该 symbol 的交易精度规则. Returns dict:
            {
              'step_size': 0.001,      # LOT_SIZE 数量步长
              'min_qty': 0.001,        # LOT_SIZE 最小数量
              'max_qty': 1000.0,       # LOT_SIZE 最大
              'min_notional': 100.0,   # MIN_NOTIONAL 最小订单总额
              'tick_size': 0.10,       # PRICE_FILTER 价格步长
              'quantity_precision': 3, # 数量小数位
              'price_precision': 2,    # 价格小数位
            }
        测试网 vs 主网过滤器可能不同, 启动时必查.
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
        notional = filters.get("MIN_NOTIONAL", {}) or filters.get("NOTIONAL", {})
        price_f = filters.get("PRICE_FILTER", {})
        return {
            "step_size":           float(lot.get("stepSize", 0.001)),
            "min_qty":             float(lot.get("minQty", 0.001)),
            "max_qty":             float(lot.get("maxQty", 0)),
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

    def place_stop_market_order(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        *,
        quantity: Optional[float] = None,
        close_position: bool = True,
        client_order_id: Optional[str] = None,
        working_type: str = "CONTRACT_PRICE",
        dry_run: Optional[bool] = None,
    ) -> dict:
        """STOP_MARKET 止损单. 默认 close_position=True (触发时平整仓).

        side 是 *平仓方向*:
        - LONG 持仓的 SL: side='SELL'
        - SHORT 持仓的 SL: side='BUY'

        working_type: 'CONTRACT_PRICE' (默认, 用最新成交价触发) 或 'MARK_PRICE' (标记价, 防插针).
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须是 BUY 或 SELL, got {side!r}")
        if stop_price <= 0:
            raise ValueError(f"stop_price 必须 > 0, got {stop_price}")
        if working_type not in ("CONTRACT_PRICE", "MARK_PRICE"):
            raise ValueError(f"working_type 必须是 CONTRACT_PRICE 或 MARK_PRICE")
        # close_position=True 和 quantity 互斥
        if close_position and quantity is not None:
            raise ValueError("close_position=True 时不能同时指定 quantity")
        if not close_position and quantity is None:
            raise ValueError("close_position=False 时必须指定 quantity")
        is_dry = self._is_dry_run(dry_run)
        self._check_live_authorization(is_dry)
        params = {
            "symbol": symbol.upper(),
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": _format_price(stop_price),
            "workingType": working_type,
            "priceProtect": "true",   # 防插针触发
            "newOrderRespType": "RESULT",
        }
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
    p.add_argument("--cancel-all", action="store_true",
                   help="撤销该 symbol 所有挂单 (默认 dry-run)")
    p.add_argument("--live", action="store_true",
                   help="🛑 关闭 dry-run, 真下单. 主网还需 ~/.allow-live 文件")
    p.add_argument("--leverage", type=int, default=3, help="期望/设置杠杆 (默认 3)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

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

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
