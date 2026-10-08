"""quick_replies_and_tags

Centro WhatsApp: respuestas rápidas configurables (antes eran 4 textos fijos en el frontend) y
etiquetas de cliente. Se siembran las 4 respuestas que ya existían, como globales.

Revision ID: 075_quick_replies_and_tags
Revises: 074_device_enrollment
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "075_quick_replies_and_tags"
down_revision: Union[str, None] = "074_device_enrollment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_REPLIES = [
    ("menu", "Ver productos", "Claro, te comparto nuestro menú para que veas todos los productos disponibles 😊\n{menu}"),
    ("pedido", "Estado de mi pedido", "¿Me confirmas tu nombre o número de pedido para revisar el estado?"),
    ("horario", "Horarios y sucursales", "Nuestro horario es de Lunes a Domingo, 8:00 AM a 9:30 PM (Vía Porras y Obarrio abren desde las 6:00 AM). ¿Te comparto la dirección de la sucursal más cercana?"),
    ("asesor", "Hablar con un asesor", "Con gusto te comunico con un asesor para que te ayude personalmente."),
]


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "quick_replies" not in tables:
        op.create_table(
            "quick_replies",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("shortcut", sa.String(40), nullable=False),
            sa.Column("title", sa.String(120), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_quick_replies_shortcut", "quick_replies", ["shortcut"])
        op.create_index("ix_quick_replies_branch_id", "quick_replies", ["branch_id"])
        rows = [{"shortcut": s, "title": t, "body": b, "sort_order": i} for i, (s, t, b) in enumerate(DEFAULT_REPLIES)]
        op.bulk_insert(sa.table(
            "quick_replies",
            sa.column("shortcut", sa.String), sa.column("title", sa.String),
            sa.column("body", sa.Text), sa.column("sort_order", sa.Integer),
        ), rows)

    if "contact_tags" not in tables:
        op.create_table(
            "contact_tags",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(60), nullable=False, unique=True),
            sa.Column("color", sa.String(7), nullable=False, server_default="#16a34a"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if "contact_tag_links" not in tables:
        op.create_table(
            "contact_tag_links",
            sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tag_id", sa.Integer(), sa.ForeignKey("contact_tags.id", ondelete="CASCADE"), primary_key=True),
        )


def downgrade() -> None:
    op.drop_table("contact_tag_links")
    op.drop_table("contact_tags")
    op.drop_index("ix_quick_replies_branch_id", table_name="quick_replies")
    op.drop_index("ix_quick_replies_shortcut", table_name="quick_replies")
    op.drop_table("quick_replies")
