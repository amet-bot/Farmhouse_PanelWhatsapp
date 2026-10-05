"""
Carga el costo real de los insumos desde el Excel de costeo de recetas (seeds/costeo_ingredientes.json)
a `inventory_items.costing_cost`. Por defecto es una PRUEBA: muestra qué cambiaría y no escribe nada.

    python scripts/load_costeo.py                 # prueba (no escribe)
    python scripts/load_costeo.py --apply         # guarda costing_cost
    python scripts/load_costeo.py --apply --piece-sizes   # además llena el peso de la pieza (solo empaques, solo donde está vacío)

Empareja por código de Invu y, si no hay, por nombre exacto. El costo va a una columna propia (no a
`reference_cost`, que la sincronización con Invu reescribe) y manda sobre el de Invu al valuar
mermas y conteos; el costo de cada compra, cuando hay cargamento, manda sobre ambos.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from database import SessionLocal  # noqa: E402
from models.inventory_item import InventoryItem  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "seeds" / "costeo_ingredientes.json"


def costo_por_unidad(fila: dict, unidad: str):
    """Costo por unidad de inventario, o (None, motivo) si no se puede calcular con seguridad."""
    precio, gramos = fila.get("pack_price"), fila.get("pack_grams")
    if not precio:
        return None, "la hoja no trae precio"
    if unidad == "gramos":
        if not gramos:
            return None, "la hoja no trae gramaje"
        return Decimal(str(precio)) / Decimal(str(gramos)), None
    if unidad == "unidad":
        return Decimal(str(precio)), None
    return None, f"unidad '{unidad}' no soportada"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="guarda los cambios (sin esto solo muestra)")
    ap.add_argument("--piece-sizes", action="store_true", help="llena piece_size vacío en empaques")
    args = ap.parse_args()

    datos = json.loads(DATA.read_text(encoding="utf-8"))
    fuente = "Excel costeo " + datetime.now().strftime("%Y-%m-%d")
    db = SessionLocal()
    por_codigo = {(i.code or "").upper(): i for i in db.query(InventoryItem).all() if i.code}
    por_nombre = {i.name.strip().lower(): i for i in db.query(InventoryItem).all()}
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)

    cambios = sin_pareja = saltados = piezas = 0
    for fila in datos["items"]:
        item = por_codigo.get(fila["code"]) or por_nombre.get(fila["name"].strip().lower())
        if not item:
            print(f"SIN PAREJA   {fila['code']:6} {fila['name']}")
            sin_pareja += 1
            continue
        if fila.get("excluded"):
            print(f"EXCLUIDO     {item.code:6} {item.name[:28]:28} {fila['excluded']}")
            saltados += 1
            continue
        nuevo, motivo = costo_por_unidad(fila, item.unit)
        if nuevo is None:
            print(f"SALTADO      {item.code:6} {item.name[:28]:28} {motivo}")
            saltados += 1
            continue
        nuevo = nuevo.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        antes = item.effective_cost
        if item.costing_cost is None or Decimal(item.costing_cost) != nuevo:
            marca = f"{Decimal(antes):.4f}" if antes is not None else "  —   "
            print(f"COSTO        {item.code:6} {item.name[:28]:28} {marca} -> {nuevo}")
            cambios += 1
            if args.apply:
                item.costing_cost, item.costing_source, item.costing_updated_at = nuevo, fuente, ahora
        if args.piece_sizes and fila.get("piece_candidate") and item.piece_size is None and fila.get("pack_grams"):
            print(f"PIEZA        {item.code:6} {item.name[:28]:28} {fila['pack_grams']:g} g")
            piezas += 1
            if args.apply:
                item.piece_size = Decimal(str(fila["pack_grams"]))
    if args.apply:
        db.commit()
    print(f"\n{'APLICADO' if args.apply else 'PRUEBA (no se escribió nada)'}: {cambios} costos, {piezas} pesos de pieza, "
          f"{saltados} saltados, {sin_pareja} sin pareja.")


if __name__ == "__main__":
    main()
