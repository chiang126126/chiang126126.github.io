/* ============================================================
   store.js — 数据层（localStorage 持久化 + 种子数据）
   无后端、无账号、无密钥。所有数据存在浏览器本地。
   ============================================================ */
(function (global) {
  'use strict';

  const KEY = 'climate_radar_v1';

  // ---------- 城市气候快照（种子；可在"信号"页手动更新）----------
  const CITIES = [
    { name: 'Paris', zh: '巴黎', maxTemp: 38, nightTemp: 22, alert: 'orange', heatDays: 5, acPenetration: 'low' },
    { name: 'Lyon', zh: '里昂', maxTemp: 39, nightTemp: 23, alert: 'orange', heatDays: 5, acPenetration: 'low' },
    { name: 'Marseille', zh: '马赛', maxTemp: 36, nightTemp: 24, alert: 'yellow', heatDays: 6, acPenetration: 'mid' },
    { name: 'Annecy', zh: '安纳西', maxTemp: 34, nightTemp: 19, alert: 'yellow', heatDays: 4, acPenetration: 'low' },
    { name: 'Genève', zh: '日内瓦', maxTemp: 35, nightTemp: 20, alert: 'yellow', heatDays: 4, acPenetration: 'low' },
    { name: 'London', zh: '伦敦', maxTemp: 33, nightTemp: 19, alert: 'yellow', heatDays: 3, acPenetration: 'low' },
    { name: 'Milano', zh: '米兰', maxTemp: 37, nightTemp: 23, alert: 'orange', heatDays: 5, acPenetration: 'mid' },
  ];

  // ---------- 市场情绪信号（种子）----------
  const MARKET = {
    polymarket: { paris: 72, london: 54, rising: true, note: '巴黎"7月最高温≥38℃"合约概率一周内 58%→72%' },
    trends: [
      { kw: 'climatiseur mobile', idx: 96, rising: true },
      { kw: 'ventilateur', idx: 88, rising: true },
      { kw: 'canicule', idx: 100, rising: true },
      { kw: 'rafraîchir appartement', idx: 61, rising: true },
      { kw: 'ventilateur de cou', idx: 47, rising: true },
    ],
    news: '家乐福单日售出约 3 万台风扇 / 空调；法国 5 月底一周售出约 68 万台风扇（同比 +1500%）；多款移动空调售罄。',
    updated: '2026-07-01',
  };

  // ---------- 种子商品 ----------
  function seedProducts() {
    return [
      mk({
        emoji: '❄️', name: '移动空调 (COMFEE 9000BTU)', nameFr: 'Climatiseur mobile COMFEE 9000 BTU',
        cat: '降温家电', category: 'ac', city: 'Paris',
        maxTemp: 38, heatDays: 5, nightTemp: 22, alert: 'orange', acPenetration: 'low',
        polymarket: 72, polymarketRising: true, googleTrends: 96, trendsRising: true, newsShortage: 'widespread', retailStockout: true, retailTight: true,
        euPrice: 329, chinaCost: 165, weightKg: 24, handCarry: false, demoEasy: true, restock: 'tight',
        complianceRisk: 'high', returnRisk: 'mid',
        views: 240, inquiries: 12, conversions: 3, fastPickup: true, b2bDemand: false,
        note: 'Leboncoin 一周十多个咨询；重、需合规，适合本地现货清货 + 配件组合。',
      }),
      mk({
        emoji: '🧊', name: '桌面半导体制冷风扇', nameFr: 'Ventilateur de bureau à refroidissement (Peltier)',
        cat: '个人降温', category: 'desk', city: 'Paris',
        maxTemp: 38, heatDays: 5, nightTemp: 22, alert: 'orange', acPenetration: 'low',
        polymarket: 72, polymarketRising: true, googleTrends: 58, trendsRising: true, newsShortage: 'emerging', retailStockout: false, retailTight: true,
        euPrice: 39.9, chinaCost: 11, weightKg: 0.9, handCarry: true, demoEasy: true, restock: 'yes',
        complianceRisk: 'mid', returnRisk: 'low',
        views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: true,
        note: '办公室人手一个；小、轻、制冷可演示。欧洲办公室大多无空调，B端潜力。',
      }),
      mk({
        emoji: '🧣', name: 'PCM 清凉颈圈', nameFr: 'Tour de cou rafraîchissant PCM',
        cat: '个人降温', category: 'wearable', city: 'Lyon',
        maxTemp: 39, heatDays: 5, nightTemp: 23, alert: 'orange', acPenetration: 'low',
        polymarket: 68, polymarketRising: true, googleTrends: 47, trendsRising: true, newsShortage: 'none', retailStockout: false, retailTight: false,
        euPrice: 16.9, chinaCost: 3.2, weightKg: 0.25, handCarry: true, demoEasy: true, restock: 'yes',
        complianceRisk: 'low', returnRisk: 'low',
        views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: false,
        note: '无电、无合规负担，毛利极高，适合组合包搭售 / 运动防暑包。',
      }),
      mk({
        emoji: '🪟', name: '移动空调窗户密封布', nameFr: 'Kit calfeutrage fenêtre pour climatiseur mobile',
        cat: '配件周边', category: 'accessory', city: 'Paris',
        maxTemp: 38, heatDays: 5, nightTemp: 22, alert: 'orange', acPenetration: 'low',
        polymarket: 72, polymarketRising: true, googleTrends: 44, trendsRising: true, newsShortage: 'emerging', retailStockout: true, retailTight: true,
        euPrice: 24.9, chinaCost: 4.5, weightKg: 0.4, handCarry: true, demoEasy: true, restock: 'yes',
        complianceRisk: 'low', returnRisk: 'low',
        views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: false,
        note: '示例决策卡商品：买移动空调的人都需要；轻、无合规、毛利高，理想组合品。',
      }),
      mk({
        emoji: '💨', name: '挂脖风扇', nameFr: 'Ventilateur de cou portable',
        cat: '个人降温', category: 'wearable', city: 'London',
        maxTemp: 33, heatDays: 3, nightTemp: 19, alert: 'yellow', acPenetration: 'low',
        polymarket: 54, polymarketRising: true, googleTrends: 47, trendsRising: true, newsShortage: 'none', retailStockout: false, retailTight: false,
        euPrice: 22.9, chinaCost: 5.5, weightKg: 0.3, handCarry: true, demoEasy: true, restock: 'yes',
        complianceRisk: 'mid', returnRisk: 'mid',
        views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: false,
        note: '带电池注意合规；通勤 / 户外场景，视觉化强。',
      }),
      mk({
        emoji: '🌫️', name: '手持喷雾风扇', nameFr: 'Ventilateur brumisateur portable',
        cat: '个人降温', category: 'wearable', city: 'Marseille',
        maxTemp: 36, heatDays: 6, nightTemp: 24, alert: 'yellow', acPenetration: 'mid',
        polymarket: 60, polymarketRising: false, googleTrends: 52, trendsRising: true, newsShortage: 'none', retailStockout: false, retailTight: false,
        euPrice: 15.9, chinaCost: 3.8, weightKg: 0.3, handCarry: true, demoEasy: true, restock: 'yes',
        complianceRisk: 'mid', returnRisk: 'mid',
        views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: false,
        note: '海滩 / 户外 / 运动场景；轻量起步好，但同质化竞争激烈。',
      }),
      mk({
        emoji: '🪟', name: '隔热遮光帘', nameFr: 'Rideau thermique occultant anti-chaleur',
        cat: '配件周边', category: 'accessory', city: 'Lyon',
        maxTemp: 39, heatDays: 5, nightTemp: 23, alert: 'orange', acPenetration: 'low',
        polymarket: 68, polymarketRising: true, googleTrends: 40, trendsRising: true, newsShortage: 'none', retailStockout: false, retailTight: false,
        euPrice: 29.9, chinaCost: 7, weightKg: 0.8, handCarry: true, demoEasy: false, restock: 'yes',
        complianceRisk: 'low', returnRisk: 'mid',
        views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: false,
        note: '被动降温、无电、无合规；教育成本略高，适合组合叙事。',
      }),
      mk({
        emoji: '🦟', name: '防蚊透气纱窗', nameFr: 'Moustiquaire de fenêtre magnétique',
        cat: '配件周边', category: 'accessory', city: 'Annecy',
        maxTemp: 34, heatDays: 4, nightTemp: 19, alert: 'yellow', acPenetration: 'low',
        polymarket: 50, polymarketRising: false, googleTrends: 38, trendsRising: false, newsShortage: 'none', retailStockout: false, retailTight: false,
        euPrice: 18.9, chinaCost: 4, weightKg: 0.35, handCarry: true, demoEasy: false, restock: 'yes',
        complianceRisk: 'low', returnRisk: 'mid',
        views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: false,
        note: '开窗通风降温的伴生需求；季节性、非应急，适合观察 / 组合。',
      }),
    ];
  }

  let _id = 100;
  function mk(p) { return { id: 'p' + (++_id), createdAt: '2026-07-01', ...p }; }

  // ---------- 组合包（按场景卖解决方案）----------
  const BUNDLES = [
    {
      id: 'b1', emoji: '🏠', name: '出租公寓热浪应急包', nameFr: 'Kit canicule appartement locatif',
      desc: '免安装、可随时拆走，专为老房 / 出租公寓 / 共管物业设计',
      items: ['移动空调 (免安装款)', '窗户密封布', '排风管转接头', '隔热遮光帘', '法语安装+使用手册'],
      sumPrice: 402.6, bundlePrice: 359, category: 'ac',
    },
    {
      id: 'b2', emoji: '💻', name: '办公室桌面降温包', nameFr: 'Kit rafraîchissement bureau',
      desc: '欧洲办公室大多无空调——人手一套，B 端可批量',
      items: ['桌面半导体制冷风扇', 'PCM 清凉颈圈', '手持喷雾风扇'],
      sumPrice: 72.7, bundlePrice: 59, category: 'desk',
    },
    {
      id: 'b3', emoji: '🏃', name: '运动 / 户外防暑包', nameFr: 'Kit anti-chaleur sport & plein air',
      desc: '通勤、骑行、露营、看比赛——随身降温三件套',
      items: ['挂脖风扇', 'PCM 清凉颈圈', '手持喷雾风扇'],
      sumPrice: 55.7, bundlePrice: 44.9, category: 'wearable',
    },
    {
      id: 'b4', emoji: '🔧', name: '移动空调安装配件包', nameFr: 'Kit accessoires installation clim mobile',
      desc: '已有移动空调的用户加购——提升客单价、减少找配件的麻烦',
      items: ['窗户密封布', '排风管转接头', '隔热遮光帘', '防蚊纱窗'],
      sumPrice: 91.6, bundlePrice: 74.9, category: 'accessory',
    },
  ];

  // ---------- 持久化 ----------
  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { console.warn('load failed', e); }
    const seed = { products: seedProducts(), market: MARKET, cities: CITIES, bundles: BUNDLES, seeded: true };
    save(seed);
    return seed;
  }
  function save(state) {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { console.warn('save failed', e); }
  }
  function reset() { localStorage.removeItem(KEY); return load(); }

  function newProduct() {
    _id = Math.max(_id, Date.now() % 100000);
    return {
      id: 'p' + (++_id) + '_' + (performance.now() | 0),
      emoji: '📦', name: '', nameFr: '', cat: '个人降温', category: 'wearable', city: 'Paris',
      maxTemp: 35, heatDays: 3, nightTemp: 20, alert: 'yellow', acPenetration: 'low',
      polymarket: 60, polymarketRising: false, googleTrends: 50, trendsRising: false,
      newsShortage: 'none', retailStockout: false, retailTight: false,
      euPrice: 25, chinaCost: 6, weightKg: 0.5, handCarry: true, demoEasy: true, restock: 'yes',
      complianceRisk: 'low', returnRisk: 'low',
      views: 0, inquiries: 0, conversions: 0, fastPickup: false, b2bDemand: false,
      note: '', createdAt: '2026-07-02',
    };
  }

  global.Store = { load, save, reset, newProduct, CITIES, defaultBundles: BUNDLES };
})(window);
