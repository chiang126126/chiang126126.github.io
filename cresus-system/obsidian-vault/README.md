# Crésus Obsidian Vault

这是 Crésus 交易研究体系的共享知识库 (Obsidian Vault)。

## 如何打开

1. 下载 Obsidian：https://obsidian.md
2. 打开 Obsidian → **"Open folder as vault"** → 选这个目录
   （`chiang126126.github.io/cresus-system/obsidian-vault`）
3. 第一次打开会加载全部 markdown 文件，速度很快

## 目录结构

```
obsidian-vault/
├── 00-Index.md              ← 导航页（入口）
├── 01-KOLs/                 ← 6 位蒸馏对象
│   ├── Arya_web3/
│   ├── BTC_Alert_/
│   ├── CryptoRounder/
│   ├── derrrrrrrq/
│   ├── lanaaielsa/
│   └── thecryptoskanda/
│
├── 02-Frameworks/           ← 提炼出的交易框架
│   └── cresus-master-framework.md  (P3 产出)
│
├── 03-Data-Sources/         ← 数据源 / 维度参考
│   └── 22-dimensions.md
│
├── 04-Market-Concepts/      ← 市场概念笔记（结构、行情类型等）
│
└── 05-Templates/            ← 模板（Obsidian Templates 功能用）
    ├── distillation-template.md
    ├── framework-card-template.md
    └── kol-profile-template.md
```

## 协作方式

- **Claude Code / Codex / Hermes agent** 都可以读写这个目录
- 看到好帖子：在 Discord `#distillation` 频道丢链接 → AI 自动蒸馏进 `01-KOLs/<name>/distilled/`
- 蒸馏后的框架卡进 `02-Frameworks/`
- Trading bot（在 `cresus-bot` private repo）会 **只读** 这个 vault 来组装 prompt

## 重要约定

- 文件名：用英文 / 数字 / 连字符，避免空格
- 每个笔记顶部加 YAML front matter（方便 Obsidian Dataview 索引）
- 原始帖子放 `raw-posts/`，蒸馏笔记放 `distilled/`，合成框架卡放 KOL 目录根下的 `framework.md`
