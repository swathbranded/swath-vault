#!/usr/bin/env python3
"""
The SWATH Vault builder.

Downloads the four public PCNA product feeds, groups them by style, applies the
rules in config.json, and writes a static site to dist/.

Usage:
    python build.py            # build, re-using cached feeds if fresh
    python build.py --refresh  # force a fresh download of the feeds
    python build.py --limit 300  # quick build with only 300 styles (for testing)
"""

import argparse
import datetime as dt
import io
import json
import math
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from inventory import load_inventory, normalize_color

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
DIST = ROOT / "dist"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
ASSETS = ROOT / "assets"

# Media tags we never want to show as a product photo.
BAD_MEDIA_TAGS = {
    "Compliance", "General Conformity Certificate", "Drinkware Compliance",
    "Art Template", "Specs", "Product Specifications", "Instruction Manual",
    "Childrens Product Certificate", "Kid Friendly Report", "Package",
}


# --------------------------------------------------------------------------- #
# Feeds
# --------------------------------------------------------------------------- #

def fetch_feed(name: str, url: str, cache_hours: float, refresh: bool) -> Path:
    """Download a feed zip to cache/ unless a fresh copy is already there."""
    CACHE.mkdir(exist_ok=True)
    zpath = CACHE / f"{name}.zip"
    if zpath.exists() and not refresh:
        age_h = (time.time() - zpath.stat().st_mtime) / 3600
        if age_h < cache_hours:
            print(f"  {name}: using cached copy ({age_h:.1f} h old)")
            return zpath
    print(f"  {name}: downloading {url}")
    req = Request(url, headers={"User-Agent": "swath-vault-builder/1.0"})
    with urlopen(req, timeout=300) as r:
        data = r.read()
    zpath.write_bytes(data)
    print(f"  {name}: {len(data)/1e6:.1f} MB")
    return zpath


def read_zip_csv(zpath: Path, usecols=None, dtype=None) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        inner = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        with z.open(inner) as f:
            return pd.read_csv(io.BytesIO(f.read()), usecols=usecols, dtype=dtype,
                               low_memory=False)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def clean(v):
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    # House style: no em dashes anywhere on the site, including supplier copy.
    s = re.sub(r"\s*[—–]\s*", ", ", s)
    return s


SUPPLIER_WORDS = re.compile(r"\b(pcna|polyconcept|leeds?(world)?|leed's|trimark|bullet line|bulletline)\b", re.I)


def scrub_supplier(text: str) -> str:
    """Drop any sentence that names the supplier. SWATH sells curation, not its
    supply chain, so 'Leeds exclusive' style phrases never reach the page."""
    if not text or not SUPPLIER_WORDS.search(text):
        return text
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in parts if not SUPPLIER_WORDS.search(s)]
    out = " ".join(kept).strip()
    # A leading "Brand presents the X" sentence gets rewritten instead of dropped.
    if not out and text:
        out = SUPPLIER_WORDS.sub("", text)
        out = re.sub(r"^\s*presents the\s+", "", out, flags=re.I).strip()
    return out


def slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "item"


def money(x) -> str:
    return f"${x:,.2f}"


def first_nonempty(series):
    for v in series:
        c = clean(v)
        if c:
            return c
    return ""


def media_score(tags: set) -> int:
    """Higher is better. Blank front shots win; decorated shots are a fallback."""
    if tags & BAD_MEDIA_TAGS:
        return -1
    score = 0
    if "Front" in tags:
        score += 40
    if "Blank" in tags:
        score += 30
    if "Part Default Image" in tags:
        score += 20
    if "Product Default Image" in tags:
        score += 15
    if "Off Figure" in tags:
        score += 5
    if "Decorated" in tags:
        score -= 10
    if "Back" in tags:
        score -= 15
    if "Internal Use" in tags:
        score -= 3
    return score


def gallery_order(tags: set) -> int:
    """Ordering for the product-page gallery (lower first)."""
    if "Front" in tags and "Blank" in tags:
        return 0
    if "Part Default Image" in tags or "Product Default Image" in tags:
        return 1
    if "Lifestyle" in tags or "StillLife" in tags:
        return 2
    if "On Model" in tags:
        return 3
    if "Angle-Left" in tags or "Angle-Right" in tags or "Left-Side" in tags or "Right-Side" in tags:
        return 4
    if "Back" in tags:
        return 5
    if "Decorated" in tags:
        return 7
    return 6


# --------------------------------------------------------------------------- #
# Build the product model
# --------------------------------------------------------------------------- #

def build_products(cfg: dict, refresh: bool, limit: int | None) -> tuple[list[dict], dict]:
    feeds = cfg["feeds"]
    ch = cfg.get("cache_hours", 20)
    print("Fetching feeds")
    zp = fetch_feed("product", feeds["product"], ch, refresh)
    zpr = fetch_feed("pricing", feeds["pricing"], ch, refresh)
    zm = fetch_feed("media", feeds["media"], ch, refresh)
    zd = fetch_feed("decoration", feeds["decoration"], ch, refresh)

    print("Loading product data")
    prod = read_zip_csv(zp, dtype=str)
    prod.columns = [c.strip() for c in prod.columns]
    prod = prod.fillna("")

    print("Loading pricing")
    pricing = read_zip_csv(zpr, usecols=["SKU", "Style", "quantityMin", "price", "PriceDescription"],
                           dtype={"SKU": str, "Style": str, "PriceDescription": str})
    pricing = pricing[pricing.PriceDescription.isin(["USD-List-Decorated", "USD-List-Blank",
                                                     "USD-Net-Decorated", "USD-Net-Blank"])]
    pricing["quantityMin"] = pd.to_numeric(pricing.quantityMin, errors="coerce")
    pricing["price"] = pd.to_numeric(pricing.price, errors="coerce")
    pricing = pricing.dropna(subset=["quantityMin", "price"])

    print("Loading media")
    media = read_zip_csv(zm, usecols=["Style", "Sku", "Url", "ClassTypeName"], dtype=str).fillna("")
    # NOTE: in PCNA's media feed the columns are swapped: "Style" holds the SKU
    # (with colour suffix) and "Sku" holds the style number.
    media = media.rename(columns={"Style": "sku", "Sku": "style"})
    media = media[media.Url.str.contains("/t_PS_MEDIUM/", na=False)].copy()
    media["tags"] = media.ClassTypeName.apply(lambda s: set(t.strip() for t in s.split(",") if t.strip()))
    media["score"] = media.tags.apply(media_score)
    media = media[media.score >= 0]
    media["gorder"] = media.tags.apply(gallery_order)

    print("Loading decoration")
    deco = read_zip_csv(zd, usecols=["Style", "DecorationName", "MaxLength", "MaxHeight", "LocationName"],
                        dtype=str).fillna("")

    # Pre-group heavy tables by style for fast lookup.
    pricing_by_style = {k: v for k, v in pricing.groupby("Style")}
    media_by_style = {k: v for k, v in media.groupby("style")}
    deco_by_style = {k: v for k, v in deco.groupby("Style")}

    f = cfg["filters"]
    pc = cfg["pricing"]
    hidden_brands = {b.lower() for b in cfg.get("hidden_brand_names", [])}
    excl_kw = [k.lower() for k in f.get("exclude_name_keywords", [])]
    excl_cat = set(f.get("exclude_categories", []))
    excl_sub = set(f.get("exclude_subcategories", []))
    excl_brand = {b.lower() for b in f.get("exclude_brands", [])}
    excl_style = set(f.get("exclude_styles", []))

    stats = {"styles_in_feed": prod.PCNA_Style_Number.nunique(), "dropped": {}}

    def drop(reason):
        stats["dropped"][reason] = stats["dropped"].get(reason, 0) + 1

    products = []
    styles = list(prod.groupby("PCNA_Style_Number", sort=False))
    if limit:
        styles = styles[:limit]

    print(f"Building {len(styles)} styles")
    for style, g in styles:
        style = clean(style)
        if not style or style in excl_style:
            drop("excluded style")
            continue
        name = first_nonempty(g.ItemName) or first_nonempty(g.SeriesName)
        if not name:
            drop("no name")
            continue
        lname = name.lower()
        if any(k in lname for k in excl_kw):
            drop("excluded keyword")
            continue
        brand = first_nonempty(g.Brand)
        if brand.lower() in excl_brand:
            drop("excluded brand")
            continue
        if brand.lower() in hidden_brands:
            brand = ""
        category = first_nonempty(g.CategoryWeb) or "Other"
        subcategory = first_nonempty(g.SubCategoryWeb) or category
        if category in excl_cat or subcategory in excl_sub:
            drop("excluded category")
            continue

        # ---- pricing ----------------------------------------------------- #
        pr = pricing_by_style.get(style)
        dec_tiers, blank_tiers = [], []
        if pr is not None:
            def tiers(desc):
                sub = pr[pr.PriceDescription == desc]
                if sub.empty:
                    return []
                t = sub.groupby("quantityMin")["price"].min().sort_index()
                return [(int(q), float(p)) for q, p in t.items() if p > 0]
            if pc["mode"] == "markup":
                margin = float(pc.get("markup_margin", 0.4))
                dec_tiers = [(q, round(p / (1 - margin), 2)) for q, p in tiers("USD-Net-Decorated")]
                blank_tiers = [(q, round(p / (1 - margin), 2)) for q, p in tiers("USD-Net-Blank")]
            else:
                dec_tiers = tiers("USD-List-Decorated")
                blank_tiers = tiers("USD-List-Blank")
        tiers_for_from = dec_tiers or blank_tiers
        if not tiers_for_from:
            if f.get("require_pricing", True):
                drop("no pricing")
                continue
            from_price = None
        else:
            from_price = min(p for _, p in tiers_for_from)
        if from_price is not None and from_price < float(f.get("min_from_price", 0)):
            drop("below price floor")
            continue
        program_qty = int(pc.get("program_quantity", 100))
        program_price = None
        if dec_tiers:
            eligible = [(q, p) for q, p in dec_tiers if q <= program_qty]
            program_price = (eligible[-1] if eligible else dec_tiers[0])[1]
        min_qty = tiers_for_from[0][0] if tiers_for_from else None
        # What the cards and the price box lead with: the decorated list price
        # at the program quantity (or the blank price when nothing decorated exists).
        if program_price is not None:
            display_price, display_label = program_price, f"decorated, at {program_qty} pieces"
        else:
            display_price, display_label = from_price, "blank"
        low_qty, low_price = None, None
        if tiers_for_from:
            lq, lp = min(tiers_for_from, key=lambda t: (t[1], -t[0]))
            if lp < display_price - 0.005:
                low_qty, low_price = lq, lp

        # ---- colours and sizes ------------------------------------------ #
        colors, seen = [], set()
        for _, row in g.iterrows():
            c = clean(row.MARKET_COLORS)
            c = re.sub(r"\s*\(\s*[^)]*\)\s*$", "", c)  # strip "(BK)" style codes
            sku = clean(row.PCNA_SKU_Number)
            if c and c.lower() not in seen:
                seen.add(c.lower())
                colors.append({"name": c, "sku": sku, "skus": [sku]})
            elif c:
                col = next(x for x in colors if x["name"].lower() == c.lower())
                if sku and sku not in col["skus"]:
                    col["skus"].append(sku)
        size_order = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL", "6XL",
                      "XS/S", "S/M", "M/L", "L/XL", "XL/2XL", "2XL/3XL", "O/S"]
        sizes = sorted({clean(s) for s in g.Product_Size if clean(s) not in ("", "0")},
                       key=lambda s: (size_order.index(s) if s in size_order else 99, s))

        # ---- media ------------------------------------------------------- #
        md = media_by_style.get(style)
        main_img, gallery, color_imgs = "", [], {}
        if md is not None and not md.empty:
            best = md.sort_values(["score"], ascending=False).iloc[0]
            main_img = best.Url
            # Per-colour thumbnail: the best-scoring image for each SKU.
            for sku, sg in md.groupby("sku"):
                b = sg.sort_values("score", ascending=False).iloc[0]
                color_imgs[sku] = b.Url
            # Gallery for the default colour's SKU, then a few from other angles.
            default_sku = best.sku
            gsub = md[md.sku == default_sku].sort_values(["gorder", "score"], ascending=[True, False])
            urls = []
            for u in gsub.Url:
                if u not in urls:
                    urls.append(u)
            gallery = urls[:6]
        if not main_img and f.get("require_image", True):
            drop("no image")
            continue
        for c in colors:
            c["img"] = color_imgs.get(c["sku"], "")

        # ---- decoration -------------------------------------------------- #
        dd = deco_by_style.get(style)
        decoration = []
        if dd is not None and not dd.empty:
            for dname, dg in dd.groupby("DecorationName"):
                dname = clean(dname)
                if not dname:
                    continue
                L = pd.to_numeric(dg.MaxLength, errors="coerce").max()
                H = pd.to_numeric(dg.MaxHeight, errors="coerce").max()
                locs = []
                for loc in dg.LocationName:
                    loc = clean(loc)
                    if loc and loc not in locs:
                        locs.append(loc)
                area = ""
                if pd.notna(L) and pd.notna(H) and L > 0 and H > 0:
                    area = f'up to {L:g}" x {H:g}"'
                decoration.append({"method": dname, "area": area, "locations": locs[:4],
                                   "location_count": len(locs)})
            decoration.sort(key=lambda d: d["method"])

        dims = first_nonempty(g.Product_Dimensions)
        if re.fullmatch(r"0(\.0+)? H x 0(\.0+)? W x 0(\.0+)? L", dims or ""):
            dims = ""
        weight = first_nonempty(g.Product_Weight)
        if weight in ("0 LB", "0", "0.00 LB"):
            weight = ""

        products.append({
            "style": style,
            "slug": slugify(style),
            "name": name,
            "brand": brand,
            "category": category,
            "subcategory": subcategory,
            "description": scrub_supplier(first_nonempty(g.Description)),
            "materials": scrub_supplier(first_nonempty(g.MaterialsDescription)),
            "dimensions": dims,
            "weight": weight,
            "packaging": scrub_supplier(first_nonempty(g.PackagingDetails)),
            "colors": colors,
            "sizes": sizes,
            "image": main_img,
            "image_large": main_img.replace("/t_PS_MEDIUM/", "/t_PS_EXTRALARGE2/"),
            "gallery": [u.replace("/t_PS_MEDIUM/", "/t_PS_EXTRALARGE2/") for u in gallery],
            "from_price": from_price,
            "program_price": program_price,
            "display_price": display_price,
            "display_label": display_label,
            "low_price": low_price,
            "low_qty": low_qty,
            "min_qty": min_qty,
            "decorated_tiers": dec_tiers,
            "blank_tiers": blank_tiers,
            "decoration": decoration,
        })

    stats["styles_published"] = len(products)

    # ---- live stock (PromoStandards Inventory) ------------------------------ #
    print("Checking live stock")
    inv, fetched = load_inventory([p["style"] for p in products], cfg.get("inventory", {}),
                                  CACHE, int(pc.get("program_quantity", 100)))
    stats["inventory_checked"] = fetched
    stats["styles_with_stock"] = 0
    for p in products:
        st = inv.get(p["style"])
        p["stock"] = st
        if st:
            stats["styles_with_stock"] += 1
            by_part = st.get("by_part", {})
            for c in p["colors"]:
                # Match on SKU first (reliable); fall back to the colour name.
                hits = [by_part[k] for k in c["skus"] if k in by_part]
                c["qty"] = sum(hits) if hits else st["by_color"].get(normalize_color(c["name"]))
            if st.get("next"):
                st["next_label"] = dt.date.fromisoformat(st["next"]["on"]).strftime("%b %d").replace(" 0", " ")
    return products, stats


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_site(cfg: dict, products: list[dict], stats: dict, local: bool = False):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(["html"]))
    env.filters["money"] = money
    built = dt.datetime.now().strftime("%B %d, %Y")
    stock_date = ""
    if stats.get("inventory_checked"):
        stock_date = dt.datetime.fromisoformat(stats["inventory_checked"]).astimezone(
            dt.timezone(dt.timedelta(hours=-6))).strftime("%b %d").replace(" 0", " ")

    by_style = {p["style"]: p for p in products}
    by_sub = {}
    for p in products:
        by_sub.setdefault((p["category"], p["subcategory"]), []).append(p)

    featured = [by_style[s] for s in cfg.get("featured_styles", []) if s in by_style]
    missing = [s for s in cfg.get("featured_styles", []) if s not in by_style]
    if missing:
        print(f"  note: featured styles not in catalog: {', '.join(missing)}")

    categories = {}
    for p in products:
        categories.setdefault(p["category"], set()).add(p["subcategory"])
    categories = {k: sorted(v) for k, v in sorted(categories.items())}
    brands = sorted({p["brand"] for p in products if p["brand"]})

    # Default listing order: recognisable brands first, and within each group the
    # items closest to the "spotlight" price point (the sweet spot for a
    # premium program) come first.
    spot = float(cfg["pricing"].get("spotlight_price", 45))
    products = sorted(products, key=lambda p: (0 if p["brand"] else 1, abs((p["display_price"] or 0) - spot), p["name"]))

    # Lightweight search index for the listing page.
    index = [{
        "s": p["style"], "u": p["slug"], "n": p["name"], "b": p["brand"],
        "c": p["category"], "sc": p["subcategory"],
        "p": p["display_price"], "pl": p["display_label"], "q": p["min_qty"],
        "i": p["image"], "k": len(p["colors"]),
        "m": [d["method"] for d in p["decoration"]][:6],
        "st": (p["stock"] or {}).get("status", ""),
    } for p in products]

    # --local writes links that end in index.html so the folder can be opened
    # straight from disk (double-click index.html) without a web server.
    sfx = "index.html" if local else ""
    common = dict(cfg=cfg, built=built, stock_date=stock_date, categories=categories, brands=brands,
                  featured=featured, count=len(products), stats=stats, sfx=sfx)

    def write(path: Path, html: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    def copy_static(dest: Path):
        (dest / "assets").mkdir(parents=True, exist_ok=True)
        for src in ASSETS.iterdir():
            if src.is_file():
                shutil.copy2(src, dest / "assets" / src.name)
        for src in STATIC.iterdir():
            if src.is_file():
                shutil.copy2(src, dest / "assets" / src.name)

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    copy_static(DIST)
    (DIST / "data").mkdir()
    (DIST / "data" / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")

    tpl_index = env.get_template("index.html")
    tpl_product = env.get_template("product.html")
    write(DIST / "index.html", tpl_index.render(index_json=json.dumps(index, separators=(",", ":")), **common))
    write(DIST / "404.html", env.get_template("404.html").render(**common))

    print(f"  writing {len(products)} product pages")
    for p in products:
        related = [r for r in by_sub.get((p["category"], p["subcategory"]), []) if r["style"] != p["style"]][:8]
        write(DIST / "p" / p["slug"] / "index.html",
              tpl_product.render(p=p, related=related, **common))

    # sitemap, robots, CNAME
    base = cfg["site_url"].rstrip("/")
    urls = [f"{base}/"] + [f"{base}/p/{p['slug']}/" for p in products]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"<url><loc>{u}</loc></url>" for u in urls]
    sm.append("</urlset>")
    (DIST / "sitemap.xml").write_text("\n".join(sm), encoding="utf-8")
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    if cfg.get("custom_domain"):
        (DIST / "CNAME").write_text(cfg["custom_domain"] + "\n", encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    (DIST / "build-info.json").write_text(json.dumps({"built": built, **stats}, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="force re-download of the feeds")
    ap.add_argument("--limit", type=int, default=None, help="only build the first N styles")
    ap.add_argument("--local", action="store_true", help="links end in index.html so dist/ works opened from disk")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    t0 = time.time()
    products, stats = build_products(cfg, args.refresh, args.limit)
    print(f"Built {stats['styles_published']} of {stats['styles_in_feed']} styles; dropped: {stats['dropped']}")
    print("Rendering")
    render_site(cfg, products, stats, local=args.local)
    print(f"Done in {time.time()-t0:.0f}s. Output in {DIST}")


if __name__ == "__main__":
    main()
