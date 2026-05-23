"""V4 data_fetcher tests."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v4_data_fetcher import list_v3_symbols, TIMEFRAMES


def test_list_v3_symbols_extracts_unique_sorted():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "recent_closed": [
                {"symbol": "BTCUSDT", "id": "1"},
                {"symbol": "ETHUSDT", "id": "2"},
                {"symbol": "BTCUSDT", "id": "3"},   # dup
            ],
            "open_trades": [
                {"symbol": "SOLUSDT", "id": "4"},
            ],
        }, f)
        path = Path(f.name)
    try:
        result = list_v3_symbols(path)
        assert result == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
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


def test_timeframes_constant():
    assert TIMEFRAMES == ("1h", "4h", "1d")
