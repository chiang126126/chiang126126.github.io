"""
数据新鲜度检查。

设计原则：信号绝不能用陈旧数据触发。每个时序输入都应过一道这里的检查，
否则可能用昨天的 OI 在今天的市场上做决策。

公开 API:
- is_fresh(series, now, max_age_seconds) → bool
- assert_fresh(series, now, max_age_seconds, label) → 抛 StaleDataError
"""

from datetime import datetime, timedelta

from ..models import Candle, TimeSeriesPoint


class StaleDataError(ValueError):
    """数据过期。strategy 层应捕获并跳过该 token 这一轮决策。"""


def _latest_ts(series: list) -> datetime | None:
    """series 可以是 list[TimeSeriesPoint] 或 list[Candle]，都有 .ts。"""
    if not series:
        return None
    return series[-1].ts


def is_fresh(
    series: list[TimeSeriesPoint] | list[Candle],
    now: datetime,
    max_age_seconds: int,
) -> bool:
    """
    最后一个数据点 ≤ max_age_seconds 之前 → 新鲜。
    序列为空 → 不新鲜（返回 False）。
    未来时间戳（series[-1].ts > now）→ 视为新鲜（数据源/系统时钟微差不应误判）。
    """
    latest = _latest_ts(series)
    if latest is None:
        return False
    age = (now - latest).total_seconds()
    return age <= max_age_seconds


def assert_fresh(
    series: list[TimeSeriesPoint] | list[Candle],
    now: datetime,
    max_age_seconds: int,
    label: str = "series",
) -> None:
    """
    断言版本：不新鲜抛 StaleDataError。
    便于在策略入口一行带说明地拒绝陈旧数据。
    """
    latest = _latest_ts(series)
    if latest is None:
        raise StaleDataError(f"{label}: empty series")
    age = (now - latest).total_seconds()
    if age > max_age_seconds:
        raise StaleDataError(
            f"{label}: last_ts={latest.isoformat()},age={age:.0f}s,"
            f"max={max_age_seconds}s"
        )
