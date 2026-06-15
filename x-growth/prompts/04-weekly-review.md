# Prompt · 周报复盘

**用途**：每周日跑一次，把上周数据喂给 Claude，输出可执行的下周调整。

**推荐模型**：Claude Opus 4.8

---

## 数据准备

每周日从 X Analytics 导出（或手抄）填到 `templates/weekly-review.md`：
- 本周新增粉丝
- 本周总 impression
- Top 5 推文（impression / like / reply / profile_visit / follow）
- Bottom 3 推文
- 本周 Reply 数量 + 估算带来的 profile visits
- 本周真实搞钱进展（affiliate 点击/成交、产品销量、实验结果）

---

## Prompt 本体

```
You're the growth + monetization analyst for an English X account
about making money with AI (edge = proof). Target pillar mix:
40% PROOF / 40% PLAYBOOK / 20% HOOK.

WEEK [N] DATA:
"""
[paste the filled-in weekly-review.md template]
"""

NORTH STAR for this phase:
[paste from playbook.md — e.g., "Day 30: 500 followers, 50K monthly
impressions, median tweet impression ≥ 200"]

Analyze and output:

1. **Verdict** — on track / behind / ahead. One sentence.

2. **Top 3 Wins** — which tweets/replies overperformed and WHY.
   Identify the exact pattern (hook type? pillar? topic? posting
   time? did proof/screenshots drive it?). Be specific with numbers.

3. **Bottom 3 Misses** — diagnose: hook problem? wrong pillar?
   bad time? format too long? no proof?

4. **Pillar Mix Audit** — actual vs target (40/40/20). Also compare
   median impression AND profile-visit-rate per pillar. If PROOF
   posts convert way better than PLAYBOOK, recommend rebalancing.

5. **Monetization Check** — any affiliate clicks / product sales /
   newsletter signups? Which content drove them? What to double down.

6. **Hook Library Update** — any new winning hook? Suggest the exact
   line to add to prompts/05-hook-library.md.

7. **Next Week's 3 Bets** — concrete content directions. Each:
   pillar, working title, why now, what real experiment backs it.

8. **One Thing to Stop Doing** — be ruthless, cut lowest-ROI behavior.

9. **Risk Flags** — impression drop, negative replies, anything
   that smells like the account drifting into guru/hype territory
   (which kills our proof-based trust).

Output: clean Markdown. Be brutally honest, not encouraging.
```

---

## 周报输出后的动作
1. 把 "Hook Library Update" 真加进 `05-hook-library.md`
2. 把 "Next Week's 3 Bets" 进入下周 backlog，并安排对应的真实实验
3. 把 "One Thing to Stop Doing" 标红，下周第一天就改
4. 周报归档到 `templates/weekly-reviews/week-NN.md`
