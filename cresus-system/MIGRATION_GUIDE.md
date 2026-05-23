# Cresus 迁移指南: Intel Mac → M5 Pro Mac

**用途**: 把 V3 系统从旧电脑 (Intel i7) 完整搬到新电脑 (M5 Pro ARM64), 同时 V4 在新电脑上线.
**前置**: V4 6 月 baseline 数据已下载完成 (在旧电脑或新电脑均可).
**核心原则**: 单点切换, 不能 overlap (V3 是 Binance testnet 实盘, 两台同时跑会重复开仓).

---

## 迁移分布概览

旧电脑 V3 状态分布在 4 处:

| 类别 | 位置 | 内容 | 含密钥? |
|---|---|---|---|
| 代码 | `~/.../chiang126126.github.io` | Python source | 否 |
| 数据 | `~/cresus-bot/` | trade history / state / logs | 否 |
| 配置 | `~/.cresus/` | env.sh + secrets | **是** |
| 自动化 | `~/Library/LaunchAgents/com.cresus.*.plist` | 9 个 launchd 任务 | 否 (但含路径) |

Python 依赖也需重装 (Intel x86_64 → ARM64, 二进制不兼容).

---

## 阶段 1: 旧电脑停机 + 备份 (~15 分钟)

### 1.1 先停所有 launchd 任务 (防迁移过程中数据被 mutate)
```bash
# 在旧电脑跑
launchctl list | grep cresus
# 输出 9 个任务. 全部 unload:
for plist in ~/Library/LaunchAgents/com.cresus.*.plist; do
    launchctl unload "$plist"
    echo "unloaded: $plist"
done
launchctl list | grep cresus     # 应该为空
```

### 1.2 确认 live trader 已停 (避免迁移瞬间还有持仓变化)
```bash
ps aux | grep -E "live_trader|velocity|paper" | grep -v grep
# 应该为空. 如有残留, 用 kill <pid>
```

### 1.3 备份运行时数据 (无 secrets, 可压缩)
```bash
cd ~
tar -czf cresus-bot-backup-$(date +%Y%m%d).tar.gz cresus-bot/
ls -lh cresus-bot-backup-*.tar.gz
# 估算 50-200MB 含所有 history + logs
```

### 1.4 备份 secrets 配置 (含密钥, 不上 git)
```bash
cd ~
tar -czf cresus-secrets-backup-$(date +%Y%m%d).tar.gz .cresus/
# 检查内容, 确保有 env.sh
tar -tzf cresus-secrets-backup-*.tar.gz | head
```

### 1.5 备份 launchd plist (9 个文件)
```bash
mkdir -p ~/cresus-launchd-backup
cp ~/Library/LaunchAgents/com.cresus.*.plist ~/cresus-launchd-backup/
ls ~/cresus-launchd-backup/
# 期待: 9 个 plist
```

### 1.6 导出 Python 依赖 (供新电脑参考)
```bash
pip3 freeze > ~/cresus-pip-freeze-old.txt
wc -l ~/cresus-pip-freeze-old.txt
```

### 1.7 旧电脑代码 push 到 git (确保新电脑能 clone 拿到最新状态)
```bash
cd ~/.../chiang126126.github.io
git status   # 应该 clean
git pull --rebase
git push     # 确保旧电脑改动都同步上 git
```

### 1.8 传文件到新电脑
三种方式选一:
- **AirDrop**: 4 个文件 (2 个 tar.gz + 1 个 launchd-backup 目录 + pip-freeze.txt)
- **iCloud Drive**: 拖到 iCloud, 新电脑下载
- **U 盘 / 网络共享**

---

## 阶段 2: 新电脑环境准备 (~20 分钟)

### 2.1 确认 macOS + Python 版本
```bash
sw_vers            # macOS 版本 (M5 Pro 应该 15.x+)
which python3      # 应该是 /opt/homebrew/bin/python3 (ARM Homebrew)
python3 --version  # 期待 3.11+
uname -m           # 应该 'arm64'
```

如果 Python 不存在:
```bash
# 安装 Homebrew (如果还没)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.11
```

### 2.2 Clone repo
```bash
mkdir -p ~/projects && cd ~/projects
git clone <your-repo-url> chiang126126.github.io
cd chiang126126.github.io
git log --oneline -5    # 确认拿到最新 V4 代码
```

### 2.3 安装 Python 依赖
```bash
# 关键 deps (V3 用的)
pip3 install requests
# V4 新加
pip3 install pandas pyarrow
# V3 其它依赖 (从旧电脑的 pip-freeze 抄)
# 大概: python-telegram-bot / fastapi / uvicorn / etc.
# 看 ~/cresus-pip-freeze-old.txt 全表, 选关键的装
```

**ARM 注意**: pandas/pyarrow 在 ARM 上首次安装可能要编译, 慢 (2-5 min), 但能成功. 如果某个 pkg 失败, 一般是 binary wheel 还没出 ARM 版本, 用 `pip install --no-binary :all: <pkg>` 从源编译.

### 2.4 还原运行时数据
```bash
cd ~
# 解压 V3 history + state
tar -xzf cresus-bot-backup-*.tar.gz
ls cresus-bot/   # 应该看到 paper_trades_history.json 等
```

### 2.5 还原 secrets
```bash
cd ~
tar -xzf cresus-secrets-backup-*.tar.gz
ls .cresus/      # 期待 env.sh
chmod 600 .cresus/env.sh    # 保护权限
```

### 2.6 验证环境变量 (不实际暴露 secrets)
```bash
source ~/.cresus/env.sh
# 检查关键变量已设
[ -n "$BINANCE_API_KEY" ] && echo "BINANCE_API_KEY: set ✓" || echo "BINANCE_API_KEY: MISSING ✗"
[ -n "$BINANCE_API_SECRET" ] && echo "BINANCE_API_SECRET: set ✓" || echo "MISSING"
[ -n "$EMAIL_SMTP_HOST" ] && echo "EMAIL: set ✓" || echo "EMAIL: missing (邮件功能受影响)"
```

### 2.7 验证 binance_client 能跑 (dry-run 测试)
```bash
cd ~/projects/chiang126126.github.io
source ~/.cresus/env.sh
python3 cresus-system/scripts/binance_client.py --check-time
# 期待: 输出 server time + drift, 无 error
```

---

## 阶段 3: Launchd 任务迁移 (~15 分钟)

### 3.1 复制 plist 到新电脑
```bash
cp ~/cresus-launchd-backup/com.cresus.*.plist ~/Library/LaunchAgents/
ls ~/Library/LaunchAgents/com.cresus.*.plist
# 期待: 9 个
```

### 3.2 修改 plist 中的路径 (旧 → 新)
旧电脑路径可能是 `/Users/old-username/...`, 新电脑用户名不同要改:
```bash
# 看新电脑用户名
whoami
# 比方说是 newuser. 找出 plist 里的旧路径:
grep -l "old-username" ~/Library/LaunchAgents/com.cresus.*.plist
# 批量替换 (备份原文件):
for plist in ~/Library/LaunchAgents/com.cresus.*.plist; do
    sed -i.bak "s|/Users/OLD_USERNAME|/Users/$(whoami)|g" "$plist"
done
# 再检查
grep -A1 "WorkingDirectory\|ProgramArguments" ~/Library/LaunchAgents/com.cresus.live-trader.plist | head -20
```

如果旧电脑 Python 是 Intel binary (`/usr/local/bin/python3`), 新电脑要改成 ARM (`/opt/homebrew/bin/python3`):
```bash
for plist in ~/Library/LaunchAgents/com.cresus.*.plist; do
    sed -i.bak "s|/usr/local/bin/python3|/opt/homebrew/bin/python3|g" "$plist"
done
```

### 3.3 加载 launchd 任务
```bash
for plist in ~/Library/LaunchAgents/com.cresus.*.plist; do
    launchctl load "$plist" && echo "loaded: $(basename $plist)" || echo "FAIL: $plist"
done
launchctl list | grep cresus   # 期待 9 个
```

### 3.4 跑测试 — 看 log 是否在生成
```bash
ls -lt ~/cresus-bot/logs/ | head     # 看哪些 log 最近被写
tail -f ~/cresus-bot/logs/live_trader.log
# 等 60 秒, 应该看到新 entries
# Ctrl+C 退出
```

---

## 阶段 4: 端到端验证 (~30 分钟)

### 4.1 检查 dashboard 数据更新
```bash
# 新电脑跑了 5-10 min 后:
stat ~/cresus-bot/live_trades_history.json
# 期待 "Modify" 时间是最近, 不是旧电脑停机时
```

### 4.2 验证 live_trader 真在 mirror
```bash
tail -50 ~/cresus-bot/logs/live_trader.log | grep -E "mirror|open|skip"
# 期待: 看到 "mirror" / "would_mirror" / "skip:" 等关键日志
```

### 4.3 检查 reconcile 跟 exchange 一致
```bash
tail -200 ~/cresus-bot/logs/live_trader.log | grep -E "recon|mismatch"
# 期待: "recon ok" 多, "mismatch" 应该 0
# 如果有 mismatch, 看是否是旧电脑停机瞬间残留, 等 1 个 cycle 应该自愈
```

### 4.4 触发一次手动测试 (可选)
```bash
cd ~/projects/chiang126126.github.io/cresus-system/scripts
python3 daily_report.py --test
# 期待: 出报告, 不 crash
```

### 4.5 检查 watchdog 在工作
```bash
launchctl list | grep watchdog
ps aux | grep watchdog | grep -v grep
# 期待: 1 个进程
```

---

## 阶段 5: 旧电脑彻底停掉 (~5 分钟)

新电脑跑了至少 **1 小时无异常** 后:

### 5.1 旧电脑确认 launchd 全停
```bash
launchctl list | grep cresus    # 应该已经空了 (阶段 1.1 做过)
```

### 5.2 旧电脑 plist 改名 (防误启)
```bash
mkdir -p ~/Library/LaunchAgents.cresus-archived
mv ~/Library/LaunchAgents/com.cresus.*.plist ~/Library/LaunchAgents.cresus-archived/
ls ~/Library/LaunchAgents/com.cresus.*.plist 2>&1 | head -2
# 期待: "No such file or directory"
```

### 5.3 旧电脑数据目录归档 (保留但不再写)
```bash
mv ~/cresus-bot ~/cresus-bot.archived-$(date +%Y%m%d)
# 重启后即使有什么残留进程, 也找不到 ~/cresus-bot 写入路径
```

### 5.4 旧电脑标记 "Cresus 已迁移"
```bash
echo "Cresus 已于 $(date) 迁移至 M5 Pro. 此机不再运行 V3/V4." > ~/Desktop/CRESUS_MIGRATED.txt
```

---

## 关键风险 + 排错

### 风险 1: 时区不一致
新电脑系统时间应该是自动同步. 检查:
```bash
date    # 看小时是否对
# V3 代码全部用 datetime.now(timezone.utc), 跟系统本地时区无关. 但 log 时间戳可能受影响.
```

### 风险 2: Binance API 第一次连接被 reject
testnet API key 不限 IP, 应该没问题. 如果失败:
- 重生成 testnet API key (binance.com/zh-CN/futures/testnet)
- 更新 ~/.cresus/env.sh

### 风险 3: 数据 overlap (两台都跑了一段时间)
**这是最严重的情况**. 如果发现新旧两台都在 mirror, 立即:
1. 旧电脑 launchctl unload 全部
2. 新电脑 launchctl unload 全部
3. 看 ~/cresus-bot/.live_trades.json 里 mirrored_paper_ids
4. 跟 Binance 实际持仓 reconcile 一次 (live_trader 启动会自动)
5. 重启新电脑 launchd

### 风险 4: launchd 任务报 "service not found"
通常是 plist 路径错. 检查:
```bash
plutil -lint ~/Library/LaunchAgents/com.cresus.live-trader.plist
# 应该输出 "OK". 否则 XML 格式错
```

### 风险 5: ARM 上某个 Python pkg 装不上
99% 的 pkg 有 ARM wheel. 极少数情况需:
```bash
pip3 install <pkg> --no-binary :all:     # 从源编译, 耗时
# 或者用 conda-forge 的 ARM build
```

---

## 验证 checklist (完成后逐项打勾)

- [ ] 旧电脑 launchctl list 不再有 cresus 任务
- [ ] 旧电脑 ps aux 无 cresus 进程
- [ ] 新电脑 launchctl list 有 9 个 cresus 任务
- [ ] 新电脑 ~/cresus-bot/logs/ 有最新 log entries (5min 内)
- [ ] 新电脑 live_trader.log 显示 "mirror" / "skip" 等正常日志
- [ ] 新电脑 dashboard 数据时间戳更新到 1h 内
- [ ] 新电脑跑 daily_report 不 crash
- [ ] 新电脑 watchdog 进程存在
- [ ] Binance 实际持仓跟 ~/cresus-bot/.live_trades.json 匹配
- [ ] 旧电脑 ~/cresus-bot 已改名归档
- [ ] 旧电脑 launchd plist 已 archived
- [ ] 新电脑跑了 24h 无 critical error

---

## V4 在新电脑的额外步骤

V3 迁好后, V4 数据下载已在新电脑:
```bash
cd ~/projects/chiang126126.github.io/cresus-system/scripts/v4
python3 v4_data_fetcher.py --months 6
# 输出到 ~/cresus-bot/v4_klines/ 等 (跟 V3 数据同目录, 但子目录分开)
```

V4 跟 V3 数据目录共享 `~/cresus-bot/` 但**不冲突** (V4 用 `v4_*` 前缀子目录).

## 时序建议

最理想顺序:
1. 旧电脑跑完 V4 baseline 下载 (1-2hr)
2. 旧电脑 git commit + push V4 数据
3. 走阶段 1-5 迁移
4. 新电脑 git pull, V4 数据已在 git 里 (如归档)
5. 启动 V3 服务 + 开始 Day 3 V4 实现

或者并行:
1. 旧电脑跑下载
2. 同时, 已开始走阶段 1 备份 (因为 V3 状态不动)
3. 下载完 → 阶段 2-5
4. 完成
