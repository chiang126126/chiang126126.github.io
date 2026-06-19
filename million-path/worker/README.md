# Million Path · Cloudflare Worker 代理部署指南

这个 Worker 让你的**纯静态看板**也能安全用上那些「CORS 关闭 / 需要隐藏 key」的数据源：
**FRED（美联储宏观）、Finnhub（美股实时）、RSS 新闻**。真实 key 存在 Worker 环境变量里，**永不进前端**。

Cloudflare Workers 免费档：**10 万请求/天**，对个人看板绰绰有余。

---

## 一、先拿两个免费 key（5 分钟）

1. **FRED key**（美联储数据，免费）：https://fredaccount.stlouisfed.org/apikeys → 注册 → 申请 API Key
2. **Finnhub key**（美股，免费 60 次/分）：https://finnhub.io/register → 登录后在 Dashboard 复制 API Key

> 只想要美股新闻和图表、不想配 Finnhub/FRED？也可以只部署 Worker 用 RSS 功能，或干脆跳过 Worker——看板会自动隐藏这几个板块，其余照常。

---

## 二、部署 Worker（网页操作，约 5 分钟）

1. 登录 https://dash.cloudflare.com → 左侧 **Workers & Pages** → **Create** → **Create Worker**
2. 给它起个名字（如 `mp500-proxy`）→ **Deploy**（先部署一个默认的）
3. 点 **Edit code**，把本目录 `worker.js` 的**全部内容**粘贴进去覆盖 → 右上 **Deploy**
4. 回到 Worker 的 **Settings → Variables and Secrets** → 添加两个 **Secret**：
   - `FRED_KEY` = 你的 FRED key
   - `FINNHUB_KEY` = 你的 Finnhub key
   - 保存后再 **Deploy** 一次
5. 复制 Worker 的访问地址，形如：`https://mp500-proxy.<你的子域>.workers.dev`

### 改白名单（重要）
打开 `worker.js` 顶部的 `ALLOWED_ORIGINS`，确认包含你的站点域名
`https://chiang126126.github.io`（已默认填好）。如果你的 Pages 域名不同，改成你的。

---

## 三、把地址填进看板

编辑仓库根目录的 `config.js`：

```js
window.MP_CONFIG = {
  WORKER_URL: "https://mp500-proxy.你的子域.workers.dev",   // ← 填这里
  ...
};
```

`git push` 后，看板的「美联储宏观 / 美股自选 / 品牌新闻」板块就会自动亮起来。

---

## 四、自测 Worker 是否通

浏览器直接打开（应返回 JSON，不报错）：

```
https://mp500-proxy.你的子域.workers.dev/?type=fred&series=DGS10
https://mp500-proxy.你的子域.workers.dev/?type=finnhub&path=quote&qs=symbol=AAPL
https://mp500-proxy.你的子域.workers.dev/?type=rss&url=https://cointelegraph.com/rss
```

- 第 1 个返回美债 10 年期收益率最新值
- 第 2 个返回 AAPL 实时报价（`c` 现价、`dp` 涨跌幅）
- 第 3 个返回 Cointelegraph RSS 的 XML

任何一个报 `*_KEY 未配置`，回第二步第 4 点补上对应 Secret 再 Deploy。

---

## 命令行部署（可选，给熟悉 wrangler 的人）

```bash
npm i -g wrangler
wrangler login
wrangler deploy worker.js --name mp500-proxy
wrangler secret put FRED_KEY
wrangler secret put FINNHUB_KEY
```

---

## 安全说明

- Worker 只允许从 `ALLOWED_ORIGINS` 跨域调用、只允许转发到 `ALLOWED_HOSTS` 白名单主机，避免被当成开放代理盗刷。
- FRED / Finnhub 的 key 只存在 Cloudflare 环境变量，前端和仓库里都看不到。
- 即便有人拿到你的 Worker 地址，最多消耗你的免费配额，不涉及任何资金或账户权限。
