# -*- coding: utf-8 -*-
import unittest

from radar.util import keccak256, selector, topic0, ema, parse_iso, hours_between
from radar.ai import AiJudge
from radar.evaluate import bootstrap_diff, group_stats
from radar.models import PoolSnapshot
from radar.screen import hard_filter
from radar.config import load_rules


class TestUtil(unittest.TestCase):
    def test_keccak(self):
        self.assertEqual(keccak256(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(selector("transfer(address,uint256)"), "0xa9059cbb")
        self.assertEqual(topic0("Transfer(address,address,uint256)"),
                         "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")

    def test_ema_and_time(self):
        self.assertAlmostEqual(ema([1, 2, 3, 4, 5], 3), 4.0, places=1)
        self.assertEqual(hours_between("2026-09-01T00:00:00Z", "2026-09-01T06:00:00Z"), 6.0)
        self.assertIsNotNone(parse_iso(1725000000))
        self.assertIsNotNone(parse_iso("1725000000000"))


class TestScreen(unittest.TestCase):
    def snap(self, **kw):
        s = PoolSnapshot(chain="robinhood", pool_address="0xpool", base_token="0xtok", base_symbol="T",
                         price_usd=1.0, liquidity_usd=kw.get("liq", 50_000), age_hours=kw.get("age", 5),
                         volume_usd={"h24": kw.get("vol", 200_000)}, price_change_pct={"h24": kw.get("chg", 50)},
                         txns={"h24": {"buys": 100, "sells": 80, "buyers": kw.get("buyers", 60), "sellers": 30}})
        return s

    def test_hard_filters(self):
        rules = load_rules()
        self.assertEqual(hard_filter(self.snap(), None, None, rules), [])
        self.assertIn("liquidity<15000", hard_filter(self.snap(liq=1000), None, None, rules))
        self.assertIn("too_new", hard_filter(self.snap(age=0.1), None, None, rules))
        self.assertIn("too_old", hard_filter(self.snap(age=100), None, None, rules))
        self.assertIn("vol/liq_too_high(wash?)", hard_filter(self.snap(liq=1000 * 20, vol=2_000_000), None, None, rules))
        self.assertIn("already_pumped_24h", hard_filter(self.snap(chg=5000), None, None, rules))


class TestAiParse(unittest.TestCase):
    def test_parse_json_in_text(self):
        v = AiJudge.parse('前言 {"verdict":"suspicious","confidence":"0.8","key_evidence":["a"],"red_flags":[],"what_would_change_mind":"x"} 后记')
        self.assertEqual(v.verdict, "SUSPICIOUS")
        self.assertEqual(v.confidence, 0.8)
        self.assertIsNone(AiJudge.parse("not json"))
        self.assertIsNone(AiJudge.parse('{"verdict":"WHATEVER"}'))


class TestEvaluate(unittest.TestCase):
    def test_group_stats_and_bootstrap(self):
        samples = [{"outcomes": {"h24": {"ret_pct": r, "max_ret_pct": r + 10}}, "status": "complete"} for r in (120, -90, 30, 60, -20)]
        gs = group_stats(samples, "h24", 50, -80)
        self.assertEqual(gs["n"], 5)
        self.assertAlmostEqual(gs["hit_rate"], 0.4)
        self.assertAlmostEqual(gs["rug_rate"], 0.2)
        d = bootstrap_diff([100] * 20 + [0] * 5, [0] * 20 + [100] * 5, 50, 300)
        self.assertGreater(d["diff"], 0)
        self.assertGreater(d["ci_low"], 0)


if __name__ == "__main__":
    unittest.main()
