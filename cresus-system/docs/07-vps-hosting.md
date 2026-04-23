# 部署位置决策指南（本地 vs VPS）

> ⏰ **决策时间点**：P0-P4 本地跑，P5 前必须搬 VPS

---

## 为什么 MacBook 不能 7×24 跑

- 合盖即睡眠（会错过信号）
- 带出门网断（会错过 5 分钟扫描窗口）
- 掉电、关机重启容易丢状态
- 电池反复充放损耗加速

所以你的 MacBook（不管旧的还是未来 M5）：
- ✅ P0-P4 开发调试期：本地跑最快
- ❌ P5+ 生产扫币：**必须**搬到 VPS

---

## 推荐方案

### 🥇 第一选择：Oracle Cloud 永久免费 Tier（强烈推荐）

**为什么**：
- **永久免费**（不是试用 30 天）
- ARM 架构 4 CPU + 24GB 内存（配置超强，本来企业级别）
- 10TB 流量/月
- 足够跑整个 Crésus + 未来扩展

**缺点**：
- 申请时可能被拒（最近 Oracle 资源紧张）
- 账单信息要填信用卡验证（不会扣费）
- 控制台不如 DigitalOcean 友好

**申请**：https://www.oracle.com/cloud/free/

### 🥈 第二选择：Hetzner CAX11（付费但便宜）

- ARM 2 CPU + 4GB RAM + 40GB SSD
- **€3.79/月**（约 ¥30）
- 数据中心在德国 / 芬兰 / 美国，稳定
- 账单清晰

**注册**：https://www.hetzner.com/cloud

### 🥉 第三选择：Vultr / DigitalOcean

- $6/月 1GB RAM
- 按小时计费，方便删库重建
- 新用户常有 $200 赠送额度

---

## 都不选的话（最低限度方案）

你家里常年插电的旧 Mac Mini / 小主机 / 树莓派也可以。关键要求：

- 常年开机、常年联网
- 能 SSH 进去
- 能装 Python 3.11+

**旧 MacBook Pro 作为"家庭服务器"方案**（退役后利用）：
- 插电 + 合盖后仍工作（设置 → 电池 → 高级 → 防止合盖时自动睡眠）
- 关屏幕不关机：`sudo pmset -a displaysleep 15 disksleep 0 sleep 0`
- 装 Homebrew + Python
- 但仍有风险：家停电、网断就跪

---

## 决策建议

基于你的情况：
1. **现在（P0-P4）**：在本地 MacBook 上跑，用 `~/cresus-bot` 目录，调试最方便
2. **即将到来的 P5**：在下一步 Q&A 里确认你要走哪条路：
   - (A) 申请 Oracle Cloud Free（免费，推荐试一次）
   - (B) 直接买 Hetzner CAX11（€4/月保险）
   - (C) 用旧 MBP 合盖做家服（免钱但不稳）
3. **M5 到了之后**：M5 纯粹当开发机，不上生产流量

---

## VPS 搬迁清单（P5 阶段执行）

到时候要做的事（现在不用管）：

1. 开 VPS，记录 IP
2. 去 Binance/OKX API 管理页改 IP 白名单
3. `scp .env` 上传（不走 git）
4. 装 Python 3.11 + uv（或 poetry）
5. `systemd` 跑 bot，开机自启
6. 装 `fail2ban` + 关闭 root SSH + 只允许 key 登录
7. 装 `ufw` 开 22 + 你的面板端口
8. 配个域名（去 GoDaddy / Namecheap / Cloudflare Registrar 买）→ 面板
9. 装 `caddy` 或 `nginx` + Let's Encrypt 自动 HTTPS

---

## 当前决策（你在 P0 阶段只需确认一件事）

在 Q&A 里告诉我：

```
P5 前我打算：
(A) 申请 Oracle Cloud Free 试试
(B) 直接 Hetzner €4/月
(C) 旧 MBP 合盖家服
(D) 还没想好，到 P5 再决定  ← 也可以
```
