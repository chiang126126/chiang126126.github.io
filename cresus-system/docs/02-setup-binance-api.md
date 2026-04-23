# Binance 只读 API 搭建指南

> 🟢 **免费** · 🟢 **10 分钟** · ⚠️ **只授权只读，永远不要开划转/提现权限**

## 为什么要只读 API

自动扫币 bot 需要拉这些数据：价格 / K 线 / 持仓量（OI）/ 资金费率 / 24h 成交量 / 订单簿深度。这些全部可以用**公开接口**或**只读 private 接口**拿到，**不需要**任何交易权限。

**风险底线：永远不给 bot 开 `Enable Trading` 和 `Enable Withdrawals`**。如果将来要走自动开仓，走子账户 + 单独 key，不动主账户。

---

## Step 1：开币安账户（如果还没有）

1. 注册：https://www.binance.com
2. 完成 KYC 实名认证（拉 API 需要）
3. 开启 2FA（推荐 Google Authenticator，不要用短信）

## Step 2：创建 API Key

1. 登录 → 右上角头像 → **"API 管理"**（直链 https://www.binance.com/zh-CN/my/settings/api-management）
2. 点 **"创建 API"** → 选择 **"系统生成"**
3. API 名字填：`cresus-readonly`
4. 完成 2FA 验证
5. 页面会显示：
   - **API Key**（公钥，可以存）
   - **Secret Key**（私钥，**只显示一次**，丢了只能重建）

## Step 3：配置权限（关键，只勾只读）

创建完成后点 **"编辑限制"**：

✅ **勾选**：
- `启用读取`（Enable Reading）—— **只勾这一个**

❌ **不要勾**：
- ❌ `启用现货及杠杆交易`（Enable Spot & Margin Trading）
- ❌ `启用合约`（Enable Futures）—— 注意：读合约 OI / funding 不需要这个
- ❌ `启用划转`（Enable Transfer）
- ❌ `启用提现`（Enable Withdrawals）
- ❌ `允许万向划转`（Permits Universal Transfer）

⚠️ **IP 白名单**（强烈推荐）：
- 如果你先本地跑，填你家公网 IP（查：https://ip.sb）
- 后续上 VPS 时改成 VPS 的 IP
- 填了 IP 白名单，key 泄露也不能被外部用

## Step 4：验证 API Key 能用

在终端运行（不需要安装任何东西）：

```bash
curl -s "https://api.binance.com/api/v3/ping"
# 应该返回 {}

curl -s "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT" | head
# 应该返回 JSON 包含 openInterest
```

这些是公开接口，不需要 key。真正用到 key 的是私有接口（账户余额等），P2 阶段我会写代码测。

## Step 5：保存凭据

追加到本地安全文件（**不要进 git**）：

```bash
# ~/cresus-secrets.txt
BINANCE_API_KEY=xxxxxxxxxxxxxxxxxxxx
BINANCE_API_SECRET=xxxxxxxxxxxxxxxxxxxx
BINANCE_IP_WHITELIST=your.public.ip.here
```

---

## 关于 Binance API 限额

- 公开接口：**1200 权重/分钟**（拉 100 币 K 线完全够）
- WebSocket：**单连接不限流**（后续优化方向）
- 我们 5 分钟扫 100 币，约 100-200 次请求/5min，离限额很远

## 关键端点预览（P2 会用）

| 端点 | 用途 |
|---|---|
| `/api/v3/ticker/24hr` | 24h 价格/成交量（批量） |
| `/api/v3/klines` | K 线 |
| `/fapi/v1/openInterest` | 合约持仓量 |
| `/fapi/v1/premiumIndex` | 资金费率 |
| `/fapi/v1/ticker/24hr` | 合约 24h 行情 |
| `/futures/data/topLongShortAccountRatio` | 大户多空比 |
| `/futures/data/topLongShortPositionRatio` | 大户持仓多空比 |
| `/futures/data/takerlongshortRatio` | Taker 多空比 |

---

## ✅ 完成检查清单

- [ ] 币安 KYC 完成，2FA 打开
- [ ] 创建了 `cresus-readonly` API Key
- [ ] **只勾了"启用读取"**，其他全部未勾
- [ ] 加了 IP 白名单
- [ ] `ping` 接口返回 `{}`
- [ ] 把 API Key + Secret 安全保存在本地

完成后告诉我 ✅，继续 OKX。
