# Discord Bot 搭建指南

> 🟢 **免费** · 🟢 **5-10 分钟** · 🟢 **第一步就做这个**

目标：让你的扫币 bot 能把信号实时推到 Discord 频道，你在手机/电脑上都能第一时间看到。

---

## Step 1：创建 Discord 服务器（如果还没有）

1. 打开 Discord 桌面端或网页版（https://discord.com/app）
2. 左侧边栏最下方点 **➕ 加号**
3. 选 **"自己创建"** → **"仅供我和我的朋友"**
4. 服务器名填：`Crésus` 或你喜欢的
5. 创建完成

## Step 2：建频道结构（重要）

在你的服务器里建这几个频道（鼠标右键左侧列表 → 创建频道）：

```
📂 Crésus / Signals
  ├── #high-confidence    (信心度 >70，开仓信号)
  ├── #watch-list         (信心度 50-70，观察)
  └── #scan-log           (每次扫描摘要)

📂 Crésus / Research
  ├── #distillation       (给 AI 喂链接做蒸馏)
  ├── #framework-review   (框架讨论)
  └── #raw-feed           (随手保存的好帖)

📂 Crésus / Ops
  ├── #alerts             (bot 异常告警)
  └── #cost-tracking      (API 花费追踪)
```

> 为什么分这么细：你原文提到 "Discord 分 session 非常好用，hermes 一 ai 身兼多职"。每个频道都是一个独立上下文，后面 Hermes agent 可以按频道订阅不同任务。

## Step 3：创建 Bot 应用

1. 打开 https://discord.com/developers/applications
2. 右上角 **"New Application"** → 名字填 `Cresus-Bot` → Create
3. 左侧菜单点 **"Bot"** → **"Reset Token"** → 弹出 token **立刻复制保存**
   - ⚠️ 这个 token 只显示一次，丢了要重置
   - ⚠️ 这个 token = 完全控制你 bot 的密码，绝不能进 git
4. 在 Bot 页面往下拉，打开这几个开关：
   - ✅ `MESSAGE CONTENT INTENT`（读消息内容）
   - ✅ `SERVER MEMBERS INTENT`（可选，看成员列表）
   - ✅ `PRESENCE INTENT`（可选）

## Step 4：邀请 Bot 进你的服务器

1. 左侧 **"OAuth2"** → **"URL Generator"**
2. SCOPES 区勾选：
   - ✅ `bot`
   - ✅ `applications.commands`（斜杠命令用）
3. BOT PERMISSIONS 勾选：
   - ✅ `Send Messages`
   - ✅ `Send Messages in Threads`
   - ✅ `Embed Links`
   - ✅ `Attach Files`
   - ✅ `Read Message History`
   - ✅ `Add Reactions`
   - ✅ `Use Slash Commands`
4. 页面最下方会生成一个 URL，复制打开 → 选你刚建的服务器 → **授权**
5. 回 Discord，应该能看到 `Cresus-Bot` 出现在右侧成员列表（离线状态，因为 bot 代码还没写）

## Step 5：拿到频道 ID（给代码用）

1. Discord 设置 → **高级** → 打开 **"开发者模式"**
2. 右键你建的每个频道 → **"复制频道 ID"**
3. 把每个 ID 记在临时文本里：

```
DISCORD_CHANNEL_HIGH_CONFIDENCE=123456789012345678
DISCORD_CHANNEL_WATCH_LIST=123456789012345678
DISCORD_CHANNEL_SCAN_LOG=123456789012345678
DISCORD_CHANNEL_DISTILLATION=123456789012345678
DISCORD_CHANNEL_ALERTS=123456789012345678
```

## Step 6：保存凭据（为 P4 准备）

在本地新建一个安全的地方（**不要进 git**）保存：

```bash
# ~/cresus-secrets.txt  (chmod 600)
DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxx
DISCORD_CHANNEL_HIGH_CONFIDENCE=xxx
DISCORD_CHANNEL_WATCH_LIST=xxx
DISCORD_CHANNEL_SCAN_LOG=xxx
DISCORD_CHANNEL_DISTILLATION=xxx
DISCORD_CHANNEL_ALERTS=xxx
```

后面 P4 阶段我会把这些塞进 private repo 的 `.env` 文件。

---

## ✅ 完成检查清单

- [ ] 创建了 Discord 服务器
- [ ] 建了 8 个频道（按上面结构）
- [ ] 创建了 `Cresus-Bot` 应用并保存了 token
- [ ] 打开了 3 个 Intent
- [ ] 把 bot 邀请进服务器并看到它在成员列表
- [ ] 打开了开发者模式，复制了所有频道 ID
- [ ] 把 token + channel IDs 安全保存在本地（非 git 目录）

完成后告诉我 ✅，我会进入下一步（Binance API）。
