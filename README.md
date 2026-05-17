# chiang126126.github.io

## 仓库内容

### 静态站点
- **SoulPing** 静态页面：`index.html`, `support.html`, `privacy.html`, `terms.html`

### 交易系统（两个独立项目）

| 项目 | 目录 | 定位 | 私仓 |
|---|---|---|---|
| **Crésus 交易研究体系** | [`cresus-system/`](cresus-system/) | 通用扫币 + KOL 蒸馏 + AI 判断 | `cresus-bot` |
| **精灵捕手 Sprite Catcher** | [`sprite-catcher/`](sprite-catcher/) | 妖币专项（多头现货 + 空头永续） | `sprite-bot` |

⚠️ **两个项目完全平行、互不依赖**：
- 代码不互相 import
- 文件不共享读写
- 各自有独立的私仓（`cresus-bot` / `sprite-bot`）、独立的 API key、独立的资金账户
- 一边出 bug 不会影响另一边

> 本仓库（public）只存储**知识库 + 文档 + 公开参考实现**，不含任何 API 密钥。
> 实盘下单代码全部在各自的 private repo。
