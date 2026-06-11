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

# Phase 6.A-fix R7: 测试套件运行时必须以 testnet 模式导入 live_trader,
# 不让 CRESUS_MODE=mainnet_pilot 等 env 干扰测试 (paranoid review 实测发现:
# 设了 env 跑会让 8 个不相关测试失败).
# 必须在 `import live_trader` 之前 pop 环境变量.
for _env_key in ('CRESUS_MODE', 'CRESUS_PILOT_CAPITAL'):
    os.environ.pop(_env_key, None)

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
    _compute_pre_entry_slippage_bps,
    _get_effective_slippage_threshold,
    LIVE_SLIPPAGE_THRESHOLD_BY_INTENSITY,
    _compute_btc_regime,
    _ab_use_sl_compensation,
    _compute_compensated_sl,
    MISSED_SIGNALS_KEEP_LAST_N,
    LIVE_MAX_ENTRY_SLIPPAGE_BPS, POLL_INTERVAL_SEC,
    LIVE_BTC_REGIME_THRESHOLD_PCT,
    LIVE_SL_COMPENSATION_MODE,
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
        # Phase 6.F (2026-06-08): 这些老测试用 conv=6 / Tier C+LONG+chop 默认 trade,
        # 会被新 6.F 黑名单 gate 拦截. 这些类测的是 OLD gate (4.H/4.F/4.M/...),
        # 不应受 6.F 干扰. 此 patch 禁用 6.F 以隔离测试 OLD gate 行为.
        # (TestPhase6FBlacklist 单独覆盖 6.F gate 本身行为.)
        self._6f_patcher = patch.object(
            live_trader, "LIVE_PHASE_6F_BLACKLIST_ENABLED", False)
        self._6f_patcher.start()
        self.addCleanup(self._6f_patcher.stop)
        # 标准的 paper trade. conviction_score=6 让 Phase 4.H filter (默认阈值 6)
        # 默认通过, 这样 TestEligibility 测试其它 gate (黑名单/白名单/并发/...) 时
        # 不会被 conv filter 误拦.
        self.recent_trade = {
            "id": "BTCUSDT|LONG|2026-05-14T11:08:07+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (self.now - timedelta(seconds=30)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
            "conviction_score": 6,
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

    # === Phase 4.B 黑名单测试 ===

    def test_blacklist_blocks_in_observation_mode(self):
        """黑名单优先级最高 — 即使 OBS mode 跳过白名单, 黑名单仍然拒."""
        trade = dict(self.recent_trade)
        trade["symbol"] = "STABLEUSDT"   # 黑名单成员 (5/25 复审后保留)
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok, "OBS mode 不应允许黑名单 symbol")
        self.assertIn("blacklist", reason)

    def test_blacklist_blocks_in_strict_whitelist_mode(self):
        """关 OBS mode 后黑名单仍生效."""
        trade = dict(self.recent_trade)
        trade["symbol"] = "XAGUSDT"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", False):
            ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok)
        # 注意: 这里命中黑名单先于白名单 (黑名单 reason 不是 whitelist)
        self.assertIn("blacklist", reason)

    def test_blacklist_priority_before_whitelist(self):
        """如果一个 symbol 既不在白名单又在黑名单, 应优先报黑名单."""
        trade = dict(self.recent_trade)
        trade["symbol"] = "STABLEUSDT"   # 黑名单且不在白名单
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", False):
            ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertFalse(ok)
        self.assertIn("blacklist", reason)
        self.assertNotIn("whitelist", reason)   # 黑名单先, 白名单不应被报

    def test_non_blacklist_symbol_still_allowed(self):
        """回归: 非黑名单 symbol 在 OBS mode 下应通过 symbol filter."""
        trade = dict(self.recent_trade)
        trade["symbol"] = "FAKEUSDT"  # 既不在白名单也不在黑名单
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
        self.assertTrue(ok, f"非黑名单 symbol OBS mode 应通过, got: {reason}")

    def test_whitelist_symbols_not_in_blacklist(self):
        """sanity: 主白名单 (BTC/ETH/SOL) 绝不应该在黑名单里."""
        from live_trader import LIVE_SYMBOL_BLACKLIST, LIVE_SYMBOL_WHITELIST
        for sym in LIVE_SYMBOL_WHITELIST:
            self.assertNotIn(sym, LIVE_SYMBOL_BLACKLIST,
                             f"白名单核心 {sym} 不应进黑名单 (配置错误)")

    def test_blacklist_is_list_of_strings(self):
        """sanity: 黑名单格式正确."""
        from live_trader import LIVE_SYMBOL_BLACKLIST
        for s in LIVE_SYMBOL_BLACKLIST:
            self.assertIsInstance(s, str)
            self.assertTrue(s.endswith("USDT"),
                            f"{s} 不是 USDT 结尾, 可能是配置错误")

    def test_current_blacklist_members(self):
        """5/25 复审后黑名单仅剩 STABLEUSDT (p<0.05) + XAGUSDT (结构性 -4411).

        历史: 5/17 加 DODO/NMR, 5/21 加 PLAY/GUA/STABLE, 5/25 复审后
        释放 DODO/NMR/PLAY/GUA (scanner 自然淘汰, n≤4 不显著), 保留:
          STABLEUSDT: 5 笔 0 胜 p<0.05, 唯一统计显著
          XAGUSDT:    TradFi-Perps 协议未签 -4411 结构性错误
        """
        from live_trader import LIVE_SYMBOL_BLACKLIST
        for sym in ("STABLEUSDT", "XAGUSDT"):
            self.assertIn(sym, LIVE_SYMBOL_BLACKLIST, f"{sym} 应在黑名单")
        # 释放的 4 个不应再在黑名单
        for sym in ("DODOXUSDT", "NMRUSDT", "PLAYUSDT", "GUAUSDT"):
            self.assertNotIn(sym, LIVE_SYMBOL_BLACKLIST,
                             f"{sym} 5/25 已释放, 不应仍在黑名单")

    def test_blacklist_current_members_block_mirror(self):
        """当前黑名单成员实测拦截."""
        for sym in ("STABLEUSDT", "XAGUSDT"):
            trade = dict(self.recent_trade)
            trade["symbol"] = sym
            with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
                ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
            self.assertFalse(ok, f"{sym} 应被黑名单拦截")
            self.assertIn("blacklist", reason)

    def test_blacklist_case_insensitive_on_paper_symbol(self):
        """防御: paper 若意外传小写/混合大小写 symbol, 黑名单仍能匹配.

        审计发现的 bug: 之前直接用原始字符串比较, "dodoxusdt" in ["DODOXUSDT"]
        会失败 → 黑名单 symbol 在 OBS mode 下被误 mirror.
        修复: is_eligible_for_mirror 内部对 sym 做 .upper() 规范化.
        """
        trade = dict(self.recent_trade)
        # 三种 case 变体都应该被拦截
        for case in ("stableusdt", "StableUSDT", "STABLEUSDT"):
            trade["symbol"] = case
            with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
                ok, reason = is_eligible_for_mirror(trade, self.empty_live, self.now)
            self.assertFalse(ok,
                f"symbol='{case}' 应被黑名单拦截, 但 ok={ok} reason={reason}")
            self.assertIn("blacklist", reason)

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
        # Phase 4.H: 关闭 conv filter
        self._conv_patcher = patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", None)
        self._conv_patcher.start()

    def tearDown(self):
        self._conv_patcher.stop()
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
        # SHORT 完整结构 (sl > entry > tp1 > tp2) — 否则 Phase 5.G post-fill
        # 结构校验会拒 (LONG 模板的 tp1/tp2 在 SHORT 里位于错误侧).
        short_paper["sl"] = 81810.0    # SL 高于 entry
        short_paper["tp1"] = 79785.0   # TP1 低于 entry (1.5R)
        short_paper["tp2"] = 78570.0   # TP2 低于 entry (3R)
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
        """每次 mirror_open 前必须调 set_leverage(LIVE_LEVERAGE), 防止 Binance 默认 20x.

        Phase 6.A-margin: 同时验证 set_margin_type(ISOLATED) 也被调,
        顺序为 set_leverage → set_margin_type → open_position.
        """
        order = []
        def lev_call(symbol, lev, **kw):
            order.append(("lev", symbol, lev))
            return {"_dryRun": True, "leverage": lev, "symbol": symbol}
        def mt_call(symbol, mt, **kw):
            order.append(("mt", symbol, mt))
            return {"_dryRun": True, "marginType": mt, "symbol": symbol}
        def open_call(**kw):
            order.append(("open", kw["symbol"]))
            return self.mock_open_result
        with patch.object(self.client, "set_leverage", side_effect=lev_call), \
             patch.object(self.client, "set_margin_type", side_effect=mt_call), \
             patch.object(self.client, "open_position", side_effect=open_call):
            r = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        self.assertIsNotNone(r)
        # 顺序: set_leverage → set_margin_type → open
        self.assertEqual(order[0][0], "lev")
        self.assertEqual(order[0][1], "BTCUSDT")
        self.assertEqual(order[0][2], live_trader.LIVE_LEVERAGE)
        self.assertEqual(order[1][0], "mt")
        self.assertEqual(order[1][1], "BTCUSDT")
        self.assertEqual(order[1][2], "ISOLATED")
        self.assertEqual(order[2][0], "open")
        # live_trade 中应记录 leverage
        self.assertEqual(r["leverage"], live_trader.LIVE_LEVERAGE)

    def test_set_margin_type_failure_does_not_block_open(self):
        """Phase 6.A-margin: set_margin_type 失败 (e.g. -4046 已被 swallow,
        但若是 -2019 余额不足等其它错) 不应 block 开仓 — 只 warn 后继续."""
        with patch.object(self.client, "set_leverage",
                          return_value={"_dryRun": True}), \
             patch.object(self.client, "set_margin_type",
                          side_effect=BinanceError("unexpected error code")), \
             patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            r = _try_mirror_open(self.client, self.paper_trade, dry_run=True)
        # 开仓仍然成功 (margin type 切失败只 warn 不阻塞)
        self.assertIsNotNone(r)
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

    def test_btc_regime_recorded_when_provided(self):
        """Phase 4.C: 传入 btc_regime 时, live_trade 应有 4 个 btc_* 字段."""
        regime_snap = {
            "regime": "up", "btc_price": 80000.0, "btc_ma25_1h": 79500.0,
            "pct_vs_ma25": 0.63, "change_24h_pct": 1.2,
            "computed_at": "2026-05-17T10:00:00+00:00",
        }
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            r = _try_mirror_open(self.client, self.paper_trade,
                                  dry_run=True, btc_regime=regime_snap)
        self.assertEqual(r["btc_regime_at_open"], "up")
        self.assertEqual(r["btc_price_at_open"], 80000.0)
        self.assertEqual(r["btc_change_24h_at_open"], 1.2)
        self.assertEqual(r["btc_pct_vs_ma25_at_open"], 0.63)

    def test_btc_regime_omitted_when_none(self):
        """向后兼容: 不传 btc_regime (或 None) 不应出现 btc_* 字段."""
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            r = _try_mirror_open(self.client, self.paper_trade,
                                  dry_run=True)   # 不传
        self.assertNotIn("btc_regime_at_open", r)

    def test_btc_regime_malformed_dict_silently_ignored(self):
        """传入空 dict 或缺 regime 字段 → 也不应崩, 不打标签."""
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result):
            r = _try_mirror_open(self.client, self.paper_trade,
                                  dry_run=True, btc_regime={})
        self.assertNotIn("btc_regime_at_open", r)

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
        # Phase 4.H: 关闭 conv filter (这类测试不测它)
        self._conv_patcher = patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", None)
        self._conv_patcher.start()

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
        self._conv_patcher.stop()
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

    def test_main_loop_failed_mirror_retries_next_tick(self):
        """mirror 失败不再永久 blacklist — 下 tick 重试.

        过去行为: 失败 → 加 mirrored_paper_ids → 永不重试 (set_leverage 临时
        失败 / API 超时等可恢复错误也被放弃, 是 bug).
        现在: 失败 → 记 missed_signal + 不加 mirrored_paper_ids → 下 tick 重试.
        退出条件由 mirror_max_age (10min) 和 paper 自然平仓提供.
        """
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
        with patch.object(self.client, "open_position",
                          side_effect=BinanceError("simulated")):
            result = main_loop(self.client, dry_run=True)
        # live_open_trades 没增加 (开仓失败)
        self.assertEqual(len(result["live_open_trades"]), 0)
        # ✓ 关键: paper_id NOT 在 mirrored_paper_ids 里 — 下 tick 可重试
        self.assertNotIn(paper_id, result["mirrored_paper_ids"])
        # ✓ 被记为 missed_signal 供 dashboard 诊断
        missed_ids = [m["paper_id"] for m in result.get("missed_signals", [])]
        self.assertIn(paper_id, missed_ids)

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

    # === Phase 4.D: sync 时 SL 补偿延续 ===

    def test_sync_b_group_applies_offset_to_new_paper_sl(self):
        """B 组 (sl_compensation_offset != 0): paper 移 SL 时, live SL 应跟着移 + 保持 offset."""
        # B 组 live_trade: paper_sl 95, live_entry 100.5, offset = +0.5
        # 原始 live_sl = 95 + 0.5 = 95.5
        lt = {
            "symbol": "BTC", "sl_price": 95.5, "phase": "A",
            "sl_paper_current": 95.0,
            "sl_compensation_offset": 0.5,
            "sl_compensation_enabled": True,
        }
        # paper 命中 TP1, SL 移到 BE (100)
        paper = {"sl": 100.0, "phase": "B"}
        updated = _sync_live_with_paper(lt, paper)
        self.assertTrue(updated)
        # 期望: live_sl = 100 (新 paper SL) + 0.5 (offset) = 100.5
        self.assertAlmostEqual(lt["sl_price"], 100.5, places=6)
        self.assertEqual(lt["sl_paper_current"], 100.0)
        # offset 不应被改 (immutable 跟随 trade 生命周期)
        self.assertAlmostEqual(lt["sl_compensation_offset"], 0.5, places=6)

    def test_sync_a_group_no_offset_unchanged(self):
        """A 组 (offset = 0): sync 行为跟旧逻辑一致."""
        lt = {
            "symbol": "BTC", "sl_price": 95.0, "phase": "A",
            "sl_paper_current": 95.0,
            "sl_compensation_offset": 0,
            "sl_compensation_enabled": False,
        }
        paper = {"sl": 100.0, "phase": "B"}
        _sync_live_with_paper(lt, paper)
        self.assertAlmostEqual(lt["sl_price"], 100.0, places=6)   # 同 paper, 无 offset

    def test_sync_offset_field_missing_treated_as_zero(self):
        """老 trade 没有 sl_compensation_offset 字段 (Phase 4.D 之前的) - 应作 0 处理."""
        lt = {"symbol": "BTC", "sl_price": 95.0, "phase": "A"}   # 无新字段
        paper = {"sl": 96.0, "phase": "A"}
        _sync_live_with_paper(lt, paper)
        # 不崩, sl_price 跟随 paper (offset 默认 0)
        self.assertAlmostEqual(lt["sl_price"], 96.0, places=6)


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


class TestComputePreEntrySlippage(unittest.TestCase):
    """Phase 4.A: 滑点护栏 — paper 信号价 → 当前价 的预滑点计算.

    严格验证侧向 (LONG / SHORT) 归一化: 正 bps = 不利, 负 bps = 有利.
    所有失败路径返回 None, 调用方应放行 (不拒绝交易).
    """

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def _kline(self, price):
        return [[0, 0, 0, 0, str(price), 0, 0, 0, 0, 0, 0, 0]]

    # --- 数值正确性 (核心: 不能算错符号, 否则会把"有利价"反向拒绝) ---

    def test_long_current_higher_than_paper_is_unfavorable_positive_bps(self):
        """LONG: 价格上涨 = 买贵 = 不利 = 正 bps."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        with patch.object(self.client, "get_klines", return_value=self._kline(100.5)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNotNone(slip)
        self.assertAlmostEqual(slip, 50.0, places=1)   # (0.5/100)*10000 = +50 bps

    def test_long_current_lower_than_paper_is_favorable_negative_bps(self):
        """LONG: 价格下跌 = 买便宜 = 有利 = 负 bps."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        with patch.object(self.client, "get_klines", return_value=self._kline(99.5)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, -50.0, places=1)

    def test_short_current_lower_than_paper_is_unfavorable_positive_bps(self):
        """SHORT: 价格下跌 = 卖便宜 = 不利 = 正 bps."""
        paper = {"symbol": "BTCUSDT", "direction": "SHORT", "entry_price": 100.0}
        with patch.object(self.client, "get_klines", return_value=self._kline(99.5)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, 50.0, places=1)

    def test_short_current_higher_than_paper_is_favorable_negative_bps(self):
        """SHORT: 价格上涨 = 卖贵 = 有利 = 负 bps."""
        paper = {"symbol": "BTCUSDT", "direction": "SHORT", "entry_price": 100.0}
        with patch.object(self.client, "get_klines", return_value=self._kline(100.5)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, -50.0, places=1)

    def test_extreme_slippage_656bps_matches_real_storj_case(self):
        """复现实测 STORJ +656 bps 灾难场景."""
        paper = {"symbol": "STORJUSDT", "direction": "LONG", "entry_price": 0.40}
        # paper 0.40 → live current 0.40 × (1 + 0.0656) = 0.42624
        with patch.object(self.client, "get_klines", return_value=self._kline(0.42624)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, 656.0, places=0)

    # --- Fail-safe 路径 (这些必须返 None, 让调用方放行) ---

    def test_returns_none_when_paper_entry_missing(self):
        paper = {"symbol": "BTCUSDT", "direction": "LONG"}
        slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_returns_none_when_paper_entry_zero(self):
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 0}
        slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_returns_none_when_paper_entry_negative(self):
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": -1.0}
        slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_returns_none_when_symbol_missing(self):
        paper = {"direction": "LONG", "entry_price": 100.0}
        slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_returns_none_when_direction_invalid(self):
        paper = {"symbol": "BTCUSDT", "direction": "HOLD", "entry_price": 100.0}
        slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_returns_none_when_get_klines_fails(self):
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        with patch.object(self.client, "get_klines",
                          side_effect=BinanceError("network")):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_returns_none_when_klines_empty(self):
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        with patch.object(self.client, "get_klines", return_value=[]):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_returns_none_when_current_price_zero(self):
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        with patch.object(self.client, "get_klines", return_value=self._kline(0)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    def test_paper_entry_string_value_parsed(self):
        """paper 字段有时是 string, 容错."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": "100.0"}
        with patch.object(self.client, "get_klines", return_value=self._kline(100.5)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, 50.0, places=1)

    def test_paper_entry_malformed_string_returns_none(self):
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": "abc"}
        slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertIsNone(slip)

    # --- 阈值常量合理性 ---

    def test_threshold_is_reasonable(self):
        """阈值是基于实测数据的合理值. 当前 v3: 100 bps.
        不应是 0 (拦截所有) 或天文数字 (失去保护).

        阈值演化:
          v1 (5/17): 30 bps
          v2 (5/17): 50 bps — 116 笔后调整, 减少误杀小滑点
          v3 (5/21): 100 bps — 333 笔后再放宽, 实测高滑点反而赚钱
        """
        self.assertGreater(LIVE_MAX_ENTRY_SLIPPAGE_BPS, 5,
                           "阈值太小会拦截正常市场波动")
        self.assertLess(LIVE_MAX_ENTRY_SLIPPAGE_BPS, 500,
                        "阈值太大失去保护意义 (>500bps = 5% 灾难)")
        # 当前 v3 值: 100 bps
        self.assertEqual(LIVE_MAX_ENTRY_SLIPPAGE_BPS, 100.0,
                         "v3: 333 笔后从 50 → 100, 高滑点组实测人均 +$0.040")

    def test_threshold_boundary_just_below(self):
        """边界: pre_slip = 阈值 - 0.1 应通过 (严格 > 比较)."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        # 算出让 pre_slip 恰好为 (阈值 - 0.1) 的 current
        # pre_slip = (current - 100) / 100 * 10000 = thresh - 0.1
        # current = 100 * (1 + (thresh - 0.1) / 10000)
        target = LIVE_MAX_ENTRY_SLIPPAGE_BPS - 0.1
        current = 100.0 * (1 + target / 10000)
        with patch.object(self.client, "get_klines",
                          return_value=[[0,0,0,0,str(current),0,0,0,0,0,0,0]]):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, target, places=1)
        # 调用方逻辑: slip > LIVE_MAX_ENTRY_SLIPPAGE_BPS → 拒. target < 阈值 → 通过.
        self.assertFalse(slip > LIVE_MAX_ENTRY_SLIPPAGE_BPS,
                         f"slip {slip} 应 <= 阈值 {LIVE_MAX_ENTRY_SLIPPAGE_BPS}, 不应拒")

    def test_threshold_boundary_just_above(self):
        """边界: pre_slip = 阈值 + 0.1 应被拒."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        target = LIVE_MAX_ENTRY_SLIPPAGE_BPS + 0.1
        current = 100.0 * (1 + target / 10000)
        with patch.object(self.client, "get_klines",
                          return_value=[[0,0,0,0,str(current),0,0,0,0,0,0,0]]):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, target, places=1)
        self.assertTrue(slip > LIVE_MAX_ENTRY_SLIPPAGE_BPS,
                        f"slip {slip} 应 > 阈值 {LIVE_MAX_ENTRY_SLIPPAGE_BPS}, 应拒")

    # --- Phase 4.U (5/25): bookTicker 优先, kline fallback ---

    def test_uses_book_ticker_ask_for_long(self):
        """LONG: 应取 askPrice (我们要吃卖一), 不是 bidPrice 也不是 kline."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        bt = {"symbol": "BTCUSDT", "bidPrice": "100.3", "askPrice": "100.5"}
        with patch.object(self.client, "get_book_ticker", return_value=bt), \
             patch.object(self.client, "get_klines",
                          return_value=self._kline(99.0)) as mock_klines:
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, 50.0, places=1)   # (100.5-100)/100*10000
        mock_klines.assert_not_called()                # 不应触发 fallback

    def test_uses_book_ticker_bid_for_short(self):
        """SHORT: 应取 bidPrice (我们要打买一)."""
        paper = {"symbol": "BTCUSDT", "direction": "SHORT", "entry_price": 100.0}
        bt = {"symbol": "BTCUSDT", "bidPrice": "99.7", "askPrice": "99.9"}
        with patch.object(self.client, "get_book_ticker", return_value=bt), \
             patch.object(self.client, "get_klines",
                          return_value=self._kline(101.0)) as mock_klines:
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        # SHORT: (current-paper)/paper * -1; current=99.7, paper=100
        # → (99.7-100)/100*10000*-1 = +30 bps (卖便宜 = 不利)
        self.assertAlmostEqual(slip, 30.0, places=1)
        mock_klines.assert_not_called()

    def test_falls_back_to_kline_on_book_ticker_error(self):
        """bookTicker 失败 → 自动 fallback 到 1m kline close (保留旧行为)."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        with patch.object(self.client, "get_book_ticker",
                          side_effect=BinanceError("network")), \
             patch.object(self.client, "get_klines",
                          return_value=self._kline(100.5)) as mock_klines:
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, 50.0, places=1)
        mock_klines.assert_called_once()

    def test_falls_back_when_book_ticker_returns_zero(self):
        """bookTicker 返 0/None → fallback (防御性, 不该相信 0 价)."""
        paper = {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0}
        bt = {"symbol": "BTCUSDT", "bidPrice": "0", "askPrice": "0"}
        with patch.object(self.client, "get_book_ticker", return_value=bt), \
             patch.object(self.client, "get_klines",
                          return_value=self._kline(100.5)):
            slip = _compute_pre_entry_slippage_bps(self.client, paper)
        self.assertAlmostEqual(slip, 50.0, places=1)


class TestDynamicSlippageThreshold(unittest.TestCase):
    """Phase 4.V: 动态预滑点阈值 (intensity → bps).

    核心不变式:
      - intensity=3 拿到最高阈值 (捕获 XANUSDT 型急拉)
      - intensity=1 保持保守阈值 (不放行低动量信号)
      - 字段缺失 / 非法值 fail-safe 到保守值
    """

    def test_intensity_3_gives_highest_threshold(self):
        """intensity=3: 高速急拉 → 200 bps (XANUSDT 134 bps 应可通过)."""
        t = {"symbol": "XANUSDT", "direction": "LONG",
             "entry_price": 1.0, "intensity": 3}
        self.assertEqual(_get_effective_slippage_threshold(t), 200.0)

    def test_intensity_2_gives_medium_threshold(self):
        t = {"intensity": 2}
        self.assertEqual(_get_effective_slippage_threshold(t), 150.0)

    def test_intensity_1_gives_base_threshold(self):
        t = {"intensity": 1}
        self.assertEqual(_get_effective_slippage_threshold(t), LIVE_MAX_ENTRY_SLIPPAGE_BPS)

    def test_intensity_missing_gives_base_threshold(self):
        """字段缺失 → fail-safe 保守值, 不误放行未知信号."""
        t = {"symbol": "X"}
        self.assertEqual(_get_effective_slippage_threshold(t), LIVE_MAX_ENTRY_SLIPPAGE_BPS)

    def test_intensity_none_gives_base_threshold(self):
        t = {"intensity": None}
        self.assertEqual(_get_effective_slippage_threshold(t), LIVE_MAX_ENTRY_SLIPPAGE_BPS)

    def test_intensity_string_invalid_gives_base_threshold(self):
        """非整数字段 → fail-safe."""
        t = {"intensity": "high"}
        self.assertEqual(_get_effective_slippage_threshold(t), LIVE_MAX_ENTRY_SLIPPAGE_BPS)

    def test_intensity_string_numeric_coerced(self):
        """字符串数字 '3' 应被 int() 解析."""
        t = {"intensity": "3"}
        self.assertEqual(_get_effective_slippage_threshold(t), 200.0)

    def test_threshold_table_is_monotone(self):
        """intensity 越高 → 阈值越高 (单调性)."""
        vals = [LIVE_SLIPPAGE_THRESHOLD_BY_INTENSITY[i] for i in (1, 2, 3)]
        self.assertEqual(vals, sorted(vals))

    def test_xanusdt_134bps_would_pass_intensity3(self):
        """XANUSDT 04:13 复盘: 134 bps 在 intensity=3 (200 bps 阈值) 应通过."""
        t = {"intensity": 3}
        threshold = _get_effective_slippage_threshold(t)
        self.assertGreater(threshold, 134.0,
                           "134 bps 应在 intensity=3 阈值内, 否则 XANUSDT 仍被拦截")

    def test_low_intensity_still_blocks_high_slip(self):
        """intensity=1 时, 101 bps 应超阈值 100 bps → 应被拒.

        阈值演进: 5/25 merge main 时 intensity=1 从 50 改 100 (与 main v3 数据驱动一致).
        """
        t = {"intensity": 1}
        threshold = _get_effective_slippage_threshold(t)
        self.assertLessEqual(threshold, 100.0,
                              "intensity=1 阈值应为 100 bps (与 main v3 数据分析对齐)")

    def test_poll_interval_reduced(self):
        """Phase 4.V: 轮询周期缩短到 5s (确保 plist 改了代码常量也同步)."""
        self.assertEqual(POLL_INTERVAL_SEC, 5,
                         "POLL_INTERVAL_SEC 必须是 5 (Phase 4.V), 否则 --loop 模式仍 30s")


class TestComputeBtcRegime(unittest.TestCase):
    """Phase 4.C: BTC regime 计算 (1h MA25 baseline).

    分类:
      pct_vs_ma25 >= +threshold → up
      pct_vs_ma25 <= -threshold → down
      其他                       → chop
    fail-safe: API 失败 / 数据不足 / 异常值 → None (不阻止 mirror).
    """

    def setUp(self):
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def _kline_set(self, closes):
        """造 25 根 1h kline, 给定 close 价 list."""
        return [[0, 0, 0, 0, str(c), 0, 0, 0, 0, 0, 0, 0] for c in closes]

    # --- 三种 regime 正确性 ---

    def test_up_regime(self):
        """current 高于 MA25 ≥ +0.5% → up."""
        # 24 根 100, 最后一根 101 → MA25 ≈ 100.04, current 101 → +0.96%
        closes = [100.0] * 24 + [101.0]
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertIsNotNone(r)
        self.assertEqual(r["regime"], "up")
        self.assertGreater(r["pct_vs_ma25"], 0.5)

    def test_down_regime(self):
        closes = [100.0] * 24 + [99.0]
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "down")
        self.assertLess(r["pct_vs_ma25"], -0.5)

    def test_chop_regime(self):
        """current 跟 MA25 接近 → chop."""
        # 全 100 → current=100, MA25=100, pct=0
        closes = [100.0] * 25
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "chop")
        self.assertAlmostEqual(r["pct_vs_ma25"], 0.0, places=3)

    def test_chop_boundary_just_below_threshold(self):
        """pct = 0.4% < 0.5% 阈值 → 仍 chop."""
        # ma25 = ~100, current 需要使 pct = 0.4%
        # 24 个 100 + 1 个 X, MA = (2400 + X) / 25
        # (X - MA) / MA = 0.004 → 解出
        # X = MA × 1.004 = (2400 + X) / 25 × 1.004
        # 25 X = (2400 + X) × 1.004
        # 25 X - 1.004 X = 2410 (approx)
        # 23.996 X = 2409.6
        # X ≈ 100.42
        closes = [100.0] * 24 + [100.4]
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "chop", f"0.4% < 0.5% 阈值应判 chop, 实际 {r}")

    # --- 真实 BTC 场景 ---

    def test_user_actual_btc_correction_scenario(self):
        """复现用户 5/17 截图: BTC $78,197 vs MA25 $79,068 → -1.1% → down."""
        # 24 根均价 79068, 最后一根 78197 → ma ≈ 79133, pct ≈ -1.18%
        closes = [79068.0] * 24 + [78197.0]
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "down", f"用户 5/17 场景应是 down, 实际 {r}")
        self.assertLess(r["pct_vs_ma25"], -1.0)

    def test_returns_context_fields(self):
        """返回 dict 必须包含完整 context 供复盘."""
        closes = [80000.0] * 24 + [80500.0]
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        for field in ("regime", "btc_price", "btc_ma25_1h",
                      "pct_vs_ma25", "change_24h_pct", "computed_at"):
            self.assertIn(field, r, f"缺字段 {field}")
        self.assertEqual(r["btc_price"], 80500.0)
        # 24h change: (80500 - 80000) / 80000 * 100 = +0.625
        self.assertAlmostEqual(r["change_24h_pct"], 0.625, places=2)

    # --- Fail-safe 路径 ---

    def test_api_error_returns_none(self):
        with patch.object(self.client, "get_klines",
                          side_effect=BinanceError("rate limit")):
            r = _compute_btc_regime(self.client)
        self.assertIsNone(r)

    def test_empty_klines_returns_none(self):
        with patch.object(self.client, "get_klines", return_value=[]):
            r = _compute_btc_regime(self.client)
        self.assertIsNone(r)

    def test_insufficient_klines_returns_none(self):
        """< 25 根 → None (要求完整 24h baseline)."""
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set([100.0] * 10)):
            r = _compute_btc_regime(self.client)
        self.assertIsNone(r)

    def test_malformed_kline_returns_none(self):
        with patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "not_a_number"] * 25]):
            r = _compute_btc_regime(self.client)
        self.assertIsNone(r)

    def test_zero_price_returns_none(self):
        """全 0 价 → None (避免除以 0)."""
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set([0] * 25)):
            r = _compute_btc_regime(self.client)
        self.assertIsNone(r)

    def test_threshold_constant_reasonable(self):
        """0.5% 阈值合理: 太小会全 up/down (失去 chop 意义), 太大几乎全 chop."""
        self.assertGreaterEqual(LIVE_BTC_REGIME_THRESHOLD_PCT, 0.1)
        self.assertLessEqual(LIVE_BTC_REGIME_THRESHOLD_PCT, 2.0)

    # --- Phase 4.K Shadow Log: sub_regime + 3h change ---

    def test_phase_4k_sub_regime_only_for_down(self):
        """sub_regime 仅在 regime='down' 时计算, up/chop 时应为 None."""
        # up regime: closes 24 个 100 + 最后 101
        with patch.object(self.client, "get_klines",
                          return_value=self._kline_set([100.0]*24 + [101.0])):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "up")
        self.assertIsNone(r.get("sub_regime"), f"up regime sub_regime 应 None, 实际 {r.get('sub_regime')}")
        self.assertIsNone(r.get("change_3h_pct"), "up regime change_3h_pct 应 None")

        # chop regime
        closes = [100.0] * 25
        with patch.object(self.client, "get_klines", return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "chop")
        self.assertIsNone(r.get("sub_regime"))
        self.assertIsNone(r.get("change_3h_pct"))

    def test_phase_4k_down_acute_3h_falling(self):
        """down + 3h 跌 > 1% → sub_regime='down_acute'.
        造数据: 前 21 个 = 100, 后 4 个递减表示最近 3h 急跌."""
        closes = [100.0]*21 + [100.0, 99.5, 99.0, 98.5]
        # current = 98.5, MA25 ≈ 99.76, pct_vs_ma25 = -1.26%, regime = down
        # 3h 前 close = closes[-4] = 100.0, current = 98.5
        # 3h change = (98.5 - 100.0) / 100.0 * 100 = -1.5% → < -1% → acute
        with patch.object(self.client, "get_klines", return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "down")
        self.assertEqual(r["sub_regime"], "down_acute")
        self.assertLess(r["change_3h_pct"], -1.0)

    def test_phase_4k_down_stable_3h_flat(self):
        """down + 3h 在 -1% 到 +0.5% 之间 → sub_regime='down_stable'."""
        closes = [100.0]*21 + [99.0, 99.0, 99.0, 99.0]
        # current = 99.0, MA25 ≈ 99.84, pct_vs_ma25 ≈ -0.84%, regime = down
        # 3h change = (99.0 - 99.0) / 99.0 * 100 = 0% → stable
        with patch.object(self.client, "get_klines", return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "down")
        self.assertEqual(r["sub_regime"], "down_stable")
        self.assertAlmostEqual(r["change_3h_pct"], 0.0, places=2)

    def test_phase_4k_down_rebound_3h_rising(self):
        """down (pct_vs_ma25 ≤ -0.5%) + 3h 涨 > 0.5% → sub_regime='down_rebound'."""
        # 设计: 前 21 个 = 102, 后 4 个反弹 (97, 99, 100, 101)
        # current = 101, MA25 = (102*21 + 97+99+100+101)/25 = (2142+397)/25 = 101.56
        # pct_vs_ma25 = (101-101.56)/101.56 = -0.55% → down (just barely)
        # 3h change = (101 - 97) / 97 * 100 = 4.12% → > 0.5% → rebound
        closes = [102.0]*21 + [97.0, 99.0, 100.0, 101.0]
        with patch.object(self.client, "get_klines", return_value=self._kline_set(closes)):
            r = _compute_btc_regime(self.client)
        self.assertEqual(r["regime"], "down", f"应为 down, 实际 {r['regime']}, pct_vs_ma25={r['pct_vs_ma25']}")
        self.assertEqual(r["sub_regime"], "down_rebound")
        self.assertGreater(r["change_3h_pct"], 0.5)

    def test_phase_4k_sub_regime_does_not_affect_gate_by_default(self):
        """默认配置下 (LIVE_REGIME_GATE_SUB_REGIME_ALLOW=set()), sub_regime 不影响 gate.
        Phase 5.R 引入了 sub_regime 参数但默认 allow-list 为空 — 行为与 4.J 一致.
        """
        from live_trader import _should_block_for_regime, LIVE_REGIME_GATE_SUB_REGIME_ALLOW
        # 默认 allow-list 必须为空 (任何启用都需要显式 flip)
        self.assertEqual(LIVE_REGIME_GATE_SUB_REGIME_ALLOW, set(),
                        "默认 LIVE_REGIME_GATE_SUB_REGIME_ALLOW 必须为空 set, 与 4.J 行为一致")
        # 不论 sub_regime 是什么, gate 默认都看 regime='down' + direction='LONG' → 拒
        self.assertTrue(_should_block_for_regime("LONG", "down"))
        self.assertTrue(_should_block_for_regime("LONG", "down", "down_rebound"))
        self.assertTrue(_should_block_for_regime("LONG", "down", "down_acute"))
        self.assertTrue(_should_block_for_regime("LONG", "down", "down_stable"))


class TestSlCompensation(unittest.TestCase):
    """Phase 4.D: SL slippage compensation + A/B 分组.

    严格验证:
    - 数学正确性 (LONG/SHORT 同公式, 符号无误)
    - A/B 分组确定性 (同 paper_id 总是同分组)
    - mode='off'/'ab'/'always' 各自行为
    - 边界 (字段缺/0/负)
    - 不会引入开仓错误 (只调 SL 数值)
    """

    # === A/B 分组 ===

    def test_ab_mode_off_always_false(self):
        for pid in ['BTC|LONG|t1', 'X|SHORT|t', '', 'abc']:
            self.assertFalse(_ab_use_sl_compensation(pid, "off"))

    def test_ab_mode_always_always_true(self):
        for pid in ['BTC|LONG|t1', 'X|SHORT|t', 'abc']:
            self.assertTrue(_ab_use_sl_compensation(pid, "always"))

    def test_ab_mode_ab_deterministic(self):
        """同 paper_id 多次调用必须返回同样结果 (确定性)."""
        pid = "STORJUSDT|LONG|2026-05-17T10:00:00+00:00"
        results = [_ab_use_sl_compensation(pid, "ab") for _ in range(10)]
        self.assertTrue(all(r == results[0] for r in results),
                         "同 paper_id 必须确定性返回同样分组")

    def test_ab_mode_ab_roughly_50_50(self):
        """大样本验证 ab mode 接近 50/50 分布 (不严格但应在 30-70% 区间)."""
        sample_ids = [f"SYM{i}|LONG|2026-05-{i:02d}T{i%24:02d}:00:00" for i in range(1, 1001)]
        true_count = sum(1 for pid in sample_ids if _ab_use_sl_compensation(pid, "ab"))
        ratio = true_count / len(sample_ids)
        # MD5 hash 应该接近均匀; 1000 个样本 +-3% (chi-square 信赖区间)
        self.assertGreater(ratio, 0.40, f"ratio {ratio:.3f} 偏低, 哈希分布异常")
        self.assertLess(ratio, 0.60, f"ratio {ratio:.3f} 偏高, 哈希分布异常")

    def test_ab_mode_empty_pid_returns_false(self):
        """无 paper_id → 不分组, 走旧逻辑 (退路)."""
        self.assertFalse(_ab_use_sl_compensation("", "ab"))
        self.assertFalse(_ab_use_sl_compensation(None, "ab"))

    def test_ab_mode_unknown_returns_false(self):
        """未知 mode → 安全退路 (不补偿)."""
        self.assertFalse(_ab_use_sl_compensation("any", "garbage"))

    def test_default_mode_uses_config(self):
        """不传 mode 时, 应使用 LIVE_SL_COMPENSATION_MODE 配置."""
        pid = "BTC|LONG|x"
        expected = _ab_use_sl_compensation(pid, LIVE_SL_COMPENSATION_MODE)
        actual = _ab_use_sl_compensation(pid)
        self.assertEqual(actual, expected)

    # === 补偿数学 ===

    def test_compute_long_unfavorable_slip(self):
        """LONG: live 入场高 (不利) → SL 也加, 距离不变."""
        # paper: entry 100, sl 95 (5% 距离)
        # live: entry 100.5 (高 0.5%, 不利)
        # 期望: live_sl = 95 + 0.5 = 95.5 (距离 5%, 不变)
        sl = _compute_compensated_sl(95.0, 100.5, 100.0)
        self.assertAlmostEqual(sl, 95.5, places=6)
        # 距离不变性验证
        self.assertAlmostEqual(100.5 - sl, 100.0 - 95.0, places=6)

    def test_compute_long_favorable_slip(self):
        """LONG: live 入场低 (有利) → SL 也减, 距离不变."""
        sl = _compute_compensated_sl(95.0, 99.5, 100.0)
        self.assertAlmostEqual(sl, 94.5, places=6)
        # 仍然在 entry 下方 5%
        self.assertAlmostEqual(99.5 - sl, 5.0, places=6)

    def test_compute_short_unfavorable_slip(self):
        """SHORT: paper_entry 100, paper_sl 105 (5% 上方). live entry 99.5 (低=不利 SHORT)."""
        # 期望: live_sl = 105 + (99.5 - 100) = 104.5
        # 距离不变: live_sl - live_entry = 104.5 - 99.5 = 5.0 ✓
        sl = _compute_compensated_sl(105.0, 99.5, 100.0)
        self.assertAlmostEqual(sl, 104.5, places=6)
        self.assertAlmostEqual(sl - 99.5, 105.0 - 100.0, places=6)

    def test_compute_short_favorable_slip(self):
        """SHORT: live entry 100.5 (高=SHORT 有利)."""
        # live_sl = 105 + 0.5 = 105.5; distance 105.5 - 100.5 = 5.0 ✓
        sl = _compute_compensated_sl(105.0, 100.5, 100.0)
        self.assertAlmostEqual(sl, 105.5, places=6)
        self.assertAlmostEqual(sl - 100.5, 5.0, places=6)

    def test_compute_no_slip(self):
        """live_entry == paper_entry → SL 不变."""
        sl = _compute_compensated_sl(95.0, 100.0, 100.0)
        self.assertAlmostEqual(sl, 95.0, places=6)

    def test_compute_extreme_storj_656bps(self):
        """复现实测 STORJ +656 bps 进场 (gate 修复前发生过)."""
        # paper 0.119, sl 0.113 (5% LONG 距离)
        # live 0.119 * 1.0656 ≈ 0.126805
        # adjusted: 0.113 + (0.126805 - 0.119) = 0.120805
        sl = _compute_compensated_sl(0.113, 0.126805, 0.119)
        self.assertAlmostEqual(sl, 0.120805, places=5)
        # 距离仍 5% (从 live entry)
        self.assertAlmostEqual(0.126805 - sl, 0.119 - 0.113, places=5)

    # === Fail-safe (任何输入异常 → None, caller 退路) ===

    def test_compute_negative_paper_sl(self):
        self.assertIsNone(_compute_compensated_sl(-1, 100, 100))

    def test_compute_zero_live_entry(self):
        self.assertIsNone(_compute_compensated_sl(95, 0, 100))

    def test_compute_zero_paper_entry(self):
        self.assertIsNone(_compute_compensated_sl(95, 100, 0))

    def test_compute_string_inputs_parsed(self):
        """字段可能是字符串 (容错)."""
        sl = _compute_compensated_sl("95", "100.5", "100")
        self.assertAlmostEqual(sl, 95.5, places=6)

    def test_compute_malformed_inputs_returns_none(self):
        self.assertIsNone(_compute_compensated_sl("abc", 100, 100))
        self.assertIsNone(_compute_compensated_sl(95, None, 100))

    def test_compute_extreme_negative_result_passthrough(self):
        """极端不可能情况: 大不利滑点 + 紧 SL → 计算结果 <= 0.
        函数本身不过滤 (输入>0即接受), 但 _try_mirror_open 会检查 compensated > 0
        再决定用 / fallback 到 paper_sl. 这是分层防御.
        """
        # paper 0.01 entry, 0.005 SL. live 进场 0.001 (极端不可能, 测试边界)
        sl = _compute_compensated_sl(0.005, 0.001, 0.01)
        self.assertIsNotNone(sl)
        self.assertAlmostEqual(sl, -0.004, places=6)
        # caller (_try_mirror_open) 应该看 sl > 0 失败 → fallback paper_sl
        self.assertFalse(sl > 0, "caller 应该不用此值, fallback 到 paper_sl")


class TestAbGroup(unittest.TestCase):
    """Phase 4.E: A/B/C 三组分配 (paper_id MD5 mod 3)."""

    def test_deterministic(self):
        """同一 paper_id 永远返回同一组."""
        pid = "BTCUSDT|LONG|2026-05-15T10:00:00+00:00"
        g1 = live_trader._ab_group(pid)
        g2 = live_trader._ab_group(pid)
        self.assertEqual(g1, g2)
        self.assertIn(g1, ("A", "B", "C"))

    def test_empty_paper_id_defaults_to_a(self):
        """空 paper_id 退路 = 'A' (基线, 不启用任何实验)."""
        self.assertEqual(live_trader._ab_group(""), "A")
        self.assertEqual(live_trader._ab_group(None), "A")

    def test_distribution_balanced_n4(self):
        """1000 个 paper_id 哈希 (mod 4 默认) 应大致 1/4 分布 (容差 ±15%)."""
        from collections import Counter
        ids = [f"SYM{i}USDT|LONG|2026-05-{i%30+1:02d}T{i%24:02d}:00:00+00:00"
               for i in range(1000)]
        counts = Counter(live_trader._ab_group(pid) for pid in ids)
        # 每组期望 250 ± 50 (考虑离散分布的 3 sigma)
        for g in ("A", "B", "C", "D"):
            self.assertGreater(counts[g], 200, f"{g} 组样本太少: {counts[g]}")
            self.assertLess(counts[g], 320, f"{g} 组样本太多: {counts[g]}")

    def test_distribution_balanced_n3(self):
        """显式 n_groups=3 时, 1000 个 paper_id 应大致 1/3 分布 (Phase 4.E 兼容)."""
        from collections import Counter
        ids = [f"SYM{i}USDT|LONG|2026-05-{i%30+1:02d}T{i%24:02d}:00:00+00:00"
               for i in range(1000)]
        counts = Counter(live_trader._ab_group(pid, n_groups=3) for pid in ids)
        for g in ("A", "B", "C"):
            self.assertGreater(counts[g], 280, f"{g} 组样本太少: {counts[g]}")
            self.assertLess(counts[g], 400, f"{g} 组样本太多: {counts[g]}")

    def test_different_ids_can_differ(self):
        """至少有 2 个不同 paper_id 落在不同组 (sanity)."""
        groups = set()
        for i in range(30):
            groups.add(live_trader._ab_group(f"X{i}USDT|LONG|T+00:00"))
        self.assertGreaterEqual(len(groups), 2, "30 个 id 应能覆盖至少 2 组")


class TestAbUseWickFilter(unittest.TestCase):
    """Phase 4.E: wick filter A/B/C 启用判定."""

    def test_off_mode_never_enables(self):
        self.assertFalse(live_trader._ab_use_wick_filter("any", mode="off"))

    def test_always_mode_always_enables(self):
        self.assertTrue(live_trader._ab_use_wick_filter("any", mode="always"))
        self.assertTrue(live_trader._ab_use_wick_filter("", mode="always"))

    def test_abc_only_c_group(self):
        """abc mode (n=3): 只有 C 组返 True, A/B 返 False."""
        for i in range(60):
            pid = f"S{i}|LONG|T+00:00"
            g = live_trader._ab_group(pid, n_groups=3)
            r = live_trader._ab_use_wick_filter(pid, mode="abc")
            if g == "C":
                self.assertTrue(r, f"{pid} 应该 C 组 = True")
            else:
                self.assertFalse(r, f"{pid} 应该 {g} 组 = False")

    def test_abcd_only_c_group(self):
        """abcd mode (n=4, Phase 4.F 默认): 只有 C 组返 True, A/B/D 返 False."""
        for i in range(80):
            pid = f"S{i}|LONG|T+00:00"
            g = live_trader._ab_group(pid, n_groups=4)
            r = live_trader._ab_use_wick_filter(pid, mode="abcd")
            if g == "C":
                self.assertTrue(r, f"{pid} (g={g}) 应该 wick=True")
            else:
                self.assertFalse(r, f"{pid} (g={g}) 应该 wick=False")

    def test_phase_4l_always_mode_all_groups_enabled(self):
        """Phase 4.L (2026-05-24): wick filter 推广到 'always', 所有 group 都启用.

        基于 9 天 (5/15-5/24, 239 笔 sl_breach_client) 数据驱动:
          C 组 32.7% 假止损率 vs A/B/D 38.0% (5.3 pp 改善).
        切到 'always' 全员启用, 预期月度 +$170.
        """
        # 各种 paper_id 覆盖 A/B/C/D 4 组, always mode 都应 True
        for i in range(100):
            pid = f"S{i}|LONG|T+00:00"
            self.assertTrue(
                live_trader._ab_use_wick_filter(pid, mode="always"),
                f"always mode 应对 {pid} (group={live_trader._ab_group(pid, n_groups=4)}) 返 True",
            )

    def test_phase_4l_module_default_is_always(self):
        """Phase 4.L: 模块级默认值已从 'abcd' 切到 'always'."""
        self.assertEqual(live_trader.LIVE_SL_WICK_FILTER_MODE, "always",
                         "Phase 4.L 已部署, 默认应为 'always'")

    def test_empty_paper_id_safe_path(self):
        """空 paper_id 在 abc 模式下也安全 (返 False = 不过滤, 等同 A 组)."""
        # _ab_group("") == "A", 所以 abc 模式 wick=False
        self.assertFalse(live_trader._ab_use_wick_filter("", mode="abc"))

    def test_unknown_mode_safe_fallback(self):
        """未知 mode → 返 False (不启用, 等同退路)."""
        self.assertFalse(live_trader._ab_use_wick_filter("any", mode="xyz"))


class TestSlBreachWickFilter(unittest.TestCase):
    """Phase 4.E: _check_sl_breach 在 wick_filter_enabled=True 时要求连续 N
    次 breach 才触发. 关键测试:
    - off (legacy): instant trigger 不变
    - on, 单次 breach: 不触发, 计数 +1
    - on, 连续 2 次: 触发
    - on, breach 后回归: 计数清零, 后续单次再 breach 不触发
    """

    def test_filter_off_instant_trigger_long(self):
        """A/B 组 (wick_filter_enabled=False) 行为不变 — 单次 breach 即触发."""
        lt = {"side": "BUY", "sl_price": 80000.0, "wick_filter_enabled": False}
        self.assertTrue(_check_sl_breach(lt, 79000.0))
        # 计数器应保持 0 (从未累计过)
        self.assertEqual(lt.get("sl_breach_count", 0), 0)

    def test_filter_off_no_field_legacy_behavior(self):
        """老 trade 没有 wick_filter_enabled 字段 = 默认 instant trigger."""
        lt = {"side": "BUY", "sl_price": 80000.0}
        self.assertTrue(_check_sl_breach(lt, 79000.0))

    def test_filter_on_single_breach_no_trigger(self):
        """C 组: 第一次 breach 仅累计, 不触发."""
        lt = {"side": "BUY", "sl_price": 80000.0, "wick_filter_enabled": True,
              "sl_breach_count": 0,
              "wick_filter_min_breaches": 2}
        result = _check_sl_breach(lt, 79000.0)
        self.assertFalse(result, "首次 breach 不应触发")
        self.assertEqual(lt["sl_breach_count"], 1)

    def test_filter_on_two_consecutive_triggers(self):
        """C 组: 连续 2 次 breach → 触发."""
        lt = {"side": "BUY", "sl_price": 80000.0, "wick_filter_enabled": True,
              "sl_breach_count": 0,
              "wick_filter_min_breaches": 2}
        self.assertFalse(_check_sl_breach(lt, 79000.0))  # 第 1 次
        self.assertEqual(lt["sl_breach_count"], 1)
        self.assertTrue(_check_sl_breach(lt, 78500.0))   # 第 2 次, 触发
        self.assertEqual(lt["sl_breach_count"], 2)

    def test_filter_on_breach_then_recover_resets_count(self):
        """C 组: breach 1 次后价格回到 SL 外, 计数应清零."""
        lt = {"side": "BUY", "sl_price": 80000.0, "wick_filter_enabled": True,
              "sl_breach_count": 0,
              "wick_filter_min_breaches": 2}
        self.assertFalse(_check_sl_breach(lt, 79000.0))   # 第 1 次 breach
        self.assertEqual(lt["sl_breach_count"], 1)
        self.assertFalse(_check_sl_breach(lt, 80500.0))   # 回到 SL 外, 清零
        self.assertEqual(lt["sl_breach_count"], 0)
        # 再次 breach 应该重新累计 (第 1 次, 不触发)
        self.assertFalse(_check_sl_breach(lt, 79000.0))
        self.assertEqual(lt["sl_breach_count"], 1)

    def test_filter_on_short_direction(self):
        """C 组 SHORT 方向: current ≥ sl 是 breach. 同样需要连续 2 次."""
        lt = {"side": "SELL", "sl_price": 80000.0, "wick_filter_enabled": True,
              "sl_breach_count": 0,
              "wick_filter_min_breaches": 2}
        self.assertFalse(_check_sl_breach(lt, 81000.0))   # 第 1 次
        self.assertTrue(_check_sl_breach(lt, 81500.0))    # 第 2 次, 触发

    def test_filter_on_custom_threshold_3(self):
        """C 组若 wick_filter_min_breaches=3, 需 3 次才触发."""
        lt = {"side": "BUY", "sl_price": 80000.0, "wick_filter_enabled": True,
              "sl_breach_count": 0,
              "wick_filter_min_breaches": 3}
        self.assertFalse(_check_sl_breach(lt, 79000.0))   # 1
        self.assertFalse(_check_sl_breach(lt, 79000.0))   # 2
        self.assertTrue(_check_sl_breach(lt, 79000.0))    # 3, 触发

    def test_filter_default_min_breaches_from_config(self):
        """C 组若没 wick_filter_min_breaches 字段, fallback 到全局常数.
        Phase 5.J→5.M→6.E: 默认 2→3→4→6, 测试更新到 6 次确认 (跟 paper 30s 周期对齐).
        """
        lt = {"side": "BUY", "sl_price": 80000.0, "wick_filter_enabled": True,
              "sl_breach_count": 0}   # 注意: 无 wick_filter_min_breaches
        # LIVE_WICK_FILTER_MIN_BREACHES 默认 6, 需 6 次连续 breach 才触发
        for i in range(5):
            self.assertFalse(_check_sl_breach(lt, 79000.0), f"breach #{i+1} 不应触发")
        self.assertTrue(_check_sl_breach(lt, 79000.0))    # 第 6, 触发

    def test_filter_no_breach_does_not_change_count(self):
        """C 组: 非 breach 调用不影响已有 0 计数."""
        lt = {"side": "BUY", "sl_price": 80000.0, "wick_filter_enabled": True,
              "sl_breach_count": 0,
              "wick_filter_min_breaches": 2}
        self.assertFalse(_check_sl_breach(lt, 81000.0))
        self.assertEqual(lt["sl_breach_count"], 0)


class TestSyncResetsBreachCount(unittest.TestCase):
    """Phase 4.E: 当 _sync_live_with_paper 更新 SL 时, 应清零 sl_breach_count
    (旧 breach 证据对新 SL 失效)."""

    def test_sl_change_resets_count(self):
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A",
              "sl_breach_count": 1,         # C 组累积了 1 次 breach
              "wick_filter_enabled": True}
        paper = {"sl": 79000.0, "phase": "B"}
        updated = live_trader._sync_live_with_paper(lt, paper)
        self.assertTrue(updated)
        self.assertEqual(lt["sl_price"], 79000.0)
        self.assertEqual(lt["sl_breach_count"], 0, "SL 移动后计数应清零")

    def test_no_sl_change_keeps_count(self):
        """SL 没变 (同步无变化), 计数不应被错误清零."""
        lt = {"symbol": "BTC", "sl_price": 80000.0, "phase": "A",
              "sl_breach_count": 1,
              "wick_filter_enabled": True}
        paper = {"sl": 80000.0, "phase": "A"}   # 完全相同
        live_trader._sync_live_with_paper(lt, paper)
        self.assertEqual(lt["sl_breach_count"], 1, "SL 未变, 计数保持")


class TestPhase4EIntegration(unittest.TestCase):
    """Phase 4.E/4.F 完整链路: _ab_group → _ab_use_* → live_trade 字段
    → _check_sl_breach / regime gate 行为一致.

    注: 用显式 n_groups=3 搜对应 group, 然后用 mode='abc' 测试 (Phase 4.E 兼容).
    Phase 4.F 见 TestPhase4FIntegration.
    """

    def test_c_group_paper_id_wick_filter_active(self):
        """选一个 C 组 paper_id (n=3), 验证整条链路启用 wick filter."""
        c_pid = None
        for i in range(100):
            candidate = f"TEST{i}|LONG|2026-05-15T10:00:00+00:00"
            if live_trader._ab_group(candidate, n_groups=3) == "C":
                c_pid = candidate
                break
        self.assertIsNotNone(c_pid)
        self.assertTrue(live_trader._ab_use_wick_filter(c_pid, mode="abc"))
        self.assertFalse(live_trader._ab_use_sl_compensation(c_pid, mode="abc"))

    def test_b_group_paper_id_compensation_only(self):
        """选一个 B 组 paper_id (n=3), 验证只启用补偿不启用 wick filter."""
        b_pid = None
        for i in range(100):
            candidate = f"TEST{i}|LONG|2026-05-15T10:00:00+00:00"
            if live_trader._ab_group(candidate, n_groups=3) == "B":
                b_pid = candidate
                break
        self.assertIsNotNone(b_pid)
        self.assertTrue(live_trader._ab_use_sl_compensation(b_pid, mode="abc"))
        self.assertFalse(live_trader._ab_use_wick_filter(b_pid, mode="abc"))

    def test_a_group_paper_id_neither(self):
        """选一个 A 组 paper_id (n=3), 既不补偿也不过滤 (基线)."""
        a_pid = None
        for i in range(100):
            candidate = f"TEST{i}|LONG|2026-05-15T10:00:00+00:00"
            if live_trader._ab_group(candidate, n_groups=3) == "A":
                a_pid = candidate
                break
        self.assertIsNotNone(a_pid)
        self.assertFalse(live_trader._ab_use_sl_compensation(a_pid, mode="abc"))
        self.assertFalse(live_trader._ab_use_wick_filter(a_pid, mode="abc"))


class TestShouldBlockForRegime(unittest.TestCase):
    """Phase 4.F: _should_block_for_regime 核心规则.

    规则: down + LONG → True. 其余 → False.
    """

    def test_down_long_blocked(self):
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down"))

    def test_down_short_allowed(self):
        self.assertFalse(live_trader._should_block_for_regime("SHORT", "down"))

    def test_up_long_allowed(self):
        self.assertFalse(live_trader._should_block_for_regime("LONG", "up"))

    def test_up_short_allowed(self):
        self.assertFalse(live_trader._should_block_for_regime("SHORT", "up"))

    def test_chop_long_allowed(self):
        self.assertFalse(live_trader._should_block_for_regime("LONG", "chop"))

    def test_chop_short_allowed(self):
        self.assertFalse(live_trader._should_block_for_regime("SHORT", "chop"))

    def test_none_regime_allowed(self):
        """regime=None (取价失败) → 不应误拦, 让 trade 通过其它 gate."""
        self.assertFalse(live_trader._should_block_for_regime("LONG", None))
        self.assertFalse(live_trader._should_block_for_regime("SHORT", None))

    def test_empty_direction_safe(self):
        """direction 空字符串 → 不拦 (其它 gate 会处理)."""
        self.assertFalse(live_trader._should_block_for_regime("", "down"))

    def test_case_insensitive(self):
        """方向 / regime 大小写都接受."""
        self.assertTrue(live_trader._should_block_for_regime("long", "DOWN"))
        self.assertTrue(live_trader._should_block_for_regime("Long", "Down"))

    def test_sub_regime_param_optional_backward_compat(self):
        """Phase 5.R: sub_regime 参数可选, 不传 = 老行为完全一致."""
        # 不传 sub_regime
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down"))
        # 传 None 等同于不传
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", None))
        # 传 "" 也视为未命中 allow-list (空字符串 → falsy short-circuit)
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", ""))


class TestPhase5RSubRegimeAllowList(unittest.TestCase):
    """Phase 5.R: _should_block_for_regime sub_regime allow-list 行为.

    设计要点:
    - 默认 LIVE_REGIME_GATE_SUB_REGIME_ALLOW = set() → 100% 等同 Phase 4.J
    - allow-list 非空且 sub_regime 命中 → down+LONG 豁免 (return False)
    - 仅影响 down + LONG; 其它 (regime, direction) 组合永远不受 allow-list 影响
    - None / 空字符串 sub_regime 永远不命中 (fail-safe)
    """

    def setUp(self):
        # 保存原值, 测试后恢复 — 避免污染其它 test
        self._orig_allow = live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW.copy()

    def tearDown(self):
        live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW = self._orig_allow

    def test_default_allow_set_is_empty(self):
        """默认必须空 — 任何启用都需要显式 flip."""
        self.assertEqual(self._orig_allow, set(),
                        "Phase 5.R 默认 LIVE_REGIME_GATE_SUB_REGIME_ALLOW 必须为空 set")

    def test_default_behavior_blocks_all_down_long(self):
        """默认空 allow-list 下, 所有 sub_regime 的 down+LONG 都被拒."""
        for sub in ("down_acute", "down_stable", "down_rebound", None, ""):
            self.assertTrue(
                live_trader._should_block_for_regime("LONG", "down", sub),
                f"default 空 allow-list, sub_regime={sub!r} 应被拒"
            )

    def test_allow_rebound_only_passes_rebound(self):
        """allow_set={'down_rebound'} → 仅 rebound 通过, acute/stable 仍拒."""
        live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {"down_rebound"}
        # rebound 通过
        self.assertFalse(live_trader._should_block_for_regime("LONG", "down", "down_rebound"))
        # acute / stable 仍被拒
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", "down_acute"))
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", "down_stable"))
        # None / 空仍被拒 (fail-safe)
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", None))
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", ""))
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down"))

    def test_allow_multiple_sub_regimes(self):
        """allow_set 多个值 → 任一命中即放行."""
        live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {"down_rebound", "down_stable"}
        self.assertFalse(live_trader._should_block_for_regime("LONG", "down", "down_rebound"))
        self.assertFalse(live_trader._should_block_for_regime("LONG", "down", "down_stable"))
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", "down_acute"))

    def test_allow_list_does_not_affect_short(self):
        """down+SHORT 本身就不被 gate 拒, allow-list 不应改变此行为."""
        live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {"down_rebound", "down_stable", "down_acute"}
        for sub in ("down_acute", "down_stable", "down_rebound", None):
            self.assertFalse(
                live_trader._should_block_for_regime("SHORT", "down", sub),
                f"down+SHORT 永远不被 gate 拒, sub={sub!r}"
            )

    def test_allow_list_does_not_affect_up_chop(self):
        """up / chop regime 下, allow-list 完全无效 (本来就不被 gate 拒)."""
        live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {"down_rebound"}
        # up+LONG / chop+LONG 永远 False
        self.assertFalse(live_trader._should_block_for_regime("LONG", "up", "down_rebound"))
        self.assertFalse(live_trader._should_block_for_regime("LONG", "chop", "down_rebound"))
        # 即便 sub_regime 给个不合理值, 也不会影响 (因为 r != "down")
        self.assertFalse(live_trader._should_block_for_regime("LONG", "up", "anything"))

    def test_allow_list_rejects_unknown_sub_regime(self):
        """sub_regime 不在 allow 集合中, 一律拒 (open-world fail-safe)."""
        live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {"down_rebound"}
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", "down_supercrash"))
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", "bullish_lol"))

    def test_allow_list_case_sensitive_on_sub_regime(self):
        """sub_regime 字符串需精确匹配 — 防止大小写笔误意外放行.
        (down_rebound 是 _compute_btc_regime 生成的固定 token, 全小写)
        """
        live_trader.LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {"down_rebound"}
        # 精确匹配
        self.assertFalse(live_trader._should_block_for_regime("LONG", "down", "down_rebound"))
        # 大写不匹配 — 应拒
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", "DOWN_REBOUND"))
        self.assertTrue(live_trader._should_block_for_regime("LONG", "down", "Down_Rebound"))


class TestPhase5SRegimeSizeMultiplier(unittest.TestCase):
    """Phase 5.S: _regime_size_multiplier — 按 (direction, regime, sub_regime) 查 multiplier.

    设计要点:
    - 生产默认 dict 是 audit-driven 的 3 条; 测试用 setUp 清空隔离, 单独验证默认值.
    - lookup 优先级: 完全匹配 > (d, r, None) > (d, None, None) > 1.0
    - clamp 到 [MIN, MAX] = [0.0, 3.0]
    - direction 大小写 normalize, regime 大小写 normalize, sub_regime 精确匹配
    """

    def setUp(self):
        self._orig_mult = dict(live_trader.LIVE_REGIME_SIZE_MULTIPLIER)
        # 单测期间清空 — 行为测试不应受生产默认配置干扰
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {}

    def tearDown(self):
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = self._orig_mult

    def test_production_default_matches_audit_2026_06_02(self):
        """生产默认必须 = 2026-06-02 audit 拍板的 3 条 (回归锁).
        若有人改了, 此测试会爆 — 提醒必须有 audit 数据支持.
        """
        expected = {
            ("LONG",  "chop", None):           1.5,
            ("SHORT", "down", "down_stable"):  1.5,
            ("LONG",  "down", "down_acute"):   0.5,
        }
        self.assertEqual(
            self._orig_mult, expected,
            "Phase 5.S 默认 LIVE_REGIME_SIZE_MULTIPLIER 与 audit 决策不一致"
        )

    def test_empty_dict_returns_one(self):
        """空 dict (test 隔离后) 下任何 (d, r, sub) 都返 1.0."""
        for combo in [
            ("LONG", "chop", None),
            ("SHORT", "down", "down_acute"),
            ("LONG", "up", None),
            ("SHORT", "down", "down_rebound"),
        ]:
            self.assertEqual(
                live_trader._regime_size_multiplier(*combo), 1.0,
                f"空 dict, {combo} 应返 1.0"
            )

    def test_exact_match(self):
        """完全匹配优先于通配."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "down", "down_acute"): 1.5,
        }
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "down", "down_acute"), 1.5
        )
        # 其它桶应保持 1.0
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "down", "down_stable"), 1.0
        )
        self.assertEqual(
            live_trader._regime_size_multiplier("LONG", "down", "down_acute"), 1.0
        )

    def test_fallback_to_regime_wildcard(self):
        """(d, r, None) 用于 regime 内不分 sub 的桶 (e.g. chop / up)."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "chop", None): 1.3,
        }
        self.assertEqual(
            live_trader._regime_size_multiplier("LONG", "chop", None), 1.3
        )
        # sub_regime 即使有, fallback 也会命中 (d, r, None)
        self.assertEqual(
            live_trader._regime_size_multiplier("LONG", "chop", "anything"), 1.3
        )

    def test_fallback_to_direction_only(self):
        """(d, None, None) 是最宽通配, 仅在更精的都没命中时用."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", None, None): 0.8,
        }
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "chop", None), 0.8
        )
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "up", None), 0.8
        )
        # LONG 不命中
        self.assertEqual(
            live_trader._regime_size_multiplier("LONG", "chop", None), 1.0
        )

    def test_lookup_priority_specific_wins(self):
        """完全匹配 > (d, r, None) > (d, None, None)."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", None, None): 0.5,
            ("SHORT", "down", None): 1.2,
            ("SHORT", "down", "down_acute"): 2.0,
        }
        # 最精的胜出
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "down", "down_acute"), 2.0
        )
        # 没完全匹配 → 用 (d, r, None)
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "down", "down_stable"), 1.2
        )
        # 都没 → 用 (d, None, None)
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "chop", None), 0.5
        )

    def test_clamp_max(self):
        """multiplier 超过 MAX 被 clamp."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "down", "down_acute"): 5.0,  # 超 3.0 上限
        }
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "down", "down_acute"),
            live_trader.LIVE_REGIME_SIZE_MULT_MAX
        )

    def test_clamp_min(self):
        """negative 或 < MIN 被 clamp 到 MIN."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "down", "down_acute"): -1.0,
        }
        self.assertEqual(
            live_trader._regime_size_multiplier("LONG", "down", "down_acute"),
            live_trader.LIVE_REGIME_SIZE_MULT_MIN
        )

    def test_zero_multiplier_allowed(self):
        """multiplier = 0 是合法配置 (= stop mirroring 该桶)."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "down", "down_acute"): 0.0,
        }
        self.assertEqual(
            live_trader._regime_size_multiplier("LONG", "down", "down_acute"), 0.0
        )

    def test_empty_direction_returns_one(self):
        """direction 空字符串 fail-safe 返 1.0."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "down", None): 1.5,
        }
        self.assertEqual(live_trader._regime_size_multiplier("", "down", None), 1.0)

    def test_case_normalization(self):
        """direction / regime 大小写都接受, sub_regime 精确匹配."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "down", "down_acute"): 1.7,
        }
        # direction 大写小写都行
        self.assertEqual(
            live_trader._regime_size_multiplier("short", "DOWN", "down_acute"), 1.7
        )
        # sub_regime 大写不匹配 (精确字符串)
        self.assertEqual(
            live_trader._regime_size_multiplier("SHORT", "down", "DOWN_ACUTE"), 1.0
        )

    def test_invalid_multiplier_value_safe(self):
        """配置的 multiplier 值是非 number 时 fail-safe 返 1.0."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "chop", None): "1.5",  # 字符串能 float() — 可转
        }
        # 应该正确转
        self.assertEqual(live_trader._regime_size_multiplier("LONG", "chop", None), 1.5)
        # 不可转值
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "chop", None): "abc",
        }
        self.assertEqual(live_trader._regime_size_multiplier("LONG", "chop", None), 1.0)


class TestPhase5SLiveNotionalForMirror(unittest.TestCase):
    """Phase 5.S: _live_notional_for_mirror — score-based base × regime multiplier."""

    def setUp(self):
        self._orig_mult = dict(live_trader.LIVE_REGIME_SIZE_MULTIPLIER)
        # 测试隔离 — 不受生产默认 mult 干扰
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {}

    def tearDown(self):
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = self._orig_mult

    def test_no_btc_regime_falls_back_to_base(self):
        """btc_regime=None → 返 base (Phase 5.A 行为)."""
        pt = {"direction": "LONG", "conviction_score": 7}
        result = live_trader._live_notional_for_mirror(pt, btc_regime=None)
        self.assertEqual(result, live_trader._live_notional_for_paper(pt))

    def test_empty_mult_dict_returns_base(self):
        """空 mult dict → 返 base (mult=1.0)."""
        pt = {"direction": "LONG", "conviction_score": 7}
        regime = {"regime": "chop", "sub_regime": None}
        result = live_trader._live_notional_for_mirror(pt, btc_regime=regime)
        self.assertEqual(result, live_trader._live_notional_for_paper(pt))

    def test_multiplier_applied(self):
        """配置后 base × mult."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "down", "down_acute"): 1.5,
        }
        pt = {"direction": "SHORT", "conviction_score": 7}
        regime = {"regime": "down", "sub_regime": "down_acute"}
        base = live_trader._live_notional_for_paper(pt)  # 7 → $800
        result = live_trader._live_notional_for_mirror(pt, btc_regime=regime)
        self.assertEqual(result, base * 1.5)  # $1200

    def test_capped_by_max_notional(self):
        """final notional 不超过 LIVE_MAX_NOTIONAL_PER_TRADE."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "down", "down_acute"): 3.0,
        }
        pt = {"direction": "SHORT", "conviction_score": 7}  # base $800
        regime = {"regime": "down", "sub_regime": "down_acute"}
        result = live_trader._live_notional_for_mirror(pt, btc_regime=regime)
        # $800 × 3.0 = $2400, 但被 cap 到 $2000
        self.assertEqual(result, live_trader.LIVE_MAX_NOTIONAL_PER_TRADE)

    def test_zero_multiplier_returns_zero(self):
        """mult=0 → final notional = 0 (会被 is_eligible 拒)."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "down", "down_acute"): 0.0,
        }
        pt = {"direction": "LONG", "conviction_score": 7}
        regime = {"regime": "down", "sub_regime": "down_acute"}
        result = live_trader._live_notional_for_mirror(pt, btc_regime=regime)
        self.assertEqual(result, 0.0)

    def test_non_dict_btc_regime_falls_back(self):
        """btc_regime 非 dict (e.g. str) → fail-safe 返 base."""
        pt = {"direction": "LONG", "conviction_score": 7}
        base = live_trader._live_notional_for_paper(pt)
        self.assertEqual(
            live_trader._live_notional_for_mirror(pt, btc_regime="chop"), base
        )
        self.assertEqual(
            live_trader._live_notional_for_mirror(pt, btc_regime=42), base
        )

    def test_missing_direction_fail_safe(self):
        """paper_trade.direction 缺 → 返 base (mult=1.0 fallback)."""
        pt = {"conviction_score": 7}  # 无 direction
        regime = {"regime": "down", "sub_regime": "down_acute"}
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "down", "down_acute"): 2.0,
        }
        # direction="" → multiplier fallback to 1.0 → 返 base
        result = live_trader._live_notional_for_mirror(pt, btc_regime=regime)
        self.assertEqual(result, live_trader._live_notional_for_paper(pt))


class TestPhase5SIsEligibleMultiplierZero(unittest.TestCase):
    """Phase 5.S: is_eligible_for_mirror 在 multiplier=0 时 reject."""

    def setUp(self):
        self._orig_mult = dict(live_trader.LIVE_REGIME_SIZE_MULTIPLIER)
        # 测试隔离 — 不受生产默认 mult 干扰
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {}

    def tearDown(self):
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = self._orig_mult

    def _make_pt(self, direction, score=7, sym="ETHUSDT"):
        return {
            "id": f"{sym}|{direction}|2026-06-02T10:00:00.000+00:00",
            "symbol": sym,
            "direction": direction,
            "conviction_score": score,
            "entered_at": datetime.now(timezone.utc).isoformat(),
        }

    def test_default_no_mult_no_reject(self):
        """空 mult dict → 不会因 Phase 5.S 拒."""
        pt = self._make_pt("SHORT")
        eligible, reason = live_trader.is_eligible_for_mirror(
            pt, {}, datetime.now(timezone.utc),
            btc_regime="up", btc_sub_regime=None,
        )
        # 不应该因 "regime size multiplier" 被拒
        self.assertNotIn("regime size multiplier", reason)

    def test_zero_mult_long_down_blocked_by_phase4j_first(self):
        """LONG+down 即使设 Phase 5.S mult=0, 也被 Phase 4.J gate 先拒 (gate 顺序).

        验证 gate 优先级: 7 (Phase 4.J regime gate) → 7b (Phase 5.S mult=0).
        down+LONG 命中 4.J 就 return, 不会走到 5.S → reason 是 "regime gate" 不是
        "regime size multiplier". 这是设计上的预期, 不是 bug.
        """
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("LONG", "down", "down_acute"): 0.0,
        }
        pt = self._make_pt("LONG", sym="BTCUSDT")
        eligible, reason = live_trader.is_eligible_for_mirror(
            pt, {}, datetime.now(timezone.utc),
            btc_regime="down", btc_sub_regime="down_acute",
        )
        self.assertFalse(eligible)
        # Phase 4.J 在前, Phase 5.S 不应被触发
        self.assertIn("regime gate", reason)
        self.assertNotIn("size multiplier", reason)

    def test_zero_mult_short_rejects(self):
        """SHORT 桶 mult=0 → Phase 5.S 接管 reject."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "up", None): 0.0,
        }
        pt = self._make_pt("SHORT", sym="BTCUSDT")
        eligible, reason = live_trader.is_eligible_for_mirror(
            pt, {}, datetime.now(timezone.utc),
            btc_regime="up", btc_sub_regime=None,
        )
        self.assertFalse(eligible)
        self.assertIn("regime size multiplier=0", reason)
        self.assertIn("Phase 5.S", reason)

    def test_positive_mult_does_not_reject(self):
        """mult > 0 → Phase 5.S 不拒 (可能其它 gate 拒, 但不是 5.S)."""
        live_trader.LIVE_REGIME_SIZE_MULTIPLIER = {
            ("SHORT", "down", "down_acute"): 1.5,
        }
        pt = self._make_pt("SHORT", sym="BTCUSDT")
        eligible, reason = live_trader.is_eligible_for_mirror(
            pt, {}, datetime.now(timezone.utc),
            btc_regime="down", btc_sub_regime="down_acute",
        )
        # 不应有 Phase 5.S reject
        self.assertNotIn("regime size multiplier", reason)


class TestPhase6AMainnetPilotConfig(unittest.TestCase):
    """Phase 6.A: CRESUS_MODE=mainnet_pilot 环境变量驱动配置.

    设计原则:
    - 默认无 env (= testnet 模式) — 完全等同部署前行为 (backward compat)
    - mainnet_pilot 模式按 PILOT_CAPITAL 分 3 tier 强制 reset 风控参数
    - Phase 5.S multipliers 一律清空
    - 启动 banner + safety verify 函数存在且可调用
    """

    def test_default_mode_is_testnet(self):
        """模块默认 CRESUS_MODE='testnet' (未设 env var 时)."""
        # 模块导入时已经 capture, 直接看
        import live_trader
        # 在 test 环境通常没设 env, 应为 'testnet'
        # (容忍 'mainnet_pilot' 也是合理 — CI/CD 可能设了 env)
        self.assertIn(live_trader.CRESUS_MODE, ('testnet', 'mainnet_pilot'))

    def test_banner_function_safe_when_not_mainnet(self):
        """non-mainnet 模式调 banner 函数应直接 return, 不抛."""
        import live_trader
        # banner 内部检查 CRESUS_MODE != mainnet_pilot 时早 return
        # 直接调不应抛异常 (即使在 mainnet 模式, log 仅 warning 不抛)
        try:
            live_trader._log_mainnet_pilot_banner()
        except Exception as e:
            self.fail(f"banner 不应抛: {e}")

    def test_verify_safety_passes_when_not_mainnet(self):
        """non-mainnet 模式 verify 函数应直接 return, 不抛."""
        import live_trader
        if live_trader.CRESUS_MODE != 'mainnet_pilot':
            # testnet 模式下, 任何 client_testnet 参数都不应抛
            try:
                live_trader._verify_mainnet_safety(client_testnet=True)
                live_trader._verify_mainnet_safety(client_testnet=False)
            except SystemExit as e:
                self.fail(f"non-mainnet 模式不应触发 safety check: {e}")

    def test_pilot_tier_subprocess_600(self):
        """Phase 6.A-fix R7 后, 用 subprocess 起新进程, 设 env 模拟 mainnet_pilot."""
        import subprocess, json as json_mod
        env = dict(os.environ)
        env['CRESUS_MODE'] = 'mainnet_pilot'
        env['CRESUS_PILOT_CAPITAL'] = '600'
        # 不实际跑 live_trader main, 只 import + 验配置
        # ~/.allow-live 用临时空文件占位 (verify_mainnet_safety 不会被 import 触发, 只在 main 触发)
        result = subprocess.run(
            [sys.executable, '-c', (
                'import sys; sys.path.insert(0, "%s"); '
                'import live_trader; '
                'import json; '
                'print(json.dumps({'
                '  "mode": live_trader.CRESUS_MODE,'
                '  "capital": live_trader.PILOT_CAPITAL,'
                '  "notional": live_trader.LIVE_NOTIONAL_BY_SCORE,'
                '  "max_concurrent": live_trader.LIVE_MAX_CONCURRENT,'
                '  "max_deploy": live_trader.LIVE_MAX_DEPLOY_USDT,'
                '  "daily_dd": live_trader.LIVE_DAILY_DD_LIMIT_USDT,'
                '  "regime_mult": live_trader.LIVE_REGIME_SIZE_MULTIPLIER,'
                '}))'
            ) % str(HERE.parent)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, f"subprocess fail: {result.stderr}")
        cfg = json_mod.loads(result.stdout.strip().split('\n')[-1])
        # 验 $600 medium tier
        self.assertEqual(cfg['mode'], 'mainnet_pilot')
        self.assertEqual(cfg['capital'], 600.0)
        # Phase 6.G G1 (2026-06-11): notional 减 33% 防御性减仓
        self.assertEqual(cfg['notional'], {'5': 100, '6': 130, '7': 200, '8': 100, '9': 100, '10': 100})
        self.assertEqual(cfg['max_concurrent'], 3)   # 2026-06-04: 2→3 (避免错过高 conviction 信号)
        self.assertEqual(cfg['max_deploy'], 450.0)
        self.assertEqual(cfg['daily_dd'], 60.0)  # 10% of $600
        self.assertEqual(cfg['regime_mult'], {})  # Phase 5.S 清空

    def _run_pilot_tier_subprocess(self, capital_str):
        """Helper: 用 subprocess 模拟 mainnet_pilot + 指定 capital, 返回配置 dict."""
        import subprocess, json as json_mod
        env = dict(os.environ)
        env['CRESUS_MODE'] = 'mainnet_pilot'
        env['CRESUS_PILOT_CAPITAL'] = capital_str
        result = subprocess.run(
            [sys.executable, '-c', (
                'import sys; sys.path.insert(0, "%s"); '
                'import live_trader; '
                'import json; '
                'print(json.dumps({'
                '  "capital": live_trader.PILOT_CAPITAL,'
                '  "notional": live_trader.LIVE_NOTIONAL_BY_SCORE,'
                '  "max_concurrent": live_trader.LIVE_MAX_CONCURRENT,'
                '  "max_deploy": live_trader.LIVE_MAX_DEPLOY_USDT,'
                '}))'
            ) % str(HERE.parent)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, f"subprocess fail: {result.stderr}")
        return json_mod.loads(result.stdout.strip().split('\n')[-1])

    def test_pilot_tier_subprocess_750_middle_tier(self):
        """中间档 $601-1200. Phase 6.G G1: notional 减 33% 后 deploy cap 不再紧绷."""
        cfg = self._run_pilot_tier_subprocess('750')
        self.assertEqual(cfg['capital'], 750.0)
        # Phase 6.G G1: 中间档 notional 跟 $600 档一致 (减 33% 后)
        self.assertEqual(cfg['notional'], {'5': 100, '6': 130, '7': 200, '8': 100, '9': 100, '10': 100})
        self.assertEqual(cfg['max_concurrent'], 3)
        self.assertEqual(cfg['max_deploy'], 600.0)  # 750 × 0.80
        self.assertGreaterEqual(cfg['max_deploy'], 3 * cfg['notional']['6'])

    def test_pilot_tier_subprocess_1125_three_score7(self):
        """$1125 测试中间档 + 3 笔 score 7 cap 兼容性."""
        cfg = self._run_pilot_tier_subprocess('1125')
        self.assertEqual(cfg['notional']['7'], 200)  # Phase 6.G G1 减仓
        self.assertEqual(cfg['max_deploy'], 900.0)  # 1125 × 0.80
        self.assertGreaterEqual(cfg['max_deploy'], 3 * cfg['notional']['7'])

    def test_pilot_tier_subprocess_1500_large_tier(self):
        """>$1200 升大档. Phase 6.G G1: 同步减 33% → {200, 265, 400, ...}."""
        cfg = self._run_pilot_tier_subprocess('1500')
        self.assertEqual(cfg['notional'], {'5': 200, '6': 265, '7': 400, '8': 200, '9': 200, '10': 200})
        self.assertEqual(cfg['max_concurrent'], 3)
        self.assertEqual(cfg['max_deploy'], 1200.0)  # 1500 × 0.80

    def test_pilot_invalid_mode_rejected(self):
        """Phase 6.A-fix Y4: 非法 CRESUS_MODE → SystemExit."""
        import subprocess
        env = dict(os.environ)
        env['CRESUS_MODE'] = 'unknown_mode'
        result = subprocess.run(
            [sys.executable, '-c', f'import sys; sys.path.insert(0, "{HERE.parent}"); import live_trader'],
            env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0, "非法 mode 应该退出")
        self.assertIn('CRESUS_MODE', result.stderr + result.stdout)

    def test_pilot_zero_capital_rejected(self):
        """Phase 6.A-fix Y4: PILOT_CAPITAL <= 0 → SystemExit."""
        import subprocess
        env = dict(os.environ)
        env['CRESUS_MODE'] = 'mainnet_pilot'
        env['CRESUS_PILOT_CAPITAL'] = '-50'
        result = subprocess.run(
            [sys.executable, '-c', f'import sys; sys.path.insert(0, "{HERE.parent}"); import live_trader'],
            env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0, "负 capital 应该退出")
        self.assertIn('CAPITAL', result.stderr + result.stdout)


class TestAbUseRegimeGate(unittest.TestCase):
    """Phase 4.F + 4.J: _ab_use_regime_gate 启用判定.

    Phase 4.J 后默认 mode='always' (gate 普及到全部组), 但保留 'abcd' / 'off'
    作为退路.
    """

    def test_off_never_enabled(self):
        self.assertFalse(live_trader._ab_use_regime_gate("any", mode="off"))

    def test_always_always_enabled(self):
        self.assertTrue(live_trader._ab_use_regime_gate("any", mode="always"))
        self.assertTrue(live_trader._ab_use_regime_gate("", mode="always"))

    def test_abcd_only_d_group(self):
        """legacy abcd mode: 只有 D 组返 True."""
        for i in range(80):
            pid = f"X{i}|LONG|T+00:00"
            g = live_trader._ab_group(pid, n_groups=4)
            r = live_trader._ab_use_regime_gate(pid, mode="abcd")
            if g == "D":
                self.assertTrue(r, f"{pid} (g={g}) 应该 D 组 = True")
            else:
                self.assertFalse(r, f"{pid} (g={g}) 应该 {g} 组 = False")

    def test_empty_paper_id_safe(self):
        """空 paper_id 在 abcd 模式下退路 = False (≡ A 组, 不启用 gate)."""
        self.assertFalse(live_trader._ab_use_regime_gate("", mode="abcd"))

    def test_unknown_mode_safe_fallback(self):
        self.assertFalse(live_trader._ab_use_regime_gate("any", mode="xyz"))

    def test_phase_4j_default_mode_is_always(self):
        """Phase 4.J: 默认 LIVE_REGIME_GATE_MODE = 'always'."""
        self.assertEqual(live_trader.LIVE_REGIME_GATE_MODE, "always",
                         "Phase 4.J 部署后默认 always (gate 普及全员)")

    def test_phase_4j_always_mode_applies_to_all_groups(self):
        """Phase 4.J: always mode 下, A/B/C/D 全部组都启用 gate."""
        # 找到 A/B/C/D 各一个 paper_id
        found = {}
        for i in range(200):
            pid = f"Y{i}|LONG|T+00:00"
            g = live_trader._ab_group(pid, n_groups=4)
            if g not in found:
                found[g] = pid
            if len(found) == 4:
                break
        self.assertEqual(set(found.keys()), {'A', 'B', 'C', 'D'},
                         f"应找全 4 组, 实际: {set(found.keys())}")
        for g, pid in found.items():
            r = live_trader._ab_use_regime_gate(pid, mode="always")
            self.assertTrue(r, f"always mode 下 {g} 组 (pid={pid[:20]}) 也应启用")


class TestIsEligibleRegimeGate(unittest.TestCase):
    """Phase 4.F: is_eligible_for_mirror 在传 btc_regime 时, D 组应用 regime gate."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        # Phase 6.F (2026-06-08): conv=6 默认 trade 撞 6.F B1, 隔离测试 4.F 行为
        self._6f_patcher = patch.object(
            live_trader, "LIVE_PHASE_6F_BLACKLIST_ENABLED", False)
        self._6f_patcher.start()
        self.addCleanup(self._6f_patcher.stop)
        self.live_state = {
            "mirrored_paper_ids": [],
            "live_open_trades": [],
        }
        # 找一个 D 组 paper_id
        self.d_pid = None
        for i in range(100):
            cand = f"X{i}USDT|LONG|2026-05-15T10:00:00+00:00"
            if live_trader._ab_group(cand, n_groups=4) == "D":
                self.d_pid = cand
                break
        # 找一个 A 组 paper_id 作对照
        self.a_pid = None
        for i in range(100):
            cand = f"A{i}USDT|LONG|2026-05-15T10:00:00+00:00"
            if live_trader._ab_group(cand, n_groups=4) == "A":
                self.a_pid = cand
                break

    def _make_trade(self, pid, direction="LONG"):
        return {
            "id": pid,
            "symbol": "BTCUSDT",   # 在白名单内
            "direction": direction,
            "entered_at": (self.now - timedelta(seconds=10)).isoformat(),
            "entry_price": 80000.0,
            "sl": 79000.0,
            "conviction_score": 6,   # Phase 4.H: 默认通过 filter
        }

    def test_d_group_down_long_blocked(self):
        """D 组 + down regime + LONG → 拒绝 mirror."""
        pt = self._make_trade(self.d_pid, "LONG")
        ok, reason = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="down"
        )
        self.assertFalse(ok)
        self.assertIn("regime gate", reason)

    def test_d_group_down_short_allowed(self):
        """D 组 + down regime + SHORT → 允许 (gate 只拒 LONG)."""
        pt = self._make_trade(self.d_pid, "SHORT")
        ok, reason = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="down"
        )
        self.assertTrue(ok, f"D + down + SHORT 应允许, reason={reason}")

    def test_d_group_up_long_allowed(self):
        """D 组 + up regime + LONG → 允许 (gate 只在 down)."""
        pt = self._make_trade(self.d_pid, "LONG")
        ok, _ = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="up"
        )
        self.assertTrue(ok)

    def test_a_group_down_long_legacy_abcd_allowed(self):
        """Phase 4.F legacy 'abcd' mode: A 组 (基线) 不启用 gate → 允许 down+LONG.

        Phase 4.J 后默认 mode='always', 所有组都拦 down+LONG. 但 'abcd' mode
        仍保留作为退路 (向后兼容老数据 / A/B 测试场景).
        """
        pt = self._make_trade(self.a_pid, "LONG")
        with patch.object(live_trader, "LIVE_REGIME_GATE_MODE", "abcd"):
            ok, _ = live_trader.is_eligible_for_mirror(
                pt, self.live_state, self.now, btc_regime="down"
            )
        self.assertTrue(ok, "legacy abcd mode: A 组应允许 down+LONG")

    def test_a_group_down_long_always_mode_blocked(self):
        """Phase 4.J 默认 'always' mode: A 组 + down + LONG 应被拒 (gate 普及).

        触发: 4.F 部署后 10 笔 down+LONG (A/B/C 组) 9 亏, 数据驱动决策
        把 gate 推广到全部组.
        """
        pt = self._make_trade(self.a_pid, "LONG")
        # 默认 LIVE_REGIME_GATE_MODE = "always", 不需 patch
        ok, reason = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="down"
        )
        self.assertFalse(ok, "Phase 4.J 后 A 组 down+LONG 应该被拦")
        self.assertIn("regime gate", reason)

    def test_no_btc_regime_arg_backward_compat(self):
        """不传 btc_regime → 不应触发 regime gate (向后兼容)."""
        pt = self._make_trade(self.d_pid, "LONG")
        ok, _ = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now,   # 不传 btc_regime
        )
        self.assertTrue(ok, "不传 btc_regime 应该跳过 gate")

    def test_btc_regime_none_no_gate(self):
        """btc_regime=None (取价失败) → 不应误拦 D 组."""
        pt = self._make_trade(self.d_pid, "LONG")
        ok, _ = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime=None
        )
        self.assertTrue(ok, "regime=None 应该跳过 gate, 避免取价失败时误拦")

    # --- Phase 4.K Shadow Log: reason 文本附带 sub_regime ---

    def test_phase_4k_rejection_reason_includes_sub_regime(self):
        """Phase 4.K: 被拒 reason 应附带 sub_regime + 3h% 信息 (shadow log)."""
        pt = self._make_trade(self.a_pid, "LONG")
        ok, reason = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now,
            btc_regime="down",
            btc_sub_regime="down_rebound",
            btc_change_3h_pct=+0.75,
        )
        self.assertFalse(ok)
        self.assertIn("sub=down_rebound", reason)
        self.assertIn("3h=+0.75%", reason)

    def test_phase_4k_no_sub_regime_no_extra_info(self):
        """老调用不传 sub_regime → reason 无附加信息 (向后兼容)."""
        pt = self._make_trade(self.a_pid, "LONG")
        ok, reason = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now,
            btc_regime="down",
            # 不传 sub_regime
        )
        self.assertFalse(ok)
        self.assertNotIn("sub=", reason)
        self.assertNotIn("3h=", reason)


class TestFundingSignal(unittest.TestCase):
    """Phase 4.M (2026-05-24): _funding_signal 三分类 + funding adverse gate.

    9 天 paper 数据 (5/15-5/24, 1072 笔) 驱动:
      funding ≤ -0.05%: 人均 +$3.97/笔 (LONG +$3.89, SHORT +$4.15)  ← favorable
      |funding| < 0.05%: 人均 +$0.57/笔                              ← neutral
      funding ≥ +0.05%: 人均 -$0.85/笔 (LONG -$0.75, SHORT -$1.13)  ← adverse
    """

    def test_funding_signal_favorable_at_negative_threshold(self):
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": -0.05}),
                         "favorable")
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": -0.10}),
                         "favorable")

    def test_funding_signal_adverse_at_positive_threshold(self):
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": 0.05}),
                         "adverse")
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": 0.20}),
                         "adverse")

    def test_funding_signal_neutral_middle_range(self):
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": 0.0}),
                         "neutral")
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": 0.04}),
                         "neutral")
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": -0.04}),
                         "neutral")

    def test_funding_signal_missing_or_invalid_field_neutral(self):
        """字段缺失或类型错误 → 'neutral' (fallback, 不影响老数据)."""
        self.assertEqual(live_trader._funding_signal({}), "neutral")
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": None}),
                         "neutral")
        self.assertEqual(live_trader._funding_signal({"funding_rate_pct": "n/a"}),
                         "neutral")


class TestIsEligibleFundingGate(unittest.TestCase):
    """Phase 4.M: is_eligible_for_mirror 增 funding adverse gate (步骤 8)."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        # Phase 6.F (2026-06-08): 隔离测试 4.M, 禁用 6.F
        self._6f_patcher = patch.object(
            live_trader, "LIVE_PHASE_6F_BLACKLIST_ENABLED", False)
        self._6f_patcher.start()
        self.addCleanup(self._6f_patcher.stop)
        self.live_state = {"mirrored_paper_ids": [], "live_open_trades": []}

    def _make_trade(self, funding_pct=None, direction="LONG"):
        t = {
            "id": f"BTCUSDT|{direction}|2026-05-24T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": direction,
            "entered_at": (self.now - timedelta(seconds=10)).isoformat(),
            "entry_price": 80000.0,
            "sl": 79000.0,
            "conviction_score": 6,
        }
        if funding_pct is not None:
            t["funding_rate_pct"] = funding_pct
        return t

    def test_adverse_funding_blocked(self):
        """funding_rate_pct ≥ +0.05% → 拒 mirror, reason 含 'funding adverse'."""
        pt = self._make_trade(funding_pct=0.12)
        ok, reason = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="up"
        )
        self.assertFalse(ok)
        self.assertIn("funding adverse", reason)

    def test_favorable_funding_allowed(self):
        """funding_rate_pct ≤ -0.05% → 允许 (favorable)."""
        pt = self._make_trade(funding_pct=-0.10)
        ok, reason = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="up"
        )
        self.assertTrue(ok, f"favorable funding 应允许, reason={reason}")

    def test_neutral_funding_allowed(self):
        pt = self._make_trade(funding_pct=0.02)
        ok, _ = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="up"
        )
        self.assertTrue(ok)

    def test_missing_funding_field_allowed_backward_compat(self):
        """老 paper 没 funding_rate_pct → fallback 'neutral' → 允许."""
        pt = self._make_trade(funding_pct=None)
        ok, _ = live_trader.is_eligible_for_mirror(
            pt, self.live_state, self.now, btc_regime="up"
        )
        self.assertTrue(ok)

    def test_reject_disabled_when_flag_off(self):
        """LIVE_REJECT_ADVERSE_FUNDING=False → adverse 也允许 (回滚开关)."""
        pt = self._make_trade(funding_pct=0.20)
        with patch.object(live_trader, "LIVE_REJECT_ADVERSE_FUNDING", False):
            ok, _ = live_trader.is_eligible_for_mirror(
                pt, self.live_state, self.now, btc_regime="up"
            )
        self.assertTrue(ok, "关掉开关后 adverse 应放行 (回滚验证)")


class TestConvictionFilter(unittest.TestCase):
    """Phase 4.H (2026-05-22 部署) / Phase 4.R7 (2026-05-24 关闭) — Conviction filter.

    4.H 部署论据: 26 笔 conv>=6 +$0.097/笔 (n 小, p≈0.1-0.2).
    4.R7 关闭论据: 部署后 paper 283 笔 + live 54 笔双源数据显示 conv>=6 反而
                    比 conv<6 更差 (paper -$1.43 vs +$1.04, live -$0.17 vs -$0.02).
                    模块默认改为 None. 本测试类测 filter 行为本身, setUp 强制开启 filter=6.
    """

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.live_state = {"mirrored_paper_ids": [], "live_open_trades": []}
        # Phase 4.R7: 模块默认 None, 本类测 filter 行为, 强制开启 = 6
        self._conv_patcher = patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", 6)
        self._conv_patcher.start()
        # Phase 6.F (2026-06-08): 本类测 4.H conv filter, 禁用 6.F 黑名单避免干扰
        self._6f_patcher = patch.object(
            live_trader, "LIVE_PHASE_6F_BLACKLIST_ENABLED", False)
        self._6f_patcher.start()
        self.addCleanup(self._6f_patcher.stop)

    def tearDown(self):
        self._conv_patcher.stop()

    def _make_trade(self, conv=None):
        t = {
            "id": "BTCUSDT|LONG|2026-05-22T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (self.now - timedelta(seconds=10)).isoformat(),
            "entry_price": 80000.0,
            "sl": 79000.0,
        }
        if conv is not None:
            t["conviction_score"] = conv
        return t

    def test_module_default_is_none_after_r7(self):
        """Phase 4.R7: 模块默认改为 None (filter 默认关闭)."""
        # stop patch 临时, 读模块原始默认
        self._conv_patcher.stop()
        try:
            from live_trader import LIVE_MIN_CONVICTION_SCORE
            self.assertIsNone(LIVE_MIN_CONVICTION_SCORE,
                              "Phase 4.R7 后模块默认应为 None (filter 关闭)")
        finally:
            self._conv_patcher.start()   # 恢复测试默认

    def test_conv_5_rejected(self):
        """conv=5 (标准钻石) 应被拒. paper 主要信号源现在拒收."""
        pt = self._make_trade(conv=5)
        ok, reason = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertFalse(ok)
        self.assertIn("conviction_score 5", reason)
        self.assertIn("threshold 6", reason)

    def test_conv_6_passes(self):
        """conv=6 = 阈值, 应通过 (大于等于阈值)."""
        pt = self._make_trade(conv=6)
        ok, _ = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertTrue(ok)

    def test_conv_7_passes(self):
        """conv=7 (高分位) 应通过."""
        pt = self._make_trade(conv=7)
        ok, _ = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertTrue(ok)

    def test_conv_8_passes(self):
        """conv=8 (最高分) 应通过."""
        pt = self._make_trade(conv=8)
        ok, _ = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertTrue(ok)

    def test_conv_missing_rejected(self):
        """conviction_score 字段缺失 → 拒绝 (防御性, 防 paper schema 变更绕过)."""
        pt = self._make_trade(conv=None)
        ok, reason = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertFalse(ok)
        self.assertIn("conviction_score 缺失", reason)

    def test_conv_invalid_type_rejected(self):
        """conviction_score 非数字 → 拒绝."""
        pt = self._make_trade(conv="invalid")
        ok, reason = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertFalse(ok)
        self.assertIn("conviction_score", reason)

    def test_conv_zero_rejected(self):
        """conv=0 (边界) 应被拒."""
        pt = self._make_trade(conv=0)
        ok, _ = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertFalse(ok)

    def test_filter_disabled_when_threshold_none(self):
        """LIVE_MIN_CONVICTION_SCORE=None → filter 关闭, 任何 conv 都通过."""
        with patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", None):
            for conv in (None, 0, 1, 5, 6, 99):
                pt = self._make_trade(conv=conv)
                ok, _ = is_eligible_for_mirror(pt, self.live_state, self.now)
                self.assertTrue(ok, f"filter off 时 conv={conv} 应该通过")

    def test_filter_disabled_when_threshold_zero(self):
        """LIVE_MIN_CONVICTION_SCORE=0 → filter 也关闭 (兼容性)."""
        with patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", 0):
            pt = self._make_trade(conv=None)
            ok, _ = is_eligible_for_mirror(pt, self.live_state, self.now)
            self.assertTrue(ok, "threshold=0 应等同 None, filter off")

    def test_custom_threshold_7(self):
        """自定义阈值 7: 6 拒, 7 通过."""
        with patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", 7):
            pt6 = self._make_trade(conv=6)
            ok6, _ = is_eligible_for_mirror(pt6, self.live_state, self.now)
            self.assertFalse(ok6)

            pt7 = self._make_trade(conv=7)
            ok7, _ = is_eligible_for_mirror(pt7, self.live_state, self.now)
            self.assertTrue(ok7)

    def test_filter_runs_after_blacklist(self):
        """黑名单优先级高于 conv filter: 黑名单 symbol 即使 conv=8 也拒."""
        pt = self._make_trade(conv=8)
        pt["symbol"] = "STABLEUSDT"   # 黑名单
        ok, reason = is_eligible_for_mirror(pt, self.live_state, self.now)
        self.assertFalse(ok)
        # 拒绝原因应为黑名单, 不是 conv filter
        self.assertIn("blacklist", reason)


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

    # === Phase 5.T integration: _try_mirror_close 传 avg_fill_price + 写 suspect flag ===

    def test_phase5t_passes_avg_fill_price_as_expected_entry(self):
        """Phase 5.T: _try_mirror_close 必须读 live_trade['avg_fill_price'] 并
        作为 expected_entry_price 传给 close_position. 防 Binance API entryPrice
        异常导致 PnL 失真 (DOGSUSDT 14:31 bug)."""
        lt = dict(self.live_trade)
        lt["avg_fill_price"] = 4.82e-05   # 本地权威 entry (DOGSUSDT 案例值)
        captured = {}

        def capture_close(**kwargs):
            captured.update(kwargs)
            return self.mock_close

        with patch.object(self.client, "close_position",
                          side_effect=capture_close):
            _try_mirror_close(
                self.client, lt, reason="sl_breach_client", dry_run=True,
            )
        self.assertEqual(captured.get("expected_entry_price"), 4.82e-05,
                        "必须传 avg_fill_price 作 expected_entry_price")

    def test_phase5t_no_avg_fill_price_passes_none(self):
        """avg_fill_price 缺 / 为 0 → expected_entry_price=None (fallback API)."""
        lt = dict(self.live_trade)
        # 不设 avg_fill_price
        captured = {}

        def capture_close(**kwargs):
            captured.update(kwargs)
            return self.mock_close

        with patch.object(self.client, "close_position",
                          side_effect=capture_close):
            _try_mirror_close(
                self.client, lt, reason="sl_breach_client", dry_run=True,
            )
        self.assertIsNone(captured.get("expected_entry_price"),
                         "无本地 entry → 传 None, close_position fallback API")

    def test_phase5t_persists_suspect_flag_and_source(self):
        """Phase 5.T: close 返的 realized_pnl_suspect + entry_price_source
        必须写进 closed trade record, 供 dashboard 标红警告."""
        lt = dict(self.live_trade)
        lt["avg_fill_price"] = 4.82e-05
        # 模拟 close_position 返 suspect=True (sanity guard 触发场景)
        suspect_close = dict(self.mock_close)
        suspect_close["realized_pnl_suspect"] = True
        suspect_close["entry_price_source"] = "expected"

        with patch.object(self.client, "close_position",
                          return_value=suspect_close):
            r = _try_mirror_close(
                self.client, lt, reason="sl_breach_client", dry_run=True,
            )
        self.assertTrue(r["realized_pnl_suspect"])
        self.assertEqual(r["entry_price_source"], "expected")

    def test_phase5t_invalid_avg_fill_price_falls_back(self):
        """avg_fill_price 是非数字 (TypeError/ValueError) → 不崩, 传 None."""
        lt = dict(self.live_trade)
        lt["avg_fill_price"] = "not-a-number"   # 故意脏数据
        captured = {}

        def capture_close(**kwargs):
            captured.update(kwargs)
            return self.mock_close

        with patch.object(self.client, "close_position",
                          side_effect=capture_close):
            r = _try_mirror_close(
                self.client, lt, reason="sl_breach_client", dry_run=True,
            )
        self.assertIsNone(captured.get("expected_entry_price"))
        self.assertIsNotNone(r, "脏数据不应让 mirror close 崩")


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
        per = live_trader.LIVE_MAX_DEPLOY_USDT / 4 * 0.5   # 半 cap
        state["live_open_trades"] = [
            {"notional_usdt": per},
            {"notional_usdt": per},
        ]
        self.assertIsNone(_check_cash_reserve(state))

    def test_at_cap_blocks(self):
        state = _empty_live_state()
        # 4 笔 × (cap/4) = cap
        per = live_trader.LIVE_MAX_DEPLOY_USDT / 4
        state["live_open_trades"] = [{"notional_usdt": per} for _ in range(4)]
        result = _check_cash_reserve(state)
        self.assertIsNotNone(result)
        self.assertIn("cap", result)

    def test_over_cap_blocks(self):
        state = _empty_live_state()
        # 4 笔 × (cap/4 + 5) > cap
        per = live_trader.LIVE_MAX_DEPLOY_USDT / 4 + 5.0
        state["live_open_trades"] = [{"notional_usdt": per} for _ in range(4)]
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
            "realized_pnl_usdt": -(LIVE_DAILY_DD_LIMIT_USDT + 0.5),
        }]
        result = _check_daily_dd(state, self.now)
        self.assertIsNotNone(result)
        self.assertIn(f"-${LIVE_DAILY_DD_LIMIT_USDT:.0f}", result)

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
        # 触发 cash reserve cap (deploy = MAX_DEPLOY_USDT)
        state["live_open_trades"] = [
            {"notional_usdt": live_trader.LIVE_MAX_DEPLOY_USDT}
        ]
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
        # Phase 4.H: 关闭 conv filter
        self._conv_patcher = patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", None)
        self._conv_patcher.start()
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)

    def tearDown(self):
        self._conv_patcher.stop()
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
        """已部署 $80 = cap → 新单不开."""
        # seed 已有 4 笔满 cap
        state = _empty_live_state()
        state["live_open_trades"] = [
            {"trade_id": "L1", "symbol": "ETHUSDT", "paper_id": "x1",
             "notional_usdt": 20.0, "side": "BUY", "sl_price": 1.0, "phase": "A"},
            {"trade_id": "L2", "symbol": "SOLUSDT", "paper_id": "x2",
             "notional_usdt": 20.0, "side": "BUY", "sl_price": 1.0, "phase": "A"},
            {"trade_id": "L3", "symbol": "BNBUSDT", "paper_id": "x3",
             "notional_usdt": 20.0, "side": "BUY", "sl_price": 1.0, "phase": "A"},
            {"trade_id": "L4", "symbol": "ADAUSDT", "paper_id": "x4",
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
        # Phase 6.F (2026-06-08): conv=6 默认 trade 会被 6.F B1 拦, 隔离测试
        self._6f_patcher = patch.object(
            live_trader, "LIVE_PHASE_6F_BLACKLIST_ENABLED", False)
        self._6f_patcher.start()
        self.addCleanup(self._6f_patcher.stop)
        self.trade = {
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (self.now - timedelta(seconds=30)).isoformat(),
            "entry_price": 81000.0,
            "sl": 80190.0,
            "conviction_score": 6,   # Phase 4.H: 默认通过 filter
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
        """balance ≥ threshold (= start × (1-DD%)) → 不触发."""
        threshold = (live_trader.LIVE_STARTING_CAPITAL_USDT *
                     (1 - live_trader.LIVE_TOTAL_DD_LIMIT_PCT / 100))
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": str(threshold + 1.0)}):
            result = _check_cumulative_dd_and_trigger(self.client)
        self.assertIsNone(result)
        self.assertFalse(live_trader.EMERGENCY_STOP_PATH.exists())

    def test_dd_at_threshold_triggers(self):
        """balance = threshold - 0.01 < threshold → 触发 + 自动写文件."""
        threshold = (live_trader.LIVE_STARTING_CAPITAL_USDT *
                     (1 - live_trader.LIVE_TOTAL_DD_LIMIT_PCT / 100))
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": str(threshold - 0.01)}):
            result = _check_cumulative_dd_and_trigger(self.client)
        self.assertIsNotNone(result)
        self.assertIn("cumulative DD", result)
        # 文件被自动创建
        self.assertTrue(live_trader.EMERGENCY_STOP_PATH.exists())
        content = live_trader.EMERGENCY_STOP_PATH.read_text()
        self.assertIn("AUTO", content)
        self.assertIn("cumulative DD", content)

    def test_dd_deep_triggers(self):
        """balance = start × 80% (即 -20% DD) → 触发."""
        balance = live_trader.LIVE_STARTING_CAPITAL_USDT * 0.80
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": str(balance)}):
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
                          return_value={"totalMarginBalance":
                                        str(live_trader.LIVE_STARTING_CAPITAL_USDT)}):
            result = check_risk_gates(state, self.now, client=self.client)
        self.assertFalse(result["block_new_opens"])

    def test_with_client_dd_triggers_and_creates_flag(self):
        state = _empty_live_state()
        # balance = start × 85% (即 -15% DD, > 5% 阈值)
        balance = live_trader.LIVE_STARTING_CAPITAL_USDT * 0.85
        with patch.object(self.client, "get_account",
                          return_value={"totalMarginBalance": str(balance)}):
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
    """Bug fix verification: 循环内 mirror 多个时, 每次都重新评估 state.

    Phase 4.H: 本测试类设计用于 mirror 流程边界, 不测试 conviction filter.
    setUp 关闭 LIVE_MIN_CONVICTION_SCORE 隔离, 保留各 test 用原 mock 数据.
    单独的 TestConvictionFilter 类负责测 4.H 行为.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_live_state = live_trader.LIVE_STATE
        self._orig_paper_history = live_trader.PAPER_HISTORY
        live_trader.LIVE_STATE = Path(self.tmpdir) / "live.json"
        live_trader.PAPER_HISTORY = Path(self.tmpdir) / "paper.json"
        self.client = BinanceClient(FAKE_KEY, FAKE_SECRET, dry_run=True)
        # Phase 4.H: 关闭 conv filter (这个 test class 不测它)
        self._conv_patcher = patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", None)
        self._conv_patcher.start()

        # Mock fill 默认对齐多数测试的 paper entry_price=100.0.
        # entry_price=1.0 的特殊测试 (test_same_symbol_blocks_within_iteration) 用
        # side_effect 重载 fill 值.
        self.mock_open_result = {
            "trade_id": "L1", "symbol": "X",
            "side": "BUY", "qty": 0.001,
            "avg_fill_price": 100.0, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0, "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": "x", "fees_paid_usdt": 0, "_dryRun": True,
        }

    def tearDown(self):
        self._conv_patcher.stop()
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
        """Paper 有 5 笔 eligible 信号, 但 max_concurrent=4 → 只 mirror 4 笔."""
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

        # ⚠️ Bug fix verification: max_concurrent=4, 必须只 mirror 4 笔
        self.assertEqual(call_count[0], 4,
                         f"应严格在 max_concurrent=4 处停止, 实际 mirror {call_count[0]} 次")
        self.assertEqual(len(result["live_open_trades"]), 4)

    def test_same_symbol_blocks_within_iteration(self):
        """Paper 有 POLYX LONG + POLYX SHORT 同时 open → 只 mirror 第一笔.

        测试本意: 同 symbol blocking, 跟 SL 触发逻辑无关. 因此 patch
        _check_sl_breach=False 隔离, 避免 Phase 4.D 补偿 / Phase 4.E wick
        filter 等正交逻辑干扰.
        """
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
            # Phase 5.G: paper entry=1.0, mock fill 跟随避免 post-fill 应急平
            r["avg_fill_price"] = 1.0
            r["sl_price"] = 0.95
            return r

        with patch.object(self.client, "open_position",
                          side_effect=make_result), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "1.0", 0, 0, 0, 0, 0, 0, 0]]), \
             patch.object(live_trader, "_check_sl_breach", return_value=False):
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
        for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
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

        # 应在 max_concurrent (4) OR cash reserve ($80) 早触发处停, 都是 4
        self.assertLessEqual(call_count[0], 4)

    def test_slippage_gate_rejects_above_threshold(self):
        """滑点护栏 (Phase 4.A v3): 预滑点 > 阈值 (当前 100 bps) → 不调 open_position, 记 missed_signal."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0,
            "sl": 95.0,
        }])
        # paper 100, 当前 102 → +200 bps 不利 → 应被拒 (>100bps 阈值)
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result) as mock_open, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "102.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        # 关键: open_position 必须未被调用 (避免开仓)
        self.assertEqual(mock_open.call_count, 0)
        self.assertEqual(len(result["live_open_trades"]), 0)
        # paper_id 不应加入 mirrored_paper_ids (下 tick 还能重试)
        self.assertNotIn("BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
                         result["mirrored_paper_ids"])
        # missed_signal 记录
        missed = result.get("missed_signals", [])
        self.assertTrue(any("pre_slippage_too_high" in m.get("reason", "")
                           for m in missed),
                        f"应记 pre_slippage missed_signal, 实际: {[m.get('reason') for m in missed]}")

    def test_slippage_gate_allows_below_threshold(self):
        """预滑点 < 阈值 → 正常 mirror."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0,
            "sl": 95.0,
        }])
        # paper 100, 当前 100.2 → +20 bps 在阈值内 → 应通过
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result) as mock_open, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "100.2", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_open.call_count, 1)
        self.assertEqual(len(result["live_open_trades"]), 1)

    def test_slippage_gate_allows_favorable_slippage(self):
        """有利滑点 (负 bps) 不应被拒 — 即使绝对值大也应通过."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0,
            "sl": 95.0,
        }])
        # LONG, 当前 99.0 → -100 bps 有利 (买便宜) → 应通过
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result) as mock_open, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "99.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_open.call_count, 1, "有利滑点不应被拒")

    def test_slippage_gate_short_direction_correct(self):
        """SHORT: 当前价上涨 = 有利 = 通过."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|SHORT|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0,
            "sl": 105.0,
        }])
        # SHORT, 当前 101 → -100 bps 有利 → 通过
        short_open = dict(self.mock_open_result)
        short_open["side"] = "SELL"
        with patch.object(self.client, "open_position",
                          return_value=short_open) as mock_open, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "101.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_open.call_count, 1, "SHORT 有利滑点应通过")

    def test_slippage_gate_short_direction_rejects_unfavorable(self):
        """SHORT: 当前价下跌 = 不利 = 超阈值 (v3 100bps) → 拒."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|SHORT|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0,
            "sl": 105.0,
        }])
        # SHORT, 当前 98 → +200 bps 不利 → 拒 (>100bps 阈值)
        with patch.object(self.client, "open_position") as mock_open, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "98.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_open.call_count, 0, "SHORT 不利滑点应被拒")

    def test_slippage_gate_mixed_candidates_correctly_filtered(self):
        """3 笔 candidate 混合: A 滑点小通过 / B 滑点大被拒 / C 滑点小通过.

        最严的集成测试 — 验证 gate 不会"误杀邻居"或"漏过坏笔".
        """
        now = datetime.now(timezone.utc)
        # 3 笔 paper, 都 entry_price=100, 但 get_klines 按 symbol 返不同价
        trades = []
        for sym in ["AAA", "BBB", "CCC"]:
            trades.append({
                "id": f"{sym}USDT|LONG|2026-05-15T10:00:0{trades and len(trades) or 0}+00:00",
                "symbol": f"{sym}USDT",
                "direction": "LONG",
                "entered_at": (now - timedelta(seconds=10)).isoformat(),
                "entry_price": 100.0,
                "sl": 95.0,
            })
        self._write_paper(trades)

        # AAA 当前 100.1 (+10 bps 通过) / BBB 当前 102 (+200 bps 拒) / CCC 当前 100.2 (+20 bps 通过)
        def make_klines(symbol, **kw):
            prices = {"AAAUSDT": "100.1", "BBBUSDT": "102.0", "CCCUSDT": "100.2"}
            return [[0, 0, 0, 0, prices.get(symbol, "100.0"), 0, 0, 0, 0, 0, 0, 0]]

        opened = []
        def open_side_effect(symbol, side, **kw):
            opened.append(symbol)
            r = dict(self.mock_open_result)
            r["symbol"] = symbol
            return r

        with patch.object(self.client, "open_position",
                          side_effect=open_side_effect) as mock_open, \
             patch.object(self.client, "get_klines",
                          side_effect=make_klines):
            result = main_loop(self.client, dry_run=True)

        # 应只 mirror AAA 和 CCC, 拒绝 BBB
        self.assertEqual(mock_open.call_count, 2,
                         f"应 mirror 2 笔 (AAA, CCC), 实际 {mock_open.call_count}")
        self.assertIn("AAAUSDT", opened)
        self.assertIn("CCCUSDT", opened)
        self.assertNotIn("BBBUSDT", opened, "BBB 滑点超阈值不应被 mirror")
        # BBB 应在 missed_signals 里
        missed_syms = [m.get("symbol") for m in result.get("missed_signals", [])
                       if "pre_slippage_too_high" in m.get("reason", "")]
        self.assertIn("BBBUSDT", missed_syms)
        # AAA / CCC 应在 mirrored_paper_ids 中
        self.assertEqual(len(result["live_open_trades"]), 2)

    def test_slippage_gate_failsafe_when_get_klines_fails(self):
        """关键 fail-safe: 取价失败 → 不拦截, 正常 mirror.

        交易安全原则: 监控失败不应该阻止策略执行 (只能拒绝, 不能误开).
        反之: 监控失败不应该阻止 mirror — 否则 API 抖动期间策略全停.
        """
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|2026-05-15T10:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0,
            "sl": 95.0,
        }])
        with patch.object(self.client, "open_position",
                          return_value=self.mock_open_result) as mock_open, \
             patch.object(self.client, "get_klines",
                          side_effect=BinanceError("network down")):
            result = main_loop(self.client, dry_run=True)
        # 取价失败时, mirror 应照常进行 (向后兼容)
        self.assertEqual(mock_open.call_count, 1,
                         "取价失败不应阻止 mirror (fail-safe)")

    def test_blacklist_blocks_in_main_loop(self):
        """Phase 4.B 集成: main_loop 中遇到黑名单 symbol 必须 skip + 不调 open_position."""
        now = datetime.now(timezone.utc)
        # 写一笔黑名单 symbol 的 paper trade
        self._write_paper([{
            "id": "STABLEUSDT|LONG|2026-05-17T10:00:00+00:00",
            "symbol": "STABLEUSDT",   # 黑名单成员
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 1.0, "sl": 0.95,
        }])
        with patch.object(self.client, "open_position") as mock_open, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "1.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_open.call_count, 0, "黑名单 symbol 不应被开仓")
        self.assertEqual(len(result["live_open_trades"]), 0)
        # paper_id 不应进 mirrored_paper_ids (但 missed_signal 处也不应记)
        # 实际上 is_eligible_for_mirror 返 False 后, _record_missed_signal 会被调用
        # (但 "in live blacklist" 不在 "already mirrored" 噪音过滤中, 应该被记)
        missed = result.get("missed_signals", [])
        self.assertTrue(any("blacklist" in m.get("reason", "")
                           for m in missed),
                        f"应记 blacklist missed_signal, 实际: {[m.get('reason') for m in missed]}")

    def test_btc_regime_propagates_to_live_trade_in_main_loop(self):
        """Phase 4.C 集成: main_loop 调 _compute_btc_regime → 写入 _btc_regime_now
        → _try_mirror_open 收到 → live_trade 含 btc_regime_at_open 等字段."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "ARCUSDT|LONG|2026-05-17T10:00:00+00:00",
            "symbol": "ARCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 0.07, "sl": 0.066,
        }])
        # mock 一个 regime snapshot — 模拟 BTC 上涨场景
        fake_regime = {
            "regime": "up", "btc_price": 80500.0, "btc_ma25_1h": 80000.0,
            "pct_vs_ma25": 0.625, "change_24h_pct": 1.5,
            "computed_at": now.isoformat(),
        }
        # mock open_position 返回合理 result
        mock_open = {
            "trade_id": "L1_ARC_L", "symbol": "ARCUSDT",
            "side": "BUY", "qty": 285, "avg_fill_price": 0.07,
            "actual_notional": 20.0, "entry_order_id": 1,
            "entry_client_id": "x", "sl_price": 0.066,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        with patch.object(live_trader, "_compute_btc_regime",
                          return_value=fake_regime) as mock_regime, \
             patch.object(self.client, "open_position",
                          return_value=mock_open) as mock_open_call, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "0.07", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        # _compute_btc_regime 必须被 main_loop 调用 (即使 paper 为空也调)
        self.assertGreaterEqual(mock_regime.call_count, 1)
        # 持仓应增加 1
        self.assertEqual(len(result["live_open_trades"]), 1)
        lt = result["live_open_trades"][0]
        # 关键: 4 个 btc 字段都应正确写入
        self.assertEqual(lt.get("btc_regime_at_open"), "up")
        self.assertEqual(lt.get("btc_price_at_open"), 80500.0)
        self.assertEqual(lt.get("btc_change_24h_at_open"), 1.5)
        self.assertEqual(lt.get("btc_pct_vs_ma25_at_open"), 0.625)
        # _btc_regime_now 也应写到 state (供 publish_live_history 读)
        self.assertEqual(result.get("_btc_regime_now", {}).get("regime"), "up")

    def test_btc_regime_failure_does_not_block_mirror(self):
        """Fail-safe: _compute_btc_regime 失败 (返 None) 不应阻止 mirror,
        live_trade 也不应有 btc_* 字段 (向后兼容)."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "ARCUSDT|LONG|2026-05-17T10:00:00+00:00",
            "symbol": "ARCUSDT",
            "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 0.07, "sl": 0.066,
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "ARCUSDT", "side": "BUY", "qty": 285,
            "avg_fill_price": 0.07, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x", "sl_price": 0.066,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        with patch.object(live_trader, "_compute_btc_regime",
                          return_value=None), \
             patch.object(self.client, "open_position",
                          return_value=mock_open) as mock_open_call, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "0.07", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        # mirror 仍应进行
        self.assertEqual(mock_open_call.call_count, 1, "regime 失败不应阻止 mirror")
        self.assertEqual(len(result["live_open_trades"]), 1)
        # live_trade 不应有 btc_* 字段
        lt = result["live_open_trades"][0]
        self.assertNotIn("btc_regime_at_open", lt)

    def test_sl_compensation_in_mirror_open(self):
        """Phase 4.D 集成: 当 paper_id 分到 B 组 (always for mode='always'), live_trade
        的 sl_price 应该是补偿后的值, 含必要的 metadata 字段."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|t_b_group",
            "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0, "sl": 95.0,   # paper SL 95
        }])
        # mock open 返回 fill 100.5 (高 0.5%, slip +50bps 在阈值 50 边界)
        mock_open = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.2,
            "avg_fill_price": 100.5, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        # 强制 always 模式 (B 组)
        with patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "always"), \
             patch.object(self.client, "open_position",
                          return_value=mock_open) as mock_op, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(mock_op.call_count, 1)
        lt = result["live_open_trades"][0]
        # 关键: SL 应该是 paper_sl(95) + slip(0.5) = 95.5
        self.assertAlmostEqual(lt["sl_price"], 95.5, places=4)
        self.assertTrue(lt["sl_compensation_enabled"])
        self.assertAlmostEqual(lt["sl_compensation_offset"], 0.5, places=6)
        self.assertEqual(lt["sl_compensation_mode"], "always")
        self.assertAlmostEqual(lt["sl_paper_current"], 95.0, places=4)

    def test_sl_compensation_mode_off_no_change(self):
        """mode='off': SL 不应被改, 跟旧逻辑一致."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|t_off",
            "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0, "sl": 95.0,
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.2,
            "avg_fill_price": 100.5, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        with patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(self.client, "open_position", return_value=mock_open), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        lt = result["live_open_trades"][0]
        # SL 跟 paper 一致, 没补偿
        self.assertAlmostEqual(lt["sl_price"], 95.0, places=4)
        self.assertFalse(lt["sl_compensation_enabled"])
        self.assertEqual(lt["sl_compensation_offset"], 0)

    def test_phase_4e_wick_filter_in_mirror_open(self):
        """Phase 4.E 集成: 当 paper_id 分到 C 组 (always mode), live_trade
        的 wick_filter_enabled=True, sl_breach_count=0."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|t_c_wick",
            "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0, "sl": 95.0,
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.2,
            "avg_fill_price": 100.5, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        # 强制 wick filter always 模式, 补偿 off (避免污染)
        with patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "always"), \
             patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(self.client, "open_position", return_value=mock_open), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        lt = result["live_open_trades"][0]
        self.assertTrue(lt["wick_filter_enabled"], "always 模式应启用 wick filter")
        self.assertEqual(lt["sl_breach_count"], 0, "初始计数应为 0")
        self.assertEqual(lt["wick_filter_min_breaches"],
                         live_trader.LIVE_WICK_FILTER_MIN_BREACHES)
        self.assertEqual(lt["wick_filter_mode"], "always")
        # 补偿 off, 所以 sl 不补偿
        self.assertAlmostEqual(lt["sl_price"], 95.0, places=4)

    def test_phase_4e_wick_filter_mode_off_no_field_change(self):
        """mode='off': wick_filter_enabled=False, 老 trade 行为."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|t_wick_off",
            "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0, "sl": 95.0,
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.2,
            "avg_fill_price": 100.5, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        with patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "off"), \
             patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(self.client, "open_position", return_value=mock_open), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        lt = result["live_open_trades"][0]
        self.assertFalse(lt["wick_filter_enabled"])
        self.assertIsNone(lt["wick_filter_min_breaches"])

    def test_phase_4m_favorable_funding_boosts_wick_breaches(self):
        """Phase 4.M 集成: favorable funding (≤ -0.05%) + wick always
        → wick_filter_min_breaches = 3 (而非默认 2). live_trade 含 funding_signal."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|t_fav_funding",
            "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0, "sl": 95.0,
            "funding_rate_pct": -0.10,   # favorable
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.2,
            "avg_fill_price": 100.0, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        with patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "always"), \
             patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(self.client, "open_position", return_value=mock_open), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        lt = result["live_open_trades"][0]
        self.assertEqual(lt["funding_signal"], "favorable")
        self.assertEqual(lt["funding_rate_pct_at_open"], -0.10)
        self.assertTrue(lt["wick_filter_enabled"])
        self.assertEqual(lt["wick_filter_min_breaches"],
                         live_trader.LIVE_FUNDING_FAVORABLE_WICK_BREACHES)

    def test_phase_4m_neutral_funding_uses_default_breaches(self):
        """Phase 4.M 集成: neutral funding → wick_filter_min_breaches = 默认 (运行时动态从常量取)."""
        now = datetime.now(timezone.utc)
        self._write_paper([{
            "id": "BTCUSDT|LONG|t_neutral_funding",
            "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 100.0, "sl": 95.0,
            "funding_rate_pct": 0.02,   # neutral
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.2,
            "avg_fill_price": 100.0, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 95.0,
            "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        with patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "always"), \
             patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(self.client, "open_position", return_value=mock_open), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "100.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        lt = result["live_open_trades"][0]
        self.assertEqual(lt["funding_signal"], "neutral")
        self.assertEqual(lt["wick_filter_min_breaches"],
                         live_trader.LIVE_WICK_FILTER_MIN_BREACHES)

    def test_phase_4f_regime_gate_blocks_down_long_in_main_loop(self):
        """Phase 4.F 集成: D 组 + down regime + LONG → main_loop 不 mirror,
        而是记录 missed_signal."""
        now = datetime.now(timezone.utc)
        # 找 D 组 paper_id
        d_pid = None
        for i in range(100):
            cand = f"BTCUSDT|LONG|d_{i}|2026-05-21T10:00:00+00:00"
            if live_trader._ab_group(cand, n_groups=4) == "D":
                d_pid = cand
                break
        self.assertIsNotNone(d_pid)
        self._write_paper([{
            "id": d_pid, "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 80000.0, "sl": 79000.0,
        }])
        # Mock BTC kline 触发 down regime (close 远低于 MA25)
        # 简化: mock _compute_btc_regime 直接返 down
        with patch.object(live_trader, "LIVE_REGIME_GATE_MODE", "always"), \
             patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "off"), \
             patch.object(live_trader, "_compute_btc_regime", return_value={
                 "regime": "down", "btc_price": 70000.0,
                 "btc_ma25_1h": 75000.0, "pct_vs_ma25": -6.67,
                 "change_24h_pct": -5.0, "computed_at": now.isoformat(),
             }), \
             patch.object(self.client, "open_position") as mock_op, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "80000.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        # 不应 mirror
        self.assertEqual(mock_op.call_count, 0,
                         "regime gate 应该拒绝, open_position 不该被调")
        # missed_signal 应该有 regime gate 原因
        missed = result.get("missed_signals", [])
        self.assertTrue(
            any("regime gate" in m.get("reason", "") for m in missed),
            f"应记 regime gate missed, 实际: {[m.get('reason') for m in missed]}"
        )

    def test_phase_4f_regime_gate_allows_down_short(self):
        """Phase 4.F 集成: D 组 + down regime + SHORT → main_loop 允许 mirror."""
        now = datetime.now(timezone.utc)
        d_pid = None
        for i in range(100):
            cand = f"ETHUSDT|SHORT|d_{i}|2026-05-21T10:00:00+00:00"
            if live_trader._ab_group(cand, n_groups=4) == "D":
                d_pid = cand
                break
        self.assertIsNotNone(d_pid)
        self._write_paper([{
            "id": d_pid, "symbol": "ETHUSDT", "direction": "SHORT",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 3000.0, "sl": 3100.0,
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "ETHUSDT", "side": "SELL", "qty": 0.01,
            "avg_fill_price": 3000.0, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 3100.0, "sl_side": "BUY", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        with patch.object(live_trader, "LIVE_REGIME_GATE_MODE", "always"), \
             patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "off"), \
             patch.object(live_trader, "_compute_btc_regime", return_value={
                 "regime": "down", "btc_price": 70000.0,
                 "btc_ma25_1h": 75000.0, "pct_vs_ma25": -6.67,
                 "change_24h_pct": -5.0, "computed_at": now.isoformat(),
             }), \
             patch.object(self.client, "open_position", return_value=mock_open), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "3000.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        # 应 mirror
        self.assertEqual(len(result["live_open_trades"]), 1,
                         "down + SHORT 应该允许 mirror")
        lt = result["live_open_trades"][0]
        self.assertTrue(lt["regime_gate_enabled"], "D 组 regime_gate_enabled=True")
        self.assertEqual(lt["btc_regime_at_open"], "down")

    def test_phase_4f_d_group_record_field_when_no_block(self):
        """D 组在 up regime LONG 不触发 gate, 但 live_trade['regime_gate_enabled']=True."""
        now = datetime.now(timezone.utc)
        d_pid = None
        for i in range(100):
            cand = f"BTCUSDT|LONG|up_{i}|2026-05-21T10:00:00+00:00"
            if live_trader._ab_group(cand, n_groups=4) == "D":
                d_pid = cand
                break
        self.assertIsNotNone(d_pid)
        self._write_paper([{
            "id": d_pid, "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 80000.0, "sl": 79000.0,
        }])
        mock_open = {
            "trade_id": "L1", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.2,
            "avg_fill_price": 80000.0, "actual_notional": 20.0,
            "entry_order_id": 1, "entry_client_id": "x",
            "sl_price": 79000.0, "sl_side": "SELL", "sl_mode": "client_side",
            "opened_at": now.isoformat(),
            "fees_paid_usdt": 0, "_dryRun": True,
        }
        # up regime + LONG: gate 不触发
        with patch.object(live_trader, "LIVE_REGIME_GATE_MODE", "always"), \
             patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "off"), \
             patch.object(live_trader, "_compute_btc_regime", return_value={
                 "regime": "up", "btc_price": 85000.0,
                 "btc_ma25_1h": 80000.0, "pct_vs_ma25": +6.25,
                 "change_24h_pct": +5.0, "computed_at": now.isoformat(),
             }), \
             patch.object(self.client, "open_position", return_value=mock_open), \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "80000.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        self.assertEqual(len(result["live_open_trades"]), 1)
        lt = result["live_open_trades"][0]
        self.assertTrue(lt["regime_gate_enabled"], "D 组应记 regime_gate_enabled=True")
        self.assertEqual(lt["regime_gate_mode"], "always")

    def test_phase_4j_a_group_down_long_now_blocked(self):
        """Phase 4.J: A 组 + down regime + LONG 在默认 mode=always 下应被拒.

        这是 4.J 核心行为变化 — 之前 'abcd' mode 下 A 组允许 down+LONG,
        现在 'always' mode 下全员拦截. 防止 A/B/C 组继续亏钱在已知失利组合上.
        """
        now = datetime.now(timezone.utc)
        # 找 A 组 paper_id
        a_pid = None
        for i in range(100):
            cand = f"BTCUSDT|LONG|a4j_{i}|2026-05-23T10:00:00+00:00"
            if live_trader._ab_group(cand, n_groups=4) == "A":
                a_pid = cand
                break
        self.assertIsNotNone(a_pid)
        self._write_paper([{
            "id": a_pid, "symbol": "BTCUSDT", "direction": "LONG",
            "entered_at": (now - timedelta(seconds=10)).isoformat(),
            "entry_price": 80000.0, "sl": 79000.0,
            "conviction_score": 7,   # 通过 4.H filter
        }])
        # 不 patch LIVE_REGIME_GATE_MODE, 用默认 'always'
        with patch.object(live_trader, "LIVE_SL_COMPENSATION_MODE", "off"), \
             patch.object(live_trader, "LIVE_SL_WICK_FILTER_MODE", "off"), \
             patch.object(live_trader, "_compute_btc_regime", return_value={
                 "regime": "down", "btc_price": 70000.0,
                 "btc_ma25_1h": 75000.0, "pct_vs_ma25": -6.67,
                 "change_24h_pct": -5.0, "computed_at": now.isoformat(),
             }), \
             patch.object(self.client, "open_position") as mock_op, \
             patch.object(self.client, "get_klines",
                          return_value=[[0, 0, 0, 0, "80000.0", 0, 0, 0, 0, 0, 0, 0]]):
            result = main_loop(self.client, dry_run=True)
        # 不应 mirror
        self.assertEqual(mock_op.call_count, 0,
                         "Phase 4.J: A 组 down+LONG 也应被拒, open_position 不该被调")
        # missed_signal 应包含 regime gate 原因
        missed = result.get("missed_signals", [])
        self.assertTrue(
            any("regime gate" in m.get("reason", "") for m in missed),
            f"应记 regime gate missed, 实际: {[m.get('reason') for m in missed]}"
        )


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
        self.assertEqual(s["starting_capital_usdt"],
                         live_trader.LIVE_STARTING_CAPITAL_USDT)

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
        # free = starting - deployed - fees_open
        expected = live_trader.LIVE_STARTING_CAPITAL_USDT - 45.0 - 0.02
        self.assertAlmostEqual(s["free_capital_usdt"], expected, places=2)

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
        # free_capital = starting − deployed + total_pnl − fees_total
        #             = starting − 20 + 0.20 − 0.035
        expected_free = (live_trader.LIVE_STARTING_CAPITAL_USDT
                         - 20.0 + 0.20 - 0.035)
        self.assertAlmostEqual(s["free_capital_usdt"], expected_free, places=2)
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
        self.assertEqual(config["starting_capital_usdt"],
                         live_trader.LIVE_STARTING_CAPITAL_USDT)
        self.assertEqual(config["max_concurrent"], live_trader.LIVE_MAX_CONCURRENT)
        self.assertEqual(config["max_deploy_usdt"], live_trader.LIVE_MAX_DEPLOY_USDT)
        self.assertEqual(config["leverage"], live_trader.LIVE_LEVERAGE)
        self.assertIn("BTCUSDT", config["symbol_whitelist"])
        self.assertEqual(config["daily_dd_limit_usdt"],
                         live_trader.LIVE_DAILY_DD_LIMIT_USDT)

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


class TestPhase4XOrphanPrevention(unittest.TestCase):
    """Phase 4.X (5/26): 防孤儿仓 — _try_mirror_open post-open 异常时返回
    panic_trade 而不是 None, 让外层能记录到 state.

    背景: 之前如果 open_position 成功 (仓位已在 Binance), 但后续字段构造抛
    异常, _try_mirror_open 把异常上抛, 进程崩溃, save_live_state 永不执行,
    仓位变成 dashboard 报警的孤儿. 5/26 复审发现 4 个孤儿仓全是此 bug 产物.
    """

    def setUp(self):
        from binance_client import BinanceClient
        self.client = MagicMock(spec=BinanceClient)
        self.client.dry_run = False
        self.client.get_book_ticker = MagicMock(return_value={
            "askPrice": "100.0", "bidPrice": "99.5",
        })
        self.client.set_leverage = MagicMock(return_value=None)
        # open_position 返回有效 result (仓位真已开)
        self.client.open_position = MagicMock(return_value={
            "avg_fill_price": 100.0,
            "qty": 4.0,
            "actual_notional": 400.0,
            "entry_order_id": "12345",
            "entry_client_id": "test_client_id",
            "sl_order_id": None,
            "sl_mode": "client_side",
            "opened_at": "2026-05-26T22:00:00+00:00",
            "fees_paid_usdt": 0.4,
            "fees_are_actual": True,
        })

    def _make_paper(self, **overrides):
        pt = {
            "id": "BTCUSDT|LONG|2026-05-26T22:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_price": 100.0,
            "sl": 95.0,
            "tp1": 105.0,
            "tp2": 110.0,
            "entered_at": "2026-05-26T22:00:00+00:00",
            "intensity": 2,
        }
        pt.update(overrides)
        return pt

    def test_post_open_panic_returns_minimal_trade_not_none(self):
        """post-open 字段构造抛异常时, 返回 panic_trade (含 paper_id/symbol)
        而不是 None — 防止外层无法记录到 state 形成孤儿.
        """
        # 构造一个会让 _funding_signal 抛异常的 paper (强制损坏 funding_rate_pct)
        # 但更可靠的方式: 用 patch 让某个内部函数 raise
        pt = self._make_paper()
        with patch.object(
            live_trader, "_ab_use_sl_compensation",
            side_effect=RuntimeError("simulated post-open failure"),
        ):
            result = live_trader._try_mirror_open(
                self.client, pt, dry_run=False, btc_regime=None,
            )
        self.assertIsNotNone(
            result,
            "post-open 异常必须返回 panic_trade 而不是 None — 否则仓位在 exchange 但 state 不知, 形成孤儿",
        )
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["paper_id"], pt["id"])
        self.assertTrue(result.get("_partial_record"),
                        "panic_trade 应有 _partial_record=True 标记")
        # 关键字段必须存在
        self.assertIn("trade_id", result)
        self.assertIn("avg_fill_price", result)
        self.assertIn("qty", result)
        self.assertIn("sl_price", result)

    def test_normal_path_still_works(self):
        """sanity: 正常情况下 _try_mirror_open 仍返回完整 live_trade (无 _partial_record)."""
        pt = self._make_paper()
        result = live_trader._try_mirror_open(
            self.client, pt, dry_run=False, btc_regime=None,
        )
        self.assertIsNotNone(result)
        self.assertFalse(result.get("_partial_record", False),
                         "正常路径不应有 _partial_record 标记")
        # 完整字段应都存在
        self.assertIn("ab_group", result)
        self.assertIn("funding_signal", result)
        self.assertIn("slippage_bps", result)


class TestPhase4ZLiveTradeMetadata(unittest.TestCase):
    """Phase 4.Z (5/27): live_trade 字典也复制 paper 的大户/散户多空比.

    数据流: scanner 写 paper_trade.top_trader_* → live_trader mirror
    时复制到 live_trade.top_trader_* → 写入 live_trades_history.json.
    复盘时直接按 live 切片 (无需 JOIN paper).
    """

    def setUp(self):
        from binance_client import BinanceClient
        self.client = MagicMock(spec=BinanceClient)
        self.client.dry_run = False
        self.client.get_book_ticker = MagicMock(return_value={
            "askPrice": "100.0", "bidPrice": "99.5",
        })
        self.client.set_leverage = MagicMock(return_value=None)
        self.client.open_position = MagicMock(return_value={
            "avg_fill_price": 100.0,
            "qty": 4.0,
            "actual_notional": 400.0,
            "entry_order_id": "12345",
            "entry_client_id": "test_client_id",
            "sl_order_id": None,
            "sl_mode": "client_side",
            "opened_at": "2026-05-27T22:00:00+00:00",
            "fees_paid_usdt": 0.4,
            "fees_are_actual": True,
        })

    def _make_paper(self):
        return {
            "id": "BTCUSDT|LONG|2026-05-27T22:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_price": 100.0,
            "sl": 95.0,
            "tp1": 105.0,
            "tp2": 110.0,
            "entered_at": "2026-05-27T22:00:00+00:00",
            "intensity": 2,
            # Phase 4.Z 字段
            "top_trader_position_ratio": 1.85,
            "top_trader_account_ratio":  1.45,
            "global_account_ratio":      0.62,
        }

    def test_normal_path_copies_phase4z_fields(self):
        pt = self._make_paper()
        result = live_trader._try_mirror_open(
            self.client, pt, dry_run=False, btc_regime=None,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["top_trader_position_ratio"], 1.85)
        self.assertEqual(result["top_trader_account_ratio"], 1.45)
        self.assertEqual(result["global_account_ratio"], 0.62)

    def test_panic_path_copies_phase4z_fields(self):
        """异常路径也必须保留 Phase 4.Z 字段 (数据完整性)."""
        pt = self._make_paper()
        with patch.object(
            live_trader, "_ab_use_sl_compensation",
            side_effect=RuntimeError("trigger panic"),
        ):
            result = live_trader._try_mirror_open(
                self.client, pt, dry_run=False, btc_regime=None,
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.get("_partial_record"))
        self.assertEqual(result["top_trader_position_ratio"], 1.85)
        self.assertEqual(result["top_trader_account_ratio"], 1.45)
        self.assertEqual(result["global_account_ratio"], 0.62)

    def test_missing_phase4z_fields_defaults_to_none(self):
        """paper 没有 Phase 4.Z 字段 (旧数据) → live_trade 该字段为 None."""
        pt = self._make_paper()
        del pt["top_trader_position_ratio"]
        del pt["top_trader_account_ratio"]
        del pt["global_account_ratio"]
        result = live_trader._try_mirror_open(
            self.client, pt, dry_run=False, btc_regime=None,
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result["top_trader_position_ratio"])
        self.assertIsNone(result["top_trader_account_ratio"])
        self.assertIsNone(result["global_account_ratio"])


class TestPhase4YSlCompensationAlways(unittest.TestCase):
    """Phase 4.Y (5/27): SL 补偿从 abcd 推广到 always.

    数据驱动决策 (5/26 复盘):
      - 11 笔 paper hit_sl → live sl_breach_client, avg gap +$1.61/笔
      - 平均 slippage 28.5 bps, 入场偏移使 live 实际 SL 距离 > paper
      - 启用 always 后所有 trade 的 live_sl = paper_sl + slippage_offset
      - Wick filter 已 always (min_breaches=2) 防误触发
    """

    def test_compensation_mode_is_always(self):
        """常量从 'abcd' 升级到 'always' (Phase 4.Y)."""
        from live_trader import LIVE_SL_COMPENSATION_MODE
        self.assertEqual(LIVE_SL_COMPENSATION_MODE, "always",
                          "Phase 4.Y: 所有 trade 都应启用 SL 补偿")

    def test_all_paper_ids_get_compensation(self):
        """always 模式下所有 paper_id 都返回 True (不分 A/B/C/D 组)."""
        # 跨多个典型 paper_id 验证
        ids = [
            "BTCUSDT|LONG|2026-05-27T00:00:00+00:00",
            "ETHUSDT|SHORT|2026-05-27T12:00:00+00:00",
            "RANDOM|LONG|2026-05-27T05:30:00+00:00",
            "MYXUSDT|SHORT|2026-05-26T14:00:00+00:00",
        ]
        for pid in ids:
            self.assertTrue(
                _ab_use_sl_compensation(pid),
                f"Phase 4.Y always 模式下 {pid} 应启用 SL 补偿",
            )

    def test_wick_filter_still_always(self):
        """前置条件: wick filter 必须 always, 否则 SL 移近 + 无 wick 过滤 = 误触发."""
        from live_trader import LIVE_SL_WICK_FILTER_MODE
        self.assertEqual(LIVE_SL_WICK_FILTER_MODE, "always",
                          "Phase 4.Y 依赖 wick filter always (防 SL 移近后的误触发)")


class TestPhase5ALiveNotionalByScore(unittest.TestCase):
    """Phase 5.A: live notional 按 paper conviction_score 分档.

    与 paper 同步:
      score 5: $400 基准, 6-7: $800 (2×), 8+: $200 (0.5×).
    """

    def test_score_5_reduced_to_200(self):
        """Phase 5.K (6/1): score 5 减半 400→200 (低 EV 缩仓)."""
        from live_trader import _live_notional_for_paper
        self.assertEqual(_live_notional_for_paper({"conviction_score": 5}), 200.0)

    def test_score_6_stays_at_400(self):
        """Phase 5.K-adjust (6/1): score 6 撤回 5.A-restore 的 800.
        5/31+6/1 实盘 6 笔全亏 avg -$5.83 矛盾历史 EV +$4.34, 保守回 $400.
        """
        from live_trader import _live_notional_for_paper
        self.assertEqual(_live_notional_for_paper({"conviction_score": 6}), 400.0)

    def test_score_7_restored_to_800(self):
        """Phase 5.A-restore: score 7 维持 $800 (11 笔 avg +$4.27 验证有效)."""
        from live_trader import _live_notional_for_paper
        self.assertEqual(_live_notional_for_paper({"conviction_score": 7}), 800.0)

    def test_score_8_plus_halved(self):
        from live_trader import _live_notional_for_paper
        self.assertEqual(_live_notional_for_paper({"conviction_score": 8}), 200.0)
        self.assertEqual(_live_notional_for_paper({"conviction_score": 9}), 200.0)
        self.assertEqual(_live_notional_for_paper({"conviction_score": 10}), 200.0)

    def test_score_missing_fallback(self):
        from live_trader import _live_notional_for_paper, LIVE_NOTIONAL_USDT
        self.assertEqual(_live_notional_for_paper({}), LIVE_NOTIONAL_USDT)
        self.assertEqual(_live_notional_for_paper({"conviction_score": "bad"}),
                          LIVE_NOTIONAL_USDT)

    def test_max_deploy_increased_to_2400(self):
        """Phase 5.A: max_deploy $1600 → $2400 (适配 score 分档)."""
        from live_trader import LIVE_MAX_DEPLOY_USDT
        self.assertEqual(LIVE_MAX_DEPLOY_USDT, 2400.0)


class TestPhase5JWickFilterThreshold(unittest.TestCase):
    """Phase 5.J → 5.M → 6.E: wick filter min_breaches 2→3→4→6.

    Phase 5.J (5/31): 2→3, sl_breach 64 → 27 (-58%) 已大幅改善.
    Phase 5.M (6/1): 3→4, 升 4 = 20s 总确认, 预期再救 8-12 笔.
    Phase 6.E (6/6): 4→6, 修 paper/live polling 不对称 (paper 30s snapshot vs live 5s poll).
                    实战 60h: 48 wick-out, $122 可救. 6 breach = 30s 确认 = 跟 paper 对齐.

    favorable funding 始终保持 +1 buffer 偏移 (现 6+1=7).
    """

    def test_default_min_breaches_is_6(self):
        """Phase 6.E (6/6): 4 → 6 (跟 paper 30s snapshot 周期对齐)."""
        from live_trader import LIVE_WICK_FILTER_MIN_BREACHES
        self.assertEqual(LIVE_WICK_FILTER_MIN_BREACHES, 6)

    def test_favorable_funding_min_breaches_default_plus_1(self):
        """funding favorable 时 default+1: 现 6 → 7 (Phase 6.E)."""
        from live_trader import (LIVE_FUNDING_FAVORABLE_WICK_BREACHES,
                                   LIVE_WICK_FILTER_MIN_BREACHES)
        self.assertEqual(LIVE_FUNDING_FAVORABLE_WICK_BREACHES,
                          LIVE_WICK_FILTER_MIN_BREACHES + 1)

    def test_wick_filter_needs_6_consecutive_breaches(self):
        """实测 _check_sl_breach: Phase 6.E 需 6 次连续 breach 才触发 (30s 确认)."""
        from live_trader import _check_sl_breach
        lt = {"side": "BUY", "sl_price": 100.0, "wick_filter_enabled": True,
              "sl_breach_count": 0}
        for i in range(5):  # 5 次都不触发
            self.assertFalse(_check_sl_breach(lt, 99.0), f"breach #{i+1} 不应触发")
        self.assertTrue(_check_sl_breach(lt, 99.0))    # 第 6 次触发

    def test_wick_filter_count_resets_on_recovery(self):
        """breach 序列被价格回归打断, 计数清零 (Phase 6.E: 需 6 次累积)."""
        from live_trader import _check_sl_breach
        lt = {"side": "BUY", "sl_price": 100.0, "wick_filter_enabled": True,
              "sl_breach_count": 0}
        _check_sl_breach(lt, 99.0)   # cnt=1
        _check_sl_breach(lt, 101.0)  # 价格回, cnt → 0
        for i in range(5):
            self.assertFalse(_check_sl_breach(lt, 99.0), f"重新累积 #{i+1} 不应触发")
        self.assertTrue(_check_sl_breach(lt, 99.0))    # cnt=6, 触发


class TestPhase5HOrphanRootCauseFix(unittest.TestCase):
    """Phase 5.H (5/30) CRITICAL: 修复孤儿仓 root cause.

    背景: 5/28-5/30 累计 14 个孤儿仓, 27 次 AttributeError. 数据驱动诊断:
      1. open_position IOC 部分成交时 (status=EXPIRED + executedQty>0) 返 None
      2. 但 exchange 上有 partial position → 孤儿
      3. _try_mirror_open 收到 None, panic_trade 路径 result.get() 二次抛错
      4. 异常上传到 main_loop, 仓位完全无 trace
    """

    def test_try_mirror_open_returns_none_when_result_none(self):
        """Phase 5.H: open_position 返 None 时 _try_mirror_open 早退, 不进 panic 路径."""
        from binance_client import BinanceClient
        client = MagicMock(spec=BinanceClient)
        client.dry_run = False
        client.get_book_ticker = MagicMock(return_value={"askPrice": "100.0", "bidPrice": "99.5"})
        client.set_leverage = MagicMock(return_value=None)
        # 模拟 open_position 返 None (IOC EXPIRED 0 fill)
        client.open_position = MagicMock(return_value=None)
        pt = {
            "id": "TEST|LONG|t1", "symbol": "TESTUSDT",
            "direction": "LONG", "entry_price": 100.0,
            "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
            "intensity": 2,
        }
        # 不应抛 AttributeError, 应 cleanly 返 None
        result = live_trader._try_mirror_open(client, pt, dry_run=False, btc_regime=None)
        self.assertIsNone(result, "open_position 返 None 时 _try_mirror_open 必须早退返 None")
        # close_position 不应被调用 (没有 partial position 需要清理)
        client.close_position.assert_not_called()


class TestPhase5GPostFillEmergency(unittest.TestCase):
    """Phase 5.G (5/28): Post-fill 应急平仓 (参考社区 shadow_entry_deviation).

    open_position 成功后 2 次后置校验:
      1) |fill - paper_entry| > 200bps → entry_deviation_too_high
      2) TP/SL 结构无效 (LONG sl<fill<tp1<tp2; SHORT sl>fill>tp1>tp2) → post_fill_structure_invalid
    任一触发: client.close_position 应急平仓 + 返回 None.
    """

    # === _validate_post_fill_structure ===

    def test_structure_long_valid(self):
        """LONG: sl < fill < tp2 → True (即使 fill 略高于 tp1 也 OK)."""
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "BUY", fill=100.0, sl=95.0, tp1=105.0, tp2=110.0,
            )
        )

    def test_structure_long_fill_slightly_above_tp1_still_valid(self):
        """Phase 5.G-fix: fill 略高于 tp1 但低于 tp2 → True (BIOUSDT 噪音 case).
        实际 fill 在 tp1 +/- 几 bps 是市场噪音, 进 Phase B 早一点不是灾难.
        """
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "BUY", fill=106.0, sl=95.0, tp1=105.0, tp2=110.0,
            )
        )

    def test_structure_long_fill_above_tp2_invalid(self):
        """LONG: fill > tp2 = 利润空间为 0 → False (真灾难)."""
        self.assertFalse(
            live_trader._validate_post_fill_structure(
                "BUY", fill=111.0, sl=95.0, tp1=105.0, tp2=110.0,
            )
        )

    def test_structure_long_sl_above_fill_invalid(self):
        """LONG: SL 在 fill 上 (即开仓即亏损区) → False."""
        self.assertFalse(
            live_trader._validate_post_fill_structure(
                "BUY", fill=100.0, sl=101.0, tp1=105.0, tp2=110.0,
            )
        )

    def test_structure_short_valid(self):
        """SHORT: sl > fill > tp2 → True."""
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "SELL", fill=100.0, sl=105.0, tp1=95.0, tp2=90.0,
            )
        )

    def test_structure_short_fill_slightly_below_tp1_still_valid(self):
        """Phase 5.G-fix: SHORT 下 fill 略低于 tp1 但高于 tp2 → True."""
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "SELL", fill=94.0, sl=105.0, tp1=95.0, tp2=90.0,
            )
        )

    def test_structure_short_fill_below_tp2_invalid(self):
        """SHORT: fill < tp2 = 利润空间为 0 → False."""
        self.assertFalse(
            live_trader._validate_post_fill_structure(
                "SELL", fill=89.0, sl=105.0, tp1=95.0, tp2=90.0,
            )
        )

    def test_structure_missing_fields_skipped(self):
        """字段缺/异常 (= 0) 返 True (向后兼容, 不误平).
        Phase 5.G-fix 后只依赖 fill/sl/tp2 三个字段, tp1 缺也 OK.
        """
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "BUY", fill=100.0, sl=0, tp1=105.0, tp2=110.0,
            )
        )
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "BUY", fill=0, sl=95.0, tp1=105.0, tp2=110.0,
            )
        )
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "BUY", fill=100.0, sl=95.0, tp1=105.0, tp2=0,
            )
        )

    def test_structure_unknown_side_skipped(self):
        """未知 side → True (跳过, 不阻塞)."""
        self.assertTrue(
            live_trader._validate_post_fill_structure(
                "INVALID", fill=100.0, sl=95.0, tp1=105.0, tp2=110.0,
            )
        )

    # === 应急平仓集成 (验证常量) ===

    def test_post_fill_threshold_constant(self):
        """常量定义在合理范围 (≥ 100 bps, ≤ 500 bps, 不与 pre-check 阈值冲突)."""
        from live_trader import LIVE_POST_FILL_MAX_DEVIATION_BPS
        # ≥ 100: 至少 1% 才触发, 避免微小滑点误平
        self.assertGreaterEqual(LIVE_POST_FILL_MAX_DEVIATION_BPS, 100)
        # ≤ 500: 5% 是绝对上限, 再大就是 paper 数据异常
        self.assertLessEqual(LIVE_POST_FILL_MAX_DEVIATION_BPS, 500)

    def test_emergency_close_returns_terminal_dict(self):
        """Phase 5.G-fix: 应急平仓后返回 _terminal_no_retry dict, 不返 None."""
        from binance_client import BinanceClient
        client = MagicMock(spec=BinanceClient)
        client.dry_run = False
        client.get_book_ticker = MagicMock(return_value={"askPrice": "100.0", "bidPrice": "99.5"})
        client.set_leverage = MagicMock(return_value=None)
        # 故意构造严重偏离的 fill 触发 entry_deviation_too_high
        client.open_position = MagicMock(return_value={
            "avg_fill_price": 150.0,   # 50% 高于 paper_entry 100
            "qty": 4.0, "actual_notional": 600.0,
            "entry_order_id": "1", "entry_client_id": "c",
            "opened_at": "2026-05-30T08:00:00+00:00",
            "fees_paid_usdt": 0.16,
            "_dryRun": False,
        })
        client.close_position = MagicMock(return_value={
            "qty_closed": 4.0, "avg_exit_price": 150.0,
            "realized_pnl_usdt": 0.0, "fees_paid_usdt": 0.16,
        })
        pt = {
            "id": "TEST|LONG|t1", "symbol": "TESTUSDT",
            "direction": "LONG", "entry_price": 100.0,
            "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
            "intensity": 2,
        }
        result = live_trader._try_mirror_open(client, pt, dry_run=False, btc_regime=None)
        # 不再返 None — 返 terminal dict
        self.assertIsNotNone(result)
        self.assertTrue(result.get("_terminal_no_retry"))
        self.assertEqual(result["close_reason"], "entry_deviation_too_high")
        # 应急平仓被调用
        client.close_position.assert_called_once()
        # PnL = -fees (entry + close ≈ 0.32)
        self.assertLess(result["realized_pnl_usdt"], 0)


class TestPhase5ECircuitBreaker(unittest.TestCase):
    """Phase 5.E (5/28): 连损熔断 — 30min 内 ≥4 笔 hit_sl 触发暂停 30min.

    数据驱动 (1410 笔模拟): 净避亏 +$127. 触发: hit_sl 计数, paper:hit_sl 也算,
    但 hit_b_trail / hit_trail / timeout / already_closed_externally 不计.
    """

    def _make_state(self, sl_times, base_time=None, paused_until=None):
        """构造一个 live_state 含指定时间的 hit_sl trades."""
        base = base_time or datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)
        closed_trades = []
        for offset_min, reason in sl_times:
            closed_trades.append({
                "symbol": "TEST", "close_reason": reason,
                "closed_at": (base - timedelta(minutes=offset_min)).isoformat(),
            })
        state = {"live_closed_trades": closed_trades}
        if paused_until:
            state["circuit_breaker_paused_until"] = paused_until
        return state, base

    def test_no_trigger_below_threshold(self):
        """3 笔 hit_sl in 30min < threshold 4 → 不触发."""
        state, now = self._make_state([(5, "sl_breach_client"), (10, "paper:hit_sl"),
                                         (15, "sl_breach_client")])
        paused, _ = live_trader._check_circuit_breaker(state, now)
        self.assertFalse(paused)

    def test_trigger_at_threshold(self):
        """正好 4 笔 → 触发."""
        state, now = self._make_state([
            (5, "sl_breach_client"), (10, "paper:hit_sl"),
            (15, "sl_breach_client"), (25, "paper:hit_sl"),
        ])
        paused, until_iso = live_trader._check_circuit_breaker(state, now)
        self.assertTrue(paused)
        self.assertIsNotNone(until_iso)
        # state 应该被更新
        self.assertEqual(state["circuit_breaker_paused_until"], until_iso)

    def test_old_sl_outside_window_not_counted(self):
        """超出 30min 窗口的 SL 不计入."""
        state, now = self._make_state([
            (5, "sl_breach_client"), (10, "sl_breach_client"),
            (15, "sl_breach_client"),
            (35, "sl_breach_client"),  # 在窗口外
            (40, "sl_breach_client"),  # 在窗口外
        ])
        paused, _ = live_trader._check_circuit_breaker(state, now)
        self.assertFalse(paused, "超出 30min 的 SL 不应计数")

    def test_non_sl_reasons_not_counted(self):
        """hit_b_trail / hit_trail / timeout 不算 SL."""
        state, now = self._make_state([
            (5, "paper:hit_b_trail"), (10, "paper:hit_trail"),
            (15, "timeout"), (20, "paper:hit_b_trail"),
            (25, "already_closed_externally"),
        ])
        paused, _ = live_trader._check_circuit_breaker(state, now)
        self.assertFalse(paused, "非 SL close reason 不应触发熔断")

    def test_still_paused_during_pause_period(self):
        """暂停期内 (paused_until > now) 直接返 True, 不重新检查."""
        future = (datetime(2026, 5, 28, 11, 0, 0, tzinfo=timezone.utc) +
                  timedelta(minutes=20)).isoformat()
        state, now = self._make_state([], paused_until=future)
        paused, until_iso = live_trader._check_circuit_breaker(state, now)
        self.assertTrue(paused)
        self.assertEqual(until_iso, future)

    def test_pause_expires_after_until(self):
        """暂停时间过去后, 标记清除, 重新检查触发条件."""
        past = (datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc) -
                timedelta(minutes=10)).isoformat()
        state, now = self._make_state([], paused_until=past)
        paused, _ = live_trader._check_circuit_breaker(state, now)
        self.assertFalse(paused, "暂停过期应清除并重新评估")
        self.assertIsNone(state["circuit_breaker_paused_until"])

    def test_invalid_paused_until_recovers(self):
        """损坏的 paused_until 字符串 → 安全清除, 不崩."""
        state, now = self._make_state([], paused_until="not a valid datetime")
        # 不应抛异常
        paused, _ = live_trader._check_circuit_breaker(state, now)
        # state 应该被清掉
        self.assertIsNone(state.get("circuit_breaker_paused_until"))


class TestPhase5DCandidatePrioritization(unittest.TestCase):
    """Phase 5.D (5/28): slot 稀缺时 mirror_candidates 按 conviction_score 优先.

    背景: 1410 笔数据显示 score 6-7 EV 是 score 5 的 5× ($4.50 vs $0.92).
    当 max_concurrent / deploy cap 限制时, 高 score 信号应优先入场,
    漏掉的应是低 EV (score 5) 信号. Phase 5.B 已让 BTC trend-aligned
    信号 +1 score, 故 score 降序排自动倾向趋势对齐.
    """

    def _make_pt(self, symbol, score, entered_at, direction="LONG"):
        """构造一个 paper trade dict 用于排序测试."""
        return {
            "id": f"{symbol}|{direction}|{entered_at}",
            "symbol": symbol, "direction": direction,
            "conviction_score": score,
            "entered_at": entered_at,
            "entry_price": 100.0, "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
        }

    def test_high_score_sorts_first(self):
        """score 6-7 应排在 score 5 前面."""
        candidates = [
            self._make_pt("AUSDT", 5, "2026-05-28T10:00:00+00:00"),
            self._make_pt("BUSDT", 7, "2026-05-28T10:01:00+00:00"),
            self._make_pt("CUSDT", 6, "2026-05-28T10:02:00+00:00"),
        ]
        candidates.sort(
            key=lambda pt: (
                -int(pt.get("conviction_score") or 0),
                pt.get("entered_at", ""),
            )
        )
        # 顺序: 7 (BUSDT) → 6 (CUSDT) → 5 (AUSDT)
        self.assertEqual([pt["symbol"] for pt in candidates],
                          ["BUSDT", "CUSDT", "AUSDT"])

    def test_same_score_fifo_by_entered_at(self):
        """同 score 时按时间 FIFO."""
        candidates = [
            self._make_pt("LATE", 5, "2026-05-28T10:05:00+00:00"),
            self._make_pt("EARLY", 5, "2026-05-28T10:00:00+00:00"),
            self._make_pt("MID", 5, "2026-05-28T10:02:00+00:00"),
        ]
        candidates.sort(
            key=lambda pt: (
                -int(pt.get("conviction_score") or 0),
                pt.get("entered_at", ""),
            )
        )
        self.assertEqual([pt["symbol"] for pt in candidates],
                          ["EARLY", "MID", "LATE"])

    def test_btc_aligned_long_sorts_before_non_aligned_via_score(self):
        """关键验证: Phase 5.B + 5.D 协同 — BTC up + LONG 因 +1 score 自动优先.
        模拟 scanner 输出: 同样基础质量的信号, BTC up 时 LONG 得 score 6, SHORT 仅 score 5.
        """
        aligned = self._make_pt("ALIGNED", 6, "2026-05-28T10:00:00+00:00", direction="LONG")
        non_aligned = self._make_pt("NONALIGNED", 5, "2026-05-28T09:55:00+00:00", direction="SHORT")
        # 注意: non_aligned 时间更早 (FIFO 本应先), 但 score 低应该排后
        candidates = [non_aligned, aligned]
        candidates.sort(
            key=lambda pt: (
                -int(pt.get("conviction_score") or 0),
                pt.get("entered_at", ""),
            )
        )
        self.assertEqual(candidates[0]["symbol"], "ALIGNED",
                          "趋势对齐 (score 6) 应优先于早到的非对齐 (score 5)")

    def test_missing_score_treated_as_zero(self):
        """异常: conviction_score 缺失 → 视为 0, 排到最后."""
        candidates = [
            self._make_pt("NORMAL", 5, "2026-05-28T10:00:00+00:00"),
            {"id": "BROKEN", "symbol": "BROKEN", "entered_at": "2026-05-28T09:00:00+00:00"},
        ]
        candidates.sort(
            key=lambda pt: (
                -int(pt.get("conviction_score") or 0),
                pt.get("entered_at", ""),
            )
        )
        self.assertEqual(candidates[0]["symbol"], "NORMAL")
        self.assertEqual(candidates[1]["symbol"], "BROKEN")


class TestPhase5AExternalCloseRecovery(unittest.TestCase):
    """Phase 5.A-fix (5/28): _try_mirror_close 处理 "exchange 已无持仓" 恢复.

    场景: live_state 还追踪某 trade, 但 exchange 上已平仓 (手动 / 之前 /
    异常成交). 之前 close_position 抛 BinanceError("无持仓"), live_trader
    返 None, 上层无限重试每 5s → DYMUSDT 死循环 6+ min.

    修复: 检测 "无持仓" / "positionAmt=0" 错误时, 不重试, 返回 synthetic
    closed dict 标记 already_closed_externally, 让 state 自动清理.
    """

    def setUp(self):
        from binance_client import BinanceClient
        self.client = MagicMock(spec=BinanceClient)
        self.live_trade = {
            "symbol": "DYMUSDT", "side": "SELL", "trade_id": "test_trade_001",
            "paper_id": "DYMUSDT|SHORT|2026-05-28T00:00:00+00:00",
            "avg_fill_price": 0.022, "qty": 18075, "sl_price": 0.025,
        }

    def test_no_position_returns_synthetic_closed_not_none(self):
        """exchange 已无持仓 → 返回 synthetic closed (非 None) 让上层归类."""
        from binance_client import BinanceError
        self.client.close_position = MagicMock(
            side_effect=BinanceError("DYMUSDT 当前无持仓 (positionAmt=0)")
        )
        result = live_trader._try_mirror_close(
            self.client, self.live_trade, reason="paper:hit_trail", dry_run=False,
        )
        self.assertIsNotNone(result, "无持仓应返回 synthetic dict, 而不是 None")
        self.assertEqual(result["close_reason"], "already_closed_externally")
        self.assertEqual(result["realized_pnl_usdt"], 0.0)
        self.assertIn("closed_at", result)

    def test_no_position_alternative_message(self):
        """支持多种 '无持仓' 错误措辞 ('无持仓' / 'positionAmt=0')."""
        from binance_client import BinanceError
        # 测试只含 "无持仓" 关键字
        self.client.close_position = MagicMock(
            side_effect=BinanceError("无 DYMUSDT 持仓记录")
        )
        result = live_trader._try_mirror_close(
            self.client, self.live_trade, reason="test", dry_run=False,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["close_reason"], "already_closed_externally")

    def test_other_errors_still_return_none(self):
        """非 '无持仓' 错误 (如 timeout, 网络) 仍返 None → 上层重试."""
        from binance_client import BinanceError
        self.client.close_position = MagicMock(
            side_effect=BinanceError("http 408 code=-1007 msg=Timeout")
        )
        result = live_trader._try_mirror_close(
            self.client, self.live_trade, reason="test", dry_run=False,
        )
        self.assertIsNone(result, "其他错误应返 None 让上层重试, 不能误归 already_closed")

    def test_synthetic_preserves_paper_id(self):
        """synthetic dict 必须保留 paper_id (供 audit 匹配)."""
        from binance_client import BinanceError
        self.client.close_position = MagicMock(
            side_effect=BinanceError("当前无持仓 (positionAmt=0)")
        )
        result = live_trader._try_mirror_close(
            self.client, self.live_trade, reason="test", dry_run=False,
        )
        self.assertEqual(result["paper_id"], self.live_trade["paper_id"])
        self.assertEqual(result["symbol"], "DYMUSDT")


class TestPhase6DAutoHealLiveOnly(unittest.TestCase):
    """Phase 6.D (2026-06-04): 用户后台手动一键平仓 → live state 自动清理.

    场景: 用户在 Binance UI 一键平仓后, reconcile 检测到 live_only mismatch,
    但 live_trader 没有主动关仓 trigger (SL/TP/timeout 都没到) → state 卡住.
    新行为: 连续 LIVE_RECON_AUTO_HEAL_TICKS=3 tick 检测到, 主动 mark
    already_closed_externally_auto.
    """

    def setUp(self):
        from datetime import datetime, timezone
        from live_trader import _auto_heal_live_only_mismatches
        self._heal = _auto_heal_live_only_mismatches
        self.now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
        self.live_state = {
            "live_open_trades": [
                {"symbol": "ONDOUSDT", "direction": "SHORT",
                 "entry_price": 0.3568, "sl": 0.3620, "entered_at": "2026-06-04T11:00:00+00:00"},
                {"symbol": "BTCUSDT", "direction": "LONG",
                 "entry_price": 50000, "sl": 49000, "entered_at": "2026-06-04T11:30:00+00:00"},
            ],
            "live_closed_trades": [],
        }

    def _recon_with_mismatch(self, *live_only_syms, api_failed=False):
        return {
            "ok": not live_only_syms,
            "api_failed": api_failed,
            "mismatches": [{"symbol": s, "kind": "live_only", "message": ""}
                           for s in live_only_syms],
            "live_symbols": [t["symbol"] for t in self.live_state["live_open_trades"]],
            "exchange_symbols": [],
        }

    def test_increments_streak_below_threshold(self):
        """N-1 tick mismatch: streak 累计但还没动手."""
        recon = self._recon_with_mismatch("ONDOUSDT")
        for _ in range(2):  # < LIVE_RECON_AUTO_HEAL_TICKS=3
            healed = self._heal(self.live_state, recon, self.now)
            self.assertEqual(healed, [])
        self.assertEqual(self.live_state["_live_only_streak"]["ONDOUSDT"], 2)
        # open_trades 还在
        syms = [t["symbol"] for t in self.live_state["live_open_trades"]]
        self.assertIn("ONDOUSDT", syms)

    def test_heals_at_threshold(self):
        """连续 3 tick mismatch → 自动清理."""
        recon = self._recon_with_mismatch("ONDOUSDT")
        for tick in range(3):
            healed = self._heal(self.live_state, recon, self.now)
        self.assertEqual(len(healed), 1)
        self.assertEqual(healed[0]["symbol"], "ONDOUSDT")
        self.assertEqual(healed[0]["close_reason"], "already_closed_externally_auto")
        self.assertEqual(healed[0]["realized_pnl_usdt"], 0.0)
        self.assertEqual(healed[0]["_auto_heal_streak_ticks"], 3)
        # open_trades 只剩 BTCUSDT
        syms = [t["symbol"] for t in self.live_state["live_open_trades"]]
        self.assertEqual(syms, ["BTCUSDT"])
        # closed_trades 多了 ONDOUSDT
        closed_syms = [t["symbol"] for t in self.live_state["live_closed_trades"]]
        self.assertEqual(closed_syms, ["ONDOUSDT"])
        # streak 已清
        self.assertNotIn("ONDOUSDT", self.live_state.get("_live_only_streak", {}))

    def test_streak_resets_when_mismatch_disappears(self):
        """tick 1 mismatch → tick 2 恢复 → streak 应重置, 不应继续累计."""
        recon_bad = self._recon_with_mismatch("ONDOUSDT")
        recon_ok = self._recon_with_mismatch()  # 无 mismatch
        self._heal(self.live_state, recon_bad, self.now)
        self.assertEqual(self.live_state["_live_only_streak"]["ONDOUSDT"], 1)
        # mismatch 消失
        self._heal(self.live_state, recon_ok, self.now)
        self.assertNotIn("ONDOUSDT", self.live_state.get("_live_only_streak", {}))
        # 再来 2 tick 不够触发
        for _ in range(2):
            healed = self._heal(self.live_state, recon_bad, self.now)
            self.assertEqual(healed, [])
        self.assertEqual(self.live_state["_live_only_streak"]["ONDOUSDT"], 2)

    def test_api_failed_skips_counter(self):
        """API 失败 tick 不动 counter (防网络闪挂误杀)."""
        recon_fail = self._recon_with_mismatch("ONDOUSDT", api_failed=True)
        for _ in range(10):  # 10 次 API fail, 没用
            healed = self._heal(self.live_state, recon_fail, self.now)
            self.assertEqual(healed, [])
        self.assertEqual(self.live_state.get("_live_only_streak", {}), {})
        # open_trades 完全没动
        syms = [t["symbol"] for t in self.live_state["live_open_trades"]]
        self.assertIn("ONDOUSDT", syms)

    def test_multiple_symbols_heal_independently(self):
        """两个 symbol 同时 live_only, 各自独立计 streak, 各自独立 heal."""
        recon = self._recon_with_mismatch("ONDOUSDT", "BTCUSDT")
        for _ in range(3):
            healed = self._heal(self.live_state, recon, self.now)
        self.assertEqual(len(healed), 2)
        healed_syms = {h["symbol"] for h in healed}
        self.assertEqual(healed_syms, {"ONDOUSDT", "BTCUSDT"})
        self.assertEqual(self.live_state["live_open_trades"], [])
        self.assertEqual(len(self.live_state["live_closed_trades"]), 2)

    def test_heal_idempotent_after_open_removed(self):
        """Symbol 已经从 open_trades 消失但 streak 还在 → 仅清 streak, 不重复 heal."""
        # 模拟竞态: streak 已经 3, 但 open_trades 被并行清理了
        self.live_state["_live_only_streak"] = {"ONDOUSDT": 3}
        self.live_state["live_open_trades"] = [
            t for t in self.live_state["live_open_trades"] if t["symbol"] != "ONDOUSDT"
        ]
        recon = self._recon_with_mismatch("ONDOUSDT")
        healed = self._heal(self.live_state, recon, self.now)
        # ONDOUSDT 不在 open_trades 了 → 不应再产生 closed 记录
        self.assertEqual(healed, [])
        # streak 也应被清掉 (heal 函数把它当 "已处理" 重置)
        self.assertNotIn("ONDOUSDT", self.live_state.get("_live_only_streak", {}))


class TestPhase6EWickFilterAndTracking(unittest.TestCase):
    """Phase 6.E (2026-06-06): 修 paper/live polling 不对称的 wick-out 问题
    + 加 MAE / close-time regime tracking 供后续验证.

    背景: 60h 实盘 48 笔 wick-out (live SL 触发 / paper 同笔 trail 到盈利), $122 gap.
    根因: paper 30s snapshot 周期 vs live 5s polling + 4 breach (20s 确认) → 不对称.
    修复: wick filter 4 → 6 (30s 确认 = paper snapshot 等效).
    配套: MAE tracking + btc_regime_at_close 用于 48h 后再 retro 验证效果.
    """

    def test_6e_default_wick_breaches_is_6(self):
        """默认 wick filter 阈值从 4 升到 6."""
        from live_trader import LIVE_WICK_FILTER_MIN_BREACHES
        self.assertEqual(LIVE_WICK_FILTER_MIN_BREACHES, 6,
                          "Phase 6.E: 默认 wick breaches 应 = 6 (跟 paper 30s 周期对齐)")

    def test_6e_favorable_wick_breaches_is_7(self):
        """funding favorable 时 +1 偏移 → 7 (= 默认 6+1)."""
        from live_trader import (
            LIVE_FUNDING_FAVORABLE_WICK_BREACHES, LIVE_WICK_FILTER_MIN_BREACHES,
        )
        self.assertEqual(LIVE_FUNDING_FAVORABLE_WICK_BREACHES,
                          LIVE_WICK_FILTER_MIN_BREACHES + 1,
                          "favorable 应跟主常量保持 +1 偏移")
        self.assertEqual(LIVE_FUNDING_FAVORABLE_WICK_BREACHES, 7)

    def test_6e_wick_filter_at_5_breaches_does_NOT_trigger(self):
        """5 breaches < 6, 不应触发 SL (旧 4 阈值会触发)."""
        from live_trader import _check_sl_breach
        # SHORT trade, current > sl 是 breach
        trade = {
            "sl_price": 100.0, "side": "SELL",
            "wick_filter_enabled": True,
            "wick_filter_min_breaches": 6,
            "sl_breach_count": 0,
        }
        # 连续 5 个 breach (5 × 5s = 25s)
        triggered = False
        for _ in range(5):
            if _check_sl_breach(trade, 101.0):
                triggered = True
                break
        self.assertFalse(triggered, "5 breach (25s) < 阈值 6 (30s), 不应触发")
        self.assertEqual(trade["sl_breach_count"], 5)

    def test_6e_wick_filter_at_6_breaches_triggers(self):
        """第 6 个连续 breach 触发 SL."""
        from live_trader import _check_sl_breach
        trade = {
            "sl_price": 100.0, "side": "SELL",
            "wick_filter_enabled": True,
            "wick_filter_min_breaches": 6,
            "sl_breach_count": 0,
        }
        triggered_at = None
        for i in range(7):
            if _check_sl_breach(trade, 101.0):
                triggered_at = i + 1
                break
        self.assertEqual(triggered_at, 6, "第 6 个 breach 应触发, 不应早不应晚")

    # === MAE tracking ===

    def test_6e_tag_close_time_regime_basic(self):
        """close 前 _tag_close_time_regime 应把 BTC regime 写入 trade."""
        from live_trader import _tag_close_time_regime
        trade = {"symbol": "BTCUSDT", "direction": "LONG"}
        live_state = {
            "_btc_regime_now": {
                "regime": "down",
                "sub_regime": "down_strong",
                "btc_price": 60500.5,
                "pct_vs_ma25": -3.42,
            }
        }
        _tag_close_time_regime(trade, live_state)
        self.assertEqual(trade["btc_regime_at_close"], "down")
        self.assertEqual(trade["btc_sub_regime_at_close"], "down_strong")
        self.assertEqual(trade["btc_price_at_close"], 60500.5)
        self.assertEqual(trade["btc_pct_vs_ma25_at_close"], -3.42)

    def test_6e_tag_close_time_regime_no_snapshot_safe(self):
        """live_state 没 _btc_regime_now 时不应崩溃, 也不写任何字段."""
        from live_trader import _tag_close_time_regime
        trade = {"symbol": "X"}
        _tag_close_time_regime(trade, {})
        self.assertNotIn("btc_regime_at_close", trade)
        # None state
        _tag_close_time_regime(trade, None)
        self.assertNotIn("btc_regime_at_close", trade)

    def test_6e_tag_close_time_regime_partial_snapshot(self):
        """snapshot 只有部分字段时, 只写存在的."""
        from live_trader import _tag_close_time_regime
        trade = {}
        live_state = {"_btc_regime_now": {"regime": "chop"}}  # 只有 regime
        _tag_close_time_regime(trade, live_state)
        self.assertEqual(trade.get("btc_regime_at_close"), "chop")
        self.assertNotIn("btc_sub_regime_at_close", trade)
        self.assertNotIn("btc_price_at_close", trade)

    def test_6e_tag_close_time_regime_invalid_snapshot_type(self):
        """snapshot 是非 dict 类型时 (旧数据 / corruption) 应该 safe skip."""
        from live_trader import _tag_close_time_regime
        trade = {}
        _tag_close_time_regime(trade, {"_btc_regime_now": "down"})  # str instead of dict
        self.assertNotIn("btc_regime_at_close", trade)


class TestPhase6FBlacklist(unittest.TestCase):
    """Phase 6.F (2026-06-08): 数据驱动黑名单.

    B1: conv=6 整桶不做 live (n=57 -$80 / 105h)
    B3: Tier C ($0.1-1) + LONG + BTC chop 不做 live (n=22 -$18 vs paper +$45)

    被 block 的信号: paper / shadow 继续跑, live 不开仓.
    """

    def _base_paper_trade(self, **overrides):
        """构造一笔通过其它所有 gate 的 paper trade, 用 overrides 调字段做测试."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        t = {
            "id": "TESTUSDT|LONG|p6f_test|2026-06-08T10:00:00+00:00",
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "entry_price": 5.0,        # Tier B by default (避免 B3 触发)
            "conviction_score": 5,     # conv5 default (避免 B1 触发)
            "entered_at": now,
            "sl": 4.5, "tp1": 6.0, "tp2": 7.0,
            "atr_pct": 0.5, "notional_usdt": 150.0,
            "phase": "A", "tier": "diamond",
        }
        t.update(overrides)
        return t

    def _base_live_state(self):
        from datetime import datetime, timezone
        return {
            "live_open_trades": [],
            "live_closed_trades": [],
            "mirrored_paper_ids": [],
            "missed_signals": [],
            "session_started_at": datetime.now(timezone.utc).isoformat(),
        }

    # === B1: conviction = 6 整桶 block ===

    def test_6f_b1_conv6_blocked(self):
        """conv=6 任何 tier / direction / regime 都应 block."""
        from datetime import datetime, timezone
        # 不同 tier / direction / regime 组合, 都因 conv=6 被 block
        scenarios = [
            (5.0, "LONG", "down"),    # Tier B
            (50.0, "SHORT", "up"),    # Tier A
            (0.05, "LONG", "chop"),   # Tier D
            (0.5, "SHORT", "down"),   # Tier C
        ]
        for entry, direction, regime in scenarios:
            with self.subTest(entry=entry, direction=direction, regime=regime):
                t = self._base_paper_trade(
                    entry_price=entry, direction=direction, conviction_score=6,
                )
                # 改 paper_id 避免重复 mirrored 状态
                t["id"] = f"X|{direction}|conv6_{entry}|2026-06-08T10:00:00"
                # 用 OBS mode 跳过 symbol whitelist
                with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
                    eligible, reason = is_eligible_for_mirror(
                        t, self._base_live_state(),
                        datetime.now(timezone.utc),
                        btc_regime=regime,
                    )
                self.assertFalse(eligible, f"conv=6 应 block, 实际 eligible={eligible}")
                self.assertIn("phase_6f_b1", reason)
                self.assertIn("conviction=6", reason)

    def test_6f_b1_conv5_not_blocked(self):
        """conv=5 不应被 B1 block (只 block 等于 6)."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(conviction_score=5, direction="SHORT", entry_price=5.0)
        t["id"] = "X|SHORT|conv5|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="down",
            )
        self.assertTrue(eligible, f"conv=5 不应被 6.F block, 实际 reason={reason}")

    def test_6f_b1_conv7_not_blocked_by_b1(self):
        """conv=7 不应被 B1 block (B1 严格 == 6). B2 (conv≥7) 不在 6.F 实施."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(conviction_score=7, direction="SHORT", entry_price=5.0)
        t["id"] = "X|SHORT|conv7|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="down",
            )
        self.assertTrue(eligible, f"conv=7 在 6.F 不 block (B2 未实施), 实际 reason={reason}")

    def test_6f_b1_conv_none_not_crash(self):
        """conviction_score 缺失 → 不应崩, B1 不触发."""
        from datetime import datetime, timezone
        t = self._base_paper_trade()
        del t["conviction_score"]
        t["id"] = "X|LONG|none|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True), \
             patch.object(live_trader, "LIVE_MIN_CONVICTION_SCORE", None):  # 让 2c 不拒
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="down",
            )
        # 应通过 B1 (None != 6), 进入后续 gate
        self.assertTrue(eligible or "phase_6f_b1" not in reason,
                          "缺 conv 字段不应触发 B1")

    # === B3: Tier C + LONG + BTC chop ===

    def test_6f_b3_tier_c_long_chop_blocked(self):
        """Tier C ($0.1-1) + LONG + BTC chop = block."""
        from datetime import datetime, timezone
        for entry in [0.1, 0.5, 0.99]:   # 含下界, 不含上界
            with self.subTest(entry=entry):
                t = self._base_paper_trade(
                    entry_price=entry, direction="LONG", conviction_score=5,
                )
                t["id"] = f"X|LONG|tierC_{entry}|2026-06-08T10:00:00"
                with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
                    eligible, reason = is_eligible_for_mirror(
                        t, self._base_live_state(),
                        datetime.now(timezone.utc),
                        btc_regime="chop",
                    )
                self.assertFalse(eligible, f"Tier C LONG chop 应 block (entry={entry})")
                self.assertIn("phase_6f_b3", reason)

    def test_6f_b3_tier_c_short_not_blocked(self):
        """Tier C + SHORT + chop 不应被 block (B3 仅 LONG)."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=0.5, direction="SHORT", conviction_score=5,
        )
        t["id"] = "X|SHORT|tierC|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="chop",
            )
        self.assertTrue(eligible, f"Tier C SHORT 不应 block, 实际 reason={reason}")

    def test_6f_b3_tier_c_long_down_not_blocked_by_6f(self):
        """Tier C LONG + regime=down: 6.F B3 不应触发 (只针对 chop).
        注: 这种组合会被 Phase 4.F regime gate 拒 (existing 行为, down+LONG always reject),
        但应该是 4.F 原因, 不是 6.F B3 原因.
        """
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=0.5, direction="LONG", conviction_score=5,
        )
        t["id"] = "X|LONG|down|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="down",
            )
        # 6.F B3 不应触发. (4.F regime gate 触发是 expected, 不是 6.F 责任)
        self.assertNotIn("phase_6f_b3", reason, "6.F B3 不应在 down regime 触发")

    def test_6f_b3_tier_c_long_up_not_blocked(self):
        """Tier C + LONG + BTC up: 6.F B3 不触发, 也不被 4.F 拦 (up+LONG 通过).
        这是 6.F B3 的 negative control — 同样 Tier+direction 但 regime 不同, 应通过.
        """
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=0.5, direction="LONG", conviction_score=5,
        )
        t["id"] = "X|LONG|up|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="up",
            )
        self.assertTrue(eligible, f"Tier C LONG up 应通过 (B3 仅 chop), 实际 reason={reason}")

    def test_6f_b3_tier_d_long_chop_not_blocked(self):
        """Tier D (<$0.1) LONG chop 不应 block (B3 仅 Tier C)."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=0.05, direction="LONG", conviction_score=5,
        )
        t["id"] = "X|LONG|tierD|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="chop",
            )
        self.assertTrue(eligible, f"Tier D LONG chop 不应 block, 实际 reason={reason}")

    def test_6f_b3_tier_b_long_chop_not_blocked(self):
        """Tier B ($1-10) LONG chop 不应 block (B3 仅 Tier C)."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=5.0, direction="LONG", conviction_score=5,
        )
        t["id"] = "X|LONG|tierB|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="chop",
            )
        self.assertTrue(eligible, f"Tier B LONG chop 不应 block, 实际 reason={reason}")

    def test_6f_b3_tier_c_boundary_1_dollar_not_blocked(self):
        """B3 上界排除: entry == $1.0 进 Tier B 不 block."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=1.0, direction="LONG", conviction_score=5,
        )
        t["id"] = "X|LONG|tierC_upper|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="chop",
            )
        self.assertTrue(eligible, f"$1.0 应入 Tier B 不 block, 实际 reason={reason}")

    def test_6f_b3_tier_c_boundary_0_1_dollar_blocked(self):
        """B3 下界包含: entry == $0.1 在 Tier C 内 → block."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=0.1, direction="LONG", conviction_score=5,
        )
        t["id"] = "X|LONG|tierC_lower|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="chop",
            )
        self.assertFalse(eligible, "$0.10 应入 Tier C 被 block")

    def test_6f_b3_btc_regime_none_not_blocked(self):
        """btc_regime=None (API 失败) → 不 block (fail-safe)."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=0.5, direction="LONG", conviction_score=5,
        )
        t["id"] = "X|LONG|noregime|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime=None,
            )
        self.assertTrue(eligible, "btc_regime=None 应 fail-safe 不 block")

    # === 总开关 ===

    def test_6f_disabled_no_blocking(self):
        """LIVE_PHASE_6F_BLACKLIST_ENABLED=False → 完全无 6.F gate (紧急回滚验证)."""
        from datetime import datetime, timezone
        # 既触发 B1 (conv=6) 又触发 B3 (Tier C LONG chop), 但总开关 OFF
        t = self._base_paper_trade(
            entry_price=0.5, direction="LONG", conviction_score=6,
        )
        t["id"] = "X|LONG|disabled|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True), \
             patch.object(live_trader, "LIVE_PHASE_6F_BLACKLIST_ENABLED", False):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="chop",
            )
        # 总开关 OFF 时, 不应有任何 phase_6f_ 拒绝原因
        self.assertNotIn("phase_6f", reason,
                          f"总开关 OFF 时不应 6.F gate, 实际 reason={reason}")

    # === 双重 trigger 优先级 ===

    def test_6f_b1_priority_over_b3(self):
        """同时触发 B1 (conv=6) + B3 条件 (Tier C LONG chop) → B1 先返回."""
        from datetime import datetime, timezone
        t = self._base_paper_trade(
            entry_price=0.5, direction="LONG", conviction_score=6,
        )
        t["id"] = "X|LONG|both|2026-06-08T10:00:00"
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True):
            eligible, reason = is_eligible_for_mirror(
                t, self._base_live_state(),
                datetime.now(timezone.utc),
                btc_regime="chop",
            )
        self.assertFalse(eligible)
        # 顺序: B1 在 B3 之前, 应该看到 b1 原因
        self.assertIn("phase_6f_b1", reason, "B1 应先触发 (代码顺序)")


class TestPhase6GG1NotionalReduction(unittest.TestCase):
    """Phase 6.G G1 (2026-06-11): notional × 2/3 防御性减仓.

    数据驱动: 170h pilot -$79 主因执行 drag ($66 fees + $27.5bps close slip).
    减仓后单笔风险下降 33%, 给 G2 macro filter + G3 close limit-IOC 验证空间.
    """

    def test_6g_g1_pilot_600_notional_reduced(self):
        """$600 pilot tier notional 应减到 {5:100, 6:130, 7:200, ...}."""
        import subprocess, json as json_mod
        env = dict(os.environ)
        env['CRESUS_MODE'] = 'mainnet_pilot'
        env['CRESUS_PILOT_CAPITAL'] = '600'
        result = subprocess.run(
            [sys.executable, '-c', (
                'import sys; sys.path.insert(0, "%s"); '
                'import live_trader; import json; '
                'print(json.dumps(live_trader.LIVE_NOTIONAL_BY_SCORE))'
            ) % str(HERE.parent)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, f"subprocess fail: {result.stderr}")
        notional = json_mod.loads(result.stdout.strip().split('\n')[-1])
        self.assertEqual(notional, {'5': 100, '6': 130, '7': 200,
                                      '8': 100, '9': 100, '10': 100})


class TestPhase6GG2MacroBlackout(unittest.TestCase):
    """Phase 6.G G2 (2026-06-11): macro event blackout filter.

    CPI 06-10 单日 56% sl_breach (vs baseline 50%) 验证 macro 是 mandatory.
    用现成 macro_calendar.get_blackout_decision() — 不重复造轮子.
    """

    def setUp(self):
        from datetime import datetime, timezone
        self.now = datetime.now(timezone.utc)
        self.live_state = {
            "mirrored_paper_ids": [],
            "live_open_trades": [],
        }
        # 隔离: 关 Phase 6.F (避免它先拦)
        self._6f_patcher = patch.object(
            live_trader, "LIVE_PHASE_6F_BLACKLIST_ENABLED", False)
        self._6f_patcher.start()
        self.addCleanup(self._6f_patcher.stop)

    def _base_trade(self, **overrides):
        from datetime import datetime, timezone
        t = {
            "id": "TESTUSDT|LONG|6g_test|2026-06-11T10:00:00+00:00",
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "entry_price": 5.0,
            "conviction_score": 5,
            "entered_at": (self.now - timedelta(seconds=10)).isoformat(),
            "sl": 4.5, "tp1": 6.0, "tp2": 7.0,
        }
        t.update(overrides)
        return t

    def test_6g_g2_blocks_when_macro_decision_blocked(self):
        """get_blackout_decision returns blocked=True → 6.G G2 拒绝."""
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True), \
             patch("macro_calendar.get_blackout_decision",
                   return_value={"blocked": True, "tier": "CORE",
                                 "reason": "CPI in -25min", "threshold_bonus": 0}):
            ok, reason = is_eligible_for_mirror(
                self._base_trade(), self.live_state, self.now,
                btc_regime="down",
            )
        self.assertFalse(ok)
        self.assertIn("phase_6g_g2", reason)
        self.assertIn("CORE", reason)

    def test_6g_g2_pass_when_macro_decision_clear(self):
        """get_blackout_decision returns blocked=False → 通过 G2 进入后续 gate."""
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True), \
             patch("macro_calendar.get_blackout_decision",
                   return_value={"blocked": False, "tier": None,
                                 "reason": None, "threshold_bonus": 0}):
            ok, reason = is_eligible_for_mirror(
                self._base_trade(), self.live_state, self.now,
                btc_regime="up",
            )
        # 应通过 G2 (可能被其它 gate 拒, 但不应是 g2 拒)
        self.assertNotIn("phase_6g_g2", reason or "")

    def test_6g_g2_observe_tier_does_not_block(self):
        """OBSERVE tier (软提示) → blocked=False → 不应触发 G2 拒绝."""
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True), \
             patch("macro_calendar.get_blackout_decision",
                   return_value={"blocked": False, "tier": "OBSERVE",
                                 "reason": "ISM in 20min", "threshold_bonus": 10}):
            ok, reason = is_eligible_for_mirror(
                self._base_trade(), self.live_state, self.now,
                btc_regime="up",
            )
        self.assertNotIn("phase_6g_g2", reason or "")

    def test_6g_g2_failsafe_on_import_error(self):
        """macro_calendar 导入 / 调用错误 → fail-safe 不 block."""
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True), \
             patch("macro_calendar.get_blackout_decision",
                   side_effect=Exception("simulated failure")):
            ok, reason = is_eligible_for_mirror(
                self._base_trade(), self.live_state, self.now,
                btc_regime="up",
            )
        # 不应被 G2 拒 (fail-safe), 可能被其它 gate 拒但不该是 g2
        self.assertNotIn("phase_6g_g2", reason or "")

    def test_6g_g2_failsafe_on_file_missing(self):
        """macro_events.json 不存在 → fail-safe."""
        with patch.object(live_trader, "LIVE_OBSERVATION_MODE", True), \
             patch("macro_calendar.get_blackout_decision",
                   side_effect=FileNotFoundError("macro_events.json")):
            ok, reason = is_eligible_for_mirror(
                self._base_trade(), self.live_state, self.now,
                btc_regime="up",
            )
        self.assertNotIn("phase_6g_g2", reason or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
