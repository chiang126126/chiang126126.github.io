/* ============================================================
   live-retail.js — 零售库存/价格信号 provider（数据管道型）
   ------------------------------------------------------------
   • 电商站点（Amazon / Darty / Carrefour）无法在浏览器直连
     （CORS + 反爬），因此本信号走【GitHub Action 数据管道】：
     定时脚本尽力抓取库存/价格快照并提交 data/retail.json，
     前端只读该 JSON，把快照映射到对应商品的库存字段。
   • 这是最脆弱的信号：抓取常常拿不到东西，因此 retail.json
     以【手工维护】为主，脚本仅在确信时覆盖。UI 永远优雅降级。
   • 映射：对 retail.json 里每个快照，用 match 关键字对商品
     name / category 做子串匹配，命中后按 status 设置：
       out  → retailStockout=true,  retailTight=true
       tight→ retailStockout=false, retailTight=true
       ok   → retailStockout=false, retailTight=false
     这些字段进入市场情绪分（scoring.js 的 marketScore），
     因此会真实影响机会分。
   • 失败（网络/JSON 异常）时返回 {ok:false}，不改动任何商品。

   数据文件：climate-radar/data/retail.json（可手动编辑）
   ============================================================ */
(function (global) {
  'use strict';
  const LS = global.LiveSignals;
  if (!LS) return;

  // 相对 index.html 的路径
  const DATA_URL = 'data/retail.json';

  // status → 商品库存字段
  const STATUS_MAP = {
    out: { retailStockout: true, retailTight: true },
    tight: { retailStockout: false, retailTight: true },
    ok: { retailStockout: false, retailTight: false },
  };

  // 单条快照 ↔ 单个商品 是否匹配（纯函数，可离线测试）
  // 规则：match 关键字是商品 name 的子串（不分大小写），或等于商品 category；
  //       若快照声明了 category，则再按 category 精确限定用于消歧
  //      （例如"移动空调"不应误命中"移动空调窗户密封布"）。
  function _match(item, product) {
    if (!item || !product) return false;
    const kw = String(item.match || '').toLowerCase().trim();
    if (!kw) return false;
    const name = String(product.name || '').toLowerCase();
    const cat = String(product.category || '').toLowerCase();
    if (item.category && cat !== String(item.category).toLowerCase()) return false;
    return name.indexOf(kw) !== -1 || cat === kw;
  }

  // 把 retail.json 映射到 products，返回被应用信号的商品数（纯函数，可离线测试）
  function _apply(json, products) {
    if (!json || !Array.isArray(json.items) || !Array.isArray(products)) return 0;
    let count = 0;
    products.forEach(prod => {
      // 首个 status 合法且命中的快照生效
      const hit = json.items.find(it => it && STATUS_MAP[it.status] && _match(it, prod));
      if (!hit) return;
      const patch = STATUS_MAP[hit.status];
      prod.retailStockout = patch.retailStockout;
      prod.retailTight = patch.retailTight;
      if (typeof hit.price === 'number') prod.retailPrice = hit.price; // 附带价格，不参与评分
      count++;
    });
    return count;
  }

  async function fetchJSON(url) {
    const bust = url + (url.indexOf('?') === -1 ? '?' : '&') + 't=' + Date.now();
    const res = await fetch(bust, { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  async function provider(state) {
    const products = state && state.products;
    if (!Array.isArray(products) || !products.length) return { ok: false, note: '无商品' };

    let json;
    try {
      json = await fetchJSON(DATA_URL);
    } catch (e) {
      return { ok: false, note: '零售快照读取失败：' + (e && e.message || e) };
    }
    if (!json || !Array.isArray(json.items) || !json.items.length) {
      return { ok: false, note: '零售快照为空' };
    }

    const n = _apply(json, products);
    return {
      ok: true,
      updated: json.updated || today(),
      source: json.source || '零售快照（data/retail.json）',
      note: `已映射 ${n} 个商品的零售库存信号（共 ${json.items.length} 条快照）`,
    };
  }

  function today() { try { return new Date().toISOString().slice(0, 10); } catch (e) { return ''; } }

  // 供离线/测试注入：映射逻辑与 fetch 解耦
  provider._apply = _apply;
  provider._match = _match;
  provider._statusMap = STATUS_MAP;

  LS.register('retail', provider);
  global.LiveRetail = provider;
})(window);
