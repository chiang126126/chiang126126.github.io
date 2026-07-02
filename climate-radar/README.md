# Climate Opportunity Radar · 气候商机雷达

> AI 辅助商品挖掘与交易决策系统 — 把**天气概率、市场情绪、消费者行为、电商库存与中国供应链能力**放在同一张决策表，输出 **BUY / PRESELL / TEST / WATCH / SKIP** 的可执行商业建议。

第一阶段聚焦**欧洲高温**带来的降温商机（移动空调、桌面制冷风扇、PCM 颈圈、窗户密封布、排风管转接头、隔热遮光帘、防蚊纱窗等），后续可扩展到冬季保暖、极寒、能源、空气质量、户外应急等场景。

打开方式：直接访问 [`/climate-radar/`](https://chiang126126.github.io/climate-radar/) —— 纯静态、零后端、零密钥，所有数据存于浏览器 localStorage。

---

## 核心逻辑：四类信号 → 机会分

系统不预测天气，而是把四类信号 + 风险放进同一个加权模型（`assets/js/scoring.js`）：

| 信号 | 权重 | 内容 |
|------|------|------|
| **C 气候** | 0.28 | 峰值温度、高温持续天数、夜间热带夜、官方预警、区域空调渗透率 |
| **M 市场情绪** | 0.24 | Polymarket 极端高温概率、Google Trends 搜索热度、缺货新闻、平台库存 |
| **P 商品与渠道** | 0.30 | 毛利率（欧洲售价 vs 中国采购）、轻/小/易运、易演示、补货速度 |
| **L 本地实盘** | 0.18 | 浏览量、询盘率、成交率、当天取货需求、B 端需求（无数据时权重顺延） |

**风险**（合规 CE/WEEE/EPR/GPSR、退货/差评）在加权后扣分。

机会分映射：

```
≥80  BUY      小批量备货，本地现货即时交付
66–79 PRESELL  先挂预售 / 一件现货测水温
52–65 TEST     轻量测试：挂 1 件现货看流量
38–51 WATCH    暂不行动，持续观察
 <38  SKIP     价差 / 风险 / 需求不匹配
```

每个建议都带**可解释理由**（正向 / 风险 / 待验证）和**风险提示**——不鼓励盲目囤货，不夸大商品效果。

---

## 功能

- **雷达总览** — 城市气候信号条、KPI、按机会分排序的商品卡（评分环 + 四维信号 + 建议徽章）
- **商品库** — 录入 / 编辑 / 删除，表单实时计算机会分
- **决策卡** — 四维信号雷达图、可解释理由、推荐售价、合规/退货风险、推荐组合
- **信号台** — 城市气候、Polymarket、Google Trends、缺货新闻（可手动更新）
- **组合包** — 按场景卖解决方案（出租公寓热浪应急包、办公室桌面降温包、运动防暑包、移动空调安装配件包）
- **周报** — 一键生成每周商机简报
- **AI 生成** — Leboncoin 法语标题/描述、买家咨询回复、预售说明、1688 供应商询价话术、单品机会分析

---

## 技术

纯静态：`HTML + CSS + 原生 JS`，无构建、无依赖、无框架。

```
climate-radar/
├── index.html
└── assets/
    ├── css/app.css
    └── js/
        ├── scoring.js     # 机会分模型（四信号加权 + 风险扣分）
        ├── store.js       # localStorage + 种子数据
        ├── generator.js   # 法语文案 / 报告生成引擎
        └── app.js         # 视图 / 路由 / 交互
```

> **接入真实数据 / LLM**：`generator.js` 的 `build*()` 函数为接入 Claude API 预留——把模板输出替换为模型结果即可，UI 无需改动。

---

## 实时信号接入（混合架构）

四路信号已按各自可行的方式真实接入，统一走 `live.js` 的 provider 契约，**全部优雅降级**（网络/数据不可用时保留种子数据，UI 不受影响）。信号台页顶「↻ 同步实时信号」可手动刷新，页面载入亦自动尝试。

| 信号 | 方式 | 实现 | 说明 |
|------|------|------|------|
| **天气** | 浏览器直连 | `live-weather.js` | [Open-Meteo](https://open-meteo.com)（聚合 ECMWF/GFS/ICON/AROME 专业模型），免 key、支持 CORS。拉 7 城**未来 14 天**最高/最低/体感温，推导峰值、热带夜、体感、**热浪信号（未来 7–14 天是否连续 ≥3 天 ≥32℃、从第几天起）** 与预警等级，写回城市并**传导到商品气候/热浪字段，真实驱动机会分**。热浪按 canicule 规则由专业预报推导（非官方 Vigilance，可后续经管道叠加） |
| **Polymarket** | 浏览器直连 | `live-polymarket.js` | 公共 Gamma API，筛选高温/气候市场→隐含概率映射巴黎/伦敦。无匹配市场时诚实降级 |
| **Google Trends** | 数据管道 | `live-trends.js` + `scripts/fetch_trends.py` + `.github/workflows/climate-radar-trends.yml` | 无浏览器可调 API，故用 GitHub Action（pytrends，每 6h）抓取 FR 热度、提交 `data/trends.json`，前端只读 |
| **电商库存** | 数据管道 | `live-retail.js` + `scripts/fetch_retail.py` + `.github/workflows/climate-radar-retail.yml` | 反爬+CORS 无法直连，故用 Action（每 12h，best-effort）刷新 `data/retail.json`；**以手工维护为主**，按关键字+品类映射到商品 `retailStockout/retailTight` |

**契约（`live.js`）**：`LiveSignals.register(name, async (state) => ({ ok, updated, source, note }))`——provider 只改自己的信号槽，成功 `ok:true`，失败 `ok:false` 且不改动 state。每个 provider 都暴露纯解析函数（`_parse` / `_apply` / `_deriveFromDaily`）便于离线单测。

**数据管道**：两个 Action 用内置 `GITHUB_TOKEN` 提交回本仓库（无需额外 secret），采集脚本逐项兜底、抓不到就保留旧值、绝不非零退出。合并到 `main` 后按计划运行；首次运行前前端显示种子快照。`data/retail.json` 可直接手动编辑维护。

> ⚠️ 说明：天气 / Polymarket 在**用户浏览器**中实时执行，Trends / 电商在 **GitHub runner** 上执行——均不依赖任何自有后端。`data/*.json` 不含任何密钥。

---

## 开发原则

系统要轻；先手动录入再逐步自动化；输出必须可执行；AI 建议必须带理由；每个商品都要显示风险；不鼓励盲目囤货；对电子产品提醒合规风险。所有决策围绕**低成本验证 · 小批量补货 · 快速复盘**。
