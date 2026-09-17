/* The SWATH Vault: listing page behaviour (search, filters, sort, paging). */
(function () {
  const S = window.SWATH || {};
  const PAGE = 48;
  const $ = (id) => document.getElementById(id);
  const grid = $("grid"), countEl = $("count"), moreBtn = $("more"), emptyEl = $("empty");
  const qEl = $("q"), subEl = $("sub"), brandEl = $("brand"), priceEl = $("price"), sortEl = $("sort");
  let items = [], shown = 0, current = [];
  const state = { q: "", cat: "", sub: "", brand: "", price: "", sort: "rel" };

  const money = (n) => "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const esc = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const href = (it) => "p/" + it.u + "/" + (S.sfx || "");

  function readUrl() {
    const u = new URLSearchParams(location.search);
    for (const k of Object.keys(state)) if (u.has(k)) state[k] = u.get(k);
    if (!state.sort) state.sort = "rel";
  }
  function writeUrl() {
    const u = new URLSearchParams();
    for (const k of Object.keys(state)) if (state[k] && !(k === "sort" && state[k] === "rel")) u.set(k, state[k]);
    const qs = u.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  }

  function syncControls() {
    qEl.value = state.q;
    document.querySelectorAll("#cat-chips .chip").forEach((c) => c.classList.toggle("chip-on", c.dataset.cat === state.cat));
    fillSubs();
    subEl.value = state.sub;
    brandEl.value = state.brand;
    priceEl.value = state.price;
    sortEl.value = state.sort;
  }

  function fillSubs() {
    const subs = state.cat ? (S.categories[state.cat] || []) : [];
    subEl.innerHTML = '<option value="">All types</option>' + subs.filter((s) => s && s !== state.cat).map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    subEl.disabled = subs.length <= 1;
    if (!subs.includes(state.sub)) state.sub = "";
  }

  function score(it, terms) {
    const hay = (it.n + " " + it.b + " " + it.s + " " + it.c + " " + it.sc + " " + (it.m || []).join(" ")).toLowerCase();
    let sc = 0;
    for (const t of terms) {
      if (!hay.includes(t)) return -1;
      if (it.n.toLowerCase().startsWith(t)) sc += 6;
      else if (it.n.toLowerCase().includes(t)) sc += 4;
      else if ((it.b || "").toLowerCase().includes(t)) sc += 3;
      else if (it.s.toLowerCase().includes(t)) sc += 5;
      else sc += 1;
    }
    return sc;
  }

  function apply() {
    const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    let [lo, hi] = [0, Infinity];
    if (state.price) { const [a, b] = state.price.split("-"); lo = +a || 0; hi = b === "" ? Infinity : +b; }
    current = [];
    for (const it of items) {
      if (state.cat && it.c !== state.cat) continue;
      if (state.sub && it.sc !== state.sub) continue;
      if (state.brand && it.b !== state.brand) continue;
      if (state.price && (it.p == null || it.p < lo || it.p >= hi)) continue;
      let sc = 0;
      if (terms.length) { sc = score(it, terms); if (sc < 0) continue; }
      current.push([sc, it]);
    }
    if (state.sort === "name") current.sort((a, b) => a[1].n.localeCompare(b[1].n));
    else if (state.sort === "plo") current.sort((a, b) => (a[1].p ?? 1e9) - (b[1].p ?? 1e9));
    else if (state.sort === "phi") current.sort((a, b) => (b[1].p ?? -1) - (a[1].p ?? -1));
    else if (terms.length) current.sort((a, b) => b[0] - a[0]);
    shown = 0;
    grid.innerHTML = "";
    renderMore();
    countEl.textContent = current.length === items.length ? `${items.length} pieces` : `${current.length} of ${items.length} pieces`;
    emptyEl.hidden = current.length > 0;
    writeUrl();
  }

  function card(it) {
    return `<a class="card" href="${href(it)}">
      <div class="card-img"><img loading="lazy" src="${esc(it.i)}" alt="${esc(it.n)}"></div>
      <div class="card-body">
        <p class="card-brand">${esc(it.b || it.sc || it.c)}</p>
        <h3 class="card-title">${esc(it.n)}</h3>
        ${it.p != null ? `<p class="card-price"><strong>${money(it.p)}</strong> <span class="muted">${esc((it.pl || "").replace("decorated, ", ""))}</span></p>` : ""}
        ${it.k > 1 ? `<p class="card-colors">${it.k} colors</p>` : ""}
      </div></a>`;
  }

  function renderMore() {
    const slice = current.slice(shown, shown + PAGE);
    grid.insertAdjacentHTML("beforeend", slice.map(([, it]) => card(it)).join(""));
    shown += slice.length;
    moreBtn.hidden = shown >= current.length;
  }

  // ---- events ---------------------------------------------------------- //
  let timer;
  qEl.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = qEl.value.trim(); apply(); }, 120); });
  $("clear-q").addEventListener("click", () => { qEl.value = ""; state.q = ""; apply(); qEl.focus(); });
  document.querySelectorAll("#cat-chips .chip").forEach((c) => c.addEventListener("click", () => {
    state.cat = c.dataset.cat; state.sub = "";
    document.querySelectorAll("#cat-chips .chip").forEach((x) => x.classList.toggle("chip-on", x === c));
    fillSubs(); subEl.value = ""; apply();
    document.getElementById("vault").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  subEl.addEventListener("change", () => { state.sub = subEl.value; apply(); });
  brandEl.addEventListener("change", () => { state.brand = brandEl.value; apply(); });
  priceEl.addEventListener("change", () => { state.price = priceEl.value; apply(); });
  sortEl.addEventListener("change", () => { state.sort = sortEl.value; apply(); });
  moreBtn.addEventListener("click", renderMore);
  $("reset").addEventListener("click", () => { Object.assign(state, { q: "", cat: "", sub: "", brand: "", price: "", sort: "rel" }); syncControls(); apply(); });

  readUrl();
  countEl.textContent = "Opening the Vault...";
  const inline = document.getElementById("vault-data");
  const load = inline ? Promise.resolve(JSON.parse(inline.textContent)) : fetch((S.root || "") + "data/index.json").then((r) => r.json());
  load.then((data) => {
    items = data;
    syncControls();
    apply();
    if (state.q || state.cat || state.sub || state.brand) document.getElementById("vault").scrollIntoView({ block: "start" });
  }).catch(() => { countEl.textContent = "The Vault could not load. Please refresh."; });
})();
