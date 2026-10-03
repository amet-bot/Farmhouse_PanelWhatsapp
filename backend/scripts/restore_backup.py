"""
Restaura un respaldo de Farmhouse Link (farmhouse-AAAA-MM-DD.sql.gz) en una base MySQL VACÍA y
compara cuántas filas quedaron en cada tabla con las que tenía el respaldo.

    python scripts/restore_backup.py farmhouse-2026-10-03.sql.gz "mysql://usuario:clave@host:3306/base_vacia"

No lo corras contra la base de producción salvo que de verdad quieras reemplazarla: borra y
vuelve a crear cada tabla del respaldo.
"""
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402

from services.db_backup import restore_into  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    archivo, url = Path(sys.argv[1]), urlparse(sys.argv[2].replace("mysql+pymysql://", "mysql://"))
    conn = pymysql.connect(host=url.hostname, port=url.port or 3306, user=unquote(url.username or ""),
                           password=unquote(url.password or ""), database=url.path.lstrip("/"), charset="utf8mb4")
    try:
        resultado = restore_into(archivo, conn)
    finally:
        conn.close()
    mal = {t: r for t, r in resultado.items() if r["esperado"] != r["restaurado"]}
    print(f"{len(resultado)} tablas restauradas, {sum(r['restaurado'] for r in resultado.values())} filas.")
    if mal:
        print("NO CUADRAN:", mal)
        return 1
    print("Todas las tablas cuadran con el respaldo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
