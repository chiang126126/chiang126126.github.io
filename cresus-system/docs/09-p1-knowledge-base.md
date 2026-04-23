# P1 知识库：6 位 KOL 蒸馏全流程

> 前提：P0 骨架完成（`08-bootstrap-cresus-bot.md` 全部打勾）。
>
> P1 目标：把 6 位 KOL 的交易帖子蒸馏成结构化框架，最终产出 `cresus-master-framework.md`——这就是 P3 阶段 DeepSeek 的 **system prompt 核心**。
>
> P1 和 P2 可以**并行**：P1 做知识库积累，P2 做数据层开发，互不阻塞。

---

## 阶段概览

```
Stage A：Obsidian 本地连接（5 分钟）
Stage B：完善 6 位 KOL 画像（20 分钟）
Stage C：第一次 Grok 拉帖 + 原文存库（20 分钟）
Stage D：Claude Code 蒸馏首次试跑（30 分钟）
Stage E：合成第一张框架卡（20 分钟）
Stage F：建立 cresus-master-framework 骨架（10 分钟）
Stage G：日常工作流固化（长期）
```

---

## Stage A · Obsidian 本地连接

### A.1 安装 Obsidian

1. 下载：[obsidian.md](https://obsidian.md)（免费，Mac/Win/Linux）
2. 安装后，选 **Open folder as vault**
3. 指向：`~/chiang126126.github.io/cresus-system/obsidian-vault/`（你本地 git clone 的路径）
4. Obsidian 会直接读取已有的 `.md` 文件，不需要额外导入

### A.2 建议开启的插件（设置 → 核心插件）

- **Backlinks** — 显示哪些文件引用了当前文件（已默认开启）
- **Templates** — 用 `05-Templates/` 里的模板快速建新笔记
- **Daily notes** — 可选，记录每日蒸馏进度

### A.3 验证

按 `Cmd+O`（Mac）/ `Ctrl+O`（Windows），搜 `00-Index`，能打开 `00-Index.md` = ✅

---

## Stage B · 完善 6 位 KOL 画像

每位 KOL 的 `README.md` 已建好骨架，现在把你知道的基本信息填进去。

**蒸馏顺序**（按交易思路清晰度排）：

| 优先级 | KOL | 路径 |
|---|---|---|
| 1 | @Arya_web3 | `01-KOLs/Arya_web3/README.md` |
| 2 | @thecryptoskanda | `01-KOLs/thecryptoskanda/README.md` |
| 3 | @CryptoRounder | `01-KOLs/CryptoRounder/README.md` |
| 4 | @derrrrrrrq | `01-KOLs/derrrrrrrq/README.md` |
| 5 | @BTC_Alert_ | `01-KOLs/BTC_Alert_/README.md` |
| 6 | @lanaaielsa | `01-KOLs/lanaaielsa/README.md` |

对每位 KOL，打开其 `README.md`，按 `05-Templates/kol-profile-template.md` 填写：
- 主要领域（spot / perp / defi / meme）
- 交易风格（短线 / 波段）
- 为什么值得蒸馏（一句话）
- 核心关注指标（从他的帖子里你注意到他经常看什么）

> **提示**：如果你对某位 KOL 还不熟，先跳过，蒸馏之后自然会补齐。

---

## Stage C · 第一次 Grok 拉帖（以 @Arya_web3 为例）

### C.1 进入 grok.com

> 免费版每天约 10-20 次查询。如果要批量，建议 $8/月的 X Premium。

### C.2 用以下 Prompt 拉帖（直接复制）

```
你是一个加密货币交易研究助手。我需要你从 X 用户 @Arya_web3 
的推文历史中，提取所有与加密货币交易相关的帖子。

时间范围：最近 3 个月（从今天往前算）

输出格式（每条推文）：
---
日期: YYYY-MM-DD
链接: https://x.com/Arya_web3/status/<id>
全文: <完整原文，保留原始语言>
涉及币种: $BTC / $ETH / 其他（没有则写"无"）
核心观点: <一句话中文提炼>
---

筛选标准（保留）：
- 交易逻辑 / 策略分析
- 具体币种的进出场讨论
- 大盘宏观判断
- 盈亏复盘与经验总结

跳过（不需要）：
- 纯情绪宣泄
- 与交易无关的生活内容
- 无评论的纯转发

按时间倒序（最新在前），一次输出 40 条。我会说"继续"让你输出下一批。

开始。
```

### C.3 把原文存入 Obsidian

每次 Grok 输出后，复制内容，新建文件：

```
obsidian-vault/01-KOLs/Arya_web3/raw-posts/2026-04.md
```

文件开头加 front matter：

```markdown
---
kol: Arya_web3
period: 2026-04
source: grok.com
fetched_at: 2026-04-23
post_count: 40
---
```

### C.4 批量拉多个月

分批用 Grok 拉，每月一个文件：
- `raw-posts/2026-03.md`
- `raw-posts/2026-02.md`
- `raw-posts/2026-01.md`

> **目标**：每位 KOL 至少拉 **3 个月**原文再开始蒸馏，积累足够样本。

---

## Stage D · Claude Code 蒸馏

原文存好后，在你的 Claude Code 会话（就是这个 claude.ai 界面）里说：

### D.1 基础蒸馏 Prompt

```
请读取以下文件，按 distillation-template 格式进行蒸馏：

原文：obsidian-vault/01-KOLs/Arya_web3/raw-posts/2026-04.md
模板：obsidian-vault/05-Templates/distillation-template.md

要求：
1. 提取所有"具体币种 + 决策条件"的组合，格式化为"决策 N"
2. 识别该月市场状态（牛/熊/震荡），写在"本月市场状态"
3. 提取重复出现 2 次以上的信号或判断逻辑，归入"认知/框架更新"
4. 保留最有价值的 3-5 条金句（附原文 + 链接）
5. 标注 @Arya_web3 本月主要依赖的数据源

输出存入：obsidian-vault/01-KOLs/Arya_web3/distilled/2026-04-distilled.md
```

### D.2 跨月合并分析 Prompt

做完 3 个月的蒸馏后，合并分析：

```
请读取以下三个月的蒸馏笔记：
- obsidian-vault/01-KOLs/Arya_web3/distilled/2026-04-distilled.md
- obsidian-vault/01-KOLs/Arya_web3/distilled/2026-03-distilled.md
- obsidian-vault/01-KOLs/Arya_web3/distilled/2026-02-distilled.md

任务：
1. 找出 @Arya_web3 在这 3 个月里"重复出现"的交易逻辑（出现 2 次以上的）
2. 识别他的标的筛选偏好（喜欢什么类型的币？排除什么？）
3. 总结他的进场/止损/止盈模式
4. 列出他依赖的核心数据维度（从 22 维里找对应）

输出：一份 Arya_web3 的"跨月规律总结"，临时存入 distilled/cross-month-summary.md
```

### D.3 生成框架卡 Prompt

```
根据 obsidian-vault/01-KOLs/Arya_web3/distilled/cross-month-summary.md，
按 obsidian-vault/05-Templates/framework-card-template.md 的格式，
生成 @Arya_web3 的完整交易框架卡。

要求：
- 填写"适用行情"（什么市场他这套有效/失效）
- 填写"标的筛选"的必要条件和加分条件
- 至少列出 2 种"结构类型"（他惯用的信号模式）
- 填写具体的进场规则（不能是"分批入场"等模糊描述）
- 列出他最依赖的 22 维数据维度

输出：obsidian-vault/01-KOLs/Arya_web3/framework.md
```

---

## Stage E · 合成第一张框架卡

完成 Stage D 后，`01-KOLs/Arya_web3/framework.md` 就是一份可执行的交易框架卡。

**检查标准**：
- [ ] "结构类型"有 ≥2 种，每种有具体识别条件
- [ ] "进场规则"写了具体价位判断逻辑（不是泛泛而谈）
- [ ] "止损规则"有具体条件触发
- [ ] "数据源依赖"标注了 22 维里的具体维度编号
- [ ] "适用行情"写了**不适合**的情况（有边界的框架才可信）

---

## Stage F · 建立 cresus-master-framework 骨架

做完第一位 KOL 后，就可以开始在 `02-Frameworks/cresus-master-framework.md` 里落第一批"硬规则"。

打开 `obsidian-vault/02-Frameworks/cresus-master-framework.md`，按已有模板填写：

文件已预建，骨架如下：
- **硬规则**：所有 KOL 都认同的，直接写死（如"vol/OI > 20x 排除"）
- **加分规则**：多数人用的，作为正向权重
- **可选规则**：只有 1 位用但有效，附条件使用
- **冲突规则**：有矛盾的，让 AI 判断时自行权衡

> 每完成一位 KOL 的 framework.md，就回来更新 cresus-master-framework.md 一次。

---

## Stage G · 日常工作流固化

### 每天（5-10 分钟）

1. 刷 Twitter 看到 KOL 好帖 → 直接复制全文
2. 在 Claude Code 里：

```
这是 @Arya_web3 今天发的一条帖子，请判断是否值得蒸馏，
如果值得，提取核心交易决策：

<粘贴帖子原文>

如果有价值，直接追加到：
obsidian-vault/01-KOLs/Arya_web3/raw-posts/2026-04.md
```

### 每周（30 分钟）

1. 检查本周各 KOL 有没有新的 raw-posts 积累
2. 触发一次蒸馏（Stage D.1）
3. 如有新规律，更新对应 `framework.md`
4. 回顾 `cresus-master-framework.md`，看有没有要更新的

### 每月（1 小时）

1. 对有新蒸馏的 KOL 运行 Stage D.2（跨月合并分析）
2. 更新 `framework.md`（版本号 +0.1）
3. 更新 `cresus-master-framework.md`
4. 检查 22 维的数据依赖有没有变化

---

## ✅ P1 完成检查清单

### 最低可行（能进 P3 的门槛）

- [ ] ≥ 2 位 KOL 完成完整蒸馏（至少 3 个月原文 + distilled + framework.md）
- [ ] `cresus-master-framework.md` 有 ≥ 5 条硬规则
- [ ] 框架卡里有 ≥ 2 种有具体识别条件的"结构类型"
- [ ] 每个结构类型都对应了 22 维里的具体数据维度

### 完整目标（P1 全部完成）

- [ ] 全部 6 位 KOL 有 `framework.md`（各自 3 个月以上蒸馏）
- [ ] `cresus-master-framework.md` 有完整的硬规则 + 加分规则 + 冲突处理说明
- [ ] `04-Market-Concepts/` 里补充了 ≥ 5 个概念笔记（妖币/主升浪/假突破/OI 异动/费率反向）
- [ ] Obsidian vault 里能跑通 wiki-link 反向引用

---

## 关联文档

- `05-grok-distillation-guide.md` — Grok 批量拉帖详细操作
- `05-Templates/distillation-template.md` — 蒸馏笔记格式
- `05-Templates/framework-card-template.md` — 框架卡格式
- `05-Templates/distillation-prompts.md` — 可直接复制的 Claude Prompt 库
- `obsidian-vault/02-Frameworks/cresus-master-framework.md` — P1 最终交付物

---

## 下一步：P2 数据层

P1 做到"最低可行"后即可并行启动 P2（`10-data-layer.md`）。  
P1 完整完成后，合并框架进入 P3（`11-ai-layer.md`）。
