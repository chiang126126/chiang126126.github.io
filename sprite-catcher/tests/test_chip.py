"""
chip.py 测试。

覆盖：
- top10 / top50 占比计算
- CEX/burn 地址排除
- circulating <= 0 抛错
- 全部地址被排除的边界
- funder 去重：独立链 / 共同源头 / 环路 / CEX 终止 / max_hops
"""

import math
from decimal import Decimal

import pytest

from sprite_catcher.features.chip import (
    _find_funder,
    compute_chip_features,
    funder_dedupe,
)
from sprite_catcher.models import HolderSnapshot, TransferEdge

from .conftest import FakeCEXRegistry, FakeHolderProvider, FakeTransferProvider


# === compute_chip_features ===


def test_chip_top10_share_basic(make_holders):
    """top10 = 100，circulating = 1000 → top10_share = 0.1"""
    holders = make_holders(
        [(f"addr{i}", 10.0) for i in range(20)]  # 20 个，每个 10
    )
    holder_prov = FakeHolderProvider(holders, supply=1000.0)
    transfer_prov = FakeTransferProvider()  # 无入金 → 每个 holder 自成 cluster
    cex_reg = FakeCEXRegistry()

    feat = compute_chip_features("tok", holder_prov, transfer_prov, cex_reg)

    assert feat.top10_share == pytest.approx(0.1)
    assert feat.top50_share == pytest.approx(0.2)
    assert feat.excluded_count == 0
    assert feat.independent_clusters == 20
    assert feat.cluster_factor == pytest.approx(1.0)


def test_chip_excludes_cex_and_burn(make_holders):
    """CEX 热钱包和 burn 地址都不计入 top 排序。"""
    holders = make_holders(
        [
            ("burn", 10000.0),
            ("binance_hot", 5000.0),
            ("alice", 100.0),
            ("bob", 50.0),
        ]
    )
    holder_prov = FakeHolderProvider(holders, supply=20000.0)
    transfer_prov = FakeTransferProvider()
    cex_reg = FakeCEXRegistry(cex_wallets={"binance_hot"}, burns={"burn"})

    feat = compute_chip_features("tok", holder_prov, transfer_prov, cex_reg)

    # 排除后只剩 alice (100) + bob (50)，top10_share = 150 / 20000
    assert feat.top10_share == pytest.approx(150 / 20000)
    assert feat.excluded_count == 2


def test_chip_circulating_zero_raises(make_holders):
    holders = make_holders([("a", 100.0)])
    holder_prov = FakeHolderProvider(holders, supply=0.0)
    transfer_prov = FakeTransferProvider()
    cex_reg = FakeCEXRegistry()

    with pytest.raises(ValueError, match="circulating supply"):
        compute_chip_features("tok", holder_prov, transfer_prov, cex_reg)


def test_chip_all_excluded_returns_inf_cluster(make_holders):
    """全部 holders 都是 LP/CEX → cluster_factor = inf。"""
    holders = make_holders([("burn", 100.0), ("lp_pool", 50.0)])
    holder_prov = FakeHolderProvider(holders, supply=1000.0)
    transfer_prov = FakeTransferProvider()
    cex_reg = FakeCEXRegistry(burns={"burn", "lp_pool"})

    feat = compute_chip_features("tok", holder_prov, transfer_prov, cex_reg)

    assert feat.top10_share == 0.0
    assert feat.top50_share == 0.0
    assert feat.independent_clusters == 0
    assert math.isinf(feat.cluster_factor)
    assert feat.excluded_count == 2


def test_chip_top10_share_extreme_concentration(make_holders):
    """单一地址占 95% → top10_share ≈ 0.95"""
    holders = make_holders([("whale", 9500.0), ("dust", 5.0)])
    holder_prov = FakeHolderProvider(holders, supply=10000.0)
    transfer_prov = FakeTransferProvider()
    cex_reg = FakeCEXRegistry()

    feat = compute_chip_features("tok", holder_prov, transfer_prov, cex_reg)
    assert feat.top10_share == pytest.approx(0.9505)


# === funder_dedupe ===


def test_funder_empty():
    assert (
        funder_dedupe([], FakeTransferProvider(), FakeCEXRegistry())
        == 0
    )


def test_funder_no_incoming_self_funded(make_holders):
    """没有任何入金 → 每个 holder 自己就是 funder → cluster = holder 数。"""
    holders = make_holders([("a", 1.0), ("b", 1.0), ("c", 1.0)])
    assert (
        funder_dedupe(holders, FakeTransferProvider(), FakeCEXRegistry())
        == 3
    )


def test_funder_same_root_merges():
    """3 个 holder 都从同一个 root 入金 → cluster = 1。"""
    holders = [
        HolderSnapshot("a", Decimal("1")),
        HolderSnapshot("b", Decimal("1")),
        HolderSnapshot("c", Decimal("1")),
    ]
    transfer_prov = FakeTransferProvider(
        {
            "a": [TransferEdge("root", "a")],
            "b": [TransferEdge("root", "b")],
            "c": [TransferEdge("root", "c")],
            # root 自己没有入金 → root 是 funder
        }
    )
    assert funder_dedupe(holders, transfer_prov, FakeCEXRegistry()) == 1


def test_funder_stops_at_cex():
    """入金链碰到 CEX 热钱包 → 以 CEX 地址作为 funder。"""
    transfer_prov = FakeTransferProvider(
        {
            "a": [TransferEdge("binance_hot", "a")],
        }
    )
    cex_reg = FakeCEXRegistry(cex_wallets={"binance_hot"})
    funder = _find_funder("a", transfer_prov, cex_reg, max_hops=3)
    # BFS: a 不是 CEX, 入金来自 binance_hot, 跳到 binance_hot
    # 下一轮: binance_hot 是 CEX → 返回 binance_hot
    assert funder == "binance_hot"


def test_funder_cycle_protection():
    """A → B → A 形成环 → BFS 不会无限循环。"""
    transfer_prov = FakeTransferProvider(
        {
            "a": [TransferEdge("b", "a")],
            "b": [TransferEdge("a", "b")],
        }
    )
    cex_reg = FakeCEXRegistry()
    funder = _find_funder("a", transfer_prov, cex_reg, max_hops=10)
    # a → b 后, b 的唯一入金是 a 但 a 已 visited → 返回 b
    assert funder == "b"


def test_funder_max_hops_respected():
    """链路长于 max_hops → 在 max_hops 处停下。"""
    transfer_prov = FakeTransferProvider(
        {
            "a": [TransferEdge("b", "a")],
            "b": [TransferEdge("c", "b")],
            "c": [TransferEdge("d", "c")],
            "d": [TransferEdge("e", "d")],
            "e": [TransferEdge("f", "e")],
            # f 没有入金
        }
    )
    cex_reg = FakeCEXRegistry()
    # max_hops=2: a → b → c, 返回 c
    funder = _find_funder("a", transfer_prov, cex_reg, max_hops=2)
    assert funder == "c"
    # max_hops=10: 应走到 f (无入金的终点)
    funder_full = _find_funder("a", transfer_prov, cex_reg, max_hops=10)
    assert funder_full == "f"


def test_funder_two_independent_chains():
    """两条不重叠的链 → cluster = 2。"""
    holders = [HolderSnapshot("a", Decimal("1")), HolderSnapshot("x", Decimal("1"))]
    transfer_prov = FakeTransferProvider(
        {
            "a": [TransferEdge("ra", "a")],
            "x": [TransferEdge("rx", "x")],
        }
    )
    assert funder_dedupe(holders, transfer_prov, FakeCEXRegistry()) == 2


def test_funder_starts_at_cex_address():
    """如果 holder 本身就是 CEX 热钱包 → 自己作为 funder。"""
    transfer_prov = FakeTransferProvider()
    cex_reg = FakeCEXRegistry(cex_wallets={"a"})
    assert _find_funder("a", transfer_prov, cex_reg, max_hops=3) == "a"
