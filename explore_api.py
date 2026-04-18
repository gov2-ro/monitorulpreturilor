"""
explore_api.py — Discover undocumented API endpoints for monitorulpreturilor.info

Strategy:
  1. WCF metadata (WSDL/MEX/help) — may give us the full contract for free
  2. Root-level service discovery
  3. Candidate endpoint probing (pattern-based + symmetric naming)
  4. Known-endpoint variations (different params, OrderBy values)

Output: terminal table + docs/reference/undocumented-endpoints.md
"""

import time
import textwrap
from datetime import datetime

import requests

BASE = "https://monitorulpreturilor.info/pmonsvc/Retail"
GAS_BASE = "https://monitorulpreturilor.info/pmonsvc/Gas"
SLEEP = 0.4  # seconds between requests

KNOWN_RETAIL = {
    "GetRetailNetworks",
    "GetUATByName",
    "GetProductCategoriesNetwork",
    "GetProductCategoriesNetworkOUG",
    "GetCatalogProductsByNameNetwork",
    "GetCatalogProductsById",
    "GetStoresForProductsByLatLon",
    "GetStoresForProductsByUat",
}

KNOWN_GAS = {
    "GetGasNetworks",
    "GetGasProductsFromCatalog",
    "GetGasServicesFromCatalog",
    "GetUATByName",
    "GetGasItemsByUat",
    "GetGasItemsByLatLon",
    "GetGasItemsByRoute",
}

# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def probe(url, label=None, sleep=True):
    """GET url; return (status, body_bytes, content_type)."""
    tag = label or url
    try:
        r = requests.get(url, timeout=20)
        ct = r.headers.get("Content-Type", "")
        size = len(r.content)
        snippet = r.text[:300].replace("\n", " ").strip()
        print(f"  [{r.status_code}] {tag:<55} {size:>7} bytes  {snippet[:80]}")
        if sleep:
            time.sleep(SLEEP)
        return r.status_code, r.content, ct, r.text
    except Exception as exc:
        print(f"  [ERR] {tag:<55} {exc}")
        if sleep:
            time.sleep(SLEEP)
        return 0, b"", "", str(exc)


def is_interesting(status, body_text):
    """Return True if response looks like real data (not an error page)."""
    if status not in (200, 400):
        return False
    if not body_text:
        return False
    # WCF XML responses contain this namespace
    if "schemas.datacontract.org" in body_text:
        return True
    # WSDL responses
    if "wsdl" in body_text.lower() or "definitions" in body_text.lower():
        return True
    # Swagger/OpenAPI
    if "swagger" in body_text.lower() or "openapi" in body_text.lower():
        return True
    # Any XML
    if body_text.strip().startswith("<"):
        return True
    # JSON
    if body_text.strip().startswith("{") or body_text.strip().startswith("["):
        return True
    return False


# ---------------------------------------------------------------------------
# Phase 1: WCF metadata + root discovery
# ---------------------------------------------------------------------------

def phase1_metadata():
    print("\n" + "="*70)
    print("PHASE 1: WCF metadata / root discovery")
    print("="*70)
    findings = []

    candidates = [
        ("ROOT", "https://monitorulpreturilor.info/"),
        ("PMONSVC root", "https://monitorulpreturilor.info/pmonsvc/"),
        ("Retail WSDL", f"{BASE}?wsdl"),
        ("Retail singleWsdl", f"{BASE}?singleWsdl"),
        ("Retail MEX", f"{BASE}/mex"),
        ("Retail help", f"{BASE}/help"),
        ("Retail swagger", f"{BASE}/swagger"),
        ("Gas WSDL", f"{GAS_BASE}?wsdl"),
        ("Gas singleWsdl", f"{GAS_BASE}?singleWsdl"),
        ("Gas MEX", f"{GAS_BASE}/mex"),
        ("Gas help", f"{GAS_BASE}/help"),
    ]

    for label, url in candidates:
        status, content, ct, text = probe(url, label)
        if is_interesting(status, text):
            findings.append({"label": label, "url": url, "status": status,
                             "size": len(content), "snippet": text[:500], "phase": 1})

    return findings


# ---------------------------------------------------------------------------
# Phase 2: Candidate endpoint probing
# ---------------------------------------------------------------------------

RETAIL_CANDIDATES = [
    # Symmetric with gas endpoints
    ("GetStoresForProductsByRoute", f"{BASE}/GetStoresForProductsByRoute?startRoutePointId=840293&endRoutePointId=7981&csvprodids=1686&OrderBy=dist"),
    # Store-level detail
    ("GetStoreDetails", f"{BASE}/GetStoreDetails?storeId=1"),
    ("GetStoreById", f"{BASE}/GetStoreById?id=1"),
    ("GetStore", f"{BASE}/GetStore?id=1"),
    # Network-filtered
    ("GetStoresByNetwork", f"{BASE}/GetStoresByNetwork?networkId=1"),
    ("GetStoresByNetworkId", f"{BASE}/GetStoresByNetworkId?networkId=1"),
    # Price history
    ("GetPriceHistory", f"{BASE}/GetPriceHistory?prodid=1686&storeid=1"),
    ("GetHistoricalPrices", f"{BASE}/GetHistoricalPrices?prodid=1686&storeid=1"),
    ("GetPricesHistory", f"{BASE}/GetPricesHistory?prodid=1686&storeid=1"),
    ("GetProductPriceHistory", f"{BASE}/GetProductPriceHistory?prodid=1686"),
    ("GetProductPriceHistoryByNetwork", f"{BASE}/GetProductPriceHistoryByNetwork?prodid=1686"),
    # Product extras
    ("GetProductsByBrand", f"{BASE}/GetProductsByBrand?brand=Jacobs"),
    ("GetCatalogProductsByBarcode", f"{BASE}/GetCatalogProductsByBarcode?barcode=12345678"),
    ("GetProductDetails", f"{BASE}/GetProductDetails?id=1686"),
    ("GetProductById", f"{BASE}/GetProductById?id=1686"),
    # Services (gas has this)
    ("GetRetailServicesCatalog", f"{BASE}/GetRetailServicesCatalog"),
    ("GetServicesCatalog", f"{BASE}/GetServicesCatalog"),
    ("GetRetailServices", f"{BASE}/GetRetailServices"),
    # Promotions
    ("GetPromos", f"{BASE}/GetPromos"),
    ("GetPromotions", f"{BASE}/GetPromotions?lat=44.43&lon=26.10&buffer=5000"),
    ("GetActivePromos", f"{BASE}/GetActivePromos"),
    # Stats
    ("GetStats", f"{BASE}/GetStats"),
    ("GetStatistics", f"{BASE}/GetStatistics"),
    # Brands
    ("GetBrands", f"{BASE}/GetBrands"),
    ("GetBrandsCatalog", f"{BASE}/GetBrandsCatalog"),
    # All stores
    ("GetAllStores", f"{BASE}/GetAllStores"),
    ("GetStores", f"{BASE}/GetStores"),
    ("GetStoresByUat", f"{BASE}/GetStoresByUat?uatId=179132"),
    # OUG/monitoring
    ("GetMonitoredProducts", f"{BASE}/GetMonitoredProducts"),
    ("GetOUGProducts", f"{BASE}/GetOUGProducts"),
    ("GetProductCategoriesOUG", f"{BASE}/GetProductCategoriesOUG"),
    # No-arg variants of known endpoints
    ("GetStoresForProductsByLatLon (no params)", f"{BASE}/GetStoresForProductsByLatLon"),
    ("GetCatalogProductsByNameNetwork (all)", f"{BASE}/GetCatalogProductsByNameNetwork"),
]

GAS_CANDIDATES = [
    # Symmetric with retail
    ("GetGasItemsByName", f"{GAS_BASE}/GetGasItemsByName?name=benzina"),
    ("GetGasStationDetails", f"{GAS_BASE}/GetGasStationDetails?stationId=1"),
    ("GetGasStationById", f"{GAS_BASE}/GetGasStationById?id=1"),
    ("GetGasStation", f"{GAS_BASE}/GetGasStation?id=1"),
    # Price history
    ("GetGasPriceHistory", f"{GAS_BASE}/GetGasPriceHistory?stationid=1&productid=11"),
    ("GetHistoricalGasPrices", f"{GAS_BASE}/GetHistoricalGasPrices?productid=11"),
    # Network filtered
    ("GetGasStationsByNetwork", f"{GAS_BASE}/GetGasStationsByNetwork?networkId=1"),
    # All stations
    ("GetAllGasStations", f"{GAS_BASE}/GetAllGasStations"),
    ("GetGasStations", f"{GAS_BASE}/GetGasStations"),
    ("GetGasStationsByUat", f"{GAS_BASE}/GetGasStationsByUat?uatId=179132"),
    # Services with location filter
    ("GetGasServicesByLatLon", f"{GAS_BASE}/GetGasServicesByLatLon?lat=44.43&lon=26.10&buffer=5000"),
    ("GetGasStationsWithServices", f"{GAS_BASE}/GetGasStationsWithServices?lat=44.43&lon=26.10&buffer=5000"),
    # Route points
    ("GetRoutePoints", f"{GAS_BASE}/GetRoutePoints"),
    ("GetRoutes", f"{GAS_BASE}/GetRoutes"),
    # Stats
    ("GetGasStats", f"{GAS_BASE}/GetGasStats"),
    # No-arg variants
    ("GetGasItemsByUat (no params)", f"{GAS_BASE}/GetGasItemsByUat"),
    ("GetGasItemsByLatLon (no params)", f"{GAS_BASE}/GetGasItemsByLatLon"),
]


def phase2_candidates():
    print("\n" + "="*70)
    print("PHASE 2: Retail candidate endpoints")
    print("="*70)
    findings = []

    for label, url in RETAIL_CANDIDATES:
        status, content, ct, text = probe(url, f"Retail/{label}")
        if is_interesting(status, text):
            findings.append({"label": f"Retail/{label}", "url": url, "status": status,
                             "size": len(content), "snippet": text[:500], "phase": 2})

    print("\n" + "="*70)
    print("PHASE 2: Gas candidate endpoints")
    print("="*70)

    for label, url in GAS_CANDIDATES:
        status, content, ct, text = probe(url, f"Gas/{label}")
        if is_interesting(status, text):
            findings.append({"label": f"Gas/{label}", "url": url, "status": status,
                             "size": len(content), "snippet": text[:500], "phase": 2})

    return findings


# ---------------------------------------------------------------------------
# Phase 3: Variations on known endpoints
# ---------------------------------------------------------------------------

def phase3_variations():
    print("\n" + "="*70)
    print("PHASE 3: Known endpoint variations")
    print("="*70)
    findings = []

    # OrderBy variations on GetStoresForProductsByLatLon
    lat, lon = 44.4268, 26.1025  # Bucharest center
    prodids = "1686"  # Lapte Zuzu - widely available
    buffer = 5000

    for order in ["name", "date", "network", "relevance", "dist", "id"]:
        url = f"{BASE}/GetStoresForProductsByLatLon?lat={lat}&lon={lon}&buffer={buffer}&csvprodids={prodids}&OrderBy={order}"
        status, content, ct, text = probe(url, f"ByLatLon OrderBy={order}")
        if is_interesting(status, text):
            findings.append({"label": f"Retail/GetStoresForProductsByLatLon?OrderBy={order}",
                             "url": url, "status": status, "size": len(content),
                             "snippet": text[:300], "phase": 3})

    # GetCatalogProductsByNameNetwork with no category — full catalog?
    for variant in [
        ("empty prodname", f"{BASE}/GetCatalogProductsByNameNetwork?prodname="),
        ("prodname=a", f"{BASE}/GetCatalogProductsByNameNetwork?prodname=a"),
        ("no params", f"{BASE}/GetCatalogProductsByNameNetwork"),
    ]:
        label, url = variant
        status, content, ct, text = probe(url, f"GetCatalogProductsByNameNetwork ({label})")
        if is_interesting(status, text):
            findings.append({"label": f"Retail/GetCatalogProductsByNameNetwork ({label})",
                             "url": url, "status": status, "size": len(content),
                             "snippet": text[:300], "phase": 3})

    # GetStoresForProductsByUat — test it actually works
    url = f"{BASE}/GetStoresForProductsByUat?uatId=179132&csvprodids={prodids}&OrderBy=price"
    status, content, ct, text = probe(url, "GetStoresForProductsByUat (Bucharest)")
    if is_interesting(status, text):
        findings.append({"label": "Retail/GetStoresForProductsByUat",
                         "url": url, "status": status, "size": len(content),
                         "snippet": text[:300], "phase": 3})

    # Gas: CSV product IDs (known to return 500 — but worth confirming)
    url = f"{GAS_BASE}/GetGasItemsByUat?UatId=179132&CSVGasCatalogProductIds=11,12&OrderBy=dist"
    status, content, ct, text = probe(url, "Gas CSV productIds (expect 500)")
    findings.append({"label": "Gas/GetGasItemsByUat?CSV_productIds=11,12",
                     "url": url, "status": status, "size": len(content),
                     "snippet": text[:300], "phase": 3, "note": "CSV test — expect 500"})

    # Gas route endpoint — test with known route point IDs from backlog
    url = f"{GAS_BASE}/GetGasItemsByRoute?startRoutePointId=840293&endRoutePointId=7981&CSVGasCatalogProductIds=21&OrderBy=dist"
    status, content, ct, text = probe(url, "GetGasItemsByRoute (test)")
    if is_interesting(status, text):
        findings.append({"label": "Gas/GetGasItemsByRoute",
                         "url": url, "status": status, "size": len(content),
                         "snippet": text[:500], "phase": 3})

    return findings


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def write_report(all_findings):
    interesting = [f for f in all_findings if f["status"] in (200,) and is_interesting(f["status"], f.get("snippet", ""))]
    all_probed = all_findings

    lines = [
        "# Undocumented API Endpoint Discovery",
        f"",
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"",
        "## Summary",
        f"",
        f"- Probes run: {len(all_probed)}",
        f"- Interesting responses: {len(interesting)}",
        f"",
        "## Interesting Findings",
        f"",
    ]

    if interesting:
        for f in interesting:
            lines += [
                f"### {f['label']}",
                f"",
                f"- **URL:** `{f['url']}`",
                f"- **Status:** {f['status']}",
                f"- **Size:** {f['size']} bytes",
                f"- **Phase:** {f['phase']}",
                f"",
                "**Response snippet:**",
                "```xml",
                textwrap.fill(f.get('snippet', ''), width=100),
                "```",
                f"",
            ]
    else:
        lines += ["No new interesting endpoints found.", ""]

    lines += [
        "## All Probes",
        "",
        "| Status | Label | URL |",
        "|--------|-------|-----|",
    ]
    for f in all_probed:
        label = f["label"]
        url = f["url"]
        status = f["status"]
        note = f.get("note", "")
        marker = " ✓" if f["status"] == 200 and is_interesting(f["status"], f.get("snippet", "")) else ""
        lines.append(f"| {status}{marker} | {label} {note} | `{url[:80]}` |")

    path = "docs/reference/undocumented-endpoints.md"
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nReport written → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("MonitorulPreturilor API Endpoint Explorer")
    print(f"Started: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")

    all_findings = []
    all_findings += phase1_metadata()
    all_findings += phase2_candidates()
    all_findings += phase3_variations()

    print("\n" + "="*70)
    print("RESULTS SUMMARY")
    print("="*70)
    interesting = [f for f in all_findings if f["status"] == 200 and is_interesting(f["status"], f.get("snippet", ""))]
    print(f"Total probes: {len(all_findings)}")
    print(f"Interesting (200 + real data): {len(interesting)}")
    if interesting:
        print("\nInteresting findings:")
        for f in interesting:
            print(f"  ✓ [{f['status']}] {f['label']}")
            print(f"    {f['url']}")

    write_report(all_findings)
