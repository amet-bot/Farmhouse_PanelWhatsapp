"""
Sincroniza database/farmhouse_catalog_meta.csv con un Catálogo de Meta Commerce
Manager usando la Graph API (POST /{catalog_id}/items_batch).

Requiere en backend/.env (o variables de entorno):
  META_CATALOG_ID       -> ID del catálogo en Commerce Manager (Business Settings > Catálogos).
  META_WA_ACCESS_TOKEN  -> Token de Usuario del Sistema con el permiso 'catalog_management'.

Uso:
  python scripts/sync_meta_catalog.py                 # sincroniza todo el catálogo
  python scripts/sync_meta_catalog.py --dry-run        # muestra qué se enviaría, sin llamar a la API
  python scripts/sync_meta_catalog.py --limit 5        # solo los primeros N productos (pruebas)
  python scripts/sync_meta_catalog.py --batch-size 25  # tamaño de lote (por defecto 50)
"""
import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from config import settings  # noqa: E402

CSV_PATH = PROJECT_ROOT / "database" / "farmhouse_catalog_meta.csv"
GRAPH_API_VERSION = "v21.0"

# Campos del CSV que Meta acepta tal cual en items_batch (mismo formato que un feed de productos).
ALLOWED_FIELDS = [
    "id", "title", "description", "availability", "condition", "price",
    "link", "image_link", "brand", "item_group_id",
]


def load_csv_rows() -> List[Dict[str, str]]:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"No se encontró el catálogo en {CSV_PATH}")
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{CSV_PATH} no tiene productos (solo encabezado o está vacío).")
    return rows


def row_to_item_data(row: Dict[str, str]) -> Dict[str, Any]:
    if not row.get("id", "").strip():
        raise ValueError(f"Fila sin 'id' (SKU): {row}")
    return {field: row[field].strip() for field in ALLOWED_FIELDS if row.get(field, "").strip()}


def chunked(items: List[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def sync(dry_run: bool = False, limit: int = None, batch_size: int = 50) -> None:
    rows = load_csv_rows()
    if limit:
        rows = rows[:limit]

    print(f"[sync_meta_catalog] {len(rows)} producto(s) leídos de {CSV_PATH.relative_to(PROJECT_ROOT)}")

    requests_payload = [
        {"method": "UPDATE", "data": row_to_item_data(row)}
        for row in rows
    ]

    if dry_run:
        print("[sync_meta_catalog] --dry-run: no se llamó a la Graph API. Ejemplo del primer producto:")
        print(requests_payload[0] if requests_payload else "(sin productos)")
        print(f"[sync_meta_catalog] Se enviarían {len(requests_payload)} producto(s) en "
              f"{(len(requests_payload) + batch_size - 1) // batch_size} lote(s) de hasta {batch_size}.")
        return

    catalog_id = str(settings.META_CATALOG_ID or "").strip()
    token = str(settings.META_WA_ACCESS_TOKEN or "").strip()
    if not catalog_id:
        raise SystemExit(
            "ERROR: META_CATALOG_ID no está configurado en backend/.env. "
            "Búscalo en Business Settings > Catálogos de Meta Commerce Manager."
        )
    if not token:
        raise SystemExit("ERROR: META_WA_ACCESS_TOKEN no está configurado en backend/.env.")

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{catalog_id}/items_batch"
    total_ok, total_err = 0, 0

    with httpx.Client(timeout=30.0) as client:
        for batch_num, batch in enumerate(chunked(requests_payload, batch_size), start=1):
            resp = client.post(url, data={
                "access_token": token,
                "item_type": "PRODUCT_ITEM",
                "requests": _to_json(batch),
            })
            if resp.status_code == 200:
                body = resp.json()
                handle_id = body.get("handles", [None])[0]
                print(f"[sync_meta_catalog] Lote {batch_num} ({len(batch)} productos) enviado. handle={handle_id}")
                total_ok += len(batch)
            else:
                print(f"[sync_meta_catalog] ERROR en lote {batch_num}: HTTP {resp.status_code} -> {resp.text}")
                total_err += len(batch)

    print(f"[sync_meta_catalog] Terminado. Enviados: {total_ok}. Con error: {total_err}.")
    if total_err:
        sys.exit(1)


def _to_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincroniza el catálogo Farmhouse con Meta Commerce Manager.")
    parser.add_argument("--dry-run", action="store_true", help="No llama a la API, solo muestra el payload.")
    parser.add_argument("--limit", type=int, default=None, help="Sincronizar solo los primeros N productos.")
    parser.add_argument("--batch-size", type=int, default=50, help="Productos por lote (máx. recomendado por Meta: 5000, aquí por defecto 50).")
    args = parser.parse_args()

    sync(dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size)
