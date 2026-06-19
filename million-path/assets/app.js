/* Million Path 看板逻辑 —— 每个面板独立容错，单个数据源失败不影响其它。 */
"use strict";
const CFG = window.MP_CONFIG || {};
const STATE = {};  // 收集各面板最新数据，供「AI 策略快照」生成 Evidence Document

//==================== 工具 ====================
const $ = id => document.getElementById(id);
const fmt = (n, d = 2) => (n == null || isNaN(n)) ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d });
const pct = n => (n == null || isNaN(n)) ? "—" : (n > 0 ? "+" : "") + fmt(n, 2) + "%";
const money = n => n == null ? "—" : "$" + fmt(n, n < 10 ? 4 : n < 1000 ? 2 : 0);
const big = n => { // 大数字：1.23T / 45.6B / 789M
  if (n == null || isNaN(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  return "$" + fmt(n, 0);
};
const cls = n => n == null ? "" : (n >= 0 ? "pos" : "neg");
const todayISO = () => new Date().toISOString().slice(0, 10);
const ago = ts => { // 时间戳(秒) -> "x小时前"
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return s + "秒前";
  if (s < 3600) return Math.floor(s / 60) + "分钟前";
  if (s < 86400) return Math.floor(s / 3600) + "小时前";
  return Math.floor(s / 86400) + "天前";
};
const jget = async (url, opt) => { const r = await fetch(url, opt); if (!r.ok) throw new Error(r.status); return r.json(); };
// 直连失败时自动改走 Worker（绕过 CORS/地区限制）。无 Worker 时仍抛原错误。
const viaWorker = u => `${(CFG.WORKER_URL || "").replace(/\/$/, "")}/?type=get&url=${encodeURIComponent(u)}`;
const jgetSmart = async (url, opt) => {
  try { return await jget(url, opt); }
  catch (e) { if (CFG.WORKER_URL && CFG.WORKER_URL.trim()) return jget(viaWorker(url), opt); throw e; }
};

//==================== 数据源主机 ====================
const BN_SPOT = "https://data-api.binance.vision";   // 行情专用主机，比 api.binance.com 更稳/更易直连
const BN_FUT  = "https://fapi.binance.com";
const CG      = "https://api.coingecko.com/api/v3";
const CC      = "https://min-api.cryptocompare.com";

const CORE = [
  { sym: "BTCUSDT", cg: "bitcoin", name: "BTC" },
  { sym: "ETHUSDT", cg: "ethereum", name: "ETH" },
  { sym: "SOLUSDT", cg: "solana", name: "SOL" },
  { sym: "BNBUSDT", cg: "binancecoin", name: "BNB" },
];
const CORE3 = ["BTC", "ETH", "SOL"];

//==================== 行情 ====================
async function fetchTickers() {
  const syms = encodeURIComponent(JSON.stringify(CORE.map(c => c.sym)));
  try {
    const d = await jget(`${BN_SPOT}/api/v3/ticker/24hr?symbols=${syms}`);
    return { src: "Binance", rows: CORE.map(c => { const x = d.find(o => o.symbol === c.sym) || {};
      return { ...c, price: +x.lastPrice, chg: +x.priceChangePercent, high: +x.highPrice, low: +x.lowPrice, vol: +x.quoteVolume }; }) };
  } catch (e) {
    const ids = CORE.map(c => c.cg).join(",");
    const d = await jget(`${CG}/simple/price?ids=${ids}&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true`);
    return { src: "CoinGecko（备用源）", rows: CORE.map(c => { const x = d[c.cg] || {};
      return { ...c, price: x.usd, chg: x.usd_24h_change, high: null, low: null, vol: x.usd_24h_vol }; }) };
  }
}
async function fetchCloses(c) {
  try {
    const d = await jget(`${BN_SPOT}/api/v3/klines?symbol=${c.sym}&interval=1d&limit=31`);
    return d.map(k => +k[4]);
  } catch (e) {
    const d = await jget(`${CG}/coins/${c.cg}/market_chart?vs_currency=usd&days=30&interval=daily`);
    return (d.prices || []).map(p => p[1]);
  }
}
// 合约资金费率 + 持仓量（对应 MP500 风控里的资金费率/OI 检查）
async function fetchFundingOI(c) {
  try {
    const [pi, oi] = await Promise.all([
      jget(`${BN_FUT}/fapi/v1/premiumIndex?symbol=${c.sym}`),
      jget(`${BN_FUT}/fapi/v1/openInterest?symbol=${c.sym}`)
    ]);
    return { funding: +pi.lastFundingRate * 100, nextFunding: +pi.nextFundingTime, oi: +oi.openInterest };
  } catch (e) { return null; }
}
async function fetchFNG() { const d = await jgetSmart("https://api.alternative.me/fng/?limit=1"); return d.data[0]; }
// 加密宏观改用 CoinPaprika（免费、无 key、限额更宽，避开 CoinGecko 429）
async function fetchGlobal() { return await jgetSmart("https://api.coinpaprika.com/v1/global"); }

//==================== 渲染：行情 + 趋势 + 合约 ====================
function sparkSVG(vals, up) {
  if (!vals || vals.length < 2) return "";
  const W = 240, H = 46, min = Math.min(...vals), max = Math.max(...vals), rng = max - min || 1;
  const x = i => i * W / (vals.length - 1), y = v => H - 2 - (v - min) / rng * (H - 4);
  const dp = vals.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  const col = up ? "#16c784" : "#ea3943";
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="g${up ? 1 : 0}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${col}" stop-opacity=".35"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <path d="${dp} L ${W} ${H} L 0 ${H} Z" fill="url(#g${up ? 1 : 0})"/>
    <path d="${dp}" fill="none" stroke="${col}" stroke-width="2"/></svg>`;
}
function regimeOf(price, sma) {
  if (price == null || sma == null) return { key: "neutral", label: "数据不足" };
  const dev = (price / sma - 1) * 100;
  if (dev >= 1) return { key: "risk-on", label: "站上30日线", dev };
  if (dev <= -1) return { key: "risk-off", label: "跌破30日线", dev };
  return { key: "neutral", label: "贴近30日线·震荡", dev };
}
const ADVICE = {
  "risk-on": "仓位上限 60–80%｜目标 +3%~+5%｜顺势做多，趋势单拿盈亏比",
  "neutral": "仓位上限 0–30%｜目标 0%~+2%｜少打，只做高分信号",
  "risk-off": "仓位上限 0–20%｜目标 0%（保本第一）｜多拿 USDT，可小仓做空(S2后)"
};

async function loadMarket() {
  let tick;
  try { tick = await fetchTickers(); }
  catch (e) { $("coinGrid").innerHTML = `<div class="err">行情源暂不可用（可能网络/地区限制）。复盘与测算不受影响。</div>`; return; }
  $("mktSrc").textContent = "数据源：" + tick.src;

  const closesArr = await Promise.all(CORE.map(c => fetchCloses(c).catch(() => [])));
  const foArr = await Promise.all(CORE.map(c => fetchFundingOI(c)));

  $("coinGrid").innerHTML = tick.rows.map((c, i) => {
    const closes = closesArr[i], up = c.chg >= 0, fo = foArr[i];
    const fund = fo ? `<span class="${cls(-Math.sign(fo.funding))}">资费 ${fo.funding >= 0 ? '+' : ''}${fo.funding.toFixed(4)}%</span>` : "";
    return `<div class="card coin">
      <div class="row"><div><div class="nm">${c.name}<span class="badge"> /USDT</span></div>
        <div class="px" id="px-${c.name}">${money(c.price)}</div></div>
        <div class="tag ${up ? 'risk-on' : 'risk-off'}" id="chg-${c.name}">${pct(c.chg)}</div></div>
      ${sparkSVG(closes.slice(-30), up)}
      <div class="meta"><span>高 ${money(c.high)}</span><span>低 ${money(c.low)}</span>
        <span>额 ${c.vol ? '$' + fmt(c.vol / 1e6, 0) + 'M' : '—'}</span></div>
      ${fo ? `<div class="meta">${fund}<span>OI ${fmt(fo.oi, 0)} ${c.name}</span></div>` : ""}
    </div>`;
  }).join("");

  // 趋势表
  const tb = tick.rows.map((c, i) => {
    const closes = closesArr[i]; if (!CORE3.includes(c.name)) return null;
    const sma = closes.length >= 30 ? closes.slice(-30).reduce((a, b) => a + b, 0) / 30 : null;
    const rg = regimeOf(c.price, sma);
    return `<tr><td><b>${c.name}</b></td>
      <td><span class="chip ${rg.key}">${rg.label}</span></td>
      <td>${money(c.price)}</td><td>${money(sma)}</td>
      <td class="${cls(rg.dev)}">${rg.dev == null ? '—' : pct(rg.dev)}</td>
      <td class="badge">${rg.key === 'risk-on' ? '偏多，可顺势' : rg.key === 'risk-off' ? '偏空，防守为主' : '方向不明，少动'}</td></tr>`;
  }).filter(Boolean).join("");
  $("trendBody").innerHTML = tb || `<tr><td colspan="6" class="badge">K线数据暂不可用</td></tr>`;

  // Hero：BTC 趋势
  const btc = tick.rows.find(c => c.name === "BTC");
  const btcCloses = closesArr[CORE.findIndex(c => c.name === "BTC")] || [];
  const btcSma = btcCloses.length >= 30 ? btcCloses.slice(-30).reduce((a, b) => a + b, 0) / 30 : null;
  const brg = regimeOf(btc?.price, btcSma);
  const hr = $("heroRegime"); hr.className = "chip " + brg.key;
  hr.textContent = `BTC ${brg.label}${brg.dev != null ? '（' + pct(brg.dev) + '）' : ''}`;
  $("heroAdvice").textContent = ADVICE[brg.key];
  // 供 Evidence 快照使用
  STATE.btcRegime = brg;
  STATE.market = tick.rows.map((c, i) => ({ name: c.name, price: c.price, chg: c.chg, funding: foArr[i] ? foArr[i].funding : null }));
  $("updated").textContent = "更新于 " + new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

//==================== 加密宏观（CoinGecko global）====================
async function loadCryptoMacro() {
  try {
    const g = await fetchGlobal();  // CoinPaprika /v1/global
    const mc = g.market_cap_usd, chg = g.market_cap_change_24h, btcD = g.bitcoin_dominance_percentage,
      vol = g.volume_24h_usd, n = g.cryptocurrencies_number;
    STATE.macro = { mc, chg, btcD };
    const cards = [
      ["加密总市值", big(mc), `<span class="${cls(chg)}">${pct(chg)}</span>`],
      ["BTC 占比", btcD != null ? btcD.toFixed(1) + "%" : "—", "主导率"],
      ["24h 成交额", big(vol), "全市场"],
      ["活跃币种", n != null ? fmt(n, 0) : "—", "CoinPaprika"],
    ];
    $("macroCards").innerHTML = cards.map(([k, v, sub]) =>
      `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="badge">${sub}</div></div>`).join("");
  } catch (e) { $("macroCards").innerHTML = `<div class="err">加密宏观数据暂不可用</div>`; }
}

//==================== 链上 TVL（DeFiLlama）====================
async function loadDefi() {
  try {
    const chains = await jgetSmart("https://api.llama.fi/v2/chains");
    const total = chains.reduce((s, c) => s + (c.tvl || 0), 0);
    const top = chains.slice().sort((a, b) => b.tvl - a.tvl).slice(0, 6);
    $("defiTotal").innerHTML = big(total);
    $("defiChains").innerHTML = top.map(c =>
      `<div class="ev" style="padding:8px 0"><div class="t"><b>${c.name}</b></div>
       <div class="d" style="text-align:right;min-width:90px">${big(c.tvl)}</div></div>`).join("");
  } catch (e) { $("defiBox").innerHTML = `<div class="err">链上 TVL 数据暂不可用（DeFiLlama）</div>`; }
}

//==================== 情绪 + 事件 ====================
async function loadSentiment() {
  try {
    const f = await fetchFNG(); const v = +f.value;
    $("fngVal").textContent = v;
    $("fngVal").style.color = v < 25 ? "#ea3943" : v < 45 ? "#f0b90b" : v < 55 ? "#cbd5e1" : v < 75 ? "#84cc16" : "#16c784";
    $("fngMark").style.left = v + "%";
    const map = { "Extreme Fear": "极度恐惧", "Fear": "恐惧", "Neutral": "中性", "Greed": "贪婪", "Extreme Greed": "极度贪婪" };
    STATE.fng = { v, label: map[f.value_classification] || f.value_classification };
    $("fngLabel").textContent = (map[f.value_classification] || f.value_classification) + " · 越低越恐慌（潜在机会），越高越贪婪（注意风险）";
  } catch (e) { $("fngVal").textContent = "—"; $("fngLabel").innerHTML = '<span class="err">情绪指数暂不可用</span>'; }
}
async function loadEvents() {
  let data; try { data = await jget("./data/events.json", { cache: "no-store" }); }
  catch (e) { $("eventsList").innerHTML = '<span class="badge">未找到 data/events.json</span>'; return; }
  const today = todayISO();
  const evs = (data.events || []).slice().sort((a, b) => a.date < b.date ? -1 : 1);
  $("eventsList").innerHTML = evs.map(e => {
    const soon = e.date >= today;
    return `<div class="ev imp-${e.impact || 'low'}">
      <div class="d">${e.date.slice(5)}<br><span class="evtag ${soon ? 'soon' : ''}">${soon ? '未来' : '已过'}</span></div>
      <div class="t"><b>${e.title}</b> <span class="evtag">${e.type || ''}</span><p>${e.note || ''}</p></div></div>`;
  }).join("") || '<span class="badge">暂无事件，去 data/events.json 添加</span>';
}

//==================== 加密新闻（CryptoCompare 直连 + 可选 RSS）====================
function stampNews() { const nt = $("newsTime"); if (nt) nt.textContent = "更新于 " + new Date().toLocaleTimeString("zh-CN"); }
async function loadCryptoNews() {
  const box = $("cryptoNews");
  // 1) 优先 CryptoCompare（直连 → 失败自动走 Worker）
  try {
    const key = CFG.CRYPTOCOMPARE_KEY ? `&api_key=${CFG.CRYPTOCOMPARE_KEY}` : "";
    const d = await jgetSmart(`${CC}/data/v2/news/?lang=EN${key}`);
    const items = (d.Data || []).slice(0, 12).map(n => ({
      title: n.title, url: n.url, source: (n.source_info && n.source_info.name) || n.source, ts: n.published_on,
      img: n.imageurl, cats: (n.categories || "").split("|").slice(0, 2).join(" · ")
    }));
    if (!items.length) throw new Error("empty");
    STATE.news = items.slice(0, 6).map(n => n.title);
    box.innerHTML = renderNews(items); stampNews(); return;
  } catch (e) { /* 进入 RSS 回退 */ }
  // 2) 回退：品牌 RSS（CoinDesk / Cointelegraph / Decrypt，经 Worker）
  try {
    const items = await fetchRssNews();
    if (!items.length) throw new Error("empty");
    STATE.news = items.slice(0, 6).map(n => n.title);
    box.innerHTML = renderNews(items); stampNews(); return;
  } catch (e) {
    box.innerHTML = `<div class="err">加密新闻暂不可用。CryptoCompare 受限、且 RSS 回退需要已部署的 Worker（见 worker/README.md）。</div>`;
  }
}
async function fetchRssNews() {
  if (!hasWorker() || typeof DOMParser === "undefined") return [];
  const feeds = (CFG.CRYPTO_RSS || []).slice(0, 2);
  const all = [];
  for (const f of feeds) {
    try {
      const xml = await (await fetch(wurl(`type=rss&url=${encodeURIComponent(f.url)}`))).text();
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      Array.from(doc.querySelectorAll("item")).slice(0, 8).forEach(it => {
        const g = s => { const el = it.querySelector(s); return el ? el.textContent : ""; };
        all.push({ title: g("title"), url: g("link") || "#", source: f.name,
          ts: Math.floor(Date.parse(g("pubDate")) / 1000) || null, img: null, cats: "" });
      });
    } catch (e) {}
  }
  return all.filter(x => x.title).sort((a, b) => (b.ts || 0) - (a.ts || 0)).slice(0, 12);
}
function renderNews(items) {
  if (!items.length) return '<span class="badge">暂无新闻</span>';
  return items.map(n => `<a class="news" href="${n.url}" target="_blank" rel="noopener">
    ${n.img ? `<img src="${n.img}" loading="lazy" alt="">` : ""}
    <div class="news-t"><b>${n.title}</b>
      <div class="badge">${n.source || ''}${n.cats ? ' · ' + n.cats : ''} · ${n.ts ? ago(n.ts) : ''}</div></div></a>`).join("");
}

//==================== Worker 板块：美联储宏观 / 美股自选 ====================
const hasWorker = () => !!(CFG.WORKER_URL && CFG.WORKER_URL.trim());
const wurl = (qs) => `${CFG.WORKER_URL.replace(/\/$/, "")}/?${qs}`;

async function loadFred() {
  if (!hasWorker()) { $("fredBox").innerHTML = workerHint(); return; }
  const series = [
    { id: "FEDFUNDS", label: "联邦基金利率", suf: "%" },
    { id: "DGS2", label: "美债 2 年", suf: "%" },
    { id: "DGS10", label: "美债 10 年", suf: "%" },
    { id: "CPIAUCSL", label: "CPI 指数", suf: "" },
  ];
  try {
    const out = await Promise.all(series.map(async s => {
      try { const d = await jget(wurl(`type=fred&series=${s.id}`));
        const o = (d.observations || [])[0]; return { ...s, val: o ? o.value : null, date: o ? o.date : "" }; }
      catch (e) { return { ...s, val: null }; }
    }));
    STATE.fred = out.filter(s => s.val != null).map(s => `${s.label} ${s.val}${s.suf}`);
    $("fredBox").innerHTML = out.map(s =>
      `<div class="card"><div class="k">${s.label}</div><div class="v">${s.val == null ? "—" : s.val + s.suf}</div>
       <div class="badge">${s.date || ''}</div></div>`).join("");
  } catch (e) { $("fredBox").innerHTML = `<div class="err">美联储数据获取失败，检查 Worker 与 FRED_KEY。</div>`; }
}
async function loadUsStocks() {
  if (!hasWorker()) { $("usBox").innerHTML = workerHint(); return; }
  const list = CFG.US_WATCHLIST || [];
  try {
    const out = await Promise.all(list.map(async sym => {
      try { const q = await jget(wurl(`type=finnhub&path=quote&qs=symbol=${sym}`));
        return { sym, price: q.c, chg: q.dp }; } catch (e) { return { sym, price: null, chg: null }; }
    }));
    $("usBox").innerHTML = out.map(s => `<div class="card">
      <div class="k">${s.sym}</div><div class="v">${s.price == null ? "—" : "$" + fmt(s.price, 2)}</div>
      <div class="badge ${cls(s.chg)}">${pct(s.chg)}</div></div>`).join("");
  } catch (e) { $("usBox").innerHTML = `<div class="err">美股报价获取失败，检查 Worker 与 FINNHUB_KEY。</div>`; }
}
function workerHint() {
  return `<div class="hint">本板块需要免费的 Cloudflare Worker（隐藏 key）。
    按 <code>worker/README.md</code> 部署后，把地址填进 <code>config.js</code> 的 <code>WORKER_URL</code> 即可自动亮起。</div>`;
}

//==================== MSTR / 微策略（BTC 杠杆代理 + mNAV）====================
async function loadMstr() {
  const box = $("mstrBox");
  const holdings = +CFG.MSTR_BTC_HOLDINGS || 0;
  const asof = CFG.MSTR_HOLDINGS_ASOF || "";
  if (!hasWorker()) {
    box.innerHTML = workerHint() +
      `<div class="badge" style="margin-top:8px">MSTR 持仓(手动)：${holdings ? fmt(holdings, 0) + " BTC（" + asof + "）" : "未填，见 config.js MSTR_BTC_HOLDINGS"}。配置 Worker 后显示实时报价与 mNAV 溢价/折价。</div>`;
    return;
  }
  try {
    const [q, prof, btcT] = await Promise.all([
      jget(wurl("type=finnhub&path=quote&qs=symbol=MSTR")),
      jget(wurl("type=finnhub&path=stock/profile2&qs=symbol=MSTR")).catch(() => ({})),
      jget(`${BN_SPOT}/api/v3/ticker/price?symbol=BTCUSDT`).catch(() => null)
    ]);
    const price = q.c, chg = q.dp;
    const mcap = prof.marketCapitalization ? prof.marketCapitalization * 1e6 : null; // Finnhub 单位为百万
    const btc = btcT ? +btcT.price : null;
    const navBtc = (holdings && btc) ? holdings * btc : null;     // BTC 持仓净值
    const mnav = (mcap && navBtc) ? mcap / navBtc : null;          // >1 溢价，<1 折价
    STATE.mstr = { price, chg, mnav };
    const cards = [
      ["MSTR 现价", price == null ? "—" : "$" + fmt(price, 2), `<span class="${cls(chg)}">${pct(chg)} (当日)</span>`],
      ["公司市值", mcap ? big(mcap) : "—", "Finnhub"],
      ["BTC 持仓净值", navBtc ? big(navBtc) : "—", holdings ? fmt(holdings, 0) + " BTC × 实时价" : "请填 config.js"],
      ["mNAV 溢价/折价", mnav ? mnav.toFixed(2) + "x" : "—",
        mnav ? `<span class="${mnav >= 1 ? 'neg' : 'pos'}">${mnav >= 1 ? '溢价 +' + ((mnav - 1) * 100).toFixed(0) + '%' : '折价 ' + ((mnav - 1) * 100).toFixed(0) + '%'}</span>` : "需填持仓"],
    ];
    box.innerHTML = cards.map(([k, v, sub]) =>
      `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="badge">${sub}</div></div>`).join("") +
      `<div class="badge" style="grid-column:1/-1;margin-top:4px">持仓为手动维护(${asof})。mNAV>1=市场给 BTC 持仓溢价(情绪偏热)；<1=折价(情绪偏冷)，是加密情绪的重要旁证。</div>`;
  } catch (e) {
    box.innerHTML = `<div class="err">MSTR 数据获取失败，检查 Worker 与 FINNHUB_KEY。</div>`;
  }
}

//==================== 交易复盘 + 统计（同源 ledger）====================
const SAMPLE = { meta: { strategy: "MP500", start_date: "2026-06-19", initial_capital: 500, usdt_cny: 6.74, target_rmb: 1e6, target_usd: 1e6 },
  weeks: [{ week: 1, start_date: "2026-06-19", end_date: "2026-06-25", stage: "S0", regime: "neutral", equity_start: 500, equity_end: 500, target_return_pct: 0, trades: 0, win_rate_pct: null, profit_factor: null, max_drawdown_pct: 0, fees_funding_pct: 0, violations: 0 }] };

function equityChart(vals, base) {
  const el = $("equityChart"); if (!vals.length) { el.innerHTML = '<span class="badge">暂无数据</span>'; return; }
  const data = [base, ...vals], W = 1000, H = 170, pad = 26;
  const min = Math.min(...data), max = Math.max(...data), rng = max - min || 1;
  const x = i => pad + i * (W - 2 * pad) / (data.length - 1 || 1), y = v => H - pad - (v - min) / rng * (H - 2 * pad);
  const dp = data.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:170px" preserveAspectRatio="none">
    <defs><linearGradient id="eg" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#f0b90b" stop-opacity=".3"/><stop offset="1" stop-color="#f0b90b" stop-opacity="0"/></linearGradient></defs>
    <path d="${dp} L ${x(data.length - 1)} ${H - pad} L ${pad} ${H - pad} Z" fill="url(#eg)"/>
    <path d="${dp}" fill="none" stroke="#f0b90b" stroke-width="2.5"/>
    <text x="${pad}" y="16" fill="#8aa0bd" font-size="13">峰值 ${fmt(max, 0)}U</text>
    <text x="${pad}" y="${H - 7}" fill="#8aa0bd" font-size="13">谷值 ${fmt(min, 0)}U</text></svg>`;
}
function renderLedger(d) {
  const m = d.meta, w = d.weeks || [];
  let peak = m.initial_capital, maxDD = 0;
  w.forEach(x => { x._ret = x.equity_start ? ((x.equity_end / x.equity_start) - 1) * 100 : null;
    peak = Math.max(peak, x.equity_end); maxDD = Math.max(maxDD, (peak - x.equity_end) / peak * 100); });
  const last = w[w.length - 1] || { equity_end: m.initial_capital, stage: "S0" };
  const cum = ((last.equity_end / m.initial_capital) - 1) * 100;
  STATE.portfolio = { equity: last.equity_end, stage: last.stage, weeks: w.length };
  const viol = w.reduce((s, x) => s + (x.violations || 0), 0);
  const rets = w.filter(x => x._ret != null).map(x => x._ret);
  const avg = rets.length ? rets.reduce((a, b) => a + b, 0) / rets.length : null;
  const wr = w.filter(x => x.win_rate_pct != null); const avgWr = wr.length ? wr.reduce((a, b) => a + b.win_rate_pct, 0) / wr.length : null;
  const pf = w.filter(x => x.profit_factor != null); const avgPf = pf.length ? pf.reduce((a, b) => a + b.profit_factor, 0) / pf.length : null;

  $("heroEquity").innerHTML = fmt(last.equity_end, 0) + ' <small>USDT</small>';
  $("heroCum").innerHTML = `<span class="${cls(cum)}">${pct(cum)}</span>`;
  $("heroStage").textContent = last.stage;
  $("heroWeeks").textContent = w.length + " 周";
  $("heroViol").innerHTML = `<span class="${viol > 0 ? 'warn' : 'pos'}">${viol}</span>`;

  $("reviewCards").innerHTML = [
    ["当前权益", fmt(last.equity_end, 0) + " U"], ["累计收益", `<span class="${cls(cum)}">${pct(cum)}</span>`],
    ["平均周收益", `<span class="${cls(avg)}">${pct(avg)}</span>`], ["最大回撤", fmt(maxDD, 1) + "%"],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  $("statCards").innerHTML = [
    ["总周数", w.length], ["平均胜率", avgWr == null ? "—" : fmt(avgWr, 0) + "%"],
    ["平均盈亏比", fmt(avgPf, 2)], ["最大回撤", fmt(maxDD, 1) + "%"],
    ["累计违规", `<span class="${viol > 0 ? 'warn' : 'pos'}">${viol}</span>`], ["目标(USD)", "$1,000,000"],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  $("kpiBody").innerHTML = w.map(x => `<tr>
    <td>${x.week}</td><td>${x.start_date}</td><td>${x.stage}</td>
    <td><span class="chip ${x.regime}">${x.regime}</span></td>
    <td class="${cls(x._ret)}">${pct(x._ret)}</td>
    <td>${x.target_return_pct != null ? x.target_return_pct + "%" : "—"}</td>
    <td>${x.trades ?? "—"}</td><td>${x.win_rate_pct != null ? x.win_rate_pct + "%" : "—"}</td>
    <td>${fmt(x.profit_factor)}</td><td>${x.max_drawdown_pct != null ? x.max_drawdown_pct + "%" : "—"}</td>
    <td>${x.fees_funding_pct != null ? x.fees_funding_pct + "%" : "—"}</td>
    <td class="${x.violations > 0 ? 'warn' : ''}">${x.violations ?? 0}</td></tr>`).join("");

  equityChart(w.map(x => x.equity_end), m.initial_capital);
  return m;
}
async function loadLedger() {
  let d; try { d = await jget("./data/ledger.json", { cache: "no-store" }); } catch (e) { d = SAMPLE; }
  const m = renderLedger(d);
  if (m) { $("cFx").value = m.usdt_cny || 6.74; $("cStart").value = m.initial_capital || 500;
    $("cRmb").value = m.target_rmb || 1e6; $("cUsd").value = m.target_usd || 1e6; renderCalc(); }
}

//==================== 复利测算 ====================
const weeksTo = (s, r, t) => r <= 0 ? (t <= s ? 0 : Infinity) : Math.log(t / s) / Math.log(1 + r);
const dur = w => !isFinite(w) ? "—" : `${Math.round(w)} 周 (${(w / 52).toFixed(1)} 年)`;
function renderCalc() {
  const s = +$("cStart").value, r = (+$("cRate").value) / 100, fx = +$("cFx").value, rmb = +$("cRmb").value, usd = +$("cUsd").value;
  const tR = rmb / fx, tU = usd, yr = (Math.pow(1 + r, 52) - 1) * 100;
  $("calcOut").innerHTML = `周化 <b>${(r * 100).toFixed(1)}%</b> ≈ 年化(复利) <b>${fmt(yr, 0)}%</b><br>` +
    `${fmt(rmb, 0)} 人民币 ≈ <b>${fmt(tR, 0)} U</b> → 需 <b>${dur(weeksTo(s, r, tR))}</b><br>` +
    `${fmt(usd, 0)} 美元 ≈ <b>${fmt(tU, 0)} U</b> → 需 <b>${dur(weeksTo(s, r, tU))}</b>`;
  $("scenBody").innerHTML = [0.02, 0.025, 0.03, 0.05, 0.08, 0.10].map(rr => {
    const y = (Math.pow(1 + rr, 52) - 1) * 100;
    return `<tr><td>${(rr * 100).toFixed(1)}%</td><td>${fmt(y, 0)}%</td><td>${dur(weeksTo(s, rr, tR))}</td><td>${dur(weeksTo(s, rr, tU))}</td></tr>`;
  }).join("");
}

//==================== AI 策略快照（Evidence Document 生成器）====================
// 把当前看板数据汇成结构化文本，供你粘进任意 LLM，让它回一个决策元组。
// 零 API 成本、无后端；人 + 风控引擎始终最终拍板。
function buildEvidence() {
  const t = new Date().toLocaleString("zh-CN");
  const p = STATE.portfolio || {};
  const mkt = (STATE.market || []).map(c =>
    `  - ${c.name}: ${money(c.price)} (${pct(c.chg)} 24h)${c.funding != null ? `, 资金费率 ${c.funding.toFixed(4)}%` : ""}`).join("\n") || "  - 暂无";
  const reg = STATE.btcRegime ? `${STATE.btcRegime.label}${STATE.btcRegime.dev != null ? "（偏离30日线 " + pct(STATE.btcRegime.dev) + "）" : ""}` : "未知";
  const macro = STATE.macro ? `总市值 ${big(STATE.macro.mc)}（24h ${pct(STATE.macro.chg)}）, BTC占比 ${STATE.macro.btcD != null ? STATE.macro.btcD.toFixed(1) + "%" : "—"}` : "暂无";
  const fng = STATE.fng ? `${STATE.fng.v}（${STATE.fng.label}）` : "暂无";
  const mstr = STATE.mstr ? `现价 $${fmt(STATE.mstr.price, 2)}（${pct(STATE.mstr.chg)}）${STATE.mstr.mnav ? ", mNAV " + STATE.mstr.mnav.toFixed(2) + "x" : ""}` : "暂无（需 Worker）";
  const fred = (STATE.fred && STATE.fred.length) ? STATE.fred.join(" | ") : "暂无（需 Worker）";
  const news = (STATE.news && STATE.news.length) ? STATE.news.map((h, i) => `  ${i + 1}. ${h}`).join("\n") : "  暂无";

  return `# MP500 Evidence Document（${t}）

## 组合
- 阶段 ${p.stage || "S0"} | 权益 ${p.equity != null ? fmt(p.equity, 0) + "U" : "—"} | 已运行 ${p.weeks || 0} 周

## 市场结构（核心池）
${mkt}
- BTC 行情状态（30日线法）: ${reg}

## 加密宏观
- ${macro}
- 恐惧贪婪指数: ${fng}

## MSTR / 微策略（BTC 杠杆代理）
- ${mstr}

## 美股 / 宏观（FRED）
- ${fred}

## 近期加密新闻头条
${news}

---
请你扮演 MP500 的战略分析师，基于以上 Evidence，按如下 JSON 给出决策元组（只做参谋，不替我下单）：
{
  "bias": "LONG | FLAT",            // S2 前不做 SHORT
  "confidence": 0.0,                // 0~1
  "expected_move_bps": 0,           // 预期幅度（基点）
  "rationale": "识别到的形态/催化剂/叙事，及为什么",
  "risk_flags": ["需要警惕的风险，如资金费率过热/流动性/宏观事件"],
  "abstain_ok": true                // 行情不明确时，FLAT/空仓是允许且常常正确的
}
要求：风控优先；不确定就给 FLAT。`;
}
function showEvidence() {
  const box = $("evidenceOut");
  if (box) box.value = buildEvidence();
  const t = $("evTime"); if (t) t.textContent = "生成于 " + new Date().toLocaleTimeString("zh-CN");
}
function copyEvidence() {
  const box = $("evidenceOut");
  if (!box || !box.value) showEvidence();
  if (box && box.value) {
    navigator.clipboard && navigator.clipboard.writeText(box.value);
    const b = $("copyEvBtn"); if (b) { const o = b.textContent; b.textContent = "已复制 ✓"; setTimeout(() => b.textContent = o, 1500); }
  }
}

//==================== 实时价格（Binance WebSocket，逐秒推送）====================
// @ticker 流约每 1 秒推一次，含最新价/24h涨跌/高低/量。WebSocket 不受 CORS 限制；
// 若所在地区屏蔽 Binance，连接失败会自动回退到 60 秒轮询（不影响其它面板）。
const _lastPx = {};
function applyLivePrice(name, price, chg) {
  if (STATE.market) { const e = STATE.market.find(x => x.name === name); if (e) { e.price = price; e.chg = chg; } }
  const pxEl = $("px-" + name);
  if (pxEl) {
    const prev = _lastPx[name];
    pxEl.textContent = money(price);
    if (prev != null && price !== prev) {
      pxEl.classList.remove("flash-up", "flash-down");
      void pxEl.offsetWidth;                       // 重排以重启动画
      pxEl.classList.add(price > prev ? "flash-up" : "flash-down");
    }
  }
  _lastPx[name] = price;
  const chgEl = $("chg-" + name);
  if (chgEl) { chgEl.textContent = pct(chg); chgEl.className = "tag " + (chg >= 0 ? "risk-on" : "risk-off"); }
}

const LIVE = {
  ws: null, on: false, backoff: 1000, timer: null,
  streams: CORE.map(c => c.sym.toLowerCase() + "@ticker").join("/"),
  start() { if (this.on) return; this.on = true; this._open(); setLiveUI(true); },
  stop() { this.on = false; this._clear(); if (this.ws) { try { this.ws.close(); } catch (e) {} this.ws = null; } setLiveUI(false); },
  _clear() { if (this.timer) { clearTimeout(this.timer); this.timer = null; } },
  _open() {
    if (!this.on || typeof WebSocket === "undefined") return;
    try {
      const ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${this.streams}`);
      this.ws = ws;
      ws.onopen = () => { this.backoff = 1000; liveDot("on"); };
      ws.onmessage = ev => {
        try {
          const d = JSON.parse(ev.data).data; if (!d || !d.s) return;
          const core = CORE.find(c => c.sym === d.s); if (!core) return;
          applyLivePrice(core.name, +d.c, +d.P);
          const u = $("updated"); if (u) u.textContent = "实时 " + new Date().toLocaleTimeString("zh-CN");
        } catch (e) {}
      };
      ws.onclose = () => { liveDot("off"); this._reconnect(); };
      ws.onerror = () => { try { ws.close(); } catch (e) {} };
    } catch (e) { this._reconnect(); }
  },
  _reconnect() { if (!this.on) return; this._clear(); this.backoff = Math.min(this.backoff * 2, 30000); this.timer = setTimeout(() => this._open(), this.backoff); }
};
function liveDot(state) {
  const d = $("liveDot"); if (!d) return;
  d.style.background = state === "on" ? "var(--green)" : "var(--muted)";
  d.style.boxShadow = state === "on" ? "0 0 8px var(--green)" : "none";
}
function setLiveUI(on) {
  const b = $("liveToggle"); if (b) b.textContent = on ? "🟢 实时" : "⚪ 省流";
  liveDot(on ? "on" : "off");
}
function toggleLive() {
  const next = !LIVE.on;
  try { localStorage.setItem("mp_live", next ? "1" : "0"); } catch (e) {}
  next ? LIVE.start() : LIVE.stop();
}

//==================== 启动 ====================
async function refreshLive() {
  // 并行刷新所有信息面板，等全部结束后再（可选）自动重建 Evidence
  await Promise.allSettled([
    loadMarket(), loadSentiment(), loadCryptoMacro(), loadDefi(),
    loadCryptoNews(), loadFred(), loadUsStocks(), loadMstr()
  ]);
  // AI 策略快照自动重生成：用户正在框选/编辑该文本框时跳过，避免打断复制
  if (CFG.AUTO_EVIDENCE !== false && document.activeElement !== $("evidenceOut")) {
    showEvidence();
  }
}
function init() {
  ["cStart", "cRate", "cFx", "cRmb", "cUsd"].forEach(id => $(id) && $(id).addEventListener("input", renderCalc));
  $("refreshBtn") && $("refreshBtn").addEventListener("click", refreshLive);
  $("genEvBtn") && $("genEvBtn").addEventListener("click", showEvidence);
  $("copyEvBtn") && $("copyEvBtn").addEventListener("click", copyEvidence);
  $("liveToggle") && $("liveToggle").addEventListener("click", toggleLive);
  loadLedger(); loadEvents(); refreshLive(); renderCalc();
  const ms = Math.max(15, (+CFG.REFRESH_SECONDS || 60)) * 1000;
  setInterval(refreshLive, ms);

  // 实时价格：默认开启（除非用户上次选了省流）；标签页隐藏时自动断开省电，回来再连
  let live = true;
  try { live = localStorage.getItem("mp_live") !== "0"; } catch (e) {}
  if (live) LIVE.start(); else setLiveUI(false);
  document.addEventListener("visibilitychange", () => {
    if (!LIVE.on && localStorage.getItem("mp_live") === "0") return;
    if (document.hidden) { LIVE._clear(); if (LIVE.ws) { try { LIVE.ws.close(); } catch (e) {} } liveDot("off"); }
    else if (LIVE.on) LIVE._open();
  });
}
document.addEventListener("DOMContentLoaded", init);
