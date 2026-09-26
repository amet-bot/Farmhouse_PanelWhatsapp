from pydantic import BaseModel, Field, ConfigDict
from decimal import Decimal
from typing import Optional, List
from datetime import datetime


class InventoryItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    unit: str = Field(..., min_length=1, max_length=30)
    category: Optional[str] = Field(None, max_length=50)


class InventoryItemResponse(BaseModel):
    id: int
    name: str
    unit: str
    category: Optional[str] = None
    active: bool
    # Lo que viene de Invu (ver models/inventory_item.py). `invu_id` presente = se sincroniza.
    invu_id: Optional[int] = None
    code: Optional[str] = None
    kind: Optional[str] = None                 # "materia_prima" | "casa"
    reference_cost: Optional[Decimal] = None   # costo de Invu, solo referencia
    synced_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SupplierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    phone: Optional[str] = Field(None, max_length=30)


class SupplierResponse(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    active: bool
    # Lo que viene de Invu. `invu_id` presente significa "este proveedor se sincroniza": la
    # pantalla lo usa para mostrarlo como de solo lectura y marcar su origen.
    invu_id: Optional[int] = None
    code: Optional[str] = None
    tax_id: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    delivery_day: Optional[int] = None
    synced_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ShipmentItemCreate(BaseModel):
    inventory_item_id: int
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)


class ShipmentItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class ShipmentCreate(BaseModel):
    branch_id: int
    received_at: Optional[datetime] = None
    supplier_id: Optional[int] = None
    notes: Optional[str] = None
    items: List[ShipmentItemCreate] = Field(..., min_length=1, max_length=100)


class ShipmentResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    received_by_user_id: int
    received_by_name: str
    received_at: datetime
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    items: List[ShipmentItemResponse]
    total_cost: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


# ==========================================================================
# Merma y existencias
# ==========================================================================
class WasteItemCreate(BaseModel):
    inventory_item_id: int
    quantity: Decimal = Field(..., gt=0)
    # Normalmente no se manda: el backend lo copia del último cargamento de ese insumo en esa
    # sucursal. Se acepta por si quien carga sabe que ese lote costó otra cosa.
    unit_cost: Optional[Decimal] = Field(None, ge=0)


class WasteItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    # Existencia que quedaba de ese insumo en esa sucursal justo antes de este registro. Se
    # calcula al responder, no se guarda: sirve para avisar "esto deja el stock en negativo".
    stock_before: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class WastePhotoResponse(BaseModel):
    """Datos de una foto de merma, sin los bytes (se piden aparte, ver GET .../photos/{id})."""
    id: int
    content_type: str
    size_bytes: int
    uploaded_by_name: Optional[str] = None
    created_at: datetime


class WasteCreate(BaseModel):
    branch_id: int
    reason: str = Field(..., min_length=1, max_length=40)
    occurred_at: Optional[datetime] = None
    notes: Optional[str] = None
    items: List[WasteItemCreate] = Field(..., min_length=1, max_length=100)
    # El peso leído en la balanza (opcional). Va con su unidad; sin unidad se toma kg.
    weight_value: Optional[Decimal] = Field(None, gt=0, max_digits=10, decimal_places=3)
    weight_unit: Optional[str] = Field(None, pattern="^(kg|g|lb)$")
    # Solo para "Recorte o limpieza": cuánto se limpió en total (para el rendimiento). En otros
    # motivos se ignora.
    processed_value: Optional[Decimal] = Field(None, gt=0, max_digits=10, decimal_places=3)
    processed_unit: Optional[str] = Field(None, pattern="^(kg|g|lb)$")


class WasteResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    recorded_by_user_id: int
    recorded_by_name: str
    occurred_at: datetime
    reason: str
    reason_label: str
    notes: Optional[str] = None
    created_at: datetime
    items: List[WasteItemResponse]
    total_cost: Optional[Decimal] = None
    # Insumos de este registro que dejaron la existencia por debajo de cero. No impide guardar
    # (ver create_waste): el sistema empezó a contar entradas hace poco y nadie cargó el
    # inventario de arranque, así que un negativo dice "falta cargar el arranque", no "error".
    negative_items: List[str] = []
    # Evidencia: el peso leído en la balanza y las fotos (estas se suben después de crear la merma).
    weight_value: Optional[Decimal] = None
    weight_unit: Optional[str] = None
    # Recorte o limpieza: merma de proceso, con lo que se limpió y el rendimiento (% aprovechado).
    is_process: bool = False
    processed_value: Optional[Decimal] = None
    processed_unit: Optional[str] = None
    yield_pct: Optional[Decimal] = None
    photos: List[WastePhotoResponse] = []

    model_config = ConfigDict(from_attributes=True)


class WasteAnalyticsTotals(BaseModel):
    records: int = 0
    cost_total: Decimal = Decimal("0")        # real + estimado
    cost_estimated: Decimal = Decimal("0")    # la parte valuada con el costo de referencia de Invu
    lines: int = 0
    lines_without_cost: int = 0               # sin cargamento ni costo de Invu: no suman
    kg_total: Decimal = Decimal("0")
    lines_without_kg: int = 0                 # por unidad y sin peso de balanza atribuible
    records_with_weight: int = 0
    records_with_photo: int = 0
    sales_net: Optional[Decimal] = None       # venta neta de Invu en el mismo período y sucursales
    waste_pct_of_sales: Optional[Decimal] = None
    cost_process: Decimal = Decimal("0")      # parte de la pérdida que es recorte/limpieza
    kg_process: Decimal = Decimal("0")


class WasteAnalyticsYield(BaseModel):
    """Rendimiento al limpiar un insumo: de lo que se limpió, qué parte se aprovechó."""
    inventory_item_id: int
    name: str
    processed_kg: Decimal                     # lo que se limpió
    trimmed_kg: Decimal                       # lo que salió como recorte
    yield_pct: Decimal                        # (limpiado - recorte) / limpiado × 100
    records: int


class WasteAnalyticsDay(BaseModel):
    date: str                                 # YYYY-MM-DD, día de Panamá
    cost: Decimal = Decimal("0")
    kg: Decimal = Decimal("0")
    records: int = 0


class WasteAnalyticsItem(BaseModel):
    inventory_item_id: int
    name: str
    unit: str
    kind: Optional[str] = None
    quantity: Decimal = Decimal("0")          # en la unidad del insumo
    kg: Optional[Decimal] = None
    cost: Decimal = Decimal("0")
    estimated: bool = False                   # algo de su costo salió de Invu
    records: int = 0


class WasteAnalyticsGroup(BaseModel):
    key: str
    label: str
    cost: Decimal = Decimal("0")
    kg: Decimal = Decimal("0")
    records: int = 0
    sales_net: Optional[Decimal] = None       # solo por sucursal
    waste_pct_of_sales: Optional[Decimal] = None


class WasteAnalyticsResponse(BaseModel):
    """Merma de un período, ya calculada: totales, por día, por insumo, por motivo, sucursal y tipo."""
    date_from: str
    date_to: str
    branch_id: Optional[int] = None
    totals: WasteAnalyticsTotals
    by_day: List[WasteAnalyticsDay]
    by_item: List[WasteAnalyticsItem]
    by_reason: List[WasteAnalyticsGroup]
    by_branch: List[WasteAnalyticsGroup]
    by_kind: List[WasteAnalyticsGroup]
    by_nature: List[WasteAnalyticsGroup] = []     # "proceso" (recorte) vs "evitable" (el resto)
    yields: List[WasteAnalyticsYield] = []


class WasteRecipeDishShare(BaseModel):
    name: str
    type: str                                 # "plato" | "modificador"
    used: Decimal                             # cuánto de este insumo usó, en la unidad del insumo
    share_pct: Decimal                        # parte del uso total del insumo


class WasteRecipeUsageItem(BaseModel):
    inventory_item_id: int
    name: str
    unit: str
    kind: Optional[str] = None
    wasted: Decimal                           # merma del período, en la unidad del insumo
    wasted_cost: Decimal
    estimated: bool = False
    used: Optional[Decimal] = None            # uso según recetas × ventas; None = ninguna receta lo usa
    waste_pct: Optional[Decimal] = None       # merma / (usado + merma) × 100
    dishes: List[WasteRecipeDishShare] = []   # los platos que más lo usan (hasta 3)


class WasteRecipeDish(BaseModel):
    name: str
    type: str
    allocated_cost: Decimal                   # merma repartida por uso (estimación)
    ingredients: List[str] = []               # los insumos que más le suman


class WasteRecipeUsageResponse(BaseModel):
    """Merma de cada insumo contra su uso real en los platos vendidos (recetas de Invu)."""
    date_from: str
    date_to: str
    branch_id: Optional[int] = None
    recipes_synced_at: Optional[datetime] = None
    recipes_count: int = 0
    recipes_running: bool = False
    sold_units: Decimal = Decimal("0")
    sold_units_with_recipe: Decimal = Decimal("0")
    lines_without_conversion: int = 0
    items: List[WasteRecipeUsageItem]
    dishes: List[WasteRecipeDish]


class WasteReasonResponse(BaseModel):
    code: str
    label: str


class StockRowResponse(BaseModel):
    """Una fila de existencias: un insumo en una sucursal."""
    inventory_item_id: int
    item_name: str
    unit: str
    category: Optional[str] = None
    branch_id: Optional[int] = None      # None cuando la fila es el total de todas las sucursales
    branch_name: Optional[str] = None
    entered: Decimal                     # todo lo que entró por cargamentos
    wasted: Decimal                      # todo lo que salió por merma
    adjusted: Decimal = Decimal("0")     # suma de las diferencias de conteo; negativo = faltó
    transferred: Decimal = Decimal("0")  # neto de traslados: recibido - despachado
    on_hand: Decimal                     # entered - wasted + adjusted + transferred; puede ser negativo, a propósito
    wasted_cost: Optional[Decimal] = None
    wasted_cost_estimated: bool = False   # parte de la pérdida se valuó con el costo de Invu
    last_movement_at: Optional[datetime] = None
    last_counted_at: Optional[datetime] = None
    # Costo unitario del último cargamento de ese insumo en esa sucursal. Solo viaja cuando la
    # consulta pide UNA sucursal: sumando todas no existe "el último costo", existe uno por
    # sucursal, y elegir cualquiera sería inventar. Lo usa el formulario de merma para estimar
    # la pérdida antes de guardar; el número que vale es el que calcula el servidor al grabar.
    last_unit_cost: Optional[Decimal] = None


# ==========================================================================
# Conteo físico
# ==========================================================================
class StockCountItemCreate(BaseModel):
    inventory_item_id: int
    # Cero vale: "no queda nada" es un conteo tan real como cualquier otro.
    counted_quantity: Decimal = Field(..., ge=0)


class StockCountCreate(BaseModel):
    branch_id: int
    notes: Optional[str] = None
    # Solo los insumos que se contaron. Lo que no viene no se toca: contar media cámara fría un
    # martes no puede poner en cero todo lo demás.
    items: List[StockCountItemCreate] = Field(..., min_length=1, max_length=500)


class StockCountItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    expected_quantity: Decimal
    counted_quantity: Decimal
    difference: Decimal
    unit_cost: Optional[Decimal] = None


class StockCountResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    counted_by_user_id: int
    counted_by_name: str
    counted_at: datetime
    notes: Optional[str] = None
    created_at: datetime
    items: List[StockCountItemResponse]
    # Renglones cuya cantidad contada no coincidió con la del sistema.
    mismatched_count: int = 0
    # Diferencia valuada: lo que sobró menos lo que faltó, al último costo conocido. None si
    # ningún renglón con diferencia tenía costo.
    difference_cost: Optional[Decimal] = None
    # Si fue el primer conteo de esa sucursal: el que hace de inventario de arranque. La pantalla
    # lo dice así, porque una diferencia enorme ahí no es un faltante, es lo que ya había.
    is_first_count: bool = False


# ==========================================================================
# Invu POS
# ==========================================================================
class InvuStatusResponse(BaseModel):
    """
    Si Invu manda sobre los proveedores. `configured=False` significa que el panel sigue
    administrándolos como antes: la pantalla usa esto para decidir entre "Nuevo proveedor" y
    "Sincronizar con Invu", que son excluyentes.
    """
    configured: bool
    last_synced_at: Optional[datetime] = None
    # Solo los ACTIVOS, que son los que el catálogo lista. Contar también los inactivos hacía
    # que la nota dijera "5 vienen de Invu" arriba de una lista de 4.
    synced_count: int = 0
    inactive_count: int = 0    # vinieron de Invu pero allá están apagados
    local_count: int = 0       # cargados a mano en el panel, sin equivalente en Invu
    # Lo mismo para los insumos (Ingredientes de Invu). Ahí el panel sigue pudiendo crear a mano.
    items_last_synced_at: Optional[datetime] = None
    items_synced_count: int = 0
    items_local_count: int = 0


class InvuSyncResult(BaseModel):
    received: int              # cuántos devolvió Invu
    created: int               # altas nuevas en el panel
    linked: int                # ya existían acá con el mismo nombre y quedaron emparejados
    updated: int               # ya estaban sincronizados y algún dato cambió
    deactivated: int = 0       # insumos: archivados en Invu, apagados acá
    synced_at: datetime


class MovementComparisonResponse(BaseModel):
    """
    Fase 4: existencia calculada por la fórmula de siempre vs. la que da el libro de movimientos
    nuevo, insumo por insumo. Solo lectura — sirve para observar si coinciden antes de decidir
    cuál manda; no cambia nada por sí sola.
    """
    inventory_item_id: int
    item_name: str
    on_hand_formula: Decimal
    on_hand_movements: Decimal
    matches: bool
