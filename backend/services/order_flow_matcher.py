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


def match_payment_method_text(customer_text: str, allowed: List[str]) -> Optional[str]:
    if not customer_text:
        return None
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


def mentions_cash(customer_text: str) -> bool:
    """Para detectar cuando alguien pide efectivo aunque no esté permitido (ej. en delivery)."""
    if not customer_text:
        return False
    normalized = normalize_text(customer_text)
    return any(kw in normalized for kw in CASH_KEYWORDS)
