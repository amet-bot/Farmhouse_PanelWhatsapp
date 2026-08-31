import re
import unicodedata
from difflib import SequenceMatcher
from typing import List, Optional

from models.branch import Branch


def normalize_text(text: str) -> str:
    """Quita acentos, pasa a minúsculas y limpia espacios extra."""
    text = text.strip().lower()
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def match_branch_by_text(customer_text: str, branches: List[Branch]) -> Optional[Branch]:
    """
    Intenta detectar si el texto libre de un cliente menciona el nombre de
    alguna sucursal activa. No usa IA: usa coincidencia normalizada,
    coincidencia por substring, y similitud aproximada (para typos leves).
    Devuelve la sucursal si hay una coincidencia razonablemente clara, o None.
    """
    if not customer_text or not branches:
        return None

    normalized_text = normalize_text(customer_text)
    if not normalized_text:
        return None

    best_branch = None
    best_score = 0.0

    for branch in branches:
        normalized_name = normalize_text(branch.name)
        if not normalized_name:
            continue

        # 1. Coincidencia exacta del nombre completo
        if normalized_text == normalized_name:
            return branch

        # 2. El nombre de la sucursal aparece como palabra/frase dentro del mensaje
        #    (ej: "quiero pedir en obarrio porfa" contiene "obarrio")
        if re.search(r'\b' + re.escape(normalized_name) + r'\b', normalized_text):
            return branch

        # 3. Similitud aproximada por si hay errores de tipeo leves
        score = SequenceMatcher(None, normalized_text, normalized_name).ratio()
        if score > best_score:
            best_score = score
            best_branch = branch

    # Umbral conservador: solo aceptamos la coincidencia aproximada si es
    # bastante alta, para evitar asignar la sucursal equivocada por error.
    if best_score >= 0.72:
        return best_branch

    return None
