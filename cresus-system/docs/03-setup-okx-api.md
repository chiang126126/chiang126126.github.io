# OKX 只读 API 搭建指南

> 🟢 **免费** · 🟢 **10 分钟** · ⚠️ **同样只授权只读**

## 为什么还要 OKX

交叉验证 Binance 数据 + 拉取 Binance 没有的币种 + 获取 OKX 独有的情绪监控指标（你原文提到的 https://x.com/okxchinese/status/2045028945720950968）。

---

## Step 1：开 OKX 账户

1. 注册：https://www.okx.com
2. 完成 KYC（Level 1 够用，拉 API 需要）
3. 开启 2FA

## Step 2：创建 API

1. 登录 → 头像菜单 → **"API"**（直链 https://www.okx.com/account/my-api）
2. 点 **"创建 V5 API Key"**
3. 填写：
   - **名称**：`cresus-readonly`
   - **Passphrase**（密码短语）：**自己设一个 8+ 位字符串并保存**，这个不是密码，是 API 签名的一部分，丢了就得重建 API
4. **权限**：**只勾选 `只读` / `Read`**，其他全不勾
5. **IP 白名单**：和 Binance 一样，强烈建议填
6. 保存后会显示三件宝：
   - **API Key**
   - **Secret Key**
   - **Passphrase**（就是你刚设的那个）

## Step 3：验证

```bash
curl -s "https://www.okx.com/api/v5/public/time"
# 返回 {"code":"0","msg":"","data":[{"ts":"..."}]}

curl -s "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP" | head
# 返回合约行情
```

## Step 4：保存凭据

```bash
# ~/cresus-secrets.txt
OKX_API_KEY=xxxxxxxx
OKX_API_SECRET=xxxxxxxx
OKX_PASSPHRASE=xxxxxxxx
OKX_IP_WHITELIST=your.public.ip.here
```

## Step 5（可选）：启用 Web3 / OnchainOS

你原文提到想用 OKX onchainos。这是另一条链路（浏览器插件钱包 + dapps），和 exchange API 是分开的两件事：

- **Exchange API**（上面建的）= 看 CEX 行情用
- **OKX Web3 / OnchainOS** = 看链上数据（聪明钱、持币分布）用

Web3 侧需要：
1. 安装 OKX 钱包浏览器插件（https://web3.okx.com/zh-hans/download）
2. 访问 https://web3.okx.com/zh-hans/onchainos/dev-docs/home/what-is-onchainos
3. 按文档注册 developer 账户（P1 后期再弄，不急）

---

## OKX API 关键端点（P2 会用）

| 端点 | 用途 |
|---|---|
| `/api/v5/market/tickers?instType=SWAP` | 所有永续合约行情 |
| `/api/v5/market/candles` | K 线 |
| `/api/v5/public/open-interest` | 持仓量 |
| `/api/v5/public/funding-rate` | 资金费率 |
| `/api/v5/rubik/stat/contracts/long-short-account-ratio` | 多空账户比 |
| `/api/v5/market/books` | 订单簿深度 |

---

## ✅ 完成检查清单

- [ ] OKX KYC Level 1 完成
- [ ] 创建了 `cresus-readonly` API
- [ ] **只勾了"只读"**
- [ ] 保存了 API Key / Secret / **Passphrase**（三个都要）
- [ ] 加了 IP 白名单
- [ ] `public/time` 接口能通
- [ ] 凭据存在本地安全位置

完成后告诉我 ✅，Exchange 部分就齐了，下一步决定 DeepSeek 什么时候开。
