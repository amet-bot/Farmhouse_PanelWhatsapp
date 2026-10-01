"""acortar los mensajes del bot: directos, efectivos y cálidos, sin relleno

Revision ID: 062_shorten_bot_messages
Revises: 061_merge_delivery_address_question

Pedido del negocio (2026-10-01): "quiero que el bot sea más directo, que el cliente no lea
mucho, respuestas cortas, efectivas, amables y respetuosas". Se recortó el texto de cada nodo
de services/auto_responses.py (y de los dos fallbacks inline de routers/webhooks.py que no
pasan por un nodo) quitando repeticiones, cortesías dobles y frases de relleno, conservando el
tono cálido y el contenido que el cliente necesita.

Esta migración solo ACTUALIZA el campo "text" de los nodos que ya existían en el grafo editable
"Flujo visual" (sembrados por migraciones anteriores con el texto largo de antes) — nunca
agrega ni quita nodos ni cambia su tipo/opciones. Sin esto, el bot seguiría usando el texto
viejo guardado en la base aunque el Python ya tenga el nuevo: ver el docstring de
services/flow_content.py (el nodo editable manda si existe y no está vacío). Mismo patrón que
las migraciones 026 y 061.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "062_shorten_bot_messages"
down_revision: Union[str, None] = "061_merge_delivery_address_question"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

# node_id -> (texto nuevo, texto viejo) — el viejo es lo que escribe downgrade().
_TEXTS = {
    "main_welcome": (
        "{saludo} Soy el asistente de Farmhouse 🌿\n¿Qué te gustaría hacer?",
        "{saludo} Soy el asistente de Farmhouse 🌿\n\n¿Qué te gustaría hacer hoy? También puedes escribirme con tus propias palabras.",
    ),
    "entry_gate": (
        "{saludo} Soy el asistente de Farmhouse 🌿\n¿Te ayudo yo, o prefieres hablar con alguien del equipo?",
        "{saludo} Soy el asistente de Farmhouse 🌿\n\n¿Quieres que te ayude yo con tu pedido, o prefieres hablar de una vez con alguien del equipo?",
    ),
    "branch_selection_delivery_body": (
        "🛵 ¿Desde cuál sucursal pedimos?",
        "Delivery, entendido 🛵 ¿Desde cuál sucursal deseas pedir?",
    ),
    "branch_selection_menu_direct_body": (
        "🍽️ ¿Desde cuál sucursal pedimos?",
        "¡Perfecto! 🍽️ ¿Desde cuál sucursal te gustaría pedir?",
    ),
    "branch_selection_pickup_body": (
        "🛍️ ¿En cuál sucursal retiras?",
        "Listo, sería para retirar 🛍️ ¿En cuál sucursal?",
    ),
    "branch_selection_visit_body": (
        "¿Cuál sucursal? Te paso dirección y horario.",
        "¿Cuál sucursal quieres consultar? Te mostraré su dirección y horario.",
    ),
    "branch_visit_opening": (
        "Te esperamos en *{sucursal}*.",
        "¡Excelente! Te esperamos en la sucursal de *{sucursal}*.",
    ),
    "branch_pickup_opening": (
        "🛍️ Retiras en *{sucursal}*.",
        "¡Perfecto! 🛍️ Retirarás tu pedido en nuestra sucursal de *{sucursal}*.",
    ),
    "branch_delivery_opening": (
        "🛵 Tu pedido sale de *{sucursal}*.",
        "¡Excelente! 🛵 Tu pedido a domicilio saldrá de nuestra sucursal de *{sucursal}*.",
    ),
    "corporate_catering_handoff": (
        "🎉 Los eventos y pedidos corporativos los coordina nuestro equipo de catering.\n\n"
        "Escríbeles aquí 👇\n📞 +507 6364-4572\nhttps://wa.me/50763644572\n\n"
        "¡Gracias por pensar en Farmhouse! 😊",
        "¡Perfecto! 🎉 Los pedidos para eventos y empresas los coordina directamente nuestro equipo "
        "de catering, que es quien trabaja con Sol para armarte la propuesta.\n\n"
        "Escríbeles por aquí y te atienden de una vez 👇\n📞 +507 6364-4572\nhttps://wa.me/50763644572\n\n"
        "¡Gracias por pensar en Farmhouse para tu evento! 😊",
    ),
    "corporate_intro": (
        "🎉 ¡Nos encantan los eventos y pedidos corporativos! Te hago unas preguntas rápidas "
        "y te comunico con Sol, nuestra encargada de eventos 😊",
        "¡Qué gran noticia! 🎉 En Farmhouse nos encanta atender pedidos corporativos, reuniones de oficina, catering y eventos especiales.\n\n"
        "Para armarte la mejor propuesta, te hago unas preguntas rápidas antes de comunicarte con Sol, nuestra encargada de eventos y cuentas corporativas 😊",
    ),
    "corporate_headcount_question": (
        "¿Para cuántas personas y qué fecha/hora tienes en mente? "
        "Puedes responder todo junto, ej: “25 personas, viernes 12 al mediodía”.",
        "Cuéntame un poco más: ¿para cuántas personas sería y qué fecha/hora tienes en mente? "
        "Puedes responderme todo junto, por ejemplo: “25 personas, viernes 12 al mediodía”.",
    ),
    "corporate_headcount_retry": (
        "¿Para cuántas personas, aproximadamente?",
        "¿Me confirmas para cuántas personas sería, aproximadamente?",
    ),
    "corporate_date_question": (
        "¿Qué fecha y hora tienes en mente?",
        "¡Genial! ¿Tienes fecha y hora en mente?",
    ),
    "corporate_date_retry": (
        "¿Me dices la fecha y hora?",
        "¿Me compartes la fecha y hora que tienes en mente?",
    ),
    "corporate_location_question": (
        "Última pregunta: ¿dónde recibes el pedido?",
        "Última pregunta: ¿dónde te gustaría recibir el pedido?",
    ),
    "corporate_location_after_combined": (
        "¡Gracias! Última pregunta: ¿dónde recibes el pedido?",
        "¡Genial, gracias! Última pregunta: ¿dónde te gustaría recibir el pedido?",
    ),
    "corporate_closing": (
        "🎉 Ya tengo todo para Sol. Te comunico con ella por aquí en un momento. ¡Gracias por pensar en Farmhouse!",
        "¡Listo! 🎉 Ya tengo todo lo que Sol necesita para armarte una propuesta. En un momento te comunico con ella por este mismo chat. "
        "¡Gracias por pensar en Farmhouse para tu evento! 😊",
    ),
    "menu_link_delivery_body": (
        "🍽️ Aquí tienes el Menú Digital de Farmhouse *{sucursal}*.\n\n"
        "_Elige tus platillos, confirma tu dirección y listo._\n\n"
        "Cualquier duda, aquí estamos 😊",
        "🍽️ Aquí tienes nuestro Menú Digital para armar tu pedido a domicilio desde Farmhouse *{sucursal}*.\n\n"
        "_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos, ingresa tu dirección y envíanos tu orden en 1 clic._\n\n"
        "Cualquier duda que tengas mientras armas tu pedido, aquí estamos para ayudarte con todo gusto 😊",
    ),
    "menu_link_pickup_body": (
        "🍽️ Aquí tienes el Menú Digital para tu retiro en Farmhouse *{sucursal}*.\n\n"
        "_Elige tus platillos y te lo dejamos listo fresco._\n\n"
        "Cualquier duda, aquí estamos 😊",
        "🍽️ Échale un vistazo a nuestro Menú Digital y arma tu pedido para retirar en Farmhouse *{sucursal}*.\n\n"
        "_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos y te lo tendremos fresco y listo cuando pases a retirarlo._\n\n"
        "Cualquier duda que tengas mientras armas tu pedido, aquí estamos para ayudarte con todo gusto 😊",
    ),
    "menu_link_generic_body": (
        "🍽️ Aquí tienes el Menú Digital de Farmhouse *{sucursal}*.\n\n"
        "_Mira qué se te antoja, o pide directo desde aquí._",
        "🍽️ Aquí tienes nuestro Menú Digital de Farmhouse *{sucursal}*.\n\n"
        "_Así vas viendo qué se te antoja antes de llegar, o si prefieres, también puedes hacer tu pedido desde aquí mismo._",
    ),
    "unknown_main_message": (
        "No estoy seguro de haber entendido 😅 ¿Cuál opción se parece a lo que buscas?",
        "No estoy completamente seguro de haber entendido 😅 ¿Cuál de estas opciones se parece más a lo que necesitas?",
    ),
    "unknown_branch_message": (
        "No identifiqué la sucursal. Elígela aquí o escríbeme su nombre.",
        "No logré identificar la sucursal. Elígela aquí o escríbeme su nombre.",
    ),
    "after_menu_help_question": (
        "¿Hay algo más en lo que pueda ayudarte?",
        "Mientras ves el menú, ¿hay algo más en lo que pueda ayudarte?",
    ),
    "chat_order_intro_question": (
        "¿Qué te gustaría pedir? (platillos y cantidades) 😊",
        "¡Perfecto! Cuéntame qué te gustaría pedir (platillos y cantidades) y lo dejamos listo para el pago 😊",
    ),
    "restart_message": (
        "Listo, empezamos de nuevo 😊",
        "Claro, empezamos de nuevo. No pasa nada 😊",
    ),
    "cancel_message": (
        "Listo, cancelado. ¿Qué te gustaría hacer?",
        "Listo, dejé a un lado esa selección. ¿Qué te gustaría hacer ahora?",
    ),
    "change_order_type_message": (
        "¿Cómo prefieres recibir el pedido?",
        "Sin problema. ¿Cómo prefieres recibir el pedido?",
    ),
    "change_branch_message": (
        "Claro, elige otra sucursal.",
        "Claro, puedes elegir otra sucursal.",
    ),
    "human_handoff_message": (
        "Claro 🤝 Ya avisé al equipo{lugar}. Alguien sigue contigo por aquí mismo, sin que repitas nada.",
        "Claro 🤝 Ya compartí tu solicitud con nuestro equipo{lugar}. Una persona continuará contigo por este mismo chat y podrá ver lo que ya conversamos, "
        "así que no tendrás que repetirlo.",
    ),
    "manager_assigned_message": (
        "🤝 Te comunico con el gerente de *{sucursal}*. En un momento te atiende por aquí 😊",
        "¡Con mucho gusto! 🤝 Te comunicamos de inmediato con el gerente de nuestra sucursal de *{sucursal}*.\n\n"
        "En un momento te estará atendiendo personalmente por aquí. ¡Muchas gracias por tu paciencia! 😊",
    ),
    "manager_declined_message": (
        "¡Gracias por escribirnos! Te esperamos pronto en Farmhouse *{sucursal}* 🌿✨",
        "¡Perfecto! Muchas gracias por escribirnos. ¡Te esperamos pronto en Farmhouse *{sucursal}*! "
        "Que tengas un excelente día 🌿✨",
    ),
    "payment_ach": (
        "🏦 Datos para tu transferencia ACH:\n\n"
        "Banco: Banco General\nTipo de cuenta: Cuenta corriente\nNombre de cuenta: Grupo Col Rizado\nNúmero de cuenta: 03-01-01-1480750\n\n"
        "Te confirmamos el total y, al transferir, ¿nos mandas foto del comprobante? Así agilizamos tu pedido 😊",
        "¡Perfecto! 🏦 Estos son los datos de nuestra cuenta para pagar por ACH:\n\n"
        "Banco: Banco General\nTipo de cuenta: Cuenta corriente\nNombre de cuenta: Grupo Col Rizado\nNúmero de cuenta: 03-01-01-1480750\n\n"
        "En cuanto nuestro equipo te confirme el total de tu pedido, puedes hacer la transferencia a esta cuenta. "
        "Cuando la hagas, ¿me regalas una foto del comprobante de pago? Así agilizamos tu pedido muchísimo más rápido. "
        "¡Muchas gracias por tu paciencia! 😊",
    ),
    "payment_card": (
        "💳 En un momento te enviamos el enlace de pago seguro para tu tarjeta. Dame unos minutos 😊",
        "¡Perfecto! 💳 Como seleccionaste pago con tarjeta, en un momento nuestro agente de turno te enviará "
        "por este chat el enlace de pago seguro para que puedas completar tu compra cómodamente con tu tarjeta de crédito o débito.\n\n"
        "Por favor regálanos unos breves minutos mientras lo generamos para ti. ¡Muchas gracias por tu paciencia y preferencia! 😊✨",
    ),
    "payment_yappy": (
        "📱 Pago con Yappy: al confirmar tu pedido te mando un botón con el monto exacto para aprobar "
        "en la app. Nunca te pediremos tu PIN. 😊",
        "¡Perfecto! 📱 Elegiste pagar con Yappy. Cuando confirmes el pedido te enviaremos por este mismo chat "
        "un botón con el monto exacto. Solo tendrás que abrirlo y aprobar la solicitud en tu aplicación Yappy. "
        "Nunca te pediremos tu PIN ni contraseña. 😊",
    ),
    "delivery_location_request": (
        "🛵 Comparte tu ubicación y te digo la sucursal más cercana (o escríbeme el nombre).",
        "Delivery, entendido 🛵 Compárteme tu ubicación con el botón de abajo y te recomiendo la "
        "sucursal más cercana. Si prefieres, escríbeme el nombre de la sucursal.",
    ),
    "pickup_location_request": (
        "📍 Comparte tu ubicación y te digo cuál sucursal te queda más cerca (o escríbeme el nombre).",
        "Compárteme tu ubicación con el botón de abajo y te digo cuál sucursal te queda más cerca 📍 "
        "Si prefieres, escríbeme el nombre de la sucursal.",
    ),
    "delivery_out_of_range_message": (
        "Estás a {km} km de *{sucursal}* y el delivery llega hasta {max_km} km 😔 "
        "¿Prefieres retirar en otra sucursal?",
        "Estás a {km} km de nuestra sucursal más cercana (*{sucursal}*) y por ahora el delivery "
        "llega hasta {max_km} km 😔 Si quieres, puedes pedir para retirar en la sucursal que te "
        "quede mejor:",
    ),
    "delivery_place_question": (
        "¿Cuál es tu dirección completa? (PH/edificio, casa o local + alguna referencia). "
        "Ej: \"PH Torre Mar, apto 5B\" o \"casa 12, portón negro\".",
        "Para que el motorizado llegue sin vueltas, cuéntame tu dirección completa (si es PH/edificio, "
        "casa o local, y alguna referencia). Por ejemplo: \"PH Torre Mar, apto 5B\" o \"casa 12, portón negro, frente al parque\".",
    ),
    "delivery_reference_question_ph": (
        "¿Nombre del PH y apartamento? Ej: PH Torre Mar, apto 5B.",
        "¿Cómo se llama el PH y cuál es el apartamento? Por ejemplo: PH Torre Mar, apto 5B.",
    ),
    "delivery_reference_question_casa": (
        "¿Número de casa y una referencia? Ej: casa 12, portón negro.",
        "¿Número de casa y alguna referencia? Por ejemplo: casa 12, portón negro, frente al parque.",
    ),
    "delivery_reference_question_local": (
        "¿Nombre del local/oficina y piso? Ej: Oficinas Delta, piso 3.",
        "¿Cómo se llama el local u oficina y en qué piso está? Por ejemplo: Oficinas Delta, piso 3.",
    ),
    "closed_now_message": (
        "Ahora mismo estamos cerrados 🌙 Abrimos a las {abre}. Puedes programar tu pedido desde el "
        "menú para esa hora.",
        "Ahora mismo estamos cerrados 🌙 Abrimos a las {abre}. Igual puedes armar tu pedido desde el "
        "menú y programarlo para cuando abramos, y te lo tendremos listo a esa hora.",
    ),
    "handoff_wait_message": (
        "Seguimos contigo 🙏 El equipo está ocupado, ya les avisé — te responden apenas se desocupen.",
        "Seguimos contigo 🙏 En este momento el equipo está ocupado, pero ya les avisé y una persona "
        "te responde por aquí en cuanto se desocupe. Gracias por la paciencia.",
    ),
    "bot_followup_message": (
        "¿Sigues ahí? 😊 Seguimos cuando quieras.",
        "¿Sigues ahí? 😊 Cuando quieras seguimos justo donde lo dejamos — cualquier cosa, escríbeme.",
    ),
    "yappy_payment_success_message": (
        "✅ Pago recibido. Tu pedido *{pedido}* está confirmado y en preparación 🌿",
        "¡Pago recibido con éxito! ✅ Tu pedido *{pedido}* ya está confirmado y en preparación. ¡Gracias por tu compra! 🌿",
    ),
    "attachment_received_message": (
        "Recibido 📎 Ya se lo pasé al equipo, en un momento te escriben.",
        "Recibí tu archivo, gracias 📎 Ya se lo compartí al equipo para que lo revise y continúe contigo por aquí.",
    ),
}


def _table():
    return sa.table("bot_flows", sa.column("id", sa.Integer), sa.column("key", sa.String), sa.column("graph_json", sa.Text))


def _apply(bind, use_new: bool) -> None:
    bot_flows = _table()
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return
    graph = json.loads(row.graph_json)
    by_id = {n.get("id"): n for n in graph.get("nodes", [])}
    for node_id, (new_text, old_text) in _TEXTS.items():
        node = by_id.get(node_id)
        if node is not None:
            node["text"] = new_text if use_new else old_text
    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))


def upgrade() -> None:
    _apply(op.get_bind(), use_new=True)


def downgrade() -> None:
    _apply(op.get_bind(), use_new=False)
