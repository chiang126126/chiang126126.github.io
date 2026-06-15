# 自动化 · 每天定时产出选题

让 **GitHub Actions 每天定点**自动：抓海外信源 → 调 Claude 用猎奇雷达筛选 → 把当天 3 个选题写到 `output/YYYY-MM-DD.md` 并提交到仓库。你早上打开就能看到，挑一个用 `prompts/02-case-breakdown.md` 展开成成品。

## 文件

- `daily_pipeline.py` — 主脚本（抓 RSS → Claude → markdown）
- `feeds.txt` — 信源清单，一行一个 RSS（改这里增删信源）
- `requirements.txt` — Python 依赖
- `output/` — 每天生成的选题（自动提交）
- `../../.github/workflows/daily-signal.yml` — 定时任务

## 一次性配置（5 分钟）

1. **加 API Key**：仓库 → Settings → Secrets and variables → Actions → New repository secret
   - Name: `ANTHROPIC_API_KEY`，Value: 你的 key（`sk-ant-...`）
2.（可选）**省钱换模型**：同页 Variables 标签 → New variable
   - Name: `ANTHROPIC_MODEL`，Value: `claude-sonnet-4-6`（不设则默认 `claude-opus-4-8`）
3. **开权限**：Settings → Actions → General → Workflow permissions → 选 **Read and write permissions** → Save
4. 完成。每天北京时间 8:00 自动跑；也可在 Actions 页面手动点 **Run workflow** 立即测试。

## 本地手动跑（想先试效果）

```bash
cd x-growth/automation
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python daily_pipeline.py
# 结果写到 output/<今天>.md
```

## 调参（环境变量）

| 变量 | 默认 | 作用 |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | 换 `claude-sonnet-4-6` / `claude-haiku-4-5` 省钱 |
| `LOOKBACK_HOURS` | `36` | 回看多少小时的条目 |
| `MAX_ITEMS` | `60` | 最多喂给模型多少条 |

## 成本

每天一次、几十条摘要，Opus 4.8 单次约几美分；换 Sonnet 更便宜。一个月下来通常一两美元级别（取决于信源量）。

## 注意

- **数字必须人工核对**：脚本只做"选题 + 拆解提纲"，不替代你回原文核实数字。发布前务必核对（这是账号信任的根基）。
- **Reddit/Nitter 偶尔不稳**：单个源失败会自动跳过，不影响整体；某天抓 0 条会跳过不提交。
- **大V推文**：`feeds.txt` 里给了 RSSHub/Nitter 占位，自建实例后把 URL 填进去即可纳入大V动态。
- **想改时间**：编辑 `daily-signal.yml` 里的 `cron`（UTC 时间，`0 0 * * *` = 北京 8:00）。
