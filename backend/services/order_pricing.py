"""
Fuente única de verdad para calcular subtotal / delivery / total de un pedido o de un
carrito activo del Menú Digital. Nunca confía en precios que manda el navegador: cada
línea se recalcula contra el catálogo del servidor (services.menu_catalog).

Usada por:
- POST /api/orders/public   (pedido confirmado)
- PUT  /api/orders/cart     (carrito activo, sincronizado en tiempo real desde /menu)
- El texto del mensaje de WhatsApp (routers/orders._build_whatsapp_order_text)

Fórmula (Punto 3 del pedido del usuario):
    subtotal = suma de (precio_base + adicionales) * cantidad, por cada línea
    total    = subtotal + delivery_fee
"""
from decimal import Decimal
from typing import List, Tuple

from fastapi import HTTPException, status
from services.menu_catalog import get_item_by_sku, clean_item_title

DELIVERY_SURCHARGE = Decimal("0.00")


def compute_delivery_fee(delivery_type: str) -> Decimal:
    """Único lugar que decide el costo automático de Delivery en el menú.
    Ahora el costo de delivery es a criterio de la sucursal/agente según distancia,
    por lo que el sistema no fija un monto automático."""
    return Decimal("0.00")


def price_cart_items(items: List) -> Tuple[list, Decimal]:
    """
    Recalcula cada línea (sku, cantidad, adicionales) contra el catálogo del servidor.
    `items` es una lista de objetos con atributos .sku, .quantity, .addon_skus, .notes
    (PublicOrderItem o CartItemIn, ambos con esa forma).

    Devuelve (line_items, subtotal) donde cada line_item es un dict serializable en
    items_json y en la respuesta del carrito/pedido.
    """
    line_items = []
    subtotal = Decimal("0.00")

    for raw_item in items:
        catalog_item = get_item_by_sku(raw_item.sku)
        if not catalog_item:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Producto no encontrado en el catálogo: {raw_item.sku}")

        addons = []
        addons_total = Decimal("0.00")
        for addon_sku in raw_item.addon_skus:
            addon_item = get_item_by_sku(addon_sku)
            if not addon_item:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Adicional no encontrado en el catálogo: {addon_sku}")
            addons.append({"sku": addon_item["sku"], "title": clean_item_title(addon_item["title"]), "price": float(addon_item["price"])})
            addons_total += addon_item["price"]

        unit_price = catalog_item["price"] + addons_total
        line_total = (unit_price * raw_item.quantity).quantize(Decimal("0.01"))
        subtotal += line_total

        line_items.append({
            "sku": catalog_item["sku"],
            "title": catalog_item["title"],
            "quantity": raw_item.quantity,
            "unit_price": float(catalog_item["price"]),
            "addons": addons,
            "notes": raw_item.notes,
            "line_total": float(line_total),
        })

    subtotal = subtotal.quantize(Decimal("0.01"))
    return line_items, subtotal
