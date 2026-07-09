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
  "risk-off": "仓位上限 0–20%｜目标 0%（保本第一）｜偏空：只顺势做空、禁止抢多（S0 paper 已启用）"
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
    return `<tr><td data-label="标的"><b>${c.name}</b></td>
      <td data-label="状态"><span class="chip ${rg.key}">${rg.label}</span></td>
      <td data-label="现价">${money(c.price)}</td><td data-label="30日均线">${money(sma)}</td>
      <td data-label="偏离" class="${cls(rg.dev)}">${rg.dev == null ? '—' : pct(rg.dev)}</td>
      <td data-label="解读" class="badge">${rg.key === 'risk-on' ? '偏多，可顺势' : rg.key === 'risk-off' ? '偏空，防守为主' : '方向不明，少动'}</td></tr>`;
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
    // 市场环境随情绪变色（一眼感受氛围）
    const moods = [[25, "mood-exfear", "极度恐惧", "#ea3943"], [45, "mood-fear", "恐惧", "#f0a020"],
      [55, "mood-neutral", "中性", "#cbd5e1"], [75, "mood-greed", "贪婪", "#84cc16"], [999, "mood-exgreed", "极度贪婪", "#16c784"]];
    const mood = moods.find(x => v < x[0]);
    const env = $("sec-env"); if (env) env.className = "panel " + mood[1];
    const em = $("envMood"); if (em) { em.textContent = "情绪 " + mood[2] + "（" + v + "）"; em.style.color = mood[3]; }
  } catch (e) { $("fngVal").textContent = "—"; $("fngLabel").innerHTML = '<span class="err">情绪指数暂不可用</span>'; }
}
async function loadEvents() {
  if (!$("eventsList")) return;   // 「我的提醒」已移除时跳过
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

//==================== 自动机器人（读取 bot 写入的数据）====================
let _botState = null;   // 机器人最新状态，供顶部权益与逐秒浮动盈亏复用
let _botLog = null;     // 机器人最近一轮日志（含跨市场快照 xm），供一日节奏面板复用
async function loadBot() {
  if (!$("botStats")) return;
  let st;
  try { st = await jget("./data/bot_state.json", { cache: "no-store" }); }
  catch (e) { $("botStats").innerHTML = '<div class="badge">机器人尚未运行（配置 LLM_API_KEY + 启用 Actions 后每小时自动决策）。</div>'; return; }
  let log = { items: [] }, tr = [];
  try { log = await jget("./data/bot_log.json", { cache: "no-store" }); } catch (e) {}
  try { tr = await jget("./data/bot_trades.json", { cache: "no-store" }); } catch (e) {}
  _botLog = log;

  const eq0 = st.equity0 || 500, cum = (st.equity / eq0 - 1) * 100;
  const wins = tr.filter(t => t.pnl > 0), wr = tr.length ? wins.length / tr.length * 100 : null;
  const sw = wins.reduce((s, t) => s + t.pnl, 0), sl = Math.abs(tr.filter(t => t.pnl < 0).reduce((s, t) => s + t.pnl, 0));
  const pf = sl > 0 ? sw / sl : (sw > 0 ? Infinity : null);
  $("botStats").innerHTML = [
    ["模式", `<span class="chip neutral">${st.mode || "—"}</span>`],
    ["机器人权益", fmt(st.equity, 1) + " U"],
    ["累计收益", `<span class="${cls(cum)}">${pct(cum)}</span>`],
    ["已平笔数", tr.length],
    ["胜率", wr == null ? "—" : fmt(wr, 0) + "%"],
    ["盈亏比", pf == null ? "—" : (pf === Infinity ? "∞" : fmt(pf, 2))],
    ["持仓中", (st.positions || []).length],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  const tnInfo = log.testnet_usdt != null ? ` · Testnet USDT ${fmt(log.testnet_usdt, 0)}` : (log.testnet_error ? ` · Testnet:${log.testnet_error}` : "");
  $("botMode").textContent = `更新于 ${(st.updated_at || "").slice(5, 16).replace("T", " ")} · 日盈亏 ${pct(log.day_pnl_pct)} · 回撤 ${fmt(log.total_dd_pct, 1)}%${tnInfo}`;

  _botState = st;
  renderBotPositions();
  updateHeroFromBot();

  const decTs = log.ts ? Math.floor(Date.parse(log.ts) / 1000) : null;
  const decTime = decTs ? new Date(log.ts).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
  const gRisk = log.global_risk ? `<span class="chip ${log.global_risk === "risk-on" ? "risk-on" : log.global_risk === "risk-off" ? "risk-off" : "neutral"}" style="margin-right:6px">全球 ${log.global_risk}</span>` : "";
  $("botDecisions").innerHTML = (gRisk ? `<div style="margin-bottom:6px">${gRisk}<span class="badge">第一层·领先信息判定（纳指期指/美元/MSTR/NVDA）</span></div>` : "") +
    ((log.items || []).map(it => {
    const ms = it.market_state ? `<span class="chip ${it.market_state === "risk-on" ? "risk-on" : it.market_state === "risk-off" ? "risk-off" : "neutral"}" style="font-size:10px">${it.market_state}</span>` : "";
    // 行动卡明细（有则展示）
    const card = [];
    if (it.entry_low && it.entry_high) card.push(`入场区间 ${fmt(+it.entry_low, 2)}–${fmt(+it.entry_high, 2)}`);
    if (it.max_hold_hours) card.push(`最长持仓 ${it.max_hold_hours}时`);
    if (it.invalidation) card.push(`失效条件：${it.invalidation}`);
    const flags = (it.risk_flags && it.risk_flags.length) ? `<div class="badge" style="display:block;margin-top:3px;color:var(--red)">⚠ ${it.risk_flags.join("；")}</div>` : "";
    return `<div class="ev" style="padding:8px 0"><div class="t" style="flex-wrap:wrap;align-items:baseline">
      <b>${it.symbol || ""}</b>
      <span class="chip ${it.bias === "LONG" ? "risk-on" : it.bias === "SHORT" ? "risk-off" : "neutral"}">${it.bias || it.action || "—"}</span>${ms}
      ${decTime ? `<span class="badge" style="color:var(--muted)">🕐 ${decTime} · ${ago(decTs)}</span>` : ""}
      <span class="badge">${it.source ? it.source + " · " : ""}${it.confidence != null ? "conf " + it.confidence + " · " : ""}${it.reason || it.rationale || ""}</span>
      ${card.length ? `<div class="badge" style="display:block;margin-top:3px">🎯 ${card.join(" ｜ ")}</div>` : ""}${flags}</div></div>`;
  }).join("") || '<span class="badge">暂无决策</span>');

  _botTrades = tr;
  renderBotTrades();
  renderTradeCal();
}

//==================== 已平仓：搜索 + 折叠 + 日/周/月日历统计 ====================
let _botTrades = [], _tradeQuery = "", _tradesExpanded = false, _calOffset = 0;
const dayKeyLocal = ts => { try { const d = new Date(ts); return isNaN(d) ? "" : d.toLocaleDateString("sv"); } catch (e) { return ""; } };
function tradeRow(t) {
  const side = t.side || "LONG";
  const sn = t.snapshot;
  const snTip = sn ? `决策快照｜日线:${sn.regime || "?"} ${sn.daily_dev_pct != null ? sn.daily_dev_pct + "%" : ""} ｜时线偏离:${sn.dev_pct ?? "?"}% ｜RSI:${sn.rsi14 ?? "?"} ｜资费:${sn.funding_pct ?? "?"}%/8h ｜情绪:${sn.fng ?? "?"} ${sn.fng_label || ""} ｜${sn.source || "?"} conf ${sn.confidence ?? "?"}` : "";
  return `<tr>
    <td data-label="标的/方向"><b>${t.symbol}</b> <span class="chip ${side === "LONG" ? "risk-on" : "risk-off"}" style="font-size:10px;padding:1px 7px">${side}</span></td>
    <td data-label="仓位">${fmtQty(t.qty, t.symbol)}</td>
    <td data-label="入场→出场">${fmt(t.entry, 2)} → ${fmt(t.exit, 2)}</td>
    <td data-label="盈亏" class="${cls(t.pnl)}">${(t.pnl >= 0 ? "+" : "") + fmt(t.pnl, 2)}U</td>
    <td data-label="R" class="${cls(t.r)}">${(t.r >= 0 ? "+" : "") + fmt(t.r, 2)}R</td>
    <td data-label="最佳浮盈" class="pos">${t.mfe_pct != null ? "+" + fmt(t.mfe_pct, 1) + "%" : "—"}</td>
    <td data-label="持仓">${fmtHold(t.hold_hours, t.opened_at, t.closed_at)}</td>
    <td data-label="手续费">${t.fee_total != null ? fmt(t.fee_total, 3) + "U" : "—"}</td>
    <td data-label="结果"><span class="chip ${t.outcome === "WIN" ? "risk-on" : t.outcome === "LOSS" ? "risk-off" : "neutral"}">${t.outcome}</span></td>
    <td data-label="盈亏原因" style="text-align:left;white-space:normal;max-width:240px" title="${snTip}">${t.analysis || t.exit_reason || ""}${sn ? ' <span class="badge" style="cursor:help">ⓘ</span>' : ""}</td>
    <td data-label="时间" class="badge">${(t.closed_at || "").slice(5, 16).replace("T", " ")}</td></tr>`;
}
function filterTrades(trades, q) {   // 关键词过滤（标的/方向/结果/出场原因/归因/日期），纯函数
  if (!q) return trades;
  const s = q.trim().toLowerCase();
  return trades.filter(t => [t.symbol, t.side, t.outcome, t.exit_reason, t.analysis,
    (t.closed_at || "").slice(0, 10), dayKeyLocal(t.closed_at)].join(" ").toLowerCase().includes(s));
}
function renderBotTrades() {
  const body = $("botTradesBody"); if (!body) return;
  const SHOW_N = 5;   // 默认只展示最近5笔，其余折叠
  const all = _botTrades.slice().reverse();
  const hit = filterTrades(all, _tradeQuery);
  const show = _tradesExpanded ? hit : hit.slice(0, SHOW_N);
  body.innerHTML = show.map(tradeRow).join("") ||
    `<tr><td colspan="11" class="badge">${_tradeQuery ? "无匹配交易，换个关键词试试" : "暂无已平仓"}</td></tr>`;
  const cnt = $("tradesCount");
  if (cnt) cnt.textContent = _tradeQuery
    ? `筛选出 ${hit.length} / ${all.length} 笔`
    : `共 ${all.length} 笔 · 显示${_tradesExpanded ? "全部" : "最近 " + Math.min(SHOW_N, hit.length) + " 笔"}`;
  const btn = $("tradesMoreBtn");
  if (btn) {
    if (hit.length > SHOW_N) { btn.style.display = ""; btn.textContent = _tradesExpanded ? `▲ 收起，只看最近 ${SHOW_N} 笔` : `▼ 展开全部 ${hit.length} 笔`; }
    else btn.style.display = "none";
  }
}
function tradeCalAgg(trades, now) {  // 日/周/月聚合（本地时区；周一为一周之始），纯函数
  const byDay = {};
  for (const t of trades) {
    const k = dayKeyLocal(t.closed_at); if (!k) continue;
    const v = byDay[k] = byDay[k] || { pnl: 0, n: 0, w: 0 };
    v.pnl += (t.pnl || 0); v.n++; if ((t.pnl || 0) > 0) v.w++;
  }
  const today = new Date(now).toLocaleDateString("sv");
  const d0 = new Date(now); d0.setHours(0, 0, 0, 0);
  const monday = new Date(d0); monday.setDate(d0.getDate() - (d0.getDay() + 6) % 7);
  const wk = []; for (let i = 0; i < 7; i++) { const d = new Date(monday); d.setDate(monday.getDate() + i); wk.push(d.toLocaleDateString("sv")); }
  const agg = ks => ks.reduce((o, k) => { const v = byDay[k]; if (v) { o.pnl += v.pnl; o.n += v.n; o.w += v.w; } return o; }, { pnl: 0, n: 0, w: 0 });
  return { byDay, today,
    day: byDay[today] || { pnl: 0, n: 0, w: 0 },
    week: agg(wk),
    month: agg(Object.keys(byDay).filter(k => k.startsWith(today.slice(0, 7)))) };
}
function renderTradeCal() {
  const grid = $("calGrid"); if (!grid) return;
  const now = new Date();
  const a = tradeCalAgg(_botTrades, now);
  const kpi = (label, o) => `<div class="card cal-kpi"><div class="k">${label}</div>
    <div class="v ${o.pnl > 0 ? "pos" : o.pnl < 0 ? "neg" : ""}">${(o.pnl >= 0 ? "+" : "") + fmt(o.pnl, 2)}U</div>
    <div class="s">${o.n ? o.n + "笔 · 胜率" + Math.round(o.w / o.n * 100) + "%" : "无交易"}</div></div>`;
  const kEl = $("calKpis"); if (kEl) kEl.innerHTML = kpi("今日", a.day) + kpi("本周", a.week) + kpi("本月", a.month);
  const base = new Date(now.getFullYear(), now.getMonth() - _calOffset, 1);
  const tEl = $("calTitle"); if (tEl) tEl.textContent = `${base.getFullYear()}年${base.getMonth() + 1}月`;
  const daysIn = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate();
  const lead = (base.getDay() + 6) % 7;
  let html = "";
  for (let i = 0; i < lead; i++) html += '<div class="cal-cell blank"></div>';
  for (let d = 1; d <= daysIn; d++) {
    const key = `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const v = a.byDay[key];
    const clsx = !v ? "" : v.pnl > 0 ? (v.pnl >= 5 ? "p2" : "p1") : v.pnl < 0 ? (v.pnl <= -5 ? "n2" : "n1") : "";
    html += `<div class="cal-cell ${v ? "has " + clsx : ""}${key === a.today ? " today" : ""}"${v ? ` data-day="${key}"` : ""}>
      <span class="d">${d}</span>${v ? `<span class="p">${(v.pnl >= 0 ? "+" : "") + fmt(v.pnl, 1)}</span><span class="n">${v.n}笔</span>` : ""}</div>`;
  }
  grid.innerHTML = html;
  const nx = $("calNext"); if (nx) nx.disabled = _calOffset <= 0;
}
function fmtHold(h, openedAt, closedAt) {
  let hrs = h;
  if (hrs == null && openedAt && closedAt) { const d = (Date.parse(closedAt) - Date.parse(openedAt)) / 3.6e6; if (!isNaN(d)) hrs = d; }
  if (hrs == null) return "—";
  if (hrs < 1) return Math.round(hrs * 60) + "分";
  if (hrs < 24) return fmt(hrs, 1) + "时";
  const dd = Math.floor(hrs / 24), hh = Math.round(hrs % 24);
  return dd + "天" + (hh ? hh + "时" : "");
}
// 机器人持仓的浮动盈亏（逐秒刷新，复用最新 _botState + 实时价）
function renderBotPositions() {
  const body = $("botOpenBody"); if (!body || !_botState) return;
  const nowISO = new Date().toISOString();
  body.innerHTML = (_botState.positions || []).map(p => {
    const side = p.side || "LONG", long = side === "LONG";
    const lp = livePrice((p.symbol || "").replace("USDT", "")); let u = null;
    if (lp != null) u = (long ? (lp - p.entry) : (p.entry - lp)) * p.qty - (p.fee_in || 0) - lp * p.qty * FEE;
    const notional = p.notional || (p.entry * p.qty);
    const upct = (u != null && notional) ? u / notional * 100 : null;
    const openT = p.opened_at ? new Date(p.opened_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
    return `<tr><td data-label="标的/方向"><b>${p.symbol}</b> <span class="chip ${long ? "risk-on" : "risk-off"}">${side}</span></td>
      <td data-label="仓位">${fmtQty(p.qty, p.symbol)}</td>
      <td data-label="入场">${fmt(p.entry, 2)}</td><td data-label="现价">${lp != null ? fmt(lp, 2) : "—"}</td>
      <td data-label="止损">${fmt(p.stop, 2)}</td><td data-label="止盈">${fmt(p.target, 2)}</td>
      <td data-label="浮动盈亏" class="${cls(u)}">${u == null ? "—" : (u >= 0 ? "+" : "") + fmt(u, 2) + "U"}</td>
      <td data-label="浮盈%" class="${cls(upct)}">${upct == null ? "—" : (upct >= 0 ? "+" : "") + fmt(upct, 2) + "%"}</td>
      <td data-label="入场时间" class="badge">${openT}</td>
      <td data-label="持仓">${fmtHold(null, p.opened_at, nowISO)}</td></tr>`;
  }).join("") || '<tr><td colspan="10" class="badge">当前无持仓</td></tr>';
}
// 持币数量格式化：4 位有效数字 + 币种，如 0.003912 BTC
function fmtQty(q, symbol) {
  if (q == null) return "—";
  const base = (symbol || "").replace("USDT", "");
  return (+q).toLocaleString("en-US", { maximumSignificantDigits: 4 }) + (base ? " " + base : "");
}
// 顶部「组合权益」按机器人实时盯市（已实现权益 + 未平仓浮动盈亏）更新
function updateHeroFromBot() {
  if (!_botState) return;
  const eq0 = _botState.equity0 || 500;
  let eq = _botState.equity;
  (_botState.positions || []).forEach(p => {
    const long = (p.side || "LONG") === "LONG";
    const lp = livePrice((p.symbol || "").replace("USDT", ""));
    if (lp != null) eq += (long ? (lp - p.entry) : (p.entry - lp)) * p.qty - (p.fee_in || 0) - lp * p.qty * FEE;
  });
  const cum = (eq / eq0 - 1) * 100;
  const he = $("heroEquity"); if (he) he.innerHTML = fmt(eq, 1) + ' <small>USDT</small>';
  const hc = $("heroCum"); if (hc) hc.innerHTML = `<span class="${cls(cum)}">${pct(cum)}</span>`;
  const hs = $("heroStart"); if (hs) hs.textContent = fmt(eq0, 0);
}

//==================== 加密新闻（CryptoCompare 直连 + 可选 RSS）====================
function stampNews() { const nt = $("newsTime"); if (nt) nt.textContent = "更新于 " + new Date().toLocaleTimeString("zh-CN"); }
async function loadCryptoNews() {
  const box = $("cryptoNews");
  // 1) 优先 CryptoCompare（直连 → 失败自动走 Worker）
  try {
    const key = CFG.CRYPTOCOMPARE_KEY ? `&api_key=${CFG.CRYPTOCOMPARE_KEY}` : "";
    const d = await jgetSmart(`${CC}/data/v2/news/?lang=EN${key}`);
    const items = (d.Data || []).slice(0, 18).map(n => ({
      title: n.title, url: n.url, source: (n.source_info && n.source_info.name) || n.source, ts: n.published_on,
      img: n.imageurl, cats: (n.categories || "").split("|").slice(0, 2).join(" · ")
    }));
    if (!items.length) throw new Error("empty");
    STATE.news = items.slice(0, 6).map(n => n.title);
    _newsItems = items; renderNews(); stampNews(); if (_newsLang === "zh") translateNews(); return;
  } catch (e) { /* 进入 RSS 回退 */ }
  // 2) 回退：品牌 RSS（CoinDesk / Cointelegraph / Decrypt，经 Worker）
  try {
    const items = await fetchRssNews();
    if (!items.length) throw new Error("empty");
    STATE.news = items.slice(0, 6).map(n => n.title);
    _newsItems = items; renderNews(); stampNews(); if (_newsLang === "zh") translateNews(); return;
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
let _newsItems = [], _newsLang = "en";
const _trCache = {};
function newsCard(n) {
  const title = (_newsLang === "zh" && _trCache[n.title]) ? _trCache[n.title] : n.title;
  const meta = `${n.source || ''}${n.cats ? ' · ' + n.cats : ''}${n.ts ? ' · ' + ago(n.ts) : ''}`;
  return `<a class="news-card" href="${n.url}" target="_blank" rel="noopener">
    ${n.img ? `<img src="${n.img}" loading="lazy" alt="">` : ""}
    <div class="nc-body"><b>${title}</b><div class="badge">${meta}</div></div></a>`;
}
function renderNews() {
  const box = $("cryptoNews"); if (!box) return;
  if (!_newsItems.length) { box.innerHTML = '<span class="badge">暂无新闻</span>'; return; }
  // 交替分到两列 → 两列高度天然错落，形成瀑布流；各列复制两份实现无缝滚动
  const colA = [], colB = [];
  _newsItems.forEach((n, i) => (i % 2 ? colB : colA).push(newsCard(n)));
  const a = colA.join(""), b = colB.join("");
  const durA = Math.max(26, colA.length * 4.4);          // 条数越多滚得越慢，速度恒定
  const durB = Math.max(26, colB.length * 4.4) * 1.15;   // 两列略不同步，更像瀑布流
  box.innerHTML = `<div class="news-masonry">
    <div class="news-col"><div class="news-track" style="animation-duration:${durA}s">${a}${a}</div></div>
    <div class="news-col"><div class="news-track" style="animation-duration:${durB}s">${b}${b}</div></div>
  </div>`;
}
// 免费翻译(MyMemory，CORS 友好)，按标题缓存，避免重复翻译
async function translateNews() {
  const todo = _newsItems.map(n => n.title).filter(t => t && !_trCache[t]);
  const btn = $("newsLangBtn");
  if (todo.length && btn) btn.textContent = "翻译中…";
  await Promise.all(todo.map(async t => {
    try {
      const r = await fetch(`https://api.mymemory.translated.net/get?q=${encodeURIComponent(t)}&langpair=en|zh-CN`);
      const d = await r.json();
      const zh = d && d.responseData && d.responseData.translatedText;
      if (zh && !/MYMEMORY WARNING/i.test(zh)) _trCache[t] = zh;
    } catch (e) {}
  }));
  if (btn) btn.textContent = _newsLang === "zh" ? "原文" : "译中文";
  renderNews();
}
function toggleNewsLang() {
  _newsLang = _newsLang === "en" ? "zh" : "en";
  const btn = $("newsLangBtn");
  if (_newsLang === "zh") { if (btn) btn.textContent = "原文"; translateNews(); }
  else { if (btn) btn.textContent = "译中文"; renderNews(); }
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
    STATE.us = {}; out.forEach(s => { STATE.us[s.sym] = s; });   // 供一日节奏共振/盘前卡复用(实时,60s)
    $("usBox").innerHTML = out.map(s => {
      const col = s.chg == null ? "" : (s.chg >= 0 ? "var(--green)" : "var(--red)");
      return `<div class="card">
        <div class="k">${s.sym}</div>
        <div class="v" style="color:${col || 'var(--text)'}">${s.price == null ? "—" : "$" + fmt(s.price, 2)}</div>
        <div style="font-size:11px;margin-top:2px;color:${col || 'var(--muted)'}">${pct(s.chg)}</div></div>`;
    }).join("");
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

  if (!_botState) {   // 机器人数据到位后，顶部权益由 updateHeroFromBot() 实时盯市，ledger 不再覆盖
    $("heroEquity").innerHTML = fmt(last.equity_end, 0) + ' <small>USDT</small>';
    $("heroCum").innerHTML = `<span class="${cls(cum)}">${pct(cum)}</span>`;
  }
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
    <td data-label="周">${x.week}</td><td data-label="日期">${x.start_date}</td><td data-label="阶段">${x.stage}</td>
    <td data-label="行情"><span class="chip ${x.regime}">${x.regime}</span></td>
    <td data-label="周收益" class="${cls(x._ret)}">${pct(x._ret)}</td>
    <td data-label="目标">${x.target_return_pct != null ? x.target_return_pct + "%" : "—"}</td>
    <td data-label="笔数">${x.trades ?? "—"}</td><td data-label="胜率">${x.win_rate_pct != null ? x.win_rate_pct + "%" : "—"}</td>
    <td data-label="盈亏比">${fmt(x.profit_factor)}</td><td data-label="回撤">${x.max_drawdown_pct != null ? x.max_drawdown_pct + "%" : "—"}</td>
    <td data-label="费用%">${x.fees_funding_pct != null ? x.fees_funding_pct + "%" : "—"}</td>
    <td data-label="违规" class="${x.violations > 0 ? 'warn' : ''}">${x.violations ?? 0}</td></tr>`).join("");

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
    return `<tr><td data-label="周化">${(rr * 100).toFixed(1)}%</td><td data-label="年化(复利)">${fmt(y, 0)}%</td><td data-label="→ 目标人民币">${dur(weeksTo(s, rr, tR))}</td><td data-label="→ 目标美元">${dur(weeksTo(s, rr, tU))}</td></tr>`;
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

//==================== 规则推断的决策元组（直接可读，非 JSON）====================
// 用确定性规则从实时数据给出参考决策。这是「战略层 lite」，非投资建议；
// 想要更深入判断，可复制下方 Evidence 文本喂给 LLM。
function computeDecision() {
  const reg = STATE.btcRegime, fng = STATE.fng, mkt = STATE.market || [];
  let bias = "FLAT", conf = 0.3; const reasons = [], flags = [];
  if (reg) {
    if (reg.key === "risk-on") { bias = "LONG"; conf = 0.55; reasons.push(`BTC ${reg.label}${reg.dev != null ? "（+" + reg.dev.toFixed(1) + "%）" : ""}，趋势偏多`); }
    else if (reg.key === "risk-off") { bias = "SHORT"; conf = 0.55; reasons.push(`BTC ${reg.label}${reg.dev != null ? "（" + reg.dev.toFixed(1) + "%）" : ""}，趋势偏空（1x 合约可顺势做空）`); }
    else { bias = "FLAT"; reasons.push("BTC 贴近30日线、方向不明，少动"); }
    if (reg.dev != null) conf += Math.min(0.2, Math.abs(reg.dev) / 50);
  }
  const btc = mkt.find(c => c.name === "BTC");
  // 与机器人 risk.vet 同源的确定性否决（保证快照结论 ≠ 机器人行为 的矛盾不再出现）
  const ma30h = _rhCache && _rhCache.ma30h, px = btc && btc.price;
  if (bias !== "FLAT" && ma30h && px) {
    if (bias === "LONG" && px < ma30h) { bias = "FLAT"; reasons.push(`小时级未确认（价 ${money(px)} 在30h线 ${money(ma30h)} 下方），机器人闸门会拦，观望`); }
    if (bias === "SHORT" && px > ma30h) { bias = "FLAT"; reasons.push(`小时级未确认（价 ${money(px)} 在30h线 ${money(ma30h)} 上方），机器人闸门会拦，观望`); }
  }
  if (btc && btc.funding != null) {
    if (bias === "SHORT" && btc.funding <= -0.05) { bias = "FLAT"; flags.push(`资费 ${btc.funding.toFixed(4)}%/8h 空头极端拥挤——机器人闸门禁追空，观望`); }
    else if (bias === "LONG" && btc.funding >= 0.10) { bias = "FLAT"; flags.push(`资费 +${btc.funding.toFixed(4)}%/8h 多头过热——机器人闸门禁追多，观望`); }
    else if (Math.abs(btc.funding) > 0.05) {
      flags.push(`资金费率偏高（${btc.funding >= 0 ? "+" : ""}${btc.funding.toFixed(4)}%/8h），${btc.funding > 0 ? "多头" : "空头"}拥挤`);
      if (bias === "LONG") conf -= 0.1;
    }
  }
  if (fng) {
    reasons.push(`市场情绪：${fng.label}（${fng.v}）`);
    if (fng.v >= 75) { flags.push(`极度贪婪（${fng.v}），警惕追高`); if (bias === "LONG") conf -= 0.1; }
    else if (fng.v <= 25) { flags.push(`极度恐惧（${fng.v}），或有超跌机会但需右侧确认`); }
  }
  if (bias === "SHORT" && btc && btc.funding != null && Math.abs(btc.funding) > 0.05 && btc.funding < 0) conf -= 0.1;
  conf = Math.max(0.1, Math.min(0.9, conf));
  const move = bias === "LONG" ? "顺势做多，目标盈亏比 ≥ 1.5"
    : bias === "SHORT" ? "顺势做空（1x 合约），目标盈亏比 ≥ 1.5"
    : "观望 / 空仓（FLAT 也是决策）";
  const advice = reg ? ADVICE[reg.key] : ADVICE.neutral;
  return { bias, conf, move, reasons, flags, advice };
}
function renderDecision() {
  const box = $("decisionBox"); if (!box) return;
  const d = computeDecision();
  const biasCls = d.bias === "LONG" ? "risk-on" : d.bias === "SHORT" ? "risk-off" : "neutral";
  const bar = Math.round(d.conf * 100);
  box.innerHTML = `
    <div class="grid g-auto" style="margin-bottom:10px">
      <div class="card"><div class="k">方向 bias</div><div class="v"><span class="chip ${biasCls}" style="font-size:15px">${d.bias}</span></div></div>
      <div class="card"><div class="k">置信度 confidence</div><div class="v">${d.conf.toFixed(2)}</div>
        <div style="height:6px;border-radius:4px;background:#0d1426;margin-top:6px;overflow:hidden"><div style="height:100%;width:${bar}%;background:linear-gradient(90deg,var(--gold),var(--gold2))"></div></div></div>
      <div class="card" style="grid-column:span 2;min-width:200px"><div class="k">操作 expected move</div><div class="v" style="font-size:14px">${d.move}</div></div>
    </div>
    <div class="card" style="margin-bottom:8px"><div class="k">理由 rationale</div>
      <div style="font-size:13px;margin-top:4px">${d.reasons.map(r => "· " + r).join("<br>") || "—"}</div></div>
    <div class="card" style="margin-bottom:8px;border-color:${d.flags.length ? 'var(--red)' : 'var(--border)'}"><div class="k">风险提示 risk flags</div>
      <div style="font-size:13px;margin-top:4px;color:${d.flags.length ? 'var(--red)' : 'var(--muted)'}">${d.flags.map(r => "⚠ " + r).join("<br>") || "暂无明显风险信号"}</div></div>
    <div class="badge">MP500 仓位建议：${d.advice}</div>
    <div class="badge" style="display:block;margin-top:6px">⚠ 以上为<b>规则推断的参考</b>，非投资建议；最终由你与风控引擎决定。想要更深入判断，展开下方 Evidence 复制给 LLM。</div>`;
  const t = $("decTime"); if (t) t.textContent = "更新于 " + new Date().toLocaleTimeString("zh-CN");
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
  paperTick();   // 持仓中则实时刷新浮动盈亏
  if (_botState) { renderBotPositions(); updateHeroFromBot(); }   // 机器人持仓 + 顶部权益逐秒盯市
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

//==================== Paper 持仓器（localStorage，实时算 PnL）====================
const FEE = 0.0005;  // 手续费 0.05%/边（币安 USDT 合约 taker，与 mp500-bot/risk.py 一致）
function livePrice(name) {
  if (_lastPx[name] != null) return _lastPx[name];
  const e = (STATE.market || []).find(c => c.name === name); return e ? e.price : null;
}
const PAPER = {
  data: { equity0: 500, positions: [], closed: [] },
  load() { try { const s = localStorage.getItem("mp_paper"); if (s) this.data = JSON.parse(s); } catch (e) {} },
  save() { try { localStorage.setItem("mp_paper", JSON.stringify(this.data)); } catch (e) {} },
  equity() { return this.data.equity0 + this.data.closed.reduce((s, t) => s + t.pnl, 0); }
};
function paperVal(id) { const el = $(id); return el ? el.value : ""; }
function paperFillEntry() { const p = livePrice(paperVal("pSym")); if (p != null) { const el = $("pEntry"); if (el) el.value = p; } }
function paperOpen() {
  const sym = paperVal("pSym"), side = paperVal("pSide");
  const entry = +paperVal("pEntry"), stop = +paperVal("pStop"), target = +paperVal("pTarget") || null;
  const riskPct = (+paperVal("pRisk") || 1) / 100;
  const msg = $("paperMsg"); const warn = t => { if (msg) { msg.textContent = t; msg.className = "err"; } };
  if (!(entry > 0)) return warn("请填入有效入场价（可点「用现价」）");
  if (!(stop > 0)) return warn("必须填止损价（无止损不开仓）");
  if (side === "LONG" && !(stop < entry)) return warn("多单止损价必须低于入场价");
  if (side === "SHORT" && !(stop > entry)) return warn("空单止损价必须高于入场价");
  const eq = PAPER.equity();
  const riskUsdt = eq * riskPct, stopDist = Math.abs(entry - stop) / entry;
  const notional = riskUsdt / stopDist, qty = notional / entry, feeIn = notional * FEE;
  PAPER.data.positions.push({ id: Date.now(), sym, side, entry, stop, target, qty, notional, riskUsdt, feeIn, openedAt: new Date().toISOString() });
  PAPER.save();
  if (msg) { msg.textContent = `已开 paper ${side} ${sym}：名义 ${fmt(notional, 1)}U，风险 ${fmt(riskUsdt, 2)}U`; msg.className = "badge"; }
  renderPaper();
}
function paperClose(id) {
  const i = PAPER.data.positions.findIndex(p => p.id === id); if (i < 0) return;
  const p = PAPER.data.positions[i], exit = livePrice(p.sym);
  if (exit == null) { const m = $("paperMsg"); if (m) { m.textContent = "暂无实时价，无法平仓（开实时开关或稍候）"; m.className = "err"; } return; }
  const gross = p.side === "LONG" ? (exit - p.entry) * p.qty : (p.entry - exit) * p.qty;
  const feeOut = exit * p.qty * FEE, pnl = gross - p.feeIn - feeOut;
  const r = p.riskUsdt ? pnl / p.riskUsdt : 0, outcome = pnl > 0 ? "WIN" : pnl < 0 ? "LOSS" : "BE";
  PAPER.data.closed.push({ ...p, exit, pnl, r, feeTotal: p.feeIn + feeOut, closedAt: new Date().toISOString(), outcome });
  PAPER.data.positions.splice(i, 1); PAPER.save(); renderPaper();
}
function paperReset() {
  if (typeof confirm === "function" && !confirm("确定清空所有 paper 持仓与历史？此操作不可撤销。")) return;
  PAPER.data = { equity0: 500, positions: [], closed: [] }; PAPER.save(); renderPaper();
}
function paperStats() {
  const c = PAPER.data.closed, n = c.length;
  const wins = c.filter(t => t.pnl > 0), losses = c.filter(t => t.pnl < 0);
  const wr = n ? wins.length / n * 100 : null;
  const sw = wins.reduce((s, t) => s + t.pnl, 0), sl = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
  const pf = sl > 0 ? sw / sl : (sw > 0 ? Infinity : null);
  let eq = PAPER.data.equity0, peak = eq, dd = 0;
  c.forEach(t => { eq += t.pnl; peak = Math.max(peak, eq); dd = Math.max(dd, (peak - eq) / peak * 100); });
  const avgR = n ? c.reduce((s, t) => s + (t.r || 0), 0) / n : null;
  return { n, wr, pf, dd, avgR, equity: PAPER.equity() };
}
function renderPaper() {
  const st = paperStats();
  if ($("paperStats")) $("paperStats").innerHTML = [
    ["Paper 权益", fmt(st.equity, 1) + " U"],
    ["累计收益", `<span class="${cls(st.equity - 500)}">${pct((st.equity / 500 - 1) * 100)}</span>`],
    ["已平笔数", st.n],
    ["胜率", st.wr == null ? "—" : fmt(st.wr, 0) + "%"],
    ["盈亏比", st.pf == null ? "—" : (st.pf === Infinity ? "∞" : fmt(st.pf, 2))],
    ["平均 R", st.avgR == null ? "—" : (st.avgR >= 0 ? "+" : "") + fmt(st.avgR, 2)],
    ["最大回撤", fmt(st.dd, 1) + "%"],
    ["持仓中", PAPER.data.positions.length],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  const ob = $("paperOpenBody");
  if (ob) ob.innerHTML = PAPER.data.positions.map(p => {
    const lp = livePrice(p.sym);
    let upnl = null, upct = null, rr = null;
    if (lp != null) {
      const gross = p.side === "LONG" ? (lp - p.entry) * p.qty : (p.entry - lp) * p.qty;
      upnl = gross - p.feeIn - lp * p.qty * FEE; upct = upnl / p.notional * 100; rr = p.riskUsdt ? upnl / p.riskUsdt : null;
    }
    return `<tr>
      <td data-label="标的/方向"><b>${p.sym}</b> <span class="chip ${p.side === "LONG" ? "risk-on" : "risk-off"}">${p.side}</span></td>
      <td data-label="入场">${fmt(p.entry, 2)}</td><td data-label="现价">${lp != null ? fmt(lp, 2) : "—"}</td>
      <td data-label="止损">${fmt(p.stop, 2)}</td><td data-label="止盈">${p.target ? fmt(p.target, 2) : "—"}</td>
      <td data-label="名义">${fmt(p.notional, 1)}U</td>
      <td data-label="浮动盈亏" class="${cls(upnl)}">${upnl == null ? "—" : (upnl >= 0 ? "+" : "") + fmt(upnl, 2) + "U"} ${upct != null ? "(" + pct(upct) + ")" : ""}</td>
      <td data-label="R" class="${cls(rr)}">${rr == null ? "—" : (rr >= 0 ? "+" : "") + fmt(rr, 2) + "R"}</td>
      <td data-label="操作"><button class="btn" data-close="${p.id}">平仓</button></td></tr>`;
  }).join("") || `<tr><td colspan="9" class="badge">暂无持仓</td></tr>`;

  const cb = $("paperClosedBody");
  if (cb) cb.innerHTML = PAPER.data.closed.slice().reverse().slice(0, 30).map(t => `<tr>
    <td data-label="标的/方向"><b>${t.sym}</b> <span class="chip ${t.side === "LONG" ? "risk-on" : "risk-off"}">${t.side}</span></td>
    <td data-label="入场→出场">${fmt(t.entry, 2)} → ${fmt(t.exit, 2)}</td>
    <td data-label="盈亏" class="${cls(t.pnl)}">${(t.pnl >= 0 ? "+" : "") + fmt(t.pnl, 2)}U</td>
    <td data-label="R" class="${cls(t.r)}">${(t.r >= 0 ? "+" : "") + fmt(t.r, 2)}R</td>
    <td data-label="结果"><span class="chip ${t.outcome === "WIN" ? "risk-on" : t.outcome === "LOSS" ? "risk-off" : "neutral"}">${t.outcome}</span></td>
    <td data-label="时间" class="badge">${(t.closedAt || "").slice(5, 16).replace("T", " ")}</td></tr>`).join("") || `<tr><td colspan="6" class="badge">暂无已平仓记录</td></tr>`;
}
function paperExport() {
  const st = paperStats();
  const txt = `本期 paper 统计（填入 ledger）：trades=${st.n}, win_rate_pct=${st.wr == null ? "" : Math.round(st.wr)}, ` +
    `profit_factor=${st.pf == null || st.pf === Infinity ? "" : st.pf.toFixed(2)}, max_drawdown_pct=${st.dd.toFixed(1)}, ` +
    `equity_end=${st.equity.toFixed(1)}`;
  const out = $("paperExport"); if (out) out.value = txt;
  if (navigator.clipboard) navigator.clipboard.writeText(txt);
  const b = $("pExportBtn"); if (b) { const o = b.textContent; b.textContent = "已复制 ✓"; setTimeout(() => b.textContent = o, 1500); }
}
let _paperT = 0;
function paperTick() { const now = Date.now(); if (now - _paperT < 900) return; _paperT = now; if ($("paperOpenBody") && PAPER.data.positions.length) renderPaper(); }

//==================== 电梯层导航（高亮当前 + 回顶）====================
function setupNav() {
  if (typeof document.querySelectorAll !== "function") return;
  const links = Array.from(document.querySelectorAll(".nav a, .elev a"));
  const map = {};
  links.forEach(a => { const id = (a.getAttribute("href") || "").slice(1); if (id) (map[id] = map[id] || []).push(a); });
  const ids = Object.keys(map);
  if (typeof IntersectionObserver === "function") {
    const obs = new IntersectionObserver(es => {
      es.forEach(e => { if (e.isIntersecting) ids.forEach(id => map[id].forEach(a => a.classList.toggle("active", id === e.target.id))); });
    }, { rootMargin: "-45% 0px -50% 0px" });
    ids.forEach(id => { const el = document.getElementById(id); if (el) obs.observe(el); });
  }
  const tt = $("toTop");
  if (tt) {
    window.addEventListener("scroll", () => { tt.style.display = window.scrollY > 500 ? "grid" : "none"; });
    tt.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  }
}

//==================== 启动 ====================
//==================== 一日节奏 · 实时研报（每项=数据+结论；纯函数可测）====================
// 语义标记：绿=机会/健康 红=风险/警报 金=中性/等待；sd()=行首状态灯
const sd = k => `<i class="sd sd-${k}"></i>`;
const tg = t => `<span class="g">${t}</span>`, tr = t => `<span class="r">${t}</span>`,
      ty = t => `<span class="y">${t}</span>`, tw = t => `<span class="w">${t}</span>`;
const sgnT = (x, d = 2, suf = "%") => x == null ? "n/a" :
  `<span class="${x >= 0 ? "g" : "r"}">${x >= 0 ? "+" : ""}${x.toFixed(d)}${suf}</span>`;
// 关键位判定：现价 vs 昨高/昨低/30日线 → 突破|跌破|区间内 + 一句结论
function levelVerdict(price, prevH, prevL, ma30d) {
  if ([price, prevH, prevL].some(x => x == null)) return null;
  const dev = ma30d ? (price / ma30d - 1) * 100 : null;
  const rz = ma30d != null ? `｜30日线 ${money(ma30d)}(${sgnT(dev, 1)})` : "";
  let dot, st, cn;
  if (price > prevH) {
    dot = "g"; st = tg("已突破昨高") + ` ${money(prevH)}`;
    cn = dev != null && dev < 0 ? ty("但仍在日线下方，反弹性质待确认") : tg("多头结构");
  } else if (price < prevL) {
    dot = "r"; st = tr("已跌破昨低") + ` ${money(prevL)}`;
    cn = tr(`破位，反抽 ${money(prevL)} 不收复则顺势偏空`);
  } else {
    st = `区间内(昨低 ${tw(money(prevL))}–昨高 ${tw(money(prevH))})`;
    if (dev != null && dev <= -1) { dot = "y"; cn = ty("日线下方＝反弹默认逢高空视角"); }
    else if (dev != null && dev >= 1) { dot = "g"; cn = ty("日线上方＝回踩默认逢低多视角"); }
    else { dot = "y"; cn = ty("贴近日线，方向不明"); }
  }
  return `${sd(dot)}现价 ${tw(money(price))}｜${st}${rz} → ${cn}`;
}
// ETH/SOL 相对 BTC 强弱 → 资金是否下沉（risk-on 确认项）
function relVerdict(ethbtc, solbtc) {
  if (ethbtc == null && solbtc == null) return null;
  const strong = [ethbtc, solbtc].filter(x => x != null && x >= 0.5).length;
  const weak = [ethbtc, solbtc].filter(x => x != null && x <= -0.5).length;
  const [dot, cn] = strong >= 2 ? ["g", tg("资金下沉山寨＝risk-on确认")]
    : strong === 1 ? ["y", ty("局部轮动，非全面risk-on")]
    : weak >= 1 ? ["r", tr("资金回避高β，偏防御")] : ["y", ty("无明显偏向")];
  return `${sd(dot)}ETH/BTC ${sgnT(ethbtc)}｜SOL/BTC ${sgnT(solbtc)} → ${cn}`;
}
// 夜间(近10h)量能 vs 前7日常态
function volVerdict(ratio) {
  if (ratio == null) return null;
  const [dot, cn] = ratio >= 1.8 ? ["r", tr("异常放量——夜间有事，查新闻/破位")]
    : ratio >= 1.3 ? ["y", ty("偏高，留意")] : ["g", tg("正常")];
  return `${sd(dot)}近10h量＝常态的 ${tw(ratio.toFixed(1) + "×")}(≥1.8×判异常) → ${cn}`;
}
// 资费+OI是否突升
function fundOiVerdict(funding, oiChg) {
  if (funding == null && oiChg == null) return null;
  const f = funding == null ? "n/a" : `${funding >= 0 ? "+" : ""}${funding.toFixed(4)}%/8h`;
  const crowded = funding != null && Math.abs(funding) >= 0.05;
  const fw = crowded ? (funding > 0 ? tr("多头拥挤") : tr("空头拥挤(勿追空)"))
    : funding != null && Math.abs(funding) >= 0.03 ? ty("偏热") : tg("中性");
  const oiHot = oiChg != null && Math.abs(oiChg) >= 8;
  const ow = oiChg == null ? "" : oiChg >= 8 ? tr("OI激增＝新杠杆进场") : oiChg <= -8 ? ty("OI大降＝杠杆清理近尾声")
    : oiChg >= 0 ? "OI小增" : "OI小降(降杠杆)";
  const dot = crowded || oiChg >= 8 ? "r" : (funding != null && Math.abs(funding) >= 0.03) || oiHot ? "y" : "g";
  return `${sd(dot)}资费 ${tw(f)}(${fw})｜OI 24h ${sgnT(oiChg, 1)} ${ow ? "→ " + ow : ""}`;
}
// 盘中：OI结构 × 价格方向 → 下跌是新空还是清算
function oiStructVerdict(oiChg, chg24) {
  if (oiChg == null || chg24 == null) return null;
  const base = `价${sgnT(chg24, 1)}/OI${sgnT(oiChg, 1)} → `;
  if (chg24 < 0 && oiChg > 2) return sd("y") + base + ty("新空进场，跌势或延续");
  if (chg24 < 0 && oiChg < -2) return sd("r") + base + tr("多头清算主导，杠杆快洗完，追空危险");
  if (chg24 > 0 && oiChg > 2) return sd("g") + base + tg("新多进场，趋势健康");
  if (chg24 > 0 && oiChg < -2) return sd("y") + base + ty("空头回补推动，涨势质量存疑");
  return sd("g") + base + "无明显杠杆结构变化";
}
// 三套预案：基于真实价位（昨高低/30h线/30日线）+日线状态+资费 生成具体计划
function genPlans(d) {
  const { price, prevH, prevL, ma30h, ma30d, funding } = d;
  if ([price, prevH, prevL].some(x => x == null)) return null;
  const dev = ma30d ? (price / ma30d - 1) * 100 : null;
  const off = dev != null && dev <= -1, on = dev != null && dev >= 1;
  const m = x => tw(money(x));
  const crowded = funding != null && funding <= -0.05;
  const A = off
    ? `跌破昨低 ${m(prevL)} 且 1h 收盘不收复 → ${tr("顺势做空")}。参考区间 ${m(prevL * 0.995)}–${m(prevL * 1.002)}(破位回抽区)，止损≈${m(prevL * 1.015)}(+1.5%)，目标≥1.5R。${crowded ? tr("⚠当前资费已极端偏空，空头拥挤——本预案暂停") : "资费≤−0.05%则放弃(拥挤)。"}机器人同规则自动执行。`
    : `若纳指/BTC转弱：跌破30日线 ${ma30d ? m(ma30d) : "—"} 先减仓观望、${tr("禁抄底")}；跌破昨低 ${m(prevL)} 再评估反手空。`;
  const B = off
    ? `反弹≠反转：站上30h线 ${ma30h ? m(ma30h) : "—"} 且回踩不破，再看 MSTR 是否转强、ETH/BTC 是否回暖。日线仍偏空(${sgnT(dev, 1)})→ ${tr("多单被闸门禁止")}；反弹至昨高 ${m(prevH)} 附近＝${ty("更优做空观察区")}，等反抽衰竭，不追第一波。`
    : on
    ? `回踩 30h线 ${ma30h ? m(ma30h) : "—"} 不破 → ${tg("顺势做多")}，入场区 ${ma30h ? m(ma30h * 0.998) + "–" + m(ma30h * 1.005) : "—"}，止损1.5%，资费≥+0.10%放弃(过热)。`
    : `站上昨高 ${m(prevH)} 且回踩不破 + MSTR/纳指共振 → ${tg("小仓试多")}(震荡市减半仓)，止损昨高下方。`;
  const C = `昨低 ${m(prevL)} – 昨高 ${m(prevH)} 区间内 → ${ty("FLAT(空仓也是决策)")}。等 1h 收盘突破任一侧再按预案A/B执行；区间中轴附近开仓盈亏比不足，机器人也会因顺势/RR闸门自动观望。`;
  // 标题随日线状态切换：三格永远=下行/上行/无方向，多空双向由A/B覆盖，无需第四预案
  const kA = off ? "A·下行·继续 risk-off(顺势空)" : on ? "A·下行·急跌回调(防守)" : "A·下行·向下破位";
  const kB = off ? "B·上行·快速反弹(反弹≠反转)" : on ? "B·上行·risk-on 延续(顺势多)" : "B·上行·向上突破";
  const kC = (off || on) ? "C·震荡无方向" : "C·区间持续";
  return [{ k: kA, v: A, cls: "p-a" }, { k: kB, v: B, cls: "p-b" }, { k: kC, v: C, cls: "p-c" }];
}
// 今日焦点横幅：全球风偏 + BTC位置 + 首要风险 + 一句建议
function genFocus(d) {
  const { price, prevH, prevL, ma30d, funding, volRatio, globalRisk, mstrGap } = d;
  if (price == null) return null;
  const seg = [];
  if (globalRisk) seg.push(`全球 <span class="chip ${globalRisk === "risk-on" ? "risk-on" : globalRisk === "risk-off" ? "risk-off" : "neutral"}" style="font-size:11px">${globalRisk}</span>`);
  const dev = ma30d ? (price / ma30d - 1) * 100 : null;
  let posT = "", advice = "";
  if (prevH != null && price > prevH) { posT = tg("BTC突破昨高"); advice = "顺势偏多·等回踩确认，不追第一波"; }
  else if (prevL != null && price < prevL) { posT = tr("BTC跌破昨低"); advice = "顺势偏空·等反抽不收复再进"; }
  else if (dev != null && dev <= -1) { posT = ty("BTC区间内·日线下方"); advice = "反弹视为做空观察区，多单禁止"; }
  else if (dev != null && dev >= 1) { posT = tg("BTC区间内·日线上方"); advice = "回踩不破可顺势多"; }
  else { posT = ty("BTC贴近30日线·方向不明"); advice = "FLAT为主·等1h收盘突破再按预案执行"; }
  seg.push(posT);
  const risk = (funding != null && funding <= -0.05) ? tr("⚠ 空头极端拥挤，禁追空")
    : (funding != null && funding >= 0.10) ? tr("⚠ 多头过热，禁追多")
    : (volRatio != null && volRatio >= 1.8) ? tr("⚠ 夜间异常放量")
    : (mstrGap != null && mstrGap <= -2) ? ty("⚠ MSTR超额弱势(降杠杆信号)") : "";
  if (risk) seg.push(risk);
  seg.push(`👉 ${tw(advice)}`);
  return seg;
}
// 欧洲上午·美股背景三方共振判定（用户矩阵：BTC+纳指+MSTR 同弱=系统性risk-off；仅MSTR弱≠空BTC）
function usBgVerdict(nq, mstr, btc24) {
  if (nq == null && mstr == null) return null;
  let dot = "y", cn = ty("无明显共振信号");
  if (nq != null && mstr != null && btc24 != null) {
    if (nq <= -0.3 && mstr < 0 && btc24 < 0) { dot = "r"; cn = tr("三者共振走弱＝系统性risk-off倾向，顺势空更有依据"); }
    else if (mstr <= -2 && Math.abs(btc24) < 1 && nq > -0.3) { dot = "y"; cn = ty("仅MSTR走弱＝或为公司自身问题，勿机械做空BTC"); }
    else if (nq >= 0.3 && mstr > 0 && btc24 > 0) { dot = "g"; cn = tg("三者同暖＝机构风偏恢复倾向"); }
  }
  return `${sd(dot)}纳指期货 ${sgnT(nq)}｜MSTR ${sgnT(mstr)}｜BTC 24h ${sgnT(btc24)} → ${cn}`;
}
// 今日重点科技/加密财报（Finnhub 免费日历，经 Worker；只筛巨头与加密概念）
const MEGA_EARN = ["AAPL","MSFT","NVDA","GOOGL","GOOG","AMZN","META","TSLA","AVGO","AMD","MRVL","MU","QCOM","INTC","NFLX","CRM","ORCL","COIN","MSTR","HOOD"];
function fmtEarnings(list, watch) {
  const set = new Set(watch);
  const hits = (list || []).filter(e => set.has(e.symbol));
  if (!hits.length) return null;
  const h = x => x === "bmo" ? "盘前" : x === "amc" ? "盘后" : "盘中";
  return hits.slice(0, 5).map(e => `${e.symbol}(${h(e.hour)})`).join("·");
}
// 新闻关键事件过滤（政策/黑客/ETF/宏观）
const KEY_NEWS_RE = /ETF|SEC|CFTC|hack|exploit|breach|stolen|Fed|FOMC|rate|CPI|inflation|payroll|regulat|ban|lawsuit|court|bankrupt|liquidat|war|sanction|tariff|Trump|Treasury|stablecoin|listing|delist|halving|upgrade|hard fork/i;
function keyEvents(items, n) {
  return (items || []).filter(x => KEY_NEWS_RE.test(x.title || "")).slice(0, n || 3);
}

let _rhCache = {};   // 节奏面板的行情缓存（昨高低/量比/OI等，避免重复请求）
async function loadRhythmLive() {
  if (!$("rhythmGrid")) return;
  const set = (id, html) => { const el = $(id); if (el && html) el.innerHTML = html; };
  const btc = (STATE.market || []).find(c => c.name === "BTC");
  try {
    const [dkl, hkl, eth, sol, oih] = await Promise.allSettled([
      jget(`${BN_SPOT}/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=9`),
      jget(`${BN_SPOT}/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=178`),
      jget(`${BN_SPOT}/api/v3/ticker/24hr?symbol=ETHBTC`),
      jget(`${BN_SPOT}/api/v3/ticker/24hr?symbol=SOLBTC`),
      jget(`${BN_FUT}/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=25`)
    ]);
    const r = {};
    if (dkl.status === "fulfilled" && dkl.value.length >= 2) {
      const prev = dkl.value[dkl.value.length - 2];
      r.prevH = +prev[2]; r.prevL = +prev[3];
    }
    if (hkl.status === "fulfilled" && hkl.value.length >= 40) {
      const vols = hkl.value.map(k => +k[7]);           // quote volume
      const last10 = vols.slice(-10).reduce((a, b) => a + b, 0);
      const prior = vols.slice(0, -10);
      r.volRatio = prior.length ? last10 / (prior.reduce((a, b) => a + b, 0) / prior.length * 10) : null;
      const closes = hkl.value.map(k => +k[4]);
      r.ma30h = closes.slice(-30).reduce((a, b) => a + b, 0) / 30;
      r.h3 = closes.slice(-4);                          // 近4根小时收盘，判方向持续性
      r.lastClose = closes[closes.length - 2];          // 最近一根已收盘1h的收盘价
    }
    if (eth.status === "fulfilled") r.ethbtc = +eth.value.priceChangePercent;
    if (sol.status === "fulfilled") r.solbtc = +sol.value.priceChangePercent;
    if (oih.status === "fulfilled" && oih.value.length >= 2) {
      const a = +oih.value[0].sumOpenInterest, b = +oih.value[oih.value.length - 1].sumOpenInterest;
      r.oiChg = a ? (b / a - 1) * 100 : null;
    }
    _rhCache = r;
  } catch (e) { /* 各项独立降级 */ }
  const r = _rhCache;
  const price = btc ? btc.price : null;
  const ma30d = STATE.btcRegime && STATE.btcRegime.dev != null && price ? price / (1 + STATE.btcRegime.dev / 100) : null;
  const funding = btc ? btc.funding : null;

  // 美股个股涨跌：优先实时报价(Finnhub经Worker,60s刷新)，机器人小时级快照兜底
  const xm0 = (_botLog && _botLog.xm) || null;
  const usQ = sym => (STATE.us && STATE.us[sym] && STATE.us[sym].chg != null) ? STATE.us[sym].chg : null;
  const mstrChg = usQ("MSTR") ?? (xm0 ? xm0.mstr_chg : null);
  const mstrLive = usQ("MSTR") != null;
  const btc24 = btc ? btc.chg : (xm0 ? xm0.btc_chg : null);

  // ① 欧洲上午
  set("rhLevels", levelVerdict(price, r.prevH, r.prevL, ma30d) || "数据不可用");
  set("rhUsBg", usBgVerdict(xm0 ? xm0.nq_chg : null, mstrChg, btc24) || "等机器人跨市场快照…");
  set("rhRel", relVerdict(r.ethbtc, r.solbtc) || "数据不可用");
  set("rhVol", volVerdict(r.volRatio) || "数据不可用");
  set("rhFund", fundOiVerdict(funding, r.oiChg) || "数据不可用");
  const evs = keyEvents(_newsItems, 3);
  set("rhEvents", evs.length
    ? sd("y") + evs.map(x => `<a href="${x.url}" target="_blank" rel="noopener" style="display:block">· ${x.title}</a>`).join("")
    : sd("g") + tg("未捕捉到") + " 政策/黑客/ETF/宏观 类高影响关键词事件");

  // ② 美股盘前（跨市场快照来自机器人，每小时更新）
  const xm = (_botLog && _botLog.xm) || null;
  let mstrGap = null;
  if (xm) {
    const gr = _botLog.global_risk;
    const grDot = gr === "risk-on" ? "g" : gr === "risk-off" ? "r" : "y";
    set("rhMacro", `${sd(grDot)}纳指期货 ${sgnT(xm.nq_chg)}｜10Y ${xm.tnx != null ? tw(xm.tnx + "%") : "n/a"}(${sgnT(xm.tnx_chg)})｜DXY ${sgnT(xm.dxy_chg)} → 全球 <span class="${grDot === "g" ? "g" : grDot === "r" ? "r" : "y"}">${gr || "?"}</span>`);
    const coinChg = usQ("COIN") ?? xm.coin_chg, nvdaChg = usQ("NVDA") ?? xm.nvda_chg;
    mstrGap = (mstrChg != null && xm.btc_chg != null) ? mstrChg - xm.btc_chg : null;
    const mstrCn = mstrGap == null ? "" : mstrGap <= -2 ? ty("（MSTR超额弱势＝降杠杆信号；若BTC稳则或为公司自身问题，勿据此空BTC）")
      : mstrGap >= 1.5 ? tg("（MSTR率先转强＝机构风偏恢复早期信号）") : "（无显著背离）";
    set("rhStocks", `${sd(mstrGap != null && Math.abs(mstrGap) >= 2 ? "y" : "g")}MSTR ${sgnT(mstrChg)}｜COIN ${sgnT(coinChg)}｜NVDA ${sgnT(nvdaChg)} ${mstrLive ? '<span class="badge">实时</span>' : ""} ${mstrCn}`);
    const xt = $("rhXmTime"); if (xt && _botLog.ts) xt.textContent = mstrLive ? "个股实时 · 期指快照" + ago(Math.floor(Date.parse(_botLog.ts) / 1000)).replace("前", "") + "前" : `快照 ${ago(Math.floor(Date.parse(_botLog.ts) / 1000))}`;
  }
  // 今日财报（Finnhub 免费日历，30分钟缓存；宏观数据日历免费源无覆盖 → 指向 TradingView 板块）
  if (hasWorker() && Date.now() - (window._earnT || 0) > 1.8e6) {
    window._earnT = Date.now();
    try {
      const today = new Date().toLocaleDateString("sv");
      const d = await jget(wurl(`type=finnhub&path=calendar/earnings&qs=${encodeURIComponent(`from=${today}&to=${today}`)}`));
      window._earnCache = (d && (d.earningsCalendar || d)) || [];
    } catch (e) { window._earnCache = null; }
  }
  const earn = fmtEarnings(window._earnCache, MEGA_EARN);
  set("rhEvCal", (window._earnCache == null
      ? `${sd("y")}财报日历暂不可用`
      : earn ? `${sd("y")}今日重点财报：${tw(earn)}（财报前后波动放大，谨慎持仓过报）`
             : `${sd("g")}${tg("今日无重点科技/加密财报")}`)
    + ` ｜ 宏观数据：<a href="#sec-env">查经济日历</a>（重大数据前后30分钟避免开新仓）`);

  const plans = genPlans({ price, prevH: r.prevH, prevL: r.prevL, ma30h: r.ma30h, ma30d, funding });
  if (plans) set("rhPlans", plans.map(p => `<div class="plan ${p.cls}"><b>${p.k}</b><span>${p.v}</span></div>`).join(""));

  // 今日焦点横幅：一眼看到 全球风偏｜BTC位置｜首要风险｜建议
  const focus = genFocus({ price, prevH: r.prevH, prevL: r.prevL, ma30d, funding,
                           volRatio: r.volRatio, globalRisk: _botLog && _botLog.global_risk, mstrGap });
  const fb = $("rhFocus");
  if (fb && focus) {
    fb.style.display = "flex";
    fb.innerHTML = `<span class="fseg">🎯 <b>今日焦点</b></span>` +
      focus.map(s => `<span class="fsep">｜</span><span class="fseg">${s}</span>`).join("");
  }

  // ③ 美股盘中·确认清单
  if (r.h3 && r.h3.length >= 4) {
    const dirs = [r.h3[1] - r.h3[0], r.h3[2] - r.h3[1], r.h3[3] - r.h3[2]];
    const ups = dirs.filter(x => x > 0).length;
    const side = price != null && r.ma30h != null ? (price >= r.ma30h ? "30h线上方" : "30h线下方") : "";
    const persistUp = ups >= 2 && price >= r.ma30h, persistDn = ups <= 1 && price < r.ma30h;
    set("rhTrendNow", `${sd(persistUp ? "g" : persistDn ? "r" : "y")}近3根1h ${ups >= 2 ? "多数收涨" : ups <= 1 ? "多数收跌" : "涨跌互现"}·${side} → ${persistUp ? tg("上行有持续性") : persistDn ? tr("下行有持续性") : ty("方向未确认，不追")}`);
  }
  if (mstrChg != null && btc) {
    const same = (mstrChg >= 0) === (btc.chg >= 0);
    set("rhResonance", `${sd(same ? "g" : "y")}MSTR ${sgnT(mstrChg, 2)}(日) vs BTC ${sgnT(btc.chg, 1)}(24h) → ${same ? tg("同向共振，信号可信度↑") : ty("背离，降低置信度观望")}${mstrLive ? ' <span class="badge">实时</span>' : ' <span class="badge">小时快照</span>'}`);
  }
  if (price != null && r.prevL != null) {
    set("rhBreak", price < r.prevL
      ? (r.lastClose != null && r.lastClose < r.prevL
        ? `${sd("r")}1h${tr("收盘确认跌破")}昨低 ${tw(money(r.prevL))} → ${tr("真破位")}`
        : `${sd("y")}现价在昨低 ${tw(money(r.prevL))} 下方但未收盘确认 → ${ty("或为插针，等收盘")}`)
      : `${sd("g")}未破昨低 ${tw(money(r.prevL))}，${tg("支撑尚在")}`);
  }
  set("rhOi2", oiStructVerdict(r.oiChg, btc ? btc.chg : null) || "数据不可用");
  if (funding != null) {
    set("rhCrowd", funding <= -0.05 ? `${sd("r")}资费 ${tw(funding.toFixed(4) + "%")} → ${tr("空头极端拥挤，禁追空")}(闸门已拦)` :
      funding >= 0.10 ? `${sd("r")}资费 ${tw("+" + funding.toFixed(4) + "%")} → ${tr("多头过热，禁追多")}(闸门已拦)` :
      `${sd("g")}资费 ${tw((funding >= 0 ? "+" : "") + funding.toFixed(4) + "%/8h")} → ${tg("未拥挤")}，方向闸门正常放行`);
  }

  // ④ 盘后/亚洲·机器人值守（超2小时未运行=断档红色警报）
  if (_botState) {
    const secAgo = _botState.updated_at ? (Date.now() - Date.parse(_botState.updated_at)) / 1000 : null;
    const ts = secAgo != null ? ago(Math.floor(Date.parse(_botState.updated_at) / 1000)) : "?";
    const stale = secAgo != null && secAgo > 7200;
    const n = (_botState.positions || []).length;
    set("rhBot", stale
      ? `${sd("r")}最近运行 ${tr(ts)}·${_botState.mode || ""} → ${tr("疑似断档！检查 Mac 的 cron 与网络")}`
      : `${sd("g")}最近运行 ${tw(ts)}·${_botState.mode || ""}·持仓 ${tw(n)} → ${n ? tg("值守中") : "空仓观望中"}`);
    set("rhGuard", n ? sd("g") + _botState.positions.map(p => {
      const kind = p.stop_kind === "TRAIL" ? tg("跟踪止盈中") : p.stop_kind === "BE" ? tg("已保本") : ty("初始止损");
      return `${tw(p.symbol)} ${p.side || "LONG"}·${kind}${p.max_hold_hours ? "·限时" + p.max_hold_hours + "h" : ""}`;
    }).join("｜") : sd("g") + "无持仓需护航；保本/跟踪止盈/超时离场随开仓自动启用");
  }
}

//==================== 一日节奏（欧洲时区，高亮当前时段）====================
function rhythmPhase(t) {   // t = 巴黎时间的"当日分钟数"
  if (t >= 420 && t < 840) return 0;    // 07:00–14:00 欧洲上午
  if (t >= 840 && t < 930) return 1;    // 14:00–15:30 美股盘前
  if (t >= 930 && t < 1320) return 2;   // 15:30–22:00 美股盘中
  return 3;                             // 22:00–07:00 盘后/亚洲
}
function updateRhythm() {
  const grid = $("rhythmGrid"); if (!grid) return;
  let h, m;
  try {
    const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date());
    h = +parts.find(p => p.type === "hour").value; m = +parts.find(p => p.type === "minute").value;
  } catch (e) { const d = new Date(); h = d.getHours(); m = d.getMinutes(); }
  const ph = rhythmPhase(h * 60 + m);
  grid.querySelectorAll(".rh-card").forEach(c => c.classList.toggle("active", +c.dataset.phase === ph));
  const now = $("rhythmNow"); if (now) now.textContent = `巴黎时间 ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

async function refreshLive() {
  // 并行刷新所有信息面板，等全部结束后再（可选）自动重建 Evidence
  await Promise.allSettled([
    loadMarket(), loadSentiment(), loadCryptoMacro(), loadDefi(),
    loadCryptoNews(), loadFred(), loadUsStocks(), loadMstr(), loadBot()
  ]);
  renderDecision();   // 规则推断的决策元组（直接可读）
  loadRhythmLive();   // 一日节奏·实时研报（依赖上面已刷新的行情/新闻/机器人数据）
  // 给 LLM 的 Evidence 文本自动重生成：用户正在框选/编辑该文本框时跳过，避免打断复制
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
  $("newsLangBtn") && $("newsLangBtn").addEventListener("click", toggleNewsLang);
  // Paper 持仓器
  PAPER.load();
  $("pOpenBtn") && $("pOpenBtn").addEventListener("click", paperOpen);
  $("pUseLiveBtn") && $("pUseLiveBtn").addEventListener("click", paperFillEntry);
  $("pSym") && $("pSym").addEventListener("change", paperFillEntry);
  $("pResetBtn") && $("pResetBtn").addEventListener("click", paperReset);
  $("pExportBtn") && $("pExportBtn").addEventListener("click", paperExport);
  $("paperOpenBody") && $("paperOpenBody").addEventListener("click", e => {
    const b = e.target.closest && e.target.closest("[data-close]"); if (b) paperClose(+b.getAttribute("data-close"));
  });
  renderPaper();
  setupNav();
  updateRhythm(); setInterval(updateRhythm, 60000);
  // 已平仓：搜索 / 折叠 / 日历
  $("tradeSearch") && $("tradeSearch").addEventListener("input", e => { _tradeQuery = e.target.value; renderBotTrades(); });
  $("tradesMoreBtn") && $("tradesMoreBtn").addEventListener("click", () => { _tradesExpanded = !_tradesExpanded; renderBotTrades(); });
  $("calPrev") && $("calPrev").addEventListener("click", () => { _calOffset++; renderTradeCal(); });
  $("calNext") && $("calNext").addEventListener("click", () => { _calOffset = Math.max(0, _calOffset - 1); renderTradeCal(); });
  $("calGrid") && $("calGrid").addEventListener("click", e => {
    const c = e.target.closest && e.target.closest("[data-day]");
    if (c) { const day = c.getAttribute("data-day"); const inp = $("tradeSearch"); if (inp) inp.value = day; _tradeQuery = day; _tradesExpanded = true; renderBotTrades(); }
  });
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
