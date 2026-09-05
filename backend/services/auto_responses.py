"""
Farmhouse WhatsApp Center - Mensajes de Respuesta Automática
Configuración centralizada de mensajes y saludos automáticos del sistema.
"""

MAIN_WELCOME_BODY = (
    "Hola Bienvenido a farmhouse, como te podemos ayudar hoy?\n\n"
    "(1) Quiero visitarlos a una de su sucursales\n"
    "(2) Quiero hacer un pedido a domicilio\n"
    "(3) Quiero hacer un pedio para retirar en local\n"
    "(4) Quiero coordinar un pedido coorporativo u organizar un evento."
)

MAIN_MENU_BUTTON = "Ver opciones"

MAIN_MENU_OPTIONS = [
    {"id": "opt_visit", "title": "(1) Visitar sucursales", "description": "Quiero visitarlos a una de su sucursales"},
    {"id": "opt_delivery", "title": "(2) Pedido a domicilio", "description": "Quiero hacer un pedido a domicilio"},
    {"id": "opt_pickup", "title": "(3) Retiro en local", "description": "Quiero hacer un pedio para retirar en local"},
    {"id": "opt_corporate", "title": "(4) Evento / Corporativo", "description": "Quiero coordinar un pedido coorporativo u organizar un evento."},
]

MAIN_MENU_TEXT_FALLBACK = MAIN_WELCOME_BODY

WELCOME_MESSAGES = [
    MAIN_WELCOME_BODY,
    "¿Cómo te podemos ayudar hoy?"
]

BRANCH_SELECTION_BODY = "¿Cuál de nuestras sucursales te gustaría contactar?"
BRANCH_SELECTION_VISIT_BODY = "¡Nos encantará recibirte! 🌿✨ ¿Cuál de nuestras sucursales te gustaría visitar?"
BRANCH_SELECTION_DELIVERY_BODY = "¡Excelente! 🛵 ¿Para cuál de nuestras sucursales deseas solicitar tu delivery?"
BRANCH_SELECTION_PICKUP_BODY = "¡Perfecto! 🛍️ ¿En cuál de nuestras sucursales deseas retirar tu pedido?"
BRANCH_SELECTION_BUTTON = "Ver sucursales"

CORPORATE_WELCOME_MESSAGE = (
    "¡Qué gran noticia! 🎉🥗 En Farmhouse nos encanta atender pedidos corporativos, reuniones de oficina, catering y eventos especiales con opciones saludables, deliciosas y frescas.\n\n"
    "En un momento nuestro coordinador de eventos y cuentas corporativas te atenderá por este chat para brindarte atención personalizada y cotizar tu requerimiento.\n\n"
    "Si gustas, puedes ir dejándonos los siguientes detalles:\n"
    "• Tipo de evento o motivo\n"
    "• Fecha y hora estimada\n"
    "• Cantidad aproximada de personas\n"
    "• Lugar de entrega o sucursal de preferencia\n\n"
    "¡Muchas gracias por elegirnos! 😊✨"
)

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

def get_branch_visit_message(branch_code: str, branch_name: str) -> str:
    info = BRANCH_VISIT_INFO.get(branch_code.upper() if branch_code else "")
    if info:
        return (
            f"¡Te esperamos con los brazos abiertos en Farmhouse *{info['name']}*! 🌿🥗✨\n\n"
            f"📍 *Dirección:* {info['address']}\n"
            f"⏰ *Horario:* {info['hours']}\n"
            f"🗺️ *Cómo llegar:* {info['maps_url']}\n\n"
            f"Si necesitas asistencia para llegar o tienes alguna consulta, un agente de turno te atenderá en seguida por este chat. ¡Nos vemos pronto! 😊"
        )
    return (
        f"¡Te esperamos con los brazos abiertos en Farmhouse *{branch_name}*! 🌿🥗✨\n\n"
        f"⏰ *Horario de atención:* Lunes a Domingo: 10:30 AM - 9:30 PM\n\n"
        f"Si necesitas asistencia para llegar o tienes alguna consulta, un agente de turno te atenderá en seguida por este chat. ¡Nos vemos pronto! 😊"
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
