/* ============================================================
   live-polymarket.js — 市场情绪 provider（Polymarket Gamma，浏览器直连）
   ------------------------------------------------------------
   • Polymarket 公共 Gamma API 免 key、支持浏览器 CORS。
   • 拉取当前活跃市场，筛选“极端高温 / 气温 / 气候”相关合约，
     提取隐含概率（0–100）并映射到 巴黎 / 伦敦，写回
     state.market.polymarket → 真实驱动市场情绪信号 M。
   • 若当前无任何匹配市场，诚实返回 ok:false（不改写种子数据）。
   • 失败自动降级为种子数据，UI 不受影响。

   端点：https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=500
   参考字段：question / outcomes / outcomePrices / lastTradePrice /
             volume / active / closed / oneDayPriceChange
   （outcomes、outcomePrices 通常是 JSON 编码的字符串数组。）
   ============================================================ */
(function (global) {
  'use strict';
  const LS = global.LiveSignals;
  if (!LS) return;

  // 只取活跃、未结算的市场；按体量排序让高流动性市场优先
  const ENDPOINT =
    'https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=500';

  // ---- 关键词（英/法混合，覆盖高温 / 气温 / 气候）----
  const HEAT_RE =
    /temperature|heat\s?wave|heatwave|\bheat\b|canicule|degree|°\s?c|celsius|fahrenheit|climate|hottest|warmest|record\s+(?:high|heat|temp)/i;
  const PARIS_RE = /paris|france|french/i;
  const LONDON_RE = /london|\buk\b|britain|england|united\s+kingdom/i;
  const GENERIC_RE = /europe|european|global|world|earth|hemisphere/i;

  function safeJson(v) {
    if (Array.isArray(v)) return v;
    if (typeof v === 'string') {
      try { const p = JSON.parse(v); return Array.isArray(p) ? p : null; }
      catch (e) { return null; }
    }
    return null;
  }

  function clampPct(n) {
    if (!isFinite(n)) return null;
    return Math.max(0, Math.min(100, Math.round(n)));
  }

  // 从单个市场对象提取“Yes / 高于阈值”的隐含概率（0–100），失败返回 null
  function impliedProb(m) {
    const outcomes = safeJson(m.outcomes);
    const prices = safeJson(m.outcomePrices);
    if (outcomes && prices && outcomes.length === prices.length && prices.length) {
      let idx = outcomes.findIndex(o => String(o).trim().toLowerCase() === 'yes');
      if (idx < 0) idx = 0; // 二元“高于阈值”市场通常首个结果即“成立”
      const p = Number(prices[idx]);
      if (isFinite(p)) return clampPct(p * 100);
    }
    const lt = Number(m.lastTradePrice);
    if (isFinite(lt)) return clampPct(lt * 100);
    return null;
  }

  function marketText(m) {
    return String((m && (m.question || m.title || m.slug)) || '');
  }

  function trunc(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  // ---- 纯解析函数：markets[] -> {paris, london, rising, note} 或 null ----
  function parse(markets) {
    if (!Array.isArray(markets)) return null;

    const cand = [];
    for (const m of markets) {
      if (!m || typeof m !== 'object') continue;
      if (m.closed === true || m.active === false) continue; // 只要可交易市场
      const q = marketText(m);
      if (!q || !HEAT_RE.test(q)) continue;

      const prob = impliedProb(m);
      if (prob == null) continue;

      let scope;
      if (PARIS_RE.test(q)) scope = 'paris';
      else if (LONDON_RE.test(q)) scope = 'london';
      else if (GENERIC_RE.test(q)) scope = 'generic';
      else continue; // 是高温市场但无匹配地理 → 跳过

      cand.push({
        q,
        prob,
        scope,
        vol: Number(m.volume != null ? m.volume : m.volumeNum) || 0,
        rising: Number(m.oneDayPriceChange) > 0,
      });
    }
    if (!cand.length) return null;

    const pick = scope =>
      cand.filter(c => c.scope === scope).sort((a, b) => b.vol - a.vol)[0] || null;

    const paris = pick('paris');
    const london = pick('london');
    const generic = pick('generic');

    // 兜底链：专属市场 → 通用欧洲/全球市场 → 另一城市
    const parisPick = paris || generic || london;
    const londonPick = london || generic || paris;
    if (!parisPick && !londonPick) return null;

    const rising = !!((parisPick && parisPick.rising) || (londonPick && londonPick.rising));

    // ---- 组织诚实的说明 ----
    const caveats = [];
    if (!paris && generic) caveats.push('巴黎沿用欧洲/全球高温市场');
    else if (!paris && london) caveats.push('巴黎暂无专属合约，沿用伦敦数据');
    if (!london && generic) caveats.push('伦敦沿用欧洲/全球高温市场');
    else if (!london && paris) caveats.push('伦敦暂无专属合约，沿用巴黎数据');

    let note = `Polymarket 匹配 ${cand.length} 个高温/气候市场`;
    if (paris) note += `｜${trunc(paris.q, 48)}`;
    if (caveats.length) note += `（${caveats.join('；')}）`;

    return {
      paris: parisPick.prob,
      london: londonPick.prob,
      rising,
      note,
    };
  }

  async function provider(state) {
    const pm = state && state.market && state.market.polymarket;
    if (!pm) return { ok: false, note: '无市场情绪信号槽' };

    let markets;
    try {
      const res = await fetch(ENDPOINT, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const body = await res.json();
      // Gamma /markets 直接返回数组；个别代理会包一层 { data: [...] }
      markets = Array.isArray(body) ? body : (body && Array.isArray(body.data) ? body.data : null);
    } catch (e) {
      return { ok: false, note: '获取失败：' + ((e && e.message) || e) };
    }
    if (!markets) return { ok: false, note: '返回格式异常' };

    const parsed = parse(markets);
    if (!parsed) return { ok: false, note: '当前无匹配的高温/气候预测市场' };

    // 成功：写回市场情绪信号（只动 polymarket 槽）
    pm.paris = parsed.paris;
    pm.london = parsed.london;
    pm.rising = parsed.rising;
    pm.note = parsed.note;

    return {
      ok: true,
      updated: today(),
      source: 'Polymarket（浏览器实时）',
      note: parsed.note,
    };
  }

  function today() { try { return new Date().toISOString().slice(0, 10); } catch (e) { return ''; } }

  // 供离线/测试注入：解析逻辑与 fetch 解耦（对应 live-weather 的 _deriveFromDaily）
  provider._parse = parse;
  provider._endpoint = ENDPOINT;

  LS.register('polymarket', provider);
  global.LivePolymarket = provider;
})(window);
