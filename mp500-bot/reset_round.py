"""S轮次熔断复位：把 20% 总回撤 kill switch 的基线重置为当前权益。

背景：2026-08-01 权益 404.46 触发 总回撤≥20% 熔断（S0 规则：熔断=停机等人工检视）。
本工具是"人工检视后决定重启"的那个按钮——不重置历史、不改累计收益口径：
  · peak_equity → 当前权益（新一轮回撤从 0 起算，再跌 20% 会再次熔断）
  · day/day_start_equity → 今天/当前权益（当日亏损口径同步归零）
  · equity0(起始资金) 与 trades 历史保持不动，看板累计收益依旧如实显示

用法（在 Mac 上）：
  cd ~/mp500/mp500-bot && set -a && . ./.env && set +a && \
    DATA_DIR="$DATA_REPO/data" python3 reset_round.py
随后 git -C "$DATA_REPO" add data/ && git -C "$DATA_REPO" commit -m "S1 round reset" && git -C "$DATA_REPO" push
（不跑本工具，机器人会一直保持熔断停机——这也是一种合理选择。）
"""
import json
import os
from datetime import datetime, timezone

path = os.path.join(os.getenv("DATA_DIR", "./data"), "bot_state.json")
with open(path) as f:
    st = json.load(f)

old_peak, eq = st.get("peak_equity"), st["equity"]
dd = (1 - eq / old_peak) * 100 if old_peak else 0
st["peak_equity"] = eq
st["day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
st["day_start_equity"] = eq
st["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(path, "w") as f:
    json.dump(st, f, ensure_ascii=False, indent=1)

print(f"✅ 熔断已复位：峰值 {old_peak}（回撤 {dd:.1f}%）→ 新基线 {eq}")
print(f"   下一次熔断线：{eq * 0.8:.2f}（再回撤 20%）；权益与历史记录未做任何改动")
