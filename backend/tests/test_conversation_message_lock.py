"""
Antes de esto, dos mensajes seguidos del mismo cliente (sin esperar la respuesta del bot) podían
procesarse en paralelo: _process_auto_flow_background pausa ~1-2s para simular que el bot "está
escribiendo" antes de releer el estado de la conversación, y si un segundo mensaje llegaba
durante esa pausa, su propia relectura podía ocurrir ANTES de que el primer mensaje confirmara su
cambio de estado (ej. corporate_intake_step) — perdiendo la respuesta del cliente en silencio.
Reproducido en vivo: un cliente que tocaba "Evento o empresa" y de inmediato el tipo de evento
podía quedar con el bot preguntando lo mismo para siempre.

Ver _get_conversation_lock / _process_auto_flow_background en routers/webhooks.py. Estas pruebas
verifican la garantía de exclusión mutua en sí (un lock de asyncio por conversation_id), sin
depender de los tiempos de la lógica de negocio real — así no dependen de timings de sleeps ni de
si SQLite tolera bien escrituras concurrentes desde dos sesiones.
"""
import asyncio

from routers import webhooks


def test_two_messages_for_the_same_conversation_never_run_at_the_same_time(monkeypatch):
    events = []

    async def fake_locked(conv_id, contact_id, phone, msg_data, msg_id):
        events.append(("start", msg_id))
        await asyncio.sleep(0.05)
        events.append(("end", msg_id))

    monkeypatch.setattr(webhooks, "_process_auto_flow_background_locked", fake_locked)

    async def run():
        await asyncio.gather(
            webhooks._process_auto_flow_background(101, 1, "+50760000001", {}, 1001),
            webhooks._process_auto_flow_background(101, 1, "+50760000001", {}, 1002),
        )

    asyncio.run(run())

    assert events == [("start", 1001), ("end", 1001), ("start", 1002), ("end", 1002)], (
        "el segundo mensaje de la misma conversación debería esperar a que el primero termine "
        "por completo, nunca correr en paralelo"
    )


def test_messages_for_different_conversations_are_not_serialized_against_each_other(monkeypatch):
    events = []

    async def fake_locked(conv_id, contact_id, phone, msg_data, msg_id):
        events.append(("start", msg_id))
        await asyncio.sleep(0.05)
        events.append(("end", msg_id))

    monkeypatch.setattr(webhooks, "_process_auto_flow_background_locked", fake_locked)

    async def run():
        await asyncio.gather(
            webhooks._process_auto_flow_background(201, 1, "+50760000001", {}, 2001),
            webhooks._process_auto_flow_background(202, 2, "+50760000002", {}, 2002),
        )

    asyncio.run(run())

    # Conversaciones distintas no deben bloquearse entre sí: ambas arrancan antes de que
    # cualquiera de las dos termine.
    assert events[0][0] == "start" and events[1][0] == "start", (
        "conversaciones distintas no deberían esperarse entre sí"
    )
