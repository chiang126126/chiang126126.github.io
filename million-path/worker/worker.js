/*
 * Million Path —— Cloudflare Worker 代理
 * 作用：安全地为纯静态看板转发那些"CORS 关闭 / 需要隐藏 key"的数据源。
 *   - FRED（美联储宏观：利率、美债收益率、CPI）—— key 隐藏
 *   - Finnhub（美股实时报价 / 财报日历 / 公司新闻）—— key 隐藏
 *   - RSS（CoinDesk / Cointelegraph / Decrypt 等）—— 解决 RSS 无 CORS
 *
 * 部署见同目录 README.md。真实 key 放 Worker 环境变量，绝不进前端。
 */

// 只允许你自己的站点跨域调用（防止别人盗用你的 Worker 配额）
const ALLOWED_ORIGINS = [
  "https://chiang126126.github.io",
  "http://localhost:8000",
  "http://127.0.0.1:8000"
];

// 白名单：只允许转发到这些主机，防止 Worker 被当成开放代理滥用
const ALLOWED_HOSTS = [
  "api.stlouisfed.org",
  "finnhub.io",
  "www.coindesk.com",
  "cointelegraph.com",
  "decrypt.co",
  "www.theblock.co"
];

function corsHeaders(origin) {
  const allow = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin"
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const cors = corsHeaders(origin);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }

    const url = new URL(request.url);
    const type = url.searchParams.get("type");

    try {
      let target;

      if (type === "fred") {
        // 例：?type=fred&series=DGS10
        const series = encodeURIComponent(url.searchParams.get("series") || "");
        if (!env.FRED_KEY) throw new Error("FRED_KEY 未配置");
        target = `https://api.stlouisfed.org/fred/series/observations` +
          `?series_id=${series}&api_key=${env.FRED_KEY}&file_type=json` +
          `&sort_order=desc&limit=1`;

      } else if (type === "finnhub") {
        // 例：?type=finnhub&path=quote&qs=symbol=AAPL
        const path = url.searchParams.get("path") || "quote";
        const qs = url.searchParams.get("qs") || "";
        if (!env.FINNHUB_KEY) throw new Error("FINNHUB_KEY 未配置");
        target = `https://finnhub.io/api/v1/${path}?${qs}&token=${env.FINNHUB_KEY}`;

      } else if (type === "rss") {
        // 例：?type=rss&url=https://cointelegraph.com/rss
        target = url.searchParams.get("url") || "";

      } else {
        return json({ error: "unknown type" }, 400, cors);
      }

      // 主机白名单校验
      const host = new URL(target).hostname;
      if (!ALLOWED_HOSTS.includes(host)) {
        return json({ error: "host not allowed: " + host }, 403, cors);
      }

      const upstream = await fetch(target, {
        headers: { "User-Agent": "mp500-worker/1.0", "Accept": "*/*" },
        cf: { cacheTtl: 300, cacheEverything: true }
      });

      const contentType = upstream.headers.get("Content-Type") ||
        (type === "rss" ? "application/xml" : "application/json");
      const body = await upstream.text();

      return new Response(body, {
        status: upstream.status,
        headers: { ...cors, "Content-Type": contentType, "Cache-Control": "max-age=300" }
      });

    } catch (err) {
      return json({ error: String(err && err.message || err) }, 502, cors);
    }
  }
};

function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...cors, "Content-Type": "application/json" }
  });
}
