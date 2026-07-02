/* ============================================================
   generator.js — AI 生成内容引擎
   一键生成：Leboncoin 法语标题/描述、买家咨询回复、
   预售说明、供应商询价话术、每周商机报告。

   说明：本引擎为「数据驱动的模板生成」，完全本地运行、
   零密钥、零依赖，可离线使用。函数签名与输出结构均为
   接入真实 LLM（Claude API 等）预留——只需把 build*()
   的返回替换为模型输出即可，UI 无需改动。
   ============================================================ */
(function (global) {
  'use strict';

  const S = () => global.Scoring;
  const cap = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  const eur = (n) => '€' + Number(n).toFixed(2).replace(/\.00$/, '');

  // 每类商品的法语卖点素材
  const FR = {
    ac: {
      hook: 'Climatiseur mobile prêt à l\'emploi — aucune installation, aucun perçage.',
      points: ['Rafraîchit une pièce en quelques minutes', 'Sans unité extérieure — idéal appartement & location', 'Se déplace de pièce en pièce sur roulettes', 'Livré avec kit fenêtre'],
      keywords: 'climatiseur mobile, clim mobile, sans installation, appartement',
    },
    desk: {
      hook: 'Mini climatiseur de bureau à refroidissement — un air frais rien que pour vous.',
      points: ['Refroidissement par semi-conducteur (Peltier), pas juste du vent', 'Compact, silencieux, USB', 'Parfait bureau, télétravail, chambre', 'Idéal quand le bureau n\'a pas de clim'],
      keywords: 'ventilateur bureau, refroidisseur d\'air, climatiseur usb, télétravail',
    },
    wearable: {
      hook: 'Rafraîchissement portable — gardez la tête froide partout.',
      points: ['Léger, se porte toute la journée', 'Idéal trajets, terrasse, sport, festival', 'Recharge rapide / réutilisable', 'Soulagement immédiat pendant la canicule'],
      keywords: 'ventilateur portable, tour de cou rafraîchissant, anti-chaleur',
    },
    accessory: {
      hook: 'L\'accessoire malin qui rend votre solution anti-chaleur vraiment efficace.',
      points: ['Installation en 2 minutes, sans outil', 'Compatible fenêtres standard', 'Améliore le rendement de votre climatiseur mobile', 'Petit prix, grand confort'],
      keywords: 'kit fenêtre climatiseur, calfeutrage, accessoire clim mobile',
    },
  };

  // ---------- Leboncoin 法语标题 ----------
  function leboncoinTitle(p) {
    const nameFr = p.nameFr || p.name;
    const city = p.city || '';
    const dispo = 'disponible immédiatement';
    // 现货 + 城市 是关键流量词
    return `${trim(nameFr, 46)} — ${cap(dispo)}${city ? ' · ' + city : ''}`;
  }

  // ---------- Leboncoin 法语描述 ----------
  function leboncoinDescription(p, res) {
    const f = FR[p.category] || FR.wearable;
    const price = res && res.priceRange ? res.priceRange : null;
    const priceLine = price ? `Prix : ${eur(price.lo)}${price.hi > price.lo ? ' – ' + eur(price.hi) : ''}` : (p.euPrice ? `Prix : ${eur(p.euPrice)}` : '');
    const lines = [
      `🌡️ ${f.hook}`,
      '',
      `✅ ${f.points.join('\n✅ ')}`,
      '',
      '📦 EN STOCK — remise en main propre le jour même ou expédition rapide.',
      p.handCarry ? '⚡ Pas besoin d\'attendre 2 semaines : disponible maintenant, près de chez vous.' : '',
      priceLine,
      '',
      p.category === 'ac' || p.category === 'wearable'
        ? 'ℹ️ Produit neuf, notice FR/EN incluse. Description honnête : idéal pour rafraîchir une pièce/un espace, ce n\'est pas une climatisation centrale.'
        : 'ℹ️ Produit neuf, notice FR/EN incluse.',
      `📍 ${p.city || 'Livraison locale'} — écrivez-moi pour la disponibilité.`,
    ].filter(Boolean);
    return lines.join('\n');
  }

  // ---------- 买家常见咨询回复 ----------
  function buyerReplies(p) {
    const f = FR[p.category] || FR.wearable;
    const price = p.euPrice ? eur(p.euPrice) : '—';
    return [
      { q: 'Toujours disponible ? / 还有货吗', a: `Bonjour, oui c'est toujours disponible et en stock. Remise en main propre possible dès aujourd'hui/demain à ${p.city || 'proximité'}. Souhaitez-vous réserver ?` },
      { q: 'Prix / dernier prix ? / 价格', a: `Le prix est de ${price}, produit neuf et disponible immédiatement. Vu la forte demande pendant la canicule, je garde ce tarif tant qu'il reste du stock.` },
      { q: 'Installation ? / 安装', a: p.category === 'ac'
        ? 'Aucune installation compliquée : c\'est un climatiseur mobile, il suffit de le brancher et de poser le kit fenêtre (fourni, 2 min). Parfait pour une location.'
        : 'Aucune installation : c\'est prêt à l\'emploi, il suffit de le brancher/charger. Notice FR incluse.' },
      { q: 'Bruit / conso ? / 噪音能耗', a: 'Utilisation normale, adapté à une chambre/un bureau. Je peux vous envoyer une petite vidéo de démonstration si vous voulez vous rendre compte avant de venir.' },
      { q: 'Livraison ? / 送货', a: `Remise en main propre à ${p.city || 'proximité'} (rapide), ou envoi possible à vos frais. Le gros avantage ici : pas d'attente de plusieurs semaines comme sur les sites en rupture.` },
    ];
  }

  // ---------- 预售说明 ----------
  function presellNote(p, res) {
    const price = res && res.priceRange ? res.priceRange : null;
    const pr = price ? `${eur(price.lo)}` : (p.euPrice ? eur(p.euPrice) : '—');
    return [
      `🔥 PRÉCOMMANDE — ${p.nameFr || p.name}`,
      '',
      `Vu la canicule annoncée à ${p.city || 'venir'}, je constitue un petit lot. Réservez le vôtre dès maintenant :`,
      `• Prix précommande : ${pr}`,
      '• Disponibilité estimée : quelques jours (stock local, pas d\'attente d\'import de 2-3 semaines)',
      '• Sans engagement : je vous confirme la dispo avant tout paiement',
      '',
      'Écrivez-moi « INTÉRESSÉ » + votre code postal, je reviens vers vous en priorité. 🙌',
    ].join('\n');
  }

  // ---------- 供应商询价话术（中文，用于 1688/淘宝/阿里国际站）----------
  function supplierInquiry(p) {
    return [
      `【询价】${p.name}`,
      '',
      '您好，我在欧洲（法国）做本地零售，想了解以下信息，方便的话请报价：',
      '1) 起订量 / 小批量（30–50 件）单价与阶梯价；',
      '2) 单件重量、包装体积（评估运费与是否可随身带货）；',
      p.category === 'ac' || p.category === 'wearable' || p.category === 'desk'
        ? '3) 是否带电池 / 电源，是否可提供 CE、WEEE、EPR、GPSR 等合规资料与说明书（欧盟销售必需）；'
        : '3) 是否需要任何合规认证、能否提供说明书；',
      '4) 现货库存与发货时效，是否支持贴牌 / 无 logo 中性包装；',
      '5) 是否可提供 1–2 张实拍图 / 演示视频用于本地推广。',
      '',
      '我们先做小批量验证，跑通后会稳定返单，谢谢！',
    ].join('\n');
  }

  // ---------- 单品机会分析（中文，可执行）----------
  function opportunityBrief(p, res) {
    const r = res.rec;
    const drivers = res.drivers.filter(d => d.t === 'pos').slice(0, 3).map(d => `· ${d.v}`).join('\n');
    const risks = res.drivers.filter(d => d.t === 'neg').map(d => `· ${d.v}`).join('\n') || '· 暂无显著风险，但仍建议小批量验证';
    const pr = res.priceRange ? `${eur(res.priceRange.lo)} – ${eur(res.priceRange.hi)}` : '—';
    return [
      `商品：${p.name}｜城市：${p.city}`,
      `机会分：${res.total}/100　建议：${r.code}（${r.label}）`,
      '',
      '为什么现在：',
      drivers || '· 信号偏弱，暂不构成明确机会',
      '',
      '风险提示：',
      risks,
      '',
      `建议动作：${r.action}`,
      `推荐售价：${pr}（现货 + 即时交付可接受适度溢价）`,
      res.margin ? `毛利率参考：约 ${res.margin}%` : '',
    ].filter(Boolean).join('\n');
  }

  // ---------- 每周商机报告 ----------
  function weeklyReport(products, scored, market, dateStr) {
    const ranked = products
      .map((p, i) => ({ p, r: scored[i] }))
      .sort((a, b) => b.r.total - a.r.total);

    const buy = ranked.filter(x => x.r.rec.code === 'BUY');
    const presell = ranked.filter(x => x.r.rec.code === 'PRESELL');
    const watch = ranked.filter(x => ['WATCH', 'SKIP'].includes(x.r.rec.code));
    const avg = Math.round(ranked.reduce((s, x) => s + x.r.total, 0) / (ranked.length || 1));
    const hotCity = topCity(products);

    return {
      title: '气候商机雷达 · 每周简报',
      date: dateStr,
      summary: `本周共评估 ${products.length} 款商品，平均机会分 ${avg}/100。气候信号集中在 ${hotCity}——高温 + 低空调渗透率 + 缺货新闻叠加，降温需求正从"舒适消费"转为"应急刚需"。市场情绪已提前交易高温预期（搜索热度与预测市场概率上升），领先于缺货新闻，是切入窗口。`,
      climate: market && market.news ? market.news : '',
      buy, presell, watch, avg, hotCity,
      actions: [
        buy.length ? `优先备货：${buy.slice(0, 3).map(x => x.p.name).join('、')}——小批量 30–50 件，本地现货 + 即时交付。` : '本周暂无 BUY 级机会，保持观察。',
        presell.length ? `预售验证：${presell.slice(0, 3).map(x => x.p.name).join('、')}——先挂现货 / 预售，用真实询盘率决定备货量。` : '',
        '组合提价：把高毛利配件（密封布、转接头、颈圈）打包进场景解决方案，提高客单价、减少消费者找配件的麻烦。',
        '风险控制：带电产品先确认 CE/WEEE/EPR/GPSR 合规；如实描述效果，避免夸大制冷、降低退货与差评。',
      ].filter(Boolean),
    };
  }

  function weeklyReportText(rep) {
    const line = (x) => `  ${x.r.total}/100 · ${x.r.rec.code} — ${x.p.name}（${x.p.city}）`;
    return [
      `# ${rep.title}　${rep.date}`,
      '',
      '## 本周判断',
      rep.summary,
      '',
      rep.climate ? '## 市场信号\n' + rep.climate + '\n' : '',
      '## 建议备货（BUY）',
      rep.buy.length ? rep.buy.map(line).join('\n') : '  （无）',
      '',
      '## 建议预售 / 测试（PRESELL）',
      rep.presell.length ? rep.presell.map(line).join('\n') : '  （无）',
      '',
      '## 本周行动',
      rep.actions.map((a, i) => `  ${i + 1}. ${a}`).join('\n'),
      '',
      '— 本报告由气候商机雷达自动生成，所有建议均围绕「低成本验证 · 小批量补货 · 快速复盘」。',
    ].filter(Boolean).join('\n');
  }

  // ---------- helpers ----------
  function topCity(products) {
    const m = {};
    products.forEach(p => { m[p.city] = (m[p.city] || 0) + Number(p.maxTemp || 0); });
    return Object.entries(m).sort((a, b) => b[1] - a[1])[0]?.[0] || 'Paris';
  }
  function trim(s, n) { return s.length > n ? s.slice(0, n - 1).trim() + '…' : s; }

  global.Generator = {
    leboncoinTitle, leboncoinDescription, buyerReplies, presellNote,
    supplierInquiry, opportunityBrief, weeklyReport, weeklyReportText,
  };
})(window);
