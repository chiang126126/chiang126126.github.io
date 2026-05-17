"""
历史样本库加载器。

数据存在 `samples.jsonl`（每行一个 JSON 对象），方便人工编辑、git diff 友好、
按行追加新样本无需 schema 迁移。

公开 API：
- load_samples()           加载全部样本（按 rally_start_date 升序）
- samples_by_label(label)  按标签筛选
- samples_by_chain(chain)  按链筛选

注意：本模块只负责加载与基本筛选，不做统计分析。回测脚本应该消费这些样本，
和实时数据源串联，自己跑 walk-forward。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import HistoricalSample, SampleLabel

SAMPLES_FILE: Path = Path(__file__).parent / "samples.jsonl"


def _parse_record(raw: dict) -> HistoricalSample:
    """把 JSONL 一行的 dict 转成 HistoricalSample。

    严格校验：必填字段缺失 → KeyError；时间格式错误 → ValueError。
    可选字段（end_price_usd / top10_share_at_peak 等）允许 null。
    """
    return HistoricalSample(
        token_symbol=raw["token_symbol"],
        chain=raw["chain"],
        rally_start_date=datetime.fromisoformat(raw["rally_start_date"]),
        peak_date=datetime.fromisoformat(raw["peak_date"]),
        end_of_window_date=datetime.fromisoformat(raw["end_of_window_date"]),
        base_low_usd=float(raw["base_low_usd"]),
        peak_high_usd=float(raw["peak_high_usd"]),
        end_price_usd=(
            float(raw["end_price_usd"])
            if raw.get("end_price_usd") is not None
            else None
        ),
        pump_multiplier=float(raw["pump_multiplier"]),
        sustained_pump_days=int(raw["sustained_pump_days"]),
        max_drawdown_during_pump=float(raw["max_drawdown_during_pump"]),
        top10_share_at_peak=raw.get("top10_share_at_peak"),
        binance_oi_share_at_peak=raw.get("binance_oi_share_at_peak"),
        vol_oi_ratio_at_peak=raw.get("vol_oi_ratio_at_peak"),
        label=SampleLabel(raw["label"]),
        operator_archetype=raw.get("operator_archetype"),
        notes=raw.get("notes", ""),
        sources=tuple(raw.get("sources", [])),
    )


def load_samples(path: Path | None = None) -> list[HistoricalSample]:
    """加载全部样本，按 rally_start_date 升序。"""
    file = path or SAMPLES_FILE
    samples: list[HistoricalSample] = []
    with file.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{file}:{line_no}: invalid JSON — {e}"
                ) from e
            samples.append(_parse_record(raw))
    samples.sort(key=lambda s: s.rally_start_date)
    return samples


def samples_by_label(
    label: SampleLabel, samples: list[HistoricalSample] | None = None
) -> list[HistoricalSample]:
    src = samples if samples is not None else load_samples()
    return [s for s in src if s.label is label]


def samples_by_chain(
    chain: str, samples: list[HistoricalSample] | None = None
) -> list[HistoricalSample]:
    src = samples if samples is not None else load_samples()
    chain_upper = chain.upper()
    return [s for s in src if s.chain.upper() == chain_upper]
