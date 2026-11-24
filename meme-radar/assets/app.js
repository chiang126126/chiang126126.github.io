/* meme-radar 看板：只读 data/*.json（由 GitHub Actions 定时生成）。各板块独立容错。 */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d = 0) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
  const usd = v => v == null ? '—' : (Math.abs(v) >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : Math.abs(v) >= 1e3 ? '$' + (v / 1e3).toFixed(1) + 'k' : '$' + Number(v).toFixed(2));
  const pct = (v, d = 0) => v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
  const cls = v => v == null ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : '';
  const age = h => h == null ? '—' : h < 1 ? Math.round(h * 60) + 'm' : h < 48 ? h.toFixed(1) + 'h' : (h / 24).toFixed(1) + 'd';
  const short = a => a ? a.slice(0, 6) + '…' + a.slice(-4) : '—';
  const ago = iso => { if (!iso) return '—'; const m = (Date.now() - Date.parse(iso)) / 60000; return m < 60 ? Math.round(m) + ' 分钟前' : m < 1440 ? (m / 60).toFixed(1) + ' 小时前' : (m / 1440).toFixed(1) + ' 天前'; };
  const price = p => p == null ? '—' : p >= 1 ? '$' + p.toFixed(4) : p >= 0.0001 ? '$' + p.toFixed(6) : '$' + Number(p).toExponential(2);
  const BS = 'https://robinhoodchain.blockscout.com';

  async function load(path) {
    const r = await fetch(path + '?t=' + Math.floor(Date.now() / 60000), { cache: 'no-store' });
    if (!r.ok) throw new Error(path + ' ' + r.status);
    return r.json();
  }

  // ---------------------------------------------------------------- status / regime
  function renderStatus(s) {
    const run = s.run || {};
    const chips = [];
    chips.push(`<span class="chip ${run.errors && run.errors.length ? 'warn' : 'ok'}"><span class="dot"></span>最近运行 ${esc(ago(s.updated))}</span>`);
    chips.push(`<span class="chip"><span class="dot"></span>规则 v${esc(run.rules_version || '?')}</span>`);
    chips.push(`<span class="chip ${run.ai_enabled ? 'ok' : ''}"><span class="dot"></span>AI 审查 ${run.ai_enabled ? '开' : '规则版'}</span>`);
    chips.push(`<span class="chip ${run.blockscout_pro ? 'ok' : ''}"><span class="dot"></span>Blockscout ${run.blockscout_pro ? 'PRO' : '公共'}</span>`);
    chips.push(`<span class="chip"><span class="dot"></span>聪明钱库 ${esc(run.smart_wallets || 0)}</span>`);
    $('#status').innerHTML = chips.join('');
    $('#foot-ver').textContent = `summary.json 更新 ${s.updated || '—'} · 本轮 ${run.seconds || 0}s · 阶段 ${(run.stages || []).join(', ')}`;
  }

  function renderRegime(r) {
    if (!r || !r.regime) return;
    const m = r.metrics || {};
    $('#rg-tag').textContent = r.regime;
    $('#rg-zh').textContent = r.regime_zh || '';
    $('#rg-conf').textContent = `置信 ${r.confidence ?? '—'}`;
    $('#rg-judg').textContent = r.judgment || '';
    $('#regime-sub').textContent = r.ts ? `判定于 ${ago(r.ts)}` + (r.stale ? '（沿用上一轮）' : '') : '';
    const ev = (r.support || []).map(t => `<li>${esc(t)}</li>`).concat((r.challenge || []).map(t => `<li class="ch">${esc(t)}</li>`));
    $('#rg-evid').innerHTML = ev.join('');
    const b = Number(r.risk_budget || 0);
    $('#water').style.width = (b * 100) + '%';
    $('#rg-budget').textContent = b.toFixed(2) + (r.blow_off ? ' ×½ 冲顶' : '');
    $('#rg-max').textContent = `今日最多新开 ${r.max_new_positions ?? 0} 仓`;
    const tiles = [
      ['BTC', m.btc_price ? '$' + num(m.btc_price) : '—', `7d ${pct(m.btc_change_7d_pct, 1)} · ${m.btc_trend || ''}`],
      ['BTC 占比', m.btc_dominance != null ? m.btc_dominance.toFixed(1) + '%' : '—', m.btc_dominance_change_7d != null ? `7d ${m.btc_dominance_change_7d > 0 ? '+' : ''}${m.btc_dominance_change_7d.toFixed(2)}pp` : '7d 变化累计中'],
      ['山寨广度 30d', m.alt_breadth_30d != null ? Math.round(m.alt_breadth_30d * 100) + '%' : '—', m.alt_breadth_7d != null ? `7d ${Math.round(m.alt_breadth_7d * 100)}% · 山寨季线 75%` : '前100币跑赢BTC比例'],
      ['ETH/BTC', pct(m.eth_btc_change_7d_pct, 1), `30d ${pct(m.eth_btc_change_30d_pct, 1)}`],
      ['恐惧贪婪', m.fear_greed != null ? Math.round(m.fear_greed) : '—', m.fear_greed_class || ''],
      ['本链头部池 24h', m.chain_top_pools_vol_24h ? usd(m.chain_top_pools_vol_24h) : '—', m.chain_vol_wow_pct != null ? `周环比 ${pct(m.chain_vol_wow_pct)}` : (m.chain_new_pools_24h_est != null ? `新池(首页) ${m.chain_new_pools_24h_est}` : '')],
    ];
    $('#rg-metrics').innerHTML = tiles.map(t => `<div class="metric"><div class="k">${esc(t[0])}</div><div class="v">${esc(t[1])}</div><div class="d">${esc(t[2])}</div></div>`).join('');
  }

  // ---------------------------------------------------------------- candidates
  function constellation(f) {
    const hm = (f && f.holder_map) || [];
    if (!hm.length) return '<p class="dim" style="font-size:12px">无持有人星图（取证未完成）</p>';
    const W = 420, H = 84, pad = 6;
    const maxP = Math.max(...hm.map(h => h.p), 0.01);
    let x = pad, items = [], lines = [];
    hm.forEach((h, i) => {
      const r = 4 + 16 * Math.sqrt(h.p / maxP);
      x += r;
      items.push({ ...h, x, y: H / 2 - 8, r });
      x += r + 4;
    });
    const scale = x > W - pad ? (W - pad) / x : 1;
    items.forEach(it => { it.x *= scale; it.r *= scale; });
    const byC = {};
    items.forEach(it => { if (it.c != null) (byC[it.c] = byC[it.c] || []).push(it); });
    Object.values(byC).forEach(g => { for (let i = 1; i < g.length; i++) lines.push(`<line x1="${g[0].x}" y1="${g[0].y}" x2="${g[i].x}" y2="${g[i].y}" stroke="var(--neg)" stroke-opacity=".6" stroke-width="1"/>`); });
    const circles = items.map(it => {
      const fill = it.c != null ? 'var(--neg)' : it.k === 'creator' ? 'var(--warn)' : 'var(--ink-1)';
      const hollow = it.f;
      return `<circle cx="${it.x.toFixed(1)}" cy="${it.y}" r="${it.r.toFixed(1)}" fill="${hollow ? 'none' : fill}" stroke="${hollow ? 'var(--warn)' : fill}" stroke-width="${hollow ? 1.5 : 0}"><title>${esc(it.a)} · ${it.p}% · 年龄 ${age(it.age)} · ${it.tx ?? '?'} 笔${it.c != null ? ' · 簇#' + (it.c + 1) : ''}${it.f ? ' · 新钱包' : ''}</title></circle>`;
    });
    const locked = Number(f.contract_held_pct || 0), burn = Number(f.burn_pct || 0), insp = Number(f.inspected_pct || 0);
    const rest = Math.max(0, 100 - locked - burn - insp);
    const segs = [[locked, 'var(--ink-3)', '池子/锁仓/合约'], [burn, 'var(--bg-3)', '销毁'], [insp, 'var(--accent)', '前排 EOA（已检查）'], [rest, 'var(--bg-2)', '其余散户']];
    let sx = 0;
    const bar = segs.map(s => { const w = s[0] / 100 * W; const el = `<rect x="${sx.toFixed(1)}" y="${H - 10}" width="${Math.max(0, w).toFixed(1)}" height="8" fill="${s[1]}"><title>${s[2]} ${s[0].toFixed(1)}%</title></rect>`; sx += w; return el; }).join('');
    return `<svg class="constellation" viewBox="0 0 ${W} ${H}" role="img" aria-label="前排持有人星图">${lines.join('')}${circles.join('')}${bar}</svg>
      <div class="legend"><span><i style="background:var(--ink-1)"></i>独立钱包</span><span><i style="background:var(--neg)"></i>同簇（连线）</span><span><i style="border:1.5px solid var(--warn)"></i>新钱包</span><span><i style="background:var(--warn)"></i>创建者</span><span>面积 ∝ 持仓 · 底条：锁仓 / 销毁 / 前排 / 其余</span></div>`;
  }

  function scoreBars(b) {
    const W = { liquidity_health: 10, distribution: 20, organic_growth: 15, sybil_integrity: 25, smart_money: 15, price_structure: 10, narrative_social: 5 };
    const N = { liquidity_health: '流动性健康', distribution: '筹码分布', organic_growth: '有机增长', sybil_integrity: '钱包独立性', smart_money: '聪明钱', price_structure: '价格结构', narrative_social: '叙事/社交' };
    return '<div class="bars">' + Object.keys(W).map(k => { const v = (b || {})[k] || 0; return `<div class="bar"><span class="k">${N[k]}</span><span class="t"><i style="width:${Math.min(100, v / W[k] * 100)}%"></i></span><span class="v">${v.toFixed(1)}/${W[k]}</span></div>`; }).join('') + '</div>';
  }

  function evidenceText(b, regime) {
    const f = b.forensics || {}, sm = b.smart_money || {}, x = b.x || {}, sec = b.security || {};
    return [
      `# 候选 ${b.symbol}（${b.name}）· Robinhood Chain · ${b.dex}`, `token ${b.token}`, `pool ${b.pool}`, `链接 ${b.url}`, '',
      `## 市场环境`, `${regime.regime}（${regime.regime_zh}）预算 ${regime.risk_budget}；${regime.judgment || ''}`, '',
      `## 快照`, `价格 ${price(b.price_usd)}；流动性 ${usd(b.liquidity_usd)}；FDV ${usd(b.fdv_usd)}；年龄 ${age(b.age_hours)}`,
      `成交 1h ${usd(b.vol_h1)} / 24h ${usd(b.vol_h24)}；涨跌 1h ${pct(b.chg_h1)} 6h ${pct(b.chg_h6)} 24h ${pct(b.chg_h24)}`,
      `独立买家 1h ${b.buyers_h1 ?? '?'} / 24h ${b.buyers_h24 ?? '?'}；24h 买${b.buys_h24}/卖${b.sells_h24}`, '',
      `## 钱包级成交`, `样本 ${x.x_trades_n ?? '?'} 笔；1h 净流入 ${usd(x.x_net_flow_1h_usd)}（占池 ${x.x_net_flow_1h_to_liq ?? '?'}）；独立买家 ${x.x_unique_buyers_trades ?? '?'}；前5买家占比 ${x.x_top5_buyer_share ?? '?'}`, '',
      `## 合约安全`, `${sec.source || '-'}；发射台 ${sec.launchpad || '-'}；honeypot ${sec.is_honeypot}；税 ${sec.buy_tax_pct}/${sec.sell_tax_pct}；owner ${sec.has_owner}；标记 ${(sec.flags || []).join(',')}`, '',
      `## 取证`, `质量 ${f.quality}；持有人 ${f.holders}；前10 EOA ${f.top10_eoa_pct}%；创建者 ${f.creator_pct}%；关联簇 ${(f.clusters || []).length} 个合计 ${f.clustered_pct}%（最大 ${f.largest_cluster_pct}%）；新钱包 ${f.fresh_wallet_count} 个 ${f.fresh_wallet_pct}%；最早买家仍持 ${f.early_buyers_holding_pct ?? '?'}%；sybil ${f.sybil_score}；${f.launchpad || ''} ${f.curve_status || ''}`,
      ...(f.notes || []).map(n => `- ${n}`), '',
      `## 聪明钱`, `库 ${sm.registry_size ?? 0}；共振 ${sm.count ?? 0}（加权 ${sm.weighted ?? 0}）净买 ${usd(sm.net_buy_usd)}`, '',
      `## 规则层`, `评分 ${b.score} ${JSON.stringify(b.score_breakdown)}`, `红旗 ${(b.flags.red || []).join(',') || '无'}；黄旗 ${(b.flags.yellow || []).join(',') || '无'}；绿旗 ${(b.flags.green || []).join(',') || '无'}`,
      `硬过滤 ${(b.killed_by || []).join(',') || '通过'}；决策 ${b.decision}：${(b.reasons || []).join('；')}`,
      b.ai ? `AI(${b.ai.provider}) ${b.ai.verdict} ${b.ai.confidence}：${(b.ai.key_evidence || []).join('；')}` : '',
      '', '请只根据以上证据判断：这是一个真实、分散、独立参与的市场，还是少数关联钱包制造的假象？给出 verdict / confidence / 关键证据 / 什么证据会改变判断。'
    ].join('\n');
  }

  function candCard(b, regime) {
    const f = b.forensics, sm = b.smart_money || {}, ai = b.ai;
    const flags = [].concat((b.killed_by || []).map(k => `<span class="flag kill">${esc(k)}</span>`),
      (b.flags.red || []).map(k => `<span class="flag red">${esc(k)}</span>`),
      (b.flags.yellow || []).map(k => `<span class="flag yellow">${esc(k)}</span>`),
      (b.flags.green || []).map(k => `<span class="flag green">${esc(k)}</span>`));
    const kv = `<span>流动性 <b>${usd(b.liquidity_usd)}</b></span><span>FDV <b>${usd(b.fdv_usd)}</b></span><span>年龄 <b>${age(b.age_hours)}</b></span>
      <span>1h <b class="${cls(b.chg_h1)}">${pct(b.chg_h1)}</b></span><span>24h <b class="${cls(b.chg_h24)}">${pct(b.chg_h24)}</b></span>
      <span>买家 1h <b>${b.buyers_h1 ?? '—'}</b></span><span>sybil <b class="${f && f.sybil_score >= 0.5 ? 'neg' : f && f.sybil_score <= 0.2 ? 'pos' : ''}">${f ? f.sybil_score : '—'}</b></span><span>聪明钱 <b>${sm.count ?? 0}</b></span>`;
    const detail = `
      <div class="blk"><h4>七维评分 · ${b.score}</h4>${scoreBars(b.score_breakdown)}
        <h4 style="margin-top:12px">持有人星图 · 前 ${f ? f.inspected : 0} 个 EOA${f && f.profiled != null ? ' · 画像 ' + f.profiled + '/' + f.inspected : ''}</h4>${constellation(f)}
        ${f && f.clusters && f.clusters.length ? `<ul class="notes">${f.clusters.slice(0, 4).map((c, i) => `<li>簇#${i + 1}：${c.size} 个钱包持 ${c.pct}% · ${c.reasons.join(', ')} · ${c.wallets.slice(0, 3).map(short).join(' ')}</li>`).join('')}</ul>` : ''}
        ${f && f.notes && f.notes.length ? `<ul class="notes">${f.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
      </div>
      <div class="blk"><h4>证据</h4>
        <div class="kvlist">
          <div><span>价格</span><span>${price(b.price_usd)}</span></div><div><span>成交 1h / 24h</span><span>${usd(b.vol_h1)} / ${usd(b.vol_h24)}</span></div>
          <div><span>24h 买/卖</span><span>${b.buys_h24}/${b.sells_h24}</span></div><div><span>独立买家 24h</span><span>${b.buyers_h24 ?? '—'}</span></div>
          <div><span>1h 净流入</span><span class="${cls(b.x.x_net_flow_1h_usd)}">${usd(b.x.x_net_flow_1h_usd)}</span></div><div><span>前 5 买家占比</span><span>${b.x.x_top5_buyer_share ?? '—'}</span></div>
          <div><span>持有人</span><span>${f ? f.holders ?? '—' : '—'}</span></div><div><span>前 10 EOA</span><span>${f ? f.top10_eoa_pct + '%' : '—'}</span></div>
          <div><span>关联簇 / 最大</span><span>${f ? f.clustered_pct + '% / ' + f.largest_cluster_pct + '%' : '—'}</span></div><div><span>新钱包</span><span>${f ? f.fresh_wallet_count + ' 个 · ' + f.fresh_wallet_pct + '%' : '—'}</span></div>
          <div><span>创建者持仓</span><span>${f ? f.creator_pct + '%' : '—'}</span></div><div><span>最早买家仍持</span><span>${f && f.early_buyers_holding_pct != null ? f.early_buyers_holding_pct + '%' : '—'}</span></div>
          <div><span>发射台</span><span>${esc((f && f.launchpad) || (b.security && b.security.launchpad) || '—')} ${esc((f && f.curve_status) || '')}</span></div><div><span>安全来源</span><span>${esc(b.security ? b.security.source : '—')}</span></div>
          <div><span>聪明钱共振</span><span>${sm.count ?? 0} · 净买 ${usd(sm.net_buy_usd)}</span></div><div><span>模拟仓</span><span>${b.position_size_usd ? '$' + b.position_size_usd : '—'}</span></div>
        </div>
        ${ai ? `<div class="ai" style="margin-top:10px"><b class="${ai.verdict === 'MANIPULATED' ? 'neg' : ai.verdict === 'REAL_MARKET' ? 'pos' : 'warn'}">${esc(ai.verdict)}</b> · 置信 ${ai.confidence} · ${esc(ai.provider)}${ai.model ? ' ' + esc(ai.model) : ''}<br>${(ai.key_evidence || []).map(esc).join('；')}${ai.what_would_change_mind ? '<br><span class="dim">改变判断需要：' + esc(ai.what_would_change_mind) + '</span>' : ''}</div>` : ''}
        <ul class="notes">${(b.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>
        <div class="actions">
          <button class="btn primary" data-copy="${esc(b.token)}">复制证据文档 → 贴给 LLM</button>
          <a class="btn" href="${esc(b.url)}" target="_blank" rel="noopener">GeckoTerminal</a>
          <a class="btn" href="https://dexscreener.com/robinhood/${esc(b.token)}" target="_blank" rel="noopener">DexScreener</a>
          <a class="btn" href="${BS}/token/${esc(b.token)}" target="_blank" rel="noopener">Blockscout</a>
        </div>
      </div>`;
    return `<article class="cand ${esc(b.decision)}" data-dec="${esc(b.decision)}" data-token="${esc(b.token)}">
      <div class="cand-h" role="button" tabindex="0" aria-expanded="false">
        <span class="dec ${esc(b.decision)}">${esc(b.decision)}</span>
        <div class="cand-t"><div class="sym">${esc(b.symbol)}<small>${esc(b.name)} · ${esc(b.dex)} · <span class="mono">${short(b.token)}</span></small></div><div class="kv">${kv}</div></div>
        <div class="score"><span class="big ${b.score >= 72 ? 'pos' : b.score >= 60 ? 'acc' : 'dim'}">${b.score == null ? '—' : Math.round(b.score)}</span><span class="lab">score</span></div>
      </div>
      <div class="flagrow">${flags.join('')}</div>
      <div class="cand-d">${detail}</div>
    </article>`;
  }

  let ALL = [], REGIME = {};
  function renderCandidates(items, regime, universe, counts) {
    ALL = items || []; REGIME = regime || {};
    const u = universe || {};
    $('#funnel').innerHTML = [
      ['发现新池', u.discovered ?? '—'], ['预过滤通过', u.prefiltered ?? '—'], ['完成取证', u.forensics_done ?? '—'],
      ['WATCH', counts.watch ?? 0, 'watch'], ['PAPER_BUY', counts.paper_buy ?? 0, 'buy'], ['剔除', (u.skipped ?? 0)],
    ].map(t => `<div class="${t[2] || ''}"><div class="k">${t[0]}</div><div class="v">${t[1]}</div></div>`).join('');
    $('#cands-sub').textContent = u.seconds ? `本轮 ${u.seconds}s · 新样本 ${u.new_samples ?? 0} · 基线 ${u.new_baseline ?? 0}` : '';
    draw('ALL');
  }
  function draw(filter) {
    const list = ALL.filter(b => filter === 'ALL' || b.decision === filter);
    const box = $('#cand-list');
    if (!list.length) {
      box.innerHTML = `<div class="empty">${ALL.length ? '该分类暂无候选。' : '还没有候选数据。首次运行：仓库 <b>Actions → meme-radar → Run workflow</b>，或本地 <code>python meme-radar/run.py cycle --verbose</code>。'}</div>`;
      return;
    }
    box.innerHTML = list.map(b => candCard(b, REGIME)).join('');
  }

  // ---------------------------------------------------------------- watchlist
  function spark(hist) {
    const pts = (hist || []).map(h => h.price).filter(p => p != null);
    if (pts.length < 2) return '';
    const mn = Math.min(...pts), mx = Math.max(...pts), W = 70, H = 20;
    const d = pts.map((p, i) => `${(i / (pts.length - 1) * W).toFixed(1)},${(H - 2 - (mx > mn ? (p - mn) / (mx - mn) : .5) * (H - 4)).toFixed(1)}`).join(' ');
    const up = pts[pts.length - 1] >= pts[0];
    return `<svg class="spark" viewBox="0 0 ${W} ${H}"><polyline points="${d}" fill="none" stroke="${up ? 'var(--pos)' : 'var(--neg)'}" stroke-width="1.5"/></svg>`;
  }
  function renderWatchlist(items) {
    const box = $('#watchlist');
    $('#wl-n').textContent = (items || []).length + ' 个';
    if (!items || !items.length) { box.innerHTML = '<div class="dim" style="font-size:12.5px">近 96 小时没有进入观察的代币。这是正常的：大多数轮次应该一无所获。</div>'; return; }
    box.innerHTML = items.slice(0, 20).map(it => {
      const o = it.outcomes || {}, r24 = o.h24 && o.h24.ret_pct, chg = it.first_price && it.price_usd ? (it.price_usd / it.first_price - 1) * 100 : null;
      return `<div class="wl-i"><span class="dec ${esc(it.decision)}" style="min-width:74px">${esc(it.decision)}</span>
        <div><div class="s">${esc(it.symbol)} <span class="dim mono" style="font-weight:400;font-size:11px">${Math.round(it.score || 0)}</span></div>
        <div class="m">首见 ${ago(it.first_seen)} · ${usd(it.liquidity_usd)} · sybil ${it.sybil_score ?? '—'} · 自首见 <span class="${cls(chg)}">${pct(chg)}</span>${r24 != null ? ' · 24h ' + pct(r24) : ''}${it.status === 'rug' ? ' · <span class="neg">RUG</span>' : ''}</div></div>
        ${spark(it.history)}</div>`;
    }).join('');
  }

  // ---------------------------------------------------------------- health
  function renderHealth(run) {
    run = run || {};
    $('#run-at').textContent = run.at ? ago(run.at) : '';
    const http = run.http || {};
    const rows = Object.keys(http).map(k => `<div><span>${esc(k)}</span><span>${http[k].calls || 0} 次${http[k].errors ? ' · <span class="neg">' + http[k].errors + ' 错</span>' : ''}</span></div>`);
    rows.unshift(`<div><span>AI 调用</span><span>${run.ai_calls || 0}</span></div>`);
    $('#health').innerHTML = rows.join('');
    $('#errs').innerHTML = run.errors && run.errors.length ? `<div class="errs">${run.errors.slice(0, 8).map(esc).join('\n')}</div>` : '';
  }

  // ---------------------------------------------------------------- validation
  function renderEvaluation(ev, samples) {
    ev = ev || {}; const h24 = (ev.horizons || {}).h24 || {}; const pr = ev.progress || {};
    const V = { edge: ['有优势', '筛选组 24h 命中率显著高于随机基线（95% CI 下界 > 0）'], no_edge: ['无优势', '筛选组并不优于随机；说明模型看起来聪明但没有商业价值，需要改规则'], unclear: ['尚不清楚', '样本够了但差异不显著，继续积累或改规则'], insufficient: ['样本不足', '两组各满 50 个样本前不下结论'] };
    const v = V[ev.verdict] || V.insufficient;
    const vb = $('#verdict'); vb.className = 'verdict ' + (ev.verdict || 'insufficient'); vb.querySelector('.big').textContent = v[0]; $('#verdict-txt').textContent = v[1];
    $('#ev-rules').textContent = ev.updated ? '评估于 ' + ago(ev.updated) : '';
    const need = ev.min_samples || 50;
    $('#prog').innerHTML = [['筛选组', pr.selected || 0, ''], ['随机基线', pr.baseline || 0, 'b']].map(p => `<div class="row ${p[2]}"><span>${p[0]}</span><span class="t"><i style="width:${Math.min(100, p[1] / need * 100)}%"></i></span><span class="v">${p[1]} / ${need}</span></div>`).join('');
    const G = [['SELECTED', '筛选（WATCH+BUY）'], ['BUY', 'PAPER_BUY'], ['BASELINE', '随机基线'], ['SKIP', '被剔除']];
    let t = '<tr><th>组</th><th class="r">n</th><th class="r">中位 24h</th><th class="r">命中率</th><th class="r">归零率</th><th class="r">正收益率</th><th class="r">均值最大涨幅</th></tr>';
    G.forEach(g => { const s = h24[g[0]] || {}; t += `<tr><td>${g[1]}</td><td class="r">${s.n || 0}</td><td class="r ${cls(s.median_ret_pct)}">${s.n ? pct(s.median_ret_pct, 1) : '—'}</td><td class="r">${s.n ? Math.round(s.hit_rate * 100) + '%' : '—'}</td><td class="r">${s.n ? Math.round(s.rug_rate * 100) + '%' : '—'}</td><td class="r">${s.n ? Math.round(s.positive_rate * 100) + '%' : '—'}</td><td class="r">${s.n && s.mean_max_ret_pct != null ? pct(s.mean_max_ret_pct) : '—'}</td></tr>`; });
    const d = h24.selected_vs_baseline;
    if (d) t += `<tr><td colspan="7" class="dim">命中率差（筛选 − 基线）${d.diff > 0 ? '+' : ''}${d.diff}，95% CI [${d.ci_low}, ${d.ci_high}]</td></tr>`;
    $('#ev-table').innerHTML = t;
    // buckets
    const FB = ev.feature_buckets || {}; const N = { sybil_score: 'sybil 分', smart_count: '聪明钱共振数', score: '评分段', launchpad: '发射台', forensics_quality: '取证质量', liquidity: '流动性', age_hours: '发现时年龄', chg_h1: '发现时 1h 涨幅', has_socials: '有社交', top10_eoa_pct: '前10 EOA 占比', fresh_wallet_pct: '新钱包占比', x_top5_buyer_share: '前5买家占比' };
    let bt = '<tr><th>特征</th><th>分桶</th><th class="r">n</th><th class="r">中位 24h</th><th class="r">命中率</th><th class="r">归零率</th></tr>';
    let any = false;
    Object.keys(FB).forEach(k => Object.keys(FB[k]).forEach(b => { const s = FB[k][b]; if (!s.n) return; any = true; bt += `<tr><td>${N[k] || k}</td><td class="mono">${esc(b)}</td><td class="r">${s.n}</td><td class="r ${cls(s.median_ret_pct)}">${pct(s.median_ret_pct, 1)}</td><td class="r">${Math.round(s.hit_rate * 100)}%</td><td class="r">${Math.round(s.rug_rate * 100)}%</td></tr>`; }));
    $('#bucket-table').innerHTML = any ? bt : '<tr><td class="dim">等待样本回填（首批 24h 结果在发现后一天出现）。</td></tr>';
    // samples
    const rs = samples || [];
    $('#samples-n').textContent = rs.length ? `显示 ${Math.min(rs.length, 30)} 条` : '';
    let st = '<tr><th>时间</th><th>代币</th><th>决策</th><th class="r">分</th><th class="r">发现价</th><th class="r">1h</th><th class="r">6h</th><th class="r">24h</th><th class="r">7d</th><th>状态</th></tr>';
    rs.slice(0, 30).forEach(s => { const o = s.outcomes || {}; const c = h => o['h' + h] ? `<td class="r ${cls(o['h' + h].ret_pct)}">${pct(o['h' + h].ret_pct)}</td>` : '<td class="r dim">·</td>'; st += `<tr><td class="mono dim">${esc(ago(s.discovered_at))}</td><td><a href="${esc(s.url || '#')}" target="_blank" rel="noopener">${esc(s.symbol)}</a></td><td><span class="dec ${esc(s.decision)}" style="min-width:0;padding:1px 6px">${esc(s.decision)}</span></td><td class="r">${s.score == null ? '—' : Math.round(s.score)}</td><td class="r mono">${price(s.price_at)}</td>${c(1)}${c(6)}${c(24)}${c(168)}<td class="${s.status === 'rug' ? 'neg' : 'dim'}">${esc(s.status)}</td></tr>`; });
    $('#samples-table').innerHTML = rs.length ? st : '<tr><td class="dim">还没有样本。</td></tr>';
  }

  function renderPortfolio(pf, open, closed) {
    pf = pf || {}; $('#pf-cap').textContent = `本金 $${num(pf.capital_usd)}`;
    const tiles = [['权益', '$' + num(pf.equity_usd, 2), cls((pf.equity_usd || 0) - (pf.capital_usd || 0))], ['已实现', '$' + num(pf.realized_pnl_usd, 2), cls(pf.realized_pnl_usd)], ['未实现', '$' + num(pf.unrealized_pnl_usd, 2), cls(pf.unrealized_pnl_usd)],
      ['持仓 / 已平', `${pf.open_positions || 0} / ${pf.closed_positions || 0}`, ''], ['胜率 · 盈亏因子', `${pf.win_rate != null ? Math.round(pf.win_rate * 100) + '%' : '—'} · ${pf.profit_factor == null ? '—' : pf.profit_factor === null ? '∞' : pf.profit_factor}`, ''], ['最大回撤', pct(-(pf.max_drawdown_pct || 0), 1), '']];
    $('#pf-tiles').innerHTML = tiles.map(t => `<div class="tile"><div class="k">${t[0]}</div><div class="v ${t[2]}">${t[1]}</div></div>`).join('');
    let t = '<tr><th>代币</th><th>开仓</th><th class="r">仓位</th><th class="r">入场价</th><th class="r">现价/峰值</th><th class="r">浮动</th><th>状态</th></tr>';
    (open || []).forEach(p => { const u = (p.last_price / p.entry_price - 1) * 100; t += `<tr><td>${esc(p.symbol)}</td><td class="mono dim">${esc(ago(p.opened_at))}</td><td class="r">$${num(p.size_usd, 2)} ×${p.remaining_fraction}</td><td class="r mono">${price(p.entry_price)}</td><td class="r mono">${price(p.last_price)} / ${(p.peak_price / p.entry_price).toFixed(2)}x</td><td class="r ${cls(u)}">${pct(u)}</td><td>${p.tp_hit && p.tp_hit.length ? 'TP' + p.tp_hit.map(i => i + 1).join(',') + ' 已收' : '持有'}</td></tr>`; });
    (closed || []).slice(0, 12).forEach(p => { t += `<tr><td class="dim">${esc(p.symbol)}</td><td class="mono dim">${esc(ago(p.closed_at))}</td><td class="r dim">$${num(p.size_usd, 2)}</td><td class="r mono dim">${price(p.entry_price)}</td><td class="r mono dim">${(p.peak_price / p.entry_price).toFixed(2)}x 峰</td><td class="r ${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</td><td class="dim">${esc(p.close_reason)} · ${p.hold_hours}h</td></tr>`; });
    $('#pos-table').innerHTML = (open && open.length) || (closed && closed.length) ? t : '<tr><td class="dim">还没有模拟仓。只有评分 ≥ 72、无红旗、且体制预算 > 0 时才会开仓。</td></tr>';
  }

  // ---------------------------------------------------------------- events
  document.addEventListener('click', e => {
    const f = e.target.closest('#filters button');
    if (f) { document.querySelectorAll('#filters button').forEach(b => b.classList.toggle('on', b === f)); draw(f.dataset.f); return; }
    const cp = e.target.closest('[data-copy]');
    if (cp) { const b = ALL.find(x => x.token === cp.dataset.copy); if (!b) return; const txt = evidenceText(b, REGIME); (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(() => { cp.textContent = '已复制'; setTimeout(() => cp.textContent = '复制证据文档 → 贴给 LLM', 1500); }).catch(() => window.prompt('复制以下内容', txt)); return; }
    const h = e.target.closest('.cand-h');
    if (h && !e.target.closest('a')) { const c = h.parentElement; c.classList.toggle('open'); h.setAttribute('aria-expanded', c.classList.contains('open')); }
  });
  document.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('cand-h')) { e.preventDefault(); e.target.click(); } });

  // ---------------------------------------------------------------- boot
  (async function boot() {
    let s;
    try { s = await load('data/summary.json'); }
    catch (err) {
      $('#status').innerHTML = '<span class="chip warn"><span class="dot"></span>尚无数据 · 等待首次运行</span>';
      try { renderRegime(await load('data/regime.json')); } catch (_) { }
      draw('ALL'); renderWatchlist([]); renderEvaluation({}, []); renderPortfolio({}, [], []); renderHealth({});
      return;
    }
    try { renderStatus(s); } catch (e) { console.error(e); }
    try { renderRegime(s.regime); } catch (e) { console.error(e); }
    let items = s.top || [];
    try { const c = await load('data/candidates.json'); if (c && c.items && c.items.length >= items.length) items = c.items; } catch (_) { }
    try { renderCandidates(items, s.regime, s.universe, s.counts || {}); } catch (e) { console.error(e); }
    try { renderWatchlist(s.watchlist); } catch (e) { console.error(e); }
    try { renderHealth(s.run); } catch (e) { console.error(e); }
    try { renderEvaluation(s.evaluation, s.recent_samples); } catch (e) { console.error(e); }
    try { renderPortfolio(s.portfolio, s.positions_open, s.positions_closed_recent); } catch (e) { console.error(e); }
  })();
})();
