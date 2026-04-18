"""Short display names + B2B flags for retail / gas networks.

Network identifiers as stored in the API are inconsistent: some are slugs
('PROFI', 'KAUFLAND'), others are barcodes ('5940475006709' = Carrefour),
and the human-readable names are verbose and inconsistent in length
('MEGA IMAGE SRL', 'LIDL DISCOUNT SRL'). For UI consumption we want a single
short label per network.

The mapping lives in `config/networks.json` — edit there to change how a
network appears across the site. This module loads the file once and exposes
small lookup helpers.
"""

import json
import os

_CFG_PATH = os.path.join(os.path.dirname(__file__), "config", "networks.json")
_CACHE = None


def _load():
    global _CACHE
    if _CACHE is None:
        with open(_CFG_PATH, encoding="utf-8") as f:
            _CACHE = json.load(f)
    return _CACHE


def _build_lookup(domain):
    """Return {key_lower: short} for both id and aliases of `domain` ('retail'|'gas')."""
    cfg = _load().get(domain, {})
    out = {}
    for nid, meta in cfg.items():
        short = meta.get("short", nid)
        out[nid.lower()] = short
        for alias in meta.get("aliases", []):
            out[alias.lower()] = short
    return out


_LOOKUPS = {}


def short(name_or_id, domain="retail"):
    """Return the short display name for a network id OR full/alias name.

    Falls back to the input value (title-cased) if no mapping exists, so the
    site never shows a blank label.
    """
    if not name_or_id:
        return ""
    if domain not in _LOOKUPS:
        _LOOKUPS[domain] = _build_lookup(domain)
    key = str(name_or_id).strip().lower()
    return _LOOKUPS[domain].get(key, str(name_or_id).title())


def is_b2b(name_or_id, domain="retail"):
    """True if the network is flagged B2B-only (e.g. SELGROS) — exclude from
    consumer comparisons."""
    cfg = _load().get(domain, {})
    key = str(name_or_id or "").strip().lower()
    for nid, meta in cfg.items():
        if nid.lower() == key or any(a.lower() == key for a in meta.get("aliases", [])):
            return bool(meta.get("b2b", False))
    return False


def all_short(domain="retail", include_b2b=False):
    """Return list of short names for the domain (consumer networks by default)."""
    cfg = _load().get(domain, {})
    return [
        meta.get("short", nid)
        for nid, meta in cfg.items()
        if include_b2b or not meta.get("b2b", False)
    ]


if __name__ == "__main__":
    print("Retail (consumer):", all_short("retail"))
    print("Retail (incl. B2B):", all_short("retail", include_b2b=True))
    print("Gas:", all_short("gas"))
    print()
    print("Examples:")
    for n in ["MEGA IMAGE SRL", "5940475006709", "KAUFLAND", "Lidl Discount SRL", "SELGROS"]:
        print(f"  {n!r:>25} -> {short(n)!r}  (b2b={is_b2b(n)})")
