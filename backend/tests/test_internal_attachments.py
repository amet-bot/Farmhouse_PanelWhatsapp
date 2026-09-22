"""
Adjuntos de Comunicación Interna: subida, validación y quién puede después abrir el archivo.

La autorización del archivo es lo que más importa acá. El endpoint de medios nació para el
Centro WhatsApp, donde se decide mirando la sucursal de la conversación; un adjunto interno no
pertenece a ninguna conversación de WhatsApp, así que sin una regla propia habría quedado
legible para cualquiera con sesión iniciada.
"""
import base64

import pytest

from services import media_storage
from tests.conftest import auth_headers_for


# PNG de 1x1 válido: alcanza para que el backend lo trate como una imagen de verdad.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _headers(user, device):
    return auth_headers_for(user, device.device_id)


@pytest.fixture
def limpia_adjuntos():
    """
    Los adjuntos se escriben en disco de verdad (backend/media/internal). Se anota qué había
    antes y se borra lo que la prueba haya dejado, para no ir llenando el repo de basura.
    """
    antes = set(media_storage.INTERNAL_ROOT.glob("*"))
    yield
    for sobrante in set(media_storage.INTERNAL_ROOT.glob("*")) - antes:
        sobrante.unlink(missing_ok=True)


@pytest.fixture
def hilo(client, clayton_agent, clayton_device, obarrio_agent):
    return client.post(
        "/api/internal/threads/direct",
        json={"user_id": obarrio_agent.id},
        headers=_headers(clayton_agent, clayton_device),
    ).json()


def _subir(client, user, device, thread_id, *, contenido=PNG_1X1, nombre="faltante.png",
           mime="image/png", caption=""):
    return client.post(
        f"/api/internal/threads/{thread_id}/attachments",
        files={"file": (nombre, contenido, mime)},
        data={"caption": caption},
        headers=_headers(user, device),
    )


def test_una_foto_llega_como_un_mensaje_mas(client, clayton_agent, clayton_device, hilo,
                                            limpia_adjuntos):
    res = _subir(client, clayton_agent, clayton_device, hilo["id"])
    assert res.status_code == 201, res.text

    msg = res.json()
    assert msg["media_url"].startswith("/media/internal/")
    assert msg["media_mime_type"] == "image/png"
    assert msg["media_name"] == "faltante.png"
    assert msg["media_size"] == len(PNG_1X1)
    # Sin pie de foto el cuerpo queda vacío, no nulo: quien lee siempre recibe un str.
    assert msg["body"] == ""


def test_el_pie_de_foto_viaja_con_el_adjunto(client, clayton_agent, clayton_device, hilo,
                                             limpia_adjuntos):
    res = _subir(client, clayton_agent, clayton_device, hilo["id"], caption="Faltan 3 cajas")
    assert res.json()["body"] == "Faltan 3 cajas"


def test_el_nombre_del_archivo_pierde_la_ruta(client, clayton_agent, clayton_device, hilo,
                                              limpia_adjuntos):
    """Un nombre con carpetas no puede terminar decidiendo dónde se guarda ni qué se descarga."""
    res = _subir(client, clayton_agent, clayton_device, hilo["id"],
                 nombre="../../etc/passwd.png")
    assert res.status_code == 201, res.text
    assert res.json()["media_name"] == "passwd.png"


def test_solo_pasan_los_tipos_de_la_lista_blanca(client, clayton_agent, clayton_device, hilo,
                                                 limpia_adjuntos):
    res = _subir(client, clayton_agent, clayton_device, hilo["id"],
                 contenido=b"<script>alert(1)</script>", nombre="x.html", mime="text/html")
    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]


def test_un_archivo_muy_pesado_se_rechaza(client, clayton_agent, clayton_device, hilo,
                                          limpia_adjuntos):
    gordo = b"\0" * (media_storage.MAX_ATTACHMENT_BYTES + 1)
    res = _subir(client, clayton_agent, clayton_device, hilo["id"],
                 contenido=gordo, nombre="enorme.pdf", mime="application/pdf")
    assert res.status_code == 413


def test_un_archivo_vacio_se_rechaza(client, clayton_agent, clayton_device, hilo,
                                     limpia_adjuntos):
    res = _subir(client, clayton_agent, clayton_device, hilo["id"], contenido=b"",
                 nombre="nada.png")
    assert res.status_code == 400


def test_quien_no_participa_no_puede_subir(client, admin_user, hilo, limpia_adjuntos):
    res = client.post(
        f"/api/internal/threads/{hilo['id']}/attachments",
        files={"file": ("x.png", PNG_1X1, "image/png")},
        headers=auth_headers_for(admin_user),
    )
    assert res.status_code == 403


def test_la_bandeja_describe_el_adjunto_sin_texto(client, clayton_agent, clayton_device,
                                                  obarrio_agent, obarrio_device, hilo,
                                                  limpia_adjuntos):
    """Una foto sin pie no puede dejar la fila de la bandeja en blanco."""
    _subir(client, clayton_agent, clayton_device, hilo["id"])
    bandeja = client.get("/api/internal/threads", headers=_headers(obarrio_agent, obarrio_device)).json()
    fila = next(t for t in bandeja if t["id"] == hilo["id"])
    assert fila["last_message_preview"] == "📷 Foto"

    _subir(client, clayton_agent, clayton_device, hilo["id"], nombre="factura.pdf",
           mime="application/pdf", contenido=b"%PDF-1.4 mini")
    bandeja = client.get("/api/internal/threads", headers=_headers(obarrio_agent, obarrio_device)).json()
    fila = next(t for t in bandeja if t["id"] == hilo["id"])
    assert fila["last_message_preview"] == "📎 factura.pdf"


def test_el_destinatario_puede_abrir_el_archivo(client, clayton_agent, clayton_device,
                                                obarrio_agent, obarrio_device, hilo,
                                                limpia_adjuntos):
    url = _subir(client, clayton_agent, clayton_device, hilo["id"]).json()["media_url"]
    res = client.get(f"/api{url}", headers=_headers(obarrio_agent, obarrio_device))
    assert res.status_code == 200, res.text
    assert res.content == PNG_1X1


def test_un_ajeno_al_hilo_no_puede_abrir_el_archivo(client, clayton_agent, clayton_device,
                                                    admin_user, hilo, limpia_adjuntos):
    """
    El admin ve todo el Centro WhatsApp, pero un directo entre dos personas no es suyo. Esta
    es la regla que el endpoint de medios no tenía: sin ella devolvía el archivo a cualquiera
    con sesión, porque no encontraba ningún mensaje de WhatsApp al que aplicarle la de sucursal.
    """
    url = _subir(client, clayton_agent, clayton_device, hilo["id"]).json()["media_url"]
    res = client.get(f"/api{url}", headers=auth_headers_for(admin_user))
    assert res.status_code == 403


def test_el_aviso_push_sale_hacia_el_otro_y_no_hacia_quien_escribe(
    client, clayton_agent, clayton_device, obarrio_agent, hilo, limpia_adjuntos, monkeypatch
):
    from services import push_service

    enviados = []

    def _spy(db, thread_id, thread_title, sender_name, body, recipient_user_ids):
        enviados.append((thread_id, sender_name, body, list(recipient_user_ids)))

    monkeypatch.setattr(push_service, "notify_internal_message", _spy)

    client.post(f"/api/internal/threads/{hilo['id']}/messages", json={"body": "llegó el camión"},
                headers=_headers(clayton_agent, clayton_device))

    assert len(enviados) == 1
    thread_id, sender_name, body, destinatarios = enviados[0]
    assert thread_id == hilo["id"]
    assert sender_name == "Agente Clayton"
    assert body == "llegó el camión"
    assert destinatarios == [obarrio_agent.id]   # nunca el propio autor


def test_el_aviso_push_de_una_foto_dice_que_es_una_foto(
    client, clayton_agent, clayton_device, obarrio_agent, hilo, limpia_adjuntos, monkeypatch
):
    from services import push_service

    enviados = []
    monkeypatch.setattr(
        push_service, "notify_internal_message",
        lambda **kw: enviados.append(kw),
    )

    _subir(client, clayton_agent, clayton_device, hilo["id"])

    assert enviados and enviados[0]["body"] == "📷 Foto"


def test_el_canal_del_equipo_avisa_con_el_nombre_del_canal(
    client, clayton_agent, clayton_device, supervisor_user, monkeypatch
):
    from services import push_service

    enviados = []
    monkeypatch.setattr(
        push_service, "notify_internal_message",
        lambda **kw: enviados.append(kw),
    )

    bandeja = client.get("/api/internal/threads", headers=_headers(clayton_agent, clayton_device)).json()
    canal = next(t for t in bandeja if t["kind"] == "branch")
    client.post(f"/api/internal/threads/{canal['id']}/messages", json={"body": "reunión 3pm"},
                headers=_headers(clayton_agent, clayton_device))

    assert enviados[0]["thread_title"] == "Equipo Clayton"
    assert set(enviados[0]["recipient_user_ids"]) == {supervisor_user.id}


def test_el_historial_devuelve_el_adjunto_igual_que_el_envio(
    client, clayton_agent, clayton_device, obarrio_agent, obarrio_device, hilo, limpia_adjuntos
):
    """
    Al recargar, la foto tiene que seguir ahí.

    El envío y el historial armaban la respuesta por su cuenta, y al agregar los adjuntos solo
    una de las dos copias se enteró: el mensaje se veía al mandarlo y desaparecía al volver a
    abrir la conversación. Ahora las dos pasan por el mismo serializador, y esto lo vigila.
    """
    enviado = _subir(client, clayton_agent, clayton_device, hilo["id"],
                     caption="Faltan 3 cajas").json()

    historial = client.get(f"/api/internal/threads/{hilo['id']}/messages",
                           headers=_headers(obarrio_agent, obarrio_device)).json()

    guardado = next(m for m in historial if m["id"] == enviado["id"])
    for campo in ("media_url", "media_mime_type", "media_name", "media_size", "body"):
        assert guardado[campo] == enviado[campo], campo
