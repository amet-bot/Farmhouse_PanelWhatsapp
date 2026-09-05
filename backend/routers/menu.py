from fastapi import APIRouter, Response
from services.menu_catalog import get_menu_structure

router = APIRouter(prefix="/menu", tags=["Menú Digital"])


@router.get("/items")
def get_menu_items(response: Response):
    """
    Devuelve el catálogo de 91 productos de Farmhouse agrupado por pestañas
    (Salads, Bowls, Wraps, Build Your Own, Toasties, Smoothies, Drinks, Vitrina & Merch)
    para alimentar la Web App de Menú Interactivo en /menu. Endpoint público con no-cache.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return get_menu_structure()

