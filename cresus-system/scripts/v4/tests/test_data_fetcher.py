"""V4 data_fetcher tests — 全部用 mock, 不打真实 API."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v4_data_fetcher import (
    TIMEFRAMES, KLINES_MAX_LIMIT,
    PROTOTYPE_MAINSTREAM, V3_LIVE_WHITELIST, V3_LIVE_BLACKLIST,
    list_v3_symbols, list_prototype_symbols, list_default_universe,
    _ms, _http_get,
    fetch_klines, klines_to_df, save_klines_parquet, load_klines,
    fetch_funding, funding_to_df,
    fetch_oi_hist, oi_to_df,
    fetch_taker_ratio, taker_to_df,
    download_one_symbol, download_all,
)
import v4_data_fetcher as df_mod


# ── Mock 工具 ───────────────────────────────────────────────────────

def _mock_kline_row(open_time_ms: int, close: float = 100.0, volume: float = 1000.0):
    """构造一行 Binance kline 原始格式 (12 元素 list)."""
    return [
        open_time_ms,              # 0 open_time
        "99.0",                    # 1 open
        "101.0",                   # 2 high
        "98.0",                    # 3 low
        f"{close}",                # 4 close
        f"{volume}",               # 5 volume
        open_time_ms + 60000 - 1,  # 6 close_time
        "100000.0",                # 7 quote_volume
        500,                       # 8 trade_count
        "600.0",                   # 9 taker_buy_base
        "60000.0",                 # 10 taker_buy_quote
        "0",                       # 11 ignore
    ]


# ── list_v3_symbols ─────────────────────────────────────────────────

def test_list_v3_symbols_extracts_unique_sorted():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "recent_closed": [
                {"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "BTCUSDT"},
            ],
            "open_trades": [{"symbol": "SOLUSDT"}],
        }, f)
        path = Path(f.name)
    try:
        assert list_v3_symbols(path) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    finally:
        path.unlink()


def test_list_v3_symbols_handles_missing_buckets():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"recent_closed": [{"symbol": "BTCUSDT"}]}, f)
        path = Path(f.name)
    try:
        assert list_v3_symbols(path) == ["BTCUSDT"]
    finally:
        path.unlink()


def test_list_v3_symbols_skips_null_symbol():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"recent_closed": [{"symbol": None}, {"symbol": "BTCUSDT"}]}, f)
        path = Path(f.name)
    try:
        assert list_v3_symbols(path) == ["BTCUSDT"]
    finally:
        path.unlink()


# ── 常量 ────────────────────────────────────────────────────────────

def test_timeframes_constant():
    assert TIMEFRAMES == ("15m", "1h", "4h", "1d")


def test_ms_helper():
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _ms(dt) == 1767225600000


# ── Prototype symbol 集合 ──────────────────────────────────────────

def test_prototype_mainstream_has_10():
    assert len(PROTOTYPE_MAINSTREAM) == 10
    # 关键 liquid: BTC ETH SOL 必须在
    assert "BTCUSDT" in PROTOTYPE_MAINSTREAM
    assert "ETHUSDT" in PROTOTYPE_MAINSTREAM
    assert "SOLUSDT" in PROTOTYPE_MAINSTREAM


def test_v3_live_whitelist_subset_of_mainstream():
    """V3 live whitelist (BTC/ETH/SOL) 应该是 mainstream 子集."""
    assert set(V3_LIVE_WHITELIST).issubset(set(PROTOTYPE_MAINSTREAM))


def test_v3_live_blacklist_has_5():
    """V3 live blacklist (PHASE_NOTES 5 个 0 胜 symbol)."""
    assert len(V3_LIVE_BLACKLIST) == 5
    assert "DODOXUSDT" in V3_LIVE_BLACKLIST
    assert "STABLEUSDT" in V3_LIVE_BLACKLIST


def test_list_prototype_symbols_15_unique():
    """10 mainstream + 5 blacklist = 15 (无重叠)."""
    syms = list_prototype_symbols()
    assert len(syms) == 15
    assert len(set(syms)) == 15  # 无重复
    assert syms == sorted(syms)  # 已排序


def test_list_default_universe_includes_mainstream(tmp_path):
    """V4 默认 universe 必须含主流币 (V3 paper 不含主流币的 bug 修复)."""
    history_path = tmp_path / "paper.json"
    history_path.write_text(json.dumps({
        "recent_closed": [
            {"symbol": "RANDOMUSDT"}, {"symbol": "MEMEUSDT"},
        ],
        "open_trades": [],
    }))
    syms = list_default_universe(history_path)
    # V3 paper 抽到 2 个 + 10 mainstream + 5 blacklist
    assert "BTCUSDT" in syms
    assert "ETHUSDT" in syms
    assert "SOLUSDT" in syms
    assert "RANDOMUSDT" in syms
    assert "MEMEUSDT" in syms
    assert "DODOXUSDT" in syms  # V3 blacklist
    assert syms == sorted(syms)
    # 排重: 2 + 10 + 5 = 17 unique (无 overlap 时)
    assert len(syms) == 17


# ── klines_to_df ────────────────────────────────────────────────────

def test_klines_to_df_empty():
    df = klines_to_df([])
    assert df.empty


def test_klines_to_df_types_and_index():
    rows = [_mock_kline_row(1735689600000), _mock_kline_row(1735689660000, close=200.0)]
    df = klines_to_df(rows)
    assert len(df) == 2
    assert df.index.name == "open_time"
    assert df.index.tz is not None
    assert df["close"].dtype == float
    assert df["trade_count"].dtype.kind in "iu"
    assert df.iloc[1]["close"] == 200.0
    assert "_ignore" not in df.columns


def test_klines_to_df_deduplicates_overlap():
    # 同一 open_time 出现两次 (分页边界) → 保留第一行
    t = 1735689600000
    rows = [_mock_kline_row(t, close=100.0), _mock_kline_row(t, close=999.0)]
    df = klines_to_df(rows)
    assert len(df) == 1
    assert df.iloc[0]["close"] == 100.0


def test_klines_to_df_sorts_ascending():
    rows = [_mock_kline_row(1735689660000, close=200.0),
            _mock_kline_row(1735689600000, close=100.0)]
    df = klines_to_df(rows)
    assert df.iloc[0]["close"] == 100.0
    assert df.iloc[1]["close"] == 200.0


# ── 分页逻辑 ────────────────────────────────────────────────────────

def test_fetch_klines_paginates_when_chunk_full():
    """Mock 两次响应: 第一次 1500 行 (满) → 应该继续拉; 第二次 100 行 → 停."""
    calls = {"n": 0}

    def mock_chunk(symbol, interval, start_ms, end_ms):
        calls["n"] += 1
        if calls["n"] == 1:
            base = start_ms
            return [_mock_kline_row(base + i * 60000) for i in range(KLINES_MAX_LIMIT)]
        elif calls["n"] == 2:
            base = start_ms
            return [_mock_kline_row(base + i * 60000) for i in range(100)]
        return []

    with patch.object(df_mod, "_klines_chunk", side_effect=mock_chunk):
        rows = fetch_klines("BTCUSDT", "1m", 1735689600000, 1735900000000)
    assert calls["n"] == 2
    assert len(rows) == KLINES_MAX_LIMIT + 100


def test_fetch_klines_stops_when_chunk_short():
    """单次返回 < KLINES_MAX_LIMIT 应该停."""
    def mock_chunk(symbol, interval, start_ms, end_ms):
        return [_mock_kline_row(start_ms + i * 60000) for i in range(50)]

    with patch.object(df_mod, "_klines_chunk", side_effect=mock_chunk):
        rows = fetch_klines("BTCUSDT", "1m", 1735689600000, 1735900000000)
    assert len(rows) == 50


def test_fetch_klines_stops_on_empty():
    with patch.object(df_mod, "_klines_chunk", return_value=[]):
        rows = fetch_klines("BTCUSDT", "1m", 1735689600000, 1735900000000)
    assert rows == []


# ── parquet round-trip ──────────────────────────────────────────────

def test_klines_parquet_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(df_mod, "V4_KLINES_DIR", tmp_path)
    rows = [_mock_kline_row(1735689600000 + i * 60000, close=100 + i) for i in range(10)]
    df = klines_to_df(rows)
    path = save_klines_parquet("BTCUSDT", "1m", df)
    assert path.exists()
    df2 = load_klines("BTCUSDT", "1m")
    assert len(df2) == 10
    assert df2.iloc[0]["close"] == 100.0
    assert df2.iloc[9]["close"] == 109.0


def test_load_klines_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(df_mod, "V4_KLINES_DIR", tmp_path)
    df = load_klines("NONEXISTENT", "1h")
    assert df.empty


# ── funding_to_df ──────────────────────────────────────────────────

def test_funding_to_df_typical():
    rows = [
        {"symbol": "BTCUSDT", "fundingTime": 1735689600000,
         "fundingRate": "0.0001", "markPrice": "100000.5"},
        {"symbol": "BTCUSDT", "fundingTime": 1735718400000,
         "fundingRate": "-0.0005", "markPrice": "100100.0"},
    ]
    df = funding_to_df(rows)
    assert len(df) == 2
    assert df.iloc[0]["funding_rate"] == 0.0001
    assert df.iloc[1]["funding_rate"] == -0.0005
    assert df.iloc[0]["mark_price"] == 100000.5
    assert df.index.name == "funding_time"


def test_funding_to_df_missing_mark_price():
    """Binance 某些情况下不返回 markPrice — 应该 fallback to 0."""
    rows = [{"symbol": "BTCUSDT", "fundingTime": 1735689600000, "fundingRate": "0.0001"}]
    df = funding_to_df(rows)
    assert df.iloc[0]["mark_price"] == 0.0


def test_funding_to_df_empty():
    assert funding_to_df([]).empty


# ── oi_to_df ────────────────────────────────────────────────────────

def test_oi_to_df_typical():
    rows = [
        {"symbol": "BTCUSDT", "timestamp": 1735689600000,
         "sumOpenInterest": "12345.6", "sumOpenInterestValue": "1234567890.0"},
        {"symbol": "BTCUSDT", "timestamp": 1735704000000,
         "sumOpenInterest": "12500.0", "sumOpenInterestValue": "1250000000.0"},
    ]
    df = oi_to_df(rows)
    assert len(df) == 2
    assert df.iloc[0]["sum_oi"] == 12345.6
    assert df.iloc[1]["sum_oi_value"] == 1250000000.0


def test_oi_to_df_empty():
    assert oi_to_df([]).empty


# ── taker_to_df ─────────────────────────────────────────────────────

def test_taker_to_df_typical():
    rows = [
        {"symbol": "BTCUSDT", "timestamp": 1735689600000,
         "buySellRatio": "1.2345", "buyVol": "600.0", "sellVol": "400.0"},
    ]
    df = taker_to_df(rows)
    assert len(df) == 1
    assert df.iloc[0]["buy_sell_ratio"] == 1.2345
    assert df.iloc[0]["buy_vol"] == 600.0
    assert df.iloc[0]["sell_vol"] == 400.0
    # 派生字段: taker_buy_ratio = buy / (buy+sell) = 0.6
    assert df.iloc[0]["taker_buy_ratio"] == pytest.approx(0.6)


def test_taker_to_df_zero_volume_handled():
    """buy_vol + sell_vol = 0 → taker_buy_ratio = NaN (不 crash)."""
    rows = [{"timestamp": 1735689600000, "buySellRatio": "0",
             "buyVol": "0", "sellVol": "0"}]
    df = taker_to_df(rows)
    assert pd.isna(df.iloc[0]["taker_buy_ratio"])


def test_taker_to_df_empty():
    assert taker_to_df([]).empty


# ── download_one_symbol orchestration ────────────────────────────────

def test_download_one_symbol_collects_ok_and_failed(tmp_path, monkeypatch):
    """Mock 全部 fetch_*: 1h K 线 OK, 4h 失败, funding OK."""
    monkeypatch.setattr(df_mod, "V4_KLINES_DIR", tmp_path / "k")
    monkeypatch.setattr(df_mod, "V4_FUNDING_DIR", tmp_path / "f")
    monkeypatch.setattr(df_mod, "V4_OI_DIR", tmp_path / "o")
    monkeypatch.setattr(df_mod, "V4_TAKER_DIR", tmp_path / "t")

    def mock_fetch_klines(sym, tf, s, e):
        if tf == "4h":
            raise RuntimeError("simulated failure")
        return [_mock_kline_row(s + i * 60000) for i in range(3)]

    def mock_fetch_funding(sym, s, e):
        return [{"fundingTime": s, "fundingRate": "0.0001", "markPrice": "100"}]

    def mock_fetch_oi(sym, s, e, interval="4h"):
        return []  # OI 30 日限制可能空 → skipped

    def mock_fetch_taker(sym, s, e, interval="4h"):
        return [{"timestamp": s, "buySellRatio": "1.0", "buyVol": "100", "sellVol": "100"}]

    monkeypatch.setattr(df_mod, "fetch_klines", mock_fetch_klines)
    monkeypatch.setattr(df_mod, "fetch_funding", mock_fetch_funding)
    monkeypatch.setattr(df_mod, "fetch_oi_hist", mock_fetch_oi)
    monkeypatch.setattr(df_mod, "fetch_taker_ratio", mock_fetch_taker)

    r = download_one_symbol("BTCUSDT", 1735689600000, 1735700000000,
                            timeframes=("15m", "1h", "4h", "1d"))
    # 15m / 1h / 1d 应该 ok, 4h 应该 failed
    assert "klines_15m" in r["ok"]
    assert "klines_1h" in r["ok"]
    assert "klines_1d" in r["ok"]
    assert ("klines_4h", "simulated failure") in r["failed"]
    assert "funding" in r["ok"]
    assert "oi" in r["skipped"]
    assert "taker" in r["ok"]
    assert r["rows"]["klines_1h"] == 3
    assert r["rows"]["funding"] == 1


def test_download_one_symbol_total_failure_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(df_mod, "V4_KLINES_DIR", tmp_path / "k")
    monkeypatch.setattr(df_mod, "V4_FUNDING_DIR", tmp_path / "f")
    monkeypatch.setattr(df_mod, "V4_OI_DIR", tmp_path / "o")
    monkeypatch.setattr(df_mod, "V4_TAKER_DIR", tmp_path / "t")

    def crash(*a, **k):
        raise RuntimeError("API dead")

    monkeypatch.setattr(df_mod, "fetch_klines", crash)
    monkeypatch.setattr(df_mod, "fetch_funding", crash)
    monkeypatch.setattr(df_mod, "fetch_oi_hist", crash)
    monkeypatch.setattr(df_mod, "fetch_taker_ratio", crash)

    r = download_one_symbol("BTCUSDT", 1735689600000, 1735700000000)
    assert r["ok"] == []
    assert len(r["failed"]) == 4 + 3  # 4 timeframe K + funding/oi/taker = 7


# ── _http_get retry logic ────────────────────────────────────────────

def test_http_get_retries_on_429(monkeypatch):
    """模拟 429 → 重试 → 成功."""
    calls = {"n": 0}

    class MockResp:
        def __init__(self, status):
            self.status_code = status
            self.text = "rate limited"
        def raise_for_status(self):
            if self.status_code >= 400:
                raise __import__("requests").HTTPError(f"{self.status_code}")
        def json(self):
            return {"ok": True}

    def mock_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return MockResp(429)
        return MockResp(200)

    monkeypatch.setattr(df_mod.requests, "get", mock_get)
    monkeypatch.setattr(df_mod, "THROTTLE_SEC", 0.0)
    monkeypatch.setattr(df_mod, "INITIAL_BACKOFF_SEC", 0.0)

    result = _http_get("/test", {})
    assert result == {"ok": True}
    assert calls["n"] == 3


def test_http_get_raises_after_max_retries(monkeypatch):
    class MockResp:
        status_code = 500
        text = "server error"
        def raise_for_status(self):
            raise __import__("requests").HTTPError("500")
        def json(self): return {}

    monkeypatch.setattr(df_mod.requests, "get", lambda *a, **k: MockResp())
    monkeypatch.setattr(df_mod, "THROTTLE_SEC", 0.0)
    monkeypatch.setattr(df_mod, "INITIAL_BACKOFF_SEC", 0.0)
    monkeypatch.setattr(df_mod, "MAX_RETRIES", 1)

    with pytest.raises(Exception):
        _http_get("/test", {})
