"""hoja de cierre: insumos que la sucursal cuenta al cerrar el turno

- item_branch_settings.on_closing_sheet / sheet_position: qué insumos van en la hoja y en qué orden.
- stock_counts.kind: 'closing' cuando el conteo vino de la hoja de cierre (para distinguirlo de
  un conteo completo y para calcular el ritmo de uso a partir de los cierres).

Revision ID: 060_closing_sheet
Revises: 059_supply
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "060_closing_sheet"
down_revision: Union[str, None] = "059_supply"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = _cols(insp, "item_branch_settings")
    if "on_closing_sheet" not in cols:
        op.add_column("item_branch_settings", sa.Column("on_closing_sheet", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if "sheet_position" not in cols:
        op.add_column("item_branch_settings", sa.Column("sheet_position", sa.Integer(), nullable=True))
    if "kind" not in _cols(insp, "stock_counts"):
        op.add_column("stock_counts", sa.Column("kind", sa.String(length=20), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "kind" in _cols(insp, "stock_counts"):
        op.drop_column("stock_counts", "kind")
    cols = _cols(insp, "item_branch_settings")
    if "sheet_position" in cols:
        op.drop_column("item_branch_settings", "sheet_position")
    if "on_closing_sheet" in cols:
        op.drop_column("item_branch_settings", "on_closing_sheet")
