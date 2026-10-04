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

from services.menu_builders import OPTION_ONLY_SKUS, public_builder

logger = logging.getLogger("farmhouse.menu_catalog")

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
CSV_PATH = PROJECT_ROOT / "database" / "farmhouse_catalog_meta.csv"

# Categorías del CSV (columna custom_label_0) que son adicionales/premiums, no platos independientes.
ADDON_CATEGORIES = {"Premiums", "Toastie Add-ons", "Smoothie Extras", "Acai Add-ons", "Coffee Extras"}

# Pestañas (pills) del menú digital, en el mismo orden que el menú impreso
# (farmhouse_menu_completo.pdf). Si una pestaña junta varias categorías del CSV, cada una sale
# como una sub-sección con su título (CATEGORY_LABELS).
TAB_DEFINITIONS = [
    {"key": "salads", "label": "Ensaladas y wraps", "categories": ["Salads", "Wraps"], "addon_category": "Premiums",
     "description": "Ensaladas en tamaño R o L y wraps en tortilla de harina de trigo."},
    {"key": "bowls", "label": "Bowls", "categories": ["Bowls"], "addon_category": "Premiums",
     "description": "Combinaciones Farmhouse. Elige tu favorito y complétalo con premiums."},
    {"key": "byo", "label": "Arma tu bowl", "categories": ["Build Your Own"], "addon_category": "Premiums",
     "description": "Elige base, toppings, dressing y crunch, y súmale una proteína si quieres."},
    {"key": "toasties", "label": "Toasties", "categories": ["Toasties"], "addon_category": "Toastie Add-ons",
     "description": "En pan de masa madre."},
    {"key": "acai", "label": "Açaí", "categories": ["Acai Bowl"], "addon_category": "Acai Add-ons",
     "description": "Elige 3 toppings y 1 crunch. Spreads y add-ons tienen un cargo adicional."},
    {"key": "smoothies", "label": "Smoothies", "categories": ["Classic Smoothies", "Signature Smoothies"], "addon_category": "Smoothie Extras",
     "description": "Clásicos y signature, hechos con fruta fresca cada día."},
    {"key": "coffee", "label": "Café, matcha y té", "categories": ["Coffee", "Matcha", "Tea"], "addon_category": None,
     "description": "Café de especialidad, matcha bar e infusiones."},
    {"key": "foamies", "label": "Foamies", "categories": ["Foamies"], "addon_category": None,
     "description": "Selección fija de By the Park. Todos a $8.00."},
    {"key": "juices", "label": "Jugos y shots", "categories": ["Juice Bar", "Shots"], "addon_category": None,
     "description": "Jugos cold pressed y wellness shots."},
    {"key": "drinks", "label": "Otras bebidas", "categories": ["Drinks", "Sodas"], "addon_category": None,
     "description": "Aguas, sodas artesanales, chicha y más."},
    {"key": "vitrina", "label": "Vitrina", "categories": ["Vitrina", "Cookies", "Loaves"], "addon_category": None,
     "description": "Para acompañar: snacks, cookies y loaves del día."},
    {"key": "merch", "label": "Merch", "categories": ["Merch"], "addon_category": None,
     "description": "Productos de marca Farmhouse."},
]

# Título de cada sub-sección cuando una pestaña junta varias categorías.
CATEGORY_LABELS = {
    "Salads": "Ensaladas", "Wraps": "Wraps",
    "Classic Smoothies": "Classic", "Signature Smoothies": "Signature",
    "Coffee": "Specialty coffee", "Matcha": "Matcha bar", "Tea": "Infusiones",
    "Juice Bar": "Juice bar", "Shots": "Wellness shots",
    "Drinks": "Otras bebidas", "Sodas": "Sodas artesanales",
    "Vitrina": "Snacks", "Cookies": "Cookies del día", "Loaves": "Loaves del día",
}

# Adicionales de cada categoría (un wrap no lleva premiums aunque comparta pestaña con las
# ensaladas; el café lleva sus extras aunque el matcha de la misma pestaña no).
CATEGORY_ADDONS = {
    "Salads": "Premiums", "Bowls": "Premiums", "Build Your Own": "Premiums",
    "Toasties": "Toastie Add-ons", "Acai Bowl": "Acai Add-ons",
    "Classic Smoothies": "Smoothie Extras", "Signature Smoothies": "Smoothie Extras",
    "Coffee": "Coffee Extras",
}

_cache: Dict[str, Any] = {"mtime": None, "rows": None}


def _parse_price(raw: str) -> Decimal:
    try:
        return Decimal(str(raw).replace("USD", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError):
        return Decimal("0.00")


def _image_url(row: Dict[str, str]) -> str:
    """Solo fotos reales de Farmhouse (frontend/static/images/menu). Las de /static/catalog son
    de banco de imágenes para el catálogo de Meta y varias no corresponden al plato (el açaí
    salía como un bibimbap): sin foto real, /menu dibuja una tarjeta ilustrada."""
    link = (row.get("image_link") or "").strip()
    return link if link.startswith("/frontend/static/") else ""


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
            # "out of stock" = ya no está en el menú impreso: no se lista ni se puede pedir.
            "available": (row.get("availability") or "in stock").strip().lower() == "in stock",
        })

    _cache["rows"] = rows
    _cache["mtime"] = mtime
    return rows


def get_item_by_sku(sku: str, include_unavailable: bool = False) -> Optional[Dict[str, Any]]:
    if not sku:
        return None
    for row in _load_rows():
        if row["sku"] == sku:
            return row if (row["available"] or include_unavailable) else None
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
    rows = [
        r for r in _load_rows()
        if r["available"] and r["category"] in categories and r["category"] not in ADDON_CATEGORIES
        and r["sku"] not in OPTION_ONLY_SKUS
    ]
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
                "section": CATEGORY_LABELS.get(row["category"], ""),
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
                "section": CATEGORY_LABELS.get(row["category"], ""),
                "has_sizes": False,
                "sizes": [{"code": "unico", "label": "Único", "sku": row["sku"], "price": float(row["price"])}],
            })

    # Mismo orden que el CSV (que sigue el menú impreso), con los platos de tamaño R/L en su lugar.
    order = {r["sku"]: i for i, r in enumerate(rows)}
    products = sorted(list(grouped.values()) + standalone, key=lambda p: min(order[x["sku"]] for x in p["sizes"]))
    for p in products:
        p["sizes"].sort(key=lambda x: 0 if x["code"] == "regular" else (1 if x["code"] == "large" else 2))
        p["addons"] = _build_addon_group(CATEGORY_ADDONS.get(p["category"]))
        p["builder"] = public_builder(p["sizes"][0]["sku"])
    return products


def clean_item_title(title: str) -> str:
    """Quita el sufijo interno del catálogo (ej. '(premium warm)', '(add-on)') del nombre mostrado al cliente."""
    return title.split(" (premium")[0].split(" (add-on")[0].split(" (extra")[0].split(" (crunch")[0]


def _build_addon_group(category: Optional[str]) -> Dict[str, Any]:
    if not category:
        return {"warm": [], "cold": [], "flat": []}
    rows = [r for r in _load_rows() if r["category"] == category and r["available"]]
    warm, cold, flat = [], [], []
    for row in rows:
        item = {"sku": row["sku"], "title": clean_item_title(row["title"]), "price": float(row["price"]), "image_url": row["image_url"]}
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
            "description": tab["description"],
            "products": _build_products(tab["categories"]),
            "addons": _build_addon_group(tab["addon_category"]),
        })
    return {"tabs": tabs}
