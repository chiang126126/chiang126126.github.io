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
    _validate_client_order_id, _format_quantity, _format_price,
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
                with self.assertRaises(BinanceTimeError):
                    self.client._do_request("GET", "https://x/y", signed=True)

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

    def test_place_stop_market_close_position_default(self):
        r = self.client.place_stop_market_order("BTCUSDT", "SELL", 75000)
        self.assertEqual(r["status"], "DRY_RUN")
        self.assertEqual(r["type"], "STOP_MARKET")
        self.assertEqual(r["closePosition"], "true")
        self.assertEqual(r["stopPrice"], "75000")

    def test_place_stop_market_with_quantity(self):
        r = self.client.place_stop_market_order(
            "BTCUSDT", "SELL", 75000,
            quantity=0.001, close_position=False,
        )
        self.assertEqual(r["origQty"], "0.001")
        # 验证内部 _params 包含 reduceOnly=true
        self.assertEqual(r["_params"].get("reduceOnly"), "true")

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
                close_position=False,  # 不带 quantity → 矛盾
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
# Main
# ============================================================================

if __name__ == "__main__":
    # 直接运行: python3 test_binance_client.py
    unittest.main(verbosity=2)
