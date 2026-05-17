"""
datasets/library.py 测试。

覆盖：
- JSONL 加载 + 注释跳过
- 按 listing_date 升序排序
- 按 label / chain 筛选
- 三种 label 的样本都存在
- 必填字段缺失抛错
"""

import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from sprite_catcher.datasets.library import (
    SAMPLES_FILE,
    _parse_record,
    load_samples,
    samples_by_chain,
    samples_by_label,
)
from sprite_catcher.models import HistoricalSample, SampleLabel


def test_samples_file_loads():
    """随仓库提交的 samples.jsonl 必须可加载，且至少包含三类样本。"""
    samples = load_samples()
    assert len(samples) > 0
    labels = {s.label for s in samples}
    assert SampleLabel.FRIENDLY_LONG in labels
    assert SampleLabel.OPERATOR_SHORT in labels
    assert SampleLabel.AVOID in labels


def test_samples_sorted_by_listing_date():
    samples = load_samples()
    for i in range(1, len(samples)):
        assert samples[i].listing_date >= samples[i - 1].listing_date


def test_samples_by_label_friendly_includes_known_cases():
    longs = samples_by_label(SampleLabel.FRIENDLY_LONG)
    symbols = {s.token_symbol for s in longs}
    # 至少这几个研究里反复确认的应该在
    assert "ORDI" in symbols
    assert "WIF" in symbols
    assert "PEPE" in symbols


def test_samples_by_label_operator_includes_known_cases():
    shorts = samples_by_label(SampleLabel.OPERATOR_SHORT)
    symbols = {s.token_symbol for s in shorts}
    assert "MYX" in symbols
    assert "COAI" in symbols
    assert "RAVE" in symbols
    assert "LAB" in symbols


def test_samples_by_label_avoid_includes_known_cases():
    avoids = samples_by_label(SampleLabel.AVOID)
    symbols = {s.token_symbol for s in avoids}
    assert "TRB" in symbols
    assert "LUNA" in symbols
    assert "TRUMP" in symbols


def test_samples_by_chain_filter():
    sol_samples = samples_by_chain("SOL")
    assert all(s.chain.upper() == "SOL" for s in sol_samples)
    # 大小写不敏感
    sol_samples_lower = samples_by_chain("sol")
    assert len(sol_samples) == len(sol_samples_lower)


def test_operator_samples_have_archetype():
    """OPERATOR_SHORT 必须有 operator_archetype 标识打法类型。"""
    shorts = samples_by_label(SampleLabel.OPERATOR_SHORT)
    for s in shorts:
        assert s.operator_archetype, f"{s.token_symbol} 缺少 archetype"


def test_friendly_samples_have_no_archetype():
    """FRIENDLY_LONG 一般不需要 archetype（None 即可）。"""
    longs = samples_by_label(SampleLabel.FRIENDLY_LONG)
    for s in longs:
        assert s.operator_archetype is None, (
            f"{s.token_symbol}: FRIENDLY 不该有 archetype，"
            f"got {s.operator_archetype}"
        )


def test_pump_multiplier_consistency():
    """pump_multiplier 应该约等于 peak / base，允许 5% 误差（数据估算）。"""
    samples = load_samples()
    for s in samples:
        if s.base_low_usd <= 0:
            continue
        derived = s.peak_high_usd / s.base_low_usd
        ratio = s.pump_multiplier / derived
        assert 0.95 < ratio < 1.05, (
            f"{s.token_symbol}: pump_multiplier {s.pump_multiplier} "
            f"与 peak/base {derived:.2f} 偏差过大"
        )


def test_avoid_samples_have_zero_sustained():
    """除 LUNA（特殊：基本面崩，有过长期趋势但最终归零）外，
    AVOID 类样本的 sustained_pump_days 应该 = 0（爆炸式 / 上线即砸）。"""
    avoids = samples_by_label(SampleLabel.AVOID)
    for s in avoids:
        if s.operator_archetype == "DEATH_SPIRAL":
            continue   # LUNA 特例
        assert s.sustained_pump_days == 0, (
            f"{s.token_symbol}: AVOID 类样本应无可交易窗口"
        )


# === _parse_record (low-level) ===


def test_parse_record_required_field_missing():
    raw = {"token_symbol": "X"}  # 缺 chain 等
    with pytest.raises(KeyError):
        _parse_record(raw)


def test_parse_record_handles_null_optionals():
    raw = {
        "token_symbol": "X",
        "chain": "SOL",
        "listing_date": "2024-01-01T00:00:00",
        "peak_date": "2024-02-01T00:00:00",
        "end_of_window_date": "2024-03-01T00:00:00",
        "base_low_usd": 1.0,
        "peak_high_usd": 10.0,
        "end_price_usd": None,
        "pump_multiplier": 10.0,
        "sustained_pump_days": 30,
        "max_drawdown_during_pump": 0.4,
        "top10_share_at_peak": None,
        "binance_oi_share_at_peak": None,
        "vol_oi_ratio_at_peak": None,
        "label": "friendly_long",
        "operator_archetype": None,
        "notes": "",
        "sources": [],
    }
    s = _parse_record(raw)
    assert s.end_price_usd is None
    assert s.top10_share_at_peak is None
    assert s.label is SampleLabel.FRIENDLY_LONG


def test_load_skips_comments_and_blank_lines(tmp_path: Path):
    """加载器应该跳过 // 注释行和空行。"""
    f = tmp_path / "test.jsonl"
    f.write_text(
        "// header comment\n"
        "\n"
        '{"token_symbol":"AAA","chain":"SOL","listing_date":"2024-01-01T00:00:00",'
        '"peak_date":"2024-02-01T00:00:00","end_of_window_date":"2024-03-01T00:00:00",'
        '"base_low_usd":1.0,"peak_high_usd":10.0,"end_price_usd":5.0,'
        '"pump_multiplier":10.0,"sustained_pump_days":30,'
        '"max_drawdown_during_pump":0.4,"label":"friendly_long","notes":"","sources":[]}\n'
        "// trailing comment\n",
        encoding="utf-8",
    )
    samples = load_samples(f)
    assert len(samples) == 1
    assert samples[0].token_symbol == "AAA"


def test_load_raises_on_invalid_json(tmp_path: Path):
    f = tmp_path / "bad.jsonl"
    f.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_samples(f)
