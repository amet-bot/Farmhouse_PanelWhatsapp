"""
Recetas cargadas en Farmhouse Link (las del dashboard del pasante) y lo necesario para usarlas:
emparejar sus ingredientes con el catálogo, enlazarlas con los platos que se venden en Invu,
precios de compra de referencia y food cost por plato. Invu siempre manda: estas recetas solo
llenan huecos (services/recipe_resolver.py).

Los datos se suben por /recipes/import (admin) desde un archivo: no viven en el repositorio,
que es público.
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.branch import Branch
from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSaleLine
from models.local_recipes import IngredientAlias, IngredientPrice, LocalRecipe, LocalRecipeLine, RecipeDishLink
from models.shipment import Shipment, ShipmentItem
from models.user import User
from security.permissions import require_permission
from services.audit import log_audit_event
from services.recipe_resolver import ORIGEN_NINGUNA, _unidad_receta, normalizar, reparar_acentos, resolver

logger = logging.getLogger("farmhouse.recipes")

router = APIRouter(prefix="/recipes", tags=["Recetas"])

AUTO_INGREDIENT_RATIO = 0.9    # parecido mínimo para emparejar un ingrediente solo
SOLD_DAYS_FOR_LINKS = 180
FOOD_COST_ALERT = Decimal("100")   # más que el precio de venta: casi seguro un error de carga


# --------------------------------------------------------------------------
# Esquemas
# --------------------------------------------------------------------------
class ImportLine(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    quantity: Decimal
    unit: Optional[str] = Field(None, max_length=20)


class ImportRecipe(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: Optional[str] = Field(None, max_length=80)
    sale_price: Optional[Decimal] = None
    ref_cost: Optional[Decimal] = None
    ref_food_cost_pct: Optional[Decimal] = None
    yield_weight_g: Optional[Decimal] = None
    yield_portions: Optional[Decimal] = None
    notes: Optional[str] = None
    lines: List[ImportLine] = []


class ImportPrice(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: Optional[str] = Field(None, max_length=80)
    brand: Optional[str] = Field(None, max_length=120)
    package_grams: Optional[Decimal] = None
    supplier: Optional[str] = Field(None, max_length=150)
    price: Decimal


class ImportPayload(BaseModel):
    source: str = Field("farmhouse_app", max_length=40)
    recipes: List[ImportRecipe] = Field([], max_length=2000)
    internas: List[ImportRecipe] = Field([], max_length=500)
    prices: List[ImportPrice] = Field([], max_length=10000)


class AliasUpdate(BaseModel):
    inventory_item_id: Optional[int] = None
    ignored: bool = False


class DishLinkIn(BaseModel):
    dish_name: str = Field(..., min_length=1, max_length=200)
    recipe_id: Optional[int] = None


# --------------------------------------------------------------------------
# Emparejamientos automáticos
# --------------------------------------------------------------------------
def _parecido(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    r = SequenceMatcher(None, a, b).ratio()
    if len(a) >= 5 and len(b) >= 5 and (a in b or b in a):
        r = max(r, 0.86)
    return r


def _sugerencias(norm: str, candidatos: dict, n: int = 3) -> list:
    """Los n candidatos más parecidos: [(ratio, objeto)]."""
    puntajes = sorted(((_parecido(norm, k), v) for k, v in candidatos.items()), key=lambda x: -x[0])
    return [(r, v) for r, v in puntajes[:n] if r >= 0.5]


def auto_emparejar_ingredientes(db: Session) -> int:
    """Crea un alias por cada nombre de ingrediente nuevo; si el catálogo tiene uno igual o muy
    parecido, lo empareja solo (marcado `auto` para poder revisarlo)."""
    catalogo = {normalizar(i.name): i for i in db.query(InventoryItem).filter(InventoryItem.active == True)}  # noqa: E712
    existentes = {a.name_norm for a in db.query(IngredientAlias.name_norm).all()}
    nombres = {}
    for nombre, norm in db.query(LocalRecipeLine.ingredient_name, LocalRecipeLine.ingredient_norm).distinct():
        nombres.setdefault(norm, nombre)
    emparejados = 0
    for norm, nombre in nombres.items():
        if norm in existentes:
            continue
        item = catalogo.get(norm)
        if not item:
            sug = _sugerencias(norm, catalogo, 1)
            if sug and sug[0][0] >= AUTO_INGREDIENT_RATIO:
                item = sug[0][1]
        db.add(IngredientAlias(name=nombre[:200], name_norm=norm, inventory_item_id=item.id if item else None, auto=bool(item)))
        emparejados += 1 if item else 0
    return emparejados


def _platos_vendidos(db: Session, days: int) -> dict:
    """{nombre normalizado: {"name", "sold"}} de lo vendido en todas las sucursales."""
    desde = date.today() - timedelta(days=days)
    out: dict = {}
    for nombre, cantidad in (
        db.query(InvuSaleLine.name, func.sum(InvuSaleLine.quantity))
        .filter(InvuSaleLine.business_date >= desde, InvuSaleLine.counted == True, InvuSaleLine.invu_item_id.isnot(None))  # noqa: E712
        .group_by(InvuSaleLine.name).all()
    ):
        norm = normalizar(nombre)
        if not norm:
            continue
        f = out.setdefault(norm, {"name": reparar_acentos(nombre), "sold": Decimal("0")})
        f["sold"] += Decimal(cantidad or 0)
    return out


# Palabras que la receta puede tener de más sin cambiar el plato ("Turkey melt sandwich").
PALABRAS_GENERICAS = {"sandwich", "sanduche", "bowl", "toast", "toastie", "wrap", "de", "el", "la"}


def _mismo_plato(plato: str, receta: str) -> bool:
    """Para enlazar SOLO (sin que nadie confirme) hace falta casi el mismo nombre, o que la
    receta sea el plato más palabras genéricas. "Matcha latte" no es "Latte" ni "Mango" es
    "Mango - agua de pipa": esos quedan como sugerencia."""
    if SequenceMatcher(None, plato, receta).ratio() >= 0.92:
        return True
    pp, pr = plato.split(), receta.split()
    if len(pp) >= 2 and pr[:len(pp)] == pp:
        return all(w in PALABRAS_GENERICAS for w in pr[len(pp):])
    return False


def auto_enlazar_platos(db: Session) -> int:
    """Enlaza los platos vendidos sin enlace con la receta cargada que es claramente el mismo
    plato ("Pesto chicken" → "Pesto chicken sandwich"). Los enlaces automáticos anteriores se
    recalculan; los que puso una persona no se tocan."""
    recetas = {r.name_norm: r for r in db.query(LocalRecipe).filter(LocalRecipe.kind == "plato", LocalRecipe.active == True)}  # noqa: E712
    db.query(RecipeDishLink).filter(RecipeDishLink.auto == True).delete(synchronize_session=False)  # noqa: E712
    if not recetas:
        return 0
    ya = {l.dish_norm for l in db.query(RecipeDishLink.dish_norm).all()}
    enlazados = 0
    for norm, info in _platos_vendidos(db, SOLD_DAYS_FOR_LINKS).items():
        if norm in ya or norm in recetas:
            continue   # igual nombre ya funciona sin enlace
        candidata = next((r for k, r in recetas.items() if _mismo_plato(norm, k)), None)
        if candidata:
            db.add(RecipeDishLink(dish_name=info["name"][:200], dish_norm=norm, recipe_id=candidata.id, auto=True))
            enlazados += 1
    return enlazados


# --------------------------------------------------------------------------
# Costos
# --------------------------------------------------------------------------
def _ultimo_costo_por_insumo(db: Session) -> dict:
    """Último costo de compra de cada insumo, en cualquier sucursal."""
    out: dict = {}
    for item_id, costo in (
        db.query(ShipmentItem.inventory_item_id, ShipmentItem.unit_cost)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .filter(ShipmentItem.unit_cost.isnot(None))
        .order_by(Shipment.received_at.desc(), ShipmentItem.id.desc()).all()
    ):
        out.setdefault(item_id, Decimal(costo))
    return out


def _precio_por_gramo(db: Session) -> dict:
    """El mejor precio por gramo (o ml) de cada ingrediente según los precios cargados."""
    out: dict = {}
    for p in db.query(IngredientPrice).filter(IngredientPrice.package_grams > 0).all():
        ppg = Decimal(p.price) / Decimal(p.package_grams)
        if p.ingredient_norm not in out or ppg < out[p.ingredient_norm]:
            out[p.ingredient_norm] = ppg
    return out


def _costear(db: Session, recetas: List[LocalRecipe]) -> dict:
    from routers.inventory import _a_unidad_del_insumo
    alias = {a.name_norm: a for a in db.query(IngredientAlias).options(joinedload(IngredientAlias.inventory_item)).all()}
    ultimos = _ultimo_costo_por_insumo(db)
    ppg = _precio_por_gramo(db)
    salida = {}
    for r in recetas:
        lineas, total, completo, emparejadas = [], Decimal("0"), True, 0
        for l in r.lines:
            a = alias.get(l.ingredient_norm)
            unidad = _unidad_receta(l.unit)
            estado = "ignorado" if a and a.ignored else ("emparejado" if a and a.inventory_item_id else "sin_emparejar")
            costo, fuente, sin_unidad = None, None, False
            item = a.inventory_item if a and a.inventory_item_id and not a.ignored else None
            if item:
                emparejadas += 1
                por_unidad = ultimos.get(item.id) or (Decimal(item.reference_cost) if item.reference_cost is not None else None)
                cantidad = _a_unidad_del_insumo(Decimal(l.quantity), unidad, item.unit, item.piece_size, item.grams_per_ml)
                sin_unidad = cantidad is None   # emparejado, pero no se descuenta hasta resolver la unidad
                if por_unidad is not None and cantidad is not None:
                    costo, fuente = (cantidad * por_unidad), ("compra" if item.id in ultimos else "invu")
            if costo is None and not (a and a.ignored) and unidad in ("g", "ml") and l.ingredient_norm in ppg:
                costo, fuente = Decimal(l.quantity) * ppg[l.ingredient_norm], "precio_cargado"
            if costo is None and not (a and a.ignored):
                completo = False
            if costo is not None:
                total += costo
            lineas.append({
                "name": l.ingredient_name, "quantity": l.quantity, "unit": unidad, "status": estado,
                "item": {"id": item.id, "name": item.name, "unit": item.unit} if item else None,
                "cost": costo.quantize(Decimal("0.0001")) if costo is not None else None, "cost_source": fuente,
                "unit_issue": sin_unidad,
            })
        precio = Decimal(r.sale_price) if r.sale_price else None
        fc = (total / precio * 100).quantize(Decimal("0.1")) if precio and total else None
        salida[r.id] = {
            "lines": lineas, "cost": total.quantize(Decimal("0.01")) if total else None, "cost_complete": completo,
            "food_cost_pct": fc, "mapped_lines": emparejadas, "total_lines": len(r.lines),
            "flag": "revisar" if (fc is not None and fc > FOOD_COST_ALERT) else ("incompleto" if not completo else None),
        }
    return salida


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.post("/import")
def import_recipes(
    data: ImportPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users.manage")),
):
    """Carga (o actualiza, por nombre) recetas de platos, preparaciones de la casa y precios de
    compra. Luego empareja ingredientes y enlaza platos vendidos por nombre donde puede."""
    contadores = {"recipes": 0, "internas": 0, "prices": 0}
    for kind, lista in (("plato", data.recipes), ("interna", data.internas)):
        for rec in lista:
            norm = normalizar(rec.name)
            if not norm:
                continue
            r = db.query(LocalRecipe).filter(LocalRecipe.kind == kind, LocalRecipe.name_norm == norm).first()
            if not r:
                r = LocalRecipe(kind=kind, name=rec.name, name_norm=norm)
                db.add(r)
            r.name, r.category, r.sale_price = rec.name, rec.category, rec.sale_price
            r.ref_cost, r.ref_food_cost_pct = rec.ref_cost, rec.ref_food_cost_pct
            r.yield_weight_g, r.yield_portions, r.notes = rec.yield_weight_g, rec.yield_portions, rec.notes
            r.source, r.active = data.source, True
            r.lines = [LocalRecipeLine(ingredient_name=l.name, ingredient_norm=normalizar(l.name), quantity=l.quantity, unit=(l.unit or None))
                       for l in rec.lines if normalizar(l.name) and l.quantity is not None and l.quantity > 0]
            contadores["recipes" if kind == "plato" else "internas"] += 1
    if data.prices:
        db.query(IngredientPrice).filter(IngredientPrice.source == data.source).delete(synchronize_session=False)
        for p in data.prices:
            if p.price is None or p.price <= 0:
                continue
            db.add(IngredientPrice(ingredient_name=p.name, ingredient_norm=normalizar(p.name), category=p.category, brand=p.brand,
                                   package_grams=p.package_grams, supplier=p.supplier, price=p.price, source=data.source))
            contadores["prices"] += 1
    db.flush()
    contadores["ingredients_auto"] = auto_emparejar_ingredientes(db)
    db.flush()
    contadores["dishes_auto"] = auto_enlazar_platos(db)
    log_audit_event(db, current_user.id, None, "recipes.import", "local_recipe", None, contadores)
    db.commit()
    logger.info(f"[Recetas] Importación de {data.source}: {contadores}")
    return contadores


@router.get("")
def list_recipes(
    kind: str = Query("plato", pattern="^(plato|interna)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports.view")),
):
    recetas = (
        db.query(LocalRecipe).options(joinedload(LocalRecipe.lines))
        .filter(LocalRecipe.kind == kind, LocalRecipe.active == True).order_by(LocalRecipe.category, LocalRecipe.name).all()  # noqa: E712
    )
    costos = _costear(db, recetas)
    enlaces = defaultdict(list)
    for l in db.query(RecipeDishLink).filter(RecipeDishLink.recipe_id.isnot(None)).all():
        enlaces[l.recipe_id].append(l.dish_name)
    filas = []
    for r in recetas:
        c = costos[r.id]
        fila = {
            "id": r.id, "name": r.name, "category": r.category, "sale_price": r.sale_price,
            "ref_cost": r.ref_cost, "ref_food_cost_pct": r.ref_food_cost_pct, "dishes": sorted(enlaces.get(r.id, [])), **c,
        }
        if kind == "interna":
            peso = Decimal(r.yield_weight_g) if r.yield_weight_g else None
            costo_lote = c["cost"] if c["cost"] else (Decimal(r.ref_cost) if r.ref_cost is not None else None)
            fila.update({
                "yield_weight_g": r.yield_weight_g, "yield_portions": r.yield_portions, "notes": r.notes,
                "batch_cost": costo_lote,
                "cost_per_g": (costo_lote / peso).quantize(Decimal("0.0001")) if costo_lote and peso else None,
                "portion_g": (peso / Decimal(r.yield_portions)).quantize(Decimal("0.1")) if peso and r.yield_portions else None,
            })
        filas.append(fila)
    con_fc = [f for f in filas if f.get("food_cost_pct") is not None and f["food_cost_pct"] <= FOOD_COST_ALERT]
    return {
        "recipes": filas,
        "summary": {
            "total": len(filas),
            "usable": sum(1 for f in filas if f["mapped_lines"] > 0),
            "fully_mapped": sum(1 for f in filas if f["total_lines"] and f["mapped_lines"] == f["total_lines"]),
            "avg_food_cost_pct": (sum(f["food_cost_pct"] for f in con_fc) / len(con_fc)).quantize(Decimal("0.1")) if con_fc else None,
            "to_review": sum(1 for f in filas if f["flag"] == "revisar"),
        },
    }


@router.get("/ingredients")
def list_ingredients(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports.view")),
):
    """Los ingredientes de las recetas cargadas y con qué insumo del catálogo están emparejados;
    los que faltan primero, por cuántas recetas los usan."""
    usos = dict(db.query(LocalRecipeLine.ingredient_norm, func.count(func.distinct(LocalRecipeLine.recipe_id))).group_by(LocalRecipeLine.ingredient_norm).all())
    catalogo = {normalizar(i.name): i for i in db.query(InventoryItem).filter(InventoryItem.active == True)}  # noqa: E712
    filas = []
    for a in db.query(IngredientAlias).options(joinedload(IngredientAlias.inventory_item)).all():
        if a.name_norm not in usos:
            continue
        pendiente = not a.ignored and not a.inventory_item_id
        filas.append({
            "id": a.id, "name": a.name, "recipes": usos[a.name_norm], "ignored": a.ignored, "auto": a.auto,
            "item": {"id": a.inventory_item.id, "name": a.inventory_item.name, "unit": a.inventory_item.unit} if a.inventory_item else None,
            "status": "ignorado" if a.ignored else ("emparejado" if a.inventory_item_id else "pendiente"),
            "suggestions": [{"id": it.id, "name": it.name, "unit": it.unit, "score": round(r, 2)} for r, it in _sugerencias(a.name_norm, catalogo, 3)] if pendiente else [],
        })
    orden = {"pendiente": 0, "emparejado": 1, "ignorado": 2}
    filas.sort(key=lambda f: (orden[f["status"]], -f["recipes"], f["name"].lower()))
    return {
        "ingredients": filas,
        "pending": sum(1 for f in filas if f["status"] == "pendiente"),
        "mapped": sum(1 for f in filas if f["status"] == "emparejado"),
        "ignored": sum(1 for f in filas if f["status"] == "ignorado"),
    }


@router.put("/ingredients/{alias_id}")
def update_ingredient(
    alias_id: int,
    data: AliasUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    a = db.query(IngredientAlias).filter(IngredientAlias.id == alias_id).first()
    if not a:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingrediente no encontrado.")
    if data.inventory_item_id is not None and not db.query(InventoryItem.id).filter(InventoryItem.id == data.inventory_item_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insumo no encontrado.")
    a.ignored = bool(data.ignored)
    a.inventory_item_id = None if data.ignored else data.inventory_item_id
    a.auto = False
    a.updated_by_user_id = current_user.id
    log_audit_event(db, current_user.id, None, "recipes.ingredient_map", "ingredient_alias", a.id,
                    {"name": a.name, "inventory_item_id": a.inventory_item_id, "ignored": a.ignored})
    db.commit()
    return {"id": a.id, "status": "ignorado" if a.ignored else ("emparejado" if a.inventory_item_id else "pendiente")}


def _conversion(unidad_receta: str, unidad_insumo: str) -> dict:
    """Qué dato hace falta para pasar la unidad de la receta a la del insumo: "densidad"
    (g ↔ ml), "pieza" (unidad ↔ g/ml), "desconocida" (la receta dice "taza", "cda"...) o
    None si son de la misma familia. Con las familias, para que la pantalla arme la pregunta."""
    from routers.inventory import _UNIT_FAMILY, _familia_de_unidad
    fr = _UNIT_FAMILY.get((unidad_receta or "").strip().lower())
    fi = _familia_de_unidad(unidad_insumo)[0]
    if not fr:
        return {"kind": "desconocida", "recipe_family": None, "item_family": fi}
    kind = None if fr[0] == fi else ("densidad" if {fr[0], fi} == {"peso", "volumen"} else "pieza")
    return {"kind": kind, "recipe_family": fr[0], "item_family": fi}


@router.get("/unit-issues")
def list_unit_issues(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports.view")),
):
    """
    Insumos que una receta pide en otra unidad (la receta en gramos y el insumo en ml, o la
    receta por unidad y el insumo en gramos...) y que hoy NO se descuentan porque falta el dato
    para convertir: cuánto pesa 1 ml o cuánto es 1 pieza. Mira las recetas de Invu de todas las
    sucursales y las cargadas en Farmhouse Link. También devuelve los ya resueltos, para revisarlos.
    """
    from routers.inventory import _a_unidad_del_insumo

    por_invu = {i.invu_id: i for i in db.query(InventoryItem).filter(InventoryItem.invu_id.isnot(None))}
    usos: dict = {}   # item.id -> {"item", "units": {unidad: set(platos)}}

    def anotar(item, unidad, plato):
        unidad = (unidad or "").strip().lower()
        if not item or not unidad or unidad == (item.unit or "").strip().lower():
            return
        f = usos.setdefault(item.id, {"item": item, "units": {}})
        f["units"].setdefault(unidad, set()).add(reparar_acentos(plato or "").strip() or "—")

    for pid, unidad, plato in db.query(InvuRecipeLine.product_invu_id, InvuRecipeLine.unit_name, InvuRecipeLine.source_name).distinct():
        anotar(por_invu.get(pid), unidad, plato)
    alias = {a.name_norm: a for a in db.query(IngredientAlias).options(joinedload(IngredientAlias.inventory_item))
             .filter(IngredientAlias.inventory_item_id.isnot(None), IngredientAlias.ignored == False)}  # noqa: E712
    for norm, unidad, plato in (
        db.query(LocalRecipeLine.ingredient_norm, LocalRecipeLine.unit, LocalRecipe.name)
        .join(LocalRecipe, LocalRecipe.id == LocalRecipeLine.recipe_id)
        .filter(LocalRecipe.kind == "plato", LocalRecipe.active == True).distinct()  # noqa: E712
    ):
        a = alias.get(norm)
        if a:
            anotar(a.inventory_item, _unidad_receta(unidad), plato)

    pendientes, resueltos = [], []
    for f in usos.values():
        it = f["item"]
        cruces = []
        for u, ps in sorted(f["units"].items()):
            c = _conversion(u, it.unit)
            if c["kind"] is None:
                continue   # misma familia (g y kg): siempre se convierte
            ok = _a_unidad_del_insumo(Decimal("1"), u, it.unit, it.piece_size, it.grams_per_ml) is not None
            cruces.append({"recipe_unit": u, **c, "ok": ok, "dishes_count": len(ps)})
        if not cruces:
            continue
        platos = sorted({p for ps in f["units"].values() for p in ps})
        fila = {
            "item": {"id": it.id, "name": it.name, "unit": it.unit, "piece_size": it.piece_size, "grams_per_ml": it.grams_per_ml},
            "dishes": platos[:6], "dishes_count": len(platos), "conversions": cruces,
            "problems": [{"recipe_unit": c["recipe_unit"], "kind": c["kind"], "dishes_count": c["dishes_count"]} for c in cruces if not c["ok"]],
        }
        (pendientes if fila["problems"] else resueltos).append(fila)
    pendientes.sort(key=lambda f: (-f["dishes_count"], f["item"]["name"].lower()))
    resueltos.sort(key=lambda f: f["item"]["name"].lower())
    return {
        "pending": pendientes, "resolved": resueltos,
        "fixable": sum(1 for f in pendientes if any(p["kind"] != "desconocida" for p in f["problems"])),
    }


@router.get("/dishes")
def list_dishes(
    days: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports.view")),
):
    """Platos vendidos (todas las sucursales) que no tienen receta en Invu, con la receta cargada
    que usan (enlazada o por nombre) o sugerencias para enlazarlos."""
    vendidos = _platos_vendidos(db, days)
    recetas = {r.name_norm: r for r in db.query(LocalRecipe).filter(LocalRecipe.kind == "plato", LocalRecipe.active == True)}  # noqa: E712
    enlaces = {l.dish_norm: l for l in db.query(RecipeDishLink).options(joinedload(RecipeDishLink.recipe)).all()}
    # Qué platos tienen receta en Invu (en alguna sucursal): esos no necesitan nada.
    con_invu = set()
    for b in db.query(Branch).filter(Branch.active == True, Branch.code != "CAT"):  # noqa: E712
        _r, _i, origen = resolver(db, b.id)
        for (tipo, _sid), o in origen.items():
            if tipo == "item" and o["source"] in ("invu", "misma_sucursal") and o.get("name"):
                con_invu.add(normalizar(o["name"]))
    filas = []
    for norm, info in vendidos.items():
        if norm in con_invu:
            continue
        link = enlaces.get(norm)
        receta = link.recipe if link and link.recipe else recetas.get(norm)
        filas.append({
            "dish_name": info["name"], "sold": info["sold"].quantize(Decimal("0.01")),
            "recipe": {"id": receta.id, "name": receta.name} if receta else None,
            "linked": bool(link and link.recipe_id), "auto": bool(link and link.auto),
            "suggestions": [] if receta else [{"id": r.id, "name": r.name, "score": round(s, 2)} for s, r in _sugerencias(norm, recetas, 3)],
        })
    filas.sort(key=lambda f: (f["recipe"] is not None, -f["sold"]))
    return {"dishes": filas, "days": days,
            "without_recipe": sum(1 for f in filas if not f["recipe"]),
            "with_loaded_recipe": sum(1 for f in filas if f["recipe"])}


@router.put("/dishes")
def link_dish(
    data: DishLinkIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    norm = normalizar(data.dish_name)
    if not norm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Falta el nombre del plato.")
    if data.recipe_id is not None and not db.query(LocalRecipe.id).filter(LocalRecipe.id == data.recipe_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receta no encontrada.")
    link = db.query(RecipeDishLink).filter(RecipeDishLink.dish_norm == norm).first()
    if data.recipe_id is None:
        if link:
            db.delete(link)
    else:
        if not link:
            link = RecipeDishLink(dish_name=data.dish_name[:200], dish_norm=norm)
            db.add(link)
        link.recipe_id, link.auto, link.updated_by_user_id = data.recipe_id, False, current_user.id
    log_audit_event(db, current_user.id, None, "recipes.dish_link", "recipe_dish_link", None, {"dish": data.dish_name, "recipe_id": data.recipe_id})
    db.commit()
    return {"dish_name": data.dish_name, "recipe_id": data.recipe_id}


@router.get("/prices")
def list_prices(
    q: str = Query("", max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports.view")),
):
    """Precios de compra cargados por ingrediente y proveedor, con el precio por kg y el mejor."""
    query = db.query(IngredientPrice)
    if q.strip():
        query = query.filter(IngredientPrice.ingredient_norm.like(f"%{normalizar(q)}%"))
    grupos: dict = {}
    for p in query.order_by(IngredientPrice.ingredient_name).all():
        g = grupos.setdefault(p.ingredient_norm, {"name": p.ingredient_name, "category": p.category, "offers": []})
        por_kg = (Decimal(p.price) / Decimal(p.package_grams) * 1000).quantize(Decimal("0.01")) if p.package_grams else None
        g["offers"].append({"supplier": p.supplier or "—", "brand": p.brand, "package_grams": p.package_grams, "price": p.price, "price_per_kg": por_kg})
    filas = []
    for g in grupos.values():
        con_kg = [o for o in g["offers"] if o["price_per_kg"]]
        mejor = min(con_kg, key=lambda o: o["price_per_kg"]) if con_kg else None
        for o in g["offers"]:
            o["best"] = bool(mejor and o is mejor and len(con_kg) > 1)
        filas.append({**g, "suppliers": len(g["offers"]), "best_price_per_kg": mejor["price_per_kg"] if mejor else None})
    filas.sort(key=lambda f: (-f["suppliers"], f["name"].lower()))
    return {"ingredients": filas, "total": len(filas)}
