/* ============================================================
   live-weather.js — 天气信号 provider（Open-Meteo，浏览器直连）
   ------------------------------------------------------------
   • 免费、无需 API key、支持浏览器 CORS。
   • 拉取 7 个目标城市未来 7 天的每日最高/最低温，推导：
       maxTemp（峰值）、nightTemp（最热夜 = 热带夜指标）、
       heatDays（≥30℃ 天数）、alert（按温度推导的预警等级）。
   • 结果写回 state.cities 并传导到对应城市的商品气候字段，
     从而真实驱动机会分（气候信号 C）。
   • 失败自动降级为种子数据，UI 不受影响。

   端点文档：https://open-meteo.com/en/docs
   ============================================================ */
(function (global) {
  'use strict';
  const LS = global.LiveSignals;
  if (!LS) return;

  // 城市 → 经纬度（Open-Meteo 无需 geocoding）
  const COORDS = {
    Paris: [48.8566, 2.3522], Lyon: [45.7640, 4.8357], Marseille: [43.2965, 5.3698],
    Annecy: [45.8992, 6.1294], Genève: [46.2044, 6.1432], London: [51.5072, -0.1276], Milano: [45.4642, 9.1900],
  };

  const ENDPOINT = 'https://api.open-meteo.com/v1/forecast';

  async function fetchCity(name, lat, lon) {
    const url = `${ENDPOINT}?latitude=${lat}&longitude=${lon}` +
      `&daily=temperature_2m_max,temperature_2m_min&forecast_days=7&timezone=auto`;
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const j = await res.json();
    const maxArr = j && j.daily && j.daily.temperature_2m_max;
    const minArr = j && j.daily && j.daily.temperature_2m_min;
    if (!Array.isArray(maxArr) || !Array.isArray(minArr) || !maxArr.length) throw new Error('无每日温度数据');
    return LS._util.deriveClimate(maxArr, minArr);
  }

  async function provider(state) {
    const cities = (state.cities || []).map(c => c.name).filter(n => COORDS[n]);
    if (!cities.length) return { ok: false, note: '无城市' };

    const patches = {};
    const results = await Promise.allSettled(cities.map(async name => {
      const [lat, lon] = COORDS[name];
      patches[name] = await fetchCity(name, lat, lon);
    }));
    const ok = results.filter(r => r.status === 'fulfilled').length;
    if (ok === 0) return { ok: false, note: '全部城市获取失败（可能网络/CORS）' };

    LS._util.propagateToCities(state, patches);
    return {
      ok: true,
      updated: today(),
      source: 'Open-Meteo（浏览器实时）',
      note: `已更新 ${ok}/${cities.length} 城的实时气温`,
    };
  }

  function today() { try { return new Date().toISOString().slice(0, 10); } catch (e) { return ''; } }

  // 供离线/测试注入：解析逻辑与 fetch 解耦
  provider._deriveFromDaily = (maxArr, minArr) => LS._util.deriveClimate(maxArr, minArr);
  provider._coords = COORDS;

  LS.register('weather', provider);
  global.LiveWeather = provider;
})(window);
