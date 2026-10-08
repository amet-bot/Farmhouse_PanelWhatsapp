"""
Envía los pedidos confirmados del Menú Digital a la pantalla de Invu (comandas del POS).

Usa `citas/add`, que Invu NO habilita por defecto: hay que pasar la certificación con su equipo
de Integraciones (integraciones@invupos.com). La documentación pública no publica el cuerpo de
ese endpoint, así que `build_payload` es un BORRADOR y es el único lugar que hay que ajustar
cuando Invu entregue el esquema (campos, códigos de plato, modificadores, tipo de orden, pago).

Reglas de seguridad:
- Apagado de fábrica (INVU_ORDER_PUSH_ENABLED=false): sin la bandera no sale nada hacia Invu.
- Nunca lanza: corre en segundo plano y un fallo de Invu no puede afectar el pedido del cliente.
- Una orden se envía una sola vez: el resultado queda en items_json["invu_push"] y, si ya dice
  "sent", no se reenvía (un reintento podría duplicar la comanda en la cocina).
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import settings
from database import SessionLocal
from models.branch import Branch
from models.order import Order
from services import invu_client

logger = logging.getLogger("farmhouse.invu_order_push")

PUSH_KEY = "invu_push"


def build_payload(order: Order, data: Dict[str, Any], customer_name: str, customer_phone: str) -> Dict[str, Any]:
    """
    BORRADOR del cuerpo de `citas/add`. Ajustar al esquema que entregue Invu en la certificación.
    Hoy manda el código del plato del menú web (`sku`) como identificador, lo que casi seguro hay
    que cambiar por el código de plato de Invu (ver invu_client.iter_menu_items).
    """
    items = []
    for line in data.get("items") or []:
        items.append({
            "sku": line.get("sku"),
            "name": line.get("title"),
            "quantity": line.get("quantity"),
            "unit_price": line.get("unit_price"),
            "modifiers": [
                {"sku": a.get("sku"), "name": a.get("title"), "quantity": a.get("quantity"), "price": a.get("price")}
                for a in line.get("addons") or []
            ],
            "notes": line.get("notes"),
        })
    return {
        "reference": order.order_code,
        "order_type": order.order_type,  # "takeout" | "delivery"
        "customer": {"name": customer_name, "phone": customer_phone},
        "delivery_address": data.get("delivery_address"),
        "scheduled_for": data.get("scheduled_for"),
        "payment_method": data.get("payment_method"),
        "items": items,
        "subtotal": float(order.subtotal),
        "delivery_cost": float(order.delivery_cost),
        "total": float(order.total),
    }


def _record(db, order: Order, data: Dict[str, Any], status: str, detail: Optional[str] = None, response: Any = None) -> None:
    data[PUSH_KEY] = {
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
        **({"detail": detail} if detail else {}),
        **({"response": response} if response is not None else {}),
    }
    order.items_json = json.dumps(data, ensure_ascii=False)
    db.commit()


def push_order_to_invu(order_id: int) -> Optional[str]:
    """Devuelve el estado resultante ("sent", "failed") o None si no correspondía enviar."""
    if not settings.INVU_ORDER_PUSH_ENABLED:
        return None

    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id, Order.deleted_at.is_(None)).first()
        if not order or not order.items_json:
            return None
        data = json.loads(order.items_json)
        if data.get("source") != "menu_web":
            return None
        if (data.get(PUSH_KEY) or {}).get("status") == "sent":
            return None

        branch = db.query(Branch).filter(Branch.id == order.branch_id).first()
        credenciales = settings.invu_branch_credentials().get(branch.code) if branch else None
        if not credenciales:
            _record(db, order, data, "skipped", "La sucursal no tiene usuario de API de Invu configurado.")
            logger.warning(f"[InvuPush] {order.order_code}: sin credenciales de Invu para la sucursal.")
            return None

        contact = order.conversation.contact if order.conversation else None
        payload = build_payload(
            order, data,
            customer_name=(contact.name if contact else "") or "",
            customer_phone=(contact.phone if contact else "") or "",
        )
        try:
            respuesta = invu_client.create_order(invu_client.Credenciales(*credenciales), payload)
        except invu_client.InvuError as e:
            _record(db, order, data, "failed", str(e))
            logger.error(f"[InvuPush] {order.order_code}: {e}")
            return "failed"

        _record(db, order, data, "sent", response=respuesta if isinstance(respuesta, (dict, list)) else str(respuesta))
        logger.info(f"[InvuPush] {order.order_code} enviada a Invu ({branch.code}).")
        return "sent"
    except Exception:  # noqa: BLE001 - segundo plano: nada puede propagarse al cliente
        logger.exception(f"[InvuPush] Error inesperado enviando la orden {order_id}.")
        return "failed"
    finally:
        db.close()
