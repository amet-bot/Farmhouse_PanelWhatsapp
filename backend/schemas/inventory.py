from pydantic import BaseModel, Field, ConfigDict
from decimal import Decimal
from typing import Optional, List
from datetime import date, datetime


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
    piece_size: Optional[Decimal] = None       # una pieza entera: gramos (o ml si es de volumen)
    grams_per_ml: Optional[Decimal] = None     # gramos que pesa 1 ml (recetas en g de un insumo en ml)

    model_config = ConfigDict(from_attributes=True)


class InventoryItemPieceSize(BaseModel):
    """Cuánto es una pieza entera del insumo (g, o ml si se mide en volumen). None la borra."""
    piece_size: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=3)


class InventoryItemDensity(BaseModel):
    """Cuántos gramos pesa 1 ml del insumo (agua = 1, aceite ≈ 0.92, miel ≈ 1.42). None la borra."""
    grams_per_ml: Optional[Decimal] = Field(None, gt=0, le=5, max_digits=8, decimal_places=4)


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


# Cómo llegó un renglón comparado con la factura. "falto" y "sobro" los deduce el servidor de lo
# facturado contra lo que llegó; "equivocado" y "danado" los marca quien recibe.
SHIPMENT_LINE_STATUSES = ("ok", "falto", "sobro", "equivocado", "danado")


class ShipmentItemCreate(BaseModel):
    inventory_item_id: int
    # Lo que llegó de verdad y entra a la existencia. Puede ser 0 si no llegó nada de lo
    # facturado, o si llegó equivocado o dañado y se devolvió.
    quantity: Decimal = Field(..., ge=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)
    invoiced_quantity: Optional[Decimal] = Field(None, ge=0)   # lo que dice la factura
    line_status: Optional[str] = Field(None, max_length=20)
    line_note: Optional[str] = Field(None, max_length=200)


class ShipmentItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    invoiced_quantity: Optional[Decimal] = None
    line_status: Optional[str] = None
    line_note: Optional[str] = None
    # Lo que hay que reclamar: (facturado − llegó) × costo, si faltó.
    claim_value: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class ShipmentCreate(BaseModel):
    branch_id: int
    received_at: Optional[datetime] = None
    supplier_id: Optional[int] = None
    notes: Optional[str] = None
    invoice_number: Optional[str] = Field(None, max_length=60)
    expected_shipment_id: Optional[int] = None   # el cargamento agendado que se está recibiendo
    items: List[ShipmentItemCreate] = Field(..., min_length=1, max_length=100)


class ShipmentPhotoResponse(BaseModel):
    id: int
    content_type: str
    size_bytes: int
    uploaded_by_name: Optional[str] = None
    created_at: datetime


class ExpectedShipmentCreate(BaseModel):
    branch_id: int
    supplier_id: Optional[int] = None
    expected_date: date
    time_from: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notes: Optional[str] = Field(None, max_length=500)


class ExpectedShipmentLine(BaseModel):
    """Una línea de una orden de compra (cargamento agendado con cantidades)."""
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None


class ExpectedShipmentResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    expected_date: date
    time_from: Optional[str] = None
    notes: Optional[str] = None
    status: str
    created_by_name: Optional[str] = None
    created_at: datetime
    shipment_id: Optional[int] = None
    # Vacío en lo agendado a mano; con líneas cuando es una orden de compra (abastecimiento).
    items: List[ExpectedShipmentLine] = []
    est_cost: Optional[Decimal] = None


class SupplierIssueRow(BaseModel):
    """Qué tanto falla un proveedor al entregar (cargamentos recibidos contra factura)."""
    supplier_id: Optional[int] = None
    supplier_name: str
    shipments: int                     # recibidos contra factura en el período
    with_issues: int
    issue_pct: Decimal
    claim_value: Decimal               # lo que faltó, en $
    last_issue_at: Optional[datetime] = None


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
    invoice_number: Optional[str] = None
    has_issues: Optional[bool] = None
    issues_count: int = 0
    claim_total: Optional[Decimal] = None
    incident_id: Optional[int] = None
    expected_shipment_id: Optional[int] = None
    photos: List[ShipmentPhotoResponse] = []
    # Solo al crear: si se avisó al supervisor por notificación (hay a quién y está configurado).
    notified: Optional[bool] = None
    # Solo en la respuesta de crear: el contexto del cargamento (ver GET /shipments/{id}/insights).
    insights: Optional["ShipmentInsights"] = None

    model_config = ConfigDict(from_attributes=True)


class ShipmentInsightItem(BaseModel):
    """Un insumo del cargamento, comparado con compras anteriores y con lo que hay."""
    inventory_item_id: int
    name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    # La compra anterior de este insumo en esta sucursal (con costo).
    prev_unit_cost: Optional[Decimal] = None
    prev_received_at: Optional[datetime] = None
    prev_supplier: Optional[str] = None
    change_pct: Optional[Decimal] = None        # (este − anterior) / anterior × 100
    # La compra más barata reciente en OTRA sucursal (últimos 90 días, la última de cada una).
    best_other_cost: Optional[Decimal] = None
    best_other_branch: Optional[str] = None
    best_other_supplier: Optional[str] = None
    best_other_received_at: Optional[datetime] = None
    reference_cost: Optional[Decimal] = None    # el costo de Invu (el que usan sus recetas)
    stock_now: Decimal = Decimal("0")           # existencia de hoy en la sucursal
    used_per_day: Optional[Decimal] = None      # uso por día en platos vendidos (14 días, recetas)
    days_left: Optional[Decimal] = None         # para cuántos días alcanza a ese ritmo


class ShipmentInsights(BaseModel):
    shipment_id: int
    branch_id: int
    branch_name: str
    supplier_name: Optional[str] = None
    total_cost: Optional[Decimal] = None
    branch_week_spend: Decimal                  # compras de la sucursal, últimos 7 días
    branch_prev_week_spend: Decimal
    supplier_month_spend: Optional[Decimal] = None   # a este proveedor, últimos 30 días
    items_without_cost: int = 0
    items: List[ShipmentInsightItem]
    # Historial del proveedor (todas las sucursales, 90 días): cuántas entregas con diferencias.
    supplier_shipments_90d: Optional[int] = None
    supplier_issues_90d: Optional[int] = None
    supplier_claim_90d: Optional[Decimal] = None


# ==========================================================================
# Merma y existencias
# ==========================================================================
class WasteItemCreate(BaseModel):
    """
    Una línea de la merma. Dos formas de decir cuánto se botó:
      - Sin `mode` (como siempre): `quantity`, en la unidad del insumo.
      - Con `mode` (el formulario): el servidor calcula `quantity` —
          "entera": `pieces` piezas completas. Si el insumo va en peso o volumen hace falta el
                    tamaño de una pieza (`piece_size`, o el que ya tiene guardado el insumo).
          "parte":  un pedazo o residuo. Insumo en peso/volumen: `quantity` en su unidad (lo que
                    marca la balanza). Insumo por unidad: `part_amount` en gramos, y se convierte
                    a fracción de pieza con el tamaño de una pieza.
    """
    inventory_item_id: int
    quantity: Optional[Decimal] = Field(None, gt=0)
    # Normalmente no se manda: el backend lo copia del último cargamento de ese insumo en esa
    # sucursal. Se acepta por si quien carga sabe que ese lote costó otra cosa.
    unit_cost: Optional[Decimal] = Field(None, ge=0)
    mode: Optional[str] = Field(None, pattern="^(entera|parte)$")
    pieces: Optional[Decimal] = Field(None, gt=0, max_digits=10, decimal_places=3)
    piece_size: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=3)
    part_amount: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=3)
    # "entera" que además se pesó: el peso real (g, o ml si es de volumen). Manda sobre el peso
    # promedio de la pieza; sin esto, el peso de una "entera" es una estimación.
    measured_amount: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=3)


class WasteItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    reference_cost: Optional[Decimal] = None   # costo de Invu del insumo, si no hay unit_cost
    mode: Optional[str] = None                 # "entera" | "parte" | None (mermas anteriores)
    pieces: Optional[Decimal] = None
    piece_size: Optional[Decimal] = None       # tamaño de una pieza del insumo (g o ml), hoy
    measured_amount: Optional[Decimal] = None  # lo que se pesó para esta línea (g o ml)
    weight_estimated: bool = False             # "entera" sin pesar: el peso es el promedio
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
    weight_estimated: Optional[bool] = None   # el peso salió del promedio de una pieza, no de la balanza
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
    # Lo que muestra la pantalla: total_cost (costo de los cargamentos) completado con el costo de
    # referencia de Invu donde no hay cargamento. `cost_estimated` avisa que salió de Invu (≈).
    display_cost: Optional[Decimal] = None
    cost_estimated: bool = False
    # Insumos de este registro que dejaron la existencia por debajo de cero. No impide guardar
    # (ver create_waste): el sistema empezó a contar entradas hace poco y nadie cargó el
    # inventario de arranque, así que un negativo dice "falta cargar el arranque", no "error".
    negative_items: List[str] = []
    # Solo al crear: por qué se avisó al encargado (monto alto, motivo repetido) y si le llegó.
    alert_reasons: List[str] = []
    notified: Optional[bool] = None
    # Evidencia: el peso leído en la balanza y las fotos (estas se suben después de crear la merma).
    weight_value: Optional[Decimal] = None
    weight_unit: Optional[str] = None
    weight_estimated: bool = False
    # Recorte o limpieza: merma de proceso, con lo que se limpió y el rendimiento (% aprovechado).
    is_process: bool = False
    processed_value: Optional[Decimal] = None
    processed_unit: Optional[str] = None
    yield_pct: Optional[Decimal] = None
    photos: List[WastePhotoResponse] = []
    # Solo en la respuesta de crear: el contexto de esta merma (ver GET /waste/{id}/insights).
    insights: Optional["WasteInsights"] = None

    model_config = ConfigDict(from_attributes=True)


class WasteInsightItem(BaseModel):
    """Un insumo de la merma, puesto en contexto en su sucursal (días en hora de Panamá)."""
    inventory_item_id: int
    name: str
    unit: str
    this_quantity: Decimal
    this_cost: Optional[Decimal] = None
    week_quantity: Decimal                      # últimos 7 días, incluida esta
    week_cost: Decimal
    week_records: int
    prev_week_cost: Decimal                     # los 7 días anteriores
    month_cost: Decimal                         # últimos 30 días
    month_records: int
    same_reason_month: int                      # mermas de este insumo con el mismo motivo en 30 días
    rank_month: Optional[int] = None            # puesto entre los insumos que más se pierden ($, 30 días)
    items_ranked: int = 0
    used_month: Optional[Decimal] = None        # usado en platos vendidos en 30 días (recetas de Invu)
    waste_pct_month: Optional[Decimal] = None   # merma / (usado + merma)
    cost_estimated: bool = False
    # Solo si se venció: la última compra de ese insumo en la sucursal contra el ritmo de uso.
    last_purchase_qty: Optional[Decimal] = None
    last_purchase_at: Optional[datetime] = None
    last_purchase_supplier: Optional[str] = None
    days_to_expire: Optional[int] = None        # días entre esa compra y el vencimiento
    used_per_day: Optional[Decimal] = None      # uso por día en platos vendidos (30 días, recetas)
    purchase_cover_days: Optional[Decimal] = None   # para cuántos días alcanzaba esa compra
    suggested_max_qty: Optional[Decimal] = None     # lo que se alcanza a usar antes de que venza
    expired_90d: int = 0                        # veces que se venció este insumo en 90 días


class WasteInsights(BaseModel):
    waste_id: int
    branch_id: int
    branch_name: str
    reason: str
    reason_label: str
    branch_week_cost: Decimal                   # toda la merma de la sucursal, últimos 7 días
    branch_prev_week_cost: Decimal
    branch_week_records: int
    items: List[WasteInsightItem]


class WasteAnalyticsTotals(BaseModel):
    records: int = 0
    cost_total: Decimal = Decimal("0")        # real + estimado
    cost_estimated: Decimal = Decimal("0")    # la parte valuada con el costo de referencia de Invu
    lines: int = 0
    lines_without_cost: int = 0               # sin cargamento ni costo de Invu: no suman
    kg_total: Decimal = Decimal("0")
    kg_estimated: Decimal = Decimal("0")      # parte de kg_total que salió del peso promedio de una pieza
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
    consumed: Decimal = Decimal("0")     # lo que el equipo registró como consumido (Registro de consumo)
    # Lo que se usó en platos vendidos desde el último conteo (ventas de Invu × recetas). None si
    # el insumo no tiene receta o nunca se contó: sin punto de partida no se descuenta nada.
    sold_since_count: Optional[Decimal] = None
    on_hand: Decimal                     # entered - wasted - consumed + adjusted + transferred - sold_since_count; puede ser negativo, a propósito
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
    # Solo en la respuesta de crear: el análisis de lo contado (ver GET /counts/{id}/analysis).
    analysis: Optional["StockCountAnalysis"] = None


class StockCountAnalysisLine(BaseModel):
    """
    Un insumo del conteo, explicado: lo que tenía que haber y lo que no se explica.

      tenía que haber = lo que decía el sistema (entradas, merma, traslados y el conteo anterior)
                        − lo que se usó en los platos vendidos desde el conteo anterior (recetas)
      sin explicar    = contado − tenía que haber     (negativo: faltó)
    """
    inventory_item_id: int
    name: str
    unit: str
    # "arranque" (primer conteo del insumo: punto de partida, no es faltante), "cuadra", "falta",
    # "sobra" o "sin_receta" (faltó, pero no está en ninguna receta de Invu: puede ser consumo).
    status: str
    expected_records: Decimal                 # lo que decía el sistema antes de contar
    used_by_sales: Optional[Decimal] = None   # usado en ventas desde el conteo anterior (None: sin receta)
    expected: Decimal                         # tenía que haber
    counted: Decimal
    unexplained: Optional[Decimal] = None     # None en el arranque
    unexplained_pct: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None
    cost: Optional[Decimal] = None            # valor de lo sin explicar (en el arranque: valor de lo contado)
    cost_estimated: bool = False              # valuado con el costo de referencia de Invu
    since: Optional[datetime] = None          # el conteo anterior de este insumo


class StockCountAnalysisTotals(BaseModel):
    items: int = 0
    baseline: int = 0
    ok: int = 0
    missing: int = 0
    surplus: int = 0
    no_recipe: int = 0
    no_conversion: int = 0                    # faltó, pero parte del uso no se pudo pasar de unidad
    missing_cost: Decimal = Decimal("0")      # lo que faltó (con receta), en positivo
    surplus_cost: Decimal = Decimal("0")
    no_recipe_cost: Decimal = Decimal("0")    # faltó en insumos sin receta, en positivo
    no_conversion_cost: Decimal = Decimal("0")
    baseline_value: Decimal = Decimal("0")    # valor de lo contado por primera vez
    cost_estimated: bool = False


class StockCountAnalysis(BaseModel):
    count_id: int
    branch_id: int
    branch_name: str
    counted_at: datetime
    tolerance_pct: Decimal
    recipes_available: bool                   # la sucursal tiene recetas de Invu cargadas
    sales_synced_at: Optional[datetime] = None  # hasta cuándo llegaron las ventas de hoy
    totals: StockCountAnalysisTotals
    lines: List[StockCountAnalysisLine]


# ==========================================================================
# Tablero (Resumen de Inventario)
# ==========================================================================
class DashboardFigures(BaseModel):
    """Las cifras de un período, para una sucursal o para todas juntas."""
    sales_net: Optional[Decimal] = None          # venta neta de Invu (None: sin ventas sincronizadas)
    purchases: Decimal = Decimal("0")            # cargamentos con costo
    purchase_lines_without_cost: int = 0
    waste: Decimal = Decimal("0")                # merma, con el costo de Invu donde falta el de compra
    waste_estimated: bool = False
    count_missing: Decimal = Decimal("0")        # faltó en conteos (insumos con receta)
    count_no_recipe: Decimal = Decimal("0")      # faltó en insumos sin receta (puede ser consumo)
    count_surplus: Decimal = Decimal("0")
    counts: int = 0
    waste_pct_sales: Optional[Decimal] = None
    purchases_pct_sales: Optional[Decimal] = None


class DashboardBranch(DashboardFigures):
    branch_id: int
    branch_code: Optional[str] = None
    branch_name: str


class DashboardTopItem(BaseModel):
    inventory_item_id: int
    name: str
    unit: str
    quantity: Decimal
    cost: Decimal
    estimated: bool = False


class DashboardResponse(BaseModel):
    date_from: date
    date_to: date
    prev_from: date
    prev_to: date
    branch_id: Optional[int] = None
    totals: DashboardFigures
    prev_totals: DashboardFigures
    branches: List[DashboardBranch]
    top_waste: List[DashboardTopItem]
    top_missing: List[DashboardTopItem]


StockCountResponse.model_rebuild()   # `analysis` se declara antes que StockCountAnalysis
WasteResponse.model_rebuild()        # `insights` se declara antes que WasteInsights
ShipmentResponse.model_rebuild()     # `insights` se declara antes que ShipmentInsights


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
