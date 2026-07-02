/* ============================================================
   live-buzz.js — 社媒 & 新闻舆情 provider（数据管道型）
   ------------------------------------------------------------
   • Reddit / X / Instagram / 新闻站均无法在浏览器直连（CORS + 反爬），
     故走 GitHub Action 数据管道：定时抓取 Reddit 公共 JSON + Google News
     RSS，归类写出 data/buzz.json，本 provider 只读该快照。
   • 读取到的舆情写回 state.market.buzz（主题计数 + 最新条目 + 来源状态），
     在「信号台」的"社媒 & 新闻雷达"面板展示，并进入每周报告。
   • 失败时不改动 state，优雅降级为种子数据。

   契约见 live.js：register(name, async (state) => { ok, updated, source, note })
   ============================================================ */
(function (global) {
  'use strict';
  const LS = global.LiveSignals;
  if (!LS) return;

  const DATA_URL = 'data/buzz.json';
  const THEME_KEYS = ['heat', 'shortage', 'ac_rush', 'office_heat', 'buying_need'];

  // 纯解析：快照 JSON → 规范化 buzz 对象（可离线单测）
  function parse(json) {
    if (!json || typeof json !== 'object') throw new Error('快照不是对象');
    const items = Array.isArray(json.items) ? json.items : [];
    // 规范化主题计数（缺失补 0）
    const themes = {};
    THEME_KEYS.forEach(k => { themes[k] = Math.max(0, parseInt((json.themes || {})[k], 10) || 0); });
    // 若快照未带 themes，则从 items 现算
    if (!json.themes) items.forEach(it => { if (themes[it.theme] != null) themes[it.theme]++; });
    const clean = items
      .filter(it => it && it.title)
      .slice(0, 40)
      .map(it => ({
        source: String(it.source || ''),
        theme: String(it.theme || ''),
        title: String(it.title || ''),
        url: String(it.url || ''),
        snippet: String(it.snippet || ''),
        origin: String(it.publisher || it.sub || ''),
        ts: String(it.ts || ''),
        lang: String(it.lang || ''),
      }));
    return {
      updated: String(json.updated || ''),
      sources: json.sources || {},
      themes,
      items: clean,
      total: clean.length,
      note: String(json.note || ''),
    };
  }

  async function provider(state) {
    let json;
    try {
      const res = await fetch(DATA_URL + '?t=' + Date.now(), { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      if (!res.ok) return { ok: false, note: '舆情快照 HTTP ' + res.status };
      json = await res.json();
    } catch (e) {
      return { ok: false, note: '舆情快照获取失败：' + (e && e.message || e) };
    }
    let buzz;
    try { buzz = parse(json); } catch (e) { return { ok: false, note: '舆情快照解析失败：' + (e && e.message || e) }; }

    if (state && state.market) state.market.buzz = buzz;
    const sh = buzz.themes.shortage || 0, rush = buzz.themes.ac_rush || 0;
    return {
      ok: true,
      updated: buzz.updated || today(),
      source: '社媒 & 新闻（Reddit + Google News）',
      note: `舆情 ${buzz.total} 条 · 缺货 ${sh} · 抢购 ${rush} 提及`,
    };
  }

  function today() { try { return new Date().toISOString().slice(0, 10); } catch (e) { return ''; } }

  provider._parse = parse;
  provider._themeKeys = THEME_KEYS;
  LS.register('buzz', provider);
  global.LiveBuzz = provider;
})(window);
