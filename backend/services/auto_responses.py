"""
Farmhouse WhatsApp Center - Mensajes de Respuesta Automática
Configuración centralizada de mensajes y saludos automáticos del sistema.

Las funciones que reciben `db` consultan primero el grafo editable "Flujo visual"
(services/flow_content.py) y solo si no hay nada editado usan el texto de aquí abajo — ver el
docstring de ese módulo para el porqué. `db` es opcional (default None) para que estas
funciones sigan siendo llamables sin base de datos, ej. al calcular MAIN_WELCOME_BODY como
constante de módulo más abajo.
"""
from typing import Optional, TYPE_CHECKING

from services.flow_content import get_node_text, get_node_options

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Nombre de respaldo que usa parse_incoming_message cuando Meta no manda un perfil de contacto
# (ver services/whatsapp_service.py). Nunca se usa como saludo personalizado, se trata como
# "no sabemos el nombre".
GENERIC_CONTACT_NAMES = {"cliente whatsapp"}


def get_customer_first_name(customer_name: Optional[str] = None) -> str:
    name = (customer_name or "").strip()
    if not name or name.lower() in GENERIC_CONTACT_NAMES:
        return ""
    return name.split()[0]


def get_main_welcome_body(customer_name: Optional[str] = None, db: "Optional[Session]" = None) -> str:
    """Saludo inicial del bot, personalizado con el nombre real de WhatsApp del cliente cuando
    se conoce (y no es el nombre genérico de respaldo), para que se sienta menos robótico."""
    name = (customer_name or "").strip()
    first_name = get_customer_first_name(name)
    saludo = f"¡Hola, {first_name}! 👋" if first_name else "¡Hola! 👋"
    fallback = f"{saludo} Soy el asistente de Farmhouse 🌿\n\n¿Qué te gustaría hacer hoy? También puedes escribirme con tus propias palabras."
    return get_node_text(db, "main_welcome", fallback, saludo=saludo)


MAIN_WELCOME_BODY = get_main_welcome_body(None)

# Menú de bienvenida como lista interactiva (hasta 10 filas): muestra Delivery/Retiro/Evento
# directamente, sin ningún paso intermedio de tipo de entrega. Es también la única UI de "menú
# principal" del bot — cualquier otro punto que necesite reofrecer el menú reutiliza esta misma
# lista (ver _send_main_welcome_menu en routers/webhooks.py), en vez de un set de botones aparte.
MAIN_MENU_LIST_BUTTON = "Elegir opción"
MAIN_MENU_LIST_ROWS = [
    {"id": "main_menu_direct", "title": "Ver el menú y pedir", "description": "Si ya sabes qué quieres, entra directo"},
    {"id": "order_delivery", "title": "Delivery", "description": "Pedido a domicilio"},
    {"id": "order_pickup", "title": "Retiro en local", "description": "Pasas a recoger tu pedido"},
    {"id": "order_corporate", "title": "Evento o empresa", "description": "Catering y pedidos corporativos"},
    {"id": "main_visit", "title": "Ver sucursales", "description": "Direcciones y horarios"},
    {"id": "main_human", "title": "Hablar con alguien", "description": "Te atiende una persona del equipo"},
]

# Fila tocable que se agrega a las listas del flujo (evento corporativo, ubicación del evento,
# ayuda del gerente, después del menú, sucursales, cantidad/fecha del evento) para que "empezar
# de nuevo" nunca dependa de que el cliente recuerde escribir "cancelar" o "menú principal".
NAV_RESTART_ROW = {"id": "nav_restart", "title": "🔄 Empezar de nuevo", "description": "Cancela y vuelve al menú principal"}

BRANCH_SELECTION_MENU_DIRECT_BODY = "¡Perfecto! 🍽️ ¿Desde cuál sucursal te gustaría pedir?"

BRANCH_SELECTION_BODY = "¿Cuál de nuestras sucursales te gustaría contactar?"
BRANCH_SELECTION_VISIT_BODY = "¿Cuál sucursal quieres consultar? Te mostraré su dirección y horario."
BRANCH_SELECTION_DELIVERY_BODY = "Delivery, entendido 🛵 ¿Desde cuál sucursal deseas pedir?"
BRANCH_SELECTION_PICKUP_BODY = "Listo, sería para retirar 🛍️ ¿En cuál sucursal?"
BRANCH_SELECTION_BUTTON = "Ver sucursales"

# --------------------------------------------------------------------------------------------
# Pedido Corporativo / Evento (catering)
#
# Hoy el bot NO hace las 4 preguntas guiadas: apenas el cliente elige "Evento o empresa" se le
# pasa directamente el número del equipo de catering, que es quien coordina con Sol. La decisión
# fue del negocio: se prefiere que la persona llegue rápido a un humano antes que contestar un
# cuestionario. Las 4 preguntas quedan intactas en el código (y con sus pruebas) detrás de este
# interruptor: poniéndolo en True el bot vuelve a hacerlas y a armarle el resumen a Sol, sin
# ningún otro cambio.
CORPORATE_INTAKE_ENABLED = False

CATERING_PHONE_DISPLAY = "+507 6364-4572"
CATERING_PHONE_WA_LINK = "https://wa.me/50763644572"

# Se manda como texto normal a propósito, no dentro de un mensaje con botones: en WhatsApp, el
# número y el enlace solo quedan tocables cuando van en un mensaje de texto común.
CORPORATE_CATERING_HANDOFF = (
    "¡Perfecto! 🎉 Los pedidos para eventos y empresas los coordina directamente nuestro equipo "
    "de catering, que es quien trabaja con Sol para armarte la propuesta.\n\n"
    f"Escríbeles por aquí y te atienden de una vez 👇\n📞 {CATERING_PHONE_DISPLAY}\n{CATERING_PHONE_WA_LINK}\n\n"
    "¡Gracias por pensar en Farmhouse para tu evento! 😊"
)

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
    "Elige un rango aquí abajo, o si prefieres, respóndeme todo junto por escrito, por ejemplo: "
    "“25 personas, viernes 12 al mediodía”."
)
CORPORATE_HEADCOUNT_RETRY = "¿Me confirmas para cuántas personas sería, aproximadamente?"

# Rangos rápidos para no obligar a escribir un número exacto: Sol siempre confirma el dato
# preciso por humano al recibir el intake, así que un rango aproximado alcanza en este paso.
CORPORATE_HEADCOUNT_RANGE_ROWS = [
    {"id": "hc_1_10", "title": "1 a 10 personas"},
    {"id": "hc_11_30", "title": "11 a 30 personas"},
    {"id": "hc_31_60", "title": "31 a 60 personas"},
    {"id": "hc_60_plus", "title": "Más de 60 personas"},
    {"id": "hc_type_exact", "title": "Cantidad exacta"},
]
CORPORATE_HEADCOUNT_RANGE_LABELS = {row["id"]: row["title"] for row in CORPORATE_HEADCOUNT_RANGE_ROWS if row["id"] != "hc_type_exact"}

CORPORATE_DATE_QUESTION = "¡Genial! ¿Tienes fecha y hora en mente?"
CORPORATE_DATE_RETRY = "¿Me compartes la fecha y hora que tienes en mente?"

CORPORATE_DATE_QUICK_ROWS = [
    {"id": "date_today", "title": "Hoy"},
    {"id": "date_tomorrow", "title": "Mañana"},
    {"id": "date_weekend", "title": "Este fin de semana"},
    {"id": "date_next_week", "title": "La próxima semana"},
    {"id": "date_type_exact", "title": "Fecha exacta"},
]
CORPORATE_DATE_QUICK_LABELS = {row["id"]: row["title"] for row in CORPORATE_DATE_QUICK_ROWS if row["id"] != "date_type_exact"}

CORPORATE_LOCATION_QUESTION = "Última pregunta: ¿dónde te gustaría recibir el pedido?"

# Variante para cuando el cliente ya dio cantidad y fecha/hora juntas en una sola respuesta:
# fusiona el agradecimiento con la última pregunta en una sola burbuja, en vez de dos.
CORPORATE_LOCATION_QUESTION_AFTER_COMBINED_ANSWER = (
    "¡Genial, gracias! Última pregunta: ¿dónde te gustaría recibir el pedido?"
)
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

def get_branch_visit_message(branch_code: str, branch_name: str, db: "Optional[Session]" = None) -> str:
    fallback_opening = f"¡Excelente! Te esperamos en la sucursal de *{branch_name}*."
    opening = get_node_text(db, "branch_visit_opening", fallback_opening, sucursal=branch_name)
    return get_branch_info_message(branch_name, branch_code, opening)

def get_branch_pickup_info_message(branch_code: str, branch_name: str, db: "Optional[Session]" = None) -> str:
    fallback_opening = f"¡Perfecto! 🛍️ Retirarás tu pedido en nuestra sucursal de *{branch_name}*."
    opening = get_node_text(db, "branch_pickup_opening", fallback_opening, sucursal=branch_name)
    return get_branch_info_message(branch_name, branch_code, opening)

def get_branch_delivery_info_message(branch_code: str, branch_name: str, db: "Optional[Session]" = None) -> str:
    fallback_opening = f"¡Excelente! 🛵 Tu pedido a domicilio saldrá de nuestra sucursal de *{branch_name}*."
    opening = get_node_text(db, "branch_delivery_opening", fallback_opening, sucursal=branch_name)
    return get_branch_info_message(branch_name, branch_code, opening)

# Respuesta a "Ver horarios" / "Ver ubicación" en la lista "¿algo más?" tras el Menú Digital:
# mismo contenido para ambas preguntas (dirección + horario + maps ya vienen juntos), con una
# apertura neutral en vez de las de arriba (que dan por hecho que el cliente ya va a visitar o
# retirar en ese momento).
def get_branch_quick_info_message(branch_code: str, branch_name: str, db: "Optional[Session]" = None) -> str:
    fallback_opening = f"Esto es lo que tenemos de nuestra sucursal de *{branch_name}*:"
    opening = get_node_text(db, "branch_quick_info_opening", fallback_opening, sucursal=branch_name)
    return get_branch_info_message(branch_name, branch_code, opening)

# Cierre cálido tras mandar el botón del Menú Digital en delivery/pickup: deja la puerta abierta
# sin forzar otra decisión de botones (el bot ya detecta por texto libre si piden un humano).
MENU_LINK_WARM_CLOSING = "Cualquier duda que tengas mientras armas tu pedido, aquí estamos para ayudarte con todo gusto 😊"

# Recuperación contextual: evita silencios cuando una frase no coincide con una palabra clave.
UNKNOWN_MAIN_MESSAGE = "No estoy completamente seguro de haber entendido 😅 ¿Cuál de estas opciones se parece más a lo que necesitas?"
UNKNOWN_BRANCH_MESSAGE = "No logré identificar la sucursal. Elígela aquí o escríbeme su nombre."
AFTER_MENU_HELP_QUESTION = "Mientras ves el menú, ¿hay algo más en lo que pueda ayudarte?"
# "Abrir el menú" se quitó de acá: justo se le mandó el botón del Menú Digital en este mismo
# turno, así que repetirlo de inmediato se sentía redundante. En su lugar van las dos preguntas
# más comunes según el negocio (horario y ubicación) — las dos mandan la misma info de la
# sucursal ya asignada (ver get_branch_quick_info_message) y vuelven a mostrar esta lista.
AFTER_MENU_HELP_BUTTONS = [
    {"id": "branch_hours", "title": "Ver horarios"},
    {"id": "branch_location", "title": "Ver ubicación"},
    {"id": "change_branch", "title": "Cambiar sucursal"},
    {"id": "main_human", "title": "Hablar con alguien"},
]
# Fila extra de la lista "¿algo más?": para quien ya sabe qué quiere y prefiere no salir del
# chat para pedir/pagar (en vez de usar el Menú Digital web).
CHAT_ORDER_ROW = {"id": "chat_order_start", "title": "Pedir y pagar por chat", "description": "Sin salir de WhatsApp"}

CHAT_ORDER_INTRO_QUESTION = "¡Perfecto! Cuéntame qué te gustaría pedir (platillos y cantidades) y lo dejamos listo para el pago 😊"
CHAT_ORDER_PAYMENT_QUESTION = "¡Anotado! ¿Cómo prefieres pagar?"
# "Tarjeta" (Tilopay) se saca de acá a propósito: Farmhouse todavía no está afiliado con Tilopay.
# Ver también menu.html (botón con `hidden`) y _step_handle_payment_selection en webhooks.py.
CHAT_ORDER_PAYMENT_ROWS = [
    {"id": "pay_ach", "title": "ACH / Transferencia"},
    {"id": "pay_yappy", "title": "Yappy"},
]

RESTART_MESSAGE = "Claro, empezamos de nuevo. No pasa nada 😊"
CANCEL_MESSAGE = "Listo, dejé a un lado esa selección. ¿Qué te gustaría hacer ahora?"
CHANGE_ORDER_TYPE_MESSAGE = "Sin problema. ¿Cómo prefieres recibir el pedido?"
CHANGE_BRANCH_MESSAGE = "Claro, puedes elegir otra sucursal."

def get_human_handoff_message(branch_name: Optional[str] = None, db: "Optional[Session]" = None) -> str:
    place = f" de *{branch_name}*" if branch_name and branch_name != "Farmhouse" else ""
    fallback = (
        f"Claro 🤝 Ya compartí tu solicitud con nuestro equipo{place}. "
        "Una persona continuará contigo por este mismo chat y podrá ver lo que ya conversamos, "
        "así que no tendrás que repetirlo."
    )
    return get_node_text(db, "human_handoff_message", fallback, lugar=place)

def get_manager_assigned_message(branch_name: str, db: "Optional[Session]" = None) -> str:
    fallback = (
        f"¡Con mucho gusto! 🤝 Te comunicamos de inmediato con el gerente de nuestra sucursal de *{branch_name}*.\n\n"
        f"En un momento te estará atendiendo personalmente por aquí. ¡Muchas gracias por tu paciencia! 😊"
    )
    return get_node_text(db, "manager_assigned_message", fallback, sucursal=branch_name)

def get_manager_declined_message(branch_name: str, db: "Optional[Session]" = None) -> str:
    fallback = (
        f"¡Perfecto! Muchas gracias por escribirnos. ¡Te esperamos pronto en Farmhouse *{branch_name}*! "
        f"Que tengas un excelente día 🌿✨"
    )
    return get_node_text(db, "manager_declined_message", fallback, sucursal=branch_name)

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

# Seguimiento automático (ver services/bot_followup.py): único mensaje que manda el bot si el
# cliente se queda callado 5+ minutos después de que el bot le habló, para no dejarlo pensando
# que quedó sin respuesta. Deliberadamente NO repite el menú/las opciones anteriores (ese es un
# alcance mayor, no pedido todavía) — solo invita a seguir, en el mismo tono cálido del resto.
#
# Interruptor de emergencia, mismo patrón que CORPORATE_INTAKE_ENABLED: si algo se ve mal en
# producción (o hay que probar en local con WHATSAPP_MODE=meta sin arriesgar un envío real),
# se pone en False y el loop de bot_followup.py deja de mandar nada, sin esperar un deploy —
# solo hace falta editar este archivo y reiniciar.
BOT_FOLLOWUP_ENABLED = True
BOT_FOLLOWUP_MESSAGE = "¿Sigues ahí? 😊 Cuando quieras seguimos justo donde lo dejamos — cualquier cosa, escríbeme."

# Confirmación al cliente cuando Yappy avisa (por el IPN real, con firma verificada — nunca por
# suposición) que el pago de su pedido se completó. Ver routers/payments.yappy_ipn: se manda
# UNA sola vez, justo cuando payment_status pasa a "paid" por primera vez.
def get_yappy_payment_success_message(order_code: str, db: "Optional[Session]" = None) -> str:
    fallback = (
        f"¡Pago recibido con éxito! ✅ Tu pedido *{order_code}* ya está confirmado y en preparación. "
        f"¡Gracias por tu compra! 🌿"
    )
    return get_node_text(db, "yappy_payment_success_message", fallback, pedido=order_code)
