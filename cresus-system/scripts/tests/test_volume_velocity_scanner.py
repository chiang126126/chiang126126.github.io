"""Volume velocity scanner 测试 — Phase 4.Z 大户多空比采集.

仅测试 Phase 4.Z 新增的 fetch 函数 + dataclass 字段, 不覆盖整个 scanner.
完整 scanner 测试是 future work.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from volume_velocity_scanner import (
    fetch_top_position_ratio,
    fetch_top_account_ratio,
    fetch_global_account_ratio,
    VelocityAlert,
)


class TestFetchTopPositionRatio(unittest.TestCase):
    """Phase 4.Z: /futures/data/topLongShortPositionRatio."""

    def test_success_returns_float(self):
        mock_resp = [{
            "symbol": "BTCUSDT",
            "longShortRatio": "1.5234",
            "longPosition": "0.6043",
            "shortPosition": "0.3957",
            "timestamp": "1583139600000",
        }]
        with patch("volume_velocity_scanner._http_get_json",
                   return_value=mock_resp):
            r = fetch_top_position_ratio("BTCUSDT")
        self.assertEqual(r, 1.5234)

    def test_ratio_below_one(self):
        """大户净空 (longShortRatio < 1)."""
        mock_resp = [{"longShortRatio": "0.6500"}]
        with patch("volume_velocity_scanner._http_get_json",
                   return_value=mock_resp):
            r = fetch_top_position_ratio("ETHUSDT")
        self.assertEqual(r, 0.65)

    def test_http_failure_returns_none(self):
        with patch("volume_velocity_scanner._http_get_json", return_value=None):
            r = fetch_top_position_ratio("BTCUSDT")
        self.assertIsNone(r)

    def test_empty_list_returns_none(self):
        with patch("volume_velocity_scanner._http_get_json", return_value=[]):
            r = fetch_top_position_ratio("BTCUSDT")
        self.assertIsNone(r)

    def test_malformed_returns_none(self):
        """字段缺 / 非数 → None (容错)."""
        with patch("volume_velocity_scanner._http_get_json",
                   return_value=[{"foo": "bar"}]):
            r = fetch_top_position_ratio("BTCUSDT")
        self.assertIsNone(r)

    def test_zero_ratio_returns_none(self):
        """异常 0 值 → None (避免下游除零)."""
        with patch("volume_velocity_scanner._http_get_json",
                   return_value=[{"longShortRatio": "0"}]):
            r = fetch_top_position_ratio("BTCUSDT")
        self.assertIsNone(r)

    def test_invalid_string_returns_none(self):
        with patch("volume_velocity_scanner._http_get_json",
                   return_value=[{"longShortRatio": "not_a_number"}]):
            r = fetch_top_position_ratio("BTCUSDT")
        self.assertIsNone(r)


class TestFetchTopAccountRatio(unittest.TestCase):
    """Phase 4.Z: /futures/data/topLongShortAccountRatio."""

    def test_success_returns_float(self):
        with patch("volume_velocity_scanner._http_get_json",
                   return_value=[{"longShortRatio": "2.1"}]):
            r = fetch_top_account_ratio("BTCUSDT")
        self.assertEqual(r, 2.1)

    def test_failure_returns_none(self):
        with patch("volume_velocity_scanner._http_get_json", return_value=None):
            r = fetch_top_account_ratio("BTCUSDT")
        self.assertIsNone(r)


class TestFetchGlobalAccountRatio(unittest.TestCase):
    """Phase 4.Z: /futures/data/globalLongShortAccountRatio."""

    def test_success_returns_float(self):
        with patch("volume_velocity_scanner._http_get_json",
                   return_value=[{"longShortRatio": "1.85"}]):
            r = fetch_global_account_ratio("BTCUSDT")
        self.assertEqual(r, 1.85)


class TestPhase4ZAlertFields(unittest.TestCase):
    """Phase 4.Z: VelocityAlert 新字段, 默认 None (向后兼容)."""

    def test_default_none(self):
        a = VelocityAlert(
            symbol="BTCUSDT", base="BTC", direction="LONG",
            alert_type="burst", price=100.0, price_change_pct=2.5,
            metric_window_min=1, volume_1m_usdt=100000,
            volume_baseline_usdt=50000, volume_ratio=2.0,
            detected_at="2026-05-27T00:00:00+00:00",
            intensity=1,
        )
        self.assertIsNone(a.top_trader_position_ratio)
        self.assertIsNone(a.top_trader_account_ratio)
        self.assertIsNone(a.global_account_ratio)

    def test_fields_accept_float(self):
        a = VelocityAlert(
            symbol="BTCUSDT", base="BTC", direction="LONG",
            alert_type="burst", price=100.0, price_change_pct=2.5,
            metric_window_min=1, volume_1m_usdt=100000,
            volume_baseline_usdt=50000, volume_ratio=2.0,
            detected_at="2026-05-27T00:00:00+00:00",
            intensity=1,
            top_trader_position_ratio=1.5,
            top_trader_account_ratio=1.2,
            global_account_ratio=2.0,
        )
        self.assertEqual(a.top_trader_position_ratio, 1.5)
        self.assertEqual(a.top_trader_account_ratio, 1.2)
        self.assertEqual(a.global_account_ratio, 2.0)


class TestUrlConstruction(unittest.TestCase):
    """验证 URL 构造正确, 防 typo (一次 URL 错误 = 整个数据维度报废)."""

    def test_top_position_url(self):
        captured = {}
        def fake_get(url, timeout=None):
            captured["url"] = url
            return [{"longShortRatio": "1.0"}]
        with patch("volume_velocity_scanner._http_get_json",
                   side_effect=fake_get):
            fetch_top_position_ratio("BTCUSDT")
        self.assertIn("/futures/data/topLongShortPositionRatio", captured["url"])
        self.assertIn("symbol=BTCUSDT", captured["url"])
        self.assertIn("period=5m", captured["url"])
        self.assertIn("limit=1", captured["url"])

    def test_top_account_url(self):
        captured = {}
        def fake_get(url, timeout=None):
            captured["url"] = url
            return [{"longShortRatio": "1.0"}]
        with patch("volume_velocity_scanner._http_get_json",
                   side_effect=fake_get):
            fetch_top_account_ratio("ETHUSDT")
        self.assertIn("/futures/data/topLongShortAccountRatio", captured["url"])
        self.assertIn("symbol=ETHUSDT", captured["url"])

    def test_global_account_url(self):
        captured = {}
        def fake_get(url, timeout=None):
            captured["url"] = url
            return [{"longShortRatio": "1.0"}]
        with patch("volume_velocity_scanner._http_get_json",
                   side_effect=fake_get):
            fetch_global_account_ratio("SOLUSDT")
        self.assertIn("/futures/data/globalLongShortAccountRatio", captured["url"])
        self.assertIn("symbol=SOLUSDT", captured["url"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
