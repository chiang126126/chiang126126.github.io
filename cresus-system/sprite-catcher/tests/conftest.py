"""
测试 fixtures — 用 fake 实现替代真实数据源。

注意：fake 类不显式继承 Protocol，靠 duck typing。
这样可以一眼看清楚每个测试给出的"假数据"长什么样。
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from sprite_catcher.models import HolderSnapshot, TimeSeriesPoint, TransferEdge


class FakeCEXRegistry:
    """记录 CEX 热钱包和 burn/合约地址。"""

    def __init__(
        self,
        cex_wallets: set[str] | None = None,
        burns: set[str] | None = None,
    ):
        self.cex_wallets = set(cex_wallets or [])
        self.burns = set(burns or [])

    def is_cex_hot_wallet(self, address: str) -> bool:
        return address in self.cex_wallets

    def is_burn_or_contract(self, address: str) -> bool:
        return address in self.burns


class FakeTransferProvider:
    """给每个地址预设入金记录。"""

    def __init__(self, edges: dict[str, list[TransferEdge]] | None = None):
        self.edges = edges or {}

    def get_incoming_transfers(
        self, address: str, *, limit: int = 10
    ) -> list[TransferEdge]:
        return list(self.edges.get(address, []))[:limit]


class FakeHolderProvider:
    """返回固定的持有人列表 + 流通量。"""

    def __init__(self, holders: list[HolderSnapshot], supply: float):
        self.holders = holders
        self.supply = supply

    def get_holders(
        self, token: str, *, top_n: int = 200
    ) -> list[HolderSnapshot]:
        return list(self.holders[:top_n])

    def get_circulating_supply(self, token: str) -> float:
        return self.supply


class FakeOIProvider:
    """返回固定的 OI / vol / 时序数据。"""

    def __init__(
        self,
        *,
        oi_by_exchange: dict[str, float],
        vol_24h: float,
        oi_series: list[TimeSeriesPoint],
        price_series: list[TimeSeriesPoint],
        large_order_ratio: float,
    ):
        self._oi_by_exchange = oi_by_exchange
        self._vol_24h = vol_24h
        self._oi_series = oi_series
        self._price_series = price_series
        self._large_order_ratio = large_order_ratio

    def get_oi_by_exchange(self, symbol: str) -> dict[str, float]:
        return dict(self._oi_by_exchange)

    def get_vol_24h(self, symbol: str) -> float:
        return self._vol_24h

    def get_oi_series(
        self, symbol: str, *, hours: int
    ) -> list[TimeSeriesPoint]:
        return list(self._oi_series)

    def get_price_series(
        self, symbol: str, *, hours: int
    ) -> list[TimeSeriesPoint]:
        return list(self._price_series)

    def get_orderbook_large_order_ratio(self, symbol: str) -> float:
        return self._large_order_ratio


# === 通用 fixtures ===


@pytest.fixture
def base_ts() -> datetime:
    return datetime(2025, 1, 1, 0, 0, 0)


@pytest.fixture
def make_series(base_ts):
    """工厂：根据 values 列表生成等间距时间序列。"""

    def _factory(
        values: list[float], step: timedelta = timedelta(minutes=5)
    ) -> list[TimeSeriesPoint]:
        return [
            TimeSeriesPoint(ts=base_ts + i * step, value=v)
            for i, v in enumerate(values)
        ]

    return _factory


@pytest.fixture
def make_holders():
    """工厂：根据 (address, balance) 元组生成 HolderSnapshot 列表。"""

    def _factory(pairs: list[tuple[str, float]]) -> list[HolderSnapshot]:
        return [
            HolderSnapshot(address=addr, balance=Decimal(str(bal)))
            for addr, bal in pairs
        ]

    return _factory
