/* Million Path 看板逻辑 —— 每个面板独立容错，单个数据源失败不影响其它。 */
"use strict";
const CFG = window.MP_CONFIG || {};

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
async function fetchFNG() { const d = await jget("https://api.alternative.me/fng/?limit=1"); return d.data[0]; }
async function fetchGlobal() { const d = await jget(`${CG}/global`); return d.data; }

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
        <div class="px">${money(c.price)}</div></div>
        <div class="tag ${up ? 'risk-on' : 'risk-off'}">${pct(c.chg)}</div></div>
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
  $("updated").textContent = "更新于 " + new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

//==================== 加密宏观（CoinGecko global）====================
async function loadCryptoMacro() {
  try {
    const g = await fetchGlobal();
    const mc = g.total_market_cap.usd, chg = g.market_cap_change_percentage_24h_usd;
    const btcD = g.market_cap_percentage.btc, ethD = g.market_cap_percentage.eth;
    const cards = [
      ["加密总市值", big(mc), `<span class="${cls(chg)}">${pct(chg)}</span>`],
      ["BTC 占比", btcD.toFixed(1) + "%", "主导率"],
      ["ETH 占比", ethD.toFixed(1) + "%", ""],
      ["24h 成交额", big(g.total_volume.usd), "全市场"],
    ];
    $("macroCards").innerHTML = cards.map(([k, v, sub]) =>
      `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="badge">${sub}</div></div>`).join("");
  } catch (e) { $("macroCards").innerHTML = `<div class="err">加密宏观数据暂不可用</div>`; }
}

//==================== 链上 TVL（DeFiLlama）====================
async function loadDefi() {
  try {
    const chains = await jget("https://api.llama.fi/v2/chains");
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
async function loadCryptoNews() {
  const box = $("cryptoNews");
  try {
    const key = CFG.CRYPTOCOMPARE_KEY ? `&api_key=${CFG.CRYPTOCOMPARE_KEY}` : "";
    const d = await jget(`${CC}/data/v2/news/?lang=EN${key}`);
    let items = (d.Data || []).slice(0, 12).map(n => ({
      title: n.title, url: n.url, source: (n.source_info && n.source_info.name) || n.source, ts: n.published_on,
      img: n.imageurl, cats: (n.categories || "").split("|").slice(0, 2).join(" · ")
    }));
    box.innerHTML = renderNews(items);
  } catch (e) { box.innerHTML = `<div class="err">加密新闻暂不可用（CryptoCompare）。上线后请在浏览器 DevTools 确认其 CORS。</div>`; }
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

//==================== 启动 ====================
function refreshLive() { loadMarket(); loadSentiment(); loadCryptoMacro(); loadDefi(); loadCryptoNews(); loadFred(); loadUsStocks(); }
function init() {
  ["cStart", "cRate", "cFx", "cRmb", "cUsd"].forEach(id => $(id) && $(id).addEventListener("input", renderCalc));
  $("refreshBtn") && $("refreshBtn").addEventListener("click", refreshLive);
  loadLedger(); loadEvents(); refreshLive(); renderCalc();
  setInterval(refreshLive, 60000);
}
document.addEventListener("DOMContentLoaded", init);
