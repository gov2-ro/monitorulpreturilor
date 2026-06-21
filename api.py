import atexit
import logging
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET

import certifi
import requests

BASE = "https://monitorulpreturilor.info/pmonsvc/Retail"
GAS_BASE = "https://monitorulpreturilor.info/pmonsvc/Gas"
NS = "http://schemas.datacontract.org/2004/07/pmonsvc.Models.Protos"

# Build a CA bundle that merges certifi's roots with any extra intermediates
# kept in data/extra_certs.pem.  This survives venv rebuilds; see activity-log
# 2026-06-12 for the incident that motivated it (Sectigo chain renewal).
_EXTRA_CERTS = os.path.join(os.path.dirname(__file__), "data", "extra_certs.pem")

def _build_ca_bundle() -> str:
    if not os.path.exists(_EXTRA_CERTS):
        return certifi.where()
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
    with open(certifi.where()) as f:
        tmp.write(f.read())
    with open(_EXTRA_CERTS) as f:
        tmp.write(f.read())
    tmp.close()
    atexit.register(os.unlink, tmp.name)
    return tmp.name

_VERIFY = _build_ca_bundle()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_invalid_char_refs(text):
    """Remove XML entity refs for characters disallowed in XML 1.0.

    The API occasionally embeds refs like &#x1C; in product names.
    ET.fromstring rejects them, so we drop them before parsing.
    Valid whitespace (&#x09; &#x0A; &#x0D;) is preserved.
    """
    def _replace(m):
        val = int(m.group(1), 16)
        if val in (0x09, 0x0A, 0x0D):
            return m.group(0)
        if val < 0x20 or val == 0x7F:
            return ""
        return m.group(0)
    return re.sub(r"&#[xX]([0-9a-fA-F]{1,4});", _replace, text)


def _t(el, tag, default=""):
    """Return stripped text of a direct child element, or default."""
    child = el.find(f"{{{NS}}}{tag}")
    return (child.text or "").strip() if child is not None else default


def _parse_date(s):
    """Normalize DD.MM.YYYY HH:MM or DD/MM/YYYY HH:MM → YYYY-MM-DD HH:MM.

    Passes through strings already in ISO form (or empty/None).
    """
    if not s or len(s) < 10 or s[2] not in ('.', '/'):
        return s
    return s[6:10] + '-' + s[3:5] + '-' + s[0:2] + s[10:]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_xml(url, retries=3, timeout=30):
    """GET url, return parsed ElementTree root. Retries with exponential backoff."""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout, verify=_VERIFY)
            r.raise_for_status()
            cleaned = _strip_invalid_char_refs(r.text).encode("utf-8")
            return ET.fromstring(cleaned)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"    retry {attempt + 1}/{retries} in {wait}s ({exc})")
            time.sleep(wait)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_networks(root):
    results = []
    for el in root.findall(f".//{{{NS}}}RetailNetwork"):
        logo = el.find(f"{{{NS}}}Logo")
        logo_url = _t(logo, "Logouri") if logo is not None else ""
        results.append({
            "id": _t(el, "Id"),
            "name": _t(el, "Name"),
            "logo_url": logo_url,
        })
    return results


def centroid_from_wkt(wkt):
    """POLYGON((lon lat, lon lat, ...)) → (center_lat, center_lon)."""
    start = wkt.index("((") + 2
    end = wkt.rindex("))")
    pairs = [p.strip().split() for p in wkt[start:end].split(",") if p.strip()]
    lons = [float(p[0]) for p in pairs]
    lats = [float(p[1]) for p in pairs]
    return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2


def parse_uats(root):
    results = []
    for el in root.findall(f".//{{{NS}}}UAT"):
        wkt = _t(el, "Wkt")
        center_lat, center_lon = centroid_from_wkt(wkt) if wkt else (None, None)
        results.append({
            "id": int(_t(el, "Id")),
            "name": _t(el, "Name"),
            "route_id": _t(el, "RouteId"),
            "wkt": wkt,
            "center_lat": center_lat,
            "center_lon": center_lon,
        })
    return results


def parse_categories(root, source):
    results = []
    for el in root.findall(f".//{{{NS}}}CatalogProductCategory"):
        logo = el.find(f"{{{NS}}}Logo")
        logo_url = _t(logo, "Logouri") if logo is not None else ""
        parent_id_str = _t(el, "ParentId")
        results.append({
            "id": int(_t(el, "Id")),
            "name": _t(el, "Name"),
            "parent_id": int(parent_id_str) if parent_id_str else None,
            "logo_url": logo_url,
            "source": source,
        })
    return results


def parse_products(root):
    results = []
    for el in root.findall(f".//{{{NS}}}CatalogProduct"):
        prod_id = _t(el, "Id")
        if not prod_id:
            continue
        categ = el.find(f".//{{{NS}}}Prodcateg/{{{NS}}}Id")
        categ_id_str = (categ.text or "").strip() if categ is not None else ""
        results.append({
            "id": int(prod_id),
            "name": _t(el, "Name"),
            "categ_id": int(categ_id_str) if categ_id_str else None,
        })
    return results


def parse_stores_and_prices(root, fetched_at):
    """Return (stores_list, prices_list). Skips products with no price/date."""
    stores = {}
    prices = []

    for store_el in root.findall(f".//{{{NS}}}RetailStore"):
        store_id_str = _t(store_el, "Id")
        if not store_id_str:
            continue
        store_id = int(store_id_str)
        name = _t(store_el, "Name")

        addr = lat = lon = uat_id = zipcode = None
        addr_el = store_el.find(f"{{{NS}}}Addr")
        if addr_el is not None:
            addr = _t(addr_el, "Addrstring")
            zipcode = _t(addr_el, "Zipcode")
            uat_id_str = _t(addr_el, "Uatid")
            uat_id = int(uat_id_str) if uat_id_str else None
            loc = addr_el.find(f"{{{NS}}}Location")
            if loc is not None:
                lat_s = _t(loc, "Lat")
                lon_s = _t(loc, "Lon")
                lat = float(lat_s) if lat_s else None
                lon = float(lon_s) if lon_s else None

        # Store-level logo (brand/format logo, e.g. CarrefourMarket.png)
        logo_el = store_el.find(f"{{{NS}}}Logo")
        logo_url = _t(logo_el, "Logouri") if logo_el is not None else None

        # Store type (e.g. "Supermarket", "Supermarket & Discounter")
        type_el = store_el.find(f"{{{NS}}}Type")
        type_id = _t(type_el, "Id") if type_el is not None else None
        type_name = _t(type_el, "Name") if type_el is not None else None
        type_id = int(type_id) if type_id else None

        # Network from store-level Retailnetwork element (primary — present even
        # when all products have price=0 so the product-loop never fires)
        net_el = store_el.find(f"{{{NS}}}Retailnetwork")
        network_id = _t(net_el, "Id") if net_el is not None else None

        prod_network_id = None
        for prod_el in store_el.findall(f".//{{{NS}}}Product"):
            price_str = _t(prod_el, "Price")
            price_date = _parse_date(_t(prod_el, "Pricedate"))
            # Skip entries where the store doesn't carry this product
            if not price_date or not price_str or price_str == "0":
                continue

            nid = _t(prod_el, "Networkid")
            if nid and prod_network_id is None:
                prod_network_id = nid

            catprod = prod_el.find(f"{{{NS}}}Catprod")
            if catprod is None:
                continue
            pid_str = _t(catprod, "Id")
            if not pid_str:
                continue
            prod_id = int(pid_str)

            prices.append({
                "product_id": prod_id,
                "store_id": store_id,
                "price": float(price_str),
                "price_date": price_date,
                "promo": _t(prod_el, "Promo"),
                "brand": _t(prod_el, "Brand"),
                "unit": _t(prod_el, "Unit"),
                "retail_categ_id": _t(prod_el, "Retailcategid"),
                "retail_categ_name": _t(prod_el, "Retailcategname"),
                "fetched_at": fetched_at,
            })

        # Conflict check: product-level Networkid should match Retailnetwork > Id
        if prod_network_id and network_id and prod_network_id != network_id:
            logging.warning(
                "store %s (%s): network_id conflict — store element=%s, product=%s",
                store_id, name, network_id, prod_network_id,
            )
        # Fall back to product-derived network_id if store element had none
        if not network_id and prod_network_id:
            network_id = prod_network_id

        stores[store_id] = {
            "id": store_id,
            "name": name,
            "addr": addr,
            "lat": lat,
            "lon": lon,
            "uat_id": uat_id,
            "network_id": network_id,
            "zipcode": zipcode,
            "logo_url": logo_url,
            "type_id": type_id,
            "type_name": type_name,
        }

    return list(stores.values()), prices


# ---------------------------------------------------------------------------
# Gas parsers
# ---------------------------------------------------------------------------

def parse_gas_networks(root):
    results = []
    for el in root.findall(f".//{{{NS}}}GasNetwork"):
        logo = el.find(f"{{{NS}}}Logo")
        logo_url = _t(logo, "Logouri") if logo is not None else ""
        results.append({
            "id": _t(el, "Id"),
            "name": _t(el, "Name"),
            "logo_url": logo_url,
        })
    return results


def parse_gas_products(root):
    results = []
    for el in root.findall(f".//{{{NS}}}GasCatalogProduct"):
        prod_id = _t(el, "Id")
        if not prod_id:
            continue
        logo = el.find(f"{{{NS}}}Logo")
        logo_url = _t(logo, "Logouri") if logo is not None else ""
        results.append({
            "id": int(prod_id),
            "name": _t(el, "Name"),
            "logo_url": logo_url,
        })
    return results


def parse_gas_items(root, fetched_at):
    """Return (stations_list, prices_list) from a GetGasItemsByUat response.

    Stations are parsed from GasItems/Stations; prices from GasItems/Products.
    Skips prices where Price is 0 or empty.
    price_date is taken from the corresponding station's Updatedate field.
    """
    # Build station dict first (needed to resolve price_date)
    stations = {}
    for st_el in root.findall(f".//{{{NS}}}GasStation"):
        st_id = _t(st_el, "Id")  # may be "P343" or "39" — keep as string
        if not st_id:
            continue
        addr_el = st_el.find(f"{{{NS}}}Addr")
        addr = lat = lon = uat_id = zipcode = None
        if addr_el is not None:
            addr = _t(addr_el, "Addrstring")
            zipcode = _t(addr_el, "Zipcode")
            uat_id_str = _t(addr_el, "Uatid")
            uat_id = int(uat_id_str) if uat_id_str else None
            loc = addr_el.find(f"{{{NS}}}Location")
            if loc is not None:
                lat_s = _t(loc, "Lat")
                lon_s = _t(loc, "Lon")
                lat = float(lat_s) if lat_s else None
                lon = float(lon_s) if lon_s else None
        net_el = st_el.find(f"{{{NS}}}Network")
        network_id = _t(net_el, "Id") if net_el is not None else None
        update_date = _parse_date(_t(st_el, "Updatedate"))
        stations[st_id] = {
            "id": st_id,
            "name": _t(st_el, "Name"),
            "addr": addr,
            "lat": lat,
            "lon": lon,
            "uat_id": uat_id,
            "network_id": network_id,
            "zipcode": zipcode,
            "update_date": update_date,
        }

    prices = []
    for prod_el in root.findall(f".//{{{NS}}}GasProduct"):
        price_str = _t(prod_el, "Price")
        if not price_str or price_str == "0":
            continue
        st_id = _t(prod_el, "Stationid")  # keep as string (matches gas_stations.id)
        if not st_id:
            continue
        catprod = prod_el.find(f"{{{NS}}}Catprod")
        if catprod is None:
            continue
        fuel_id_str = _t(catprod, "Id")
        if not fuel_id_str:
            continue
        price_date = stations.get(st_id, {}).get("update_date", "")
        prices.append({
            "product_id": int(fuel_id_str),
            "station_id": st_id,
            "price": float(price_str),
            "price_date": price_date,
            "fetched_at": fetched_at,
        })

    return list(stations.values()), prices
