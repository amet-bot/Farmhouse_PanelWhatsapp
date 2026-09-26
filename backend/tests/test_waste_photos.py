"""
Fotos de respaldo de la merma (el peso en la balanza): se guardan en la base, solo imágenes de
verdad, y las ve solo quien puede ver esa merma.
"""
from tests.conftest import auth_headers_for

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"foto-del-peso"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _headers(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _merma(client, headers, branch_id):
    item = client.post("/api/inventory/items", json={"name": "Piña", "unit": "kg"}, headers=headers).json()
    res = client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": "vencido",
        "items": [{"inventory_item_id": item["id"], "quantity": "1.3"}],
    }, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _subir(client, headers, waste_id, data, name="peso.jpg", ctype="image/jpeg"):
    return client.post(f"/api/inventory/waste/{waste_id}/photos",
                       files={"file": (name, data, ctype)}, headers=headers)


def test_sube_y_devuelve_la_foto(client, clayton_branch, clayton_agent, clayton_device):
    h = _headers(clayton_agent, clayton_device)
    merma = _merma(client, h, clayton_branch.id)
    assert merma["photos"] == []

    res = _subir(client, h, merma["id"], JPEG)
    assert res.status_code == 201, res.text
    fotos = res.json()["photos"]
    assert len(fotos) == 1
    assert fotos[0]["content_type"] == "image/jpeg" and fotos[0]["size_bytes"] == len(JPEG)
    assert fotos[0]["uploaded_by_name"] == clayton_agent.name

    img = client.get(f"/api/inventory/waste/{merma['id']}/photos/{fotos[0]['id']}", headers=h)
    assert img.status_code == 200
    assert img.content == JPEG
    assert img.headers["content-type"] == "image/jpeg"
    assert img.headers["x-content-type-options"] == "nosniff"

    # El listado trae las fotos (sin los bytes).
    lista = client.get("/api/inventory/waste", headers=h).json()
    assert lista[0]["photos"][0]["id"] == fotos[0]["id"]
    assert "data" not in lista[0]["photos"][0]


def test_el_tipo_se_decide_por_el_contenido(client, clayton_branch, clayton_agent, clayton_device):
    h = _headers(clayton_agent, clayton_device)
    merma = _merma(client, h, clayton_branch.id)
    # Un HTML que dice ser imagen no entra.
    falso = _subir(client, h, merma["id"], b"<html><script>alert(1)</script></html>", "x.jpg", "image/jpeg")
    assert falso.status_code == 415
    # Un PNG que dice ser JPG entra, como PNG.
    png = _subir(client, h, merma["id"], PNG, "x.jpg", "image/jpeg")
    assert png.status_code == 201
    assert png.json()["photos"][0]["content_type"] == "image/png"


def test_otra_sucursal_no_puede_ver_ni_subir(client, clayton_branch, clayton_agent, clayton_device,
                                              obarrio_agent, obarrio_device):
    merma = _merma(client, _headers(clayton_agent, clayton_device), clayton_branch.id)
    foto = _subir(client, _headers(clayton_agent, clayton_device), merma["id"], JPEG).json()["photos"][0]

    ajeno = _headers(obarrio_agent, obarrio_device)
    assert _subir(client, ajeno, merma["id"], JPEG).status_code == 403
    assert client.get(f"/api/inventory/waste/{merma['id']}/photos/{foto['id']}", headers=ajeno).status_code == 403


def test_admin_ve_la_foto_de_cualquier_sucursal(client, clayton_branch, clayton_agent, clayton_device, admin_user):
    merma = _merma(client, _headers(clayton_agent, clayton_device), clayton_branch.id)
    foto = _subir(client, _headers(clayton_agent, clayton_device), merma["id"], JPEG).json()["photos"][0]
    img = client.get(f"/api/inventory/waste/{merma['id']}/photos/{foto['id']}", headers=_headers(admin_user))
    assert img.status_code == 200 and img.content == JPEG


def test_tope_de_fotos_por_merma(client, clayton_branch, clayton_agent, clayton_device):
    h = _headers(clayton_agent, clayton_device)
    merma = _merma(client, h, clayton_branch.id)
    for _ in range(6):
        assert _subir(client, h, merma["id"], JPEG).status_code == 201
    assert _subir(client, h, merma["id"], JPEG).status_code == 400


def test_foto_de_otra_merma_no_se_sirve_por_id_cruzado(client, clayton_branch, clayton_agent, clayton_device):
    h = _headers(clayton_agent, clayton_device)
    a = _merma(client, h, clayton_branch.id)
    b = client.post("/api/inventory/waste", json={
        "branch_id": clayton_branch.id, "reason": "vencido",
        "items": [{"inventory_item_id": a["items"][0]["inventory_item_id"], "quantity": "1"}],
    }, headers=h).json()
    foto_a = _subir(client, h, a["id"], JPEG).json()["photos"][0]
    assert client.get(f"/api/inventory/waste/{b['id']}/photos/{foto_a['id']}", headers=h).status_code == 404


def test_guarda_el_peso_de_la_balanza(client, clayton_branch, clayton_agent, clayton_device):
    h = _headers(clayton_agent, clayton_device)
    item = client.post("/api/inventory/items", json={"name": "Piña", "unit": "unidad"}, headers=h).json()
    base = {"branch_id": clayton_branch.id, "reason": "vencido",
            "items": [{"inventory_item_id": item["id"], "quantity": "2"}]}

    con_peso = client.post("/api/inventory/waste", json={**base, "weight_value": "1.35", "weight_unit": "kg"}, headers=h)
    assert con_peso.status_code == 201, con_peso.text
    assert con_peso.json()["weight_value"] == "1.350" and con_peso.json()["weight_unit"] == "kg"

    # Sin unidad se toma kg; sin peso, no hay unidad.
    sin_unidad = client.post("/api/inventory/waste", json={**base, "weight_value": "800"}, headers=h).json()
    assert sin_unidad["weight_unit"] == "kg"
    sin_peso = client.post("/api/inventory/waste", json={**base, "weight_unit": "g"}, headers=h).json()
    assert sin_peso["weight_value"] is None and sin_peso["weight_unit"] is None

    # Unidad inventada o peso en cero: no.
    assert client.post("/api/inventory/waste", json={**base, "weight_value": "1", "weight_unit": "ton"}, headers=h).status_code == 422
    assert client.post("/api/inventory/waste", json={**base, "weight_value": "0"}, headers=h).status_code == 422

    lista = client.get("/api/inventory/waste", headers=h).json()
    assert any(w["weight_value"] == "1.350" for w in lista)
