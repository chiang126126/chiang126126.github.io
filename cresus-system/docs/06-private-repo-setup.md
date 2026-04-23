# Private Repo 创建与协作指南

> 🟢 **完全免费** · ⏰ **10 分钟** · ⚠️ **Bot 代码和密钥必须放这里**

## 为什么必须另开 private repo

本 public 仓库是 GitHub Pages 站点，任何人可见。Bot 代码包含：
- API 调用逻辑（一旦泄露可能被分析出策略）
- `.env.example`（虽然不含真密钥，但暴露你用了哪些服务）
- 策略参数（例如止盈止损规则）

这些**必须**放 private repo。GitHub 个人 private repo **无限免费**（2019 起）。

---

## Step 1：创建 Private Repo

### 方案 A：网页（推荐新手）

1. 打开 https://github.com/new
2. Repository name：`cresus-bot`
3. Description：`Crésus automated crypto scanning bot`
4. **Visibility：勾选 🔒 Private**
5. ☑️ Add a README file
6. ☑️ Add .gitignore → 选 `Python`
7. ☑️ Choose a license → 选 **None**（不公开分发）
8. **Create repository**

### 方案 B：命令行

```bash
# 需要先装 gh: brew install gh  (Mac)
gh auth login
gh repo create cresus-bot --private --description "Crésus automated crypto scanning bot" --gitignore Python
```

## Step 2：本地克隆

```bash
# 把 cresus-bot 克隆到和本仓库并列的位置
cd ~/   # 或你习惯放代码的位置
git clone git@github.com:chiang126126/cresus-bot.git
cd cresus-bot
```

> 如果还没配 SSH：用 `https://github.com/chiang126126/cresus-bot.git`，但建议配好 SSH（https://docs.github.com/en/authentication/connecting-to-github-with-ssh）

## Step 3：初始化 Python 项目骨架

> P2 阶段我会帮你填充这个骨架。现在先把目录建起来，把 `.gitignore` 和 `.env.example` 配好。

在 `cresus-bot/` 执行：

```bash
mkdir -p src/{data_layer,ai_layer,execution,notifier,common} \
         config tests scripts logs

touch src/__init__.py \
      src/data_layer/__init__.py \
      src/ai_layer/__init__.py \
      src/execution/__init__.py \
      src/notifier/__init__.py \
      src/common/__init__.py
```

创建 `.env.example`（这份可以 commit）：

```bash
# ====== Exchange APIs (read-only) ======
BINANCE_API_KEY=
BINANCE_API_SECRET=

OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=

COINGLASS_API_KEY=

# ====== AI ======
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# ====== Discord ======
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_HIGH_CONFIDENCE=
DISCORD_CHANNEL_WATCH_LIST=
DISCORD_CHANNEL_SCAN_LOG=
DISCORD_CHANNEL_ALERTS=

# ====== Scanning config ======
SCAN_INTERVAL_SECONDS=300
SCAN_TOP_N_COINS=100
VOL_OI_HARD_FILTER=20
CONFIDENCE_OPEN_THRESHOLD=70
CONFIDENCE_WATCH_THRESHOLD=50

# ====== Runtime ======
LOG_LEVEL=INFO
TIMEZONE=Asia/Hong_Kong
DRY_RUN=true
```

创建 `.env`（**绝不 commit**）：

```bash
cp .env.example .env
# 然后用 ~/cresus-secrets.txt 里的真值填进去
```

验证 `.gitignore` 包含（Python 模板应该已经有了）：

```
.env
.env.local
*.env.bak
logs/
*.pem
*.key
```

## Step 4：加一点保护（强烈推荐）

在 GitHub 仓库 Settings → Secrets and variables → **Actions**：

- 把**长期**的密钥（比如 DEEPSEEK_API_KEY）放这里
- 以后用 GitHub Actions 做 CI/CD 时直接引用，不走 `.env`
- 多一层保险

Settings → **Branch protection rules** → main：
- ☑️ Require pull request before merging（自己一个人也建议开，防止误操作）
- ☑️ Require status checks to pass

## Step 5：在两个仓库之间建立连接

在本 public 仓库（`chiang126126.github.io`）的 `cresus-system/docs/` 里加一份 `private-bot-location.md`（不泄露代码，只记录这个仓库存在）：

> 我们会在 P2 阶段补充这个文件。

---

## 目录全景（两个仓库协作）

```
~/
├── chiang126126.github.io/          ← 本 public 仓库
│   └── cresus-system/
│       ├── docs/                     ← 所有指南
│       └── obsidian-vault/           ← 共享知识库（bot 读取这里）
│
└── cresus-bot/                       ← private 仓库
    ├── .env                          ← 真密钥（不 commit）
    ├── .env.example                  ← 模板（commit）
    ├── src/
    │   ├── data_layer/               ← P2
    │   ├── ai_layer/                 ← P3
    │   ├── execution/                ← P4
    │   └── notifier/                 ← P4
    ├── config/
    ├── scripts/
    └── logs/
```

Bot 需要读 Obsidian vault 的框架卡当作 prompt → 它通过**文件路径引用**读取（因为两个仓库在同一台机器上）：

```python
# cresus-bot/src/ai_layer/prompts.py (P3 会写)
OBSIDIAN_VAULT = Path.home() / "chiang126126.github.io" / "cresus-system" / "obsidian-vault"
FRAMEWORK_FILE = OBSIDIAN_VAULT / "02-Frameworks" / "cresus-master-framework.md"
```

---

## ✅ 完成检查清单

- [ ] GitHub 上创建了 `cresus-bot` private repo
- [ ] 本地克隆了仓库
- [ ] 建好了 `src/` 目录结构
- [ ] `.env.example` 已创建并 commit
- [ ] `.env` 已创建但**未** commit（`git status` 不应该出现它）
- [ ] 把 `~/cresus-secrets.txt` 的值填进 `.env`（Discord + Binance + OKX 已填）

完成告诉我 ✅，P2 阶段我会开始往这个 repo 填代码。
