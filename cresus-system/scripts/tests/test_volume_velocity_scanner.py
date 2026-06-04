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

    def test_score_5_reduced_to_200(self):
        """Phase 5.K (6/1): score 5 减半 400→200 (低 EV 信号缩仓).
        依据 5/31 数据: paper EV +$0.92 - 实盘摩擦 $2-3 = 净 EV ≈ 0.
        """
        from volume_velocity_scanner import _notional_for_score
        self.assertEqual(_notional_for_score(5), 200.0)

    def test_score_6_stays_at_400(self):
        """Phase 5.K-adjust (6/1): score 6 撤回 5.A-restore 的 800, 保持 $400.
        5/31+6/1 实盘 6 笔 score 6 全亏 avg -$5.83, 与历史 EV +$4.34 矛盾.
        n=6 太小不能定论, 但 risk 翻倍不值, 等更多数据.
        """
        from volume_velocity_scanner import _notional_for_score
        self.assertEqual(_notional_for_score(6), 400.0)

    def test_score_7_restored_to_800(self):
        """Phase 5.A-restore: score 7 维持 $800. 5/31+6/1 共 11 笔 avg +$4.27."""
        from volume_velocity_scanner import _notional_for_score
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

    def test_alt_season_short_now_gets_penalty(self):
        """Phase 5.N (6/1): ALT_SEASON_RUNNING + SHORT 现在也减分.
        旧 Phase 5.A 只减 LONG, 5.N 基于 live 数据 (-$1.81 avg) 补上 SHORT 减分.
        """
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="SHORT", change_1h=-2.0, change_4h=-2.0)
        score_no_regime, _ = _compute_conviction(a, None, regime=None)
        score_alt, _ = _compute_conviction(a, None, regime="ALT_SEASON_RUNNING")
        self.assertEqual(score_alt, max(0, score_no_regime - 1),
                          "Phase 5.N: ALT_SEASON_RUNNING+SHORT 应 -1 (live 反向追单陷阱)")

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


class TestPhase5NRegimeShortPenalty(unittest.TestCase):
    """Phase 5.N (6/1): regime + direction live 亏损陷阱 -1 conviction.

    数据驱动 (5/26+ live 830 笔):
      RANGE_BORING + SHORT:        128 笔 avg -$1.45 ⚠️
      ALT_SEASON_RUNNING + SHORT:   27 笔 avg -$1.81 ⚠️
    保留: RISK_OFF + SHORT (live avg +$0.64, 唯一稳定盈利)
    """

    def _make_short_alert(self):
        from volume_velocity_scanner import VelocityAlert
        return VelocityAlert(
            symbol="BTCUSDT", base="BTC", direction="SHORT",
            alert_type="burst", price=100.0, price_change_pct=-2.5,
            metric_window_min=1, volume_1m_usdt=100000,
            volume_baseline_usdt=10000, volume_ratio=10.0,
            detected_at="2026-06-01T00:00:00+00:00", intensity=2,
            change_1h_pct=-2.0, change_4h_pct=-2.0,
            funding_rate_pct=0.5, oi_delta_5m_pct=-1.0,
        )

    def test_range_boring_short_penalized(self):
        """RANGE_BORING + SHORT 应 -1 分 (live 实盘亏损陷阱)."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_short_alert()
        base, _ = _compute_conviction(a, None, regime=None)
        penalized, _ = _compute_conviction(a, None, regime="RANGE_BORING")
        self.assertEqual(penalized, max(0, base - 1),
                          "Phase 5.N: RANGE_BORING+SHORT 应减 1 分")

    def test_alt_season_short_penalized(self):
        """ALT_SEASON_RUNNING + SHORT 应 -1 分."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_short_alert()
        base, _ = _compute_conviction(a, None, regime=None)
        penalized, _ = _compute_conviction(a, None, regime="ALT_SEASON_RUNNING")
        self.assertEqual(penalized, max(0, base - 1),
                          "Phase 5.N: ALT_SEASON_RUNNING+SHORT 应减 1 分")

    def test_risk_off_short_not_penalized(self):
        """RISK_OFF + SHORT 不减分 (这是 live 唯一盈利组合, 必须保留)."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_short_alert()
        base, _ = _compute_conviction(a, None, regime=None)
        risk_off, _ = _compute_conviction(a, None, regime="RISK_OFF")
        self.assertEqual(risk_off, base,
                          "Phase 5.N: RISK_OFF+SHORT 不应减分 (唯一 live 盈利)")

    def test_range_boring_long_not_penalized(self):
        """RANGE_BORING + LONG 不减分 (LONG 是 break-even, 不是亏损陷阱)."""
        from volume_velocity_scanner import _compute_conviction, VelocityAlert
        # 构造 LONG 信号
        a = VelocityAlert(
            symbol="BTCUSDT", base="BTC", direction="LONG",
            alert_type="burst", price=100.0, price_change_pct=2.5,
            metric_window_min=1, volume_1m_usdt=100000,
            volume_baseline_usdt=10000, volume_ratio=10.0,
            detected_at="2026-06-01T00:00:00+00:00", intensity=2,
            change_1h_pct=2.0, change_4h_pct=2.0,
            funding_rate_pct=0.5, oi_delta_5m_pct=1.0,
        )
        base, _ = _compute_conviction(a, None, regime=None)
        rb_long, _ = _compute_conviction(a, None, regime="RANGE_BORING")
        self.assertEqual(rb_long, base, "RANGE_BORING+LONG 不减分")


class TestPhase5BBtcRegimeBonus(unittest.TestCase):
    """Phase 5.B: BTC regime trend-aligned conviction +1.
    BTC up + LONG → +1, BTC down + SHORT → +1, 其他不动.
    """

    def _make_alert(self, direction="LONG"):
        from volume_velocity_scanner import VelocityAlert
        return VelocityAlert(
            symbol="BTCUSDT", base="BTC", direction=direction,
            alert_type="burst", price=100.0, price_change_pct=2.5,
            metric_window_min=1, volume_1m_usdt=100000,
            volume_baseline_usdt=10000, volume_ratio=10.0,
            detected_at="2026-05-27T00:00:00+00:00", intensity=2,
            change_1h_pct=2.0, change_4h_pct=2.0,
            funding_rate_pct=0.5, oi_delta_5m_pct=1.0,
        )

    def test_btc_up_long_gets_bonus(self):
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="LONG")
        base, _ = _compute_conviction(a, None, btc_regime=None)
        bonus, _ = _compute_conviction(a, None, btc_regime="up")
        self.assertEqual(bonus, base + 1, "BTC up + LONG 应 +1")

    def test_btc_down_short_gets_bonus(self):
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="SHORT")
        # SHORT 信号: 反向 1h/4h 即正向(下跌)
        a.change_1h_pct = -2.0
        a.change_4h_pct = -2.0
        base, _ = _compute_conviction(a, None, btc_regime=None)
        bonus, _ = _compute_conviction(a, None, btc_regime="down")
        self.assertEqual(bonus, base + 1, "BTC down + SHORT 应 +1")

    def test_btc_up_short_no_bonus(self):
        """逆势不加分."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="SHORT")
        a.change_1h_pct = -2.0
        a.change_4h_pct = -2.0
        base, _ = _compute_conviction(a, None, btc_regime=None)
        no_bonus, _ = _compute_conviction(a, None, btc_regime="up")
        self.assertEqual(no_bonus, base, "BTC up + SHORT 不应加分")

    def test_btc_chop_no_bonus(self):
        """chop regime 不加不减."""
        from volume_velocity_scanner import _compute_conviction
        a = self._make_alert(direction="LONG")
        base, _ = _compute_conviction(a, None, btc_regime=None)
        chop, _ = _compute_conviction(a, None, btc_regime="chop")
        self.assertEqual(chop, base, "BTC chop 不应加分")


class TestPhase5CTp1PartialClose(unittest.TestCase):
    """Phase 5.C: TP1 触发时部分平仓 A/B 测试.
    A 组 (50%): 维持满仓走 trailing.
    B 组 (50%): 锁 50% 利润, 剩 50% 继续 trailing.
    """

    def test_mode_off_never_partial(self):
        from volume_velocity_scanner import _use_tp1_partial_close
        for pid in ["BTC|LONG|t1", "X|SHORT|t", ""]:
            self.assertFalse(_use_tp1_partial_close(pid, "off"))

    def test_mode_always_always_partial(self):
        from volume_velocity_scanner import _use_tp1_partial_close
        for pid in ["BTC|LONG|t1", "X|SHORT|t"]:
            self.assertTrue(_use_tp1_partial_close(pid, "always"))

    def test_mode_ab_deterministic(self):
        """同 paper_id 必须返回同样分组."""
        from volume_velocity_scanner import _use_tp1_partial_close
        pid = "STORJUSDT|LONG|2026-05-27T10:00:00+00:00"
        results = [_use_tp1_partial_close(pid, "ab") for _ in range(10)]
        self.assertTrue(all(r == results[0] for r in results))

    def test_mode_ab_roughly_50_50(self):
        """大样本接近 50/50."""
        from volume_velocity_scanner import _use_tp1_partial_close
        ids = [f"SYM{i}|LONG|2026-05-{(i%28)+1:02d}T00:00:00" for i in range(1, 1001)]
        true_n = sum(1 for pid in ids if _use_tp1_partial_close(pid, "ab"))
        ratio = true_n / 1000
        self.assertGreater(ratio, 0.40)
        self.assertLess(ratio, 0.60)

    def test_mode_ab_empty_pid_returns_false(self):
        from volume_velocity_scanner import _use_tp1_partial_close
        self.assertFalse(_use_tp1_partial_close("", "ab"))
        self.assertFalse(_use_tp1_partial_close(None, "ab"))

    def test_apply_partial_close_b_group(self):
        """B 组: 触 TP1 锁 50%, notional 减半."""
        from volume_velocity_scanner import _apply_tp1_partial_close, PAPER_FEE_PCT_ROUND_TRIP
        # 构造一个 MD5 落在 B 组的 paper_id (h%2==1)
        # paper_id "B" hash → 9d5ed678fe57bcca... 转 int 不一定 ==1, 多试几个
        b_group_pid = None
        for i in range(100):
            import hashlib
            test_pid = f"TEST|LONG|t{i}"
            h = int(hashlib.md5(test_pid.encode("utf-8")).hexdigest(), 16)
            if (h % 2) == 1:
                b_group_pid = test_pid
                break
        self.assertIsNotNone(b_group_pid)
        # entry=100, current=110 (10% gain on LONG = 10% gross)
        t = {"id": b_group_pid, "notional_usdt": 400.0}
        _apply_tp1_partial_close(t, cur=110.0, entry=100.0, is_long=True)
        self.assertTrue(t["tp1_partial_closed"])
        self.assertEqual(t["notional_usdt"], 200.0)  # 减半
        # 锁定金额 = 200 × (10% - 0.08%) / 100 = 200 × 9.92 / 100 = $19.84
        expected = round(200.0 * (10.0 - PAPER_FEE_PCT_ROUND_TRIP) / 100.0, 2)
        self.assertEqual(t["tp1_locked_pnl_usdt"], expected)

    def test_apply_partial_close_a_group(self):
        """A 组: 不改 trade, 仅标记."""
        from volume_velocity_scanner import _apply_tp1_partial_close
        # 找一个 A 组 (h%2==0) 的 pid
        a_group_pid = None
        for i in range(100):
            import hashlib
            test_pid = f"TEST|LONG|t{i}"
            h = int(hashlib.md5(test_pid.encode("utf-8")).hexdigest(), 16)
            if (h % 2) == 0:
                a_group_pid = test_pid
                break
        self.assertIsNotNone(a_group_pid)
        t = {"id": a_group_pid, "notional_usdt": 400.0}
        _apply_tp1_partial_close(t, cur=110.0, entry=100.0, is_long=True)
        self.assertFalse(t["tp1_partial_closed"])
        self.assertEqual(t["notional_usdt"], 400.0)  # 不变
        self.assertNotIn("tp1_locked_pnl_usdt", t)

    def test_apply_partial_close_short(self):
        """SHORT 同 LONG 公式 (取反 raw_pct)."""
        from volume_velocity_scanner import _apply_tp1_partial_close, PAPER_FEE_PCT_ROUND_TRIP
        b_group_pid = None
        for i in range(100):
            import hashlib
            test_pid = f"TEST|SHORT|t{i}"
            h = int(hashlib.md5(test_pid.encode("utf-8")).hexdigest(), 16)
            if (h % 2) == 1:
                b_group_pid = test_pid
                break
        # entry=100, current=90 (SHORT 赚 10%)
        t = {"id": b_group_pid, "notional_usdt": 400.0}
        _apply_tp1_partial_close(t, cur=90.0, entry=100.0, is_long=False)
        self.assertTrue(t["tp1_partial_closed"])
        # SHORT 利润 = -(90-100)/100 × 100 = +10% gross
        expected = round(200.0 * (10.0 - PAPER_FEE_PCT_ROUND_TRIP) / 100.0, 2)
        self.assertEqual(t["tp1_locked_pnl_usdt"], expected)


class TestPhase6BLossReductionFilters(unittest.TestCase):
    """Phase 6.B (2026-06-03): 实战亏损反馈 filter + 浮盈保护.

    A. 历史 30m 胜率 < 25% AND N >= 20 → reject
    B. SL distance < 0.3% → reject
    C. Phase A 浮盈达 1.0R 时 SL 移到 entry (breakeven shift)
    """

    def _make_alert(self, symbol="TEST", direction="SHORT", score=5,
                    price=100.0, sl_distance_pct=0.5, atr_pct=0.5):
        """构造钻石 conviction VelocityAlert for testing.

        sl_distance_pct: SL 距入场价的百分比 (绝对值). 函数根据 direction 自动放
            正确方向 (LONG=入场下方 / SHORT=入场上方), 避免手写 SL/TP 顺序错.
        """
        from volume_velocity_scanner import VelocityAlert
        a = VelocityAlert(
            symbol=symbol, base=symbol.replace("USDT", ""), direction=direction,
            alert_type="sustained", price=price,
            price_change_pct=-2.0, metric_window_min=10,
            volume_1m_usdt=1000.0, volume_baseline_usdt=200.0, volume_ratio=5.0,
            detected_at="2026-06-03T20:00:00+00:00", intensity=2,
        )
        a.atr_pct = atr_pct
        sl_dist = price * sl_distance_pct / 100.0
        if direction == "LONG":
            a.suggested_sl = price - sl_dist
            a.suggested_tp1 = price + 1.5 * sl_dist
            a.suggested_tp2 = price + 3.0 * sl_dist
        else:  # SHORT
            a.suggested_sl = price + sl_dist
            a.suggested_tp1 = price - 1.5 * sl_dist
            a.suggested_tp2 = price - 3.0 * sl_dist
        a.conviction_score = score
        a.conviction_tier = "diamond"
        return a

    def _make_state(self):
        return {"open_trades": [], "closed_trades": []}

    def _make_winrate_summary(self, symbol, direction, alert_type, n, win_rate, mu=0.0):
        return {
            "by_key": {
                f"{symbol}|{alert_type}|{direction}": {
                    "stages": {
                        "30m": {"n": n, "win_rate": win_rate, "avg_outcome_pct": mu}
                    }
                }
            }
        }

    # === Tier 1A: 历史胜率 filter ===

    def test_6b_a_low_winrate_rejected(self):
        """BASEDUSDT 12% N=32 → reject."""
        from volume_velocity_scanner import _open_paper_trade
        from datetime import datetime, timezone, timedelta
        a = self._make_alert(symbol="BASEDUSDT")
        ws = self._make_winrate_summary("BASEDUSDT", "SHORT", "sustained",
                                         n=32, win_rate=0.12, mu=-0.90)
        result = _open_paper_trade(a, self._make_state(),
                                    datetime.now(timezone.utc), 1000.0,
                                    winrate_summary=ws)
        self.assertIsNone(result, "历史 30m 胜率 12% N=32 应被 reject")

    def test_6b_a_low_sample_not_rejected(self):
        """DRAMUSDT 0% N=10 — N 不足不应被 winrate filter 拒 (会被 1B SL 距离拒)."""
        from volume_velocity_scanner import _open_paper_trade
        from datetime import datetime, timezone, timedelta
        # 给个充裕 SL 距离避免 1B 干扰, 单独测 1A
        a = self._make_alert(symbol="DRAMUSDT", price=100.0, sl_distance_pct=0.5)
        ws = self._make_winrate_summary("DRAMUSDT", "SHORT", "sustained",
                                         n=10, win_rate=0.0, mu=-0.79)
        result = _open_paper_trade(a, self._make_state(),
                                    datetime.now(timezone.utc), 1000.0,
                                    winrate_summary=ws)
        self.assertIsNotNone(result, "N=10 < 20 不应被 winrate filter 拒")

    def test_6b_a_high_winrate_passes(self):
        """胜率 >= 25% 不应被拒."""
        from volume_velocity_scanner import _open_paper_trade
        from datetime import datetime, timezone, timedelta
        a = self._make_alert(symbol="MONUSDT")
        ws = self._make_winrate_summary("MONUSDT", "SHORT", "sustained",
                                         n=74, win_rate=0.42, mu=0.84)
        result = _open_paper_trade(a, self._make_state(),
                                    datetime.now(timezone.utc), 1000.0,
                                    winrate_summary=ws)
        self.assertIsNotNone(result, "胜率 42% N=74 应通过 1A filter")

    def test_6b_a_no_winrate_data_passes(self):
        """无 winrate_summary 时 (新 symbol / 测试) → 不应被拒 (fail-safe)."""
        from volume_velocity_scanner import _open_paper_trade
        from datetime import datetime, timezone, timedelta
        a = self._make_alert(symbol="NEWUSDT")
        result = _open_paper_trade(a, self._make_state(),
                                    datetime.now(timezone.utc), 1000.0,
                                    winrate_summary=None)
        self.assertIsNotNone(result, "无历史数据时不应被 1A 拒")

    # === Tier 1B: SL 距离 filter ===

    def test_6b_b_micro_sl_rejected(self):
        """DRAMUSDT R=0.23% → reject."""
        from volume_velocity_scanner import _open_paper_trade
        from datetime import datetime, timezone, timedelta
        a = self._make_alert(symbol="DRAMUSDT", price=100.0, sl_distance_pct=0.23)
        result = _open_paper_trade(a, self._make_state(),
                                    datetime.now(timezone.utc), 1000.0)
        self.assertIsNone(result, "SL 距离 0.23% < 0.3% 应被 1B reject")

    def test_6b_b_normal_sl_passes(self):
        """SL >= 0.3% 应通过."""
        from volume_velocity_scanner import _open_paper_trade
        from datetime import datetime, timezone, timedelta
        a = self._make_alert(symbol="NORMALUSDT", price=100.0, sl_distance_pct=0.5)
        result = _open_paper_trade(a, self._make_state(),
                                    datetime.now(timezone.utc), 1000.0)
        self.assertIsNotNone(result, "SL 距离 0.5% 应通过 1B filter")

    def test_6b_b_long_micro_sl_rejected(self):
        """LONG 方向 R=0.2% 也应被拒."""
        from volume_velocity_scanner import _open_paper_trade
        from datetime import datetime, timezone, timedelta
        a = self._make_alert(symbol="X", direction="LONG", price=100.0,
                              sl_distance_pct=0.2)
        result = _open_paper_trade(a, self._make_state(),
                                    datetime.now(timezone.utc), 1000.0)
        self.assertIsNone(result, "LONG SL 0.2% < 0.3% 应被 1B reject")

    # === Tier 1C: Breakeven shift ===

    def test_6b_c_breakeven_shift_short(self):
        """SHORT 浮盈达 1.0R 时 SL 应被移到 entry."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        # SHORT: entry 100, SL 101 (R=1%), 当前价 99 = 浮盈 1R
        state = {"open_trades": [{
            "symbol": "TESTUSDT", "direction": "SHORT",
            "entry_price": 100.0, "sl": 101.0, "tp1": 98.5, "tp2": 97.0,
            "phase": "A", "entered_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "atr_pct": 0.5, "notional_usdt": 150.0,
            "high_water_mark": 99.0,
        }], "closed_trades": []}
        prices = {"TESTUSDT": 99.0}  # 浮盈 = 1.0R
        closed, closed_list, transitions = _update_paper_trades(
            state, prices, datetime.now(timezone.utc),
        )
        t = state["open_trades"][0]
        self.assertEqual(t["sl"], 100.0, "SL 应移到 entry (breakeven)")
        self.assertTrue(t.get("_breakeven_shifted"), "_breakeven_shifted flag 应为 True")
        self.assertEqual(t["phase"], "A", "仍应在 Phase A (TP1 没触发)")

    def test_6b_c_breakeven_shift_long(self):
        """LONG 浮盈达 1.0R 时 SL 应被移到 entry."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        state = {"open_trades": [{
            "symbol": "TESTUSDT", "direction": "LONG",
            "entry_price": 100.0, "sl": 99.0, "tp1": 101.5, "tp2": 103.0,
            "phase": "A", "entered_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "atr_pct": 0.5, "notional_usdt": 150.0,
            "high_water_mark": 101.0,
        }], "closed_trades": []}
        prices = {"TESTUSDT": 101.0}  # 浮盈 = 1.0R
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        t = state["open_trades"][0]
        self.assertEqual(t["sl"], 100.0, "SL 应移到 entry (breakeven)")
        self.assertTrue(t.get("_breakeven_shifted"))

    def test_6b_c_below_1r_no_shift(self):
        """浮盈 < 1.0R 不应触发 breakeven shift."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        state = {"open_trades": [{
            "symbol": "X", "direction": "SHORT",
            "entry_price": 100.0, "sl": 101.0, "tp1": 98.5, "tp2": 97.0,
            "phase": "A", "entered_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "atr_pct": 0.5, "notional_usdt": 150.0,
            "high_water_mark": 99.5,
        }], "closed_trades": []}
        prices = {"X": 99.5}  # 浮盈 0.5R
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        t = state["open_trades"][0]
        self.assertEqual(t["sl"], 101.0, "SL 不应改变 (浮盈 < 1.0R)")
        self.assertFalse(t.get("_breakeven_shifted", False))

    def test_6b_c_idempotent_no_double_shift(self):
        """第二次 update 不应重复 shift (已 shifted flag 防御)."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        state = {"open_trades": [{
            "symbol": "X", "direction": "SHORT",
            "entry_price": 100.0, "sl": 101.0, "tp1": 98.5, "tp2": 97.0,
            "phase": "A", "entered_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "atr_pct": 0.5, "notional_usdt": 150.0,
            "high_water_mark": 99.0,
        }], "closed_trades": []}
        prices = {"X": 99.0}
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        # 第一次 shift, SL 现在是 100
        t = state["open_trades"][0]
        self.assertEqual(t["sl"], 100.0)
        # 模拟价格回拉到 99.5 (浮盈 < 1R 但 SL 已是 100), 再 update
        # 理论上不会再 shift (flag 已 True)
        # 但 99.5 < SL=100 不 violates SL trigger 条件 (SHORT cur >= sl 才 trigger),
        # 所以仍然 open. SL 应保持 100, 不再改.
        prices2 = {"X": 99.5}
        _update_paper_trades(state, prices2, datetime.now(timezone.utc))
        # state 还应包含 trade (SL 100 > cur 99.5, 没触发)
        if state["open_trades"]:
            t2 = state["open_trades"][0]
            self.assertEqual(t2["sl"], 100.0, "SL 不应被再次改变")
            self.assertTrue(t2.get("_breakeven_shifted"))


class TestPhase6CExtendedProtection(unittest.TestCase):
    """Phase 6.C (2026-06-04): 0.8R 中间保护 + Funding 方向感知评分.

    A. 0.8R milestone: SL 移到 entry ± 0.2R (Phase A 内中间保护)
    B. Funding 方向感知: 追拥挤方向减分, fade 拥挤方向加分
    """

    def _make_trade(self, direction="SHORT", entry=100.0, sl=101.0,
                    tp1=None, tp2=None, hwm=None, initial_r=None):
        """构造一个 Phase A trade dict for testing."""
        from datetime import datetime, timezone, timedelta
        if tp1 is None:
            tp1 = entry - 1.5 * abs(entry - sl) if direction == "SHORT" else entry + 1.5 * abs(entry - sl)
        if tp2 is None:
            tp2 = entry - 3.0 * abs(entry - sl) if direction == "SHORT" else entry + 3.0 * abs(entry - sl)
        if hwm is None:
            hwm = entry
        if initial_r is None:
            initial_r = abs(entry - sl)
        return {
            "symbol": "TESTUSDT", "direction": direction,
            "entry_price": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
            "initial_r": initial_r,
            "phase": "A", "entered_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "atr_pct": 0.5, "notional_usdt": 150.0,
            "high_water_mark": hwm,
        }

    # === Phase 6.C-A: 0.8R intermediate shift ===

    def test_6c_a_short_0_8r_milestone(self):
        """SHORT 浮盈 0.8R 时 SL 应移到 entry + 0.2R (不是 entry)."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        # SHORT: entry 100, SL 101 (1R=1), 当前 99.2 (浮盈 0.8R)
        state = {"open_trades": [self._make_trade(
            direction="SHORT", entry=100.0, sl=101.0, hwm=99.2,
        )], "closed_trades": []}
        prices = {"TESTUSDT": 99.2}
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        t = state["open_trades"][0]
        # SL 应是 entry + 0.2R = 100 + 0.2 = 100.2
        self.assertAlmostEqual(t["sl"], 100.2, places=3,
                                msg="SHORT 0.8R 触发, SL 应移到 entry + 0.2R")
        self.assertEqual(t.get("_profit_milestone"), 0.8)
        self.assertEqual(t["phase"], "A", "仍应在 Phase A (没到 TP1)")

    def test_6c_a_long_0_8r_milestone(self):
        """LONG 浮盈 0.8R 时 SL 应移到 entry - 0.2R."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        # LONG: entry 100, SL 99 (1R=1), 当前 100.8 (浮盈 0.8R)
        state = {"open_trades": [self._make_trade(
            direction="LONG", entry=100.0, sl=99.0, hwm=100.8,
        )], "closed_trades": []}
        prices = {"TESTUSDT": 100.8}
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        t = state["open_trades"][0]
        # SL 应是 entry - 0.2R = 100 - 0.2 = 99.8
        self.assertAlmostEqual(t["sl"], 99.8, places=3)
        self.assertEqual(t.get("_profit_milestone"), 0.8)

    def test_6c_a_milestone_progression_0_8_to_1_0(self):
        """0.8R → 1.0R 进阶: 第一次 update 触发 0.8, 第二次进 1.0."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        state = {"open_trades": [self._make_trade(
            direction="SHORT", entry=100.0, sl=101.0, hwm=99.2,
        )], "closed_trades": []}
        # First update at 0.8R
        prices = {"TESTUSDT": 99.2}
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        t = state["open_trades"][0]
        self.assertAlmostEqual(t["sl"], 100.2, places=3, msg="0.8R 触发后 SL=100.2")
        self.assertEqual(t.get("_profit_milestone"), 0.8)
        # Second update at 1.0R
        # hwm 跟踪 SHORT 最低价: 99.0
        t["high_water_mark"] = 99.0
        prices2 = {"TESTUSDT": 99.0}
        _update_paper_trades(state, prices2, datetime.now(timezone.utc))
        t2 = state["open_trades"][0]
        # 1.0R 触发, SL 应移到 entry = 100
        self.assertAlmostEqual(t2["sl"], 100.0, places=3, msg="1.0R 触发后 SL=entry=100")
        self.assertEqual(t2.get("_profit_milestone"), 1.0)
        # backward compat flag
        self.assertTrue(t2.get("_breakeven_shifted"))

    def test_6c_a_no_regression_when_retraces(self):
        """到 1.0R 后再回到 0.8R 不应触发 (milestone 只前进不后退)."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        state = {"open_trades": [self._make_trade(
            direction="SHORT", entry=100.0, sl=101.0, hwm=99.0,
        )], "closed_trades": []}
        # First update: 1.0R 触发, milestone=1.0, SL=100
        prices = {"TESTUSDT": 99.0}
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        t = state["open_trades"][0]
        self.assertEqual(t.get("_profit_milestone"), 1.0)
        self.assertEqual(t["sl"], 100.0)
        # 价格回拉到 99.5 (浮盈 0.5R, 在 0.8 以下)
        # SL 仍是 100 (没触发 SHORT SL: cur=99.5 >= 100? No, 99.5 < 100, OK)
        prices2 = {"TESTUSDT": 99.5}
        _update_paper_trades(state, prices2, datetime.now(timezone.utc))
        if state["open_trades"]:
            t2 = state["open_trades"][0]
            self.assertEqual(t2["sl"], 100.0, "SL 不应被回退")
            self.assertEqual(t2.get("_profit_milestone"), 1.0)

    def test_6c_a_direct_1_0r_skips_0_8(self):
        """如果直接到 1.0R (跳过 0.8R), milestone 应直接是 1.0 不是 0.8."""
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        # 第一次 update 就到 1.0R: SHORT entry 100 SL 101 (R=1), 价格 99
        state = {"open_trades": [self._make_trade(
            direction="SHORT", entry=100.0, sl=101.0, hwm=99.0,
        )], "closed_trades": []}
        prices = {"TESTUSDT": 99.0}
        _update_paper_trades(state, prices, datetime.now(timezone.utc))
        t = state["open_trades"][0]
        self.assertEqual(t.get("_profit_milestone"), 1.0,
                          "应直接进 1.0 milestone, 跳过 0.8 中间步骤")
        self.assertEqual(t["sl"], 100.0)

    def test_6c_a_initial_r_field_preserved_across_shifts(self):
        """initial_r 字段在 SL 移动后仍应正确反映原始 1R."""
        from volume_velocity_scanner import _update_paper_trades, _initial_r_distance
        from datetime import datetime, timezone, timedelta
        state = {"open_trades": [self._make_trade(
            direction="SHORT", entry=100.0, sl=101.0, hwm=99.0,
        )], "closed_trades": []}
        # Trigger 1.0R BE, SL → 100
        _update_paper_trades(state, {"TESTUSDT": 99.0},
                              datetime.now(timezone.utc))
        t = state["open_trades"][0]
        # initial_r 应保持 1.0 (不受 SL 移动影响)
        self.assertEqual(t.get("initial_r"), 1.0)
        # _initial_r_distance helper 用 initial_r 仍正确
        self.assertEqual(_initial_r_distance(t, 100.0), 1.0)

    def test_6c_a_legacy_breakeven_not_loosened(self):
        """Phase 6.C 部署迁移 (paranoid H1): 老 trade 已 _breakeven_shifted=True
        但没有 _profit_milestone 字段, 下一 tick 在 0.8~1.0R 区间内不应被 0.8R
        分支"loosen" SL 回 entry ± 0.2R.
        """
        from volume_velocity_scanner import _update_paper_trades
        from datetime import datetime, timezone, timedelta
        # 模拟 Phase 6.B-C 时代开的 SHORT trade:
        # entry=100, 原 SL=101 (1R=1), 已触发 BE → SL 现在 = 100 (entry),
        # _breakeven_shifted=True, 但没有 _profit_milestone 字段.
        # 当前价 99.2 = 浮盈 0.8R.
        legacy = self._make_trade(
            direction="SHORT", entry=100.0, sl=100.0, hwm=99.2,
        )
        legacy["_breakeven_shifted"] = True
        legacy["_breakeven_shifted_at"] = "2026-06-03T19:00:00+00:00"
        # 注意: 无 _profit_milestone 字段 (Phase 6.C 之前不存在这字段)
        # initial_r 也无 — 模拟最老的 trade. helper 会用 sl 回退 (但此时 sl=entry=100,
        # 距离 0 — 这是另一个 issue, 但 H1 fix 应在 helper 找不到 init_r 前就保护好).
        # 为了测试 H1, 我们要 init_r > 0, 所以 set initial_r=1.0 模拟新格式 trade
        # 但还是没 _profit_milestone (即 deploy 之前一 tick 开仓):
        legacy["initial_r"] = 1.0

        state = {"open_trades": [legacy], "closed_trades": []}
        _update_paper_trades(state, {"TESTUSDT": 99.2},
                              datetime.now(timezone.utc))
        t = state["open_trades"][0]
        # 关键断言: SL 必须仍 = entry (100), 不应被 0.8R 分支 loosen 回 100.2
        self.assertEqual(t["sl"], 100.0,
                          "老 _breakeven_shifted trade 在 0.8R 区间, SL 不应被 loosen")
        # milestone 应该被回填为 1.0 (但持久化是否回填不强求, 关键是行为正确)

    # === Phase 6.C-B: Funding direction-aware scoring ===
    # 注意: _compute_conviction 末尾有 score = max(score, 0) 防御性 clamp,
    # 所以 Phase 6.C-B 减分对终值影响通过 delta 验证 (baseline + funding 各打分对比),
    # 而不是直接验证负值. clamp 后 negative 仍会落入 "regular" tier, 区别在于
    # 当有其它正信号 (如 4h 对齐 +2) 时, 减分能拉低 final tier, 这正是目标.

    def _make_alert_for_funding(self, direction, funding_pct,
                                 baseline_aligned=False):
        """构造 funding test 用 alert.

        baseline_aligned: 是否额外加 1h/4h trend 对齐 → 让 score 有 +2 基础,
        能观察 funding 减分把 score 拉低 (不被 max(0) clamp 掩盖).
        """
        from volume_velocity_scanner import VelocityAlert
        a = VelocityAlert(
            symbol="X", base="X", direction=direction,
            # 用 "burst" 避免 sustained 加 +1, 让 baseline = 0 (或 +2 if aligned)
            alert_type="burst", price=100.0,
            price_change_pct=-2.0, metric_window_min=1,
            volume_1m_usdt=1000.0, volume_baseline_usdt=200.0, volume_ratio=5.0,
            detected_at="2026-06-03T20:00:00+00:00", intensity=2,
        )
        a.funding_rate_pct = funding_pct
        if baseline_aligned:
            # 1h+4h 同向 → +2 (对齐分), 不触发 4h<=-3 硬否决
            a.change_1h_pct = 1.0 if direction == "LONG" else -1.0
            a.change_4h_pct = 2.0 if direction == "LONG" else -2.0
        return a

    def _funding_delta(self, direction, funding_pct):
        """计算 funding 对 score 的"原始"贡献 = baseline_score - funding_score.
        用 baseline_aligned=True 保证 baseline 足够高 (+2), 让负值 funding 可观察.
        return: funding_pct 加进去后相对 baseline 的 delta (正=加分, 负=减分).
        """
        from volume_velocity_scanner import _compute_conviction
        a_base = self._make_alert_for_funding(direction, None,
                                                baseline_aligned=True)
        base_score, _ = _compute_conviction(a_base, None,
                                              regime=None, btc_regime=None)
        a_f = self._make_alert_for_funding(direction, funding_pct,
                                             baseline_aligned=True)
        f_score, _ = _compute_conviction(a_f, None,
                                          regime=None, btc_regime=None)
        return f_score - base_score

    def test_6c_b_long_chases_crowded_long_penalized(self):
        """LONG + funding > 0.3% (多头拥挤) → -2 delta vs baseline."""
        delta = self._funding_delta("LONG", 0.35)
        self.assertEqual(delta, -2, f"LONG 追拥挤多头应减 2 分, 实际 delta={delta}")

    def test_6c_b_short_fades_crowded_long_rewarded(self):
        """SHORT + funding > 0.3% (多头拥挤) → +2 delta (fade alpha)."""
        delta = self._funding_delta("SHORT", 0.35)
        self.assertEqual(delta, 2, f"SHORT fade 拥挤多头应 +2, 实际 delta={delta}")

    def test_6c_b_long_fades_crowded_short_rewarded(self):
        """LONG + funding < -0.3% (空头拥挤) → +2 delta."""
        delta = self._funding_delta("LONG", -0.35)
        self.assertEqual(delta, 2)

    def test_6c_b_short_chases_crowded_short_penalized(self):
        """SHORT + funding < -0.3% (空头拥挤) → -2 delta."""
        delta = self._funding_delta("SHORT", -0.35)
        self.assertEqual(delta, -2)

    def test_6c_b_moderate_funding_milder_signal(self):
        """中等 funding (0.05% ~ 0.3%) → ±1 (vs 极端 ±2)."""
        self.assertEqual(self._funding_delta("LONG", 0.1), -1)
        self.assertEqual(self._funding_delta("SHORT", 0.1), 1)
        self.assertEqual(self._funding_delta("LONG", -0.1), 1)
        self.assertEqual(self._funding_delta("SHORT", -0.1), -1)

    def test_6c_b_low_funding_no_effect(self):
        """微小 funding (|f| < 0.05%) → 0 delta (无影响)."""
        for d in ("LONG", "SHORT"):
            for f in (-0.04, -0.01, 0.0, 0.01, 0.04):
                delta = self._funding_delta(d, f)
                self.assertEqual(delta, 0,
                                  f"低 funding |{f}|<0.05 应无影响, "
                                  f"实际 {d}+f={f} → delta={delta}")

    def test_6c_b_none_funding_no_effect(self):
        """funding 字段缺失 → 不应崩溃, delta=0."""
        delta = self._funding_delta("LONG", None)
        self.assertEqual(delta, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
