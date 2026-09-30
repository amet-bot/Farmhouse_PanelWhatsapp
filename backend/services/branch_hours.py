"""
Horario de atención de una sucursal, para que el bot sepa si está cerrada en este momento.

Cada sucursal puede tener su propio horario (Branch.opens_at / closes_at, "HH:MM", hora de
Panamá, editable en Administración -> Sucursales). Si no tiene ninguno cargado se asume el
horario general de Farmhouse (DEFAULT_OPENS_AT / DEFAULT_CLOSES_AT), el mismo que muestran los
mensajes de dirección y horario de services/auto_responses.py.
"""
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

PANAMA_TZ = ZoneInfo("America/Panama")
DEFAULT_OPENS_AT = "10:30"
DEFAULT_CLOSES_AT = "21:30"


def parse_hhmm(value: Optional[str]) -> Optional[time]:
    """"10:30" -> time(10, 30). Devuelve None si el texto no es una hora válida."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        hours, minutes = raw.split(":", 1)
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        return None


def format_12h(value: time) -> str:
    """time(10, 30) -> "10:30 AM", como se escribe en los mensajes del bot."""
    return value.strftime("%I:%M %p").lstrip("0")


def branch_schedule(branch) -> tuple[time, time]:
    opens = parse_hhmm(getattr(branch, "opens_at", None)) or parse_hhmm(DEFAULT_OPENS_AT)
    closes = parse_hhmm(getattr(branch, "closes_at", None)) or parse_hhmm(DEFAULT_CLOSES_AT)
    return opens, closes


def is_branch_open(branch, now: Optional[datetime] = None) -> bool:
    """True si la sucursal atiende en este momento (hora de Panamá). Un horario que cruza la
    medianoche (ej. 18:00 a 02:00) también se contempla."""
    opens, closes = branch_schedule(branch)
    current = (now or datetime.now(PANAMA_TZ)).astimezone(PANAMA_TZ).time()
    if opens <= closes:
        return opens <= current < closes
    return current >= opens or current < closes


def branch_opening_label(branch) -> str:
    """"10:30 AM": la hora a la que abre, para decirle al cliente cuándo vuelve a atender."""
    opens, _ = branch_schedule(branch)
    return format_12h(opens)
