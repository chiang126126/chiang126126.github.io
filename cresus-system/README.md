# Crésus 交易研究体系

> 一套以 AI 为核心、可持续迭代的加密货币研究与辅助决策闭环。

## 体系构成

```
Crésus
├── 手动研究链（Claude + Obsidian + Grok + Skills）
│   └── 把优质帖子 / 交易经验蒸馏进 Obsidian 共享知识库
│
└── 自动扫币链（Python bot + Exchange API + DeepSeek + Discord）
    └── 7×24 扫 100 币 → 筛异常 → AI 判断 → Discord 通知
```

两条链**完全独立**：手动研究链负责把交易认知沉淀成可复用框架；自动扫币链负责按框架实时扫描市场、识别机会。

## 仓库说明

**这个 public 仓库**存放：
- 📚 `obsidian-vault/` — 共享知识库（可公开，无密钥）
- 📖 `docs/` — 搭建 / 运维指南
- 🎛️ 未来的面板前端（P5 阶段）

**另一个 private 仓库**（后续创建）存放：
- 🤖 自动扫币 bot 的所有代码
- 🔑 `.env`（API 密钥，绝不进 git）
- 📡 Discord bot 实现

## 落地路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P0** | 地基：目录结构 + 文档 + Obsidian 骨架 | 🚧 进行中 |
| **P1** | 知识库：6 位 KOL 蒸馏流程 + 框架卡 | ⏳ |
| **P2** | 数据层：Binance/OKX/Coinglass + 22 维扫描 | ⏳ |
| **P3** | AI 判断层：DeepSeek + prompt 工程 | ⏳ |
| **P4** | 执行+通知：Discord bot + 信号路由 | ⏳ |
| **P5** | 面板：GitHub Pages 前端 + VPS 后端 | ⏳ |
| **P6** | 模拟盘 3 天 → mini 仓 1 周 → 正式 | ⏳ |

## 新手上路

按以下顺序读文档：

1. [docs/00-architecture.md](docs/00-architecture.md) — 整体架构
2. [docs/01-setup-discord-bot.md](docs/01-setup-discord-bot.md) — 建 Discord bot（免费，立刻做）
3. [docs/02-setup-binance-api.md](docs/02-setup-binance-api.md) — 建币安只读 API（免费，立刻做）
4. [docs/03-setup-okx-api.md](docs/03-setup-okx-api.md) — 建 OKX 只读 API（免费，立刻做）
5. [docs/06-private-repo-setup.md](docs/06-private-repo-setup.md) — 建 private repo 放 bot 代码
6. [docs/05-grok-distillation-guide.md](docs/05-grok-distillation-guide.md) — 启动 KOL 蒸馏
7. [docs/04-setup-deepseek-api.md](docs/04-setup-deepseek-api.md) — 建 DeepSeek（P3 前）
8. [docs/07-vps-hosting.md](docs/07-vps-hosting.md) — 决定部署位置（P5 前）
