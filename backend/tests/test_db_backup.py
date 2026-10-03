"""
Respaldo diario de la base: solo admin lo ve y lo baja, /media nunca sirve la carpeta de
respaldos, se guardan los últimos KEEP_DAYS, y el lector de sentencias arma bien un CREATE TABLE
de varias líneas y los INSERT con punto y coma dentro de los textos.
"""
import gzip
import json
from datetime import datetime

import pytest

from services import db_backup
from tests.conftest import auth_headers_for


def _escribir(path, tablas, cuerpo="SET NAMES utf8mb4;\n"):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(db_backup.HEADER_PREFIX + json.dumps({"created_at": "2026-10-03T03:30:00-05:00", "tables": tablas}) + "\n")
        f.write(cuerpo)


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    d = tmp_path / "_backups"
    d.mkdir()
    monkeypatch.setattr(db_backup, "BACKUP_DIR", d.resolve())
    return d


def test_solo_admin_ve_y_baja_los_respaldos(client, carpeta, admin_user, supervisor_user, clayton_agent, clayton_device, monkeypatch):
    monkeypatch.setattr(db_backup, "is_supported", lambda: False)   # la base de pruebas no es MySQL
    _escribir(carpeta / "farmhouse-2026-10-03.sql.gz", {"users": 4, "branches": 6})
    (carpeta / "otra-cosa.txt").write_text("no es un respaldo")

    ha = auth_headers_for(admin_user)
    data = client.get("/api/system/backups", headers=ha).json()
    assert data["keep_days"] == db_backup.KEEP_DAYS and data["supported"] is False
    assert [(b["name"], b["tables"], b["rows"]) for b in data["backups"]] == [("farmhouse-2026-10-03.sql.gz", 2, 10)]

    r = client.get("/api/system/backups/farmhouse-2026-10-03.sql.gz", headers=ha)
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert client.get("/api/system/backups/otra-cosa.txt", headers=ha).status_code == 404
    assert client.get("/api/system/backups/..%2F..%2Fmain.py", headers=ha).status_code == 404

    for user, device in ((supervisor_user, clayton_device), (clayton_agent, clayton_device)):
        h = auth_headers_for(user, device.device_id)
        assert client.get("/api/system/backups", headers=h).status_code == 403
        assert client.get("/api/system/backups/farmhouse-2026-10-03.sql.gz", headers=h).status_code == 403
        assert client.post("/api/system/backups", headers=h).status_code == 403

    # En SQLite no hay respaldo posible: lo dice claro en vez de fallar raro.
    assert client.post("/api/system/backups", headers=ha).status_code == 400


def test_media_nunca_sirve_la_carpeta_de_respaldos(client, clayton_agent, clayton_device):
    from routers import media
    carpeta = media.MEDIA_DIR / "_backups"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / "farmhouse-2099-01-01.sql.gz"
    archivo.write_bytes(b"secreto")
    try:
        h = auth_headers_for(clayton_agent, clayton_device.device_id)
        assert client.get("/media/_backups/farmhouse-2099-01-01.sql.gz", headers=h).status_code == 404
        assert client.get("/api/media/_backups/farmhouse-2099-01-01.sql.gz", headers=h).status_code == 404
    finally:
        archivo.unlink()


def test_se_guardan_los_ultimos_y_el_diario_va_a_su_hora(carpeta):
    for dia in range(1, 18):
        _escribir(carpeta / f"farmhouse-2026-09-{dia:02d}.sql.gz", {"users": 1})
    borrados = db_backup.prune()
    assert len(borrados) == 17 - db_backup.KEEP_DAYS and "farmhouse-2026-09-01.sql.gz" in borrados
    assert len(db_backup.list_backups()) == db_backup.KEEP_DAYS

    from services.branch_hours import PANAMA_TZ
    assert db_backup.daily_due(datetime(2026, 10, 3, 2, 0, tzinfo=PANAMA_TZ)) is False      # todavía no es la hora
    assert db_backup.daily_due(datetime(2026, 10, 3, 4, 0, tzinfo=PANAMA_TZ)) is True
    _escribir(carpeta / "farmhouse-2026-10-03.sql.gz", {"users": 1})
    assert db_backup.daily_due(datetime(2026, 10, 3, 4, 0, tzinfo=PANAMA_TZ)) is False      # ya se hizo hoy


def test_el_lector_arma_bien_las_sentencias(carpeta):
    cuerpo = (
        "SET NAMES utf8mb4;\n"
        "\n-- Tabla notas: 2 filas\n"
        "DROP TABLE IF EXISTS `notas`;\n"
        "CREATE TABLE `notas` (\n  `id` int NOT NULL,\n  `texto` text COMMENT 'x',\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;\n"
        "INSERT INTO `notas` (`id`, `texto`) VALUES (1,'hola; chau'),(2,'línea\\nnueva;');\n"
    )
    p = carpeta / "farmhouse-2026-10-03.sql.gz"
    _escribir(p, {"notas": 2}, cuerpo)
    sentencias = list(db_backup.iter_statements(p))
    assert sentencias[0] == "SET NAMES utf8mb4;"
    assert sentencias[2].startswith("CREATE TABLE `notas` (") and sentencias[2].endswith("ENGINE=InnoDB;")
    assert sentencias[3].startswith("INSERT INTO `notas`") and "hola; chau" in sentencias[3]
    assert len(sentencias) == 4
    assert db_backup.backup_path("farmhouse-2026-10-03.sql.gz") == p.resolve()
    assert db_backup.backup_path("../farmhouse-2026-10-03.sql.gz") is None
