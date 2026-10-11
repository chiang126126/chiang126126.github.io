#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meme-radar 入口：python meme-radar/run.py cycle [--verbose] [--offline] [--data-dir DIR]"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# --data-dir 必须在导入 radar 之前生效（DATA_DIR 在导入时确定）
if "--data-dir" in sys.argv:
    i = sys.argv.index("--data-dir")
    if i + 1 < len(sys.argv):
        os.environ["RADAR_DATA_DIR"] = str(Path(sys.argv[i + 1]).resolve())

from radar.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
