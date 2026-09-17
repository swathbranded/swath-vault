/* The SWATH Vault: product page behaviour (gallery + colour swatches). */
(function () {
  function bind() {
    const main = document.getElementById("main-img");
    if (!main) return;
    const swap = (src, el, group) => {
      if (!src) return;
      main.src = src;
      document.querySelectorAll(group).forEach((x) => x.classList.remove("thumb-on", "swatch-on"));
      if (el) el.classList.add(group === ".thumb" ? "thumb-on" : "swatch-on");
    };
    document.querySelectorAll(".thumb").forEach((t) => t.addEventListener("click", () => swap(t.dataset.src, t, ".thumb")));
    document.querySelectorAll(".swatch").forEach((s) => s.addEventListener("click", () => swap(s.dataset.src, s, ".swatch")));
  }
  if (document.getElementById("main-img")) bind();
  else document.addEventListener("swath:rendered", bind);
})();
