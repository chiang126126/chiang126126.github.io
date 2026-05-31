"""Phase 2.1 单元测试 — BinanceClient 不连网验证.

覆盖:
- API secret 安全 (永不出现在 repr/str)
- HMAC-SHA256 签名正确性
- URL 构造 / endpoint 路由
- Token bucket 节流逻辑
- 错误分级 (auth fatal / rate limit retriable / network retriable / business fatal)
- 凭证加载 (env / file)

运行: python3 -m pytest cresus-system/scripts/tests/test_binance_client.py
或: python3 cresus-system/scripts/tests/test_binance_client.py
"""
import hashlib
import hmac
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 让 import 能找到 binance_client
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from binance_client import (  # noqa: E402
    BinanceClient,
    BinanceError, BinanceAuthError, BinanceRateLimitError,
    BinanceNetworkError, BinanceTimeError,
    _TokenBucket, _validate_credential,
    _validate_client_order_id, _validate_trade_id,
    _format_quantity, _format_price,
    round_qty_down_to_step, round_price_to_tick,
    MAINNET_BASE, TESTNET_BASE,
    RECV_WINDOW_MS, MAX_TIME_DRIFT_SEC,
    DEFAULT_MAX_ATTEMPTS,
    load_credentials,
)

# Fake keys for unit tests (real Binance keys are 64-char alphanumeric).
# These must pass _validate_credential's length/format checks.
FAKE_KEY    = "FAKE_API_KEY_DO_NOT_USE_" + "x" * 40   # 64 chars
FAKE_SECRET = "FAKE_API_SECRET_DO_NOT_USE_" + "y" * 37  # 64 chars


# ============================================================================
# Secret safety: 最高优先级 — 不能泄露
# ============================================================================

class TestSecretSafety(unittest.TestCase):

    # 64 chars to pass length validation
    SECRET = "MY_SUPER_SECRET_XYZ789_DO_NOT_LEAK_" + "z" * 29
    API_KEY = "api_key_abc123def456_" + "k" * 43  # 64 chars

    def setUp(self):
        self.client = BinanceClient(self.API_KEY, self.SECRET)

    def test_repr_hides_secret(self):
        r = repr(self.client)
        self.assertNotIn(self.SECRET, r)
        self.assertNotIn("XYZ789", r)

    def test_str_hides_secret(self):
        s = str(self.client)
        self.assertNotIn(self.SECRET, s)

    def test_dict_does_not_have_plain_secret(self):
        """vars() / __dict__ 也不应包含明文 secret (只存 bytes 形式 _api_secret_bytes)."""
        d = vars(self.client)
        # secret 应该以 bytes 形式存在 (用于 HMAC), 这是必要的
        # 但不应有同名的 'api_secret' 字段
        self.assertNotIn("api_secret", d)
        self.assertNotIn("_api_secret", d)
        # bytes 字段存在, 但 repr/str 不应触及它
        self.assertIn("_api_secret_bytes", d)

    def test_repr_shows_only_partial_api_key(self):
        # api_key 前 6 位 = "api_ke"
        r = repr(self.client)
        self.assertIn("api_ke", r)      # 前 6 位
        self.assertNotIn("abc123", r)   # 后面截掉
        self.assertNotIn("def456", r)
        self.assertIn("***", r)         # 有 mask


# ============================================================================
# HMAC 签名正确性
# ============================================================================

class TestSigning(unittest.TestCase):

    # Must be 30+ chars to pass _validate_credential
    SIGN_SECRET = "test_secret_value_padded_to_64_chars_" + "p" * 27

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, self.SIGN_SECRET)

    def test_signature_matches_stdlib_hmac(self):
        """签名结果必须匹配标准 HMAC-SHA256."""
        query = "symbol=BTCUSDT&side=BUY&quantity=0.001&timestamp=1234567890"
        expected = hmac.new(
            self.SIGN_SECRET.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        actual = self.client._sign(query)
        self.assertEqual(expected, actual)

    def test_signature_deterministic(self):
        s1 = self.client._sign("foo=1&bar=2")
        s2 = self.client._sign("foo=1&bar=2")
        self.assertEqual(s1, s2)

    def test_signature_differs_for_different_inputs(self):
        s1 = self.client._sign("foo=1&bar=2")
        s2 = self.client._sign("foo=1&bar=3")
        self.assertNotEqual(s1, s2)

    def test_signed_query_includes_timestamp_and_signature(self):
        q = self.client._build_signed_query({"symbol": "BTCUSDT"})
        self.assertIn("symbol=BTCUSDT", q)
        self.assertIn("timestamp=", q)
        self.assertIn("recvWindow=", q)
        self.assertIn("signature=", q)

    def test_signed_query_does_not_leak_secret(self):
        q = self.client._build_signed_query({"x": "y"})
        self.assertNotIn(self.SIGN_SECRET, q)


# ============================================================================
# Endpoint 配置
# ============================================================================

class TestEndpointConfig(unittest.TestCase):

    def test_testnet_default(self):
        c = BinanceClient(FAKE_KEY, FAKE_SECRET)
        self.assertTrue(c.testnet)
        self.assertEqual(c.base_url, TESTNET_BASE)
        self.assertTrue(c.base_url.startswith("https://"))

    def test_mainnet_explicit(self):
        c = BinanceClient(FAKE_KEY, FAKE_SECRET, testnet=False)
        self.assertFalse(c.testnet)
        self.assertEqual(c.base_url, MAINNET_BASE)
        self.assertTrue(c.base_url.startswith("https://"))


# ============================================================================
# 输入验证
# ============================================================================

class TestInputValidation(unittest.TestCase):

    def test_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            BinanceClient("", "secret")

    def test_empty_secret_rejected(self):
        with self.assertRaises(ValueError):
            BinanceClient("key", "")

    def test_none_key_rejected(self):
        with self.assertRaises(ValueError):
            BinanceClient(None, "s")  # type: ignore

    def test_none_secret_rejected(self):
        with self.assertRaises(ValueError):
            BinanceClient("k", None)  # type: ignore

    def test_invalid_recv_window_rejected(self):
        # 用合法 key 长度, 让 recv_window 检查能命中
        with self.assertRaises(ValueError):
            BinanceClient(FAKE_KEY, FAKE_SECRET, recv_window_ms=0)
        with self.assertRaises(ValueError):
            BinanceClient(FAKE_KEY, FAKE_SECRET, recv_window_ms=70000)

    def test_angle_brackets_rejected(self):
        # 用户最常见错误: 复制了文档里的 <占位符>
        bad_key = "<" + "x" * 62 + ">"
        with self.assertRaises(ValueError) as ctx:
            BinanceClient(bad_key, FAKE_SECRET)
        self.assertIn("尖括号", str(ctx.exception))

    def test_three_dots_placeholder_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            BinanceClient("...", FAKE_SECRET)
        # 实际会命中 length 校验先, 但都是 helpful error
        self.assertIsInstance(ctx.exception, ValueError)

    def test_whitespace_rejected(self):
        bad_key = "abc123 " + "x" * 56   # 64 chars but has space
        with self.assertRaises(ValueError) as ctx:
            BinanceClient(bad_key, FAKE_SECRET)
        self.assertIn("空格", str(ctx.exception))

    def test_short_key_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            BinanceClient("too_short_5chars", FAKE_SECRET)  # 16 chars
        self.assertIn("长度", str(ctx.exception))

    def test_unicode_in_key_rejected(self):
        bad_key = "abc密钥" + "x" * 58  # has non-ASCII
        with self.assertRaises(ValueError) as ctx:
            BinanceClient(bad_key, FAKE_SECRET)
        self.assertIn("ASCII", str(ctx.exception))


# ============================================================================
# Token bucket
# ============================================================================

class TestTokenBucket(unittest.TestCase):

    def test_basic_acquire(self):
        b = _TokenBucket(capacity=10, refill_per_sec=1)
        self.assertTrue(b.acquire(5, blocking=False))
        # 用浮点比较
        self.assertAlmostEqual(b.tokens, 5, delta=0.01)

    def test_overflow_blocks_when_non_blocking(self):
        b = _TokenBucket(capacity=10, refill_per_sec=1)
        b.tokens = 3
        b.last_refill = time.monotonic()  # 防止立即 refill
        self.assertFalse(b.acquire(5, blocking=False))

    def test_refill_caps_at_capacity(self):
        b = _TokenBucket(capacity=10, refill_per_sec=1000)
        b.tokens = 0
        b.last_refill = time.monotonic() - 1.0  # 1 秒前
        b._refill()
        # 应该补到 capacity 不超
        self.assertLessEqual(b.tokens, 10)

    def test_zero_weight_always_succeeds(self):
        b = _TokenBucket(capacity=10, refill_per_sec=1)
        b.tokens = 0
        self.assertTrue(b.acquire(weight=0))


# ============================================================================
# 错误分级 (mock HTTP layer)
# ============================================================================

def _make_http_error(status, body_bytes):
    """构造 urllib.error.HTTPError."""
    import urllib.error
    return urllib.error.HTTPError(
        url="https://x/y",
        code=status,
        msg="X",
        hdrs={},  # type: ignore
        fp=io.BytesIO(body_bytes),
    )


class TestErrorCategorization(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET)
        # 不让 _bucket 真等待
        self.client._bucket.acquire = MagicMock(return_value=True)

    def _run_and_expect(self, http_error_factory, expected_exc):
        """重试 4 次都失败 → 期望抛出指定异常."""
        side_effects = [http_error_factory() for _ in range(DEFAULT_MAX_ATTEMPTS)]
        with patch("urllib.request.urlopen", side_effect=side_effects):
            with patch("time.sleep"):  # 不真睡
                with self.assertRaises(expected_exc):
                    self.client._do_request("GET", "https://x/y", signed=False)

    def test_401_is_auth_error_no_retry(self):
        """401 是 FATAL, 不重试."""
        body = b'{"code":-2014,"msg":"API-key format invalid."}'
        with patch("urllib.request.urlopen",
                   side_effect=_make_http_error(401, body)):
            with patch("time.sleep"):
                with self.assertRaises(BinanceAuthError):
                    self.client._do_request("GET", "https://x/y", signed=True)

    def test_403_is_auth_error(self):
        body = b'{"code":-2015,"msg":"Invalid API-key, IP, or permissions."}'
        with patch("urllib.request.urlopen",
                   side_effect=_make_http_error(403, body)):
            with patch("time.sleep"):
                with self.assertRaises(BinanceAuthError):
                    self.client._do_request("GET", "https://x/y", signed=True)

    def test_429_is_rate_limit_retriable(self):
        """429 重试 4 次, 最后抛 RateLimitError."""
        self._run_and_expect(
            lambda: _make_http_error(429, b'{"code":-1003,"msg":"Too many requests."}'),
            BinanceRateLimitError,
        )

    def test_418_is_rate_limit_retriable(self):
        """418 (IP-banned) 也归 rate limit."""
        self._run_and_expect(
            lambda: _make_http_error(418, b'{"code":-1003,"msg":"IP banned."}'),
            BinanceRateLimitError,
        )

    def test_500_is_network_error_retriable(self):
        self._run_and_expect(
            lambda: _make_http_error(500, b'{"code":-1000,"msg":"unknown."}'),
            BinanceNetworkError,
        )

    def test_502_is_network_error(self):
        self._run_and_expect(
            lambda: _make_http_error(502, b'<html>Bad Gateway</html>'),
            BinanceNetworkError,
        )

    def test_400_is_fatal_no_retry(self):
        """400 业务错 (e.g. min notional) 不可重试."""
        body = b'{"code":-1013,"msg":"Filter failure: MIN_NOTIONAL"}'
        with patch("urllib.request.urlopen",
                   side_effect=_make_http_error(400, body)):
            with patch("time.sleep"):
                with self.assertRaises(BinanceError) as ctx:
                    self.client._do_request("GET", "https://x/y", signed=False)
                # 应该不是 Auth/RateLimit/Network 子类 (业务错)
                self.assertNotIsInstance(ctx.exception, BinanceAuthError)
                self.assertNotIsInstance(ctx.exception, BinanceRateLimitError)
                self.assertNotIsInstance(ctx.exception, BinanceNetworkError)

    def test_time_drift_code(self):
        """code -1021 = timestamp 超出 recvWindow."""
        body = b'{"code":-1021,"msg":"Timestamp for this request was 1000ms ahead."}'
        with patch("urllib.request.urlopen",
                   side_effect=_make_http_error(400, body)):
            with patch("time.sleep"):
                with patch.object(self.client, "check_time_drift",
                                   return_value=0.0):
                    with self.assertRaises(BinanceTimeError):
                        self.client._do_request("GET", "https://x/y", signed=True)

    def test_time_drift_triggers_resync(self):
        """Phase 5.I (5/31): -1021 时主动调 check_time_drift(sync=True)
        让下一次请求 (launchd next tick) 用 fresh offset.
        """
        body = b'{"code":-1021,"msg":"Timestamp for this request was 1000ms ahead."}'
        with patch("urllib.request.urlopen",
                   side_effect=_make_http_error(400, body)):
            with patch("time.sleep"):
                with patch.object(self.client, "check_time_drift",
                                   return_value=0.005) as resync_mock:
                    with self.assertRaises(BinanceTimeError):
                        self.client._do_request("GET", "https://x/y", signed=True)
                    # resync 必须被调用至少一次, 且 sync=True
                    resync_mock.assert_called_once_with(sync=True)

    def test_time_drift_resync_failure_does_not_crash(self):
        """check_time_drift 抛错时, 不应阻止 BinanceTimeError 上抛 (兜底)."""
        body = b'{"code":-1021,"msg":"Timestamp drift"}'
        with patch("urllib.request.urlopen",
                   side_effect=_make_http_error(400, body)):
            with patch("time.sleep"):
                with patch.object(self.client, "check_time_drift",
                                   side_effect=Exception("network down during resync")):
                    # 仍应抛 BinanceTimeError, 不被 resync 异常掩盖
                    with self.assertRaises(BinanceTimeError):
                        self.client._do_request("GET", "https://x/y", signed=True)

    def test_recv_window_default_is_30s(self):
        """Phase 5.I: 默认 recvWindow 应为 30000 (容忍 Mac 唤醒后 NTP 漂移)."""
        from binance_client import RECV_WINDOW_MS
        self.assertEqual(RECV_WINDOW_MS, 30000)

    def test_network_timeout_retriable(self):
        """TimeoutError 重试 4 次, 最后抛 NetworkError."""
        side_effects = [TimeoutError("timeout") for _ in range(DEFAULT_MAX_ATTEMPTS)]
        with patch("urllib.request.urlopen", side_effect=side_effects):
            with patch("time.sleep"):
                with self.assertRaises(BinanceNetworkError):
                    self.client._do_request("GET", "https://x/y", signed=False)

    def test_success_after_retry(self):
        """第 1-2 次 timeout, 第 3 次成功 → 不抛错."""
        good_resp = MagicMock()
        good_resp.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=b'{"result":"ok"}')
        ))
        good_resp.__exit__ = MagicMock(return_value=False)
        side_effects = [
            TimeoutError("t1"),
            TimeoutError("t2"),
            good_resp,
        ]
        with patch("urllib.request.urlopen", side_effect=side_effects):
            with patch("time.sleep"):
                result = self.client._do_request("GET", "https://x/y", signed=False)
        self.assertEqual(result, {"result": "ok"})


# ============================================================================
# 凭证加载
# ============================================================================

class TestLoadCredentials(unittest.TestCase):

    def test_env_vars_priority(self):
        env = {
            "BINANCE_API_KEY": "env_key",
            "BINANCE_API_SECRET": "env_secret",
            "BINANCE_TESTNET": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            k, s, t = load_credentials(keys_file=Path("/nonexistent/path.json"))
            self.assertEqual(k, "env_key")
            self.assertEqual(s, "env_secret")
            self.assertTrue(t)

    def test_env_testnet_false_recognized(self):
        env = {
            "BINANCE_API_KEY": "k",
            "BINANCE_API_SECRET": "s",
            "BINANCE_TESTNET": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            _, _, t = load_credentials(keys_file=Path("/nonexistent.json"))
            self.assertFalse(t)

    def test_missing_credentials_raises(self):
        env = {"BINANCE_API_KEY": "", "BINANCE_API_SECRET": ""}
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(SystemExit):
                load_credentials(keys_file=Path("/nonexistent.json"))


# ============================================================================
# URL 构造
# ============================================================================

class TestURLConstruction(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, testnet=True)
        # 不让 acquire 阻塞 + 不让真的 urlopen 跑
        self.client._bucket.acquire = MagicMock(return_value=True)

    def test_public_url_includes_params(self):
        """验证 public endpoint URL 构造."""
        captured_url = []

        def fake_urlopen(req, timeout=None):
            captured_url.append(req.full_url)
            raise TimeoutError("intercepted")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep"):
                try:
                    self.client._public_request("GET", "/fapi/v1/klines",
                                                 {"symbol": "BTCUSDT", "interval": "1m"})
                except BinanceNetworkError:
                    pass
        self.assertTrue(captured_url, "urlopen 没被调用")
        url = captured_url[0]
        self.assertTrue(url.startswith(TESTNET_BASE))
        self.assertIn("/fapi/v1/klines", url)
        self.assertIn("symbol=BTCUSDT", url)
        self.assertIn("interval=1m", url)

    def test_book_ticker_url_construction(self):
        """Phase 4.U: bookTicker endpoint 路径和参数验证."""
        captured_url = []

        def fake_urlopen(req, timeout=None):
            captured_url.append(req.full_url)
            raise TimeoutError("intercepted")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep"):
                try:
                    self.client.get_book_ticker("XANUSDT")
                except BinanceNetworkError:
                    pass
        self.assertTrue(captured_url, "urlopen 没被调用")
        url = captured_url[0]
        self.assertIn("/fapi/v1/ticker/bookTicker", url)
        self.assertIn("symbol=XANUSDT", url)
        # bookTicker 不带 timestamp/signature (public endpoint)
        self.assertNotIn("signature=", url)

    def test_book_ticker_uppercases_symbol(self):
        """防御: 小写 symbol 自动转大写 (与 get_klines 一致)."""
        captured_url = []

        def fake_urlopen(req, timeout=None):
            captured_url.append(req.full_url)
            raise TimeoutError("intercepted")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep"):
                try:
                    self.client.get_book_ticker("xanusdt")
                except BinanceNetworkError:
                    pass
        self.assertIn("symbol=XANUSDT", captured_url[0])

    def test_signed_url_has_signature(self):
        captured_url = []
        captured_headers = []

        def fake_urlopen(req, timeout=None):
            captured_url.append(req.full_url)
            captured_headers.append(dict(req.headers))
            raise TimeoutError("intercepted")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep"):
                try:
                    self.client._signed_request("GET", "/fapi/v2/account", {})
                except BinanceNetworkError:
                    pass
        self.assertTrue(captured_url)
        url = captured_url[0]
        self.assertIn("signature=", url)
        self.assertIn("timestamp=", url)
        self.assertIn("recvWindow=", url)
        # 验证 X-MBX-APIKEY header (注意 urllib 把 header 大小写转了)
        # urllib normalizes to title case
        h = captured_headers[0]
        api_keys = [v for k, v in h.items() if k.lower() == "x-mbx-apikey"]
        self.assertTrue(api_keys, f"X-MBX-APIKEY header 缺失: {h}")


# ============================================================================
# Phase 4.V: place_limit_order
# ============================================================================

class TestPlaceLimitOrder(unittest.TestCase):
    """Phase 4.V: IOC 限价入场单.

    核心不变式:
      - type=LIMIT, timeInForce=IOC (默认)
      - price 参数存在
      - dry_run 响应与市价单同结构 (上层可统一处理)
    """

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def test_limit_order_type_and_tif_in_dry_run(self):
        """type=LIMIT, timeInForce=IOC 必须写入 params (dry_run 响应反映真实参数)."""
        r = self.client.place_limit_order("XANUSDT", "BUY", 100.0, 0.02141)
        self.assertEqual(r["type"], "LIMIT")
        self.assertEqual(r["_method"], "place_limit_order")
        self.assertEqual(r["symbol"], "XANUSDT")
        self.assertEqual(r["side"], "BUY")

    def test_limit_order_dry_run_flag(self):
        r = self.client.place_limit_order("XANUSDT", "BUY", 100.0, 0.02141)
        self.assertEqual(r["status"], "DRY_RUN")

    def test_sell_side_dry_run(self):
        r = self.client.place_limit_order("SAGAUSDT", "SELL", 50.0, 0.02100)
        self.assertEqual(r["side"], "SELL")
        self.assertEqual(r["symbol"], "SAGAUSDT")

    def test_gtc_tif_accepted(self):
        """GTC 也是有效的 timeInForce."""
        r = self.client.place_limit_order("XANUSDT", "BUY", 100.0, 0.02141,
                                          time_in_force="GTC")
        self.assertEqual(r["status"], "DRY_RUN")

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            self.client.place_limit_order("XANUSDT", "INVALID", 100.0, 0.02141)

    def test_invalid_tif_raises(self):
        with self.assertRaises(ValueError):
            self.client.place_limit_order("XANUSDT", "BUY", 100.0, 0.02141,
                                          time_in_force="ZZZZ")

    def test_zero_price_raises(self):
        with self.assertRaises(ValueError):
            self.client.place_limit_order("XANUSDT", "BUY", 100.0, 0.0)

    def test_zero_qty_raises(self):
        with self.assertRaises(ValueError):
            self.client.place_limit_order("XANUSDT", "BUY", 0.0, 0.02141)


# ============================================================================
# Phase 2.2.1: 订单参数格式化 helpers
# ============================================================================

class TestFormatHelpers(unittest.TestCase):

    def test_quantity_decimal(self):
        self.assertEqual(_format_quantity(0.001), "0.001")
        self.assertEqual(_format_quantity(1.0), "1")
        self.assertEqual(_format_quantity(0.12345678), "0.12345678")

    def test_quantity_no_sci_notation(self):
        """Binance 不接受 1e-05 这种格式."""
        self.assertNotIn("e", _format_quantity(0.00001))
        self.assertNotIn("E", _format_quantity(0.000001))

    def test_quantity_zero_rejected(self):
        with self.assertRaises(ValueError):
            _format_quantity(0)
        with self.assertRaises(ValueError):
            _format_quantity(-1)

    def test_price_strips_trailing_zeros(self):
        self.assertEqual(_format_price(0.50000), "0.5")
        self.assertEqual(_format_price(80000.0), "80000")

    def test_client_order_id_valid(self):
        _validate_client_order_id("cresus_test_123")
        _validate_client_order_id("a")
        _validate_client_order_id("A" * 36)

    def test_client_order_id_invalid(self):
        with self.assertRaises(ValueError):
            _validate_client_order_id("")
        with self.assertRaises(ValueError):
            _validate_client_order_id("a" * 37)   # too long
        with self.assertRaises(ValueError):
            _validate_client_order_id("has space")
        with self.assertRaises(ValueError):
            _validate_client_order_id("has.dot")
        with self.assertRaises(ValueError):
            _validate_client_order_id("has/slash")


class TestQuantityRounding(unittest.TestCase):
    """Phase 2.2.1.1: 防止 -1111 precision error."""

    def test_round_down_to_step_basic(self):
        # 0.000246 BTC at stepSize=0.001 → 0.0 (太小)
        self.assertEqual(round_qty_down_to_step(0.000246, 0.001), 0.0)
        # 0.0035 → 0.003
        self.assertEqual(round_qty_down_to_step(0.0035, 0.001), 0.003)
        # 1.234 at stepSize=0.01 → 1.23
        self.assertEqual(round_qty_down_to_step(1.234, 0.01), 1.23)

    def test_round_down_exact_step(self):
        self.assertEqual(round_qty_down_to_step(0.001, 0.001), 0.001)
        self.assertEqual(round_qty_down_to_step(1.0, 0.1), 1.0)

    def test_round_down_floating_point_robust(self):
        # 0.3 / 0.1 在浮点下不等于 3.0 而是 2.999999...
        # 我们的实现用 +1e-9 防御
        self.assertEqual(round_qty_down_to_step(0.3, 0.1), 0.3)
        self.assertEqual(round_qty_down_to_step(0.6, 0.1), 0.6)

    def test_round_down_no_trailing_decimals(self):
        # stepSize=0.001 → 结果应该是 0.001, 不是 0.001000000004
        result = round_qty_down_to_step(0.005, 0.001)
        self.assertEqual(result, 0.005)
        # 检查 string representation
        self.assertNotIn("000004", str(result))

    def test_round_down_invalid_step(self):
        with self.assertRaises(ValueError):
            round_qty_down_to_step(1.0, 0)
        with self.assertRaises(ValueError):
            round_qty_down_to_step(1.0, -0.001)

    def test_round_price_to_tick(self):
        # BTCUSDT tickSize=0.10: $81234.56 → $81234.6 (round to nearest)
        self.assertEqual(round_price_to_tick(81234.56, 0.1), 81234.6)
        self.assertEqual(round_price_to_tick(81234.54, 0.1), 81234.5)
        # ETHUSDT tickSize=0.01: $3000.123 → $3000.12
        self.assertEqual(round_price_to_tick(3000.123, 0.01), 3000.12)


# ============================================================================
# Phase 2.2.1: dry_run 默认行为 + 安全闸门
# ============================================================================

class TestDryRunDefault(unittest.TestCase):

    def test_dry_run_default_true(self):
        """实例默认 dry_run=True (安全默认)."""
        c = BinanceClient(FAKE_KEY, FAKE_SECRET)
        self.assertTrue(c.dry_run)

    def test_dry_run_explicit_false(self):
        c = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=False)
        self.assertFalse(c.dry_run)

    def test_method_dry_run_overrides_instance(self):
        """方法级 dry_run 参数可覆盖实例配置."""
        c = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        # _is_dry_run 方法测试
        self.assertTrue(c._is_dry_run(None))     # 用实例
        self.assertTrue(c._is_dry_run(True))     # 显式
        self.assertFalse(c._is_dry_run(False))   # 显式覆盖


class TestLiveAuthorization(unittest.TestCase):
    """非 dry_run + mainnet 必须存在 ~/.allow-live 文件."""

    def test_dry_run_always_allowed(self):
        c = BinanceClient(FAKE_KEY, FAKE_SECRET, testnet=False, dry_run=True)
        c._check_live_authorization(dry_run=True)  # 不应抛错

    def test_testnet_live_allowed_no_file(self):
        c = BinanceClient(FAKE_KEY, FAKE_SECRET, testnet=True, dry_run=False)
        c._check_live_authorization(dry_run=False)  # testnet 直接放行

    def test_mainnet_live_requires_file(self):
        c = BinanceClient(FAKE_KEY, FAKE_SECRET, testnet=False, dry_run=False)
        with patch("pathlib.Path.exists", return_value=False):
            with self.assertRaises(BinanceError) as ctx:
                c._check_live_authorization(dry_run=False)
            self.assertIn("allow-live", str(ctx.exception))

    def test_mainnet_live_with_file_allowed(self):
        c = BinanceClient(FAKE_KEY, FAKE_SECRET, testnet=False, dry_run=False)
        with patch("pathlib.Path.exists", return_value=True):
            c._check_live_authorization(dry_run=False)  # 不应抛错


# ============================================================================
# Phase 2.2.1: 下单方法 (dry_run 模式, 不联网)
# ============================================================================

class TestOrderMethods(unittest.TestCase):

    def setUp(self):
        # dry_run 模式: 所有方法返回 mock dict, 不调真 API
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def test_place_market_order_dry_run(self):
        r = self.client.place_market_order("btcusdt", "BUY", 0.001)
        self.assertEqual(r["status"], "DRY_RUN")
        self.assertEqual(r["symbol"], "BTCUSDT")
        self.assertEqual(r["side"], "BUY")
        self.assertEqual(r["type"], "MARKET")
        self.assertEqual(r["_method"], "place_market_order")

    def test_place_market_order_invalid_side(self):
        with self.assertRaises(ValueError):
            self.client.place_market_order("BTCUSDT", "HOLD", 0.001)

    def test_place_market_order_invalid_qty(self):
        with self.assertRaises(ValueError):
            self.client.place_market_order("BTCUSDT", "BUY", 0)
        with self.assertRaises(ValueError):
            self.client.place_market_order("BTCUSDT", "BUY", -0.001)

    def test_place_market_order_with_client_id(self):
        r = self.client.place_market_order(
            "BTCUSDT", "BUY", 0.001,
            client_order_id="cresus_test_001",
        )
        self.assertEqual(r["clientOrderId"], "cresus_test_001")

    def test_place_market_order_invalid_client_id(self):
        with self.assertRaises(ValueError):
            self.client.place_market_order(
                "BTCUSDT", "BUY", 0.001,
                client_order_id="invalid id with spaces",
            )

    def test_place_stop_market_with_quantity_default(self):
        """默认 close_position=False, 需要 quantity (兼容性最好)."""
        r = self.client.place_stop_market_order(
            "BTCUSDT", "SELL", 75000, quantity=0.001,
        )
        self.assertEqual(r["status"], "DRY_RUN")
        self.assertEqual(r["type"], "STOP_MARKET")
        self.assertEqual(r["origQty"], "0.001")
        self.assertEqual(r["_params"].get("reduceOnly"), "true")
        # priceProtect 默认 False, 不应在 params 里
        self.assertNotIn("priceProtect", r["_params"])

    def test_place_stop_market_close_position_explicit(self):
        """显式 close_position=True 仍支持 (但不推荐, 兼容性差)."""
        r = self.client.place_stop_market_order(
            "BTCUSDT", "SELL", 75000, close_position=True,
        )
        self.assertEqual(r["status"], "DRY_RUN")
        self.assertEqual(r["closePosition"], "true")

    def test_place_stop_market_price_protect_opt_in(self):
        """priceProtect 默认关闭, 仅 opt-in 时启用."""
        r1 = self.client.place_stop_market_order(
            "BTCUSDT", "SELL", 75000, quantity=0.001,
        )
        self.assertNotIn("priceProtect", r1["_params"])
        r2 = self.client.place_stop_market_order(
            "BTCUSDT", "SELL", 75000, quantity=0.001, price_protect=True,
        )
        self.assertEqual(r2["_params"].get("priceProtect"), "true")

    def test_place_stop_market_close_and_qty_conflict(self):
        with self.assertRaises(ValueError):
            self.client.place_stop_market_order(
                "BTCUSDT", "SELL", 75000,
                quantity=0.001, close_position=True,
            )

    def test_place_stop_market_no_qty_no_close_position(self):
        with self.assertRaises(ValueError):
            self.client.place_stop_market_order(
                "BTCUSDT", "SELL", 75000,
                # 默认 close_position=False, 不带 quantity → 矛盾
            )

    def test_place_stop_market_invalid_working_type(self):
        with self.assertRaises(ValueError):
            self.client.place_stop_market_order(
                "BTCUSDT", "SELL", 75000,
                working_type="WEIRD_PRICE",
            )

    def test_cancel_order_requires_one_id(self):
        with self.assertRaises(ValueError):
            self.client.cancel_order("BTCUSDT")  # 既没 order_id 也没 client_order_id

    def test_cancel_order_by_client_id(self):
        r = self.client.cancel_order("BTCUSDT", client_order_id="cresus_test_001")
        self.assertEqual(r["status"], "DRY_RUN")
        self.assertEqual(r["_params"]["origClientOrderId"], "cresus_test_001")

    def test_cancel_order_by_order_id(self):
        r = self.client.cancel_order("BTCUSDT", order_id=12345)
        self.assertEqual(r["_params"]["orderId"], 12345)


class TestAccountConfigMethods(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def test_set_leverage_valid(self):
        r = self.client.set_leverage("BTCUSDT", 3)
        self.assertEqual(r["leverage"], 3)

    def test_set_leverage_invalid(self):
        with self.assertRaises(ValueError):
            self.client.set_leverage("BTCUSDT", 0)
        with self.assertRaises(ValueError):
            self.client.set_leverage("BTCUSDT", 200)

    def test_set_margin_type_valid(self):
        r = self.client.set_margin_type("BTCUSDT", "ISOLATED")
        self.assertEqual(r["marginType"], "ISOLATED")

    def test_set_margin_type_invalid(self):
        with self.assertRaises(ValueError):
            self.client.set_margin_type("BTCUSDT", "FANCY")

    def test_set_position_mode_one_way(self):
        r = self.client.set_position_mode(dual_side=False)
        self.assertEqual(r["dualSidePosition"], False)


# ============================================================================
# Phase 2.2.2: trade_id validation
# ============================================================================

class TestTradeIdValidation(unittest.TestCase):

    def test_valid_trade_id(self):
        _validate_trade_id("sig_123")
        _validate_trade_id("a")
        _validate_trade_id("A" * 25)  # max len

    def test_too_long_trade_id(self):
        with self.assertRaises(ValueError):
            _validate_trade_id("a" * 26)

    def test_empty_trade_id(self):
        with self.assertRaises(ValueError):
            _validate_trade_id("")

    def test_invalid_chars_trade_id(self):
        with self.assertRaises(ValueError):
            _validate_trade_id("has space")
        with self.assertRaises(ValueError):
            _validate_trade_id("has.dot")


# ============================================================================
# Phase 2.2.2: 高阶 trade lifecycle (dry-run mode)
# ============================================================================

class TestOpenPositionDryRun(unittest.TestCase):
    """open_position 在 dry-run 模式下的逻辑 (含 filters / qty / SL 验证)."""

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        # Mock filters: BTCUSDT testnet 实际值
        self._mock_filters = {
            "step_size": 0.0001,
            "min_qty": 0.0001,
            "max_qty": 1000.0,
            "min_notional": 50.0,
            "tick_size": 0.10,
            "quantity_precision": 4,
            "price_precision": 2,
            "status": "TRADING",
        }
        self._mock_klines = [[0, 0, 0, 0, "81000.00", 0, 0, 0, 0, 0, 0, 0]]

    def _patch_helpers(self):
        return [
            patch.object(self.client, "get_symbol_filters",
                         return_value=self._mock_filters),
            patch.object(self.client, "get_klines",
                         return_value=self._mock_klines),
        ]

    def test_open_long_basic(self):
        patches = self._patch_helpers()
        for p in patches: p.start()
        try:
            r = self.client.open_position(
                symbol="BTCUSDT", side="BUY",
                notional_usdt=100.0, sl_price=80000.0,
                trade_id="t001",
            )
        finally:
            for p in patches: p.stop()
        self.assertTrue(r["_dryRun"])
        self.assertEqual(r["symbol"], "BTCUSDT")
        self.assertEqual(r["side"], "BUY")
        self.assertEqual(r["sl_side"], "SELL")
        self.assertEqual(r["sl_price"], 80000.0)   # round to tick
        self.assertEqual(r["entry_client_id"], "cresus_t001_E")
        self.assertEqual(r["sl_client_id"], "cresus_t001_SL")
        # qty: 100 / 81000 = 0.00123, round to step 0.0001 → 0.0012
        self.assertAlmostEqual(r["qty"], 0.0012, places=4)

    def test_open_short_basic(self):
        patches = self._patch_helpers()
        for p in patches: p.start()
        try:
            r = self.client.open_position(
                symbol="BTCUSDT", side="SELL",
                notional_usdt=100.0, sl_price=82000.0,
                trade_id="t002",
            )
        finally:
            for p in patches: p.stop()
        self.assertEqual(r["side"], "SELL")
        self.assertEqual(r["sl_side"], "BUY")

    def test_open_long_sl_above_current_rejected(self):
        """LONG SL 必须 < 当前价 (否则立即触发)."""
        patches = self._patch_helpers()
        for p in patches: p.start()
        try:
            with self.assertRaises(ValueError) as ctx:
                self.client.open_position(
                    symbol="BTCUSDT", side="BUY",
                    notional_usdt=100.0, sl_price=82000.0,  # > 81000
                    trade_id="t003",
                )
            self.assertIn("必须 <", str(ctx.exception))
        finally:
            for p in patches: p.stop()

    def test_open_short_sl_below_current_rejected(self):
        patches = self._patch_helpers()
        for p in patches: p.start()
        try:
            with self.assertRaises(ValueError):
                self.client.open_position(
                    symbol="BTCUSDT", side="SELL",
                    notional_usdt=100.0, sl_price=80000.0,  # < 81000
                    trade_id="t004",
                )
        finally:
            for p in patches: p.stop()

    def test_open_invalid_side(self):
        with self.assertRaises(ValueError):
            self.client.open_position(
                symbol="BTCUSDT", side="HOLD",
                notional_usdt=100, sl_price=80000, trade_id="t005",
            )

    def test_open_negative_notional(self):
        with self.assertRaises(ValueError):
            self.client.open_position(
                symbol="BTCUSDT", side="BUY",
                notional_usdt=-10, sl_price=80000, trade_id="t006",
            )

    def test_open_invalid_trade_id(self):
        with self.assertRaises(ValueError):
            self.client.open_position(
                symbol="BTCUSDT", side="BUY",
                notional_usdt=100, sl_price=80000, trade_id="has space",
            )

    def test_open_client_side_sl_marker(self):
        """use_exchange_sl=False 应在返回 dict 标记 sl_mode='client_side'."""
        patches = self._patch_helpers()
        for p in patches: p.start()
        try:
            r = self.client.open_position(
                symbol="BTCUSDT", side="BUY",
                notional_usdt=100.0, sl_price=80000.0,
                trade_id="t099", use_exchange_sl=False,
            )
        finally:
            for p in patches: p.stop()
        self.assertEqual(r["sl_mode"], "client_side")
        # SL price 仍然记录, 但 order_id/client_id 为 None (待客户端 polling)
        self.assertEqual(r["sl_price"], 80000.0)

    def test_open_default_exchange_sl_marker(self):
        """默认 use_exchange_sl=True, 返回 dict 标记 sl_mode='exchange'."""
        patches = self._patch_helpers()
        for p in patches: p.start()
        try:
            r = self.client.open_position(
                symbol="BTCUSDT", side="BUY",
                notional_usdt=100.0, sl_price=80000.0,
                trade_id="t100",
            )
        finally:
            for p in patches: p.stop()
        self.assertEqual(r["sl_mode"], "exchange")


class TestUpdateStopOrderDryRun(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        self._mock_filters = {
            "step_size": 0.0001, "min_qty": 0.0001, "max_qty": 1000.0,
            "min_notional": 50.0, "tick_size": 0.10,
            "quantity_precision": 4, "price_precision": 2, "status": "TRADING",
        }
        self._mock_klines = [[0, 0, 0, 0, "81000.00", 0, 0, 0, 0, 0, 0, 0]]
        self._mock_positions = [{"symbol": "BTCUSDT", "positionAmt": "0.0012"}]

    def _patch(self):
        return [
            patch.object(self.client, "get_symbol_filters", return_value=self._mock_filters),
            patch.object(self.client, "get_klines", return_value=self._mock_klines),
            patch.object(self.client, "get_positions", return_value=self._mock_positions),
        ]

    def test_basic_update(self):
        ps = self._patch()
        for p in ps: p.start()
        try:
            r = self.client.update_stop_order(
                symbol="BTCUSDT",
                new_stop_price=80500.0,           # < 81000 OK for LONG SELL SL
                old_sl_client_order_id="cresus_t001_SL",
                new_sl_client_order_id="cresus_t001_SLB",
                sl_side="SELL",
            )
        finally:
            for p in ps: p.stop()
        self.assertTrue(r["_dryRun"])
        self.assertEqual(r["new_sl_price"], 80500.0)
        self.assertEqual(r["new_sl_client_id"], "cresus_t001_SLB")

    def test_same_old_new_id_rejected(self):
        ps = self._patch()
        for p in ps: p.start()
        try:
            with self.assertRaises(ValueError):
                self.client.update_stop_order(
                    symbol="BTCUSDT", new_stop_price=80500.0,
                    old_sl_client_order_id="cresus_t001_SL",
                    new_sl_client_order_id="cresus_t001_SL",  # SAME
                    sl_side="SELL",
                )
        finally:
            for p in ps: p.stop()

    def test_sell_sl_above_current_rejected(self):
        """LONG SL (SELL) 必须 < 当前价."""
        ps = self._patch()
        for p in ps: p.start()
        try:
            with self.assertRaises(ValueError):
                self.client.update_stop_order(
                    symbol="BTCUSDT",
                    new_stop_price=82000.0,    # > 81000
                    old_sl_client_order_id="old_id",
                    new_sl_client_order_id="new_id",
                    sl_side="SELL",
                )
        finally:
            for p in ps: p.stop()


class TestClosePositionDryRun(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def test_dry_run_returns_zero(self):
        r = self.client.close_position(
            symbol="BTCUSDT", side="BUY", trade_id="t001",
        )
        self.assertTrue(r["_dryRun"])
        self.assertEqual(r["qty_closed"], 0)

    def test_invalid_side(self):
        with self.assertRaises(ValueError):
            self.client.close_position(symbol="BTCUSDT", side="X")

    def test_invalid_trade_id(self):
        with self.assertRaises(ValueError):
            self.client.close_position(
                symbol="BTCUSDT", side="BUY", trade_id="has space",
            )


# ============================================================================
# Phase 3.2.d: 真实 commission/手续费 (Binance /fapi/v1/userTrades)
# ============================================================================

class TestActualCommission(unittest.TestCase):
    """_actual_commission_usdt 必须基于 Binance 真实 fill 的 commission, 不是估算."""

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=False)

    def test_sums_usdt_commission_across_fills(self):
        fills = [
            {"commission": "0.0040", "commissionAsset": "USDT"},
            {"commission": "0.0035", "commissionAsset": "USDT"},
            {"commission": "0.0001", "commissionAsset": "USDT"},
        ]
        with patch.object(self.client, "get_user_trades", return_value=fills):
            fee = self.client._actual_commission_usdt("BTCUSDT", 12345)
        self.assertAlmostEqual(fee, 0.0076, places=6)

    def test_get_user_trades_passes_order_id(self):
        with patch.object(self.client, "_signed_request",
                          return_value=[]) as mock_req:
            self.client.get_user_trades("BTCUSDT", order_id=999, limit=10)
        args, kwargs = mock_req.call_args
        method, path, params = args
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/fapi/v1/userTrades")
        self.assertEqual(params["symbol"], "BTCUSDT")
        self.assertEqual(params["orderId"], 999)
        self.assertEqual(params["limit"], 10)

    def test_get_user_trades_rejects_invalid_limit(self):
        with self.assertRaises(ValueError):
            self.client.get_user_trades("BTCUSDT", limit=0)
        with self.assertRaises(ValueError):
            self.client.get_user_trades("BTCUSDT", limit=1001)

    def test_bnb_commission_returns_none_for_fallback(self):
        """BNB-discount 模式暂不支持换算 → 返 None 让上层回退估算."""
        fills = [{"commission": "0.0001", "commissionAsset": "BNB"}]
        with patch.object(self.client, "get_user_trades", return_value=fills):
            fee = self.client._actual_commission_usdt("BTCUSDT", 1)
        self.assertIsNone(fee)

    def test_empty_fills_returns_none(self):
        """API 延迟 / 订单未 settle → 没 fills → None (上层回退估算)."""
        with patch.object(self.client, "get_user_trades", return_value=[]):
            fee = self.client._actual_commission_usdt("BTCUSDT", 1)
        self.assertIsNone(fee)

    def test_api_error_returns_none(self):
        """get_user_trades 抛异常 → 返 None (上层回退估算, 不让 close 失败)."""
        with patch.object(self.client, "get_user_trades",
                          side_effect=BinanceError("rate limit")):
            fee = self.client._actual_commission_usdt("BTCUSDT", 1)
        self.assertIsNone(fee)

    def test_mixed_usdt_and_bnb_falls_back(self):
        """任何一 fill 是非 USDT → 整单回退 (避免半精确)."""
        fills = [
            {"commission": "0.004", "commissionAsset": "USDT"},
            {"commission": "0.001", "commissionAsset": "BNB"},
        ]
        with patch.object(self.client, "get_user_trades", return_value=fills):
            fee = self.client._actual_commission_usdt("BTCUSDT", 1)
        self.assertIsNone(fee)

    def test_malformed_commission_value_skipped(self):
        """坏值 fill 跳过 (不应让整次 close 崩)."""
        fills = [
            {"commission": "0.004", "commissionAsset": "USDT"},
            {"commission": "abc", "commissionAsset": "USDT"},   # malformed → skipped
            {"commission": "0.002", "commissionAsset": "USDT"},
        ]
        with patch.object(self.client, "get_user_trades", return_value=fills):
            fee = self.client._actual_commission_usdt("BTCUSDT", 1)
        self.assertAlmostEqual(fee, 0.006, places=6)


# ============================================================================
# Main
class TestPhase5AMaxQtyAndChunking(unittest.TestCase):
    """Phase 5.A-fix (5/28): MARKET_LOT_SIZE 限制 + 自动 chunking.

    场景: DYMUSDT $800 notional = 18075 units 超 MARKET_LOT_SIZE.maxQty 10000.
    之前死循环 -4005. 修复:
      - get_symbol_filters 返回 market_max_qty (vs LOT_SIZE.maxQty)
      - open_position 截断 qty (防新仓超限)
      - close_position 自动 chunking (qty > maxQty 拆多笔)
    """

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, testnet=True, dry_run=False)
        self.client._bucket.acquire = MagicMock(return_value=True)

    # === get_symbol_filters market_max_qty ===

    def test_filters_returns_market_max_qty(self):
        """get_symbol_filters 应返回 market_max_qty (来自 MARKET_LOT_SIZE.maxQty)."""
        fake_ei = {
            "symbols": [{
                "symbol": "DYMUSDT", "status": "TRADING",
                "quantityPrecision": 1, "pricePrecision": 5,
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.1",
                     "minQty": "0.1", "maxQty": "1000000"},
                    {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.1",
                     "minQty": "0.1", "maxQty": "10000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.00001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }]
        }
        with patch.object(self.client, "get_exchange_info", return_value=fake_ei):
            f = self.client.get_symbol_filters("DYMUSDT")
        self.assertEqual(f["max_qty"], 1000000.0)        # LOT_SIZE
        self.assertEqual(f["market_max_qty"], 10000.0)   # MARKET_LOT_SIZE

    def test_filters_fallback_to_lot_size_if_no_market_lot(self):
        """若 MARKET_LOT_SIZE 不存在 fallback 到 LOT_SIZE.maxQty (向后兼容)."""
        fake_ei = {
            "symbols": [{
                "symbol": "BTCUSDT", "status": "TRADING",
                "quantityPrecision": 3, "pricePrecision": 2,
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001",
                     "minQty": "0.001", "maxQty": "1000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                ],
            }]
        }
        with patch.object(self.client, "get_exchange_info", return_value=fake_ei):
            f = self.client.get_symbol_filters("BTCUSDT")
        self.assertEqual(f["market_max_qty"], 1000.0)  # 等于 LOT_SIZE.maxQty

    # === open_position qty 截断 ===

    def test_open_position_caps_qty_to_market_max(self):
        """raw_qty > market_max_qty 时, qty 被截断, notional 缩水."""
        fake_filters = {
            "step_size": 0.1, "min_qty": 0.1, "max_qty": 1000000.0,
            "market_max_qty": 10000.0, "min_notional": 5.0,
            "tick_size": 0.00001, "quantity_precision": 1, "price_precision": 5,
            "status": "TRADING",
        }
        with patch.object(self.client, "get_symbol_filters", return_value=fake_filters), \
             patch.object(self.client, "get_klines",
                          return_value=[[0,0,0,0,"0.044",0,0,0,0,0,0,0]]), \
             patch.object(self.client, "place_market_order",
                          return_value={
                              "orderId": 1, "executedQty": "10000.0",
                              "cumQuote": "440.0", "avgPrice": "0.044",
                              "status": "FILLED",
                          }), \
             patch.object(self.client, "place_stop_market_order",
                          return_value={"orderId": 2}), \
             patch.object(self.client, "_actual_commission_usdt", return_value=None):
            # $800 / $0.044 = 18181 raw → 截断到 10000 (= market_max)
            result = self.client.open_position(
                symbol="DYMUSDT", side="SELL",
                notional_usdt=800.0, sl_price=0.05,
                trade_id="test_cap_001", use_exchange_sl=False,
            )
        # qty 应被截断到 ≤ market_max
        self.assertLessEqual(float(result.get("qty", 999999)), 10000.0)

    def test_open_position_no_cap_when_qty_under_max(self):
        """raw_qty < market_max 时不截断, 行为不变."""
        fake_filters = {
            "step_size": 0.001, "min_qty": 0.001, "max_qty": 1000.0,
            "market_max_qty": 1000.0, "min_notional": 5.0,
            "tick_size": 0.01, "quantity_precision": 3, "price_precision": 2,
            "status": "TRADING",
        }
        with patch.object(self.client, "get_symbol_filters", return_value=fake_filters), \
             patch.object(self.client, "get_klines",
                          return_value=[[0,0,0,0,"50000.0",0,0,0,0,0,0,0]]), \
             patch.object(self.client, "place_market_order",
                          return_value={
                              "orderId": 1, "executedQty": "0.016",
                              "cumQuote": "800.0", "avgPrice": "50000.0",
                              "status": "FILLED",
                          }), \
             patch.object(self.client, "place_stop_market_order",
                          return_value={"orderId": 2}), \
             patch.object(self.client, "_actual_commission_usdt", return_value=None):
            # $800 / $50000 = 0.016 BTC, 远 < market_max 1000
            result = self.client.open_position(
                symbol="BTCUSDT", side="BUY",
                notional_usdt=800.0, sl_price=49000.0,
                trade_id="test_nocap_001", use_exchange_sl=False,
            )
        self.assertAlmostEqual(float(result["qty"]), 0.016, places=3)

    # === close_position chunking ===

    def _mock_position(self, symbol, amt):
        """构造 get_positions 返回的单仓数据."""
        return [{
            "symbol": symbol,
            "positionAmt": str(amt),
            "entryPrice": "0.044",
        }]

    def test_close_position_single_when_qty_under_max(self):
        """qty < market_max: 单笔关仓 (不 chunking)."""
        fake_filters = {
            "step_size": 0.1, "min_qty": 0.1, "max_qty": 1000000.0,
            "market_max_qty": 10000.0, "min_notional": 5.0,
            "tick_size": 0.00001, "quantity_precision": 1, "price_precision": 5,
            "status": "TRADING",
        }
        mock_place = MagicMock(return_value={
            "orderId": 999, "executedQty": "5000.0",
            "cumQuote": "220.0", "avgPrice": "0.044",
        })
        with patch.object(self.client, "get_positions",
                          return_value=self._mock_position("DYMUSDT", -5000.0)), \
             patch.object(self.client, "get_symbol_filters", return_value=fake_filters), \
             patch.object(self.client, "get_open_orders", return_value=[]), \
             patch.object(self.client, "place_market_order", mock_place), \
             patch.object(self.client, "_actual_commission_usdt", return_value=None):
            self.client.close_position("DYMUSDT", "SELL", trade_id="t1")
        self.assertEqual(mock_place.call_count, 1, "qty 5000 < max 10000 应单笔")

    def test_close_position_chunks_when_qty_exceeds_max(self):
        """qty > market_max: 自动拆多笔."""
        fake_filters = {
            "step_size": 0.1, "min_qty": 0.1, "max_qty": 1000000.0,
            "market_max_qty": 10000.0, "min_notional": 5.0,
            "tick_size": 0.00001, "quantity_precision": 1, "price_precision": 5,
            "status": "TRADING",
        }
        # 18075 units: 拆 2 笔 (10000 + 8075)
        mock_place = MagicMock(return_value={
            "orderId": 1, "executedQty": "10000.0",
            "cumQuote": "205.3", "avgPrice": "0.02053",
        })
        with patch.object(self.client, "get_positions",
                          return_value=self._mock_position("DYMUSDT", -18075.0)), \
             patch.object(self.client, "get_symbol_filters", return_value=fake_filters), \
             patch.object(self.client, "get_open_orders", return_value=[]), \
             patch.object(self.client, "place_market_order", mock_place), \
             patch.object(self.client, "_actual_commission_usdt", return_value=None):
            self.client.close_position("DYMUSDT", "SELL", trade_id="t1")
        self.assertEqual(mock_place.call_count, 2, "qty 18075 > max 10000 应拆 2 笔")
        # 验证 chunk size: 第一笔 10000, 第二笔 8075
        call_qtys = [c.kwargs["quantity"] for c in mock_place.call_args_list]
        self.assertEqual(call_qtys[0], 10000.0)
        self.assertAlmostEqual(call_qtys[1], 8075.0, places=4)

    def test_close_position_chunks_three_or_more(self):
        """qty ≫ market_max: 拆 3+ 笔."""
        fake_filters = {
            "step_size": 0.1, "min_qty": 0.1, "max_qty": 1000000.0,
            "market_max_qty": 10000.0, "min_notional": 5.0,
            "tick_size": 0.00001, "quantity_precision": 1, "price_precision": 5,
            "status": "TRADING",
        }
        # 25000 units: 拆 3 笔 (10000 + 10000 + 5000)
        mock_place = MagicMock(return_value={
            "orderId": 1, "executedQty": "10000.0",
            "cumQuote": "205.3", "avgPrice": "0.02053",
        })
        with patch.object(self.client, "get_positions",
                          return_value=self._mock_position("DYMUSDT", -25000.0)), \
             patch.object(self.client, "get_symbol_filters", return_value=fake_filters), \
             patch.object(self.client, "get_open_orders", return_value=[]), \
             patch.object(self.client, "place_market_order", mock_place), \
             patch.object(self.client, "_actual_commission_usdt", return_value=None):
            self.client.close_position("DYMUSDT", "SELL", trade_id="t1")
        self.assertEqual(mock_place.call_count, 3, "qty 25000 应拆 3 笔")

    def test_close_position_chunks_only_first_has_coid(self):
        """多笔时只第 1 笔用原 client_order_id (Binance 不允许重复 coid)."""
        fake_filters = {
            "step_size": 0.1, "min_qty": 0.1, "max_qty": 1000000.0,
            "market_max_qty": 10000.0, "min_notional": 5.0,
            "tick_size": 0.00001, "quantity_precision": 1, "price_precision": 5,
            "status": "TRADING",
        }
        mock_place = MagicMock(return_value={
            "orderId": 1, "executedQty": "10000.0",
            "cumQuote": "205.3", "avgPrice": "0.02053",
        })
        with patch.object(self.client, "get_positions",
                          return_value=self._mock_position("DYMUSDT", -15000.0)), \
             patch.object(self.client, "get_symbol_filters", return_value=fake_filters), \
             patch.object(self.client, "get_open_orders", return_value=[]), \
             patch.object(self.client, "place_market_order", mock_place), \
             patch.object(self.client, "_actual_commission_usdt", return_value=None):
            self.client.close_position("DYMUSDT", "SELL", trade_id="test_chunk")
        # 第 1 笔有 coid, 第 2 笔无
        self.assertEqual(mock_place.call_args_list[0].kwargs["client_order_id"],
                          "cresus_test_chunk_C")
        self.assertIsNone(mock_place.call_args_list[1].kwargs["client_order_id"])


# ============================================================================

if __name__ == "__main__":
    # 直接运行: python3 test_binance_client.py
    unittest.main(verbosity=2)
