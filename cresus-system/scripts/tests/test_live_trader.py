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
    _paper_to_live_side,
    load_paper_state, load_live_state, save_live_state,
    main_loop, _empty_live_state,
    LIVE_SYMBOL_WHITELIST, LIVE_MAX_CONCURRENT, LIVE_MIRROR_MAX_AGE_SEC,
)
from binance_client import _validate_trade_id  # noqa: E402

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
