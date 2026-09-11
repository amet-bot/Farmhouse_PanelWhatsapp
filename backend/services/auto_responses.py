"""
Farmhouse WhatsApp Center - Mensajes de Respuesta Automática
Configuración centralizada de mensajes y saludos automáticos del sistema.
"""
from typing import Optional

# Nombre de respaldo que usa parse_incoming_message cuando Meta no manda un perfil de contacto
# (ver services/whatsapp_service.py). Nunca se usa como saludo personalizado, se trata como
# "no sabemos el nombre".
GENERIC_CONTACT_NAMES = {"cliente whatsapp"}


def get_customer_first_name(customer_name: Optional[str] = None) -> str:
    name = (customer_name or "").strip()
    if not name or name.lower() in GENERIC_CONTACT_NAMES:
        return ""
    return name.split()[0]


def get_main_welcome_body(customer_name: Optional[str] = None) -> str:
    """Saludo inicial del bot, personalizado con el nombre real de WhatsApp del cliente cuando
    se conoce (y no es el nombre genérico de respaldo), para que se sienta menos robótico."""
    name = (customer_name or "").strip()
    first_name = get_customer_first_name(name)
    saludo = f"¡Hola, {first_name}! 👋" if first_name else "¡Hola! 👋"
    return f"{saludo} Soy el asistente de Farmhouse 🌿\n\n¿Qué te gustaría hacer hoy? También puedes escribirme con tus propias palabras."


MAIN_WELCOME_BODY = get_main_welcome_body(None)

# WhatsApp admite un máximo de tres respuestas rápidas, así que este set de 3 botones se usa
# solo como recuperación (cuando el bot no entendió un texto libre). El menú de bienvenida
# principal usa la lista de abajo, que sí puede mostrar las 5 opciones de una vez.
MAIN_MENU_BUTTONS = [
    {"id": "main_order", "title": "Hacer un pedido"},
    {"id": "main_visit", "title": "Ver sucursales"},
    {"id": "main_human", "title": "Hablar con alguien"},
]

# Menú de bienvenida como lista interactiva (hasta 10 filas): muestra Delivery/Retiro/Evento
# directamente, sin el paso intermedio de "Hacer un pedido" -> submenú de tipo de entrega.
# Los ids coinciden con los que ya reconoce el submenú de tipo de entrega (ORDER_TYPE_BUTTONS)
# para no duplicar lógica de despacho en webhooks.py.
MAIN_MENU_LIST_BUTTON = "Elegir opción"
MAIN_MENU_LIST_ROWS = [
    {"id": "order_delivery", "title": "Delivery", "description": "Pedido a domicilio"},
    {"id": "order_pickup", "title": "Retiro en local", "description": "Pasas a recoger tu pedido"},
    {"id": "order_corporate", "title": "Evento o empresa", "description": "Catering y pedidos corporativos"},
    {"id": "main_visit", "title": "Ver sucursales", "description": "Direcciones y horarios"},
    {"id": "main_human", "title": "Hablar con alguien", "description": "Te atiende una persona del equipo"},
]

ORDER_TYPE_QUESTION = "¡Claro! ¿Cómo quieres recibir tu pedido?"
ORDER_TYPE_BUTTONS = [
    {"id": "order_delivery", "title": "Delivery"},
    {"id": "order_pickup", "title": "Retiro en local"},
    {"id": "order_corporate", "title": "Evento / empresa"},
]

BRANCH_SELECTION_BODY = "¿Cuál de nuestras sucursales te gustaría contactar?"
BRANCH_SELECTION_VISIT_BODY = "¿Cuál sucursal quieres consultar? Te mostraré su dirección y horario."
BRANCH_SELECTION_DELIVERY_BODY = "Delivery, entendido 🛵 ¿Desde cuál sucursal deseas pedir?"
BRANCH_SELECTION_PICKUP_BODY = "Listo, sería para retirar 🛍️ ¿En cuál sucursal?"
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

CORPORATE_HEADCOUNT_QUESTION = (
    "Cuéntame un poco más: ¿para cuántas personas sería y qué fecha/hora tienes en mente? "
    "Puedes responderme todo junto, por ejemplo: “25 personas, viernes 12 al mediodía”."
)
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
        "address": "Torre MMG, Planta Baja, Costa del Este",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=9.0083064,-79.4773394"
    },
    "SF": {
        "name": "San Francisco",
        "address": "Plaza 76, San Francisco",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=8.9912804,-79.5031756"
    },
    "CLY": {
        "name": "Clayton",
        "address": "Clayton Mall, Local #4",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=9.003859,-79.573043"
    },
    "OBR": {
        "name": "Obarrio",
        "address": "Adison House, Calle Abel Bravo, Obarrio",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=8.9863531,-79.5196357"
    },
    "VP": {
        "name": "Vía Porras",
        "address": "Vía Porras, Parque Omar",
        "hours": "Lunes a Domingo: 10:30 AM - 9:30 PM",
        "maps_url": "https://maps.google.com/?q=8.9967623,-79.5065669"
    }
}

MANAGER_HELP_QUESTION = "¿Qué más te gustaría hacer?"
MANAGER_HELP_BUTTONS = [
    {"id": "manager_yes", "title": "Hablar con gerente"},
    {"id": "view_menu", "title": "Ver el menú"},
    {"id": "manager_no", "title": "Nos vemos pronto"},
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

# Recuperación contextual: evita silencios cuando una frase no coincide con una palabra clave.
UNKNOWN_MAIN_MESSAGE = "No estoy completamente seguro de haber entendido 😅 ¿Cuál de estas opciones se parece más a lo que necesitas?"
UNKNOWN_ORDER_MESSAGE = "Quiero ayudarte bien 😊 ¿Buscas delivery, retiro en una sucursal o un pedido para evento/empresa?"
UNKNOWN_BRANCH_MESSAGE = "No logré identificar la sucursal. Elígela aquí o escríbeme su nombre."
AFTER_MENU_HELP_QUESTION = "Mientras ves el menú, ¿hay algo más en lo que pueda ayudarte?"
AFTER_MENU_HELP_BUTTONS = [
    {"id": "view_menu", "title": "Abrir el menú"},
    {"id": "change_branch", "title": "Cambiar sucursal"},
    {"id": "main_human", "title": "Hablar con alguien"},
]

RESTART_MESSAGE = "Claro, empezamos de nuevo. No pasa nada 😊"
CANCEL_MESSAGE = "Listo, dejé a un lado esa selección. ¿Qué te gustaría hacer ahora?"
CHANGE_ORDER_TYPE_MESSAGE = "Sin problema. ¿Cómo prefieres recibir el pedido?"
CHANGE_BRANCH_MESSAGE = "Claro, puedes elegir otra sucursal."

def get_human_handoff_message(branch_name: Optional[str] = None) -> str:
    place = f" de *{branch_name}*" if branch_name and branch_name != "Farmhouse" else ""
    return (
        f"Claro 🤝 Ya compartí tu solicitud con nuestro equipo{place}. "
        "Una persona continuará contigo por este mismo chat y podrá ver lo que ya conversamos, "
        "así que no tendrás que repetirlo."
    )

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
    "¡Perfecto! 📱 Elegiste pagar con Yappy. Cuando confirmes el pedido te enviaremos por este mismo chat "
    "un botón con el monto exacto. Solo tendrás que abrirlo y aprobar la solicitud en tu aplicación Yappy. "
    "Nunca te pediremos tu PIN ni contraseña. 😊"
)
