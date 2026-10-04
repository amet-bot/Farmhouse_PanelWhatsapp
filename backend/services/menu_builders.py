"""
Opciones que el cliente elige dentro de un producto del Menú Digital (/menu): la base, toppings,
dressing y crunch de "Arma tu bowl", los toppings del açaí, qué infusión, Coca Cola regular o
zero, etc. Calcado del menú impreso (farmhouse_menu_completo.pdf).

Cada grupo dice cuántas opciones se pueden elegir (min/max). Casi todas van incluidas en el
precio; las que cobran aparte (chilli crunch) apuntan a un SKU del catálogo con `price_sku`, y
ese SKU se suma como adicional de la línea: el precio sale siempre del catálogo del servidor.

`color` y `style` solo los usa el dibujo del bowl en /menu (base = fondo del bowl, wedge =
porción, drizzle = hilo de dressing, sprinkle = crunch espolvoreado).
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status


def _opts(*items) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        key, label, color = item[:3]
        opt = {"key": key, "label": label, "color": color}
        if len(item) > 3:
            opt["price_sku"] = item[3]
        out.append(opt)
    return out


BYO_GROUPS = [
    {"key": "bases", "title": "Base", "hint": "Elige hasta 2", "min": 1, "max": 2, "style": "base",
     "options": _opts(
         ("mezclum", "Mézclum", "#7ea24c"),
         ("kale_repollo", "Kale + repollo", "#56804a"),
         ("cilantro_pesto_rice", "Cilantro pesto rice", "#b3c98a"),
         ("quinoa", "Quinoa", "#dcc69c"),
     )},
    {"key": "toppings", "title": "Toppings", "hint": "Elige hasta 4", "min": 0, "max": 4, "style": "wedge",
     "options": _opts(
         ("zanahorias_ralladas", "Zanahorias ralladas", "#f0913a"),
         ("tomates_cherry", "Tomates cherry", "#e0483a"),
         ("pepino", "Pepino", "#a3d47f"),
         ("tomates_pepino", "Tomates cherry + pepino", "#d4683f"),
         ("roasted_corn", "Roasted corn", "#f2c94c"),
         ("brocoli", "Brócoli rostizado", "#4d8b3a"),
         ("pimenton", "Pimentón rostizado", "#c9432c"),
         ("frijolitos", "Frijolitos negros", "#3a2f2f"),
         ("zapallo", "Zapallo rostizado", "#e88f2f"),
         ("zanahorias_encurtidas", "Zanahorias encurtidas", "#f5a35b"),
         ("cranberries", "Cranberries", "#a52a45"),
         ("kalamata", "Aceitunas kalamata", "#5a3550"),
         ("zucchini", "Zucchini rostizado", "#b5cf6f"),
         ("charred_onions", "Charred onions", "#8c5a3a"),
         ("cebolla_encurtida", "Cebolla encurtida", "#d17aa8"),
         ("cilantro", "Cilantro", "#3f9b4a"),
         ("limon", "Rodaja de limón", "#ecdf5a"),
         ("zero_waste_greens", "Zero-waste greens", "#6ea64f"),
         ("jalapenos", "Pickled jalapeños", "#5f8f2f"),
     )},
    {"key": "dressing", "title": "Dressing", "hint": "Elige 1", "min": 1, "max": 1, "style": "drizzle",
     "options": _opts(
         ("thai_peanut", "Thai peanut", "#c98a4b"),
         ("chipotle_lime", "Chipotle lime", "#d9773f"),
         ("green_tahini", "Green tahini", "#9cbf5a"),
         ("farmhouse_caesar", "Farmhouse caesar", "#efe0b0"),
         ("chili_gurt", "Chili-gurt", "#e8a98c"),
         ("carrot_bliss", "Carrot bliss vinaigrette", "#f29a42"),
         ("balsamic_vinaigrette", "Balsamic vinaigrette", "#6b3a2c"),
         ("pesto_vinaigrette", "Pesto vinaigrette", "#6b9a3a"),
         ("aceite_oliva", "Aceite de oliva", "#cbb63e"),
         ("vinagre_balsamico", "Vinagre balsámico", "#3b2420"),
         ("limon_exprimido", "Limón exprimido", "#efe48a"),
     )},
    {"key": "crunch", "title": "Crunch", "hint": "Elige 1", "min": 1, "max": 1, "style": "sprinkle",
     "options": _opts(
         ("almendras", "Almendras tostadas", "#c79a63"),
         ("arroz_crispy", "Arroz crispy", "#f3e7c6"),
         ("garbanzos_crispy", "Garbanzos crispy", "#d6a35a"),
         ("zaatar_crumbs", "Za'atar crumbs", "#9a9a55"),
         ("totopos", "Totopos", "#e9c56b"),
         ("pecans", "Pecans caramelizados", "#8e5a2c"),
         ("cebollitas", "Cebollitas crunchy", "#d9a85b"),
         ("chilli_crunch", "Chilli crunch", "#b8321f", "BYO_CHILLI_CRUNCH"),
     )},
]

ACAI_GROUPS = [
    {"key": "toppings", "title": "Toppings", "hint": "Elige 3", "min": 1, "max": 3, "style": "wedge",
     "options": _opts(
         ("fresa", "Fresa", "#e04b5a"),
         ("banana", "Banana", "#f3dc7a"),
         ("pina", "Piña", "#f2c230"),
         ("mango", "Mango", "#f5a623"),
         ("blueberries", "Blueberries", "#4a4f9c"),
     )},
    {"key": "crunch", "title": "Crunch", "hint": "Elige 1", "min": 1, "max": 1, "style": "sprinkle",
     "options": _opts(
         ("pecans", "Pecans caramelizadas", "#8e5a2c"),
         ("miso_tahini_granola", "Miso tahini granola", "#c9a46a"),
         ("chocolate_granola", "Chocolate granola", "#5a3a2a"),
         ("cacao_nibs", "Cacao nibs", "#3f2419"),
         ("coco_rallado", "Coco rallado", "#f7f2e6"),
     )},
]


def _single(key: str, title: str, *labels: str) -> Dict[str, Any]:
    """Grupo simple de 'elige 1', sin dibujo."""
    return {"key": key, "title": title, "hint": "Elige 1", "min": 1, "max": 1, "style": "pill",
            "options": [{"key": f"o{i}", "label": lab, "color": None} for i, lab in enumerate(labels, 1)]}


LIQUID = _single("base", "¿Con qué lo preparamos?", "Agua de pipa", "Leche de almendras")
TEMPERATURE = _single("temperatura", "¿Caliente o frío?", "Caliente", "Frío")

# SKU -> {visual, groups}. visual: "bowl" (ensalada/bowl), "acai" o None (solo botones).
BUILDERS: Dict[str, Dict[str, Any]] = {
    "BYO_REG": {"visual": "bowl", "groups": BYO_GROUPS},
    "BYO_LRG": {"visual": "bowl", "groups": BYO_GROUPS},
    "BWL_ACAI": {"visual": "acai", "groups": ACAI_GROUPS},
    "DRK_INFUSIONES": {"visual": None, "groups": [
        _single("infusion", "Elige tu infusión", "Té verde", "Manzanilla", "Jazmín", "Frutos rojos", "Earl grey", "Thai lemon-ginger")]},
    "DRK_COCACOLA": {"visual": None, "groups": [_single("tipo", "¿Cuál prefieres?", "Regular", "Zero")]},
    "SMO_MANGO": {"visual": None, "groups": [LIQUID]},
    "SMO_PAPAYA": {"visual": None, "groups": [LIQUID]},
    "DRK_MATCHA_LATTE": {"visual": None, "groups": [TEMPERATURE]},
    "DRK_FRUITY_MATCHA": {"visual": None, "groups": [_single("fruta", "¿Con qué fruta?", "Fresas", "Mango")]},
    "DRK_CHAI_LATTE": {"visual": None, "groups": [_single("base", "¿En agua o en leche?", "En agua", "En leche"), TEMPERATURE]},
}

# SKUs que solo existen como opción con costo dentro de un producto (no se listan como plato).
OPTION_ONLY_SKUS = {
    opt["price_sku"]
    for b in BUILDERS.values() for g in b["groups"] for opt in g["options"] if opt.get("price_sku")
}


def is_repeatable(group: Dict[str, Any]) -> bool:
    """Toppings y crunch de más de una opción se pueden repetir; la base no (2x quinoa no es otra base)."""
    return group["max"] > 1 and group["style"] != "base"


def builder_for(sku: str) -> Optional[Dict[str, Any]]:
    return BUILDERS.get(sku)


def resolve_choices(sku: str, choices: Optional[Dict[str, List[str]]], product_title: str = "") -> Dict[str, Any]:
    """
    Valida lo que eligió el cliente contra el grupo de opciones del producto y devuelve
    {"groups": [{"title", "items": [labels]}], "price_skus": [sku, ...]}.
    Un producto sin opciones no acepta `choices`; uno con opciones exige el mínimo de cada grupo.
    """
    choices = choices or {}
    builder = BUILDERS.get(sku)
    if not builder:
        if any(choices.values()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{product_title or sku} no lleva opciones para elegir.")
        return {"groups": [], "price_skus": []}

    known = {g["key"] for g in builder["groups"]}
    unknown = [k for k in choices if k not in known]
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Opción no válida para {product_title or sku}.")

    groups, price_skus = [], []
    for group in builder["groups"]:
        picked = choices.get(group["key"]) or []
        # Los toppings se pueden repetir (3 de fresa, o 2 de banana y 1 de piña) sin pasar del máximo.
        if len(set(picked)) != len(picked) and not is_repeatable(group):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{group['title']}: hay una opción repetida.")
        by_key = {o["key"]: o for o in group["options"]}
        bad = [k for k in picked if k not in by_key]
        if bad:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{group['title']}: esa opción ya no está en el menú.")
        if len(picked) < group["min"]:
            falta = "una opción" if group["min"] == 1 else f"al menos {group['min']} opciones"
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{product_title or sku}: elige {falta} de {group['title']}.")
        if len(picked) > group["max"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{product_title or sku}: en {group['title']} se pueden elegir hasta {group['max']}.")
        if picked:
            counts: Dict[str, int] = {}
            for k in picked:
                counts[k] = counts.get(k, 0) + 1
            groups.append({"title": group["title"], "items": [
                f"{n}x {by_key[k]['label']}" if n > 1 else by_key[k]["label"] for k, n in counts.items()
            ]})
            price_skus += [by_key[k]["price_sku"] for k in picked if by_key[k].get("price_sku")]
    return {"groups": groups, "price_skus": price_skus}


def public_builder(sku: str) -> Optional[Dict[str, Any]]:
    """Lo que necesita /menu para dibujar las opciones (sin datos internos)."""
    builder = BUILDERS.get(sku)
    if not builder:
        return None
    from services.menu_catalog import get_item_by_sku  # evita import circular

    groups = []
    for g in builder["groups"]:
        options = []
        for o in g["options"]:
            opt = {"key": o["key"], "label": o["label"], "color": o["color"]}
            if o.get("price_sku"):
                item = get_item_by_sku(o["price_sku"])
                if not item:
                    continue
                opt["price"] = float(item["price"])
            options.append(opt)
        groups.append({k: g[k] for k in ("key", "title", "hint", "min", "max", "style")}
                      | {"repeatable": is_repeatable(g), "options": options})
    return {"visual": builder["visual"], "groups": groups}
