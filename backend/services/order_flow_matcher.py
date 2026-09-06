from typing import List, Optional

from services.branch_matcher import normalize_text

DELIVERY_KEYWORDS = ["delivery", "domicilio", "envio", "traer", "llevar", "a mi casa"]
PICKUP_KEYWORDS = ["retiro", "recoger", "recojo", "pasar a buscar", "en el local", "pickup", "voy a buscar"]

CARD_KEYWORDS = ["tarjeta", "visa", "mastercard", "master card", "credito", "debito"]
YAPPY_KEYWORDS = ["yappy"]
CASH_KEYWORDS = ["efectivo", "cash", "contado"]
ACH_KEYWORDS = ["ach", "transferencia", "banco general", "banco", "transferir", "deposito"]


def match_delivery_type_text(customer_text: str) -> Optional[str]:
    if not customer_text:
        return None
    normalized = normalize_text(customer_text)
    if any(kw in normalized for kw in DELIVERY_KEYWORDS):
        return "delivery"
    if any(kw in normalized for kw in PICKUP_KEYWORDS):
        return "pickup"
    return None


def match_payment_method_text(customer_text: str, allowed: Optional[List[str]] = None) -> Optional[str]:
    if not customer_text:
        return None
    if allowed is None:
        allowed = ["card", "yappy", "cash", "ach"]
    normalized = normalize_text(customer_text)
    if "card" in allowed and any(kw in normalized for kw in CARD_KEYWORDS):
        return "card"
    if "yappy" in allowed and any(kw in normalized for kw in YAPPY_KEYWORDS):
        return "yappy"
    if "cash" in allowed and any(kw in normalized for kw in CASH_KEYWORDS):
        return "cash"
    if "ach" in allowed and any(kw in normalized for kw in ACH_KEYWORDS):
        return "ach"
    return None


MAIN_OPTION_VISIT_KEYWORDS = ["1", "visitar", "visita", "visitar sucursal", "visitar sucursales", "ubicacion", "ubicaciones", "horario", "horarios", "direccion", "donde estan", "donde queda"]
MAIN_OPTION_DELIVERY_KEYWORDS = ["2", "delivery", "domicilio", "a domicilio", "pedido a domicilio", "a mi casa", "llevar", "traer", "envio"]
MAIN_OPTION_PICKUP_KEYWORDS = ["3", "retiro", "retirar", "pickup", "recoger", "recojo", "retirar en local", "en el local", "pasar a buscar", "pasar a recoger"]
MAIN_OPTION_CORPORATE_KEYWORDS = ["4", "corporativo", "coorporativo", "evento", "eventos", "catering", "empresa", "reunion", "organizar un evento", "pedido corporativo"]


def match_main_option(customer_text: str) -> Optional[str]:
    if not customer_text:
        return None
    normalized = normalize_text(customer_text)
    
    # Exact numeric choices
    if normalized in ["1", "opcion 1", "opt 1", "1 visitar", "1 visitar sucursal"]:
        return "visit"
    if normalized in ["2", "opcion 2", "opt 2", "2 delivery", "2 domicilio", "2 pedido a domicilio"]:
        return "delivery"
    if normalized in ["3", "opcion 3", "opt 3", "3 retiro", "3 pickup", "3 retirar en local"]:
        return "pickup"
    if normalized in ["4", "opcion 4", "opt 4", "4 corporativo", "4 evento", "4 eventos"]:
        return "corporate"

    if any(kw in normalized for kw in MAIN_OPTION_CORPORATE_KEYWORDS if kw != "4"):
        return "corporate"
    if any(kw in normalized for kw in MAIN_OPTION_VISIT_KEYWORDS if kw != "1"):
        return "visit"
    if any(kw in normalized for kw in MAIN_OPTION_PICKUP_KEYWORDS if kw != "3"):
        return "pickup"
    if any(kw in normalized for kw in MAIN_OPTION_DELIVERY_KEYWORDS if kw != "2"):
        return "delivery"

    return None


def match_manager_help(customer_text: str) -> Optional[str]:
    if not customer_text:
        return None
    normalized = normalize_text(customer_text)

    # Exact numeric/opción choices en este contexto (1=gerente, 2=ver menú, 3=no gracias)
    if normalized in ["1", "opcion 1", "opt 1", "si", "si por favor"]:
        return "yes"
    if normalized in ["2", "opcion 2", "opt 2"]:
        return "menu"
    if normalized in ["3", "opcion 3", "opt 3", "no", "no gracias"]:
        return "no"

    if any(kw in normalized for kw in ["ver menu", "ver el menu", "quiero ver el menu", "mostrar menu", "el menu"]):
        return "menu"
    if any(kw in normalized for kw in ["gerente", "hablar con gerente", "hablar con un gerente", "encataria", "encantaria", "hablar"]):
        return "yes"
    if any(kw in normalized for kw in ["nos vemos pronto", "nos vemos", "no gracias", "hasta luego"]):
        return "no"

    return None


EVENT_TYPE_MEETING_KEYWORDS = ["reunion", "reunion corporativa", "oficina", "corporativa", "corporativo", "empresa"]
EVENT_TYPE_CELEBRATION_KEYWORDS = ["celebracion", "cumpleanos", "fiesta", "especial", "evento especial", "aniversario"]


def match_event_type(customer_text: str) -> Optional[str]:
    """Para la pregunta guiada '¿Qué tipo de evento tienes en mente?' del flujo Corporativo/Evento."""
    if not customer_text:
        return None
    normalized = normalize_text(customer_text)

    if normalized in ["1", "opcion 1", "opt 1"]:
        return "meeting"
    if normalized in ["2", "opcion 2", "opt 2"]:
        return "celebration"
    if normalized in ["3", "opcion 3", "opt 3"]:
        return "other"

    if any(kw in normalized for kw in EVENT_TYPE_MEETING_KEYWORDS):
        return "meeting"
    if any(kw in normalized for kw in EVENT_TYPE_CELEBRATION_KEYWORDS):
        return "celebration"
    if "otro" in normalized:
        return "other"

    return None


EVENT_LOCATION_PICKUP_KEYWORDS = ["retiro", "retirar", "recoger", "recojo", "sucursal", "pasar a buscar"]
EVENT_LOCATION_DELIVERY_KEYWORDS = ["entrega", "mi oficina", "mi lugar", "domicilio", "en el evento", "en mi empresa"]
EVENT_LOCATION_UNDECIDED_KEYWORDS = ["no se", "no lo se", "aun no", "coordinar", "despues lo vemos"]


def match_event_location(customer_text: str) -> Optional[str]:
    """Para la pregunta guiada '¿Dónde te gustaría recibir el pedido?' del flujo Corporativo/Evento."""
    if not customer_text:
        return None
    normalized = normalize_text(customer_text)

    if normalized in ["1", "opcion 1", "opt 1"]:
        return "pickup"
    if normalized in ["2", "opcion 2", "opt 2"]:
        return "delivery"
    if normalized in ["3", "opcion 3", "opt 3"]:
        return "undecided"

    if any(kw in normalized for kw in EVENT_LOCATION_UNDECIDED_KEYWORDS):
        return "undecided"
    if any(kw in normalized for kw in EVENT_LOCATION_PICKUP_KEYWORDS):
        return "pickup"
    if any(kw in normalized for kw in EVENT_LOCATION_DELIVERY_KEYWORDS):
        return "delivery"

    return None


def mentions_cash(customer_text: str) -> bool:
    """Para detectar cuando alguien pide efectivo aunque no esté permitido (ej. en delivery)."""
    if not customer_text:
        return False
    normalized = normalize_text(customer_text)
    return any(kw in normalized for kw in CASH_KEYWORDS)
