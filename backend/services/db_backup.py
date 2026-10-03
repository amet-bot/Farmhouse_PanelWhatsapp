"""
Respaldo diario de la base de datos.

La base de MySQL de Railway no tiene acceso público (y está bien que no lo tenga), así que el
respaldo lo hace el propio servidor: cada madrugada vuelca todas las tablas a un archivo .sql.gz
en el disco persistente (media/_backups) y guarda los últimos KEEP_DAYS. Desde Administración se
puede bajar una copia para guardarla fuera de Railway, o hacer un respaldo en el momento.

El volcado es SQL normal (CREATE TABLE + INSERT), escrito en Python con el mismo conector que usa
la app, sin depender de que el contenedor tenga mysqldump. La primera línea guarda cuántas filas
tenía cada tabla: `restore_into()` las compara después de restaurar, que es la prueba de que el
respaldo sirve (scripts/restore_backup.py).

La carpeta _backups NO se sirve por /media (routers/media.py la bloquea): solo la baja un admin
por /system/backups.
"""
import asyncio
import gzip
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from database import engine
from services.branch_hours import PANAMA_TZ

logger = logging.getLogger("farmhouse.backup")

BACKUP_DIR = (Path(__file__).resolve().parent.parent / "media" / "_backups").resolve()
KEEP_DAYS = 14
RUN_HOUR = 3            # 3:30 a. m. de Panamá: no hay pedidos ni cierres en curso
RUN_MINUTE = 30
CHECK_INTERVAL_SECONDS = 20 * 60
STARTUP_DELAY_SECONDS = 240
NAME_RE = re.compile(r"^farmhouse-(\d{4}-\d{2}-\d{2})(-\d{6})?\.sql\.gz$")
HEADER_PREFIX = "-- FARMHOUSE_BACKUP "
BATCH_ROWS = 200


def is_supported() -> bool:
    return engine.dialect.name == "mysql"


def _ahora_panama() -> datetime:
    return datetime.now(PANAMA_TZ)


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _sql_value(conn, v) -> str:
    """Un valor listo para un INSERT. Los binarios van en hexadecimal (X'..'): el escape del
    conector los deja como texto con caracteres sueltos que no se pueden guardar en UTF-8."""
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "X'" + bytes(v).hex() + "'"
    return conn.escape(v)


def dump_to(path: Path) -> Dict[str, int]:
    """Vuelca todas las tablas a `path` (.sql.gz). Devuelve las filas por tabla."""
    raw = engine.raw_connection()
    try:
        conn = raw.driver_connection if hasattr(raw, "driver_connection") else raw.connection
        cur = conn.cursor()
        cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
        tablas = sorted(r[0] for r in cur.fetchall())
        conteos: Dict[str, int] = {}
        for t in tablas:
            cur.execute(f"SELECT COUNT(*) FROM {_quote_ident(t)}")
            conteos[t] = int(cur.fetchone()[0])

        tmp = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as f:
            f.write(HEADER_PREFIX + json.dumps({"created_at": _ahora_panama().isoformat(), "tables": conteos}) + "\n")
            f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\nSET UNIQUE_CHECKS=0;\n")
            for t in tablas:
                cur.execute(f"SHOW CREATE TABLE {_quote_ident(t)}")
                create_sql = cur.fetchone()[1]
                f.write(f"\n-- Tabla {t}: {conteos[t]} filas\n")
                f.write(f"DROP TABLE IF EXISTS {_quote_ident(t)};\n{create_sql};\n")
                if not conteos[t]:
                    continue
                cur.execute(f"SELECT * FROM {_quote_ident(t)}")
                cols = ", ".join(_quote_ident(d[0]) for d in cur.description)
                while True:
                    filas = cur.fetchmany(BATCH_ROWS)
                    if not filas:
                        break
                    valores = ",".join("(" + ",".join(_sql_value(conn, v) for v in fila) + ")" for fila in filas)
                    f.write(f"INSERT INTO {_quote_ident(t)} ({cols}) VALUES {valores};\n")
            f.write("SET UNIQUE_CHECKS=1;\nSET FOREIGN_KEY_CHECKS=1;\n")
        os.replace(tmp, path)
        return conteos
    finally:
        raw.close()


def read_header(path: Path) -> Optional[dict]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            linea = f.readline()
        if linea.startswith(HEADER_PREFIX):
            return json.loads(linea[len(HEADER_PREFIX):])
    except Exception:
        logger.warning("[Backup] No se pudo leer el encabezado de %s", path.name, exc_info=True)
    return None


def iter_statements(path: Path):
    """Las sentencias del respaldo, una por una. Cada INSERT ocupa una sola línea (los saltos de
    línea dentro de los textos van escapados) y un CREATE TABLE termina en una línea con ';'."""
    actual: List[str] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for linea in f:
            if not actual and (linea.startswith("--") or not linea.strip()):
                continue
            actual.append(linea)
            if linea.rstrip("\n").endswith(";"):
                yield "".join(actual).strip()
                actual = []
    if actual and "".join(actual).strip():
        yield "".join(actual).strip()


def restore_into(path: Path, connection) -> Dict[str, dict]:
    """Restaura el respaldo en la conexión pymysql dada (una base vacía) y compara cuántas filas
    quedaron con las del encabezado. Devuelve {tabla: {"esperado", "restaurado"}}."""
    header = read_header(path) or {"tables": {}}
    cur = connection.cursor()
    for sentencia in iter_statements(path):
        cur.execute(sentencia)
    connection.commit()
    resultado = {}
    for t, esperado in header["tables"].items():
        cur.execute(f"SELECT COUNT(*) FROM {_quote_ident(t)}")
        resultado[t] = {"esperado": esperado, "restaurado": int(cur.fetchone()[0])}
    return resultado


def list_backups() -> List[dict]:
    if not BACKUP_DIR.exists():
        return []
    filas = []
    for p in BACKUP_DIR.iterdir():
        if not NAME_RE.match(p.name):
            continue
        header = read_header(p) or {}
        tablas = header.get("tables", {})
        filas.append({
            "name": p.name, "size": p.stat().st_size,
            "created_at": header.get("created_at") or datetime.fromtimestamp(p.stat().st_mtime, PANAMA_TZ).isoformat(),
            "tables": len(tablas), "rows": sum(tablas.values()),
        })
    filas.sort(key=lambda r: r["name"], reverse=True)
    return filas


def prune(keep: int = KEEP_DAYS) -> List[str]:
    """Deja los `keep` respaldos más nuevos y borra los demás."""
    if not BACKUP_DIR.exists():
        return []
    archivos = sorted((p for p in BACKUP_DIR.iterdir() if NAME_RE.match(p.name)), key=lambda p: p.name, reverse=True)
    borrados = []
    for p in archivos[keep:]:
        try:
            p.unlink()
            borrados.append(p.name)
        except OSError:
            logger.warning("[Backup] No se pudo borrar %s", p.name, exc_info=True)
    return borrados


def backup_path(name: str) -> Optional[Path]:
    """La ruta de un respaldo por su nombre, validada (sin rutas raras)."""
    if not NAME_RE.match(name or ""):
        return None
    p = (BACKUP_DIR / name).resolve()
    if p.parent != BACKUP_DIR or not p.is_file():
        return None
    return p


def run_backup(manual: bool = False) -> dict:
    """Hace un respaldo ahora. El diario se llama farmhouse-AAAA-MM-DD.sql.gz; uno manual lleva
    además la hora, para no pisar el del día."""
    if not is_supported():
        raise RuntimeError("El respaldo solo funciona con MySQL.")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ahora = _ahora_panama()
    nombre = f"farmhouse-{ahora:%Y-%m-%d}" + (f"-{ahora:%H%M%S}" if manual else "") + ".sql.gz"
    path = BACKUP_DIR / nombre
    inicio = datetime.now()
    conteos = dump_to(path)
    prune()
    segundos = (datetime.now() - inicio).total_seconds()
    logger.info(f"[Backup] {nombre}: {len(conteos)} tablas, {sum(conteos.values())} filas, {path.stat().st_size} bytes en {segundos:.1f} s")
    return next((b for b in list_backups() if b["name"] == nombre), {"name": nombre})


def daily_due(now: Optional[datetime] = None) -> bool:
    now = now or _ahora_panama()
    if (now.hour, now.minute) < (RUN_HOUR, RUN_MINUTE):
        return False
    return not (BACKUP_DIR / f"farmhouse-{now:%Y-%m-%d}.sql.gz").exists()


async def run_backup_loop() -> None:
    if not is_supported():
        logger.info("[Backup] Base que no es MySQL: el respaldo diario queda apagado.")
        return
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            if daily_due():
                await asyncio.to_thread(run_backup)
        except Exception:
            logger.exception("[Backup] Falló el respaldo diario.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
