---
type: prompt-library
title: 蒸馏 Prompt 库
tags: [prompts, distillation, workflow]
---

# 蒸馏 Prompt 库

> 直接复制，在 Claude Code 会话里使用。替换 `<handle>` 和 `<period>` 即可。

---

## Prompt 1：单月蒸馏（最常用）

```
请读取以下原文文件，按蒸馏模板格式整理输出：

原文：obsidian-vault/01-KOLs/<handle>/raw-posts/<period>.md
模板：obsidian-vault/05-Templates/distillation-template.md

蒸馏要求：
1. 识别该月整体市场状态（牛市 / 熊市 / 震荡），用 2 句话描述
2. 提取所有"具体币种 + 决策条件"组合，格式化为"决策 N"条目
   - 要包含：触发信号 / 标的条件 / 进场方式 / 止盈止损 / 仓位
   - 如原帖信息不完整，注明"未提及"，不要自己补全
3. 把作者本月重复强调（≥ 2 次）的观点归入"认知 / 框架更新"
4. 保留 3-5 条最有价值的金句（附原文 + 日期 + 链接）
5. 标注作者本月依赖的主要数据维度（对应 22 维清单打勾）

输出保存到：obsidian-vault/01-KOLs/<handle>/distilled/<period>-distilled.md
```

---

## Prompt 2：单条帖子快速蒸馏（日常用）

```
以下是 @<handle> 发布的一条帖子，请判断是否值得进知识库，
如果值得，提取核心信息：

<粘贴帖子原文>

判断标准：
- 有具体进出场逻辑 → 值得蒸馏
- 有新的市场结构分析 → 值得蒸馏
- 仅表达情绪 / 无依据预测 → 不需要

如果值得，输出格式：
---
日期: YYYY-MM-DD
链接: <原帖链接>
涉及币种: $XXX
触发条件: <具体数值 / 指标>
操作方向: 做多 / 做空 / 观望
依赖数据: <22 维里的维度编号>
核心逻辑: <2-3 句话>
---

追加到：obsidian-vault/01-KOLs/<handle>/raw-posts/<当月文件>.md
```

---

## Prompt 3：跨月规律合并

```
请读取 @<handle> 以下几个月的蒸馏笔记，找出跨月规律：

文件列表：
- obsidian-vault/01-KOLs/<handle>/distilled/<period1>-distilled.md
- obsidian-vault/01-KOLs/<handle>/distilled/<period2>-distilled.md
- obsidian-vault/01-KOLs/<handle>/distilled/<period3>-distilled.md

分析目标：
1. 哪些"决策模式"在多个月都出现（重复 ≥ 2 次的）？
2. 他的标的偏好是什么？（市值段、板块、链、上线时间）
3. 他在什么市场状态下表现最好 / 最差？
4. 他最依赖哪几个数据维度（从 22 维清单中标出）？
5. 他的止损逻辑是否一致？有没有演化？

输出一份"跨月规律总结"，保存到：
obsidian-vault/01-KOLs/<handle>/distilled/cross-month-summary.md
```

---

## Prompt 4：生成框架卡

```
根据以下跨月总结，按框架卡模板生成 @<handle> 的完整交易框架：

来源：obsidian-vault/01-KOLs/<handle>/distilled/cross-month-summary.md
模板：obsidian-vault/05-Templates/framework-card-template.md

生成要求：
1. "一句话概述"必须包含：交易风格 + 核心信号 + 典型操作（≤ 15 字）
2. "适用行情"必须写 ❌ 不适合的情况（没有边界的框架没有参考价值）
3. "标的筛选"必要条件里，每条必须有具体数值或可量化的描述
4. "结构类型"至少列 2 种，每种给出完整识别条件（3 个以上指标）
5. "数据源依赖"必须对应到 22 维清单的具体维度编号

输出保存到：obsidian-vault/01-KOLs/<handle>/framework.md
```

---

## Prompt 5：更新综合框架（每完成一位 KOL 后用）

```
我刚完成了 @<handle> 的框架卡：
obsidian-vault/01-KOLs/<handle>/framework.md

请读取这份新框架卡，以及现有的综合框架：
obsidian-vault/02-Frameworks/cresus-master-framework.md

任务：
1. 从新框架卡中提取"硬规则"候选（在所有已完成的 KOL 里都能找到共识的）
2. 提取"加分规则"候选（这位 KOL 独有但有价值的）
3. 标注是否与已有规则存在冲突（如有，注明冲突点）
4. 更新 cresus-master-framework.md（在对应章节追加，保留原有内容，更新版本号 +0.1）

已完成 KOL：<列出目前已完成的 KOL 列表>
```

---

## Prompt 6：从帖子截图蒸馏（有图时用）

```
这是 @<handle> 在 Twitter 上发的帖子截图，请提取交易信息：

[附上截图]

提取要求：
- 完整转录原文（保留原始语言，不要意译）
- 识别涉及的币种、时间框架、具体信号
- 判断操作方向（做多/做空/观望）
- 标注依赖的数据类型（对应 22 维）

如果有价值，追加到：
obsidian-vault/01-KOLs/<handle>/raw-posts/<当月>.md
```

---

## Prompt 7：Grok 批量拉帖（在 grok.com 里用）

```
你是一个加密货币交易研究助手。从 X 用户 @<handle> 的推文中，
提取 <year>-<month> 内所有与加密货币交易相关的帖子。

输出格式（每条）：
---
日期: YYYY-MM-DD HH:MM
链接: https://x.com/<handle>/status/<id>
全文: <完整原文>
涉及币种: $XXX
核心观点: <一句话中文提炼>
---

筛选标准（保留）：
- 交易逻辑 / 策略
- 具体币种进出场讨论
- 大盘宏观判断
- 盈亏复盘

跳过：
- 纯情绪宣泄
- 与交易无关的内容
- 无评论的纯转发

按时间倒序，每次 40 条，我说"继续"输出下一批。
```

---

## 快速参考：KOL handle 对应路径

| KOL | handle | 路径 |
|---|---|---|
| Arya | Arya_web3 | `01-KOLs/Arya_web3/` |
| Skanda | thecryptoskanda | `01-KOLs/thecryptoskanda/` |
| Rounder | CryptoRounder | `01-KOLs/CryptoRounder/` |
| derrrrq | derrrrrrrq | `01-KOLs/derrrrrrrq/` |
| BTC Alert | BTC_Alert_ | `01-KOLs/BTC_Alert_/` |
| Lana | lanaaielsa | `01-KOLs/lanaaielsa/` |
