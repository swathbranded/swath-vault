"""
Product types (subcategories) for the Vault, from PCNA's PromoStandards Product Data 2.0.0
service. PCNA's public CSV feed repeats the category as the subcategory, so the Type filter
had nothing to show; this service carries the real ones (Drinkware > Tumblers, Bags >
Backpacks, Apparel > Polos and so on).

Uses the same PCNA_PS_ID / PCNA_PS_PASSWORD secrets as inventory.py. If they are missing or
the service is down, the build keeps the feed's categories and simply has no types.
"""

import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.sax.saxutils import escape

NS = "http://www.promostandards.org/WSDL/ProductDataService/2.0.0/"
ENDPOINT = "https://psproductdata200.pcna.online/psProductData.svc"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def fetch_one(user, pw, style, timeout=45, retries=2):
    body = (f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            f'xmlns:ns="{NS}" xmlns:shar="{NS}SharedObjects/"><soapenv:Header/><soapenv:Body>'
            f"<ns:GetProductRequest><shar:wsVersion>2.0.0</shar:wsVersion>"
            f"<shar:id>{escape(user)}</shar:id><shar:password>{escape(pw)}</shar:password>"
            f"<shar:localizationCountry>US</shar:localizationCountry>"
            f"<shar:localizationLanguage>en</shar:localizationLanguage>"
            f"<shar:productId>{escape(style)}</shar:productId></ns:GetProductRequest>"
            f"</soapenv:Body></soapenv:Envelope>").encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(ENDPOINT, data=body, headers={
                "Content-Type": "text/xml; charset=utf-8", "SOAPAction": "getProduct",
                "User-Agent": "swath-vault-builder/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                root = ET.fromstring(r.read())
            cats, errs = [], []
            for el in root.iter():
                tag = _local(el.tag)
                if tag == "ProductCategory":
                    f = {_local(k.tag): (k.text or "").strip() for k in el}
                    if f.get("category"):
                        cats.append((f["category"], f.get("subCategory", "")))
                elif tag in ("ServiceMessage", "ErrorMessage"):
                    f = {_local(k.tag): (k.text or "").strip() for k in el}
                    errs.append(f"{f.get('code','')} {f.get('description','')}".strip())
            return cats, ("; ".join(errs) if errs and not cats else None)
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    return [], f"request failed: {last}"


def load_types(styles, cfg: dict, cache_dir: Path):
    """Return {style: (category, subCategory)}. Never raises."""
    if not cfg.get("enabled", True):
        return {}
    user, pw = os.environ.get("PCNA_PS_ID", ""), os.environ.get("PCNA_PS_PASSWORD", "")
    cache_file = cache_dir / "product_types.json"
    max_age_h = float(cfg.get("max_cache_hours", 24 * 8))

    def from_cache(reason):
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            age_h = (time.time() - data.get("ts", 0)) / 3600
            if age_h <= max_age_h:
                print(f"  types: {reason}; using cached types ({age_h:.0f} h old)")
                return {k: tuple(v) for k, v in data["styles"].items()}
        print(f"  types: {reason}; no types this build")
        return {}

    if not user or not pw:
        return from_cache("PCNA_PS_ID / PCNA_PS_PASSWORD not set")
    styles = list(dict.fromkeys(styles))
    if not styles:
        return {}
    cats, err = fetch_one(user, pw, styles[0])
    if err and ("105" in err or "password" in err.lower()):
        return from_cache(f"login rejected ({err})")

    workers = int(cfg.get("workers", 6))
    print(f"  types: checking {len(styles)} styles ({workers} at a time)")
    t0, out, errors = time.time(), {}, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, user, pw, s): s for s in styles}
        for f in as_completed(futs):
            cats, err = f.result()
            if cats:
                out[futs[f]] = cats[0]
            elif err:
                errors += 1
    print(f"  types: {len(out)} styles typed, {errors} errors, {time.time()-t0:.0f}s")
    if len(out) < 0.5 * len(styles):
        return from_cache(f"only {len(out)} of {len(styles)} answered")
    cache_dir.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps({"ts": time.time(), "styles": out}), encoding="utf-8")
    return out
