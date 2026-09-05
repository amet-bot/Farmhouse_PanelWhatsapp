"""
Carga y estructura database/farmhouse_catalog_meta.csv para el Menú Digital (/menu)
y para validar/recalcular precios en POST /api/orders/public (Punto: nunca confiar
en el precio que manda el navegador, siempre recalcular desde el catálogo servidor).
"""
import csv
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("farmhouse.menu_catalog")

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
CSV_PATH = PROJECT_ROOT / "database" / "farmhouse_catalog_meta.csv"

# Categorías del CSV (columna custom_label_0) que son adicionales/premiums, no platos independientes.
ADDON_CATEGORIES = {"Premiums", "Toastie Add-ons", "Smoothie Extras"}

# Agrupación de categorías del CSV en las pestañas (pills) del menú digital.
TAB_DEFINITIONS = [
    {"key": "salads", "label": "🥗 Salads", "categories": ["Salads"], "addon_category": "Premiums"},
    {"key": "bowls", "label": "🍚 Bowls & Açaí", "categories": ["Bowls"], "addon_category": "Premiums"},
    {"key": "wraps", "label": "🌯 Wraps", "categories": ["Wraps"], "addon_category": None},
    {"key": "byo", "label": "🥣 Build Your Own", "categories": ["Build Your Own"], "addon_category": "Premiums"},
    {"key": "toasties", "label": "🥪 Toasties", "categories": ["Toasties"], "addon_category": "Toastie Add-ons"},
    {"key": "smoothies", "label": "🥤 Smoothies", "categories": ["Classic Smoothies", "Signature Smoothies"], "addon_category": "Smoothie Extras"},
    {"key": "drinks", "label": "☕ Cafetería & Bebidas", "categories": ["Drinks"], "addon_category": None},
    {"key": "vitrina", "label": "🍪 Sweets & Vitrina", "categories": ["Vitrina"], "addon_category": None},
    {"key": "merch", "label": "🛍️ Merch", "categories": ["Merch"], "addon_category": None},
]

_cache: Dict[str, Any] = {"mtime": None, "rows": None}


def _parse_price(raw: str) -> Decimal:
    try:
        return Decimal(str(raw).replace("USD", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError):
        return Decimal("0.00")


def _image_url(row: Dict[str, str]) -> str:
    sku = row.get("id", "").strip()
    return f"/static/catalog/{sku}.jpg"


def _load_rows() -> List[Dict[str, Any]]:
    if not CSV_PATH.exists():
        logger.error(f"[menu_catalog] No se encontró el catálogo en {CSV_PATH}")
        return []

    mtime = CSV_PATH.stat().st_mtime
    if _cache["rows"] is not None and _cache["mtime"] == mtime:
        return _cache["rows"]

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        raw_rows = list(csv.DictReader(f))

    rows: List[Dict[str, Any]] = []
    for row in raw_rows:
        sku = (row.get("id") or "").strip()
        if not sku:
            continue
        rows.append({
            "sku": sku,
            "title": (row.get("title") or "").strip(),
            "description": (row.get("description") or "").strip(),
            "price": _parse_price(row.get("price", "0")),
            "image_url": _image_url(row),
            "item_group_id": (row.get("item_group_id") or "").strip() or None,
            "category": (row.get("custom_label_0") or "").strip(),
        })

    _cache["rows"] = rows
    _cache["mtime"] = mtime
    return rows


def get_item_by_sku(sku: str) -> Optional[Dict[str, Any]]:
    if not sku:
        return None
    for row in _load_rows():
        if row["sku"] == sku:
            return row
    return None


def _size_label(title: str) -> str:
    if "(Regular)" in title:
        return "Regular"
    if "(Large)" in title:
        return "Large"
    return ""


def _base_name(title: str) -> str:
    return title.replace("(Regular)", "").replace("(Large)", "").strip()


def _build_products(categories: List[str]) -> List[Dict[str, Any]]:
    rows = [r for r in _load_rows() if r["category"] in categories and r["category"] not in ADDON_CATEGORIES]
    grouped: Dict[str, Dict[str, Any]] = {}
    standalone: List[Dict[str, Any]] = []

    for row in rows:
        gid = row["item_group_id"]
        if gid:
            product = grouped.setdefault(gid, {
                "id": gid,
                "title": _base_name(row["title"]),
                "description": row["description"],
                "image_url": row["image_url"],
                "category": row["category"],
                "has_sizes": True,
                "sizes": [],
            })
            product["sizes"].append({
                "code": "large" if "Large" in row["title"] else "regular",
                "label": _size_label(row["title"]) or "Único",
                "sku": row["sku"],
                "price": float(row["price"]),
            })
        else:
            standalone.append({
                "id": row["sku"],
                "title": row["title"],
                "description": row["description"],
                "image_url": row["image_url"],
                "category": row["category"],
                "has_sizes": False,
                "sizes": [{"code": "unico", "label": "Único", "sku": row["sku"], "price": float(row["price"])}],
            })

    products = list(grouped.values()) + standalone
    for p in products:
        p["sizes"].sort(key=lambda s: 0 if s["code"] == "regular" else (1 if s["code"] == "large" else 2))
    return products


def clean_item_title(title: str) -> str:
    """Quita el sufijo interno del catálogo (ej. '(premium warm)', '(add-on)') del nombre mostrado al cliente."""
    return title.split(" (premium")[0].split(" (add-on")[0].split(" (extra")[0]


def _build_addon_group(category: Optional[str]) -> Dict[str, Any]:
    if not category:
        return {"warm": [], "cold": [], "flat": []}
    rows = [r for r in _load_rows() if r["category"] == category]
    warm, cold, flat = [], [], []
    for row in rows:
        item = {"sku": row["sku"], "title": clean_item_title(row["title"]), "price": float(row["price"])}
        title_lower = row["title"].lower()
        if "(premium warm)" in title_lower:
            warm.append(item)
        elif "(premium cold)" in title_lower:
            cold.append(item)
        else:
            flat.append(item)
    return {"warm": warm, "cold": cold, "flat": flat}


def get_menu_structure() -> Dict[str, Any]:
    tabs = []
    for tab in TAB_DEFINITIONS:
        tabs.append({
            "key": tab["key"],
            "label": tab["label"],
            "products": _build_products(tab["categories"]),
            "addons": _build_addon_group(tab["addon_category"]),
        })
    return {"tabs": tabs}
