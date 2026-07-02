/* ============================================================
   live-trends.js — 搜索热度信号 provider（Google Trends，数据管道型）
   ------------------------------------------------------------
   • Google Trends 无浏览器可调官方 API（CORS 阻断），故不在浏览器直连，
     而由 GitHub Action（.github/workflows/climate-radar-trends.yml）定时用
     pytrends 抓取，提交 climate-radar/data/trends.json 快照；本 provider 只读该快照。
   • 读取到的关键词热度写回 state.market.trends（数组 {kw, idx, rising}），
     从而真实驱动市场情绪信号 M。
   • 失败（快照缺失/网络异常/形状不符）时不改动 state，优雅降级为种子数据。

   契约见 live.js：register(name, async (state) => { ok, updated, source, note })
   ============================================================ */
(function (global) {
  'use strict';
  const LS = global.LiveSignals;
  if (!LS) return;

  // 快照路径：相对于看板页面（climate-radar/index.html）解析到 climate-radar/data/trends.json
  const SNAPSHOT = 'data/trends.json';

  // 解析逻辑与 fetch 解耦，便于离线单测（见文件末尾 provider._parse）
  function parse(json) {
    if (!json || typeof json !== 'object') throw new Error('快照不是对象');
    const list = json.keywords;
    if (!Array.isArray(list) || !list.length) throw new Error('keywords 为空');
    const out = [];
    for (const it of list) {
      if (!it || typeof it !== 'object') continue;
      const kw = String(it.kw == null ? '' : it.kw).trim();
      if (!kw) continue;
      let idx = Number(it.idx);
      if (!isFinite(idx)) idx = 0;
      idx = Math.max(0, Math.min(100, Math.round(idx)));   // 收敛到 0–100 整数
      out.push({ kw, idx, rising: !!it.rising });
    }
    if (!out.length) throw new Error('无有效关键词');
    return out;
  }

  async function provider(state) {
    let json;
    try {
      // 加时间戳避免浏览器/CDN 强缓存旧快照
      const res = await fetch(SNAPSHOT + '?t=' + Date.now(), { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      if (!res.ok) return { ok: false, note: '快照 HTTP ' + res.status };
      json = await res.json();
    } catch (e) {
      return { ok: false, note: '快照获取失败：' + (e && e.message || e) };
    }

    let trends;
    try {
      trends = parse(json);
    } catch (e) {
      return { ok: false, note: '快照解析失败：' + (e && e.message || e) };
    }

    // 成功才写回；失败路径均已提前 return，不会污染种子数据
    if (state && state.market) state.market.trends = trends;

    const rising = trends.filter(t => t.rising).length;
    return {
      ok: true,
      updated: (json && json.updated) || today(),
      source: (json && json.source) || 'Google Trends (pytrends)',
      note: `搜索热度已更新 ${trends.length} 词（${rising} 词上升）· geo=${(json && json.geo) || 'FR'}`,
    };
  }

  function today() { try { return new Date().toISOString().slice(0, 10); } catch (e) { return ''; } }

  // 供离线/单测注入：给定快照 JSON → [{kw, idx, rising}]
  provider._parse = parse;

  LS.register('trends', provider);
  global.LiveTrends = provider;
})(window);
