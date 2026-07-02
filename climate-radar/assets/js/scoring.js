/* ============================================================
   scoring.js — Opportunity Score engine
   气候商机雷达 — 机会评分模型

   把四类信号 + 风险 放进同一张决策表：
     C  气候信号   Climate        权重 0.28
     M  市场情绪   Market         权重 0.24
     P  商品与渠道 Product/Channel 权重 0.30
     L  本地反馈   Local feedback 权重 0.18 (无数据时权重顺延)

   Risk penalty: 合规风险 + 退货风险 在加权后扣分。
   输出 0-100 机会分，并映射到 BUY/PRESELL/TEST/WATCH/SKIP。
   每一分都可解释：drivers[] 给出正/负/信息三类理由。
   ============================================================ */
(function (global) {
  'use strict';

  const clamp = (n, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, n));
  const lerp = (v, x0, x1, y0, y1) => y0 + ((clamp(v, x0, x1) - x0) / (x1 - x0 || 1)) * (y1 - y0);

  // 分段映射：把一个原始值按台阶映射成 0-100 子分
  function steps(value, table) {
    // table: [[threshold, score], ...] 升序；线性内插
    for (let i = 0; i < table.length; i++) {
      const [t, s] = table[i];
      if (value <= t) {
        if (i === 0) return s;
        const [pt, ps] = table[i - 1];
        return lerp(value, pt, t, ps, s);
      }
    }
    return table[table.length - 1][1];
  }

  const ALERT = { none: 0, yellow: 42, orange: 76, red: 100 };
  const PENETRATION = { low: 1, mid: 0.55, high: 0.2 };      // 空调渗透率越低机会越大
  const LEVEL = { low: 0, mid: 0.5, high: 1 };
  const RESTOCK = { yes: 1, tight: 0.55, no: 0.15 };

  // ---------- 1. 气候信号 ----------
  function climateScore(p) {
    const tempS = steps(num(p.maxTemp), [[26, 8], [30, 34], [33, 58], [35, 74], [38, 90], [41, 100]]);
    const daysS = steps(num(p.heatDays), [[0, 0], [1, 24], [2, 46], [3, 70], [4, 86], [6, 100]]);
    const nightS = num(p.nightTemp) >= 20 ? steps(num(p.nightTemp), [[20, 60], [23, 82], [26, 100]]) : lerp(num(p.nightTemp), 12, 20, 10, 55);
    const alertS = ALERT[p.alert] ?? 0;
    const penBoost = PENETRATION[p.acPenetration] ?? 0.55;

    // 温度 + 持续天数是主驱动；夜温 + 预警加成；低渗透率放大
    let base = tempS * 0.34 + daysS * 0.30 + nightS * 0.18 + alertS * 0.18;
    base = base * (0.72 + 0.28 * penBoost); // 渗透率越低整体上浮
    return clamp(base);
  }

  // ---------- 2. 市场情绪信号 ----------
  function marketScore(p) {
    const poly = num(p.polymarket);                    // 0-100 极端高温概率定价
    const polyTrend = p.polymarketRising ? 12 : 0;
    const trends = num(p.googleTrends);                // 0-100 搜索热度
    const trendsTrend = p.trendsRising ? 12 : 0;
    const newsS = steps01(p.newsShortage, { none: 0, emerging: 60, widespread: 100 });
    const stockoutS = p.retailStockout ? 100 : (p.retailTight ? 55 : 15);

    let s = poly * 0.28 + trends * 0.30 + newsS * 0.22 + stockoutS * 0.20;
    s += polyTrend + trendsTrend;                      // 上升趋势额外加成（提前交易预期）
    return clamp(s);
  }

  // ---------- 3. 商品与渠道信号 ----------
  function productScore(p) {
    const eu = num(p.euPrice), cost = num(p.chinaCost);
    const margin = eu > 0 ? (eu - cost) / eu : 0;                 // 毛利率
    const marginS = steps(margin * 100, [[10, 8], [30, 40], [45, 66], [60, 84], [75, 100]]);
    const logisticsS = logistics(p);                             // 轻/小/易运
    const demoS = p.demoEasy ? 100 : 45;                         // 是否容易演示、消费者秒懂
    const restockS = (RESTOCK[p.restock] ?? 0.55) * 100;         // 补货是否来得及

    let s = marginS * 0.40 + logisticsS * 0.22 + demoS * 0.16 + restockS * 0.22;
    return clamp(s);
  }
  function logistics(p) {
    const wS = steps(num(p.weightKg), [[0.3, 100], [1, 88], [3, 68], [8, 42], [20, 18], [40, 6]]);
    const carry = p.handCarry ? 12 : 0;                          // 可人肉带货
    return clamp(wS * 0.86 + carry + (num(p.weightKg) <= 2 ? 4 : 0));
  }

  // ---------- 4. 本地销售反馈信号 ----------
  function localScore(p) {
    const views = num(p.views), inq = num(p.inquiries), deals = num(p.conversions);
    if (views <= 0 && inq <= 0 && deals <= 0) return { score: null, hasData: false };
    // 询盘率 + 成交率是核心；快速取货 / B端需求加成
    const inqRate = views > 0 ? inq / views : (inq > 0 ? 0.25 : 0);
    const dealRate = inq > 0 ? deals / inq : 0;
    const inqS = steps(inqRate * 100, [[2, 20], [6, 50], [12, 78], [20, 100]]);
    const dealS = steps(dealRate * 100, [[5, 25], [15, 55], [30, 80], [50, 100]]);
    const volS = steps(inq, [[1, 20], [4, 50], [10, 80], [20, 100]]);
    let s = inqS * 0.34 + dealS * 0.30 + volS * 0.20;
    if (p.fastPickup) s += 8;                                    // 要求当天/次日取货 = 刚需
    if (p.b2bDemand) s += 8;                                     // B端小批量 = 高价值
    return { score: clamp(s), hasData: true };
  }

  // ---------- 风险 ----------
  function riskPenalty(p) {
    const comp = LEVEL[p.complianceRisk] ?? 0;
    const ret = LEVEL[p.returnRisk] ?? 0;
    const penalty = comp * 13 + ret * 10;                        // 高合规风险最多 -13，高退货 -10
    return { penalty, comp, ret };
  }

  // ---------- 主函数 ----------
  const WEIGHTS = { C: 0.28, M: 0.24, P: 0.30, L: 0.18 };

  function score(p) {
    const C = climateScore(p);
    const M = marketScore(p);
    const P = productScore(p);
    const Lr = localScore(p);
    const L = Lr.hasData ? Lr.score : null;

    // 权重归一（无本地数据时把 L 的权重按比例分给 C/M/P）
    let w = { ...WEIGHTS };
    if (L === null) {
      const rest = w.C + w.M + w.P;
      const share = w.L;
      w = { C: w.C + share * (w.C / rest), M: w.M + share * (w.M / rest), P: w.P + share * (w.P / rest), L: 0 };
    }
    let weighted = C * w.C + M * w.M + P * w.P + (L ?? 0) * w.L;

    const rk = riskPenalty(p);
    let final = clamp(weighted - rk.penalty);

    const signals = { C: r0(C), M: r0(M), P: r0(P), L: L === null ? null : r0(L) };
    const rec = recommend(final);
    const margin = num(p.euPrice) > 0 ? (num(p.euPrice) - num(p.chinaCost)) / num(p.euPrice) : 0;

    return {
      total: r0(final),
      signals,
      weights: w,
      rec,
      margin: r0(margin * 100),
      risk: rk,
      drivers: drivers(p, signals, rk, margin, Lr.hasData),
      priceRange: priceRange(p),
    };
  }

  function recommend(s) {
    if (s >= 80) return { code: 'BUY', label: '备货', action: '小批量备货，本地现货即时交付', tone: 'buy' };
    if (s >= 66) return { code: 'PRESELL', label: '预售', action: '先挂预售 / 一件现货测水温，确认询盘再备货', tone: 'presell' };
    if (s >= 52) return { code: 'TEST', label: '轻测', action: '轻量测试：挂 1 件现货看流量与咨询', tone: 'test' };
    if (s >= 38) return { code: 'WATCH', label: '观察', action: '暂不行动，持续观察气候与市场信号', tone: 'watch' };
    return { code: 'SKIP', label: '放弃', action: '当前不建议进入，价差 / 风险 / 需求不匹配', tone: 'skip' };
  }

  function priceRange(p) {
    const eu = num(p.euPrice);
    if (eu <= 0) return null;
    // 现货 + 即时交付可接受适度溢价：基准价上浮 8% ~ 22%
    const lo = eu * 1.0, hi = eu * 1.15;
    return { lo: round9(lo), hi: round9(hi) };
  }

  // ---------- 可解释理由 ----------
  function drivers(p, sig, rk, margin, hasLocal) {
    const d = [];
    // 正向
    if (sig.C >= 70) d.push({ t: 'pos', k: '气候', v: `${p.city || '目标城市'} 未来将出现持续高温（${num(p.heatDays)}天 · 峰值 ${num(p.maxTemp)}℃），降温需求从舒适消费转为应急刚需` });
    if (num(p.nightTemp) >= 20) d.push({ t: 'pos', k: '夜温', v: `夜间温度 ${num(p.nightTemp)}℃ 降不下来（热带夜），睡眠场景强烈驱动空调 / 风扇购买` });
    if (margin >= 0.5) d.push({ t: 'pos', k: '价差', v: `毛利率约 ${Math.round(margin * 100)}%（欧洲售价 €${num(p.euPrice)} vs 中国采购 €${num(p.chinaCost)}），现货可接受适度溢价` });
    if (num(p.weightKg) <= 1.5 && p.demoEasy) d.push({ t: 'pos', k: '产品', v: `轻（${num(p.weightKg)}kg）、小、容易演示，符合"消费者一眼看懂"的启动逻辑${p.handCarry ? '，可人肉带货' : ''}` });
    if (p.retailStockout) d.push({ t: 'pos', k: '缺货', v: '欧洲电商 / 本地卖场已缺货或配送延迟，本地现货 + 即时交付出现价值窗口' });
    if (p.trendsRising || p.polymarketRising) d.push({ t: 'pos', k: '情绪', v: `市场正在提前交易高温预期（搜索热度${p.trendsRising ? '↑' : ''}${p.polymarketRising ? ' · 预测市场概率↑' : ''}），领先于缺货新闻` });
    if (hasLocal && num(p.inquiries) >= 4) d.push({ t: 'pos', k: '实盘', v: `Leboncoin 已收到 ${num(p.inquiries)} 条咨询${num(p.conversions) > 0 ? ` · 成交 ${num(p.conversions)} 单` : ''}，真实需求已出现` });
    if (p.b2bDemand) d.push({ t: 'pos', k: 'B端', v: '出现 B 端小批量需求，客单价与复购价值高于个人消费者' });

    // 负向 / 风险
    if (rk.comp >= 0.5) d.push({ t: 'neg', k: '合规', v: `电子 / 带电产品需关注 CE、WEEE、EPR、GPSR 等合规要求，${rk.comp >= 1 ? '风险较高，务必先确认' : '需要核实'}` });
    if (rk.ret >= 0.5) d.push({ t: 'neg', k: '退货', v: `退货 / 差评风险${rk.ret >= 1 ? '较高' : '中等'}，注意如实描述效果、避免夸大制冷能力` });
    if (num(p.weightKg) >= 8) d.push({ t: 'neg', k: '物流', v: `产品较重（${num(p.weightKg)}kg），运输成本高、不便人肉带货，影响补货速度` });
    if (margin > 0 && margin < 0.25) d.push({ t: 'neg', k: '价差', v: `毛利率仅约 ${Math.round(margin * 100)}%，难以覆盖运费与退货风险，信息差窗口不足` });
    if (p.restock === 'no') d.push({ t: 'neg', k: '补货', v: '补货来不及：即使需求爆发也无法及时交付，只适合清现有库存' });

    // 信息
    if (!hasLocal) d.push({ t: 'info', k: '待验证', v: '尚无本地实盘反馈——建议先挂 1 件现货，用真实询盘率验证再决定备货量' });
    if (num(p.acPenetration) === 'low' || p.acPenetration === 'low') d.push({ t: 'info', k: '区域', v: '目标区域空调渗透率低、老建筑多，移动 / 免安装产品打中真实居住环境' });

    return d;
  }

  // ---------- helpers ----------
  function num(v) { const n = parseFloat(v); return isFinite(n) ? n : 0; }
  function r0(n) { return Math.round(n); }
  function steps01(key, map) { return map[key] ?? 0; }
  function round9(v) { // 定价心理：靠近 x.90
    const base = Math.floor(v);
    return base + 0.90;
  }

  global.Scoring = { score, recommend, WEIGHTS, climateScore, marketScore, productScore, localScore };
})(window);
