# -*- coding: utf-8 -*-
"""cli.py — 命令行入口。绝不非零退出（除非 --strict），与仓库其它数据管道一致。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="meme-radar", description="Robinhood Chain 早期 MEME 资金雷达")
    ap.add_argument("cmd", choices=["cycle", "regime", "scan", "outcomes", "evaluate", "report", "import-wallets", "evidence", "selftest"])
    ap.add_argument("arg", nargs="?", help="import-wallets 的文件路径 / evidence 的代币地址")
    ap.add_argument("--offline", action="store_true", help="只读缓存，不发网络请求")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--max-forensics", type=int, default=None, help="本轮最多深挖多少个代币")
    ap.add_argument("--strict", action="store_true", help="有错误时非零退出")
    ap.add_argument("--data-dir", default=None, help="数据目录（默认 meme-radar/data；run.py 已提前处理）")
    args = ap.parse_args(argv)

    import os
    if args.offline:
        os.environ["RADAR_OFFLINE"] = "1"

    if args.cmd == "selftest":
        import unittest
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent.parent / "tests"))
        res = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if res.wasSuccessful() else 1

    from .pipeline import Pipeline
    p = Pipeline(verbose=args.verbose, max_forensics=args.max_forensics)
    if args.cmd == "cycle":
        p.cycle()
    elif args.cmd == "regime":
        p.cycle({"regime", "report"})
    elif args.cmd == "scan":
        p.cycle({"scan", "evaluate", "report"})
    elif args.cmd == "outcomes":
        p.cycle({"outcomes", "evaluate", "report"})
    elif args.cmd == "evaluate":
        p.cycle({"evaluate", "report"})
    elif args.cmd == "report":
        p.cycle({"report"})
    elif args.cmd == "import-wallets":
        if not args.arg:
            print("用法: import-wallets <json|csv>")
            return 2
        print(f"导入 {p.import_wallets(Path(args.arg))} 个钱包，库内共 {len(p.registry)} 个")
    elif args.cmd == "evidence":
        if not args.arg:
            print("用法: evidence <token_address>")
            return 2
        print(p.evidence_for(args.arg))
    if args.strict and p.errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
