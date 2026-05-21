"""
静态 CEX 热钱包 + burn 地址注册表。

实现 sprite_catcher.interfaces.CEXWalletRegistry Protocol。

维护原则：
- 只放已确认的 CEX 冷热钱包，不猜
- 定期从 Etherscan / BSCScan 的"labeled accounts"更新
- burn 地址只放 0x000...dead 系列
"""

from __future__ import annotations

# BSC + ETH + ARB 上已知 CEX 热钱包（全小写）
_CEX_HOT_WALLETS: frozenset[str] = frozenset({
    # Binance（BSC）
    "0x8894e0a0c962cb723c1976a4421c95949be2d4e3",
    "0xe2fc31f816a9b94326492132018c3aecc4a93ae1",
    "0xf977814e90da44bfa03b6295a0616a897441acec",
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be",
    "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8",
    "0x28c6c06298d514db089934071355e5743bf21d60",
    # OKX
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b",
    "0xaa1a3b7f76e27f897d4b0ea12f8c3e6c5c22c5ab",
    # Bybit
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40",
    # KuCoin
    "0x2b5634c42055806a59e9107ed44d43c426e58258",
    "0xd6216fc19db775df9774a6e33526131da7d19a2c",
    # Huobi / HTX
    "0xab5c66752a9e8167967685f1450532fb96d5d24f",
    "0x6748f50f686bfbca6fe8ad62b22228b87f31ff2b",
    # Gate.io
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe",
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2",
})

# burn 地址 + 已知 0 地址
_BURN_OR_CONTRACT: frozenset[str] = frozenset({
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
    "0xdead000000000000000042069420694206942069",
})


class StaticCEXWalletRegistry:
    """
    实现 sprite_catcher.interfaces.CEXWalletRegistry。

    is_burn_or_contract 只检查地址格式和已知列表，
    不做实时链上调用（避免延迟）。
    """

    def is_cex_hot_wallet(self, address: str) -> bool:
        return address.lower() in _CEX_HOT_WALLETS

    def is_burn_or_contract(self, address: str) -> bool:
        addr = address.lower()
        if addr in _BURN_OR_CONTRACT:
            return True
        # 全零地址（任意长度）
        stripped = addr.removeprefix("0x")
        return len(stripped) == 40 and all(c == "0" for c in stripped)
