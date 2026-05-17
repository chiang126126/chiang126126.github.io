"""
筹码集中度 + Funder 去重。

输出 ChipFeatures：
- top10_share / top50_share
- cluster_factor（越大越同源 → 越像庄）

关键设计：BFS 资金图 ≤ 3 跳，遇到 CEX 热钱包/burn/合约就停止。
"""

from ..interfaces import CEXWalletRegistry, HolderProvider, TransferProvider
from ..models import ChipFeatures, HolderSnapshot


def _find_funder(
    address: str,
    transfer_provider: TransferProvider,
    cex_registry: CEXWalletRegistry,
    max_hops: int,
) -> str:
    """
    沿入金链向上追溯，找到资金源头地址。

    终止条件（按优先级）：
    1. 当前地址是 CEX 热钱包 / burn / 合约 → 返回当前地址作为 funder
    2. 当前地址没有任何入金记录 → 自己就是 funder
    3. 所有候选源都已访问过（环路保护） → 返回当前地址
    4. 达到 max_hops → 返回最远祖先

    简化：每跳只取第一个非 visited 的源。生产版本应按金额加权。
    """
    visited: set[str] = {address}
    current = address

    for _ in range(max_hops):
        # 1) 终止条件：碰到 CEX/burn/合约
        if cex_registry.is_cex_hot_wallet(current):
            return current
        if cex_registry.is_burn_or_contract(current):
            return current

        incoming = transfer_provider.get_incoming_transfers(current, limit=10)
        if not incoming:
            # 2) 没有入金记录
            return current

        # 3) 找下一跳：第一个未访问过的 source
        next_addr: str | None = None
        for edge in incoming:
            if edge.from_addr not in visited:
                next_addr = edge.from_addr
                break

        if next_addr is None:
            # 全部访问过（环路） → 停止
            return current

        visited.add(next_addr)
        current = next_addr

    # 4) 达到 max_hops
    return current


def funder_dedupe(
    holders: list[HolderSnapshot],
    transfer_provider: TransferProvider,
    cex_registry: CEXWalletRegistry,
    *,
    max_hops: int = 3,
) -> int:
    """
    对一组 holders 做 funder 去重，返回独立 funder cluster 数量。

    cluster_factor = len(holders) / clusters
    cluster_factor 越大 → 越同源 → 越可能是庄拆地址。
    """
    if not holders:
        return 0

    funders: set[str] = set()
    for holder in holders:
        funder = _find_funder(
            holder.address, transfer_provider, cex_registry, max_hops
        )
        funders.add(funder)
    return len(funders)


def compute_chip_features(
    token: str,
    holder_provider: HolderProvider,
    transfer_provider: TransferProvider,
    cex_registry: CEXWalletRegistry,
    *,
    funder_sample_size: int = 200,
    bfs_hops: int = 3,
) -> ChipFeatures:
    """
    计算筹码集中度的完整特征。

    步骤：
    1. 拉取 top funder_sample_size 个持有人
    2. 排除 LP / burn / 合约 / CEX 钱包（不是单一持有人）
    3. 计算 top10 / top50 占流通比
    4. 对剩余持有人做 funder 去重

    边界：
    - circulating <= 0 → raise ValueError（数据异常，不应静默吞掉）
    - 全部地址被排除 → cluster_factor=inf（说明这个池子是纯 LP）
    """
    raw_holders = holder_provider.get_holders(token, top_n=funder_sample_size)
    circulating = holder_provider.get_circulating_supply(token)

    if circulating <= 0:
        raise ValueError(
            f"circulating supply must be positive, got {circulating} "
            f"for token={token!r}"
        )

    # 排除非散户地址（LP/burn/合约/CEX）
    filtered: list[HolderSnapshot] = [
        h
        for h in raw_holders
        if not cex_registry.is_burn_or_contract(h.address)
        and not cex_registry.is_cex_hot_wallet(h.address)
    ]
    excluded_count = len(raw_holders) - len(filtered)

    if not filtered:
        return ChipFeatures(
            top10_share=0.0,
            top50_share=0.0,
            independent_clusters=0,
            cluster_factor=float("inf"),
            excluded_count=excluded_count,
        )

    sorted_holders = sorted(filtered, key=lambda h: h.balance, reverse=True)

    top10_sum = sum(float(h.balance) for h in sorted_holders[:10])
    top50_sum = sum(float(h.balance) for h in sorted_holders[:50])

    top10_share = top10_sum / circulating
    top50_share = top50_sum / circulating

    clusters = funder_dedupe(
        sorted_holders, transfer_provider, cex_registry, max_hops=bfs_hops
    )
    cluster_factor = (
        len(sorted_holders) / clusters if clusters > 0 else float("inf")
    )

    return ChipFeatures(
        top10_share=top10_share,
        top50_share=top50_share,
        independent_clusters=clusters,
        cluster_factor=cluster_factor,
        excluded_count=excluded_count,
    )
