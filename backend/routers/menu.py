from fastapi import APIRouter
from services.menu_catalog import get_menu_structure

router = APIRouter(prefix="/menu", tags=["Menú Digital"])


@router.get("/items")
def get_menu_items():
    """
    Devuelve el catálogo de 72 productos de Farmhouse agrupado por pestañas
    (Salads, Bowls, Wraps, Build Your Own, Toasties, Smoothies, Drinks & Vitrina)
    para alimentar la Web App de Menú Interactivo en /menu. Endpoint público.
    """
    return get_menu_structure()
