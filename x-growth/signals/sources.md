# 信源清单 · Make Money with AI

每天早 8:00 用 Feedly / Inoreader 扫一遍，复制 24h 内容到 `01-signal-filter.md`。

---

## A · AI 工具 / 产品（PROOF + PLAYBOOK 弹药）

- Product Hunt daily（每天新 AI 工具 = 选题源）
- There's An AI For That (theresanaiforthat.com) newsletter
- Futurepedia 新工具
- Ben's Bites（工具 + 用法）
- The Rundown AI
- TLDR AI
- Future Tools (Matt Wolfe)
- Cursor / n8n / Make.com / Zapier changelog（自动化变现）
- Gumroad / Lemon Squeezy blog（卖数字产品）

## B · 大模型官方（HOOK 借势 — 新模型发布 = 搞钱新场景）

- OpenAI blog / @sama / @OpenAI
- Anthropic news / @AnthropicAI
- Google DeepMind / @GoogleDeepMind
- Meta AI
- 任何新模型发布 → 立刻想"普通人怎么用它赚钱"

## C · 搞钱 / 副业 / Indie（PROOF + PLAYBOOK）

- IndieHackers 每日 digest（真实 MRR 故事）
- @levelsio / @marc_louvion / @dannypostmaa 的最新 ship 和收入晒
- Starter Story（副业案例库）
- r/SideProject
- r/Entrepreneur
- r/juststart
- r/freelance（AI 接单需求）
- Upwork / Fiverr 趋势（哪些 AI 服务有人买）

## D · 经济焦虑（HOOK — 借大盘情绪）

- 主流财经头条：裁员数据、通胀、利率、就业（做"搞钱角度"二创，不做财经分析）
- @morganhousel（学财经叙事）
- layoffs.fyi（科技裁员追踪 → 强 HOOK）
- Reddit r/recruitinghell / r/jobs（就业焦虑 = 流量）

## E · X List "Money-AI Beat"（实时蹲守）

参考 `positioning.md` 标杆账号，订阅：
- @rowancheung @minchoi @heyBarsee @AlexFinnX
- @thejustinwelsh @gregisenberg
- @levelsio @marc_louvion @yoheinakajima @dannypostmaa
- @sama @AnthropicAI（借势）

---

## 信源管理 Tips
1. OPML 备份到 `signals/sources.opml`（隐私 OK 时）
2. Feedly 建 mute filter：过滤政治、荐股荐币、纯币圈噪音
3. 每月审计：删 30 天无贡献的信源，加新的
4. 周报记录"本周选题来自哪些信源"，砍掉零贡献的

## Phase B 自动化方向（Day 30+）
- Make.com：RSS → 拼接 → Claude API → markdown 输出到 Notion
- Python：`feedparser` + Anthropic SDK 自动跑信号过滤
- 早 8:00 cron 触发 → 结果发 Telegram bot
