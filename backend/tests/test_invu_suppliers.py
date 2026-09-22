"""
Proveedores traídos de Invu POS.

Nunca se habla con Invu de verdad: se reemplaza `iter_providers`, que es la única puerta por la
que el proyecto sale a esa API. Lo que se vigila es lo de este lado — el emparejamiento con lo
que ya estaba cargado a mano, que es donde una integración así rompe cosas, y la regla de que
mientras Invu mande el panel no crea proveedores propios.
"""
import pytest

from config import settings
from models.supplier import Supplier
from services import invu_client, invu_sync
from tests.conftest import auth_headers_for


def _headers(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _proveedor(invu_id, nombre, **extra):
    """Una fila como la que devuelve `providers/list`, con los vacíos raros de Invu incluidos."""
    fila = {
        "id": invu_id,
        "code": f"S{invu_id}",
        "name": nombre,
        "tax_id": "155612345-2-2021",
        "contact_name": "Kenji Tanaka",
        "currency": "dollar",
        "delivery_day": 3,
        "phone_1_type": "mobile",
        "phone_1": "6000-1111",
        "phone_2": "",
        "phone_3": "0",
        "email": "proveedor@ejemplo.com",
        "status": 1,
    }
    fila.update(extra)
    return fila


@pytest.fixture
def invu_encendido(monkeypatch):
    """Enciende la integración sin credenciales reales."""
    monkeypatch.setattr(settings, "INVU_API_USERNAME", "usuario-de-prueba")
    monkeypatch.setattr(settings, "INVU_API_PASSWORD", "clave-de-prueba")
    invu_client.reset_token_cache()
    yield
    invu_client.reset_token_cache()


@pytest.fixture
def invu_apagado(monkeypatch):
    monkeypatch.setattr(settings, "INVU_API_USERNAME", None)
    monkeypatch.setattr(settings, "INVU_API_PASSWORD", None)
    invu_client.reset_token_cache()


@pytest.fixture
def responde_invu(monkeypatch):
    """Devuelve una función para decidir qué proveedores 'trae' Invu en esta prueba."""
    def _cargar(filas):
        monkeypatch.setattr(invu_client, "iter_providers", lambda updated_after=None: iter(filas))
        monkeypatch.setattr(invu_sync.invu_client, "iter_providers", lambda updated_after=None: iter(filas))
    return _cargar


# ==========================================================================
# Estado de la integración
# ==========================================================================
def test_sin_credenciales_el_panel_sigue_mandando(client, clayton_agent, clayton_device, invu_apagado):
    res = client.get("/api/inventory/invu/status", headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 200
    assert res.json()["configured"] is False

    # Y se puede seguir creando proveedores a mano, como siempre.
    creado = client.post("/api/inventory/suppliers", json={"name": "Verduras del Valle"},
                         headers=_headers(clayton_agent, clayton_device))
    assert creado.status_code == 201


def test_con_invu_configurado_el_panel_no_crea_proveedores(client, clayton_agent, clayton_device,
                                                           invu_encendido):
    """
    Una sola fuente de verdad. Crear uno acá daría de alta un proveedor que en Invu no existe y
    que la próxima sincronización no sabría emparejar.
    """
    res = client.post("/api/inventory/suppliers", json={"name": "Inventado SA"},
                      headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 409
    assert "Invu" in res.json()["detail"]


def test_sincronizar_sin_credenciales_avisa_en_vez_de_romper(client, clayton_agent, clayton_device,
                                                             invu_apagado):
    res = client.post("/api/inventory/invu/sync-suppliers",
                      headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 503


# ==========================================================================
# Sincronización
# ==========================================================================
def test_trae_los_proveedores_con_todos_sus_datos(client, clayton_agent, clayton_device,
                                                  invu_encendido, responde_invu, db_session):
    responde_invu([_proveedor(6, "Distribuidora Nikkei")])

    res = client.post("/api/inventory/invu/sync-suppliers",
                      headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 201 or res.status_code == 200, res.text
    resumen = res.json()
    assert resumen["received"] == 1
    assert resumen["created"] == 1

    proveedor = db_session.query(Supplier).filter(Supplier.invu_id == 6).first()
    assert proveedor.name == "Distribuidora Nikkei"
    assert proveedor.code == "S6"
    assert proveedor.tax_id == "155612345-2-2021"
    assert proveedor.contact_name == "Kenji Tanaka"
    assert proveedor.email == "proveedor@ejemplo.com"
    assert proveedor.delivery_day == 3
    assert proveedor.phone == "6000-1111"
    assert proveedor.synced_at is not None


def test_los_vacios_raros_de_invu_quedan_en_nulo(client, clayton_agent, clayton_device,
                                                 invu_encendido, responde_invu, db_session):
    """Invu manda vacíos como "", "0" y "0000-00-00"; ninguno puede terminar guardado como texto."""
    responde_invu([_proveedor(7, "Sin datos", tax_id="", contact_name="0",
                              email="0000-00-00", delivery_day="0", phone_1="")])

    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    proveedor = db_session.query(Supplier).filter(Supplier.invu_id == 7).first()
    assert proveedor.tax_id is None
    assert proveedor.contact_name is None
    assert proveedor.email is None
    assert proveedor.delivery_day is None
    assert proveedor.phone is None


def test_toma_el_primer_telefono_que_exista(client, clayton_agent, clayton_device,
                                            invu_encendido, responde_invu, db_session):
    responde_invu([_proveedor(8, "Tres teléfonos", phone_1="", phone_2="6000-2222", phone_3="6000-3333")])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))
    assert db_session.query(Supplier).filter(Supplier.invu_id == 8).first().phone == "6000-2222"


def test_empareja_con_el_que_ya_estaba_cargado_a_mano(client, clayton_agent, clayton_device,
                                                      invu_apagado, invu_encendido,
                                                      responde_invu, db_session):
    """
    El caso que rompe una integración así: los proveedores del panel se cargaron antes de que
    existiera Invu. Sin emparejar por nombre, la primera sincronización crearía un duplicado y
    los cargamentos viejos quedarían colgando del que ya no se usa.
    """
    a_mano = Supplier(name="Verduras del Valle", phone="6000-9999")
    db_session.add(a_mano)
    db_session.commit()
    id_original = a_mano.id

    # Invu lo tiene escrito distinto en mayúsculas: igual tiene que reconocerlo.
    responde_invu([_proveedor(12, "verduras del valle")])
    res = client.post("/api/inventory/invu/sync-suppliers",
                      headers=_headers(clayton_agent, clayton_device))
    resumen = res.json()

    assert resumen["linked"] == 1
    assert resumen["created"] == 0
    assert db_session.query(Supplier).count() == 1

    db_session.refresh(a_mano)
    assert a_mano.id == id_original      # el mismo registro, no uno nuevo
    assert a_mano.invu_id == 12


def test_sincronizar_dos_veces_no_duplica(client, clayton_agent, clayton_device,
                                          invu_encendido, responde_invu, db_session):
    responde_invu([_proveedor(6, "Distribuidora Nikkei")])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    responde_invu([_proveedor(6, "Distribuidora Nikkei")])
    segunda = client.post("/api/inventory/invu/sync-suppliers",
                          headers=_headers(clayton_agent, clayton_device)).json()

    assert segunda["created"] == 0
    assert segunda["updated"] == 0     # nada cambió, no se cuenta como actualización
    assert db_session.query(Supplier).count() == 1


def test_un_cambio_en_invu_se_refleja_aca(client, clayton_agent, clayton_device,
                                          invu_encendido, responde_invu, db_session):
    responde_invu([_proveedor(6, "Distribuidora Nikkei", phone_1="6000-1111")])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    responde_invu([_proveedor(6, "Distribuidora Nikkei SA", phone_1="6000-7777")])
    resumen = client.post("/api/inventory/invu/sync-suppliers",
                          headers=_headers(clayton_agent, clayton_device)).json()

    assert resumen["updated"] == 1
    proveedor = db_session.query(Supplier).filter(Supplier.invu_id == 6).first()
    assert proveedor.name == "Distribuidora Nikkei SA"
    assert proveedor.phone == "6000-7777"


def test_un_proveedor_inactivo_en_invu_se_apaga_pero_no_se_borra(client, clayton_agent,
                                                                 clayton_device, invu_encendido,
                                                                 responde_invu, db_session):
    """Los cargamentos viejos lo siguen nombrando: desactivar sí, desaparecer no."""
    responde_invu([_proveedor(6, "Distribuidora Nikkei")])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    responde_invu([_proveedor(6, "Distribuidora Nikkei", status=0)])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    proveedor = db_session.query(Supplier).filter(Supplier.invu_id == 6).first()
    assert proveedor is not None
    assert proveedor.active is False


def test_un_proveedor_solo_del_panel_sobrevive_a_la_sincronizacion(client, clayton_agent,
                                                                   clayton_device, invu_encendido,
                                                                   responde_invu, db_session):
    """Puede ser uno chico que nunca se cargó en Invu; borrarlo se llevaría sus cargamentos."""
    propio = Supplier(name="Panadería de la esquina")
    db_session.add(propio)
    db_session.commit()

    responde_invu([_proveedor(6, "Distribuidora Nikkei")])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    db_session.refresh(propio)
    assert propio.invu_id is None
    assert propio.active is True


def test_el_estado_cuenta_de_donde_viene_cada_uno(client, clayton_agent, clayton_device,
                                                  invu_encendido, responde_invu, db_session):
    db_session.add(Supplier(name="Panadería de la esquina"))
    db_session.commit()

    responde_invu([_proveedor(6, "Nikkei"), _proveedor(7, "Central")])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    estado = client.get("/api/inventory/invu/status",
                        headers=_headers(clayton_agent, clayton_device)).json()
    assert estado["configured"] is True
    assert estado["synced_count"] == 2
    assert estado["local_count"] == 1
    assert estado["last_synced_at"] is not None


def test_el_estado_no_cuenta_como_visibles_los_inactivos(client, clayton_agent, clayton_device,
                                                         invu_encendido, responde_invu):
    """
    El catálogo solo lista proveedores activos. Si el estado contara también los apagados, la
    nota diría "3 vienen de Invu" arriba de una lista de 2 y nadie sabría cuál creer.
    """
    responde_invu([_proveedor(6, "Nikkei"), _proveedor(7, "Central"),
                   _proveedor(8, "Ya no trabajamos con ellos", status=0)])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    estado = client.get("/api/inventory/invu/status",
                        headers=_headers(clayton_agent, clayton_device)).json()
    assert estado["synced_count"] == 2
    assert estado["inactive_count"] == 1

    listados = client.get("/api/inventory/suppliers?limit=50",
                          headers=_headers(clayton_agent, clayton_device)).json()
    assert len(listados) == estado["synced_count"]


def test_si_invu_falla_se_cuenta_en_vez_de_romper(client, clayton_agent, clayton_device,
                                                  invu_encendido, monkeypatch):
    def _explota(updated_after=None):
        raise invu_client.InvuError("Invu rechazó las credenciales (HTTP 401).")
        yield  # pragma: no cover

    monkeypatch.setattr(invu_sync.invu_client, "iter_providers", _explota)

    res = client.post("/api/inventory/invu/sync-suppliers",
                      headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 502
    assert "credenciales" in res.json()["detail"]


def test_los_proveedores_sincronizados_salen_en_el_autocomplete(client, clayton_agent,
                                                                clayton_device, invu_encendido,
                                                                responde_invu):
    responde_invu([_proveedor(6, "Distribuidora Nikkei")])
    client.post("/api/inventory/invu/sync-suppliers", headers=_headers(clayton_agent, clayton_device))

    res = client.get("/api/inventory/suppliers?q=nikkei", headers=_headers(clayton_agent, clayton_device))
    assert "Distribuidora Nikkei" in [s["name"] for s in res.json()]
