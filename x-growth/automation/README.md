# 自动化 · 每天定时产出选题

两条路，选其一：

| 方案 | 费用 | 前提 | 推荐场景 |
|---|---|---|---|
| **方案 A · GitHub Actions**（`daily_pipeline.py`） | 需独立 Anthropic API Key（按用量计费） | 不需要电脑开着 | 有 API 预算、想全自动 |
| **方案 B · 本地 Claude Code CLI**（`daily_pipeline_local.py`） | **$0**（消耗 Max 订阅额度） | 电脑跑脚本时需在线 | 已有 Max 订阅、想省钱 |

---

## 方案 B · 本地版（推荐，$0 额外费用）

### 文件

- `daily_pipeline_local.py` — 调 `claude` CLI（消耗 Max 订阅，不用 API Key）
- `run_daily.sh` — cron / launchd 的 shell 包装（自动装依赖、写日志）
- `com.xgrowth.daily.plist` — macOS launchd 定时任务配置（比 cron 更可靠）
- `feeds.txt` — 信源清单，一行一个 RSS
- `output/` — 每天生成的选题 + 运行日志

### 前提

1. 已安装 Claude Code CLI 并用 Max 订阅账号登录过：

   ```bash
   # 验证
   claude --version   # 能打出版本号即可
   claude -p "你好"   # 能返回回复即可
   ```

2. 安装 feedparser（只需一次）：

   ```bash
   pip3 install feedparser
   ```

### 手动跑一次（测试）

```bash
cd x-growth/automation
bash run_daily.sh
# 或者直接：
python3 daily_pipeline_local.py
# 结果写到 output/YYYY-MM-DD.md，日志写到 output/run.log
```

### 定时跑 · macOS（推荐用 launchd，关机补跑）

```bash
# 1. 编辑 plist，把 /ABSOLUTE/PATH/TO 全部替换成你的实际路径
#    例：/Users/yourname/chiang126126.github.io/x-growth/automation
#    可用命令一键替换（把 yourname 改成你的用户名）：
sed -i '' 's|/ABSOLUTE/PATH/TO|/Users/yourname/chiang126126.github.io/x-growth/automation|g' \
    com.xgrowth.daily.plist

# 2. 给 run_daily.sh 加执行权限
chmod +x run_daily.sh

# 3. 复制到 LaunchAgents 并加载
cp com.xgrowth.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.xgrowth.daily.plist

# 4. 验证已注册
launchctl list | grep xgrowth

# 5. 立即手动触发一次测试
launchctl start com.xgrowth.daily
```

> 默认每天本地时间 **08:00** 跑。改时间：编辑 plist 里的 `Hour` / `Minute` 值，然后重新 `launchctl unload` → `launchctl load`。

### 定时跑 · Linux / macOS（cron 方案）

```bash
chmod +x x-growth/automation/run_daily.sh
crontab -e
```

在 crontab 里加一行（改成你的实际路径）：

```
# 每天早 8:00 跑（24h 制）
0 8 * * * /Users/yourname/chiang126126.github.io/x-growth/automation/run_daily.sh
```

> cron 的缺点：关机时错过的任务不会补跑。macOS 推荐 launchd。

### 调参（环境变量，可选）

| 变量 | 默认 | 作用 |
|---|---|---|
| `LOOKBACK_HOURS` | `36` | 回看多少小时的条目 |
| `MAX_ITEMS` | `60` | 最多喂给模型多少条 |
| `CLAUDE_BIN` | `claude` | claude 可执行路径（不在 PATH 时用绝对路径） |

---

## 方案 A · GitHub Actions（需 API Key）

### 文件

- `daily_pipeline.py` — 调 Anthropic Python SDK
- `requirements.txt` — `anthropic>=0.40`, `feedparser>=6.0`
- `../../.github/workflows/daily-signal.yml` — 定时任务（北京 8:00）

### 一次性配置

1. **加 API Key**：仓库 → Settings → Secrets → New repository secret
   - Name: `ANTHROPIC_API_KEY`，Value: `sk-ant-...`
2.（可选）**省钱换模型**：Settings → Variables → New variable
   - Name: `ANTHROPIC_MODEL`，Value: `claude-sonnet-4-6`
3. **开权限**：Settings → Actions → General → Workflow permissions → **Read and write** → Save

### 本地手动跑

```bash
cd x-growth/automation
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python daily_pipeline.py
```

### 成本

每天一次，Opus 4.8 约几美分；换 Sonnet 更便宜，月均约 1-2 美元。

---

## 共同注意事项

- **数字必须人工核对**：脚本只做"选题 + 拆解提纲"，不替代回原文核实数字。发布前务必核对——这是账号信任的根基。
- **Reddit/Nitter 偶尔不稳**：单个源失败自动跳过；某天抓 0 条会跳过不写文件。
- **大V推文**：`feeds.txt` 里有 RSSHub/Nitter 占位，自建实例后把 URL 填进去即可。
- **结果在哪**：`output/YYYY-MM-DD.md`，每天一个文件。挑一个选题，用 `prompts/02-case-breakdown.md` 展开成成品发布。
