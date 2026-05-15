"""Phase 3.1 单元测试 — live_trader.py 骨架.

覆盖:
- State load/save (atomic write, schema fallback)
- is_eligible_for_mirror filter chain
- _trade_age_sec parsing
- _generate_trade_id (符合 binance _validate_trade_id)
- main_loop 不崩溃 (空数据 + mock client)
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import live_trader  # noqa: E402
from live_trader import (  # noqa: E402
    is_eligible_for_mirror, _trade_age_sec, _generate_trade_id,
    _paper_to_live_side, _try_mirror_open, _try_mirror_close,
    _get_current_price, _check_sl_breach, _sync_live_with_paper,
    _check_emergency_stop_flag, _check_pause_flag,
    _check_cash_reserve, _calculate_daily_realized_pnl,
    _check_daily_dd, check_risk_gates,
    _get_account_balance, _check_cumulative_dd_and_trigger,
    check_position_reconciliation,
    _compute_live_stats, publish_live_history,
    load_paper_state, load_live_state, save_live_state,
    main_loop, _empty_live_state,
    _record_missed_signal, _prune_obsolete_missed,
    MISSED_SIGNALS_KEEP_LAST_N,
    LIVE_SYMBOL_WHITELIST, LIVE_MAX_CONCURRENT, LIVE_MIRROR_MAX_AGE_SEC,
    LIVE_NOTIONAL_USDT, LIVE_MAX_DEPLOY_USDT, LIVE_DAILY_DD_LIMIT_USDT,
    LIVE_STARTING_CAPITAL_USDT, LIVE_TOTAL_DD_LIMIT_PCT,
)
from binance_client import (  # noqa: E402
    _validate_trade_id, BinanceError, BinanceClient,
)

FAKE_KEY    = "FAKE_API_KEY_DO_NOT_USE_" + "x" * 40
FAKE_SECRET = "FAKE_API_SECRET_DO_NOT_USE_" + "y" * 37


# ============================================================================
# Trade ID generation (Phase 3.1: 符合 binance_client 约束)
# ============================================================================

class TestGenerateTradeId(unittest.TestCase):

    def test_normal_paper_id(self):
        paper_id = "BTCUSDT|LONG|2026-05-14T11:08:07.253118+00:00"
        tid = _generate_trade_id(paper_id)
        # 必须能通过 binance_client 的验证
        _validate_trade_id(tid)
        self.assertLessEqual(len(tid), 25)
        self.assertIn("BTCUSDT", tid)
        self.assertIn("L", tid)  # LONG → L

    def test_short_direction(self):
        paper_id = "ETHUSDT|SHORT|2026-05-14T11:08:07+00:00"
        tid = _generate_trade_id(paper_id)
        _validate_trade_id(tid)
        # 不要求精确格式, 只要符合规范
        self.assertLessEqual(len(tid), 25)

    def test_malformed_paper_id_falls_back(self):
        """异常 paper_id 应 fallback 而非崩溃."""
        tid = _generate_trade_id("garbage")
        _validate_trade_id(tid)

    def test_empty_paper_id(self):
        tid = _generate_trade_id("")
        _validate_trade_id(tid)


# ============================================================================
# Eligibility filter (核心 mirror 决策逻辑)
# ============================================================================

class TestEligibility(unittest.TestCase):

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        # 标准的 paper trade
        self.recent_trade = {
            "id": "BTCUSDT|LONG|2026-05-14T11:08:07+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (self.now - timedelta(seconds=30)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
        }
        self.empty_live = _empty_live_state()

    def test_eligible_basic(self):
        ok, reason = is_eligible_for_mirror(
            self.recent_trade, self.empty_live, self.now,
        )
        self.assertTrue(ok, f"expected eligible, got: {reason}")

    def test_already_mirrored(self):
        live = _empty_live_state()
        live["mirrored_paper_ids"] = [self.recent_trade["id"]]
        ok, reason = is_eligible_for_mirror(self.recent_trade, live, self.now)
        self.assertFalse(ok)
        self.assertIn("already", reason)

    def test_symbol_not_in_whitelist(self):
        trade = dict(self.recent_trade)
        trade["symbol"] = "DOGEUSDT"   # not in whitelist
        # 关闭 observation mode 才能测试白名单过滤
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", False):
            ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok)
        self.assertIn("whitelist", reason)

    def test_observation_mode_bypasses_whitelist(self):
        """Phase: LIVE_OBSERVATION_MODE=True 时跳过白名单 (但其他 filter 仍然生效)."""
        trade = dict(self.recent_trade)
        trade["symbol"] = "DOGEUSDT"   # not in whitelist
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertTrue(ok, f"observation mode 应跳过白名单, got: {reason}")

    def test_max_concurrent_reached(self):
        live = _empty_live_state()
        live["live_open_trades"] = [{} for _ in range(LIVE_MAX_CONCURRENT)]
        ok, reason = is_eligible_for_mirror(self.recent_trade, live, self.now)
        self.assertFalse(ok)
        self.assertIn("max_concurrent", reason)

    def test_too_old(self):
        trade = dict(self.recent_trade)
        old_time = self.now - timedelta(seconds=LIVE_MIRROR_MAX_AGE_SEC + 100)
        trade["entered_at"] = old_time.isoformat()
        ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok)
        self.assertIn("too old", reason)

    def test_missing_id(self):
        trade = dict(self.recent_trade)
        trade["id"] = ""
        ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok)
        self.assertIn("missing id", reason)

    def test_invalid_direction(self):
        trade = dict(self.recent_trade)
        trade["direction"] = "WEIRD"
        ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok)


# ============================================================================
# Side mapping
# ============================================================================

class TestSideMapping(unittest.TestCase):

    def test_long_to_buy(self):
        self.assertEqual(_paper_to_live_side("LONG"), "BUY")
        self.assertEqual(_paper_to_live_side("long"), "BUY")

    def test_short_to_sell(self):
        self.assertEqual(_paper_to_live_side("SHORT"), "SELL")
        self.assertEqual(_paper_to_live_side("short"), "SELL")


# ============================================================================
# Age parsing
# ============================================================================

class TestTradeAge(unittest.TestCase):

    def test_recent_trade(self):
        now = datetime.now(timezone.utc)
        trade = {"entered_at": (now - timedelta(seconds=60)).isoformat()}
        age = _trade_age_sec(trade, now)
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 60, delta=1)

    def test_missing_field(self):
        self.assertIsNone(_trade_age_sec({}, datetime.now(timezone.utc)))

    def test_malformed(self):
        self.assertIsNone(
            _trade_age_sec({"entered_at": "garbage"}, datetime.now(timezone.utc))
        )


# ============================================================================
# State I/O (with tempdir)
# ============================================================================

class TestStateIO(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Redirect state path to temp
        self._orig_live_state = live_trader.LIVE_STATE
        self._orig_paper_history = live_trader.PAPER_HISTORY
        live_trader.LIVE_STATE = Path(self.tmpdir) / "live_state.json"
        live_trader.PAPER_HISTORY = Path(self.tmpdir) / "paper_history.json"

    def tearDown(self):
        live_trader.LIVE_STATE = self._orig_live_state
        live_trader.PAPER_HISTORY = self._orig_paper_history
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_nonexistent_returns_empty(self):
        state = load_live_state()
        self.assertEqual(state["live_open_trades"], [])
        self.assertEqual(state["mirrored_paper_ids"], [])

    def test_save_and_reload(self):
        state = _empty_live_state()
        state["mirrored_paper_ids"] = ["test_id_1", "test_id_2"]
        save_live_state(state)
        loaded = load_live_state()
        self.assertEqual(loaded["mirrored_paper_ids"], ["test_id_1", "test_id_2"])
        self.assertIsNotNone(loaded["last_update"])

    def test_save_atomic(self):
        """save_live_state 应用 .tmp + rename. .tmp 不应残留."""
        state = _empty_live_state()
        save_live_state(state)
        self.assertTrue(live_trader.LIVE_STATE.exists())
        tmp_path = live_trader.LIVE_STATE.with_suffix(".tmp")
        self.assertFalse(tmp_path.exists())

    def test_mirrored_ids_rolling_window(self):
        """超过 MIRRORED_IDS_KEEP_LAST_N 自动滚动."""
        state = _empty_live_state()
        # 创建 600 个 ids
        state["mirrored_paper_ids"] = [f"id_{i}" for i in range(600)]
        save_live_state(state)
        loaded = load_live_state()
        # 应该保留 500 (MIRRORED_IDS_KEEP_LAST_N)
        self.assertEqual(len(loaded["mirrored_paper_ids"]),
                         live_trader.MIRRORED_IDS_KEEP_LAST_N)
        # 保留的应该是最后的 500 (id_100 到 id_599)
        self.assertEqual(loaded["mirrored_paper_ids"][0], "id_100")
        self.assertEqual(loaded["mirrored_paper_ids"][-1], "id_599")

    def test_load_paper_state_empty_if_missing(self):
        state = load_paper_state()
        self.assertEqual(state["open_trades"], [])
        self.assertEqual(state["recent_closed"], [])

    def test_load_corrupt_state_recovers(self):
        live_trader.LIVE_STATE.parent.mkdir(parents=True, exist_ok=True)
        live_trader.LIVE_STATE.write_text("not valid json")
        state = load_live_state()
        # 应该 fallback 到空 state, 不崩
        self.assertEqual(state["live_open_trades"], [])


# ============================================================================
# Main loop integration (mock 客户端, 不联网)
# ============================================================================

class TestMainLoop(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_live_state = live_trader.LIVE_STATE
        self._orig_paper_history = live_trader.PAPER_HISTORY
        live_trader.LIVE_STATE = Path(self.tmpdir) / "live.json"
        live_trader.PAPER_HISTORY = Path(self.tmpdir) / "paper.json"

    def tearDown(self):
        live_trader.LIVE_STATE = self._orig_live_state
        live_trader.PAPER_HISTORY = self._orig_paper_history
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_paper(self, open_trades):
        live_trader.PAPER_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        live_trader.PAPER_HISTORY.write_text(json.dumps({
            "open_trades": open_trades,
            "recent_closed": [],
        }))

    def test_main_loop_no_paper_doesnt_crash(self):
        """没有 paper state 时 main_loop 应优雅处理."""
        from binance_client import BinanceClient
        client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        result = main_loop(client, dry_run=True)
        self.assertEqual(result["live_open_trades"], [])

    def test_main_loop_with_recent_paper(self):
        from binance_client import BinanceClient
        client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|2026-05-14T11:08:07+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
        }])
        result = main_loop(client, dry_run=True)
        # Phase 3.1 阶段不实际 mirror, 只输出日志
        # 但 state 应该被保存
        self.assertIsNotNone(result["last_update"])

    def test_main_loop_filters_non_whitelisted_symbol(self):
        """非白名单 symbol 不会被认为 eligible (需关 observation mode)."""
        from binance_client import BinanceClient
        # 关掉 observation mode 才能测试白名单过滤
        self._obs_patch = patch.object(live_trader, "LIVE_OBSERVATION_MODE", False)
        self._obs_patch.start()
        self.addCleanup(self._obs_patch.stop)
        client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "DOGEUSDT|LONG|2026-05-14T11:08:07+00:00",
            "symbol": "DOGEUSDT",   # 不在白名单
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
        }])
        # 不应抛错, 仅 skip
        result = main_loop(client, dry_run=True)
        self.assertEqual(len(result["mirrored_paper_ids"]), 0)


# ============================================================================
# Phase 3.2.a: _try_mirror_open (真实 mirror 调用)
# ============================================================================

class TestTryMirrorOpen(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        self.paper_trade = {
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_price": 81000.0,
            "sl": 80190.0,
            "tp1": 82215.0,
            "tp2": 83430.0,
            "atr_pct": 1.0,
            "conviction_score": 5,
            "alert_type": "sustained",
        }
        # Mock open_position result
        self.mock_open_result = {
            "trade_id": "L1_BTCUSDT_L",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": 0.0002,
            "avg_fill_price": 81050.0,   # 50 上滑 (LONG 不利)
            "requested_notional": 20.0,
            "actual_notional": 16.21,
            "entry_order_id": 12345,
            "entry_client_id": "cresus_L1_BTCUSDT_L_E",
            "sl_price": 80190.0,
            "sl_side": "SELL",
            "sl_order_id": None,
            "sl_client_id": None,
            "sl_mode": "client_side",
            "opened_at": "2026-05-15T10:00:30+00:00",
            "fees_paid_usdt": 0.0065,
            "_dryRun": True,
        }

    def test_mirror_returns_live_trade(self):
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            result = _try_mirror_open(
                self.client, self.paper_trade, dry_run=True,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["direction"], "LONG")
        self.assertEqual(result["paper_id"], self.paper_trade["id"])
        self.assertEqual(result["phase"], "A")
        # TP1/TP2 应该被拷贝
        self.assertEqual(result["tp1_price"], 82215.0)
        self.assertEqual(result["tp2_price"], 83430.0)

    def test_mirror_slippage_long_positive(self):
        """LONG: 实际 fill 高于预期 → 正 bps (不利)."""
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            result = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        # (81050 - 81000) / 81000 * 10000 = +6.17 bps
        self.assertGreater(result["slippage_bps"], 0)
        self.assertAlmostEqual(result["slippage_bps"], 6.17, places=1)

    def test_mirror_slippage_short_inverse(self):
        """SHORT: 实际 fill 高于预期 → 有利, 应为负 bps."""
        short_paper = dict(self.paper_trade)
        short_paper["direction"] = "SHORT"
        short_paper["sl"] = 81810.0   # SHORT SL 要高于 entry
        short_result = dict(self.mock_open_result)
        short_result["side"] = "SELL"
        short_result["avg_fill_price"] = 81050.0  # 高于 81000 = SHORT 有利
        with patch.object(self.client, "open_position",
                          return_value=short_result):
            result = _try_mirror_open(self.client, short_paper, dry_run=True)
        # SHORT 高价 fill 是有利 → 负 slippage_bps
        self.assertLess(result["slippage_bps"], 0)

    def test_mirror_handles_binance_error(self):
        """API 错误应被 catch, 不抛."""
        with patch.object(self.client, "open_position",
                          side_effect=BinanceError("simulated error")):
            result = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertIsNone(result)

    def test_mirror_handles_value_error(self):
        """ValueError (e.g. bad SL) 应被 catch."""
        with patch.object(self.client, "open_position",
                          side_effect=ValueError("bad sl")):
            result = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertIsNone(result)

    def test_mirror_handles_unexpected_exception(self):
        """任何意外异常 (RuntimeError 等) 也不让 loop 崩."""
        with patch.object(self.client, "open_position",
                          side_effect=RuntimeError("unexpected")):
            result = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertIsNone(result)

    def test_set_leverage_called_before_open(self):
        """每次 mirror_open 前必须调 set_leverage(LIVE_LEVERAGE), 防止 Binance 默认 20x."""
        order = []
        def lev_call(symbol, lev, **kw):
            order.append(("lev", symbol, lev))
            return {"_dryRun": True, "leverage": lev, "symbol": symbol}
        def open_call(**kw):
            order.append(("open", kw["symbol"]))
            return self.mock_open_result
        with patch.object(self.client, "set_leverage", side_effect=lev_call), \
             patch.object(self.client, "open_position", side_effect=open_call):
            r = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertIsNotNone(r)
        # set_leverage 必须先于 open_position
        self.assertEqual(order[0][0], "lev")
        self.assertEqual(order[0][1], "BTCUSDT")
        self.assertEqual(order[0][2], live_trader.LIVE_LEVERAGE)
        self.assertEqual(order[1][0], "open")
        # live_trade 中应记录 leverage
        self.assertEqual(r["leverage"], live_trader.LIVE_LEVERAGE)

    def test_set_leverage_failure_aborts_mirror(self):
        """set_leverage 失败 → 不开仓 (避免误用错杠杆), 返回 None."""
        with patch.object(self.client, "set_leverage",
                          side_effect=BinanceError("leverage not allowed")):
            with patch.object(self.client, "open_position") as op_mock:
                r = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertIsNone(r)
        # open_position 一定没被调
        op_mock.assert_not_called()

    def test_risk_amount_recorded(self):
        """live_trade 应记录 risk_usdt / risk_pct = (entry - SL) / entry × notional."""
        # paper sl = 80190, entry fill = 81050, notional = 16.21
        # risk_pct = |81050 - 80190| / 81050 * 100 = 1.0611%
        # risk_usdt = 1.0611% * 16.21 = 0.172
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            r = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertIsNotNone(r["risk_pct"])
        self.assertAlmostEqual(r["risk_pct"], 1.061, places=2)
        self.assertAlmostEqual(r["risk_usdt"], 0.172, places=2)

    def test_mirror_latency_recorded(self):
        """信号→镜像延迟 = live.opened_at − paper.entered_at."""
        paper_t = dict(self.paper_trade)
        paper_t["entered_at"] = "2026-05-15T10:00:00+00:00"
        # mock_open_result opened_at = 2026-05-15T10:00:30+00:00 → 30s
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            r = _try_mirror_open(self.client, paper_t, dry_run=True)
        self.assertAlmostEqual(r["mirror_latency_sec"], 30.0, places=0)

    def test_mirror_missing_sl_field(self):
        """Paper 缺关键字段 → 返回 None."""
        bad_paper = dict(self.paper_trade)
        del bad_paper["sl"]
        result = _try_mirror_open(self.client, bad_paper, dry_run=True)
        self.assertIsNone(result)

    def test_mirror_returns_is_dry_run_flag(self):
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            result = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertTrue(result["is_dry_run"])


# ============================================================================
# Phase 3.2.a: main_loop integration with real mirroring
# ============================================================================

class TestMainLoopMirroring(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_live_state = live_trader.LIVE_STATE
        self._orig_paper_history = live_trader.PAPER_HISTORY
        live_trader.LIVE_STATE = Path(self.tmpdir) / "live.json"
        live_trader.PAPER_HISTORY = Path(self.tmpdir) / "paper.json"

        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        # Mock fill response
        self.mock_result = {
            "trade_id": "L1_BTCUSDT_L",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": 0.0002,
            "avg_fill_price": 81050.0,
            "actual_notional": 16.21,
            "entry_order_id": 12345,
            "entry_client_id": "cresus_L1_BTCUSDT_L_E",
            "sl_price": 80190.0,
            "sl_side": "SELL",
            "sl_mode": "client_side",
            "opened_at": "2026-05-15T10:00:30+00:00",
            "fees_paid_usdt": 0.0065,
            "_dryRun": True,
        }

    def tearDown(self):
        live_trader.LIVE_STATE = self._orig_live_state
        live_trader.PAPER_HISTORY = self._orig_paper_history
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_paper(self, open_trades):
        live_trader.PAPER_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        live_trader.PAPER_HISTORY.write_text(json.dumps({
            "open_trades": open_trades, "recent_closed": [],
        }))

    def test_main_loop_mirrors_eligible_trade(self):
        """白名单内 + 新鲜 paper trade → 调 open_position + 加入 live_open."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
            "tp1": 82215.0,
            "tp2": 83430.0,
        }])
        with patch.object(self.client, "open_position",
                          return_value=self.mock_result) as mock_open:
            result = main_loop(self.client, dry_run=True)
        # open_position 应被调用 1 次
        self.assertEqual(mock_open.call_count, 1)
        # live_open_trades 增加 1 条
        self.assertEqual(len(result["live_open_trades"]), 1)
        # mirrored_paper_ids 应包含该 paper_id
        self.assertIn(
            "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            result["mirrored_paper_ids"],
        )

    def test_main_loop_failed_mirror_blocks_retry(self):
        """mirror 失败的 paper_id 也加入 mirrored_paper_ids (防重复砍腰)."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
        }])
        with patch.object(self.client, "open_position",
                          side_effect=BinanceError("simulated")):
            result = main_loop(self.client, dry_run=True)
        # live_open_trades 没增加
        self.assertEqual(len(result["live_open_trades"]), 0)
        # 但 paper_id 已记入 mirrored_paper_ids (下次不重试)
        self.assertIn(
            "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            result["mirrored_paper_ids"],
        )

    def test_main_loop_skips_already_mirrored(self):
        """已 mirror 过的 paper_id 不会被再次调用."""
        now = datetime.now(timezone.utc)
        paper_id = "BTCUSDT|LONG|2026-05-15T10:00:00+00:00"
        self._write_paper([{
            "id": paper_id,
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
        }])
        # 先 seed: 已 mirror 过
        state = _empty_live_state()
        state["mirrored_paper_ids"] = [paper_id]
        save_live_state(state)
        with patch.object(self.client, "open_position",
                          return_value=self.mock_result) as mock_open:
            result = main_loop(self.client, dry_run=True)
        # open_position 不应被调用 (已 mirrored skip)
        self.assertEqual(mock_open.call_count, 0)


# ============================================================================
# Phase 3.2.b: SL breach detection / paper-close mirror
# ============================================================================

class TestCheckSLBreach(unittest.TestCase):

    def test_long_above_sl_no_breach(self):
        lt = {"side": "BUY", "sl_price": 80000.0}
        self.assertFalse(_check_sl_breach(lt, 81000.0))

    def test_long_at_sl_breach(self):
        lt = {"side": "BUY", "sl_price": 80000.0}
        self.assertTrue(_check_sl_breach(lt, 80000.0))  # equal counts as breach

    def test_long_below_sl_breach(self):
        lt = {"side": "BUY", "sl_price": 80000.0}
        self.assertTrue(_check_sl_breach(lt, 79500.0))

    def test_short_below_sl_no_breach(self):
        lt = {"side": "SELL", "sl_price": 82000.0}
        self.assertFalse(_check_sl_breach(lt, 81000.0))

    def test_short_above_sl_breach(self):
        lt = {"side": "SELL", "sl_price": 82000.0}
        self.assertTrue(_check_sl_breach(lt, 82500.0))

    def test_no_sl_no_breach(self):
        lt = {"side": "BUY", "sl_price": None}
        self.assertFalse(_check_sl_breach(lt, 1.0))

    def test_invalid_side(self):
        lt = {"side": "HOLD", "sl_price": 100.0}
        self.assertFalse(_check_sl_breach(lt, 50.0))


class TestSyncLiveWithPaper(unittest.TestCase):

    def test_sl_changed(self):
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A"}
        paper = {"sl": 81000.0, "phase": "B"}
        updated = _sync_live_with_paper(lt, paper)
        self.assertTrue(updated)
        self.assertEqual(lt["sl_price"], 81000.0)
        self.assertEqual(lt["phase"], "B")

    def test_no_change(self):
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A"}
        paper = {"sl": 80000.0, "phase": "A"}
        updated = _sync_live_with_paper(lt, paper)
        self.assertFalse(updated)

    def test_only_sl_change(self):
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A"}
        paper = {"sl": 80500.0, "phase": "A"}
        updated = _sync_live_with_paper(lt, paper)
        self.assertTrue(updated)
        self.assertEqual(lt["sl_price"], 80500.0)
        self.assertEqual(lt["phase"], "A")

    def test_mfe_copied_from_paper(self):
        """Per-phase MFE 从 paper 拷贝 (paper 已在监控 high water mark)."""
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A",
              "phase_a_mfe_pct": None, "phase_b_mfe_pct": None,
              "phase_c_mfe_pct": None}
        paper = {"sl": 80000.0, "phase": "B",
                 "phase_a_mfe_pct": 1.85, "phase_b_mfe_pct": 0.32,
                 "phase_a_mfe_price": 82500.0}
        updated = _sync_live_with_paper(lt, paper)
        self.assertTrue(updated)
        self.assertEqual(lt["phase_a_mfe_pct"], 1.85)
        self.assertEqual(lt["phase_b_mfe_pct"], 0.32)
        self.assertEqual(lt["phase_a_mfe_price"], 82500.0)
        # phase_c 未在 paper 提供 → 保持 None
        self.assertIsNone(lt["phase_c_mfe_pct"])

    def test_mfe_unchanged_no_update(self):
        """MFE 值未变 + sl/phase 未变 → updated=False."""
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A",
              "phase_a_mfe_pct": 1.85}
        paper = {"sl": 80000.0, "phase": "A", "phase_a_mfe_pct": 1.85}
        self.assertFalse(_sync_live_with_paper(lt, paper))
        self.assertEqual(lt["sl_price"], 80000.0)
        self.assertEqual(lt["phase"], "A")

    def test_only_phase_change(self):
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A"}
        paper = {"sl": 80000.0, "phase": "B"}
        updated = _sync_live_with_paper(lt, paper)
        self.assertTrue(updated)
        self.assertEqual(lt["phase"], "B")

    def test_paper_missing_sl(self):
        """Paper 缺 sl 字段不应崩."""
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A"}
        paper = {"phase": "A"}   # 没 sl
        # 不应崩, 也不变 lt
        _sync_live_with_paper(lt, paper)
        self.assertEqual(lt["sl_price"], 80000.0)


class TestGetCurrentPrice(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def test_normal(self):
        with patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "81000.5", 0, 0, 0, 0, 0, 0, 0]]):
            price = _get_current_price(self.client, "BTCUSDT")
        self.assertEqual(price, 81000.5)

    def test_api_error_returns_none(self):
        with patch.object(self.client, "get_klines",
                          side_effect=BinanceError("network")):
            price = _get_current_price(self.client, "BTCUSDT")
        self.assertIsNone(price)

    def test_empty_klines_returns_none(self):
        with patch.object(self.client, "get_klines", return_value=[]):
            price = _get_current_price(self.client, "BTCUSDT")
        self.assertIsNone(price)


class TestTryMirrorClose(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        self.live_trade = {
            "trade_id": "L1_BTC_L",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "paper_id": "BTCUSDT|LONG|...",
            "sl_price": 80000.0,
            "phase": "A",
        }
        self.mock_close = {
            "closed_at": "2026-05-15T10:30:00+00:00",
            "avg_exit_price": 81500.0,
            "realized_pnl_usdt": 0.5,
            "close_order_id": 99999,
            "qty_closed": 0.0002,
        }

    def test_close_success(self):
        with patch.object(self.client, "close_position",
                          return_value=self.mock_close):
            result = _try_mirror_close(
                self.client, self.live_trade,
                reason="sl_breach_client", dry_run=True,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["close_reason"], "sl_breach_client")
        self.assertEqual(result["avg_exit_price"], 81500.0)
        self.assertEqual(result["realized_pnl_usdt"], 0.5)
        # 原字段保留
        self.assertEqual(result["trade_id"], "L1_BTC_L")
        self.assertEqual(result["symbol"], "BTCUSDT")

    def test_close_binance_error(self):
        with patch.object(self.client, "close_position",
                          side_effect=BinanceError("oops")):
            result = _try_mirror_close(
                self.client, self.live_trade,
                reason="test", dry_run=True,
            )
        self.assertIsNone(result)

    def test_close_unexpected_error(self):
        with patch.object(self.client, "close_position",
                          side_effect=RuntimeError("unexpected")):
            result = _try_mirror_close(
                self.client, self.live_trade,
                reason="test", dry_run=True,
            )
        self.assertIsNone(result)

    def test_fee_aggregation_entry_plus_close(self):
        """已平仓 trade 的 fees_paid_usdt = 开仓侧 + 平仓侧 (含真实 + 估算)."""
        # 模拟开仓时已记录的实际手续费
        live_trade_with_fee = dict(self.live_trade)
        live_trade_with_fee["fees_paid_usdt"] = 0.008   # 开仓 actual
        live_trade_with_fee["fees_are_actual"] = True
        close_with_fee = dict(self.mock_close)
        close_with_fee["fees_paid_usdt"] = 0.0075       # 平仓 actual
        close_with_fee["fees_are_actual"] = True

        with patch.object(self.client, "close_position",
                          return_value=close_with_fee):
            result = _try_mirror_close(
                self.client, live_trade_with_fee,
                reason="sl_breach_client", dry_run=True,
            )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["entry_fees_usdt"], 0.008, places=6)
        self.assertAlmostEqual(result["close_fees_usdt"], 0.0075, places=6)
        self.assertAlmostEqual(result["fees_paid_usdt"], 0.0155, places=6)
        self.assertTrue(result["fees_are_actual"])

    def test_close_slippage_on_sl_breach(self):
        """SL 触发: 期望价 = sl_price, 实际成交 vs 期望 → close_slippage_bps."""
        lt = dict(self.live_trade)
        lt["sl_price"] = 80000.0
        # LONG (BUY) 平出 SELL: 实际 79900 比期望 80000 低 → 不利 (正 bps)
        close = dict(self.mock_close)
        close["avg_exit_price"] = 79900.0
        with patch.object(self.client, "close_position", return_value=close):
            r = _try_mirror_close(
                self.client, lt, reason="sl_breach_client", dry_run=True,
            )
        self.assertIsNotNone(r["close_slippage_bps"])
        # (79900 - 80000) / 80000 * 10000 = -12.5 → LONG 平仓低于期望 → 正 (不利)
        self.assertAlmostEqual(r["close_slippage_bps"], 12.5, places=1)

    def test_close_slippage_short_inverse(self):
        """SHORT 平出 BUY: 实际高于期望 = 不利 (正 bps)."""
        lt = dict(self.live_trade)
        lt["side"] = "SELL"
        lt["sl_price"] = 80000.0
        close = dict(self.mock_close)
        close["avg_exit_price"] = 80100.0   # SHORT 平仓 100 高 = 不利
        with patch.object(self.client, "close_position", return_value=close):
            r = _try_mirror_close(
                self.client, lt, reason="sl_breach_client", dry_run=True,
            )
        self.assertGreater(r["close_slippage_bps"], 0)
        self.assertAlmostEqual(r["close_slippage_bps"], 12.5, places=1)

    def test_close_slippage_paper_close_uses_current_price(self):
        """paper:hit_tp2 等情况: 期望价 = live_trade.current_price."""
        lt = dict(self.live_trade)
        lt["current_price"] = 81500.0
        # close 在 81600 = LONG 平仓高于期望 = 有利 (负 bps)
        close = dict(self.mock_close)
        close["avg_exit_price"] = 81600.0
        with patch.object(self.client, "close_position", return_value=close):
            r = _try_mirror_close(
                self.client, lt, reason="paper:hit_tp2", dry_run=True,
            )
        self.assertLess(r["close_slippage_bps"], 0)

    def test_close_slippage_none_when_no_reference(self):
        """无 current_price + 非 SL 触发 → close_slippage_bps = None."""
        lt = dict(self.live_trade)
        lt.pop("current_price", None)
        with patch.object(self.client, "close_position",
                          return_value=self.mock_close):
            r = _try_mirror_close(
                self.client, lt, reason="paper:timeout", dry_run=True,
            )
        self.assertIsNone(r["close_slippage_bps"])

    def test_fees_are_actual_false_if_either_side_estimated(self):
        """开仓 actual 但平仓回退到估算 → fees_are_actual = False (混合)."""
        live_trade_with_fee = dict(self.live_trade)
        live_trade_with_fee["fees_paid_usdt"] = 0.008
        live_trade_with_fee["fees_are_actual"] = True
        close_with_fee = dict(self.mock_close)
        close_with_fee["fees_paid_usdt"] = 0.0075
        close_with_fee["fees_are_actual"] = False       # 平仓回退估算

        with patch.object(self.client, "close_position",
                          return_value=close_with_fee):
            result = _try_mirror_close(
                self.client, live_trade_with_fee,
                reason="hit_sl", dry_run=True,
            )
        self.assertFalse(result["fees_are_actual"])
        # 总额仍正确累加
        self.assertAlmostEqual(result["fees_paid_usdt"], 0.0155, places=6)


class TestMainLoopPhase32b(unittest.TestCase):
    """Phase 3.2.b: 三种 close 触发 + sync from paper."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_live_state = live_trader.LIVE_STATE
        self._orig_paper_history = live_trader.PAPER_HISTORY
        live_trader.LIVE_STATE = Path(self.tmpdir) / "live.json"
        live_trader.PAPER_HISTORY = Path(self.tmpdir) / "paper.json"
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def tearDown(self):
        live_trader.LIVE_STATE = self._orig_live_state
        live_trader.PAPER_HISTORY = self._orig_paper_history
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed(self, paper_open, paper_closed, live_open):
        """Seed paper history + live state."""
        live_trader.PAPER_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        live_trader.PAPER_HISTORY.write_text(json.dumps({
            "open_trades": paper_open,
            "recent_closed": paper_closed,
        }))
        state = _empty_live_state()
        state["live_open_trades"] = live_open
        save_live_state(state)

    def test_paper_closed_triggers_mirror_close(self):
        """A. Paper 把 trade 关了 → live 也关."""
        paper_id = "BTCUSDT|LONG|2026-05-15T10:00:00+00:00"
        live_open = [{
            "trade_id": "L1_BTC_L",
            "paper_id": paper_id,
            "symbol": "BTCUSDT", "side": "BUY", "direction": "LONG",
            "sl_price": 80000.0, "phase": "A",
        }]
        paper_closed = [{
            "id": paper_id, "symbol": "BTCUSDT", "direction": "LONG",
            "close_reason": "hit_trail",
        }]
        self._seed([], paper_closed, live_open)
        mock_close = {
            "closed_at": "...", "avg_exit_price": 82000.0,
            "realized_pnl_usdt": 0.6, "close_order_id": 1, "qty_closed": 0.0002,
        }
        with patch.object(self.client, "close_position",
                          return_value=mock_close) as mock_call:
            result = main_loop(self.client, dry_run=True)
        # close_position 应被调用
        self.assertEqual(mock_call.call_count, 1)
        # live_open 清空, live_closed 增加
        self.assertEqual(len(result["live_open_trades"]), 0)
        self.assertEqual(len(result["live_closed_trades"]), 1)
        # close_reason 包含 paper:hit_trail
        self.assertIn("paper:hit_trail",
                      result["live_closed_trades"][0]["close_reason"])

    def test_sl_breach_triggers_close(self):
        """C. Paper 还开着, 但当前价触 SL → client-side close."""
        paper_id = "BTCUSDT|LONG|2026-05-15T10:00:00+00:00"
        live_open = [{
            "trade_id": "L1_BTC_L",
            "paper_id": paper_id,
            "symbol": "BTCUSDT", "side": "BUY", "direction": "LONG",
            "sl_price": 80000.0, "phase": "A",
        }]
        paper_open = [{
            "id": paper_id, "symbol": "BTCUSDT", "direction": "LONG",
            "sl": 80000.0, "phase": "A",
        }]
        self._seed(paper_open, [], live_open)
        mock_close = {
            "closed_at": "...", "avg_exit_price": 79900.0,
            "realized_pnl_usdt": -0.22, "close_order_id": 2, "qty_closed": 0.0002,
        }
        # Current price = 79900 < sl 80000 → breach
        with patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "79900.0", 0, 0, 0, 0, 0, 0, 0]]):
            with patch.object(self.client, "close_position",
                              return_value=mock_close) as mock_call:
                result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_call.call_count, 1)
        self.assertEqual(len(result["live_open_trades"]), 0)
        self.assertEqual(len(result["live_closed_trades"]), 1)
        self.assertEqual(result["live_closed_trades"][0]["close_reason"],
                         "sl_breach_client")

    def test_sl_sync_from_paper(self):
        """B. Paper 还开着但 sl 更新 → live sync 但不关."""
        paper_id = "BTCUSDT|LONG|2026-05-15T10:00:00+00:00"
        live_open = [{
            "trade_id": "L1_BTC_L",
            "paper_id": paper_id,
            "symbol": "BTCUSDT", "side": "BUY", "direction": "LONG",
            "sl_price": 80000.0, "phase": "A",
        }]
        paper_open = [{
            "id": paper_id, "symbol": "BTCUSDT", "direction": "LONG",
            "sl": 81000.0, "phase": "B",   # paper 把 sl 移到 BE+
        }]
        self._seed(paper_open, [], live_open)
        # 当前价 81500, > 新 sl 81000, 不触发
        with patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "81500.0", 0, 0, 0, 0, 0, 0, 0]]):
            with patch.object(self.client, "close_position") as mock_close:
                result = main_loop(self.client, dry_run=True)
        # close 不应被调
        self.assertEqual(mock_close.call_count, 0)
        # live_open 仍在, sl/phase 已 sync
        self.assertEqual(len(result["live_open_trades"]), 1)
        self.assertEqual(result["live_open_trades"][0]["sl_price"], 81000.0)
        self.assertEqual(result["live_open_trades"][0]["phase"], "B")

    def test_close_failure_keeps_open_for_retry(self):
        """Close 失败 → trade 留在 live_open 下 tick 重试."""
        paper_id = "BTCUSDT|LONG|2026-05-15T10:00:00+00:00"
        live_open = [{
            "trade_id": "L1_BTC_L",
            "paper_id": paper_id,
            "symbol": "BTCUSDT", "side": "BUY", "direction": "LONG",
            "sl_price": 80000.0, "phase": "A",
        }]
        # paper 关了, 但 close 调用失败
        paper_closed = [{
            "id": paper_id, "close_reason": "timeout",
        }]
        self._seed([], paper_closed, live_open)
        with patch.object(self.client, "close_position",
                          side_effect=BinanceError("temp fail")):
            result = main_loop(self.client, dry_run=True)
        # 仍 open 等下 tick 重试
        self.assertEqual(len(result["live_open_trades"]), 1)
        self.assertEqual(len(result["live_closed_trades"]), 0)


# ============================================================================
# Phase 3.3.a 风控软门测试
# ============================================================================

class TestFileFlags(unittest.TestCase):
    """测试 pause / emergency stop flag 文件的检测."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_pause = live_trader.PAUSE_FLAG_PATH
        self._orig_stop = live_trader.EMERGENCY_STOP_PATH
        live_trader.PAUSE_FLAG_PATH = Path(self.tmpdir) / ".cresus-pause"
        live_trader.EMERGENCY_STOP_PATH = Path(self.tmpdir) / ".cresus-emergency-stop"

    def tearDown(self):
        live_trader.PAUSE_FLAG_PATH = self._orig_pause
        live_trader.EMERGENCY_STOP_PATH = self._orig_stop
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pause_flag_absent_returns_none(self):
        self.assertIsNone(_check_pause_flag())

    def test_pause_flag_empty(self):
        live_trader.PAUSE_FLAG_PATH.write_text("")
        result = _check_pause_flag()
        self.assertIsNotNone(result)
        self.assertIn("manual pause", result)

    def test_pause_flag_with_message(self):
        live_trader.PAUSE_FLAG_PATH.write_text("睡觉了, 别交易")
        result = _check_pause_flag()
        self.assertIsNotNone(result)
        self.assertIn("睡觉了", result)

    def test_emergency_stop_absent(self):
        self.assertIsNone(_check_emergency_stop_flag())

    def test_emergency_stop_with_reason(self):
        live_trader.EMERGENCY_STOP_PATH.write_text("auto: cumulative DD 5.2%")
        result = _check_emergency_stop_flag()
        self.assertIsNotNone(result)
        self.assertIn("cumulative DD", result)


class TestCashReserve(unittest.TestCase):

    def test_empty_state_no_block(self):
        state = _empty_live_state()
        self.assertIsNone(_check_cash_reserve(state))

    def test_under_cap(self):
        state = _empty_live_state()
        state["live_open_trades"] = [
            {"notional_usdt": 20.0},
            {"notional_usdt": 20.0},
        ]
        self.assertIsNone(_check_cash_reserve(state))  # $40 < $60 cap

    def test_at_cap_blocks(self):
        state = _empty_live_state()
        state["live_open_trades"] = [
            {"notional_usdt": 20.0},
            {"notional_usdt": 20.0},
            {"notional_usdt": 20.0},
        ]
        result = _check_cash_reserve(state)
        self.assertIsNotNone(result)
        self.assertIn("cap", result)

    def test_over_cap_blocks(self):
        state = _empty_live_state()
        state["live_open_trades"] = [
            {"notional_usdt": 25.0},
            {"notional_usdt": 25.0},
            {"notional_usdt": 25.0},
        ]
        result = _check_cash_reserve(state)
        self.assertIsNotNone(result)


class TestDailyPnL(unittest.TestCase):

    def setUp(self):
        # Fix "now" 在 UTC 12:00 当天某天
        self.now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.day_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)

    def test_no_trades_zero_pnl(self):
        state = _empty_live_state()
        pnl, count, day_start = _calculate_daily_realized_pnl(state, self.now)
        self.assertEqual(pnl, 0.0)
        self.assertEqual(count, 0)

    def test_today_trade_counted(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [{
            "closed_at": (self.now - timedelta(hours=2)).isoformat(),
            "realized_pnl_usdt": -1.5,
        }, {
            "closed_at": (self.now - timedelta(hours=1)).isoformat(),
            "realized_pnl_usdt": +0.5,
        }]
        pnl, count, _ = _calculate_daily_realized_pnl(state, self.now)
        self.assertAlmostEqual(pnl, -1.0, places=2)
        self.assertEqual(count, 2)

    def test_yesterday_not_counted(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [{
            "closed_at": (self.day_start - timedelta(hours=2)).isoformat(),
            "realized_pnl_usdt": -10.0,   # 昨天的大亏不算
        }]
        pnl, count, _ = _calculate_daily_realized_pnl(state, self.now)
        self.assertEqual(pnl, 0.0)
        self.assertEqual(count, 0)

    def test_mixed_days(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [
            {"closed_at": (self.day_start - timedelta(hours=5)).isoformat(),
             "realized_pnl_usdt": -100.0},   # 昨天
            {"closed_at": (self.now - timedelta(hours=1)).isoformat(),
             "realized_pnl_usdt": -2.0},     # 今天
            {"closed_at": (self.now - timedelta(minutes=30)).isoformat(),
             "realized_pnl_usdt": +1.0},     # 今天
        ]
        pnl, count, _ = _calculate_daily_realized_pnl(state, self.now)
        self.assertAlmostEqual(pnl, -1.0, places=2)
        self.assertEqual(count, 2)

    def test_malformed_timestamp_skipped(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [
            {"closed_at": "garbage", "realized_pnl_usdt": -10.0},
            {"closed_at": self.now.isoformat(), "realized_pnl_usdt": -1.0},
        ]
        pnl, count, _ = _calculate_daily_realized_pnl(state, self.now)
        self.assertAlmostEqual(pnl, -1.0, places=2)
        self.assertEqual(count, 1)


class TestDailyDD(unittest.TestCase):

    def setUp(self):
        self.now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_profitable_day_no_block(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [{
            "closed_at": self.now.isoformat(),
            "realized_pnl_usdt": +2.0,
        }]
        self.assertIsNone(_check_daily_dd(state, self.now))

    def test_small_loss_no_block(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [{
            "closed_at": self.now.isoformat(),
            "realized_pnl_usdt": -3.0,  # 没到 -$5
        }]
        self.assertIsNone(_check_daily_dd(state, self.now))

    def test_over_limit_blocks(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [{
            "closed_at": self.now.isoformat(),
            "realized_pnl_usdt": -5.5,
        }]
        result = _check_daily_dd(state, self.now)
        self.assertIsNotNone(result)
        self.assertIn("-$5", result)

    def test_at_limit_blocks(self):
        """Edge: 正好 -$5 也触发 (使用 <= 而非 <)."""
        state = _empty_live_state()
        state["live_closed_trades"] = [{
            "closed_at": self.now.isoformat(),
            "realized_pnl_usdt": -LIVE_DAILY_DD_LIMIT_USDT,
        }]
        self.assertIsNotNone(_check_daily_dd(state, self.now))


class TestRiskGatesAggregator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_pause = live_trader.PAUSE_FLAG_PATH
        self._orig_stop = live_trader.EMERGENCY_STOP_PATH
        live_trader.PAUSE_FLAG_PATH = Path(self.tmpdir) / ".cresus-pause"
        live_trader.EMERGENCY_STOP_PATH = Path(self.tmpdir) / ".cresus-emergency-stop"
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        live_trader.PAUSE_FLAG_PATH = self._orig_pause
        live_trader.EMERGENCY_STOP_PATH = self._orig_stop
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_all_clear_doesnt_block(self):
        state = _empty_live_state()
        result = check_risk_gates(state, self.now)
        self.assertFalse(result["block_new_opens"])
        self.assertEqual(result["reasons"], [])

    def test_pause_blocks(self):
        live_trader.PAUSE_FLAG_PATH.write_text("paused for sleep")
        state = _empty_live_state()
        result = check_risk_gates(state, self.now)
        self.assertTrue(result["block_new_opens"])
        self.assertEqual(len(result["reasons"]), 1)

    def test_multiple_triggers_reported(self):
        """多个 gate 同时触发应全部报告."""
        live_trader.PAUSE_FLAG_PATH.write_text("manual")
        live_trader.EMERGENCY_STOP_PATH.write_text("auto: DD limit")
        state = _empty_live_state()
        state["live_open_trades"] = [{"notional_usdt": 60.0}]   # 触发 cash reserve
        result = check_risk_gates(state, self.now)
        self.assertTrue(result["block_new_opens"])
        self.assertGreaterEqual(len(result["reasons"]), 3)

    def test_metrics_included(self):
        state = _empty_live_state()
        state["live_open_trades"] = [{"notional_usdt": 20.0}]
        state["live_closed_trades"] = [{
            "closed_at": self.now.isoformat(),
            "realized_pnl_usdt": -1.5,
        }]
        result = check_risk_gates(state, self.now)
        self.assertAlmostEqual(result["daily_pnl"], -1.5, places=2)
        self.assertAlmostEqual(result["deployed_usdt"], 20.0, places=2)


class TestMainLoopWithRiskGates(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_live_state = live_trader.LIVE_STATE
        self._orig_paper_history = live_trader.PAPER_HISTORY
        self._orig_pause = live_trader.PAUSE_FLAG_PATH
        self._orig_stop = live_trader.EMERGENCY_STOP_PATH
        live_trader.LIVE_STATE = Path(self.tmpdir) / "live.json"
        live_trader.PAPER_HISTORY = Path(self.tmpdir) / "paper.json"
        live_trader.PAUSE_FLAG_PATH = Path(self.tmpdir) / ".cresus-pause"
        live_trader.EMERGENCY_STOP_PATH = Path(self.tmpdir) / ".cresus-emergency-stop"
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def tearDown(self):
        live_trader.LIVE_STATE = self._orig_live_state
        live_trader.PAPER_HISTORY = self._orig_paper_history
        live_trader.PAUSE_FLAG_PATH = self._orig_pause
        live_trader.EMERGENCY_STOP_PATH = self._orig_stop
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed_eligible_paper_trade(self):
        now = datetime.now(timezone.utc)
        live_trader.PAPER_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        live_trader.PAPER_HISTORY.write_text(json.dumps({
            "open_trades": [{
                "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "entered_at": (now - timedelta(seconds=10)).isoformat(),
                "entry_price": 81000.0,
                "sl": 80190.0,
                "tp1": 82215.0,
                "tp2": 83430.0,
            }],
            "recent_closed": [],
        }))

    def test_pause_flag_blocks_new_open(self):
        """pause flag 存在 → eligible trade 不被 mirror."""
        live_trader.PAUSE_FLAG_PATH.write_text("test pause")
        self._seed_eligible_paper_trade()
        with patch.object(self.client, "open_position") as mock_open:
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_open.call_count, 0)
        self.assertEqual(len(result["live_open_trades"]), 0)
        # 也不应记入 mirrored (这样删 pause flag 后还能再 mirror)
        self.assertEqual(len(result["mirrored_paper_ids"]), 0)

    def test_no_pause_allows_open(self):
        """没 pause flag → eligible trade 正常 mirror."""
        self._seed_eligible_paper_trade()
        mock_result = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY",
            "qty": 0.0002, "avg_fill_price": 81000.0,
            "actual_notional": 16.2, "entry_order_id": 1,
            "entry_client_id": "x", "sl_price": 80190.0, "sl_side": "SELL",
            "sl_mode": "client_side", "opened_at": "x", "fees_paid_usdt": 0,
            "_dryRun": True,
        }
        with patch.object(self.client, "open_position", return_value=mock_result):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(len(result["live_open_trades"]), 1)

    def test_cash_reserve_blocks(self):
        """已部署 $60 = cap → 新单不开."""
        # seed 已有 3 笔满 cap
        state = _empty_live_state()
        state["live_open_trades"] = [
            {"trade_id": "L1", "symbol": "ETHUSDT", "paper_id": "x1",
             "notional_usdt": 20.0, "side": "BUY", "sl_price": 1.0, "phase": "A"},
            {"trade_id": "L2", "symbol": "SOLUSDT", "paper_id": "x2",
             "notional_usdt": 20.0, "side": "BUY", "sl_price": 1.0, "phase": "A"},
            {"trade_id": "L3", "symbol": "BNBUSDT", "paper_id": "x3",
             "notional_usdt": 20.0, "side": "BUY", "sl_price": 1.0, "phase": "A"},
        ]
        save_live_state(state)
        self._seed_eligible_paper_trade()
        with patch.object(self.client, "open_position") as mock_open:
            # 同时 mock klines 避免 sl polling 试网络
            with patch.object(self.client, "get_klines",
                              return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
                result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_open.call_count, 0)


# ============================================================================
# Phase 3.3.b: 单 symbol 上限 (is_eligible_for_mirror 加的新检查)
# ============================================================================

class TestSingleSymbolLimit(unittest.TestCase):

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.trade = {
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (self.now - timedelta(seconds=30)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
        }

    def test_symbol_already_open_blocks(self):
        """live 已有同 symbol 持仓 (即使 direction 不同) → block."""
        live = _empty_live_state()
        live["live_open_trades"] = [{
            "symbol": "BTCUSDT", "side": "SELL",  # SHORT
            "paper_id": "other_paper_id",
        }]
        ok, reason = is_eligible_for_mirror(self.trade, live, self.now)
        self.assertFalse(ok)
        self.assertIn("already has open", reason)

    def test_different_symbol_doesnt_block(self):
        """live 有 ETH 持仓, 不阻碍开 BTC."""
        live = _empty_live_state()
        live["live_open_trades"] = [{
            "symbol": "ETHUSDT", "side": "BUY",
            "paper_id": "ethpaper",
        }]
        ok, reason = is_eligible_for_mirror(self.trade, live, self.now)
        self.assertTrue(ok, f"unexpected block: {reason}")


# ============================================================================
# Phase 3.3.b: Cumulative DD kill switch
# ============================================================================

class TestCumulativeDD(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_stop = live_trader.EMERGENCY_STOP_PATH
        live_trader.EMERGENCY_STOP_PATH = Path(self.tmpdir) / ".emergency-stop"
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def tearDown(self):
        live_trader.EMERGENCY_STOP_PATH = self._orig_stop
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_account_balance_normal(self):
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": "95.5"}):
            b = _get_account_balance(self.client)
        self.assertAlmostEqual(b, 95.5)

    def test_account_balance_api_failure(self):
        with patch.object(self.client, "get_account",
                          side_effect=BinanceError("network")):
            b = _get_account_balance(self.client)
        self.assertIsNone(b)

    def test_dd_under_limit_no_trigger(self):
        """balance ≥ threshold ($95) → 不触发."""
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": "96.0"}):
            result = _check_cumulative_dd_and_trigger(self.client)
        self.assertIsNone(result)
        self.assertFalse(live_trader.EMERGENCY_STOP_PATH.exists())

    def test_dd_at_threshold_triggers(self):
        """balance = 94.99 < threshold $95 → 触发 + 自动写文件."""
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": "94.99"}):
            result = _check_cumulative_dd_and_trigger(self.client)
        self.assertIsNotNone(result)
        self.assertIn("cumulative DD", result)
        # 文件被自动创建
        self.assertTrue(live_trader.EMERGENCY_STOP_PATH.exists())
        content = live_trader.EMERGENCY_STOP_PATH.read_text()
        self.assertIn("AUTO", content)
        self.assertIn("cumulative DD", content)

    def test_dd_deep_triggers(self):
        """balance = 80 (-20% DD) → 触发."""
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": "80.0"}):
            result = _check_cumulative_dd_and_trigger(self.client)
        self.assertIsNotNone(result)
        self.assertIn("20", result)  # 20% DD 应出现在 reason

    def test_dd_api_failure_doesnt_block(self):
        """get_account 失败时返 None (不 trigger), 让其他 gate 兜底."""
        with patch.object(self.client, "get_account",
                          side_effect=BinanceError("temp")):
            result = _check_cumulative_dd_and_trigger(self.client)
        self.assertIsNone(result)
        self.assertFalse(live_trader.EMERGENCY_STOP_PATH.exists())


# ============================================================================
# Phase 3.3.b: Position reconciliation
# ============================================================================

class TestPositionReconciliation(unittest.TestCase):

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def test_all_match_ok(self):
        """Live 和 exchange 同样的 symbols → ok."""
        live = _empty_live_state()
        live["live_open_trades"] = [
            {"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"},
        ]
        mock_positions = [
            {"symbol": "BTCUSDT", "positionAmt": "0.001"},
            {"symbol": "ETHUSDT", "positionAmt": "0.01"},
            {"symbol": "SOLUSDT", "positionAmt": "0"},  # 空仓不算
        ]
        with patch.object(self.client, "get_positions",
                          return_value=mock_positions):
            result = check_position_reconciliation(self.client, live)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mismatches"], [])

    def test_live_only_mismatch(self):
        """Live tracks BTCUSDT 但 exchange 0 持仓 → live_only mismatch."""
        live = _empty_live_state()
        live["live_open_trades"] = [{"symbol": "BTCUSDT"}]
        mock_positions = [{"symbol": "BTCUSDT", "positionAmt": "0"}]
        with patch.object(self.client, "get_positions",
                          return_value=mock_positions):
            result = check_position_reconciliation(self.client, live)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["mismatches"]), 1)
        self.assertEqual(result["mismatches"][0]["kind"], "live_only")
        self.assertEqual(result["mismatches"][0]["symbol"], "BTCUSDT")

    def test_exchange_only_mismatch(self):
        """Exchange 有 ETHUSDT 但 live 不知 → exchange_only mismatch."""
        live = _empty_live_state()
        # live 没有 trades
        mock_positions = [{"symbol": "ETHUSDT", "positionAmt": "0.01"}]
        with patch.object(self.client, "get_positions",
                          return_value=mock_positions):
            result = check_position_reconciliation(self.client, live)
        self.assertFalse(result["ok"])
        self.assertEqual(result["mismatches"][0]["kind"], "exchange_only")

    def test_api_failure_returns_ok_with_flag(self):
        """API 失败时 ok=True (不 block), 但 api_failed=True 标记."""
        live = _empty_live_state()
        live["live_open_trades"] = [{"symbol": "BTCUSDT"}]
        with patch.object(self.client, "get_positions",
                          side_effect=BinanceError("rate limit")):
            result = check_position_reconciliation(self.client, live)
        self.assertTrue(result["ok"])
        self.assertTrue(result["api_failed"])

    def test_short_position_detected(self):
        """SHORT 持仓 (positionAmt 负数) 也算 active."""
        live = _empty_live_state()
        mock_positions = [{"symbol": "BTCUSDT", "positionAmt": "-0.001"}]
        with patch.object(self.client, "get_positions",
                          return_value=mock_positions):
            result = check_position_reconciliation(self.client, live)
        # live 没 BTC, exchange 有 → exchange_only mismatch
        self.assertEqual(len(result["mismatches"]), 1)


# ============================================================================
# Phase 3.3.b: check_risk_gates 含 client 参数 (累计 DD)
# ============================================================================

class TestRiskGatesWithClient(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_stop = live_trader.EMERGENCY_STOP_PATH
        self._orig_pause = live_trader.PAUSE_FLAG_PATH
        live_trader.EMERGENCY_STOP_PATH = Path(self.tmpdir) / ".emergency"
        live_trader.PAUSE_FLAG_PATH = Path(self.tmpdir) / ".pause"
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        live_trader.EMERGENCY_STOP_PATH = self._orig_stop
        live_trader.PAUSE_FLAG_PATH = self._orig_pause
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_without_client_skips_cumulative_dd(self):
        """不传 client → 跳过累计 DD 检查 (向后兼容)."""
        state = _empty_live_state()
        result = check_risk_gates(state, self.now)
        self.assertFalse(result["block_new_opens"])

    def test_with_client_normal_no_block(self):
        state = _empty_live_state()
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": "100.0"}):
            result = check_risk_gates(state, self.now, client=self.client)
        self.assertFalse(result["block_new_opens"])

    def test_with_client_dd_triggers_and_creates_flag(self):
        state = _empty_live_state()
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": "85.0"}):
            result = check_risk_gates(state, self.now, client=self.client)
        self.assertTrue(result["block_new_opens"])
        # 应该有 cumulative DD reason
        dd_reasons = [r for r in result["reasons"] if "cumulative DD" in r]
        self.assertEqual(len(dd_reasons), 1)
        # 也应该有 emergency stop reason (因为 trigger 也 creates 文件)
        # 然后下次 check 时 emergency_stop_flag 也命中
        # 实际上第一次 check_risk_gates 调用里两个 reason 都会出 (因为 emergency 先 check, 然后 cumulative DD 也 check 并触发)
        emergency_reasons = [r for r in result["reasons"]
                             if "emergency stop" in r]
        # 第一次调用时, emergency_stop_flag check 先, 那时还没文件
        # 然后 cumulative DD check 触发 + 写文件
        # 所以这一次只看到 cumulative DD reason
        # 但下次 tick 会同时命中 (emergency_stop_flag + cumulative_DD)
        # 这里只测第一次, 所以 emergency_reasons 数应该是 0 或 1
        self.assertLessEqual(len(emergency_reasons), 1)


# ============================================================================
# Bug fix: 循环内 re-check eligibility (防 max_concurrent / single_symbol / cash 超限)
# ============================================================================

class TestMirrorIterationGuards(unittest.TestCase):
    """Bug fix verification: 循环内 mirror 多个时, 每次都重新评估 state."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_live_state = live_trader.LIVE_STATE
        self._orig_paper_history = live_trader.PAPER_HISTORY
        live_trader.LIVE_STATE = Path(self.tmpdir) / "live.json"
        live_trader.PAPER_HISTORY = Path(self.tmpdir) / "paper.json"
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

        self.mock_open_result = {
            "trade_id": "L1", "symbol": "X",
            "side": "BUY", "qty": 0.001,
            "avg_fill_price": 100.0, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0, "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": "x", "fees_paid_usdt": 0, "_dryRun": True,
        }

    def tearDown(self):
        live_trader.LIVE_STATE = self._orig_live_state
        live_trader.PAPER_HISTORY = self._orig_paper_history
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_paper(self, trades):
        live_trader.PAPER_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        live_trader.PAPER_HISTORY.write_text(json.dumps({
            "open_trades": trades, "recent_closed": [],
        }))

    def test_max_concurrent_enforced_during_loop(self):
        """Paper 有 5 笔 eligible 信号, 但 max_concurrent=3 → 只 mirror 3 笔."""
        now = datetime.now(timezone.utc)
        trades = []
        for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
            trades.append({
                "id": f"{sym}|LONG|2026-05-15T10:00:0{i}+00:00",
                "symbol": f"{sym}USDT",
                "direction": "LONG",
                "entered_at": (now - timedelta(seconds=10)).isoformat(),
                "entry_price": 100.0,
                "sl": 95.0,
            })
        self._write_paper(trades)

        # 自定义 mock_result 每次返回不同 symbol
        call_count = [0]
        def make_result(symbol, side, **kwargs):
            call_count[0] += 1
            r = dict(self.mock_open_result)
            r["symbol"] = symbol
            r["trade_id"] = f"L{call_count[0]}"
            return r

        with patch.object(self.client, "open_position",
                          side_effect=make_result):
            with patch.object(self.client, "get_klines",
                              return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
                result = main_loop(self.client, dry_run=True)

        # ⚠️ Bug fix verification: max_concurrent=3, 必须只 mirror 3 笔
        self.assertEqual(call_count[0], 3,
                         f"应严格在 max_concurrent=3 处停止, 实际 mirror {call_count[0]} 次")
        self.assertEqual(len(result["live_open_trades"]), 3)

    def test_same_symbol_blocks_within_iteration(self):
        """Paper 有 POLYX LONG + POLYX SHORT 同时 open → 只 mirror 第一笔."""
        now = datetime.now(timezone.utc)
        trades = [
            {
                "id": "POLYXUSDT|LONG|2026-05-15T10:00:00+00:00",
                "symbol": "POLYXUSDT", "direction": "LONG",
                "entered_at": (now - timedelta(seconds=10)).isoformat(),
                "entry_price": 1.0, "sl": 0.95,
            },
            {
                "id": "POLYXUSDT|SHORT|2026-05-15T10:00:05+00:00",
                "symbol": "POLYXUSDT", "direction": "SHORT",
                "entered_at": (now - timedelta(seconds=5)).isoformat(),
                "entry_price": 1.0, "sl": 1.05,
            },
        ]
        self._write_paper(trades)

        call_count = [0]
        def make_result(symbol, side, **kwargs):
            call_count[0] += 1
            r = dict(self.mock_open_result)
            r["symbol"] = symbol
            r["side"] = side
            return r

        with patch.object(self.client, "open_position",
                          side_effect=make_result):
            with patch.object(self.client, "get_klines",
                              return_value=[[0, 0, 0, 0, "1.0", 0, 0, 0, 0, 0, 0, 0]]):
                result = main_loop(self.client, dry_run=True)

        # ⚠️ Bug fix: 同 symbol 即使不同方向, 也只能开 1 笔
        self.assertEqual(call_count[0], 1,
                         f"同 symbol 应只 mirror 1 笔, 实际 {call_count[0]}")
        self.assertEqual(len(result["live_open_trades"]), 1)

    def test_orphan_position_blocks_mirror(self):
        """关键 bug fix: state 被清后, exchange 有但 live 不知 → 跳过 mirror."""
        now = datetime.now(timezone.utc)
        # paper 有 STORJUSDT signal
        self._write_paper([{
            "id": "STORJUSDT|LONG|2026-05-15T15:00:00+00:00",
            "symbol": "STORJUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=60)).isoformat(),
            "entry_price": 0.1089, "sl": 0.1073,
        }])
        # exchange 已经有 STORJUSDT 持仓 (上次 mirror 留下的孤儿)
        mock_positions = [{"symbol": "STORJUSDT", "positionAmt": "183.0"}]

        call_count = [0]
        def make_open(*args, **kwargs):
            call_count[0] += 1
            r = dict(self.mock_open_result)
            r["symbol"] = kwargs.get("symbol", "STORJUSDT")
            return r

        # client.dry_run=False (真实模式, recon 会跑)
        live_client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=False, testnet=True)
        with patch.object(live_client, "open_position", side_effect=make_open):
            with patch.object(live_client, "get_positions",
                              return_value=mock_positions):
                with patch.object(live_client, "get_account",
                                  return_value={"totalMarginBalance": "5000"}):
                    with patch.object(live_client, "get_klines",
                                      return_value=[[0, 0, 0, 0, "0.1089",
                                                     0, 0, 0, 0, 0, 0, 0]]):
                        result = main_loop(live_client, dry_run=False)

        # ⚠️ Bug fix: 即使 paper 有 signal, 不应再 mirror (防 2× size)
        self.assertEqual(call_count[0], 0,
                         f"orphan 已存在, 不应 mirror, 实际调用 {call_count[0]} 次")
        # paper_id 应被加入 mirrored_paper_ids (防本 tick warning 刷屏)
        self.assertIn("STORJUSDT|LONG|2026-05-15T15:00:00+00:00",
                      result["mirrored_paper_ids"])

    def test_cash_reserve_blocks_within_iteration(self):
        """Paper 有 4 笔 eligible, deploy cap $60 → 最多 mirror 3 笔 (3 × $20 = $60)."""
        now = datetime.now(timezone.utc)
        trades = []
        for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD"]):
            trades.append({
                "id": f"{sym}|LONG|2026-05-15T10:00:0{i}+00:00",
                "symbol": f"{sym}USDT",
                "direction": "LONG",
                "entered_at": (now - timedelta(seconds=10)).isoformat(),
                "entry_price": 100.0, "sl": 95.0,
            })
        self._write_paper(trades)

        call_count = [0]
        def make_result(symbol, side, **kwargs):
            call_count[0] += 1
            r = dict(self.mock_open_result)
            r["symbol"] = symbol
            r["actual_notional"] = 20.0   # 每笔 $20
            return r

        with patch.object(self.client, "open_position",
                          side_effect=make_result):
            with patch.object(self.client, "get_klines",
                              return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
                result = main_loop(self.client, dry_run=True)

        # 应在 max_concurrent (3) OR cash reserve ($60) 早触发处停, 都是 3
        self.assertLessEqual(call_count[0], 3)


# ============================================================================
# Phase 3.2.c: Live trades history publishing
# ============================================================================

class TestComputeLiveStats(unittest.TestCase):

    def test_empty_state(self):
        state = _empty_live_state()
        s = _compute_live_stats(state)
        self.assertEqual(s["total_trades"], 0)
        self.assertEqual(s["wins"], 0)
        self.assertEqual(s["losses"], 0)
        self.assertEqual(s["total_pnl_usdt"], 0)
        self.assertEqual(s["starting_capital_usdt"], 100.0)

    def test_with_closed_trades(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [
            {"realized_pnl_usdt": 0.5, "fees_paid_usdt": 0.02},
            {"realized_pnl_usdt": -0.3, "fees_paid_usdt": 0.02},
            {"realized_pnl_usdt": 1.0, "fees_paid_usdt": 0.02},
        ]
        s = _compute_live_stats(state)
        self.assertEqual(s["total_trades"], 3)
        self.assertEqual(s["closed"], 3)
        self.assertEqual(s["wins"], 2)
        self.assertEqual(s["losses"], 1)
        self.assertAlmostEqual(s["win_rate"], 2/3, places=2)
        self.assertAlmostEqual(s["total_pnl_usdt"], 1.2, places=2)
        self.assertAlmostEqual(s["best_trade_usdt"], 1.0, places=2)
        self.assertAlmostEqual(s["worst_trade_usdt"], -0.3, places=2)

    def test_with_open_positions(self):
        state = _empty_live_state()
        state["live_open_trades"] = [
            {"notional_usdt": 20.0, "fees_paid_usdt": 0.01},
            {"notional_usdt": 25.0, "fees_paid_usdt": 0.01},
        ]
        s = _compute_live_stats(state)
        self.assertEqual(s["open"], 2)
        self.assertEqual(s["slots_used"], 2)
        self.assertAlmostEqual(s["deployed_usdt"], 45.0, places=2)
        # free = 100 - 45 + 0 (无已实现 PnL) = 55
        self.assertAlmostEqual(s["free_capital_usdt"], 55.0, places=2)

    def test_net_pnl_and_fees_breakdown(self):
        """net_pnl = total_pnl − fees_realized; free_capital 用 NET."""
        state = _empty_live_state()
        state["live_closed_trades"] = [
            {"realized_pnl_usdt": 0.50,  "fees_paid_usdt": 0.015,
             "fees_are_actual": True},
            {"realized_pnl_usdt": -0.30, "fees_paid_usdt": 0.012,
             "fees_are_actual": True},
        ]
        state["live_open_trades"] = [
            {"notional_usdt": 20.0, "fees_paid_usdt": 0.008,
             "fees_are_actual": True},
        ]
        s = _compute_live_stats(state)
        # gross PnL (毛)
        self.assertAlmostEqual(s["total_pnl_usdt"], 0.20, places=4)
        # 已实现费用 (closed only)
        self.assertAlmostEqual(s["fees_realized_usdt"], 0.027, places=6)
        # 持仓中费用 (open only)
        self.assertAlmostEqual(s["fees_open_usdt"], 0.008, places=6)
        # 总费用
        self.assertAlmostEqual(s["fees_paid_usdt"], 0.035, places=6)
        # net = 0.20 − 0.027 = 0.173
        self.assertAlmostEqual(s["net_pnl_usdt"], 0.173, places=4)
        # free_capital = starting − deployed + net = 100 − 20 + 0.173 → round(2) = 80.17
        self.assertAlmostEqual(s["free_capital_usdt"], 80.17, places=2)
        self.assertTrue(s["fees_are_actual"])

    def test_fees_are_actual_false_if_any_estimate(self):
        state = _empty_live_state()
        state["live_closed_trades"] = [
            {"realized_pnl_usdt": 0.5, "fees_paid_usdt": 0.02,
             "fees_are_actual": True},
            {"realized_pnl_usdt": -0.3, "fees_paid_usdt": 0.02,
             "fees_are_actual": False},
        ]
        s = _compute_live_stats(state)
        self.assertFalse(s["fees_are_actual"])


class TestMissedSignals(unittest.TestCase):

    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def test_record_basic(self):
        live = _empty_live_state()
        pt = {"id": "BTC|LONG|t1", "symbol": "BTCUSDT", "direction": "LONG",
              "conviction_score": 5, "entered_at": "2026-05-15T10:00:00+00:00"}
        _record_missed_signal(live, pt, "max_concurrent reached (3/3)", self.now)
        self.assertEqual(len(live["missed_signals"]), 1)
        m = live["missed_signals"][0]
        self.assertEqual(m["paper_id"], "BTC|LONG|t1")
        self.assertEqual(m["symbol"], "BTCUSDT")
        self.assertEqual(m["direction"], "LONG")
        self.assertEqual(m["conviction_score"], 5)
        self.assertIn("max_concurrent", m["reason"])

    def test_already_mirrored_not_recorded(self):
        """"already mirrored" 是噪音, 不应记录."""
        live = _empty_live_state()
        pt = {"id": "BTC|LONG|t1", "symbol": "BTCUSDT", "direction": "LONG"}
        _record_missed_signal(live, pt, "already mirrored", self.now)
        self.assertEqual(len(live["missed_signals"]), 0)

    def test_dedupe_same_paper_id(self):
        """同 paper_id 多次记录 → 只保留最新 (reason 变化时也更新)."""
        live = _empty_live_state()
        pt = {"id": "BTC|LONG|t1", "symbol": "BTCUSDT", "direction": "LONG"}
        _record_missed_signal(live, pt, "max_concurrent reached", self.now)
        # 下一 tick: 原因变了 (空了 slot 但风控触发)
        _record_missed_signal(live, pt, "risk_gate: daily_dd", self.now)
        self.assertEqual(len(live["missed_signals"]), 1)
        self.assertIn("daily_dd", live["missed_signals"][0]["reason"])

    def test_rolling_window_cap(self):
        live = _empty_live_state()
        for i in range(MISSED_SIGNALS_KEEP_LAST_N + 10):
            pt = {"id": f"SYM{i}|LONG|t", "symbol": f"SYM{i}USDT",
                  "direction": "LONG"}
            _record_missed_signal(live, pt, "max_concurrent reached", self.now)
        # 保留最近 N 条
        self.assertEqual(len(live["missed_signals"]), MISSED_SIGNALS_KEEP_LAST_N)
        # 验证保留的是最新的 (最后 10 个)
        symbols = [m["symbol"] for m in live["missed_signals"]]
        self.assertIn(f"SYM{MISSED_SIGNALS_KEEP_LAST_N + 9}USDT", symbols)
        self.assertNotIn("SYM0USDT", symbols)

    def test_prune_paper_closed(self):
        """paper 已平仓的 signal → 从 missed 移除 (不再 actionable)."""
        live = _empty_live_state()
        live["missed_signals"] = [
            {"paper_id": "BTC|LONG|t1", "symbol": "BTCUSDT"},
            {"paper_id": "ETH|LONG|t2", "symbol": "ETHUSDT"},
        ]
        _prune_obsolete_missed(live, paper_open_ids={"BTC|LONG|t1"})
        self.assertEqual(len(live["missed_signals"]), 1)
        self.assertEqual(live["missed_signals"][0]["symbol"], "BTCUSDT")

    def test_prune_already_mirrored(self):
        """后来被成功 mirror 的 → 从 missed 移除."""
        live = _empty_live_state()
        live["mirrored_paper_ids"] = ["BTC|LONG|t1"]
        live["missed_signals"] = [
            {"paper_id": "BTC|LONG|t1", "symbol": "BTCUSDT"},
            {"paper_id": "ETH|LONG|t2", "symbol": "ETHUSDT"},
        ]
        _prune_obsolete_missed(live, paper_open_ids={"BTC|LONG|t1", "ETH|LONG|t2"})
        symbols = [m["symbol"] for m in live["missed_signals"]]
        self.assertNotIn("BTCUSDT", symbols)
        self.assertIn("ETHUSDT", symbols)

    def test_missing_paper_id_noop(self):
        """没 paper_id 不应崩."""
        live = _empty_live_state()
        _record_missed_signal(live, {"symbol": "x"}, "any", self.now)
        self.assertEqual(len(live["missed_signals"]), 0)


class TestPublishLiveHistory(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_history = live_trader.LIVE_HISTORY
        live_trader.LIVE_HISTORY = Path(self.tmpdir) / "live_history.json"

    def tearDown(self):
        live_trader.LIVE_HISTORY = self._orig_history
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_publish_empty_state(self):
        state = _empty_live_state()
        ok = publish_live_history(state)
        self.assertTrue(ok)
        self.assertTrue(live_trader.LIVE_HISTORY.exists())
        payload = json.loads(live_trader.LIVE_HISTORY.read_text())
        # 验证关键字段
        self.assertIn("version", payload)
        self.assertIn("generated_at", payload)
        self.assertIn("stats", payload)
        self.assertIn("open_trades", payload)
        self.assertIn("recent_closed", payload)
        self.assertIn("config", payload)
        self.assertEqual(payload["open_trades"], [])
        self.assertEqual(payload["recent_closed"], [])

    def test_publish_with_trades(self):
        state = _empty_live_state()
        state["live_open_trades"] = [{
            "symbol": "BTCUSDT", "side": "BUY",
            "qty": 0.001, "avg_fill_price": 81000.0,
            "notional_usdt": 81.0, "sl_price": 80190.0,
            "phase": "A", "trade_id": "L1",
        }]
        state["live_closed_trades"] = [{
            "symbol": "ETHUSDT", "side": "BUY",
            "realized_pnl_usdt": 0.3,
            "closed_at": "2026-05-15T10:00:00+00:00",
            "close_reason": "paper:hit_trail",
        }]
        ok = publish_live_history(state)
        self.assertTrue(ok)
        payload = json.loads(live_trader.LIVE_HISTORY.read_text())
        self.assertEqual(len(payload["open_trades"]), 1)
        self.assertEqual(len(payload["recent_closed"]), 1)
        self.assertEqual(payload["stats"]["total_trades"], 2)
        self.assertEqual(payload["stats"]["wins"], 1)

    def test_publish_atomic(self):
        """publish 应用 .tmp + rename 原子写."""
        state = _empty_live_state()
        ok = publish_live_history(state)
        self.assertTrue(ok)
        # .tmp 不应残留
        tmp_path = live_trader.LIVE_HISTORY.with_suffix(".tmp")
        self.assertFalse(tmp_path.exists())

    def test_publish_includes_risk_and_recon(self):
        state = _empty_live_state()
        risk = {
            "block_new_opens": True,
            "reasons": ["test reason"],
            "daily_pnl": -2.5,
            "deployed_usdt": 40.0,
        }
        recon = {
            "ok": False,
            "mismatches": [{"symbol": "BTC", "kind": "live_only",
                            "message": "test"}],
            "api_failed": False,
            "live_symbols": ["BTCUSDT"],
            "exchange_symbols": [],
        }
        ok = publish_live_history(state, risk=risk, recon=recon)
        self.assertTrue(ok)
        payload = json.loads(live_trader.LIVE_HISTORY.read_text())
        self.assertTrue(payload["risk_status"]["block_new_opens"])
        self.assertFalse(payload["reconciliation"]["ok"])
        self.assertEqual(len(payload["reconciliation"]["mismatches"]), 1)

    def test_publish_config_section(self):
        state = _empty_live_state()
        publish_live_history(state)
        payload = json.loads(live_trader.LIVE_HISTORY.read_text())
        config = payload["config"]
        self.assertEqual(config["starting_capital_usdt"], 100.0)
        self.assertEqual(config["max_concurrent"], 3)
        self.assertIn("BTCUSDT", config["symbol_whitelist"])
        self.assertEqual(config["daily_dd_limit_usdt"], 5.0)

    def test_publish_recent_closed_sorted_desc(self):
        """recent_closed 应按 closed_at 倒序 (最新在前)."""
        state = _empty_live_state()
        state["live_closed_trades"] = [
            {"closed_at": "2026-05-15T10:00:00+00:00", "realized_pnl_usdt": 0.5},
            {"closed_at": "2026-05-15T12:00:00+00:00", "realized_pnl_usdt": -0.2},
            {"closed_at": "2026-05-15T11:00:00+00:00", "realized_pnl_usdt": 0.3},
        ]
        publish_live_history(state)
        payload = json.loads(live_trader.LIVE_HISTORY.read_text())
        # 应按时间倒序: 12:00, 11:00, 10:00
        ts = [t["closed_at"] for t in payload["recent_closed"]]
        self.assertEqual(ts[0], "2026-05-15T12:00:00+00:00")
        self.assertEqual(ts[-1], "2026-05-15T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
