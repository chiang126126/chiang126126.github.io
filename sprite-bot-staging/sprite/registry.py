"""
代币注册表：Binance 合约 symbol → 链上 token_id。

为什么需要这个映射：
  - L2/L3 链上分析需要 "{CHAIN}:{contract}" 格式
  - Binance OI/价格数据需要 "MYXUSDT" 格式
  - 两者没有直接 API 可以互转，需要手动维护或定期同步

维护方式：
  - 当扫描发现新的妖币候选时，先查此表
  - 查不到 → 告警 + 跳过链上分析，仅使用 OI 信号
  - 查到 → 走完整 L2/L3 管道

初始版本手动维护。后续可接入 CoinGecko API 自动同步
（GET /coins/{id}/contract/{contract_address}）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenInfo:
    binance_symbol: str   # "MYXUSDT"
    chain: str            # "BSC" | "ETH" | "ARB" | "BASE"
    contract: str         # "0x..."

    @property
    def token_id(self) -> str:
        """sprite_catcher 接口期望的格式。"""
        return f"{self.chain}:{self.contract}"


# 已知妖币候选的合约地址
# 来源：Binance 新上合约公告 + CoinGecko
_REGISTRY: dict[str, TokenInfo] = {
    # 历史样本中的 CEX 币
    "MYXUSDT": TokenInfo("MYXUSDT", "ARB", "0x2129f36f07604c8a8c9a61804e01c6d9e9fe7f7f"),
    "COAIUSDT": TokenInfo("COAIUSDT", "BSC", "0x4cfe225ce54c6609a525768b13b7c8c0a3f0ee5d"),
    "AIAUSDT": TokenInfo("AIAUSDT", "BSC", "0x6c3779a33b6200e5d28cf7a0ea9a8e2a18e9d29e"),
    "ZKJUSDT": TokenInfo("ZKJUSDT", "BSC", "0x1a7e4e63778b4f12a199c062f3efdd288afcbce8"),
    "KOGEUSDT": TokenInfo("KOGEUSDT", "BSC", "0xe6df0bb08e5a97b40b21950a0a51b94c4dba0b5e"),
    "RAVEUSDT": TokenInfo("RAVEUSDT", "ETH", "0x5afe3855358e112b5647b952709e6165e1c1eeee"),
    "VIRTUALUSDT": TokenInfo("VIRTUALUSDT", "BASE", "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"),
    "DOGEUSDT": TokenInfo("DOGEUSDT", "ETH", "0x3832d2f059e55934220881f831be501d180671a7"),
    "SHIBUSDT": TokenInfo("SHIBUSDT", "ETH", "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce"),
    "PEPEUSDT": TokenInfo("PEPEUSDT", "ETH", "0x6982508145454ce325ddbe47a25d4ec3d2311933"),
}


def lookup(binance_symbol: str) -> TokenInfo | None:
    """查询合约 symbol 对应的链上信息。找不到返回 None。"""
    return _REGISTRY.get(binance_symbol.upper())


def register(info: TokenInfo) -> None:
    """动态注册新代币（运行时发现新目标时调用）。"""
    _REGISTRY[info.binance_symbol.upper()] = info


def all_known_symbols() -> list[str]:
    return list(_REGISTRY.keys())
