/* ============================================================
   app.js — UI / 路由 / 交互
   ============================================================ */
(function () {
  'use strict';
  const { Scoring, Store, Generator } = window;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const app = $('#app');

  let state = Store.load();
  let view = 'radar';
  let selectedId = null;
  let filter = { rec: 'all', q: '' };

  // ---------- score cache ----------
  const scoreOf = (p) => Scoring.score(p);
  const allScored = () => state.products.map(scoreOf);

  // ---------- SVG helpers ----------
  const TONE = { BUY: '#16d99b', PRESELL: '#22d3ee', TEST: '#f5b544', WATCH: '#8aa0c4', SKIP: '#f4517a' };
  function ring(score, size = 58, stroke = 5) {
    const r = (size - stroke) / 2, c = 2 * Math.PI * r, off = c * (1 - score / 100);
    const col = score >= 80 ? TONE.BUY : score >= 66 ? TONE.PRESELL : score >= 52 ? TONE.TEST : score >= 38 ? TONE.WATCH : TONE.SKIP;
    return `<div class="ring" style="width:${size}px;height:${size}px">
      <svg width="${size}" height="${size}">
        <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#12203a" stroke-width="${stroke}"/>
        <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${col}" stroke-width="${stroke}"
          stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${off}" style="filter:drop-shadow(0 0 5px ${col}aa)"/>
      </svg>
      <div class="num" style="color:${col};text-shadow:0 0 16px ${col}66">${score}<small>/100</small></div>
    </div>`;
  }
  function radarChart(sig, size = 200) {
    const axes = [
      { k: 'C', label: '气候', color: '#ff6b4a' },
      { k: 'M', label: '市场', color: '#a78bfa' },
      { k: 'P', label: '商品', color: '#22d3ee' },
      { k: 'L', label: '实盘', color: '#3b82f6' },
    ];
    const cx = size / 2, cy = size / 2, R = size / 2 - 30;
    const pt = (i, frac) => {
      const a = -Math.PI / 2 + i * (2 * Math.PI / axes.length);
      return [cx + Math.cos(a) * R * frac, cy + Math.sin(a) * R * frac];
    };
    let grid = '';
    [0.25, 0.5, 0.75, 1].forEach(f => {
      const p = axes.map((_, i) => pt(i, f).join(',')).join(' ');
      grid += `<polygon points="${p}" fill="none" stroke="#1c2c48" stroke-width="1"/>`;
    });
    let spokes = '', labels = '';
    axes.forEach((ax, i) => {
      const [x, y] = pt(i, 1);
      spokes += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#1c2c48" stroke-width="1"/>`;
      const [lx, ly] = pt(i, 1.22);
      const val = sig[ax.k] === null ? '—' : sig[ax.k];
      labels += `<text x="${lx}" y="${ly}" fill="${ax.color}" font-size="11" font-weight="700" text-anchor="middle" dominant-baseline="middle">${ax.label} ${val}</text>`;
    });
    const poly = axes.map((ax, i) => pt(i, (sig[ax.k] ?? 0) / 100).join(',')).join(' ');
    let dots = axes.map((ax, i) => { const [x, y] = pt(i, (sig[ax.k] ?? 0) / 100); return `<circle cx="${x}" cy="${y}" r="3" fill="${ax.color}"/>`; }).join('');
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      ${grid}${spokes}
      <polygon points="${poly}" fill="rgba(56,224,255,.16)" stroke="#5cf0ff" stroke-width="2" style="filter:drop-shadow(0 0 6px rgba(56,224,255,.55))"/>
      ${dots}${labels}
    </svg>`;
  }
  function sigMini(sig) {
    const rows = [['C', '气候'], ['M', '市场'], ['P', '商品'], ['L', '实盘']];
    return `<div class="sig-mini">` + rows.map(([k, l]) => {
      const v = sig[k];
      const w = v === null ? 0 : v;
      return `<div class="row"><span class="lbl">${l}</span><span class="track"><i class="fill-${k}" style="width:${w}%"></i></span><span class="val">${v === null ? '—' : v}</span></div>`;
    }).join('') + `</div>`;
  }
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------- 买家反馈（本地实盘定性字段）----------
  function buyerFeedbackHTML(p) {
    const chips = (p.concerns || '').split(/[,，、]/).map(c => c.trim()).filter(Boolean);
    const has = (p.topQuestion && p.topQuestion.trim()) || (p.noBuyReason && p.noBuyReason.trim()) || (+p.acceptPrice > 0) || chips.length;
    if (!has) return '';
    const priceCmp = (+p.acceptPrice > 0 && +p.euPrice > 0)
      ? (+p.acceptPrice < +p.euPrice * 0.9 ? '<span style="color:var(--test)"> · 低于售价，价格敏感</span>'
        : (+p.acceptPrice >= +p.euPrice ? '<span style="color:var(--buy)"> · ≥售价，有溢价空间</span>' : ''))
      : '';
    return `<div class="panel" style="margin-top:16px">
      <div class="eyebrow" style="margin-bottom:12px">买家反馈 · 本地实盘</div>
      <div class="fb-list">
        ${p.topQuestion && p.topQuestion.trim() ? `<div class="fb"><span class="fk">最常问</span><span class="fv">${esc(p.topQuestion)}</span></div>` : ''}
        ${p.noBuyReason && p.noBuyReason.trim() ? `<div class="fb"><span class="fk">未成交原因</span><span class="fv">${esc(p.noBuyReason)}</span></div>` : ''}
        ${+p.acceptPrice > 0 ? `<div class="fb"><span class="fk">可接受价</span><span class="fv">€${esc(p.acceptPrice)}${+p.euPrice > 0 ? ` <span style="color:var(--muted)">/ 售价 €${esc(p.euPrice)}</span>` : ''}${priceCmp}</span></div>` : ''}
        ${chips.length ? `<div class="fb"><span class="fk">关心项</span><span class="fv">${chips.map(c => `<span class="chip">${esc(c)}</span>`).join(' ')}</span></div>` : ''}
      </div>
    </div>`;
  }

  // ---------- 社媒 & 新闻舆情面板 ----------
  const BUZZ_THEME = {
    heat: { label: '高温', color: '#ff6a45' },
    shortage: { label: '缺货', color: '#ff5b86' },
    ac_rush: { label: '抢购', color: '#ffbe4d' },
    office_heat: { label: '办公过热', color: '#9d74ff' },
    buying_need: { label: '求购', color: '#38e0ff' },
  };
  const BUZZ_SRC = { reddit: 'Reddit', googlenews: 'Google News', x: 'X', instagram: 'Instagram' };
  function buzzPanelHTML(buzz) {
    if (!buzz) return '';
    const themes = buzz.themes || {};
    const items = buzz.items || [];
    const sources = buzz.sources || {};
    const max = Math.max(1, ...Object.keys(BUZZ_THEME).map(k => themes[k] || 0));
    const bars = Object.keys(BUZZ_THEME).map(k => {
      const c = BUZZ_THEME[k], v = themes[k] || 0;
      return `<div class="row"><span class="lbl" style="width:64px">${c.label}</span><span class="track"><i style="width:${Math.round(v / max * 100)}%;background:${c.color}"></i></span><span class="val">${v}</span></div>`;
    }).join('');
    const chips = Object.keys(BUZZ_SRC).map(k => {
      const st = sources[k] || 'disabled';
      const on = st === 'ok';
      const col = on ? 'var(--buy)' : (st === 'disabled' ? 'var(--dim)' : 'var(--test)');
      const txt = on ? '✓' : (st === 'disabled' ? '需API' : st);
      return `<span class="chip" style="color:${col};border-color:${col}44">${BUZZ_SRC[k]} ${txt}</span>`;
    }).join('');
    const rows = items.slice(0, 6).map(it => {
      const c = BUZZ_THEME[it.theme] || { label: it.theme, color: 'var(--muted)' };
      const origin = it.origin || (it.source === 'reddit' ? 'Reddit' : it.source || '');
      return `<a class="buzz-item" href="${esc(it.url || '#')}" target="_blank" rel="noopener">
        <span class="bt" style="color:${c.color};border-color:${c.color}55">${c.label}</span>
        <span class="btitle">${esc(it.title)}</span>
        <span class="bsrc">${esc(origin)}${it.ts ? ' · ' + esc(it.ts) : ''}</span>
      </a>`;
    }).join('');
    return `<div class="panel" style="margin-top:16px">
      <div class="section-head" style="margin-bottom:12px">
        <div style="display:flex;align-items:center;gap:10px"><span class="badge-ai" style="color:var(--cyan2)">◎ 舆情</span><b style="font-size:15px">社媒 & 新闻雷达</b></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">${chips}</div>
      </div>
      <div class="decision-grid" style="gap:16px">
        <div>
          <div class="eyebrow" style="margin-bottom:10px">话题热度 · 提及量</div>
          <div class="sig-mini">${bars}</div>
          <div class="data-note">Reddit 公共 JSON + Google News RSS 定时抓取；X/Instagram 无 keyless 接口，需自备 key</div>
        </div>
        <div>
          <div class="eyebrow" style="margin-bottom:10px">最新舆情 · ${buzz.total || items.length} 条</div>
          <div class="buzz-list">${rows || '<div class="data-note">暂无舆情条目</div>'}</div>
        </div>
      </div>
    </div>`;
  }

  // ============================================================
  //  ROUTER
  // ============================================================
  function render() {
    $$('#nav button').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    if (view === 'radar') renderRadar();
    else if (view === 'products') renderProducts();
    else if (view === 'signals') renderSignals();
    else if (view === 'bundles') renderBundles();
    else if (view === 'report') renderReport();
    else if (view === 'decision') renderDecision();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function go(v) { view = v; render(); }

  // ============================================================
  //  VIEW: RADAR (overview)
  // ============================================================
  function renderRadar() {
    const scored = allScored();
    const paired = state.products.map((p, i) => ({ p, r: scored[i] })).sort((a, b) => b.r.total - a.r.total);
    const buy = paired.filter(x => x.r.rec.code === 'BUY').length;
    const presell = paired.filter(x => x.r.rec.code === 'PRESELL').length;
    const avg = Math.round(paired.reduce((s, x) => s + x.r.total, 0) / (paired.length || 1));
    const hotCity = state.cities.slice().sort((a, b) => b.maxTemp - a.maxTemp)[0];

    // filtered list
    let list = paired;
    if (filter.rec !== 'all') list = list.filter(x => x.r.rec.code === filter.rec);
    if (filter.q) { const q = filter.q.toLowerCase(); list = list.filter(x => (x.p.name + x.p.nameFr + x.p.city).toLowerCase().includes(q)); }

    app.innerHTML = `
    <section class="view">
      <div class="hero">
        <div class="hero-grid">
          <div>
            <div class="eyebrow"><span class="live" id="livePill"><span class="dot"></span><span class="lt">LIVE</span></span> · AI 辅助商品挖掘与交易决策</div>
            <h1>把<span class="g">天气 · 市场 · 供应链</span>放进同一张决策表</h1>
            <p>系统不预测天气，而是把气候概率、市场情绪、消费者行为、电商库存与中国供应链能力放在一起，输出「备货 / 预售 / 轻测 / 观察 / 放弃」的可执行判断。第一阶段聚焦欧洲高温降温商机。</p>
            <div class="hero-stats">
              <div class="hstat"><div class="k">评估商品</div><div class="v cy">${state.products.length}</div></div>
              <div class="hstat"><div class="k">建议备货</div><div class="v buy">${buy}</div></div>
              <div class="hstat"><div class="k">平均机会分</div><div class="v">${avg}</div></div>
              <div class="hstat"><div class="k">最热城市</div><div class="v hot" style="font-size:16px">${hotCity.zh} ${hotCity.maxTemp}℃</div></div>
            </div>
          </div>
          <div class="panel glass">
            <div class="eyebrow" style="margin-bottom:12px">城市气候信号 · 未来 14 天专业预报峰值</div>
            <div class="thermo">
              ${state.cities.map(c => {
                const w = Math.max(6, Math.min(100, (c.maxTemp - 24) / (42 - 24) * 100));
                const ac = { none: '#3b82f6', yellow: '#f5b544', orange: '#ff6b4a', red: '#f4517a' }[c.alert];
                const hw = c.heatwave && c.heatwave.sustained ? `<span title="持续高温 ${c.heatwave.days} 天" style="font-size:11px">🔥</span>` : '';
                return `<div class="city-row"><span class="name">${c.zh}</span><span class="bar"><i style="width:${w}%"></i></span>${hw}<span class="temp">${c.maxTemp}℃</span><span class="alert" title="${c.alert}" style="background:${ac};box-shadow:0 0 8px ${ac}"></span></div>`;
              }).join('')}
            </div>
            <div class="data-note">🔥 = 未来 7–14 天持续高温信号 · 🌙 圆点 = 预警等级 · 「信号台」可同步/编辑</div>
          </div>
        </div>
      </div>

      <div class="toolbar">
        <div class="seg" id="recFilter">
          ${['all', 'BUY', 'PRESELL', 'TEST', 'WATCH', 'SKIP'].map(k => `<button data-rec="${k}" class="${filter.rec === k ? 'active' : ''}">${k === 'all' ? '全部' : k}</button>`).join('')}
        </div>
        <div class="search">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>
          <input id="searchInput" placeholder="搜索商品 / 城市…" value="${esc(filter.q)}" />
        </div>
      </div>

      <div class="grid-cards" id="cards">
        ${list.length ? list.map(x => oppCard(x.p, x.r)).join('') : emptyCards()}
      </div>
    </section>`;

    $('#recFilter').onclick = e => { const b = e.target.closest('[data-rec]'); if (b) { filter.rec = b.dataset.rec; renderRadar(); } };
    const si = $('#searchInput'); si.oninput = () => { filter.q = si.value; const cards = $('#cards'); let l = paired; if (filter.rec !== 'all') l = l.filter(x => x.r.rec.code === filter.rec); if (filter.q) { const q = filter.q.toLowerCase(); l = l.filter(x => (x.p.name + x.p.nameFr + x.p.city).toLowerCase().includes(q)); } cards.innerHTML = l.length ? l.map(x => oppCard(x.p, x.r)).join('') : emptyCards(); bindCards(); };
    bindCards();
  }

  function oppCard(p, r) {
    const topDriver = r.drivers.find(d => d.t === 'pos') || r.drivers[0];
    const chips = [];
    if (p.retailStockout) chips.push('<span class="chip hot">🔥 缺货窗口</span>');
    if (r.risk.comp >= 1) chips.push('<span class="chip warn">⚠ 合规</span>');
    else if (r.risk.comp >= 0.5) chips.push('<span class="chip risk">合规待核</span>');
    if (p.b2bDemand) chips.push('<span class="chip">B端</span>');
    return `<article class="opp" data-id="${p.id}">
      <div class="top">
        <div class="emoji">${p.emoji}</div>
        <div class="h">
          <b>${esc(p.name)}</b>
          <div class="cat">${esc(p.cat)} · <span class="city">📍${esc(p.city)}</span></div>
        </div>
        ${ring(r.total, 58)}
      </div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span class="rec-badge rec-${r.rec.code}">${r.rec.code} · ${r.rec.label}</span>
        ${chips.join('')}
      </div>
      ${sigMini(r.signals)}
      <div class="foot">
        <span class="margin">毛利率 <b>${r.margin}%</b></span>
        <button class="btn sm ghost" data-open="${p.id}">决策卡 →</button>
      </div>
    </article>`;
  }
  function emptyCards() {
    return `<div class="empty" style="grid-column:1/-1"><div class="e-ico">🛰️</div><h3>没有匹配的商品</h3><p>调整筛选，或录入一个新商品开始评估。</p><button class="btn primary" onclick="document.getElementById('addBtn').click()">＋ 录入商品</button></div>`;
  }
  function bindCards() {
    $$('.opp').forEach(el => el.onclick = e => { const btn = e.target.closest('[data-open]'); selectedId = el.dataset.id; go('decision'); });
  }

  // ============================================================
  //  VIEW: PRODUCTS
  // ============================================================
  function renderProducts() {
    const scored = allScored();
    const paired = state.products.map((p, i) => ({ p, r: scored[i] })).sort((a, b) => b.r.total - a.r.total);
    app.innerHTML = `
    <section class="view">
      <div class="section-head">
        <div><div class="eyebrow">商品库</div><h2>商品与信号录入</h2><p>手动录入四类信号，系统实时计算机会分。先支持手动，再逐步接入爬虫与 API 自动化。</p></div>
        <button class="btn primary" onclick="document.getElementById('addBtn').click()">＋ 录入商品</button>
      </div>
      <div class="grid-cards">
        ${paired.map(x => `
          <article class="opp">
            <div class="top">
              <div class="emoji">${x.p.emoji}</div>
              <div class="h"><b>${esc(x.p.name)}</b><div class="cat">${esc(x.p.cat)} · <span class="city">📍${esc(x.p.city)}</span></div></div>
              ${ring(x.r.total, 54)}
            </div>
            <span class="rec-badge rec-${x.r.rec.code}">${x.r.rec.code} · ${x.r.rec.label}</span>
            ${sigMini(x.r.signals)}
            <div class="foot">
              <div style="display:flex;gap:6px">
                <button class="btn sm" data-edit="${x.p.id}">编辑</button>
                <button class="btn sm" data-decision="${x.p.id}">决策卡</button>
              </div>
              <button class="btn sm danger" data-del="${x.p.id}">删除</button>
            </div>
          </article>`).join('')}
      </div>
    </section>`;
    $$('[data-edit]').forEach(b => b.onclick = () => openForm(b.dataset.edit));
    $$('[data-decision]').forEach(b => b.onclick = () => { selectedId = b.dataset.decision; go('decision'); });
    $$('[data-del]').forEach(b => b.onclick = () => {
      const p = state.products.find(x => x.id === b.dataset.del);
      if (confirm(`删除「${p.name}」？此操作不可撤销。`)) { state.products = state.products.filter(x => x.id !== b.dataset.del); Store.save(state); toast('已删除'); renderProducts(); }
    });
  }

  // ============================================================
  //  VIEW: DECISION CARD
  // ============================================================
  function renderDecision() {
    const p = state.products.find(x => x.id === selectedId) || state.products[0];
    if (!p) { go('radar'); return; }
    const r = scoreOf(p);
    const pr = r.priceRange;
    const relatedBundle = state.bundles.find(b => b.category === p.category);

    app.innerHTML = `
    <section class="view">
      <div class="toolbar" style="justify-content:space-between">
        <button class="btn ghost" onclick="history.length>1?history.back():null" id="backBtn">← 返回雷达</button>
        <div style="display:flex;gap:8px">
          <button class="btn" data-edit="${p.id}">编辑信号</button>
          <button class="btn primary" id="copyBrief">复制机会分析</button>
        </div>
      </div>

      <div class="decision-grid">
        <div>
          <div class="panel">
            <div class="dc-head">
              <div class="emoji" style="width:60px;height:60px;font-size:30px;border-radius:14px;display:grid;place-items:center;background:radial-gradient(circle at 30% 25%,#1a2942,#0d1626);border:1px solid var(--border)">${p.emoji}</div>
              <div><h2>${esc(p.name)}</h2><div class="sub">${esc(p.nameFr || '')}</div><div class="sub">${esc(p.cat)} · 📍${esc(p.city)}</div></div>
            </div>
            <div class="score-big">
              ${ring(r.total, 96, 8)}
              <div class="meta">
                <span class="rec-badge rec-${r.rec.code}">${r.rec.code} · ${r.rec.label}</span>
                <p>${esc(r.rec.action)}</p>
              </div>
            </div>
            <div class="kv-grid">
              <div class="kv-box"><div class="k">推荐售价（现货可溢价）</div><div class="v">${pr ? '€' + pr.lo + ' – €' + pr.hi : '—'}</div></div>
              <div class="kv-box"><div class="k">毛利率 · 采购成本</div><div class="v">${r.margin}% <span style="font-size:12px;color:var(--muted)">· €${p.chinaCost}</span></div></div>
              <div class="kv-box"><div class="k">合规风险</div><div class="v sm" style="color:${riskColor(r.risk.comp)}">${riskLabel(p.complianceRisk)}</div></div>
              <div class="kv-box"><div class="k">退货风险</div><div class="v sm" style="color:${riskColor(r.risk.ret)}">${riskLabel(p.returnRisk)}</div></div>
            </div>
          </div>

          <div class="panel" style="margin-top:16px">
            <div class="eyebrow" style="margin-bottom:12px">决策理由 · 每一分都可解释</div>
            <ul class="reason-list">
              ${r.drivers.map(d => `<li class="${d.t}">${driverIcon(d.t)}<span><b style="color:var(--text)">[${d.k}]</b> ${esc(d.v)}</span></li>`).join('')}
            </ul>
          </div>
        </div>

        <div>
          <div class="panel">
            <div class="eyebrow" style="margin-bottom:4px">信号雷达 · 四维分解</div>
            <div class="radar-wrap">${radarChart(r.signals, 210)}</div>
            <div class="radar-legend">
              ${[['C', '气候信号', '#ff6b4a'], ['M', '市场情绪', '#a78bfa'], ['P', '商品渠道', '#22d3ee'], ['L', '本地实盘', '#3b82f6']].map(([k, n, c]) =>
                `<div class="li"><span class="sw" style="background:${c}"></span><span class="n">${n}</span><span class="v" style="color:${c}">${r.signals[k] === null ? '待录入' : r.signals[k]}</span></div>`).join('')}
            </div>
          </div>

          ${relatedBundle ? `<div class="panel" style="margin-top:16px">
            <div class="eyebrow" style="margin-bottom:10px">推荐组合 · 场景化提价</div>
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px"><span style="font-size:22px">${relatedBundle.emoji}</span><b>${esc(relatedBundle.name)}</b></div>
            <div style="font-size:12.5px;color:var(--muted)">${esc(relatedBundle.desc)}</div>
            <button class="btn sm" style="margin-top:12px" onclick="__go('bundles')">查看组合包 →</button>
          </div>` : ''}
          ${buyerFeedbackHTML(p)}
        </div>
      </div>

      <div class="panel" style="margin-top:16px">
        <div class="section-head" style="margin-bottom:14px">
          <div style="display:flex;align-items:center;gap:10px"><span class="badge-ai">✦ AI 生成</span><b style="font-size:15px">一键生成销售内容</b></div>
        </div>
        <div class="tabs" id="genTabs">
          <button class="active" data-gen="leboncoin">Leboncoin 法语文案</button>
          <button data-gen="replies">买家咨询回复</button>
          <button data-gen="presell">预售说明</button>
          <button data-gen="supplier">供应商询价</button>
        </div>
        <div id="genBody"></div>
      </div>
    </section>`;

    $('#backBtn').onclick = () => go('radar');
    $$('[data-edit]').forEach(b => b.onclick = () => openForm(p.id));
    $('#copyBrief').onclick = () => copy(Generator.opportunityBrief(p, r), '#copyBrief');
    const tabs = $('#genTabs');
    tabs.onclick = e => { const b = e.target.closest('[data-gen]'); if (!b) return; $$('#genTabs button').forEach(x => x.classList.remove('active')); b.classList.add('active'); renderGen(b.dataset.gen, p, r); };
    renderGen('leboncoin', p, r);
  }

  function renderGen(kind, p, r) {
    const body = $('#genBody');
    let html = '';
    if (kind === 'leboncoin') {
      const title = Generator.leboncoinTitle(p);
      const desc = Generator.leboncoinDescription(p, r);
      html = genBlock('标题 Titre', title, 'FR', true) + genBlock('商品描述 Description', desc, 'FR');
    } else if (kind === 'replies') {
      html = Generator.buyerReplies(p).map(x => genBlock(x.q, x.a, 'FR')).join('');
    } else if (kind === 'presell') {
      html = genBlock('预售帖 Précommande', Generator.presellNote(p, r), 'FR');
    } else if (kind === 'supplier') {
      html = genBlock('1688 / 阿里国际站询价话术', Generator.supplierInquiry(p), 'CN');
    }
    body.innerHTML = html;
    $$('.copy-btn', body).forEach(b => b.onclick = () => copy(b.dataset.copy, b, true));
  }
  function genBlock(label, text, lang, isTitle) {
    return `<div class="gen-block">
      <div class="gh"><b>${esc(label)} <span class="fr">${lang}</span></b>
        <button class="copy-btn" data-copy="${esc(text)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>复制</button>
      </div>
      <pre class="${isTitle ? 'title-txt' : ''}">${esc(text)}</pre>
    </div>`;
  }

  // ============================================================
  //  VIEW: SIGNALS BOARD
  // ============================================================
  function renderSignals() {
    const m = state.market;
    app.innerHTML = `
    <section class="view">
      <div class="section-head"><div><div class="eyebrow">信号台</div><h2>气候 · 市场情绪信号</h2><p>天气为 Open-Meteo 未来 14 天专业预报（ECMWF/GFS 等模型），含热浪信号——判断未来 7–14 天是否可能出现持续高温；叠加预测市场、搜索热度与缺货新闻，判断市场是否正在提前交易高温预期。</p></div>
      <div style="display:flex;gap:8px"><button class="btn" id="refreshLive">↻ 同步实时信号</button><button class="btn" id="editCities">编辑城市气候</button></div></div>

      <div class="sig-board" style="margin-bottom:20px">
        ${state.cities.map(c => {
          const s = Scoring.climateScore({ maxTemp: c.maxTemp, heatDays: c.heatDays, nightTemp: c.nightTemp, alert: c.alert, acPenetration: c.acPenetration });
          const ac = { none: '—', yellow: '🟡 黄色', orange: '🟠 橙色', red: '🔴 红色' }[c.alert];
          const hw = c.heatwave || {};
          const hwOn = !!hw.sustained;
          const hwColor = hwOn ? (hw.days >= 5 ? '#ff6b4a' : '#ffb347') : '#8aa0c4';
          const hwText = hwOn
            ? `⚠ 是 · 连续 ${hw.days} 天${hw.startsIn > 0 ? `（第 ${hw.startsIn + 1} 天起）` : '（本周内）'}`
            : '暂无持续信号';
          return `<div class="sig-card">
            <div class="sh"><b>${c.zh} · ${c.name}</b><span class="tag">气候分 ${Math.round(s)}</span></div>
            <div class="metric"><span class="m-lbl">日间峰值</span><span class="m-val" style="color:${c.maxTemp >= 38 ? '#ff6b4a' : c.maxTemp >= 35 ? '#ffb347' : '#22d3ee'}">${c.maxTemp}℃${c.feelsLike ? ` <span style="color:var(--muted);font-size:11px">体感 ${c.feelsLike}°</span>` : ''}</span></div>
            <div class="metric"><span class="m-lbl">夜间低温</span><span class="m-val" style="color:${c.nightTemp >= 20 ? '#ff6b4a' : '#8aa0c4'}">${c.nightTemp}℃ ${c.nightTemp >= 20 ? '热带夜' : ''}</span></div>
            <div class="metric" style="border-bottom:1px solid ${hwOn ? 'rgba(255,107,74,.25)' : 'var(--border)'}"><span class="m-lbl" style="white-space:nowrap">7–14天持续高温</span><span class="m-val" style="color:${hwColor};text-align:right">${hwText}</span></div>
            <div class="metric"><span class="m-lbl">连续高温 · 热带夜</span><span class="m-val">${c.heatDays} 天 · ${hw.tropicalNights != null ? hw.tropicalNights : (c.nightTemp >= 20 ? '≥1' : 0)} 夜</span></div>
            <div class="metric"><span class="m-lbl">热浪预警等级</span><span class="m-val">${ac}</span></div>
            <div class="metric"><span class="m-lbl">空调渗透率</span><span class="m-val">${{ low: '低', mid: '中', high: '高' }[c.acPenetration]}</span></div>
          </div>`;
        }).join('')}
      </div>

      <div class="decision-grid">
        <div class="panel">
          <div class="eyebrow" style="margin-bottom:12px">🎲 预测市场 · Polymarket</div>
          <div class="sig-card" style="border:none;padding:0;background:none">
            <div class="metric"><span class="m-lbl">巴黎极端高温概率</span><span class="m-val trend-up">${m.polymarket.paris}% ${m.polymarket.rising ? '↑' : ''}</span></div>
            <div class="metric"><span class="m-lbl">伦敦极端高温概率</span><span class="m-val trend-up">${m.polymarket.london}% ${m.polymarket.rising ? '↑' : ''}</span></div>
          </div>
          <div class="data-note" style="margin-top:10px">${esc(m.polymarket.note)}</div>
        </div>
        <div class="panel">
          <div class="eyebrow" style="margin-bottom:12px">🔍 Google Trends · 搜索热度</div>
          <div class="sig-card" style="border:none;padding:0;background:none">
            ${m.trends.map(t => `<div class="metric"><span class="m-lbl">${esc(t.kw)}</span><span class="m-val ${t.rising ? 'trend-up' : 'trend-flat'}">${t.idx} ${t.rising ? '↑' : ''}</span></div>`).join('')}
          </div>
        </div>
      </div>
      <div class="panel" style="margin-top:16px">
        <div class="eyebrow" style="margin-bottom:8px">📰 缺货 / 销量新闻</div>
        <p style="margin:0;font-size:13.5px;color:var(--text)">${esc(m.news)}</p>
        <div class="data-note">更新时间 ${esc(m.updated)}</div>
      </div>
      ${buzzPanelHTML(m.buzz)}
      <div class="disclaimer"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0z"/></svg>
        <span>天气为 Open-Meteo 专业模型 14 天预报；「热浪预警」由该预报按 canicule 规则（连续 ≥3 天 ≥32℃）推导，非官方 Vigilance/MeteoSwiss 通报——后者可后续经数据管道叠加。其余信号含人工录入 / 示例值，仅辅助判断、不构成保证；真实决策请以最新预报、平台库存与本地实盘为准，不鼓励盲目囤货。</span></div>
    </section>`;
    $('#editCities').onclick = () => openCityEditor();
    const rl = $('#refreshLive'); if (rl) rl.onclick = () => refreshLive(true);
  }

  // ============================================================
  //  VIEW: BUNDLES
  // ============================================================
  function renderBundles() {
    app.innerHTML = `
    <section class="view">
      <div class="section-head"><div><div class="eyebrow">组合包 · 场景化解决方案</div><h2>不卖单品，卖场景解决方案</h2><p>单品难以竞争过平台与品牌方。按真实居住 / 办公 / 户外场景组合，提高客单价，也减少消费者自己找配件的麻烦。含中文品牌的安装、使用与法/英语手册。</p></div></div>
      <div class="grid-cards">
        ${state.bundles.map(b => {
          const save = Math.round((1 - b.bundlePrice / b.sumPrice) * 100);
          return `<div class="bundle">
            <div class="bh"><div class="emoji">${b.emoji}</div><div><b>${esc(b.name)}</b><div class="fr">${esc(b.nameFr)}</div></div></div>
            <div style="font-size:12.5px;color:var(--muted)">${esc(b.desc)}</div>
            <ul class="items">${b.items.map(it => `<li><span class="e">•</span>${esc(it)}</li>`).join('')}</ul>
            <div class="bfoot">
              <div class="price"><span class="was">单买 €${b.sumPrice.toFixed(2).replace(/\.00$/, '')}</span><span class="now">€${b.bundlePrice.toFixed(2).replace(/\.00$/, '')}</span></div>
              <span class="save">省 ${save}%</span>
            </div>
          </div>`;
        }).join('')}
      </div>
      <div class="disclaimer"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/></svg>
        <span>比个人消费者更值得研究的，是小型 B 端需求（办公室、民宿、养老 / 宠物机构）。组合包同样适合作为 B 端小批量报价的起点。</span></div>
    </section>`;
  }

  // ============================================================
  //  VIEW: WEEKLY REPORT
  // ============================================================
  function renderReport() {
    const scored = allScored();
    const rep = Generator.weeklyReport(state.products, scored, state.market, '2026-07-02');
    const line = (x) => `<li><span class="rank" style="color:${TONE[x.r.rec.code]}">${x.r.total}</span><span class="nm">${esc(x.p.name)} <span class="muted">· ${esc(x.p.city)}</span></span><span class="rec-badge rec-${x.r.rec.code}" style="font-size:10px;padding:2px 8px">${x.r.rec.code}</span></li>`;
    app.innerHTML = `
    <section class="view">
      <div class="section-head"><div><div class="eyebrow">每周商机报告</div><h2>气候商机雷达 · 周报</h2></div>
        <button class="btn primary" id="copyReport">复制报告全文</button></div>
      <div class="report">
        <h3>${rep.title}</h3>
        <div class="rmeta">${rep.date} · 自动生成 · 平均机会分 ${rep.avg}/100 · 最热城市 ${rep.hotCity}</div>
        <h4><span class="bar"></span>本周判断</h4>
        <p>${esc(rep.summary)}</p>
        ${rep.climate ? `<h4><span class="bar"></span>市场信号</h4><p class="muted">${esc(rep.climate)}</p>` : ''}
        ${rep.buzz ? `<h4><span class="bar"></span>社媒 & 新闻舆情</h4><p class="muted">${esc(rep.buzz)}</p>` : ''}
        <h4><span class="bar"></span>建议备货（BUY）</h4>
        <ul class="rlist">${rep.buy.length ? rep.buy.map(line).join('') : '<li class="muted" style="justify-content:center">本周暂无 BUY 级机会</li>'}</ul>
        <h4><span class="bar"></span>建议预售 / 测试（PRESELL）</h4>
        <ul class="rlist">${rep.presell.length ? rep.presell.map(line).join('') : '<li class="muted" style="justify-content:center">无</li>'}</ul>
        <h4><span class="bar"></span>本周行动</h4>
        <ul class="rlist">${rep.actions.map((a, i) => `<li><span class="rank">${i + 1}</span><span class="nm">${esc(a)}</span></li>`).join('')}</ul>
        <div class="disclaimer" style="margin-top:20px"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 6v6l4 2"/></svg>
          <span>所有建议围绕「低成本验证 · 小批量补货 · 快速复盘」。带电产品注意 CE/WEEE/EPR/GPSR 合规；如实描述效果，避免夸大制冷、避免盲目囤货。</span></div>
      </div>
    </section>`;
    $('#copyReport').onclick = () => copy(Generator.weeklyReportText(rep), '#copyReport');
  }

  // ============================================================
  //  MODAL: PRODUCT FORM
  // ============================================================
  function openForm(id) {
    const editing = !!id;
    const p = editing ? { ...state.products.find(x => x.id === id) } : Store.newProduct();
    const sel = (val, opts) => opts.map(([v, l]) => `<option value="${v}" ${val === v ? 'selected' : ''}>${l}</option>`).join('');
    const cityOpts = Store.CITIES.map(c => [c.name, `${c.zh} ${c.name}`]);

    const modal = document.createElement('div');
    modal.className = 'modal-back';
    modal.innerHTML = `
    <div class="modal">
      <div class="modal-head">
        <h3>${editing ? '编辑商品信号' : '录入新商品'}</h3>
        <button class="icon-btn" id="closeModal"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
      </div>
      <div class="modal-body">
        <form id="pform">
          <div class="form-grid">
            <div class="field"><label>Emoji 图标</label><input name="emoji" value="${esc(p.emoji)}" maxlength="2" /></div>
            <div class="field"><label>商品分类</label><select name="category">${sel(p.category, [['ac', '降温家电'], ['desk', '个人降温 · 桌面'], ['wearable', '个人降温 · 随身'], ['accessory', '配件周边']])}</select></div>
            <div class="field full"><label>商品名（中文）</label><input name="name" value="${esc(p.name)}" placeholder="如：桌面半导体制冷风扇" required /></div>
            <div class="field full"><label>商品名（法语 <span class="hint">用于 Leboncoin 文案</span>）</label><input name="nameFr" value="${esc(p.nameFr)}" placeholder="Ventilateur de bureau…" /></div>
            <div class="field"><label>分类标签</label><input name="cat" value="${esc(p.cat)}" placeholder="个人降温" /></div>
            <div class="field"><label>目标城市</label><select name="city">${sel(p.city, cityOpts)}</select></div>

            <div class="form-section">🌡️ 气候信号</div>
            ${rangeField('maxTemp', '日间峰值温度', p.maxTemp, 24, 44, '℃')}
            ${rangeField('nightTemp', '夜间低温', p.nightTemp, 10, 30, '℃')}
            ${rangeField('heatDays', '高温持续天数', p.heatDays, 0, 10, '天')}
            <div class="field"><label>官方高温预警</label><select name="alert">${sel(p.alert, [['none', '无'], ['yellow', '黄色'], ['orange', '橙色'], ['red', '红色']])}</select></div>
            <div class="field"><label>区域空调渗透率</label><select name="acPenetration">${sel(p.acPenetration, [['low', '低（机会大）'], ['mid', '中'], ['high', '高']])}</select></div>

            <div class="form-section">📈 市场情绪信号</div>
            ${rangeField('polymarket', 'Polymarket 高温概率', p.polymarket, 0, 100, '%')}
            ${rangeField('googleTrends', 'Google 搜索热度', p.googleTrends, 0, 100, '')}
            <div class="field"><label>预测市场概率</label><select name="polymarketRising">${sel(String(p.polymarketRising), [['true', '正在上升 ↑'], ['false', '持平 / 下降']])}</select></div>
            <div class="field"><label>搜索热度趋势</label><select name="trendsRising">${sel(String(p.trendsRising), [['true', '正在上升 ↑'], ['false', '持平 / 下降']])}</select></div>
            <div class="field"><label>缺货新闻</label><select name="newsShortage">${sel(p.newsShortage, [['none', '无'], ['emerging', '开始出现'], ['widespread', '普遍报道']])}</select></div>
            <div class="field"><label>零售平台库存</label><select name="retailStockout">${sel(p.retailStockout ? 'out' : (p.retailTight ? 'tight' : 'ok'), [['ok', '充足'], ['tight', '紧张 / 配送延迟'], ['out', '已缺货']])}</select></div>

            <div class="form-section">📦 商品与渠道信号</div>
            <div class="field"><label>欧洲售价 €</label><input name="euPrice" type="number" step="0.1" value="${p.euPrice}" /></div>
            <div class="field"><label>中国采购成本 €（含运）</label><input name="chinaCost" type="number" step="0.1" value="${p.chinaCost}" /></div>
            <div class="field"><label>单件重量 kg</label><input name="weightKg" type="number" step="0.1" value="${p.weightKg}" /></div>
            <div class="field"><label>补货速度</label><select name="restock">${sel(p.restock, [['yes', '来得及'], ['tight', '偏紧'], ['no', '来不及']])}</select></div>
            <div class="field"><label>可人肉带货</label><select name="handCarry">${sel(String(p.handCarry), [['true', '是'], ['false', '否']])}</select></div>
            <div class="field"><label>容易演示 / 秒懂</label><select name="demoEasy">${sel(String(p.demoEasy), [['true', '是'], ['false', '否']])}</select></div>
            <div class="field"><label>合规风险 <span class="hint">CE/WEEE/EPR</span></label><select name="complianceRisk">${sel(p.complianceRisk, [['low', '低'], ['mid', '中'], ['high', '高（带电）']])}</select></div>
            <div class="field"><label>退货 / 差评风险</label><select name="returnRisk">${sel(p.returnRisk, [['low', '低'], ['mid', '中'], ['high', '高']])}</select></div>

            <div class="form-section">🎯 本地实盘反馈 <span class="hint" style="text-transform:none;letter-spacing:0;color:var(--dim)">（可留空，挂现货后回填）</span></div>
            <div class="field"><label>浏览量</label><input name="views" type="number" value="${p.views}" /></div>
            <div class="field"><label>咨询人数</label><input name="inquiries" type="number" value="${p.inquiries}" /></div>
            <div class="field"><label>成交数</label><input name="conversions" type="number" value="${p.conversions}" /></div>
            <div class="field"><label>要求当天/次日取货</label><select name="fastPickup">${sel(String(p.fastPickup), [['false', '否'], ['true', '是']])}</select></div>
            <div class="field"><label>B 端小批量需求</label><select name="b2bDemand">${sel(String(p.b2bDemand), [['false', '暂无'], ['true', '出现 B 端需求']])}</select></div>
            <div class="field"><label>买家可接受价 € <span class="hint">对比售价判断价格敏感度</span></label><input name="acceptPrice" type="number" step="0.1" value="${p.acceptPrice || 0}" /></div>
            <div class="field full"><label>买家最常问的问题</label><input name="topQuestion" value="${esc(p.topQuestion || '')}" placeholder="还有货吗？能自取吗？安装难吗？噪音大吗？…" /></div>
            <div class="field full"><label>未成交原因</label><input name="noBuyReason" value="${esc(p.noBuyReason || '')}" placeholder="价格偏高 / 想等品牌方现货 / 担心安装 / 担心退货…" /></div>
            <div class="field full"><label>买家关心项 <span class="hint">逗号分隔</span></label><input name="concerns" value="${esc(p.concerns || '')}" placeholder="安装, 噪音, 能耗, 退货, 质保, 使用说明" /></div>

            <div class="field full"><label>备注</label><textarea name="note" placeholder="观察、假设、场景…">${esc(p.note)}</textarea></div>
          </div>
          <div class="form-foot">
            <div class="live-score">实时机会分 <b id="liveScore" style="color:var(--cyan)">–</b><span id="liveRec" class="rec-badge rec-WATCH">–</span></div>
            <div style="display:flex;gap:8px">
              ${editing ? `<button type="button" class="btn danger" id="delFromForm">删除</button>` : ''}
              <button type="submit" class="btn primary">${editing ? '保存' : '录入并评估'}</button>
            </div>
          </div>
        </form>
      </div>
    </div>`;
    document.body.appendChild(modal);
    document.body.style.overflow = 'hidden';

    const form = $('#pform', modal);
    const close = () => { modal.remove(); document.body.style.overflow = ''; };
    $('#closeModal', modal).onclick = close;
    modal.onclick = e => { if (e.target === modal) close(); };

    // range live values
    $$('input[type=range]', form).forEach(r => { const out = $('#' + r.name + '_val', form); r.oninput = () => { out.textContent = r.value; live(); }; });

    function readForm() {
      const fd = new FormData(form); const o = {};
      fd.forEach((v, k) => o[k] = v);
      // typed conversions
      ['polymarketRising', 'trendsRising', 'handCarry', 'demoEasy', 'fastPickup', 'b2bDemand'].forEach(k => o[k] = o[k] === 'true');
      o.retailStockout = fd.get('retailStockout') === 'out';
      o.retailTight = fd.get('retailStockout') === 'tight';
      ['maxTemp', 'nightTemp', 'heatDays', 'polymarket', 'googleTrends', 'euPrice', 'chinaCost', 'weightKg', 'views', 'inquiries', 'conversions', 'acceptPrice'].forEach(k => o[k] = parseFloat(o[k]) || 0);
      return { ...p, ...o };
    }
    function live() {
      const r = scoreOf(readForm());
      $('#liveScore', form).textContent = r.total;
      $('#liveScore', form).style.color = TONE[r.rec.code];
      const rb = $('#liveRec', form); rb.className = 'rec-badge rec-' + r.rec.code; rb.textContent = r.rec.code + ' · ' + r.rec.label;
    }
    form.oninput = live; form.onchange = live; live();

    if (editing) $('#delFromForm', modal).onclick = () => { if (confirm(`删除「${p.name}」？`)) { state.products = state.products.filter(x => x.id !== id); Store.save(state); close(); toast('已删除'); render(); } };

    form.onsubmit = e => {
      e.preventDefault();
      const np = readForm();
      if (!np.name.trim()) { alert('请填写商品名'); return; }
      if (editing) { const i = state.products.findIndex(x => x.id === id); state.products[i] = np; }
      else state.products.unshift(np);
      Store.save(state); close(); toast(editing ? '已保存' : '已录入并评估');
      if (!editing) { selectedId = np.id; go('decision'); } else render();
    };
  }

  function rangeField(name, label, val, min, max, unit) {
    return `<div class="field"><label>${label} <span class="hint">${unit}</span></label>
      <div class="range-row"><input type="range" name="${name}" min="${min}" max="${max}" value="${val}" /><span class="range-val"><span id="${name}_val">${val}</span></span></div></div>`;
  }

  // ---------- city editor ----------
  function openCityEditor() {
    const modal = document.createElement('div');
    modal.className = 'modal-back';
    modal.innerHTML = `<div class="modal"><div class="modal-head"><h3>编辑城市气候信号</h3><button class="icon-btn" id="cc"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M18 6L6 18M6 6l12 12"/></svg></button></div>
    <div class="modal-body"><form id="cform"><div class="form-grid">
      ${state.cities.map((c, i) => `<div class="field full" style="border:1px solid var(--border);border-radius:10px;padding:12px"><label style="color:var(--cyan)">${c.zh} ${c.name}</label>
        <div class="form-grid" style="gap:8px">
          <div class="field"><label>峰值℃</label><input type="number" name="max_${i}" value="${c.maxTemp}"></div>
          <div class="field"><label>夜温℃</label><input type="number" name="night_${i}" value="${c.nightTemp}"></div>
          <div class="field"><label>持续天</label><input type="number" name="days_${i}" value="${c.heatDays}"></div>
          <div class="field"><label>预警</label><select name="alert_${i}">${['none', 'yellow', 'orange', 'red'].map(a => `<option ${c.alert === a ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
        </div></div>`).join('')}
    </div><div class="form-foot"><span class="live-score" style="font-size:12px;color:var(--muted)">用于全局气候信号与雷达</span><button class="btn primary" type="submit">保存</button></div></form></div></div>`;
    document.body.appendChild(modal); document.body.style.overflow = 'hidden';
    const close = () => { modal.remove(); document.body.style.overflow = ''; };
    $('#cc', modal).onclick = close; modal.onclick = e => { if (e.target === modal) close(); };
    $('#cform', modal).onsubmit = e => {
      e.preventDefault(); const fd = new FormData(e.target);
      state.cities.forEach((c, i) => { c.maxTemp = +fd.get('max_' + i); c.nightTemp = +fd.get('night_' + i); c.heatDays = +fd.get('days_' + i); c.alert = fd.get('alert_' + i); });
      Store.save(state); close(); toast('气候信号已更新'); renderSignals();
    };
  }

  // ---------- utils ----------
  function riskLabel(l) { return { low: '低', mid: '中', high: '高' }[l] || l; }
  function riskColor(v) { return v >= 1 ? '#f4517a' : v >= 0.5 ? '#f5b544' : '#16d99b'; }
  function driverIcon(t) {
    if (t === 'pos') return '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M20 6L9 17l-5-5"/></svg>';
    if (t === 'neg') return '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0z"/></svg>';
    return '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/></svg>';
  }
  function copy(text, btn, isEl) {
    navigator.clipboard.writeText(text).then(() => {
      toast('已复制到剪贴板');
      const b = isEl ? btn : $(btn);
      if (b) { const o = b.textContent; b.classList.add('done'); b.textContent = '✓ 已复制'; setTimeout(() => { b.classList.remove('done'); b.textContent = o; }, 1400); }
    }).catch(() => toast('复制失败，请手动选择'));
  }
  let toastT;
  function toast(msg) { const t = $('#toast'); $('#toastMsg').textContent = msg; t.classList.add('show'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 1800); }

  // ============================================================
  //  LIVE SIGNALS — 实时信号水合
  // ============================================================
  let liveBusy = false;
  async function refreshLive(manual) {
    if (!window.LiveSignals || liveBusy) return;
    const names = LiveSignals.providerNames();
    if (!names.length) { if (manual) toast('暂无已启用的实时数据源'); return; }
    liveBusy = true;
    updateLivePill('refresh', '同步中…');
    let any = false;
    await LiveSignals.hydrate(state, (name, patch) => { any = true; });
    liveBusy = false;
    if (any) { Store.save(state); render(); }
    const meta = LiveSignals.status();
    const okCount = Object.values(meta).filter(m => m && m.ok).length;
    updateLivePill(okCount ? 'ok' : 'idle', okCount ? `实时 · ${okCount} 源已更新` : '实时数据未接通（种子）');
    if (manual) toast(okCount ? `已同步 ${okCount} 路实时信号` : '实时源暂不可达，已保留种子数据');
  }
  function updateLivePill(kind, text) {
    const el = $('#livePill'); if (!el) return;
    const color = kind === 'ok' ? 'var(--buy)' : kind === 'refresh' ? 'var(--test)' : 'var(--muted)';
    el.querySelector('.dot').style.background = color;
    el.querySelector('.dot').style.boxShadow = '0 0 10px ' + color;
    el.querySelector('.lt').textContent = text;
  }

  // expose for inline onclick
  window.__go = go;
  window.__refreshLive = () => refreshLive(true);

  // ---------- init ----------
  $('#nav').onclick = e => { const b = e.target.closest('button[data-view]'); if (b) go(b.dataset.view); };
  $('#addBtn').onclick = () => openForm(null);
  render();
  // 载入后自动尝试水合实时信号（失败静默降级）
  if (window.LiveSignals && LiveSignals.providerNames().length) setTimeout(() => refreshLive(false), 300);
})();
