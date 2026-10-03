"""tareas con foto como prueba

- tasks.requires_photo: la tarea pide una foto para poder marcarse hecha.
- task_photos: las fotos (dentro de la base, como las de merma: el disco del servidor no es para
  evidencia).

Revision ID: 064_task_photos
Revises: 063_recurring_tasks
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "064_task_photos"
down_revision: Union[str, None] = "063_recurring_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "requires_photo" not in {c["name"] for c in insp.get_columns("tasks")}:
        op.add_column("tasks", sa.Column("requires_photo", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if not insp.has_table("task_photos"):
        op.create_table(
            "task_photos",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("content_type", sa.String(length=40), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("data", sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql"), nullable=False),
            sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_task_photos_task_id", "task_photos", ["task_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("task_photos"):
        op.drop_index("ix_task_photos_task_id", table_name="task_photos")
        op.drop_table("task_photos")
    if "requires_photo" in {c["name"] for c in insp.get_columns("tasks")}:
        op.drop_column("tasks", "requires_photo")
