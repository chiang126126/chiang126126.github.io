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
import os
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
    ):
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key 必须是非空字符串")
        if not api_secret or not isinstance(api_secret, str):
            raise ValueError("api_secret 必须是非空字符串")
        if recv_window_ms <= 0 or recv_window_ms > 60000:
            raise ValueError("recv_window_ms 必须在 (0, 60000] 范围")
        self._api_key = api_key
        # 保留 bytes 形式给 HMAC 用. 不暴露 __dict__ 外.
        self._api_secret_bytes = api_secret.encode("utf-8")
        self.testnet = bool(testnet)
        self.base_url = TESTNET_BASE if self.testnet else MAINNET_BASE
        self.timeout = float(timeout)
        self.recv_window_ms = int(recv_window_ms)
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
    p.add_argument("--symbol", type=str, default=None, help="指定 symbol 测试")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    key, secret, env_testnet = load_credentials()
    use_testnet = not args.mainnet  # CLI 优先级最高
    if args.mainnet and env_testnet:
        log.warning("⚠️ env 配置是 testnet, 但 --mainnet flag 已切到主网")

    client = BinanceClient(key, secret, testnet=use_testnet)
    print(f"Connected: {client}")
    print(f"Base URL: {client.base_url}")
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

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
