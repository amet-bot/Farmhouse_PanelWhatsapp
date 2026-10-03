"""
Recetas efectivas de una sucursal: las de Invu, y donde Invu no tiene, se llena el hueco.

Orden de prioridad para cada plato o modificador que se vende en la sucursal:
  1. "invu": su receta en Invu, en ESA sucursal (siempre manda).
  2. "misma_sucursal": otra versión del mismo plato (mismo nombre, otro id de Invu: salón y
     delivery) tiene receta en esta sucursal.
  3. "cargada": receta cargada en Farmhouse Link (dashboard del pasante), enlazada a ese plato
     o con el mismo nombre, con al menos un ingrediente emparejado con el catálogo.
  4. "otra_sucursal": el mismo plato (mismo nombre) tiene receta en otra sucursal. El menú es
     el mismo en todas; si Vía Porras vende "La Lupita" sin receta y Clayton la tiene, se usa la
     de Clayton (la de la sucursal con la receta más completa).
  5. "reventa": un producto que se vende tal cual (Coca Cola Zero, agua embotellada) y existe
     como insumo con el MISMO nombre medido por unidad: venderlo descuenta 1 unidad.
  6. "sin_receta": no se puede estimar su uso.

Nada de esto escribe en la base ni toca Invu: se calcula al pedirlo.
"""
import html
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from models.branch import Branch
from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSaleLine, InvuSaleModifier

SOLD_LOOKBACK_DAYS = 180   # platos vendidos que se consideran para llenar huecos

ORIGEN_INVU = "invu"
ORIGEN_OTRA = "otra_sucursal"
ORIGEN_GEMELA = "misma_sucursal"   # otra versión del plato en la misma sucursal (salón / delivery)
ORIGEN_CARGADA = "cargada"         # receta cargada en Farmhouse Link (las del pasante, models/local_recipes)
ORIGEN_REVENTA = "reventa"
ORIGEN_NINGUNA = "sin_receta"


def normalizar(nombre: Optional[str]) -> str:
    """'Galleta De Avena &amp Choco' y 'galleta de avena & choco' son el mismo plato."""
    t = html.unescape(nombre or "").replace("&amp", "&")
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    t = re.sub(r"[^a-z0-9&]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _vendidos(db, branch_id: int, desde) -> Tuple[Dict[int, str], Dict[int, str]]:
    """{invu_item_id: nombre} de platos y {invu_modifier_id: nombre} de modificadores vendidos."""
    platos = dict(
        db.query(InvuSaleLine.invu_item_id, func.max(InvuSaleLine.name))
        .filter(InvuSaleLine.branch_id == branch_id, InvuSaleLine.business_date >= desde, InvuSaleLine.invu_item_id.isnot(None))
        .group_by(InvuSaleLine.invu_item_id).all()
    )
    mods = dict(
        db.query(InvuSaleModifier.invu_modifier_id, func.max(InvuSaleModifier.name))
        .join(InvuSaleLine, InvuSaleLine.id == InvuSaleModifier.line_id)
        .filter(InvuSaleLine.branch_id == branch_id, InvuSaleLine.business_date >= desde, InvuSaleModifier.invu_modifier_id.isnot(None))
        .group_by(InvuSaleModifier.invu_modifier_id).all()
    )
    return platos, mods


def _donantes(db, branch_id: int) -> Dict[Tuple[str, str], Tuple[str, List[InvuRecipeLine]]]:
    """Recetas por (tipo, nombre normalizado) para prestar. Primero la de la MISMA sucursal (un
    plato suele estar dos veces en Invu, para salón y para delivery, y a veces solo una versión
    tiene receta); si no, la de la sucursal que tenga más ingredientes para ese plato."""
    grupos: Dict[Tuple[str, str, int, int], List[InvuRecipeLine]] = {}
    for linea in db.query(InvuRecipeLine).filter(InvuRecipeLine.source_name.isnot(None)).all():
        clave = normalizar(linea.source_name)
        if not clave:
            continue
        grupos.setdefault((linea.source_type, clave, linea.branch_id, linea.source_invu_id), []).append(linea)
    nombres = {b.id: b.name for b in db.query(Branch.id, Branch.name).all()}
    mejor: Dict[Tuple[str, str], Tuple[str, List[InvuRecipeLine]]] = {}
    rango: Dict[Tuple[str, str], tuple] = {}
    for (tipo, clave, b_id, _sid), lineas in grupos.items():
        puntaje = (b_id == branch_id, len(lineas))   # la misma sucursal gana; si no, la más completa
        if (tipo, clave) not in rango or puntaje > rango[(tipo, clave)]:
            rango[(tipo, clave)] = puntaje
            mejor[(tipo, clave)] = (nombres.get(b_id, f"Sucursal {b_id}") if b_id != branch_id else None, lineas)
    return mejor


def resolver(db, branch_id: int, hoy=None) -> Tuple[dict, dict, dict]:
    """
    Devuelve (recetas, insumos, origen):
      recetas: {(tipo, id de Invu): [líneas]} — líneas de InvuRecipeLine o equivalentes
      insumos: {invu_id: InventoryItem}
      origen:  {(tipo, id de Invu): {"source": ..., "from_branch": nombre o None, "name": ...}}
    """
    from routers.inventory import _familia_de_unidad   # diferido: routers importa services
    from services.invu_sales_sync import hoy_panama

    recetas: dict = {}
    origen: dict = {}
    for linea in db.query(InvuRecipeLine).filter(InvuRecipeLine.branch_id == branch_id).all():
        recetas.setdefault((linea.source_type, linea.source_invu_id), []).append(linea)
    for clave, lineas in recetas.items():
        origen[clave] = {"source": ORIGEN_INVU, "from_branch": None, "name": lineas[0].source_name}
    insumos = {i.invu_id: i for i in db.query(InventoryItem).filter(InventoryItem.invu_id.isnot(None))}

    desde = (hoy or hoy_panama()) - timedelta(days=SOLD_LOOKBACK_DAYS)
    platos, mods = _vendidos(db, branch_id, desde)
    faltan = [("item", i, n) for i, n in platos.items() if ("item", i) not in recetas]
    faltan += [("modifier", i, n) for i, n in mods.items() if ("modifier", i) not in recetas]
    if not faltan:
        return recetas, insumos, origen

    donantes = _donantes(db, branch_id)
    cargadas = recetas_cargadas(db, insumos)
    por_nombre = {}
    for it in insumos.values():
        if it.active and it.invu_id is not None and _familia_de_unidad(it.unit)[0] == "unidad":
            por_nombre.setdefault(normalizar(it.name), it)

    for tipo, sid, nombre in faltan:
        clave = normalizar(nombre)
        donante = donantes.get((tipo, clave))
        if donante and donante[0] is None:   # otra versión del mismo plato en esta sucursal
            recetas[(tipo, sid)] = donante[1]
            origen[(tipo, sid)] = {"source": ORIGEN_GEMELA, "from_branch": None, "name": nombre}
            continue
        cargada = cargadas.get(clave) if tipo == "item" else None
        if cargada:
            recetas[(tipo, sid)] = cargada["lines"]
            origen[(tipo, sid)] = {"source": ORIGEN_CARGADA, "from_branch": None, "name": nombre, "recipe": cargada["name"]}
            continue
        if donante:
            recetas[(tipo, sid)] = donante[1]
            if donante[0] is None:   # otra versión del mismo plato en esta sucursal
                origen[(tipo, sid)] = {"source": ORIGEN_GEMELA, "from_branch": None, "name": nombre}
            else:
                origen[(tipo, sid)] = {"source": ORIGEN_OTRA, "from_branch": donante[0], "name": nombre}
            continue
        reventa = por_nombre.get(clave) if tipo == "item" else None
        if reventa:
            recetas[(tipo, sid)] = [SimpleNamespace(
                source_type=tipo, source_invu_id=sid, source_name=nombre,
                product_invu_id=reventa.invu_id, product_name=reventa.name,
                quantity=Decimal("1"), unit_name=reventa.unit,
            )]
            origen[(tipo, sid)] = {"source": ORIGEN_REVENTA, "from_branch": None, "name": nombre}
    return recetas, insumos, origen


def _unidad_receta(u: Optional[str]) -> str:
    u = (u or "").strip().lower()
    return {"ud": "unidad", "uds": "unidad", "u": "unidad", "und": "unidad", "unidades": "unidad", "gr": "g", "grs": "g", "mililitros": "ml"}.get(u, u or "unidad")


def recetas_cargadas(db, insumos: dict) -> Dict[str, dict]:
    """
    Recetas cargadas (kind="plato") listas para usar, por nombre de plato normalizado: el de la
    receta y el de cada plato vendido enlazado a ella (RecipeDishLink). Cada ingrediente
    emparejado se vuelve una línea como las de Invu. Agrega a `insumos` los del catálogo que no
    vienen de Invu (con una clave negativa), para que el cálculo de uso los encuentre.
    Una receta sin ningún ingrediente emparejado no se usa.
    """
    from models.local_recipes import IngredientAlias, LocalRecipe, RecipeDishLink

    alias = {a.name_norm: a for a in db.query(IngredientAlias).all()}
    por_id = {}
    salida: Dict[str, dict] = {}
    for r in db.query(LocalRecipe).filter(LocalRecipe.kind == "plato", LocalRecipe.active == True).all():  # noqa: E712
        lineas = []
        for l in r.lines:
            a = alias.get(l.ingredient_norm)
            if not a or a.ignored or not a.inventory_item:
                continue
            it = a.inventory_item
            clave = it.invu_id if it.invu_id is not None else -it.id
            insumos.setdefault(clave, it)
            lineas.append(SimpleNamespace(
                source_type="item", source_invu_id=None, source_name=r.name,
                product_invu_id=clave, product_name=it.name,
                quantity=Decimal(l.quantity), unit_name=_unidad_receta(l.unit),
            ))
        if not lineas:
            continue
        info = {"name": r.name, "lines": lineas, "recipe_id": r.id}
        por_id[r.id] = info
        salida.setdefault(r.name_norm, info)
    for link in db.query(RecipeDishLink).filter(RecipeDishLink.recipe_id.isnot(None)).all():
        if link.recipe_id in por_id:
            salida[link.dish_norm] = por_id[link.recipe_id]
    return salida
