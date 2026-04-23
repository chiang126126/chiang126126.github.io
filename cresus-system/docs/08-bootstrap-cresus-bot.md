# Bootstrap cresus-bot（Private Repo 初始化）

> 前提：你已经在 GitHub 上创建了 `chiang126126/cresus-bot` private repo（含 Python `.gitignore` + README）。
>
> 这份文档带你完成：本地 clone → Python 环境 → 目录骨架 → 烟雾测试 → 首次 commit。
> 全流程 ~15 分钟。

---

## Stage A · 本地 Clone + Python 环境（5 分钟）

### A.1 Clone 到本地

打开 MacBook Terminal（或 iTerm2）：

```bash
cd ~                         # 或你习惯放代码的位置
git clone https://github.com/chiang126126/cresus-bot.git
cd cresus-bot
pwd                          # 应显示 /Users/xxx/cresus-bot
ls -la                       # 应看到 .git/ .gitignore README.md
```

> 如果你配了 SSH key：`git clone git@github.com:chiang126126/cresus-bot.git`

### A.2 确认 Python 3.11+

```bash
python3 --version
```

- ≥ 3.11：✅ 跳到 A.3
- < 3.11 或没有：`brew install python@3.12`（需要先 `brew` https://brew.sh）

### A.3 装 uv（现代 Python 包管理器，比 pip 快 10 倍）

```bash
brew install uv               # 推荐
# 或：curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version                  # 验证
```

---

## Stage B · 创建项目骨架（5 分钟）

### B.1 创建目录树

在 `cresus-bot/` 根目录执行（一行搞定）：

```bash
mkdir -p src/{common,data_layer,ai_layer,execution,notifier} \
         config tests scripts logs

touch src/__init__.py \
      src/common/__init__.py \
      src/data_layer/__init__.py \
      src/ai_layer/__init__.py \
      src/execution/__init__.py \
      src/notifier/__init__.py \
      tests/__init__.py \
      logs/.gitkeep

ls -la src/
```

### B.2 写 `.gitignore`（追加，不是覆盖）

```bash
cat >> .gitignore <<'EOF'

# === Crésus additions ===
.env
.env.local
*.env.bak
*.secrets
logs/*.log
.venv/
.python-version
EOF
```

### B.3 写 `.env.example`（模板，会 commit）

```bash
cat > .env.example <<'EOF'
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
EOF
```

### B.4 写真 `.env`（绝不 commit）

```bash
cp .env.example .env
# 这一步先不填内容，后面 Discord/Binance/OKX key 拿到后再填
```

### B.5 写 `pyproject.toml`（依赖清单）

```bash
cat > pyproject.toml <<'EOF'
[project]
name = "cresus-bot"
version = "0.1.0"
description = "Crésus automated crypto scanning & AI-assisted signal bot"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "loguru>=0.7",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "ruff>=0.4",
]

[tool.ruff]
line-length = 100
target-version = "py311"
EOF
```

### B.6 写 `src/common/config.py`（加载 `.env`）

```bash
cat > src/common/config.py <<'EOF'
"""Environment-based configuration for cresus-bot.

Loads values from .env at repo root. Missing values default to empty/safe.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    binance_api_key: str = ""
    binance_api_secret: str = ""
    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""
    coinglass_api_key: str = ""

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    discord_bot_token: str = ""
    discord_channel_high_confidence: str = ""
    discord_channel_watch_list: str = ""
    discord_channel_scan_log: str = ""
    discord_channel_alerts: str = ""

    scan_interval_seconds: int = 300
    scan_top_n_coins: int = 100
    vol_oi_hard_filter: float = 20.0
    confidence_open_threshold: int = 70
    confidence_watch_threshold: int = 50

    log_level: str = "INFO"
    timezone: str = "Asia/Hong_Kong"
    dry_run: bool = True


settings = Settings()
EOF
```

### B.7 写 `scripts/hello_scan.py`（P0 烟雾测试）

```bash
cat > scripts/hello_scan.py <<'EOF'
"""P0 smoke test.

Verifies:
1. Python environment works
2. Project module imports work
3. .env loading works
4. Network reaches Binance + OKX public endpoints
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx
from loguru import logger

from common.config import settings


def main() -> None:
    logger.info("=== Crésus bot P0 smoke test ===")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(f"Config: TIMEZONE={settings.timezone}, DRY_RUN={settings.dry_run}")
    logger.info(f"Scan interval: {settings.scan_interval_seconds}s, top={settings.scan_top_n_coins}")

    with httpx.Client(timeout=5) as client:
        r = client.get("https://api.binance.com/api/v3/ping")
        r.raise_for_status()
        logger.info(f"Binance ping: HTTP {r.status_code} body={r.text or '{}'}")

        r = client.get("https://www.okx.com/api/v5/public/time")
        r.raise_for_status()
        okx_ts = r.json().get("data", [{}])[0].get("ts", "?")
        logger.info(f"OKX server time: {okx_ts}")

    logger.success("All checks passed. Bot scaffold is ready for P2.")


if __name__ == "__main__":
    main()
EOF
```

### B.8 写一个简短的 `README.md`（覆盖 GitHub 自带的）

```bash
cat > README.md <<'EOF'
# cresus-bot (private)

Automated crypto scanning + AI-assisted signal bot for the Crésus system.

> 📖 Public docs & knowledge base: https://github.com/chiang126126/chiang126126.github.io/tree/main/cresus-system

## Quick start

```bash
uv sync
cp .env.example .env    # fill in your API keys
uv run python scripts/hello_scan.py
```

## Layout

```
src/
├── common/         # config, logging, utils
├── data_layer/     # P2: Binance/OKX/Coinglass fetchers
├── ai_layer/       # P3: DeepSeek judge
├── execution/      # P4: signal routing
└── notifier/       # P4: Discord bot
```

## Roadmap

See public repo: `cresus-system/README.md`.
EOF
```

---

## Stage C · 装依赖 + 运行烟雾测试（2 分钟）

```bash
uv sync                                    # 自动建 .venv 并装依赖
uv run python scripts/hello_scan.py
```

**期望输出**：

```
INFO     | === Crésus bot P0 smoke test ===
INFO     | Python: 3.12.x
INFO     | Config: TIMEZONE=Asia/Hong_Kong, DRY_RUN=True
INFO     | Binance ping: HTTP 200 body={}
INFO     | OKX server time: 17xxxxxxxxxx
SUCCESS  | All checks passed. Bot scaffold is ready for P2.
```

看到这个 = ✅ P0 bot 骨架全部 work。

---

## Stage D · 首次 commit + push（1 分钟）

```bash
git status                  # 检查：.env 不在 untracked 列表里（被 .gitignore 拦住了）

git add .gitignore .env.example pyproject.toml README.md \
        src/ scripts/ tests/ logs/.gitkeep

git status                  # 再检查：Changes to be committed 里没有 .env
git commit -m "P0: scaffold cresus-bot with config, smoke test, directory skeleton"
git push -u origin main
```

**⚠️ 关键检查点**：`git status` 里**绝对不能**看到 `.env`。如果看到，立刻：
```bash
git rm --cached .env 2>/dev/null || true
# 确认 .gitignore 里有 .env 这一行
grep "^\.env$" .gitignore || echo ".env" >> .gitignore
```

---

## ✅ 完成检查清单

- [ ] `~/cresus-bot/` 目录存在
- [ ] `uv --version` 能跑
- [ ] 目录树有 `src/{common,data_layer,ai_layer,execution,notifier}`
- [ ] `.env.example` 已建，`.env` 已建但被 `.gitignore` 忽略
- [ ] `uv sync` 无报错
- [ ] `uv run python scripts/hello_scan.py` 打印 "All checks passed"
- [ ] `git push` 成功，GitHub 仓库页面能看到新文件
- [ ] GitHub 页面**没有** `.env` 文件（有就是事故）

全部打勾 = P0 bot 骨架完成，可以进入 P1（知识库）+ P2（数据层）并行阶段。
