"""expand_main_intake_content

Revision ID: 017_expand_main_intake
Revises: 016_add_bot_flows

Fase 2 de "Flujo visual" para el panel central: reemplaza el grafo ilustrativo de la Fase 1
(9 nodos genéricos, incluida una pregunta de "Método de pago" que en realidad no existe como
tal) por un grafo con UN NODO POR CADA MENSAJE REAL Y EDITABLE del bot, con el texto copiado
VERBATIM de services/auto_responses.py (fuente de verdad) al momento de escribir esta
migración. A partir de este cambio, editar un nodo desde el panel y guardar SÍ cambia lo que
el bot le contesta a un cliente real (ver services/flow_content.py) — la lógica de qué paso
sigue (interrupciones, sucursales, atajos del intake corporativo) se queda intacta en
routers/webhooks.py, tal como se acordó con el usuario.

Las conexiones entre nodos son solo ilustrativas (no hay motor que las camine); lo único que
importa funcionalmente es el `id` de cada nodo, que debe coincidir exactamente con los ids que
usa services/flow_content.py / routers/webhooks.py.
"""
import json
from datetime import datetime, timezone
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '017_expand_main_intake'
down_revision: Union[str, None] = '016_add_bot_flows'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Altura real medida en el navegador (getBoundingClientRect) para cada nodo, con el contenido
# exacto de esta migración — igual que se hizo para el grafo de catering, para que ningún
# nodo quede encimado con el siguiente por tener más texto del esperado. Si se edita el texto
# de un nodo más adelante desde el panel, el usuario ya tiene "Vertical"/"Horizontal" para
# reacomodar; esto es solo para que la siembra inicial se vea bien de entrada.
_MEASURED_HEIGHT = {
    "trigger": 90, "main_welcome": 123, "order_type_question": 252,
    "branch_selection_menu_direct_body": 72, "branch_selection_visit_body": 89,
    "branch_selection_delivery_body": 89, "branch_selection_pickup_body": 72,
    "branch_visit_opening": 72, "manager_help_question": 157,
    "manager_assigned_message": 141, "manager_declined_message": 106,
    "branch_delivery_opening": 89, "menu_link_delivery_body": 210,
    "branch_pickup_opening": 89, "menu_link_pickup_body": 210,
    "menu_link_generic_body": 158, "payment_ach": 279, "payment_card": 192,
    "payment_yappy": 141, "human_handoff_message": 123, "corporate_intro": 175,
    "corporate_event_type_question": 157, "corporate_headcount_question": 123,
    "corporate_date_question": 72, "corporate_location_question": 157,
    "corporate_location_after_combined": 89, "corporate_closing": 123,
    "corporate_pause_action": 107, "corporate_invalid_option_retry": 72,
    "corporate_headcount_retry": 89, "corporate_date_retry": 72,
    "restart_message": 72, "cancel_message": 89, "change_order_type_message": 72,
    "change_branch_message": 72, "unknown_main_message": 106,
    "unknown_order_message": 89, "unknown_branch_message": 89,
    "after_menu_help_question": 157, "visit_recovery_message": 72,
    "attachment_received_message": 89, "end": 74,
}
_DEFAULT_HEIGHT = 100
_MARGIN = 55


def _main_intake_graph() -> dict:
    nodes = []
    conns = []
    # Dos columnas de siembra: la 0 es la cadena principal conectada (disparador -> bienvenida
    # -> ...); la 1 es para mensajes independientes que NO cuelgan de nada (interrupciones
    # universales, reintentos, recuperación) — así no dependen del auto-layout del editor, que
    # no reparte bien muchos nodos huérfanos compartiendo la misma fila.
    y_cols = {0: 20, 1: 20}
    x_cols = {0: 460, 1: 1000}

    def add(node_id, ntype, name, text=None, options=None, w=340, col=0, **extra):
        node = {"id": node_id, "type": ntype, "x": x_cols[col], "y": y_cols[col], "w": w, "name": name}
        if text is not None:
            node["text"] = text
        if options is not None:
            node["options"] = options
        node.update(extra)
        nodes.append(node)
        y_cols[col] += _MEASURED_HEIGHT.get(node_id, _DEFAULT_HEIGHT) + _MARGIN
        return node_id

    def link(a, b, port=0):
        conns.append({"from": a, "fromPort": port, "to": b})

    trigger = add("trigger", "trigger", "Cliente escribe",
                   text="Se activa con el primer mensaje del cliente, o cuando no hay contexto todavía.")

    main_welcome = add("main_welcome", "message", "Bienvenida principal",
                        text="{saludo} Soy el asistente de Farmhouse 🌿\n\n¿Qué te gustaría hacer hoy? También puedes escribirme con tus propias palabras.")
    link(trigger, main_welcome)

    order_type_q = add("order_type_question", "question", "Tipo de pedido (lista real: 6 opciones)",
                        text=("¡Claro! ¿Cómo quieres recibir tu pedido?\n\n"
                              "Nota: el menú de bienvenida real es una LISTA de WhatsApp con 6 filas "
                              "(Ver el menú y pedir, Delivery, Retiro en local, Evento o empresa, "
                              "Ver sucursales, Hablar con alguien) — este nodo edita las 3 opciones "
                              "centrales del camino de pedido; la lista completa no es editable aquí."),
                        options=["Delivery", "Retiro en local", "Evento / empresa"])
    link(main_welcome, order_type_q)

    # No se conecta a order_type_question: en la vida real este camino se activa aparte
    # ("ver el menú" en texto libre, o la fila 1 de la lista de bienvenida), sin pasar por la
    # pregunta de tipo de pedido — se deja sin conexión de entrada a propósito.
    menu_direct_body = add("branch_selection_menu_direct_body", "message", "Sucursal (acceso directo al menú)",
                            text="¡Perfecto! 🍽️ ¿Desde cuál sucursal te gustaría pedir?", col=1)

    visit_body = add("branch_selection_visit_body", "message", "Sucursal (visita/consulta)",
                      text="¿Cuál sucursal quieres consultar? Te mostraré su dirección y horario.", col=1)
    delivery_body = add("branch_selection_delivery_body", "message", "Sucursal (delivery)",
                         text="Delivery, entendido 🛵 ¿Desde cuál sucursal deseas pedir?")
    link(order_type_q, delivery_body, 0)

    pickup_body = add("branch_selection_pickup_body", "message", "Sucursal (retiro)",
                       text="Listo, sería para retirar 🛍️ ¿En cuál sucursal?")
    link(order_type_q, pickup_body, 1)

    visit_opening = add("branch_visit_opening", "message", "Apertura: info de sucursal (visita)",
                         text="¡Excelente! Te esperamos en la sucursal de *{sucursal}*.")
    link(visit_body, visit_opening)

    manager_help_q = add("manager_help_question", "question", "¿Algo más? (tras info de sucursal)",
                          text="¿Qué más te gustaría hacer?",
                          options=["Hablar con gerente", "Ver el menú", "Nos vemos pronto"])
    link(visit_opening, manager_help_q)

    manager_assigned = add("manager_assigned_message", "message", "Gerente asignado",
                            text=("¡Con mucho gusto! 🤝 Te comunicamos de inmediato con el gerente de nuestra sucursal de *{sucursal}*.\n\n"
                                  "En un momento te estará atendiendo personalmente por aquí. ¡Muchas gracias por tu paciencia! 😊"))
    link(manager_help_q, manager_assigned, 0)

    manager_declined = add("manager_declined_message", "message", "Despedida (no quiso gerente)",
                            text="¡Perfecto! Muchas gracias por escribirnos. ¡Te esperamos pronto en Farmhouse *{sucursal}*! Que tengas un excelente día 🌿✨")
    link(manager_help_q, manager_declined, 2)

    delivery_opening = add("branch_delivery_opening", "message", "Apertura: info de sucursal (delivery)",
                            text="¡Excelente! 🛵 Tu pedido a domicilio saldrá de nuestra sucursal de *{sucursal}*.")
    link(delivery_body, delivery_opening)

    menu_link_delivery = add("menu_link_delivery_body", "message", "Menú Digital (delivery)",
                              text=("🍽️ Aquí tienes nuestro Menú Digital para armar tu pedido a domicilio desde Farmhouse *{sucursal}*.\n\n"
                                    "_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos, ingresa tu dirección y envíanos tu orden en 1 clic._\n\n"
                                    "Cualquier duda que tengas mientras armas tu pedido, aquí estamos para ayudarte con todo gusto 😊"))
    link(delivery_opening, menu_link_delivery)

    pickup_opening = add("branch_pickup_opening", "message", "Apertura: info de sucursal (retiro)",
                          text="¡Perfecto! 🛍️ Retirarás tu pedido en nuestra sucursal de *{sucursal}*.")
    link(pickup_body, pickup_opening)

    menu_link_pickup = add("menu_link_pickup_body", "message", "Menú Digital (retiro)",
                            text=("🍽️ Échale un vistazo a nuestro Menú Digital y arma tu pedido para retirar en Farmhouse *{sucursal}*.\n\n"
                                  "_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos y te lo tendremos fresco y listo cuando pases a retirarlo._\n\n"
                                  "Cualquier duda que tengas mientras armas tu pedido, aquí estamos para ayudarte con todo gusto 😊"))
    link(pickup_opening, menu_link_pickup)

    menu_link_generic = add("menu_link_generic_body", "message", "Menú Digital (genérico)",
                             text=("🍽️ Aquí tienes nuestro Menú Digital de Farmhouse *{sucursal}*.\n\n"
                                   "_Así vas viendo qué se te antoja antes de llegar, o si prefieres, también puedes hacer tu pedido desde aquí mismo._"))
    link(menu_direct_body, menu_link_generic)

    payment_ach = add("payment_ach", "message", "Instrucciones de pago (ACH)",
                       text=("¡Perfecto! 🏦 Estos son los datos de nuestra cuenta para pagar por ACH:\n\n"
                             "Banco: Banco General\n"
                             "Tipo de cuenta: Cuenta corriente\n"
                             "Nombre de cuenta: Grupo Col Rizado\n"
                             "Número de cuenta: 03-01-01-1480750\n\n"
                             "En cuanto nuestro equipo te confirme el total de tu pedido, puedes hacer la transferencia a esta cuenta. "
                             "Cuando la hagas, ¿me regalas una foto del comprobante de pago? Así agilizamos tu pedido muchísimo más rápido. "
                             "¡Muchas gracias por tu paciencia! 😊"))
    link(menu_link_delivery, payment_ach)

    payment_card = add("payment_card", "message", "Instrucciones de pago (Tarjeta)",
                        text=("¡Perfecto! 💳 Como seleccionaste pago con tarjeta, en un momento nuestro agente de turno te enviará "
                              "por este chat el enlace de pago seguro para que puedas completar tu compra cómodamente con tu tarjeta de crédito o débito.\n\n"
                              "Por favor regálanos unos breves minutos mientras lo generamos para ti. ¡Muchas gracias por tu paciencia y preferencia! 😊✨"))
    link(payment_ach, payment_card)

    payment_yappy = add("payment_yappy", "message", "Instrucciones de pago (Yappy)",
                         text=("¡Perfecto! 📱 Elegiste pagar con Yappy. Cuando confirmes el pedido te enviaremos por este mismo chat "
                               "un botón con el monto exacto. Solo tendrás que abrirlo y aprobar la solicitud en tu aplicación Yappy. "
                               "Nunca te pediremos tu PIN ni contraseña. 😊"))
    link(payment_card, payment_yappy)

    human_handoff = add("human_handoff_message", "message", "Entrega a una persona (pausa el bot)",
                         text="Claro 🤝 Ya compartí tu solicitud con nuestro equipo{lugar}. Una persona continuará contigo por este mismo chat y podrá ver lo que ya conversamos, así que no tendrás que repetirlo.")
    link(payment_yappy, human_handoff)

    corporate_intro = add("corporate_intro", "message", "Intro Pedido Corporativo/Evento",
                           text=("¡Qué gran noticia! 🎉 En Farmhouse nos encanta atender pedidos corporativos, reuniones de oficina, catering y eventos especiales.\n\n"
                                 "Para armarte la mejor propuesta, te hago unas preguntas rápidas antes de comunicarte con Sol, nuestra encargada de eventos y cuentas corporativas 😊"))
    link(order_type_q, corporate_intro, 2)

    corporate_event_type_q = add("corporate_event_type_question", "question", "Corporativo 1/4: tipo de evento",
                                  text="¿Qué tipo de evento tienes en mente?",
                                  options=["Reunión corporativa", "Evento especial", "Otro"])
    link(corporate_intro, corporate_event_type_q)

    corporate_headcount_q = add("corporate_headcount_question", "message", "Corporativo 2/4: personas y fecha",
                                 text=("Cuéntame un poco más: ¿para cuántas personas sería y qué fecha/hora tienes en mente? "
                                       "Puedes responderme todo junto, por ejemplo: “25 personas, viernes 12 al mediodía”."))
    link(corporate_event_type_q, corporate_headcount_q, 0)

    corporate_date_q = add("corporate_date_question", "message", "Corporativo 3/4: fecha y hora",
                            text="¡Genial! ¿Tienes fecha y hora en mente?")
    link(corporate_headcount_q, corporate_date_q)

    corporate_location_q = add("corporate_location_question", "question", "Corporativo 4/4: lugar de entrega",
                                text="Última pregunta: ¿dónde te gustaría recibir el pedido?",
                                options=["Retiro en sucursal", "Entrega en mi lugar", "Aún no lo sé"])
    link(corporate_date_q, corporate_location_q)

    corporate_location_combined = add("corporate_location_after_combined", "message", "Corporativo 4/4 (respuesta conjunta)",
                                       text="¡Genial, gracias! Última pregunta: ¿dónde te gustaría recibir el pedido?")
    link(corporate_headcount_q, corporate_location_combined, 0)

    corporate_closing = add("corporate_closing", "message", "Cierre del intake corporativo",
                             text=("¡Listo! 🎉 Ya tengo todo lo que Sol necesita para armarte una propuesta. En un momento te comunico con ella por este mismo chat. "
                                   "¡Gracias por pensar en Farmhouse para tu evento! 😊"))
    link(corporate_location_q, corporate_closing, 0)
    link(corporate_location_combined, corporate_closing)

    corporate_pause_action = add("corporate_pause_action", "action", "Pausar y avisar al equipo",
                                  kind="Pausar automatización y notificar al equipo",
                                  note="Se activa al terminar las 4 preguntas del intake corporativo (ilustrativo).")
    link(corporate_closing, corporate_pause_action)

    corporate_invalid = add("corporate_invalid_option_retry", "message", "Reintento: opción no reconocida (corporativo)",
                             text="No entendí bien, ¿me eliges una de estas opciones?", col=1)
    corporate_headcount_retry = add("corporate_headcount_retry", "message", "Reintento: cantidad de personas",
                                     text="¿Me confirmas para cuántas personas sería, aproximadamente?", col=1)
    corporate_date_retry = add("corporate_date_retry", "message", "Reintento: fecha y hora",
                                text="¿Me compartes la fecha y hora que tienes en mente?", col=1)

    restart_msg = add("restart_message", "message", "Reinicio ('reiniciar')",
                       text="Claro, empezamos de nuevo. No pasa nada 😊", col=1)
    cancel_msg = add("cancel_message", "message", "Cancelar selección",
                      text="Listo, dejé a un lado esa selección. ¿Qué te gustaría hacer ahora?", col=1)
    change_order_type_msg = add("change_order_type_message", "message", "Cambiar tipo de pedido",
                                 text="Sin problema. ¿Cómo prefieres recibir el pedido?", col=1)
    change_branch_msg = add("change_branch_message", "message", "Cambiar de sucursal",
                             text="Claro, puedes elegir otra sucursal.", col=1)

    unknown_main = add("unknown_main_message", "message", "Recuperación: no entendí (menú principal)",
                        text="No estoy completamente seguro de haber entendido 😅 ¿Cuál de estas opciones se parece más a lo que necesitas?", col=1)
    unknown_order = add("unknown_order_message", "message", "Recuperación: no entendí (tipo de pedido)",
                         text="Quiero ayudarte bien 😊 ¿Buscas delivery, retiro en una sucursal o un pedido para evento/empresa?", col=1)
    unknown_branch = add("unknown_branch_message", "message", "Recuperación: no entendí (sucursal)",
                          text="No logré identificar la sucursal. Elígela aquí o escríbeme su nombre.", col=1)

    after_menu_q = add("after_menu_help_question", "question", "¿Algo más? (tras enviar el menú)",
                        text="Mientras ves el menú, ¿hay algo más en lo que pueda ayudarte?",
                        options=["Abrir el menú", "Cambiar sucursal", "Hablar con alguien"], col=1)

    visit_recovery = add("visit_recovery_message", "message", "Recuperación: modo visita",
                          text="Quiero asegurarme de ayudarte bien 😊", col=1)
    attachment_received = add("attachment_received_message", "message", "Archivo recibido (no es texto)",
                               text="Recibí tu archivo, gracias 📎 Ya se lo compartí al equipo para que lo revise y continúe contigo por aquí.", col=1)

    end = add("end", "end", "Fin: con el equipo")
    link(human_handoff, end)

    return {"nodes": nodes, "conns": conns}


def upgrade() -> None:
    bind = op.get_bind()
    graph_json = json.dumps(_main_intake_graph(), ensure_ascii=False)
    result = bind.execute(
        sa.text("UPDATE bot_flows SET graph_json = :graph_json, updated_at = :updated_at WHERE `key` = 'main_intake'"),
        {"graph_json": graph_json, "updated_at": datetime.now(timezone.utc)},
    )
    if result.rowcount == 0:
        # No debería pasar (016 ya sembró la fila), pero por si la migración 016 no corrió
        # en este entorno, se inserta directamente en vez de fallar en silencio.
        bot_flows = sa.table(
            'bot_flows',
            sa.column('key', sa.String),
            sa.column('name', sa.String),
            sa.column('graph_json', sa.Text),
            sa.column('updated_at', sa.DateTime),
        )
        op.bulk_insert(bot_flows, [{
            'key': 'main_intake',
            'name': 'Camino principal (conectado al bot real)',
            'graph_json': graph_json,
            'updated_at': datetime.now(timezone.utc),
        }])


def downgrade() -> None:
    # No se conserva el grafo anterior de la Fase 1: revertir solo deja la fila tal cual está
    # (downgrade de esta migración no es un caso de uso real, ver 016 para recrear desde cero).
    pass
