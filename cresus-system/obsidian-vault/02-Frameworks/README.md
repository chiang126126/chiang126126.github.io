---
type: frameworks-index
title: 交易框架索引
---

# 02-Frameworks

这里存放由 KOL 蒸馏合成出来的可执行交易框架。

## 规划中的框架

| 文件 | 来源 | 状态 |
|---|---|---|
| [[cresus-master-framework]] | 合并 6 位 KOL 框架 + 你自己的判断 | ⏳ P3 产出 |
| 妖币行情识别框架 | 综合蒸馏 | ⏳ |
| 主升浪识别框架 | 综合蒸馏 | ⏳ |
| 假突破 / 背离框架 | 综合蒸馏 | ⏳ |
| BTC 宏观框架 | @BTC_Alert_ 为主 | ⏳ |

## 框架卡格式

所有框架都用 [[05-Templates/framework-card-template]] 的格式写。

## 最终成品：cresus-master-framework

`cresus-master-framework.md` 会是扫币 bot 的 **system prompt** 核心内容。它不是 6 份框架的简单拼接，而是：

1. 从 6 位 KOL 中抽出**共同认可**的规则 → "硬规则"
2. 只有部分人用的 → "加分规则"
3. 只有 1 位用的、但有效性高的 → "可选规则"
4. 互相冲突的 → 标注，扫币 bot 遇到冲突时让 AI 判断该用哪套

产出这份主框架是 P1-P3 之间的过渡工作。
