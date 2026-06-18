# Million Path · MP500

> 以 500 USDT 为风险本金，构建**可验证、可复盘、低爆仓概率**的长期复利交易系统。
> 目标：冲击 100 万人民币 → 100 万美元。**但任何收益目标必须服从风控。**

⚠️ 本仓库是**策略文档 + 复盘工具**，不是收益承诺，也不含任何实盘下单代码或 API 密钥。加密货币交易可能损失全部本金，本仓库不构成投资建议。

---

## 目录结构

```
million-path/
├── index.html                     # ⭐ 交易驾驶舱（根目录，Pages 直接访问）
├── data/
│   ├── ledger.json                # 交易/复盘数据（看板读取，脚本追加）
│   ├── ledger.sample.json         # 示例数据
│   └── events.json                # 大事件/解锁/上币日历（手动维护）
├── docs/
│   ├── MP500-v1.1.md              # 主策略文档（周节奏 + 收益预期表 + S1合约仅paper）
│   └── weekly_review_TEMPLATE.md  # 周复盘模板（被脚本自动填充）
├── scripts/
│   └── new_week.py                # 每周自动生成复盘脚手架（自动填数）
├── reviews/                       # 每周复盘 week-NN.md 存档
├── AUDIT.md                       # 策略与代码审计报告
└── .env.example                   # 密钥占位（真实 .env 永不进 git）
```

## 看板功能（`index.html`，单文件零依赖、响应式，手机/Mac/iPad 通用）

- **概览**：组合权益、累计收益、阶段、违规、BTC 行情状态与本周建议
- **实时行情**：BTC/ETH/SOL/BNB 现价、24h 涨跌、高低、成交额、迷你走势图
- **趋势判断**：核心三币「价格 vs 30 日均线」自动判定 risk-on / neutral / risk-off
- **市场情绪**：Fear & Greed 恐惧贪婪指数
- **大事件日历**：宏观/解锁/上币（来自 `data/events.json`，自己维护）
- **交易复盘**：周 KPI 明细表 + 权益曲线
- **数据统计 & 复利测算器**

> 数据源：行情/K线 Binance（备用 CoinGecko）、情绪 alternative.me。均为浏览器直连的免费公开 API，**真实实时**。Binance 在部分地区受限时自动切 CoinGecko。新闻类「大事件」在纯静态托管下无法真正实时，故用手动维护的 `events.json`。

## 每周怎么用（90 秒）

```bash
# 1) 周一：生成本周复盘脚手架（自动结转上周权益、填好周次/日期/阶段）
python scripts/new_week.py --regime risk-on      # 或 neutral / risk-off

# 2) 周日：在 reviews/week-NN.md 和 data/ledger.json
#    填【需手填】字段（equity_end / 笔数 / 胜率 / 盈亏比 / 回撤 / 费用 / 违规）

# 3) 看板自动重算周收益、累计收益、回撤、违规，并画权益曲线
```

## 本地预览看板

```bash
python3 -m http.server 8000   # 在仓库根目录运行，浏览器打开 http://localhost:8000
```
（直接双击 `index.html` 也能看；行情需联网，复盘/测算离线可用，fetch 失败自动回退示例数据。）

## 挂到 GitHub Pages（手机/Mac/iPad 随时打开）

1. 仓库 **Settings → Pages**
2. Source 选 **Deploy from a branch**，Branch 选 **main**、目录选 **`/ (root)`**，保存
3. 等 1–2 分钟，访问 `https://<你的用户名>.github.io/million-path/`
4. 在手机/iPad Safari 打开该网址 → 分享 → **添加到主屏幕**，就像 App 一样随时看

---

## ⭐ 把本目录抽成「独立新仓库」（推荐，避免污染其他项目）

本项目当前作为一个**完全隔离的子目录**存在，未改动任何其他文件。要变成你想要的独立仓库 `million-path`：

```bash
# 在本仓库根目录执行：把 million-path/ 拆成独立分支
git subtree split --prefix=million-path -b million-path-export

# 在 GitHub 新建空仓库 million-path（private 推荐），然后：
git push git@github.com:<你的账号>/million-path.git million-path-export:main
```

或最简单：直接把 `million-path/` 文件夹复制到一个新建的空仓库里 `git init && git add . && git commit && git push`。

> 实盘 bot 代码（order router / risk engine / exchange client / `.env`）请放在**另一个 private repo**，不要放进这个公开文档仓库。

---

## 风险声明

- 本系统是**纪律与风控框架**，非盈利保证。
- 「日化/周化稳定高收益」不现实；详见 `docs/MP500-v1.1.md` §1。
- 审计结论见 `AUDIT.md`：确认「策略与代码无误」≠「一定能达成 100 万」。
