/*
  Million Path 看板配置 —— 改这里就行，改完 git push 即可生效。
  说明：本文件会被浏览器直接加载，请勿在此放任何"能动钱"的密钥。
  真正的私密 key（FRED / Finnhub）放在 Cloudflare Worker 的环境变量里，永不进前端。
*/
window.MP_CONFIG = {
  // —— Cloudflare Worker 地址（部署后填，见 worker/README.md）——
  // 留空：美联储宏观 / 美股实时报价 / RSS 新闻 这几个板块会自动隐藏，其余照常工作。
  WORKER_URL: "",

  // —— 可选：Marketaux 美股新闻 key（免费 100 次/日，marketaux.com 注册）——
  // 这是低风险 key（只读、低额度）。建议去 Marketaux 后台设置域名白名单。留空则用 TradingView 新闻替代。
  MARKETAUX_KEY: "",

  // —— 可选：CryptoCompare key（一般留空也能用低额度的加密新闻）——
  CRYPTOCOMPARE_KEY: "",

  // 美股自选股（需配置 WORKER_URL，走 Finnhub 报价）
  US_WATCHLIST: ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "TSLA"],

  // 加密新闻额外接入的品牌 RSS（需配置 WORKER_URL）
  CRYPTO_RSS: [
    { name: "CoinDesk", url: "https://www.coindesk.com/arc/outboundfeeds/rss/" },
    { name: "Cointelegraph", url: "https://cointelegraph.com/rss" },
    { name: "Decrypt", url: "https://decrypt.co/feed" }
  ]
};
