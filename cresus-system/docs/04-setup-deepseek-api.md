# DeepSeek API 搭建指南

> 💰 **~$5 充值够用 1 个月** · ⏰ **P3 阶段前开即可，不用现在**

## 为什么用 DeepSeek

- 价格极便宜（对标 GPT-4 便宜 30 倍）
- 推理能力够强（reasoning 模型 deepseek-reasoner）
- 中文场景友好
- API 兼容 OpenAI SDK，迁移简单

## 成本估算

- 输入：¥1 / 百万 tokens（≈ $0.14）
- 输出：¥2 / 百万 tokens（≈ $0.28）

按你设计：每次判断 ~6K input + 4K system cache + 1K output ≈ 11K tokens/次
- 每 5 分钟扫一次 → 每次 5-10 个异常币 → ~50-100 次判断/天
- 每天 tokens ≈ 50 × 11K = 550K ≈ ¥1 / 天
- **一个月 ~¥30 (≈$5)**

---

## Step 1：注册 DeepSeek

1. 访问 https://platform.deepseek.com
2. 手机号 / Google / GitHub 注册
3. 完成邮箱验证

## Step 2：充值

1. 顶部 **"Billing"** → **"Top up"**
2. 支持支付宝 / 微信 / 银行卡
3. **先充 ¥50（约 $7）试水**，不够再充

## Step 3：创建 API Key

1. **"API Keys"** → **"Create API Key"**
2. 名字填：`cresus-trading`
3. 复制生成的 key（`sk-xxx...`），**只显示一次**

## Step 4：设置用量告警（防意外烧钱）

1. **"Billing"** → **"Usage limits"**
2. 设置：
   - 月度硬上限：¥100（超过自动停 API）
   - 告警阈值：¥50（到了发邮件）

## Step 5：验证

```bash
curl https://api.deepseek.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-xxxxxxx" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "Hi, respond with just: pong"}]
  }'
```

应返回一段 JSON，`choices[0].message.content` 是 "pong" 之类。

## Step 6：保存凭据

```bash
# ~/cresus-secrets.txt
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

---

## 选哪个模型？

P3 初期同时测两个：

| 模型 | 用途 | 价格 |
|---|---|---|
| `deepseek-chat` | 快速判断、日常扫描 | 便宜 |
| `deepseek-reasoner` | 复杂结构判断 / 深度推理 | 贵一点但更准 |

我的建议：
- 初筛用 `deepseek-chat`（信心度 50-70 的币）
- 高信心度（可能开仓的）用 `deepseek-reasoner` 做二次验证

---

## ✅ 完成检查清单

- [ ] 注册 DeepSeek 账户
- [ ] 充值 ¥50
- [ ] 创建了 `cresus-trading` API key
- [ ] 设置了用量硬上限 ¥100
- [ ] curl 测试能返回
- [ ] 凭据保存在本地

⚠️ **不急**：这个可以等 P3 阶段再做，别过早充值。
