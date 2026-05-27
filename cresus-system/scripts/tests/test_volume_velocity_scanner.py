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


class TestPhase5AScoreBasedNotional(unittest.TestCase):
    """Phase 5.A (5/27): conviction score 分档仓位.

    数据驱动 (1410 笔 paper):
      score 5  (92%): avg +$0.92 → $400 基准
      score 6-7 (7%): avg +$4.50 → $800 (2× 加大)
      score 8+ (0.5%): avg -$17.78 (n=7 反向证据) → $200 减半
    """

    def test_score_5_baseline(self):
        from volume_velocity_scanner import _notional_for_score
        self.assertEqual(_notional_for_score(5), 400.0)

    def test_score_6_7_doubled(self):
        from volume_velocity_scanner import _notional_for_score
        self.assertEqual(_notional_for_score(6), 800.0)
        self.assertEqual(_notional_for_score(7), 800.0)

    def test_score_8_plus_halved(self):
        """高分反向证据 — score 8+ 减半到 $200."""
        from volume_velocity_scanner import _notional_for_score
        self.assertEqual(_notional_for_score(8), 200.0)
        self.assertEqual(_notional_for_score(9), 200.0)
        self.assertEqual(_notional_for_score(10), 200.0)

    def test_score_missing_fallback(self):
        from volume_velocity_scanner import _notional_for_score, PAPER_NOTIONAL_PER_TRADE_USDT
        self.assertEqual(_notional_for_score(None), PAPER_NOTIONAL_PER_TRADE_USDT)
        self.assertEqual(_notional_for_score("invalid"), PAPER_NOTIONAL_PER_TRADE_USDT)

    def test_atr_threshold_relaxed(self):
        """Phase 5.A: ATR 拒从 2.0 → 3.0 (数据: ATR≥2% avg +$7.14 最高 EV)."""
        from volume_velocity_scanner import PAPER_MAX_ATR_PCT
        self.assertEqual(PAPER_MAX_ATR_PCT, 3.0)


class TestPhase5AAltSeasonPenalty(unittest.TestCase):
    """Phase 5.A: ALT_SEASON_RUNNING + LONG conviction -1.

    数据驱动: 156 笔 avg +$0.06 win 33% (准负 EV).
    软减分而非硬拒, 其他维度强信号仍可救.
    """

    def _make_alert(self, direction="LONG", change_1h=2.0, change_4h=2.0,
                     funding=0.5, oi_delta=1.0):
        from volume_velocity_scanner import VelocityAlert
        return VelocityAlert(
            symbol="BTCUSDT", base="BTC", direction=direction,
            alert_type="burst", price=100.0, price_change_pct=2.5,
            metric_window_min=1, volume_1m_usdt=100000,
            volume_baseline_usdt=10000, volume_ratio=10.0,
            detected_at="2026-05-27T00:00:00+00:00", intensity=2,
            change_1h_pct=change_1h, change_4h_pct=change_4h,
            funding_rate_pct=funding, oi_delta_5m_pct=oi_delta,
        )

    def test_alt_season_long_gets_penalty(self):
        """ALT_SEASON_RUNNING + LONG → score 减 1."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="LONG")
        score_no_regime, _ = _compute_conviction(a, None, regime=None)
        score_alt, _ = _compute_conviction(a, None, regime="ALT_SEASON_RUNNING")
        self.assertEqual(score_alt, max(0, score_no_regime - 1),
                          "ALT_SEASON_RUNNING+LONG 应减 1 (但不低于 0)")

    def test_alt_season_short_no_penalty(self):
        """ALT_SEASON_RUNNING + SHORT → 不减分 (数据未显示需要)."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="SHORT", change_1h=-2.0, change_4h=-2.0)
        score_no_regime, _ = _compute_conviction(a, None, regime=None)
        score_alt, _ = _compute_conviction(a, None, regime="ALT_SEASON_RUNNING")
        self.assertEqual(score_alt, score_no_regime, "ALT_SEASON_RUNNING+SHORT 不应减分")

    def test_other_regimes_no_penalty(self):
        """RISK_OFF / RANGE_BORING 等其他 regime 不减分."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="LONG")
        base, _ = _compute_conviction(a, None, regime=None)
        for r in ["RISK_OFF", "RANGE_BORING", None]:
            score, _ = _compute_conviction(a, None, regime=r)
            self.assertEqual(score, base, f"regime={r} 不应触发减分")

    def test_score_never_negative(self):
        """边界: 即使所有指标都不利, score 永不为负 (tier 计算要非负)."""
        from volume_velocity_scanner import _compute_conviction
        # 低 base score + ALT_SEASON 减分 → 不能负
        a = self._make_alert(direction="LONG", change_1h=0.5, change_4h=0.5,
                              funding=0.0, oi_delta=-1.0)
        score, _ = _compute_conviction(a, None, regime="ALT_SEASON_RUNNING")
        self.assertGreaterEqual(score, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
