/* ============================================================
   live.js — 实时信号注册与水合（hydration）层
   ------------------------------------------------------------
   混合架构的统一入口：
   • 浏览器直连型 provider（天气 Open-Meteo、Polymarket）——在用户浏览器里
     实时 fetch，不经任何后端 / 密钥。
   • 数据管道型 provider（Google Trends、电商库存）——由 GitHub Action 定时
     抓取并提交 JSON 快照，前端只读 climate-radar/data/*.json。

   每个 provider 遵循同一契约：
     LiveSignals.register(name, async (state) => patch|null)
   provider 直接把结果写进 state（cities / market / products 的气候字段），
   并返回 { ok, updated, source, note }；失败返回 null，则保留种子数据。

   所有 provider 均"优雅降级"：网络不可用时 UI 照常工作（种子数据）。
   ============================================================ */
(function (global) {
  'use strict';

  const providers = [];   // { name, fn }
  const meta = {};        // name -> { ok, updated, source, note, ts }

  function register(name, fn) { providers.push({ name, fn }); }

  function withTimeout(promise, ms) {
    return Promise.race([
      promise,
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms)),
    ]);
  }

  // 逐个（并发）运行 provider；每个成功即回调，UI 可增量刷新
  async function hydrate(state, onUpdate, opts = {}) {
    const timeout = opts.timeout || 9000;
    await Promise.all(providers.map(async ({ name, fn }) => {
      try {
        const patch = await withTimeout(Promise.resolve(fn(state)), timeout);
        if (patch && patch.ok) {
          meta[name] = { ...patch, ts: nowISO() };
          if (typeof onUpdate === 'function') onUpdate(name, patch, state);
        } else {
          meta[name] = { ok: false, note: (patch && patch.note) || '无数据', ts: nowISO() };
        }
      } catch (e) {
        meta[name] = { ok: false, note: '获取失败：' + (e && e.message || e), ts: nowISO() };
        // 静默降级；仅在控制台留痕
        if (global.console) console.warn('[LiveSignals]', name, e && e.message);
      }
    }));
    return meta;
  }

  function status() { return meta; }
  function providerNames() { return providers.map(p => p.name); }
  function nowISO() { try { return new Date().toISOString(); } catch (e) { return ''; } }

  // ---- 共享工具，供各 provider 复用 ----
  // 从每日温度序列推导气候字段
  function deriveClimate(maxArr, minArr, heatThreshold = 30) {
    const maxT = Math.round(Math.max(...maxArr));
    const worstNight = Math.round(Math.max(...minArr)); // 最热的一夜（热带夜指标）
    let heatDays = 0;
    for (const m of maxArr) if (m >= heatThreshold) heatDays++;
    const alert = maxT >= 40 ? 'red' : maxT >= 37 ? 'orange' : maxT >= 33 ? 'yellow' : 'none';
    return { maxTemp: maxT, nightTemp: worstNight, heatDays, alert };
  }

  // 把城市级气候字段传导到该城市的商品上（只改气候字段，不动市场/商品/实盘字段）
  function propagateToCities(state, cityPatches) {
    (state.cities || []).forEach(c => {
      const p = cityPatches[c.name];
      if (p) Object.assign(c, p);
    });
    (state.products || []).forEach(prod => {
      const p = cityPatches[prod.city];
      if (p) { prod.maxTemp = p.maxTemp; prod.nightTemp = p.nightTemp; prod.heatDays = p.heatDays; prod.alert = p.alert; }
    });
  }

  global.LiveSignals = {
    register, hydrate, status, providerNames,
    _util: { deriveClimate, propagateToCities, withTimeout },
  };
})(window);
