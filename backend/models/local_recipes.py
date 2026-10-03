"""
Recetas cargadas en Farmhouse Link (las del dashboard del pasante, farmhouse-app), para llenar
lo que Invu no tiene. Invu siempre manda: estas solo se usan para un plato que no tiene receta
en Invu en esa sucursal (ver services/recipe_resolver.py).

- LocalRecipe: un plato (kind="plato", gramos por porción) o una preparación de la casa
  (kind="interna": peso del lote y cuántas porciones rinde).
- LocalRecipeLine: un ingrediente con el NOMBRE que traía la receta; se empareja con el
  catálogo a través de IngredientAlias (un emparejamiento sirve para todas las recetas).
- RecipeDishLink: qué plato vendido en Invu usa qué receta cargada ("Pesto chicken" →
  "Pesto chicken sandwich").
- IngredientPrice: precio de compra por proveedor, de referencia para el food cost.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from database import Base


def _ahora():
    return datetime.now(timezone.utc)


class LocalRecipe(Base):
    __tablename__ = "local_recipes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    kind = Column(String(20), nullable=False, default="plato")          # plato | interna
    name = Column(String(200), nullable=False)
    name_norm = Column(String(200), nullable=False)
    category = Column(String(80), nullable=True)
    sale_price = Column(Numeric(10, 2), nullable=True)
    ref_cost = Column(Numeric(12, 4), nullable=True)                     # costo que calculó la fuente
    ref_food_cost_pct = Column(Numeric(8, 2), nullable=True)
    yield_weight_g = Column(Numeric(12, 3), nullable=True)               # preparaciones: peso del lote
    yield_portions = Column(Numeric(10, 2), nullable=True)               # preparaciones: porciones que rinde
    notes = Column(Text, nullable=True)
    source = Column(String(40), nullable=False, default="farmhouse_app")
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_ahora, nullable=False)
    updated_at = Column(DateTime, default=_ahora, onupdate=_ahora, nullable=False)

    lines = relationship("LocalRecipeLine", back_populates="recipe", cascade="all, delete-orphan", order_by="LocalRecipeLine.id")

    __table_args__ = (Index("ix_local_recipe_kind_norm", "kind", "name_norm", unique=True),)


class LocalRecipeLine(Base):
    __tablename__ = "local_recipe_lines"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    recipe_id = Column(Integer, ForeignKey("local_recipes.id", ondelete="CASCADE"), nullable=False, index=True)
    ingredient_name = Column(String(200), nullable=False)
    ingredient_norm = Column(String(200), nullable=False, index=True)
    quantity = Column(Numeric(12, 4), nullable=False)
    unit = Column(String(20), nullable=True)

    recipe = relationship("LocalRecipe", back_populates="lines")


class IngredientAlias(Base):
    """Un nombre de ingrediente de las recetas cargadas → un insumo del catálogo (o "no se
    descuenta": sal, agua, cosas que no se llevan en inventario)."""
    __tablename__ = "ingredient_aliases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    name_norm = Column(String(200), nullable=False, unique=True)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True)
    ignored = Column(Boolean, nullable=False, default=False)
    auto = Column(Boolean, nullable=False, default=False)               # lo emparejó el sistema por nombre
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=_ahora, onupdate=_ahora, nullable=False)

    inventory_item = relationship("InventoryItem")


class RecipeDishLink(Base):
    """Un plato como se vende en Invu (por nombre) → la receta cargada que usa."""
    __tablename__ = "recipe_dish_links"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    dish_name = Column(String(200), nullable=False)
    dish_norm = Column(String(200), nullable=False, unique=True)
    recipe_id = Column(Integer, ForeignKey("local_recipes.id", ondelete="CASCADE"), nullable=True)
    auto = Column(Boolean, nullable=False, default=False)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=_ahora, onupdate=_ahora, nullable=False)

    recipe = relationship("LocalRecipe")


class IngredientPrice(Base):
    __tablename__ = "ingredient_prices"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ingredient_name = Column(String(200), nullable=False)
    ingredient_norm = Column(String(200), nullable=False, index=True)
    category = Column(String(80), nullable=True)
    brand = Column(String(120), nullable=True)
    package_grams = Column(Numeric(12, 3), nullable=True)
    supplier = Column(String(150), nullable=True)
    price = Column(Numeric(12, 4), nullable=False)
    source = Column(String(40), nullable=False, default="farmhouse_app")
    imported_at = Column(DateTime, default=_ahora, nullable=False)
