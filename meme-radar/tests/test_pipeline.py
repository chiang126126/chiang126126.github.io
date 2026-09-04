# -*- coding: utf-8 -*-
"""端到端：合成链上跑完整 cycle，再把时间拨快 30h 回填结果。"""
import json
import os
import shutil
import time
import unittest
from pathlib import Path

from radar import util
from radar.config import ENV
from radar.pipeline import Pipeline
from tests.fakechain import NOW, build_fake_http, build_universe, all_overrides


class TestPipelineOffline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for p in util.DATA_DIR.glob("*"):
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        cls.tokens = build_universe()
        cls.fake = build_fake_http(cls.tokens)
        cls.p = Pipeline(http_overrides=all_overrides(cls.fake), verbose=False)
        cls.result = cls.p.cycle()

    def tok(self, sym):
        return next(t for t in self.tokens if t.symbol == sym)

    def cand(self, sym):
        return next((c for c in self.result["scan"]["candidates"] if c.symbol == sym), None)

    def test_regime(self):
        r = self.result["regime"]
        self.assertEqual(r["metrics"]["btc_trend"], "STRONG")
        self.assertEqual(r["regime"], "BTC_ONLY")
        self.assertAlmostEqual(r["risk_budget"], 0.35)
        self.assertTrue((util.DATA_DIR / "regime.json").exists())

    def test_universe_filtering(self):
        u = self.result["scan"]["universe"]
        symbols = {c.symbol for c in self.result["scan"]["candidates"]}
        self.assertNotIn("WETH", symbols)
        self.assertNotIn("DEADCOIN", symbols)
        self.assertNotIn("OLDIE", symbols)
        self.assertIn("GOODCAT", symbols)
        self.assertGreaterEqual(u["discovered"], 8)
        skipped = {c.symbol for c in self.result["scan"]["skip_candidates"]}
        self.assertIn("NEWBORN", skipped)      # 太新但流动性够 → 记录为 SKIP 样本
        self.assertNotIn("DEADCOIN", skipped)  # 流动性 < 3000 不进样本

    def test_healthy_token_scores_high_and_buys(self):
        c = self.cand("GOODCAT")
        self.assertIsNotNone(c)
        self.assertEqual(c.killed_by, [])
        self.assertEqual(c.forensics.quality, "full")
        self.assertLess(c.forensics.sybil_score, 0.2)
        self.assertEqual(c.forensics.launchpad, "pons_v2")
        self.assertEqual(c.forensics.curve_status, "graduated")
        self.assertIn("HOLDERS_LOOK_INDEPENDENT", c.flags["green"])
        self.assertGreaterEqual(c.score_total, 72, c.score_breakdown)
        self.assertEqual(c.decision, "PAPER_BUY", c.decision_reasons)
        self.assertAlmostEqual(c.position_size_usd, 3.5)
        self.assertEqual(self.p.ledger.open_count(), 1)

    def test_sybil_token_is_caught(self):
        c = self.cand("FAKEPUMP")
        self.assertIsNotNone(c)
        f = c.forensics
        self.assertGreaterEqual(len(f.clusters), 1)
        self.assertIn("same_funder", f.clusters[0]["reasons"])
        self.assertGreater(f.sybil_score, 0.6, f.to_dict())
        self.assertTrue("CLUSTER_CONTROLS_SUPPLY" in c.flags["red"] or "BUYS_CONCENTRATED" in c.flags["red"] or c.killed_by,
                        (c.flags, c.killed_by))
        self.assertNotEqual(c.decision, "PAPER_BUY")

    def test_plain_token_skipped_by_score(self):
        c = self.cand("MEHTOKEN")
        self.assertIsNotNone(c)
        self.assertEqual(c.decision, "SKIP", (c.score_total, c.decision_reasons))

    def test_outputs_written(self):
        for name in ("summary.json", "candidates.json", "watchlist.json", "evaluation.json", "ledger.jsonl", "positions.json"):
            self.assertTrue((util.DATA_DIR / name).exists(), name)
        summary = json.loads((util.DATA_DIR / "summary.json").read_text())
        self.assertEqual(summary["counts"]["paper_buy"], 1)
        self.assertGreaterEqual(summary["samples"]["total"], 5)
        self.assertGreaterEqual(summary["samples"]["by_decision"].get("BASELINE", 0), 1)
        self.assertTrue(list((util.DATA_DIR / "reports").glob("*.md")))
        self.assertEqual(self.result["errors"], [])

    def test_outcomes_after_30h(self):
        # 把样本与持仓的时间拨到 30h 前，再跑 outcomes
        p = self.p
        for s in p.ledger.samples:
            s["discovered_at"] = util.iso(util.parse_iso(NOW - 30 * 3600))
        for pos in p.ledger.positions["open"]:
            pos["opened_at"] = util.iso(util.parse_iso(NOW - 30 * 3600))
            pos["last_checked"] = pos["opened_at"]
        p.ledger.save()
        # FAKEPUMP 的池子在 30h 后流动性只剩 1k → RUG
        fp = self.tok("FAKEPUMP")
        fp.liq = 1000
        stats = p.run_outcomes()
        good = next(s for s in p.ledger.samples if s["symbol"] == "GOODCAT")
        self.assertIn("h24", good["outcomes"])
        self.assertGreater(good["outcomes"]["h24"]["ret_pct"], 100)
        self.assertGreater(good["outcomes"]["h24"]["max_ret_pct"], 150)
        bad = next(s for s in p.ledger.samples if s["symbol"] == "FAKEPUMP")
        self.assertEqual(bad["status"], "rug")
        self.assertLess(bad["outcomes"]["h24"]["ret_pct"], -80)
        # 模拟仓：翻倍触发 TP1，收回一半本金
        allpos = p.ledger.positions["open"] + p.ledger.positions["closed"]
        pos = next(x for x in allpos if x["symbol"] == "GOODCAT")
        self.assertIn(0, pos["tp_hit"])
        self.assertTrue(any(e["reason"] == "TP1" for e in pos["exits"]))
        self.assertGreater(pos["realized_usd"], 3.5 * 0.5 * 1.99)
        ev = p.run_evaluate()
        self.assertEqual(ev["verdict"], "insufficient")
        self.assertIn("sybil_score", ev["feature_buckets"])
        self.assertGreaterEqual(stats["outcomes"]["updated"], 2)


if __name__ == "__main__":
    unittest.main()
