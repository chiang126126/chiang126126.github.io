/* =========================================================================
   DispoClim — logique du radar
   ========================================================================= */
(function () {
  "use strict";
  const D = window.DISPOCLIM;
  if (!D) return;

  const FREE_RESULT_LIMIT = 6; // au-delà : Pass requis (démo de la logique freemium)
  const byId = (id) => document.getElementById(id);
  const retailerMap = Object.fromEntries(D.RETAILERS.map((r) => [r.id, r]));
  const storeMap = Object.fromEntries(D.STORES.map((s) => [s.id, s]));
  const productMap = Object.fromEntries(D.PRODUCTS.map((p) => [p.id, p]));

  const state = {
    product: "midea-portasplit-12000",
    cp: "74580",
    radius: 100,
    maxPrice: "",
    inStockOnly: false,
    fulfillment: "all", // all | pickup | delivery
    retailers: new Set(D.RETAILERS.map((r) => r.id)),
    sort: "distance",
    isPaid: false,
  };

  /* ---------- géo ---------- */
  function haversine(a, b, c, d) {
    const R = 6371, toR = (x) => (x * Math.PI) / 180;
    const dLat = toR(c - a), dLon = toR(d - b);
    const s = Math.sin(dLat / 2) ** 2 + Math.cos(toR(a)) * Math.cos(toR(c)) * Math.sin(dLon / 2) ** 2;
    return Math.round(R * 2 * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s)));
  }
  function originFromCP(cp) {
    cp = (cp || "").trim();
    const exact = D.STORES.find((s) => s.cp === cp && !s.online);
    if (exact) return [exact.lat, exact.lon];
    const dep = cp.slice(0, 2);
    if (D.DEP_CENTROIDS[dep]) return D.DEP_CENTROIDS[dep];
    return null; // CP inconnu → pas de filtre distance
  }

  /* ---------- temps ---------- */
  function ago(ms) {
    const m = Math.round((Date.now() - ms) / 60000);
    if (m < 1) return "à l'instant";
    if (m < 60) return `il y a ${m} min`;
    const h = Math.round(m / 60);
    return `il y a ${h} h`;
  }

  /* ---------- compute ---------- */
  function compute() {
    const origin = originFromCP(state.cp);
    let rows = D.INVENTORY.filter((rec) => rec.productId === state.product).map((rec) => {
      const store = storeMap[rec.storeId];
      const dist = origin && store.lat != null ? haversine(origin[0], origin[1], store.lat, store.lon) : null;
      return { ...rec, store, retailer: retailerMap[store.retailer], dist };
    });

    rows = rows.filter((r) => {
      if (!state.retailers.has(r.store.retailer)) return false;
      if (state.inStockOnly && r.status === "out_of_stock") return false;
      if (state.maxPrice && r.price && r.price > Number(state.maxPrice)) return false;
      if (state.fulfillment !== "all" && !r.fulfillment.includes(state.fulfillment)) return false;
      if (origin && !r.store.online && r.dist != null && r.dist > state.radius) return false;
      return true;
    });

    const statusRank = { in_stock: 0, low_stock: 1, out_of_stock: 2 };
    rows.sort((a, b) => {
      if (state.sort === "distance") {
        const ad = a.dist == null ? 1e9 : a.dist, bd = b.dist == null ? 1e9 : b.dist;
        return ad - bd || statusRank[a.status] - statusRank[b.status];
      }
      if (state.sort === "price") return (a.price ?? 1e9) - (b.price ?? 1e9);
      if (state.sort === "fresh") return b.updatedAt - a.updatedAt;
      // "availability"
      return statusRank[a.status] - statusRank[b.status] || (a.dist ?? 1e9) - (b.dist ?? 1e9);
    });
    return rows;
  }

  /* ---------- render ---------- */
  const RELLABEL = { high: "Fiabilité élevée", medium: "Fiabilité moyenne", low: "Donnée plus ancienne" };
  const STBADGE = {
    in_stock: ['in', "En stock"],
    low_stock: ['low', "Stock faible"],
    out_of_stock: ['out', "Rupture"],
  };

  function rowHTML(r) {
    const [cls, label] = STBADGE[r.status];
    const rb = r.retailer;
    const initials = rb.name.split(" ").map((w) => w[0]).join("").slice(0, 3).toUpperCase();
    const priceHTML = r.price
      ? `<div class="v">${r.price.toFixed(2).replace(".", ",")} €</div>`
      : `<div class="v na">—</div>`;
    const fulfil = r.fulfillment.map((f) => `<span class="tag">${f === "pickup" ? "Retrait" : "Livraison"}</span>`).join("");
    const distHTML = r.store.online ? "En ligne" : r.dist != null ? `${r.dist} km` : "—";
    const qtyHTML = r.status === "in_stock" ? `<span class="qty">${r.qty} dispo.</span>`
      : r.status === "low_stock" ? `<span class="qty">${r.qty} restant${r.qty > 1 ? "s" : ""}</span>` : "";
    const actLabel = r.status === "out_of_stock" ? "M'alerter" : "Voir l'offre";
    const actClass = r.status === "out_of_stock" ? "btn-light" : "btn-primary";
    const actHref = r.status === "out_of_stock" ? "alerte.html" : "#";
    return `<div class="row">
      <div class="store">
        <div class="retailer-badge" style="background:${rb.color}">${initials}</div>
        <div class="meta">
          <div class="name">${r.store.name}</div>
          <div class="sub">${r.store.online ? "Vente en ligne" : r.store.city + " · " + r.store.cp} · <span class="dist">${distHTML}</span></div>
        </div>
      </div>
      <div class="status-cell">
        <span class="badge ${cls}"><span class="dot"></span>${label}</span>
        ${qtyHTML}
      </div>
      <div class="trust">
        <span class="rel ${r.reliability}">
          <span class="bars"><i></i><i></i><i></i></span>${RELLABEL[r.reliability]}
        </span>
        <span class="when">Maj ${ago(r.updatedAt)} · ${r.source === "api" ? "API" : r.source === "affiliate" ? "Flux partenaire" : "Web"}</span>
      </div>
      <div class="price">
        ${priceHTML}
        <div class="fulfil">${fulfil}</div>
      </div>
      <div class="act">
        <a class="btn btn-sm ${actClass}" href="${actHref}">${actLabel}</a>
      </div>
    </div>`;
  }

  function render() {
    const rows = compute();
    const container = byId("results");
    const countEl = byId("result-count");
    const inStock = rows.filter((r) => r.status !== "out_of_stock").length;
    countEl.innerHTML = `<b>${rows.length}</b> résultat${rows.length > 1 ? "s" : ""} · <b style="color:var(--ok)">${inStock}</b> avec du stock`;

    if (!rows.length) {
      container.innerHTML = `<div class="empty"><div class="big">📡</div>
        Aucun magasin ne correspond à ces critères.<br>Élargissez le rayon ou désactivez « uniquement en stock ».</div>`;
      return;
    }

    const shown = state.isPaid ? rows : rows.slice(0, FREE_RESULT_LIMIT);
    let html = shown.map(rowHTML).join("");
    if (!state.isPaid && rows.length > FREE_RESULT_LIMIT) {
      html += `<div class="locked">
        🔒 <span><b>${rows.length - FREE_RESULT_LIMIT} autres magasins</b> et la surveillance temps réel sont réservés au Pass.</span>
        <a class="btn btn-sm btn-primary" href="#tarifs">Débloquer pour 1,99 €</a>
      </div>`;
    }
    container.innerHTML = html;
  }

  /* ---------- market strip ---------- */
  function renderStats() {
    const s = D.marketStats();
    if (byId("stat-monitored")) byId("stat-monitored").textContent = s.monitored.toLocaleString("fr-FR");
    if (byId("stat-instock")) byId("stat-instock").textContent = s.inStockStores;
    if (byId("stat-sold")) byId("stat-sold").textContent = s.sold24h;
    if (byId("stat-update")) byId("stat-update").textContent = ago(s.lastUpdate);
  }

  /* ---------- wiring ---------- */
  function buildRetailerChips() {
    const wrap = byId("retailer-chips");
    if (!wrap) return;
    wrap.innerHTML = D.RETAILERS.map((r) =>
      `<span class="chip on" data-rid="${r.id}">${r.name}</span>`).join("");
    wrap.querySelectorAll(".chip").forEach((c) => {
      c.addEventListener("click", () => {
        const id = c.dataset.rid;
        if (state.retailers.has(id)) { state.retailers.delete(id); c.classList.remove("on"); }
        else { state.retailers.add(id); c.classList.add("on"); }
        render();
      });
    });
  }

  function buildProductSelect() {
    const sel = byId("f-product");
    if (!sel) return;
    sel.innerHTML = D.PRODUCTS.map((p) =>
      `<option value="${p.id}">${p.img} ${p.name}${p.btu ? " · " + p.btu + " BTU" : ""}</option>`).join("");
    sel.value = state.product;
  }

  function init() {
    buildProductSelect();
    buildRetailerChips();
    renderStats();
    render();
    setInterval(renderStats, 60000);

    const bind = (id, ev, fn) => { const el = byId(id); if (el) el.addEventListener(ev, fn); };
    bind("f-product", "change", (e) => { state.product = e.target.value; render(); });
    bind("f-cp", "input", (e) => { state.cp = e.target.value; render(); });
    bind("f-radius", "change", (e) => { state.radius = Number(e.target.value); render(); });
    bind("f-price", "input", (e) => { state.maxPrice = e.target.value; render(); });
    bind("f-fulfil", "change", (e) => { state.fulfillment = e.target.value; render(); });
    bind("f-instock", "change", (e) => { state.inStockOnly = e.target.checked; render(); });
    bind("f-sort", "change", (e) => { state.sort = e.target.value; render(); });
    bind("demo-paid", "change", (e) => { state.isPaid = e.target.checked; render(); });

    // mobile nav
    const tg = byId("nav-toggle");
    if (tg) tg.addEventListener("click", () => byId("nav-links").classList.toggle("open"));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
