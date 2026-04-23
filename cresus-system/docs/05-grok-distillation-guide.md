# Grok 批量蒸馏 6 位 KOL 操作指南

> 📌 **目标**：把 6 位 KOL 的交易帖子（每位至少 1000 篇）蒸馏成结构化笔记，存进 Obsidian vault。
>
> ⚠️ **现实约束**：Grok 不在你 ChatGPT Plus / Claude Max 订阅里，需要单独访问。

---

## 方案对比与推荐

| 方案 | 成本 | 能拉多少 | 推荐度 |
|---|---|---|---|
| grok.com 免费版 | 免费 | 每天约 10-20 次查询 | ⭐⭐⭐ 起步用 |
| X Premium | $8/月 | 充足（含 Grok + 看推） | ⭐⭐⭐⭐⭐ 最划算 |
| SuperGrok | $30/月 | 无限 | ⭐⭐ 现在还不需要 |
| Apify 付费抓取 | $50-100/月 | 批量全自动 | ⭐ 后期再考虑 |

**我的建议**：先用 **grok.com 免费版**跑 1-2 位博主验证流程；确认效果后再订 **X Premium $8/月**放量。

---

## 两条并行路径

### 路径 A（批量 · Grok）

Grok 能直接访问 X 的实时数据，最适合批量拉帖子清单。

### 路径 B（精细 · Claude Code）

你现在这个 Claude Max 会话 + WebFetch 可以直接蒸馏单条帖子，最适合高价值帖子深度精炼。

**建议：先 A 批量获取原文 → 再 B 精细蒸馏**。

---

## Grok 批量拉帖子的标准 Prompt

在 grok.com 对话框输入（每位 KOL 分开跑）：

```
你是一个交易研究助手。我需要你批量提取 X 用户 @Arya_web3 
在 2024-01-01 到 2024-12-31 期间发布的所有与加密货币交易
相关的推文。

输出要求：
1. 每条推文输出格式：
   ---
   日期: YYYY-MM-DD HH:MM
   链接: https://x.com/Arya_web3/status/xxx
   全文: <完整原文>
   涉及币种: $BTC / $ETH / ...
   核心观点: <一句话提炼>
   ---

2. 只保留以下类型：
   - 交易逻辑 / 策略讨论
   - 对具体币种的分析
   - 宏观大盘观点
   - 盈亏复盘
   跳过：纯情绪发泄、与项目无关的生活内容、转发无评论的

3. 按时间倒序（最新在前）
4. 如果数量很多，一次输出 30-50 条，我会说"继续"让你接着输出下一批

开始。
```

**关键技巧**：
- Grok 单次输出有长度限制，所以要求分批 + 说"继续"
- 跨月分批：`2024-01` / `2024-02` 分开问，避免漏
- 遇到 Grok 说"数据不够完整"时，改小范围（比如只问 2024-Q1）

### 把原文存进 Obsidian

每次 Grok 输出后：

1. 复制输出
2. 粘贴到 Obsidian：
   `cresus-system/obsidian-vault/01-KOLs/Arya_web3/raw-posts/2024-01.md`
3. 文件头加 front matter：

```markdown
---
kol: Arya_web3
period: 2024-01
source: grok.com
fetched_at: 2026-04-23
---
```

## 6 位 KOL 按顺序蒸馏

建议顺序（先蒸馏交易思路最清晰的）：

1. `@Arya_web3` ← 起步
2. `@thecryptoskanda`
3. `@CryptoRounder`
4. `@derrrrrrrq`
5. `@BTC_Alert_`
6. `@lanaaielsa`

每位的子目录结构已建好：

```
01-KOLs/Arya_web3/
├── README.md            # KOL 画像（必填）
├── raw-posts/           # Grok 拉回来的原文（按月）
├── distilled/           # Claude 精炼出的结构化笔记
└── framework.md         # 最终的"Arya 交易框架"
```

---

## Claude 精细蒸馏（路径 B）

对于 raw-posts 里的内容，下一步让 Claude Code 做结构化蒸馏。在 Claude Code 会话中说：

```
读 obsidian-vault/01-KOLs/Arya_web3/raw-posts/2024-01.md
按 obsidian-vault/05-Templates/distillation-template.md 
的格式，蒸馏成 distilled/2024-01-distilled.md。
要求：
- 保留所有具体币种 + 时间点 + 观点
- 提取重复出现的"决策条件"（比如某某指标触发时他会做什么）
- 标注该月整体市场状态（牛/熊/震荡）
```

---

## 从 6 位 KOL 到 "交易框架卡"

每位 KOL 蒸馏完后，产出一份 `framework.md`，格式见：
[obsidian-vault/05-Templates/framework-card-template.md](../obsidian-vault/05-Templates/framework-card-template.md)

6 份框架卡 → 在 `02-Frameworks/` 里合并出一份"Crésus 综合框架"，这就是 P3 阶段 DeepSeek 的 **system prompt 核心内容**。

---

## ✅ 第一周目标（起步）

- [ ] 试 grok.com 免费版，能拉出 @Arya_web3 最近 1 个月帖子
- [ ] 存到 `01-KOLs/Arya_web3/raw-posts/2024-XX.md`
- [ ] 用 Claude Code 蒸馏该月出 distilled 笔记
- [ ] 总结一份初版 `framework.md`
- [ ] 确认流程能跑通后，决定是否订 X Premium 放量

完成一位 KOL 的完整闭环后，再批量复制到其他 5 位。
