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
    _paper_to_live_side, _try_mirror_open,
    load_paper_state, load_live_state, save_live_state,
    main_loop, _empty_live_state,
    LIVE_SYMBOL_WHITELIST, LIVE_MAX_CONCURRENT, LIVE_MIRROR_MAX_AGE_SEC,
    LIVE_NOTIONAL_USDT,
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
        ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok)
        self.assertIn("whitelist", reason)

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
        """非白名单 symbol 不会被认为 eligible."""
        from binance_client import BinanceClient
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
