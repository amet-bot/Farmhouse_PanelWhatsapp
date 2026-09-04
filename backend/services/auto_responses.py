"""
Farmhouse WhatsApp Center - Mensajes de Respuesta Automatica
Configuracion centralizada de mensajes y saludos automaticos del sistema.
"""

WELCOME_MESSAGES = [
    "¡Hola! Bienvenido a Farmhouse, un gusto saludarte 😊",
    "¿Deseas ver el menú o ya tienes tu pedido listo?"
]

BRANCH_SELECTION_BODY = "¿Cuál de nuestras sucursales te gustaría contactar?"
BRANCH_SELECTION_BUTTON = "Ver sucursales"

def get_branch_welcome_message(branch_name: str) -> str:
    return f"¡Bienvenido a Farmhouse {branch_name}! 🌿 Un gusto atenderte."

SIMULATED_MENU_TEXT = (
    "📋 *Menú Farmhouse* 🥗🥪🥤\n\n"
    "🥗 *Ensaladas & Bowls*\n"
    "• Caesar Salad ($8.50)\n"
    "• Quinoa Farm Bowl ($9.75)\n"
    "• Mediterranean Salmon Bowl ($12.00)\n\n"
    "🥪 *Sandwiches & Paninis*\n"
    "• Avocado Toast Deluxe ($7.00)\n"
    "• Chicken Pesto Panini ($8.75)\n"
    "• Roast Beef Melt ($10.50)\n\n"
    "🥤 *Bebidas Naturales & Smoothies*\n"
    "• Green Detox Smoothie ($4.50)\n"
    "• Berry Blast ($4.50)\n"
    "• Cold Brew Artesanal ($3.75)\n\n"
    "🍰 *Postres Saludables*\n"
    "• Cheesecake Keto ($4.75)\n"
    "• Brownie Vegano ($4.00)\n\n"
    "_Puedes indicarnos qué platillos deseas ordenar o continuar con los datos de entrega._"
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
    "¡Perfecto! 📱 En un momento nuestro equipo te comparte el número para pagar por Yappy. "
    "Cuando hagas el pago, ¿me regalas una captura del comprobante? Así agilizamos tu pedido muchísimo más rápido. "
    "¡Muchas gracias por tu paciencia! 😊"
)

CASH_PAYMENT_MESSAGE = (
    "¡Perfecto! 💵 Puedes pagar en efectivo cuando recibas tu pedido (o cuando lo retires en el local). "
    "En un momento alguien de nuestro equipo te atiende para tomar los detalles de tu pedido. "
    "¡Gracias por tu paciencia! 😊"
)
