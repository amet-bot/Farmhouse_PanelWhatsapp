"""
Inventario: paquete por temas. `router` es el APIRouter único (prefijo /inventory);
cada módulo registra sus endpoints en él al importarse. Los nombres se reexportan aquí
porque otros routers y las pruebas los importan como `routers.inventory.<nombre>`.
"""
from .common import (  # noqa: F401
    PROCESS_WASTE_REASONS,
    WASTE_REASONS,
    WASTE_REASON_LABELS,
    _linea_con_problema,
    _reclamo_de_linea,
    _serialize_shipment,
    logger,
    router,
)
from .helpers import (  # noqa: F401
    WASTE_SELF_DELETE_WINDOW,
    _ESTADO_TEXTO,
    _KG_POR_UNIDAD,
    _TRANSFER_OUT_STATUSES,
    _UNIT_FAMILY,
    _a_unidad_del_insumo,
    _avisar_diferencias_background,
    _cant,
    _chequear_quien_borra,
    _chequear_sin_conteo_posterior,
    _consumo_map,
    _describir_problema,
    _dias_utc,
    _existencia_map,
    _familia_de_unidad,
    _hay_a_quien_avisar,
    _insumos_con_receta,
    _last_costs_map,
    _on_hand_map,
    _recetas_de_sucursal,
    _transfer_net_map,
    _uso_por_ventas,
    _vendido_desde_conteo,
    _visible_branch_filter,
)
from .items import (  # noqa: F401
    create_inventory_item,
    create_supplier,
    search_inventory_items,
    search_suppliers,
)
from .shipments import (  # noqa: F401
    _shipment_insights,
    create_shipment,
    delete_shipment,
    list_shipments,
    shipment_insights,
)
from .waste import (  # noqa: F401
    WASTE_ALERT_MIN_COST,
    WASTE_ALERT_REPEAT,
    WASTE_ANALYTICS_MAX_DAYS,
    WASTE_PHOTOS_PER_RECORD,
    WASTE_PHOTO_MAX_BYTES,
    _alertas_de_merma,
    _avisar_merma_background,
    _cantidad_de_linea,
    _chequear_fecha_posterior_a_conteo,
    _compra_contra_vencimiento,
    _image_type,
    _kg_factor,
    _last_known_cost,
    _recorte_kg,
    _rendimiento,
    _serialize_waste,
    _waste_for_user,
    _waste_insights,
    add_waste_photo,
    create_waste,
    delete_waste,
    get_waste,
    get_waste_photo,
    list_waste,
    list_waste_reasons,
    set_item_density,
    set_item_piece_size,
    sync_recipes_from_invu,
    waste_analytics,
    waste_insights,
    waste_recipe_usage,
)
from .stock import (  # noqa: F401
    list_stock,
)
from .counts import (  # noqa: F401
    COUNT_TOLERANCE_PCT,
    _analizar_conteo,
    _cifras_sucursal,
    _first_count_ids,
    _pct_de,
    _porcentajes,
    _serialize_count,
    _sumar_cifras,
    count_analysis,
    create_count,
    inventory_dashboard,
    list_counts,
)
from .movements import (  # noqa: F401
    compare_movements_with_formula,
)
from .invu import (  # noqa: F401
    invu_status,
    sync_items_from_invu,
    sync_suppliers_from_invu,
)
