"""
Live stock for the Vault, from PCNA's PromoStandards Inventory 2.0.0 service.

Credentials come ONLY from environment variables, never from config.json or the repo:
    PCNA_PS_ID        PromoStandards production user id
    PCNA_PS_PASSWORD  PromoStandards production password
On GitHub these are repository secrets (Settings > Secrets and variables > Actions).

If the credentials are missing or the service is down, the build carries on: it falls
back to the last good cache/inventory.json when that is recent enough, and otherwise
simply publishes pages without a stock badge.
"""

import datetime as dt
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.sax.saxutils import escape

NS_SHARED = "http://www.promostandards.org/WSDL/Inventory/2.0.0/SharedObjects/"

ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ns="http://www.promostandards.org/WSDL/Inventory/2.0.0/" '
    f'xmlns:shar="{NS_SHARED}"><soapenv:Header/><soapenv:Body>'
    "<ns:GetInventoryLevelsRequest><shar:wsVersion>2.0.0</shar:wsVersion>"
    "<shar:id>{id}</shar:id><shar:password>{pw}</shar:password>"
    "<shar:productId>{pid}</shar:productId></ns:GetInventoryLevelsRequest>"
    "</soapenv:Body></soapenv:Envelope>"
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name, default=""):
    c = _child(el, name)
    return (c.text or "").strip() if c is not None and c.text else default


def _qty(el):
    """Read a <quantityAvailable>/<Quantity>/<value> style block as an int."""
    if el is None:
        return 0
    for v in el.iter():
        if _local(v.tag) == "value" and v.text:
            try:
                return int(float(v.text))
            except ValueError:
                return 0
    return 0


def normalize_color(name: str) -> str:
    name = re.sub(r"\s*\(\s*[^)]*\)\s*$", "", name or "")
    return name.strip().lower()


def parse_response(xml_text: str) -> dict:
    """Return {"parts": [...], "error": str|None} for one product."""
    root = ET.fromstring(xml_text)
    parts, errors = [], []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "PartInventory":
            future = []
            for fa in el.iter():
                if _local(fa.tag) == "FutureAvailability":
                    q = _qty(_child(fa, "Quantity"))
                    on = _text(fa, "availableOn")[:10]
                    if q > 0 and on:
                        future.append({"qty": q, "on": on})
            parts.append({
                "part": _text(el, "partId"),
                "color": _text(el, "partColor"),
                "size": _text(el, "labelSize"),
                "main": _text(el, "mainPart", "true").lower() != "false",
                "qty": _qty(_child(el, "quantityAvailable")),
                "made_to_order": _text(el, "manufacturedItem").lower() == "true"
                                 or _text(el, "buyToOrder").lower() == "true",
                "future": sorted(future, key=lambda f: f["on"]),
            })
        elif tag == "ServiceMessage":
            errors.append(f'{_text(el, "code")} {_text(el, "description")}'.strip())
    return {"parts": parts, "error": "; ".join(errors) if errors and not parts else None}


def fetch_one(endpoint, user, pw, style, timeout=45, retries=2):
    body = ENVELOPE.format(id=escape(user), pw=escape(pw), pid=escape(style)).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(endpoint, data=body, headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "getInventoryLevels",
                "User-Agent": "swath-vault-builder/1.0",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return parse_response(r.read().decode("utf-8", "replace"))
        except Exception as e:  # network blip or 5xx: back off and retry
            last = e
            time.sleep(2 * (attempt + 1))
    return {"parts": [], "error": f"request failed: {last}"}


def summarize(parts: list, program_qty: int) -> dict | None:
    """Collapse part-level stock into what the page shows."""
    main = [p for p in parts if p["main"]] or parts
    if not main:
        return None
    total = sum(p["qty"] for p in main)
    by_color, by_part = {}, {}
    for p in main:
        key = normalize_color(p["color"])
        by_color[key] = by_color.get(key, 0) + p["qty"]
        by_part[p["part"]] = by_part.get(p["part"], 0) + p["qty"]
    future = sorted((f for p in main for f in p["future"]), key=lambda f: f["on"])
    made_to_order = all(p["made_to_order"] for p in main)
    if made_to_order:
        status = "mto"
    elif total >= program_qty:
        status = "in"
    elif total > 0:
        status = "low"
    else:
        status = "out"
    return {
        "status": status,
        "total": total,
        "by_color": by_color,
        "by_part": by_part,
        "next": future[0] if future else None,
    }


def load_inventory(styles, cfg_inv: dict, cache_dir: Path, program_qty: int):
    """Return ({style: summary}, fetched_iso). Never raises; logs and degrades instead."""
    if not cfg_inv.get("enabled", False):
        print("  inventory: disabled in config.json")
        return {}, ""
    cache_file = cache_dir / "inventory.json"
    max_age_h = float(cfg_inv.get("max_cache_hours", 48))

    def from_cache(reason):
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            age_h = (time.time() - data.get("fetched_ts", 0)) / 3600
            if age_h <= max_age_h:
                print(f"  inventory: {reason}; using cached stock ({age_h:.1f} h old)")
                return data["styles"], data.get("fetched", "")
        print(f"  inventory: {reason}; no recent cache, publishing without stock")
        return {}, ""

    user, pw = os.environ.get("PCNA_PS_ID", ""), os.environ.get("PCNA_PS_PASSWORD", "")
    if not user or not pw:
        return from_cache("PCNA_PS_ID / PCNA_PS_PASSWORD not set")

    endpoint = cfg_inv["endpoint"]
    workers = int(cfg_inv.get("workers", 6))
    styles = list(dict.fromkeys(styles))

    # Quick login check: one call first, so a bad password stops the run in seconds
    # instead of failing 2,600 times.
    if styles:
        probe = fetch_one(endpoint, user, pw, styles[0])
        err = (probe.get("error") or "").lower()
        if "105" in err or "invalid username" in err or "password" in err:
            print(f"  inventory: LOGIN REJECTED by PCNA ({probe['error']}).")
            print("    Re-enter the PCNA_PS_ID / PCNA_PS_PASSWORD repository secrets.")
            return from_cache("login rejected")
    print(f"  inventory: checking {len(styles)} styles ({workers} at a time)")
    t0 = time.time()
    out, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, endpoint, user, pw, s): s for s in styles}
        for i, f in enumerate(as_completed(futs), 1):
            s = futs[f]
            res = f.result()
            if res["error"]:
                errors[s] = res["error"]
            summ = summarize(res["parts"], program_qty)
            if summ:
                out[s] = summ
            if i % 250 == 0:
                print(f"    {i}/{len(styles)}")
    secs = time.time() - t0
    print(f"  inventory: {len(out)} styles with stock data, {len(errors)} errors, {secs:.0f}s")
    if errors:
        sample = list(errors.items())[:5]
        print("    sample errors: " + " | ".join(f"{k}: {v}" for k, v in sample))

    # A mostly failed run (bad password, outage) must not wipe good cached data.
    if styles and len(out) < 0.5 * len(styles):
        return from_cache(f"only {len(out)} of {len(styles)} styles answered")

    fetched = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    cache_dir.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps({"fetched": fetched, "fetched_ts": time.time(),
                                      "styles": out}), encoding="utf-8")
    return out, fetched
