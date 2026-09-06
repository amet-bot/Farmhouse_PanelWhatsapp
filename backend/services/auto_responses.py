"""
Farmhouse WhatsApp Center - Mensajes de Respuesta Automática
Configuración centralizada de mensajes y saludos automáticos del sistema.
"""
from typing import Optional

# Nombre de respaldo que usa parse_incoming_message cuando Meta no manda un perfil de contacto
# (ver services/whatsapp_service.py). Nunca se usa como saludo personalizado, se trata como
# "no sabemos el nombre".
GENERIC_CONTACT_NAMES = {"cliente whatsapp"}


def get_main_welcome_body(customer_name: Optional[str] = None) -> str:
    """Saludo inicial del bot, personalizado con el nombre real de WhatsApp del cliente cuando
    se conoce (y no es el nombre genérico de respaldo), para que se sienta menos robótico."""
    name = (customer_name or "").strip()
    saludo = f"¡Hola, {name}! Bienvenido a farmhouse." if name and name.lower() not in GENERIC_CONTACT_NAMES else "¡Hola! Bienvenido a farmhouse."
    return (
        f"{saludo}\n\n"
        "¿Cómo te podemos ayudar hoy?\n\n"
        "(1) Quiero visitarlos en una de sus sucursales\n"
        "(2) Quiero hacer un pedido a domicilio\n"
        "(3) Quiero hacer un pedido para retirar en el local\n"
        "(4) Quiero coordinar un pedido corporativo u organizar un evento."
    )


MAIN_WELCOME_BODY = get_main_welcome_body(None)

MAIN_MENU_BUTTON = "Ver opciones"

MAIN_MENU_OPTIONS = [
    {"id": "opt_visit", "title": "(1) Visitar sucursales", "description": "Quiero visitarlos en una de sus sucursales"},
    {"id": "opt_delivery", "title": "(2) Pedido a domicilio", "description": "Quiero hacer un pedido a domicilio"},
    {"id": "opt_pickup", "title": "(3) Retiro en el local", "description": "Quiero hacer un pedido para retirar en el local"},
    {"id": "opt_corporate", "title": "(4) Evento / Corporativo", "description": "Quiero coordinar un pedido corporativo u organizar un evento"},
]

MAIN_MENU_TEXT_FALLBACK = MAIN_WELCOME_BODY

WELCOME_MESSAGES = [
    MAIN_WELCOME_BODY,
    "¿Cómo te podemos ayudar hoy?"
]

BRANCH_SELECTION_BODY = "¿Cuál de nuestras sucursales te gustaría contactar?"
BRANCH_SELECTION_VISIT_BODY = "¡Excelente! Elige una de nuestras sucursales:"
BRANCH_SELECTION_DELIVERY_BODY = "¡Excelente! 🛵 ¿Para cuál de nuestras sucursales deseas solicitar tu delivery?"
BRANCH_SELECTION_PICKUP_BODY = "¡Excelente! 🛍️ Elige la sucursal en la que quieres hacer tu pedido:"
BRANCH_SELECTION_BUTTON = "Ver sucursales"

CORPORATE_INTAKE_INTRO = (
    "¡Qué gran noticia! 🎉 En Farmhouse nos encanta atender pedidos corporativos, reuniones de oficina, catering y eventos especiales.\n\n"
    "Para armarte la mejor propuesta, te hago unas preguntas rápidas antes de comunicarte con Sol, nuestra encargada de eventos y cuentas corporativas 😊"
)

CORPORATE_EVENT_TYPE_QUESTION = "¿Qué tipo de evento tienes en mente?"
CORPORATE_EVENT_TYPE_BUTTONS = [
    {"id": "event_type_meeting", "title": "Reunión corporativa"},
    {"id": "event_type_celebration", "title": "Evento especial"},
    {"id": "event_type_other", "title": "Otro"},
]
CORPORATE_EVENT_TYPE_LABELS = {
    "meeting": "Reunión corporativa / oficina",
    "celebration": "Celebración o evento especial",
    "other": "Otro",
}

CORPORATE_HEADCOUNT_QUESTION = "¡Perfecto! ¿Para cuántas personas sería, aproximadamente?"
CORPORATE_HEADCOUNT_RETRY = "¿Me confirmas para cuántas personas sería, aproximadamente?"

CORPORATE_DATE_QUESTION = "¡Genial! ¿Tienes fecha y hora en mente?"
CORPORATE_DATE_RETRY = "¿Me compartes la fecha y hora que tienes en mente?"

CORPORATE_LOCATION_QUESTION = "Última pregunta: ¿dónde te gustaría recibir el pedido?"
CORPORATE_LOCATION_BUTTONS = [
    {"id": "event_loc_pickup", "title": "Retiro en sucursal"},
    {"id": "event_loc_delivery", "title": "Entrega en mi lugar"},
    {"id": "event_loc_undecided", "title": "Aún no lo sé"},
]
CORPORATE_LOCATION_LABELS = {
    "pickup": "Retiro en una de nuestras sucursales",
    "delivery": "Entrega en su oficina o el lugar del evento",
    "undecided": "Aún no lo sabe, se coordina con Sol",
}

CORPORATE_INVALID_OPTION_RETRY = "No entendí bien, ¿me eliges una de estas opciones?"

CORPORATE_INTAKE_CLOSING_MESSAGE = (
    "¡Listo! 🎉 Ya tengo todo lo que Sol necesita para armarte una propuesta. En un momento te comunico con ella por este mismo chat. "
    "¡Gracias por pensar en Farmhouse para tu evento! 😊"
)

def get_corporate_intake_summary(notes: str) -> str:
    return f"📋 Resumen para Sol (Pedido Corporativo / Evento):\n{notes}"

BRANCH_VISIT_INFO = {
    "CDE": {
        "name": "Costa del Este",
        "address": "Costa del Este, Plaza Real, Planta Baja",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=Farmhouse+Costa+del+Este"
    },
    "SF": {
        "name": "San Francisco",
        "address": "Calle 74 Este, San Francisco",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=Farmhouse+San+Francisco+Panama"
    },
    "CLY": {
        "name": "Clayton",
        "address": "Clayton Mall, Local #4",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=Farmhouse+Clayton+Panama"
    },
    "OBR": {
        "name": "Obarrio",
        "address": "Calle 57 Este, Obarrio",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=Farmhouse+Obarrio+Panama"
    },
    "VP": {
        "name": "Vía Porras",
        "address": "Vía Porras, San Francisco",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=Farmhouse+Via+Porras+Panama"
    }
}

MANAGER_HELP_QUESTION = "¿Te podemos ayudar en algo más?"
MANAGER_HELP_BUTTONS = [
    {"id": "manager_yes", "title": "Hablar con gerente"},
    {"id": "view_menu", "title": "Ver el menú"},
    {"id": "manager_no", "title": "Nos vemos pronto"},
]
MANAGER_HELP_OPTIONS = [
    {"id": "manager_yes", "title": "(1) Hablar con gerente", "description": "Sí, me encantaría hablar con un gerente"},
    {"id": "view_menu", "title": "(2) Ver el menú", "description": "Quiero ver el menú"},
    {"id": "manager_no", "title": "(3) Nos vemos pronto", "description": "No, gracias, nos vemos pronto"},
]

def get_branch_info_message(branch_name: str, branch_code: str, opening_line: str) -> str:
    """Arma un único mensaje con la dirección, el horario y el link de Maps de una sucursal (Punto de venta físico)."""
    info = BRANCH_VISIT_INFO.get(branch_code)
    lines = [opening_line]
    if info:
        if info.get("address"):
            lines.append(f"📍 {info['address']}")
        if info.get("hours"):
            lines.append(f"🕒 {info['hours']}")
        if info.get("maps_url"):
            lines.append(f"🗺️ Ubícanos en Google Maps: {info['maps_url']}")
    return "\n".join(lines)

def get_branch_visit_message(branch_code: str, branch_name: str) -> str:
    return get_branch_info_message(branch_name, branch_code, f"¡Excelente! Te esperamos en la sucursal de *{branch_name}*.")

def get_branch_pickup_info_message(branch_code: str, branch_name: str) -> str:
    return get_branch_info_message(branch_name, branch_code, f"¡Perfecto! 🛍️ Retirarás tu pedido en nuestra sucursal de *{branch_name}*.")

def get_branch_delivery_info_message(branch_code: str, branch_name: str) -> str:
    return get_branch_info_message(branch_name, branch_code, f"¡Excelente! 🛵 Tu pedido a domicilio saldrá de nuestra sucursal de *{branch_name}*.")

# Cierre cálido tras mandar el botón del Menú Digital en delivery/pickup: deja la puerta abierta
# sin forzar otra decisión de botones (el bot ya detecta por texto libre si piden un humano).
MENU_LINK_WARM_CLOSING = "Cualquier duda que tengas mientras armas tu pedido, aquí estamos para ayudarte con todo gusto 😊"

def get_manager_assigned_message(branch_name: str) -> str:
    return (
        f"¡Con mucho gusto! 🤝 Te comunicamos de inmediato con el gerente de nuestra sucursal de *{branch_name}*.\n\n"
        f"En un momento te estará atendiendo personalmente por aquí. ¡Muchas gracias por tu paciencia! 😊"
    )

def get_manager_declined_message(branch_name: str) -> str:
    return (
        f"¡Perfecto! Muchas gracias por escribirnos. ¡Te esperamos pronto en Farmhouse *{branch_name}*! "
        f"Que tengas un excelente día 🌿✨"
    )

def get_branch_welcome_message(branch_name: str) -> str:
    return f"¡Bienvenido a Farmhouse {branch_name}! 🌿 Un gusto atenderte."

ACH_PAYMENT_INSTRUCTIONS = (
    "¡Perfecto! 🏦 Estos son los datos de nuestra cuenta para pagar por ACH:\n\n"
    "Banco: Banco General\n"
    "Tipo de cuenta: Cuenta corriente\n"
    "Nombre de cuenta: Grupo Col Rizado\n"
    "Número de cuenta: 03-01-01-1480750\n\n"
    "En cuanto nuestro equipo te confirme el total de tu pedido, puedes hacer la transferencia a esta cuenta. "
    "Cuando la hagas, ¿me regalas una foto del comprobante de pago? Así agilizamos tu pedido muchísimo más rápido. "
    "¡Muchas gracias por tu paciencia! 😊"
)

CARD_PAYMENT_MESSAGE = (
    "¡Perfecto! 💳 Como seleccionaste pago con tarjeta, en un momento nuestro agente de turno te enviará "
    "por este chat el enlace de pago seguro para que puedas completar tu compra cómodamente con tu tarjeta de crédito o débito.\n\n"
    "Por favor regálanos unos breves minutos mientras lo generamos para ti. ¡Muchas gracias por tu paciencia y preferencia! 😊✨"
)

YAPPY_PAYMENT_MESSAGE = (
    "¡Perfecto! 📱 En un momento nuestro equipo te comparte el número para pagar por Yappy. "
    "Cuando hagas el pago, ¿me regalas una captura del comprobante? Así agilizamos tu pedido muchísimo más rápido. "
    "¡Muchas gracias por tu paciencia! 😊"
)

CASH_PAYMENT_MESSAGE = (
    "¡Perfecto! 💵 Puedes pagar en efectivo cuando recibas tu pedido (o cuando lo retires en el local). "
    "En un momento alguien de nuestro equipo te atiende para tomar los detalles de tu pedido. "
    "¡Gracias por tu paciencia! 😊"
)
