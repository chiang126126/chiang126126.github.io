# 信源清单 · 海外搞钱案例拆解

每天早 8:00 用 Feedly / Inoreader 扫一遍，复制 24h 内容到 `01-signal-filter.md`。
**目标**：找有"具体收入数字 + 操作路径 + 时间线"的真实案例。

---

## A · 案例金矿（最高优先级）

### Indie Hackers（独立开发者社区）
- 价值：搞钱思路顶级宝库，真实 Revenue + 实操复盘
- 抓取重点：**Milestone（里程碑，如 $10k MRR）标签** + 高赞访谈
- 入口：indiehackers.com/products（按 revenue 排序）、/interviews、Milestones 板块
- RSS：indiehackers.com 部分板块有 feed；无 feed 的用 RSSHub / Kill the Newsletter

### Starter Story（赚钱项目访谈）
- 价值：故事性极强，详披露起步 + 第一批用户 + 利润，最适合转 thread/小红书
- 抓取重点：**最新创业者访谈案例**
- 入口：starterstory.com/case-studies
- RSS：starterstory.com/feed（或 RSSHub）

### 海外搞钱 / 增长大 V（推文 RSS）
- @levelsio（独立开发、晒真实收入）
- @thejustinwelsh（一人公司、solopreneur）
- @thedankoe（搞钱认知、个人品牌）
- @marc_louvion（ship fast、晒 MRR）
- @dannypostmaa（build in public 收入透明）
- @yongfook（独立 SaaS 复盘）
- @gregisenberg（副业点子库）
- 抓取：Nitter / RSSHub（twitter/user/levelsio）→ Feedly，再 AI 汉化拆解

### Reddit（高赞痛点 + 干货）
- r/Entrepreneur、r/SideProject、r/SaaS、r/juststart、r/EntrepreneurRideAlong
- 抓取重点：**过去 24h 点赞 >500 的 Top Posts**
- RSS：reddit.com/r/SaaS/top/.rss?t=day（各 sub 同理）

---

## B · 补充案例源

- Starter Story Money（细分赚钱方式）
- Failory（失败/复盘案例，反面教材也是好选题）
- MicroConf / MicroAcquire 博客（小型收购、估值数据）
- Trends.vc / Greg Isenberg 的 idea 库
- Product Hunt（新产品 → 追踪它后续 MRR）
- Hacker News "Show HN" + "Ask HN: how do you make money"

## C · 热点 / 情绪借势（HOOK 柱）

- layoffs.fyi（科技裁员追踪 → 强情绪 HOOK）
- 海外 AI 工具新闻（Ben's Bites / The Rundown）→ "普通人怎么用它赚钱"
- 经济头条（通胀/就业）→ 搞钱角度二创（不做财经分析）

## D · 中文蹲守（reply 涨粉，不抓内容）

- 中文 X 出海/独立开发/副业大 V（生财、出海、indie 圈）
- 蹲守他们推文，用"补一个海外案例细节"的 reply 引流到你主页

---

## 信源管理 Tips
1. OPML 备份到 `signals/sources.opml`
2. Feedly mute filter：过滤政治、荐股荐币、纯币圈噪音
3. **每个案例存档**：标题 + 来源链接 + 核心数字，进 `signals/backlog.md`，可排期慢慢拆
4. 每月审计：删 30 天无贡献信源，加新的
5. 周报记录"本周选题来自哪些源"，砍零贡献的

## Phase B 自动化（Day 30+）
- Make.com：RSS（IH/Starter Story/Reddit top/Nitter）→ 拼接 → Claude API → markdown 到 Notion
- Python：`feedparser` + Anthropic SDK，早 8:00 cron，结果发 Telegram/微信
- 注意：Nitter 实例不稳定，备 RSSHub 自建实例
