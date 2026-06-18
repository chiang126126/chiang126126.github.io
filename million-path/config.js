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
  US_WATCHLIST: ["SPY", "QQQ", "MSTR", "NVDA", "COIN", "TSLA"],

  // —— MSTR / Strategy（微策略）BTC 持仓，手动维护 ——
  // 没有干净的免费 CORS API 提供实时持仓，这里手填、定期更新即可。
  // 看板用它 × 实时 BTC 价 估算 BTC 净值，再用 Finnhub 市值算 mNAV(溢价/折价)。
  MSTR_BTC_HOLDINGS: 0,            // 持有的 BTC 数量（0 = 未填，相关估算会提示你去填）
  MSTR_HOLDINGS_ASOF: "请更新",     // 上述数字的日期，如 "2026-06"
  MSTR_AVG_COST: 0,               // 平均买入成本(USD/BTC，可选，填 0 不显示)

  // 加密新闻额外接入的品牌 RSS（需配置 WORKER_URL）
  CRYPTO_RSS: [
    { name: "CoinDesk", url: "https://www.coindesk.com/arc/outboundfeeds/rss/" },
    { name: "Cointelegraph", url: "https://cointelegraph.com/rss" },
    { name: "Decrypt", url: "https://decrypt.co/feed" }
  ]
};
