/* The SWATH Vault: listing page behaviour (search, filters, sort, paging). */
(function () {
  const S = window.SWATH || {};
  const PAGE = 48;
  const $ = (id) => document.getElementById(id);
  const grid = $("grid"), countEl = $("count"), moreBtn = $("more"), emptyEl = $("empty");
  const qEl = $("q"), subEl = $("sub"), brandEl = $("brand"), priceEl = $("price"), sortEl = $("sort");
  let items = [], shown = 0, current = [];
  const state = { q: "", cat: "", sub: "", brand: "", price: "", stock: "", sort: "rel" };
  const stockEl = $("stock");

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
    if (stockEl) stockEl.checked = state.stock === "1";
  }

  function fillSubs() {
    // With a category chosen: that category's types. With "All": every type, grouped by
    // category, and picking one also selects its category.
    const cats = S.categories || {};
    const typesOf = (c) => (cats[c] || []).filter((s) => s && s !== c);
    let html = '<option value="">All types</option>', any = false;
    if (state.cat) {
      const subs = typesOf(state.cat);
      any = subs.length > 0;
      html += subs.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    } else {
      for (const c of Object.keys(cats)) {
        const subs = typesOf(c);
        if (!subs.length) continue;
        any = true;
        html += `<optgroup label="${esc(c)}">` + subs.map((s) => `<option value="${esc(c + "||" + s)}">${esc(s)}</option>`).join("") + "</optgroup>";
      }
    }
    subEl.innerHTML = html;
    subEl.disabled = !any;
    if (state.cat && !typesOf(state.cat).includes(state.sub)) state.sub = "";
  }

  // Search synonyms: typing any word in a group finds items matching any other word in it.
  const SYN = [
    ["hat", "hats", "cap", "caps", "ballcap", "beanie", "beanies", "toque", "visor", "boonie", "snapback", "trucker", "headwear"],
    ["bottle", "bottles", "flask"],
    ["tumbler", "tumblers", "quencher"],
    ["mug", "mugs", "cup", "cups"],
    ["backpack", "backpacks", "daypack", "rucksack"],
    ["tote", "totes"],
    ["duffel", "duffle", "duffels"],
    ["tee", "tees", "t-shirt", "tshirt", "t-shirts"],
    ["hoodie", "hoodies", "hoody", "hooded"],
    ["jacket", "jackets", "outerwear"],
    ["notebook", "notebooks", "journal", "journals"],
    ["padfolio", "padfolios", "portfolio"],
    ["pen", "pens"],
    ["charger", "chargers", "charging", "powerbank", "power bank"],
    ["blanket", "blankets", "throw", "throws"],
    ["cooler", "coolers"],
    ["umbrella", "umbrellas"],
    ["speaker", "speakers"],
    ["earbuds", "headphones"],
    ["glove", "gloves", "mitt", "mitts", "mittens"],
    ["keychain", "keychains", "keyring"],
    ["towel", "towels"],
  ];
  const SYN_MAP = {};
  for (const g of SYN) for (const w of g) (SYN_MAP[w] = SYN_MAP[w] || new Set(), g.forEach((x) => SYN_MAP[w].add(x)));
  const expand = (t) => [...(SYN_MAP[t] || [t])];
  // Short terms (3 letters or fewer) must match whole words, so "hat" never hits "Manhattan".
  const hitsIn = (hay, w) => (w.length <= 3 ? new RegExp("(^|[^a-z0-9])" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "($|[^a-z0-9])").test(hay) : hay.includes(w));

  function score(it, terms) {
    const name = it.n.toLowerCase(), brand = (it.b || "").toLowerCase(), style = it.s.toLowerCase();
    const type = ((it.c || "") + " " + (it.sc || "")).toLowerCase();
    const hay = name + " " + brand + " " + style + " " + type + " " + (it.m || []).join(" ").toLowerCase();
    let sc = 0;
    for (const t of terms) {
      const alts = expand(t);
      let best = 0;
      for (const w of alts) {
        if (!hitsIn(hay, w)) continue;
        const exact = w === t;
        let s = 1;
        if (hitsIn(name, w)) s = name.startsWith(w) ? 10 : 8;   // the word is in the product name
        else if (hitsIn(style, w)) s = 8;                          // or it is the style number
        else if (hitsIn(brand, w)) s = 5;
        else if (hitsIn(type, w)) s = 4;                           // only the category/type matches
        if (!exact) s = Math.max(1, s - 3);                        // synonym hits rank below exact hits
        if (s > best) best = s;
      }
      if (!best) return -1;
      sc += best;
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
      if (state.stock === "1" && it.st !== "in") continue;
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
    const num = (n) => n.toLocaleString("en-US");
    countEl.textContent = current.length === items.length ? `${num(items.length)} pieces` : `${num(current.length)} of ${num(items.length)} pieces`;
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
        ${it.st === "in" ? `<p class="card-stock">In stock</p>` : it.st === "low" ? `<p class="card-stock card-stock-low">Limited stock</p>` : ""}
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
  subEl.addEventListener("change", () => {
    const v = subEl.value;
    if (v.includes("||")) {
      const [c, sub] = v.split("||");
      state.cat = c; state.sub = sub;
      document.querySelectorAll("#cat-chips .chip").forEach((x) => x.classList.toggle("chip-on", x.dataset.cat === c));
      fillSubs(); subEl.value = sub;
    } else {
      state.sub = v;
    }
    apply();
  });
  brandEl.addEventListener("change", () => { state.brand = brandEl.value; apply(); });
  priceEl.addEventListener("change", () => { state.price = priceEl.value; apply(); });
  sortEl.addEventListener("change", () => { state.sort = sortEl.value; apply(); });
  if (stockEl) stockEl.addEventListener("change", () => { state.stock = stockEl.checked ? "1" : ""; apply(); });
  moreBtn.addEventListener("click", renderMore);
  $("reset").addEventListener("click", () => { Object.assign(state, { q: "", cat: "", sub: "", brand: "", price: "", stock: "", sort: "rel" }); syncControls(); apply(); });

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
