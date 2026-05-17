"""
精灵捕手 — 数据源 Protocol 接口。

特征计算函数不直接调 HTTP / RPC，而是依赖这些 Protocol。
生产实现（Binance/Helius/Bitquery）和测试实现（Mock）各自实现这些接口。

为什么用 Protocol 而不是 ABC：
- duck typing 友好，不需要被实现类显式继承
- 测试用 fake 类无需多余的 `class FakeX(BaseX)` 声明
"""

from typing import Protocol

from .models import HolderSnapshot, TimeSeriesPoint, TransferEdge


class HolderProvider(Protocol):
    """提供 token 的持有人快照。"""

    def get_holders(self, token: str, *, top_n: int = 200) -> list[HolderSnapshot]:
        """按 balance 降序返回前 top_n 个持有人。"""
        ...

    def get_circulating_supply(self, token: str) -> float:
        """返回流通供应量。如果 <= 0 调用方应当抛错。"""
        ...


class TransferProvider(Protocol):
    """提供链上转账历史，用于 funder 追溯。"""

    def get_incoming_transfers(
        self, address: str, *, limit: int = 10
    ) -> list[TransferEdge]:
        """返回 address 收到的最近 limit 笔转账，from_addr 即资金源头。"""
        ...


class CEXWalletRegistry(Protocol):
    """识别 CEX 热钱包、burn 地址、合约地址。"""

    def is_cex_hot_wallet(self, address: str) -> bool: ...
    def is_burn_or_contract(self, address: str) -> bool: ...


class OIProvider(Protocol):
    """提供合约 OI、行情、订单簿质量。"""

    def get_oi_by_exchange(self, symbol: str) -> dict[str, float]:
        """返回各交易所的 OI（USD 等价），key 小写交易所名。"""
        ...

    def get_vol_24h(self, symbol: str) -> float:
        """24h 成交量（USD）。"""
        ...

    def get_oi_series(
        self, symbol: str, *, hours: int
    ) -> list[TimeSeriesPoint]:
        """OI 时间序列（按 ts 升序），通常 1 分钟或 5 分钟一个点。"""
        ...

    def get_price_series(
        self, symbol: str, *, hours: int
    ) -> list[TimeSeriesPoint]:
        """价格时间序列，时间戳与 get_oi_series 必须严格对齐。"""
        ...

    def get_orderbook_large_order_ratio(self, symbol: str) -> float:
        """大单（金额 > 中位数 ×10）占订单簿挂单总数的比例。0-1。"""
        ...
