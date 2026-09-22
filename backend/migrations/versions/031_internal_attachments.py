"""internal_attachments

Adjuntos en Comunicación Interna: cuatro columnas nullable sobre internal_messages. El archivo
vive en disco (media/internal/), acá solo queda la referencia. No se toca `body`, que sigue
NOT NULL: un mensaje que es solo adjunto guarda "".

Revision ID: 031_internal_attachments
Revises: 030_internal_chat
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '031_internal_attachments'
down_revision: Union[str, None] = '030_internal_chat'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = (
    ('media_url', sa.String(length=500)),
    ('media_mime_type', sa.String(length=120)),
    ('media_name', sa.String(length=255)),
    ('media_size', sa.Integer()),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('internal_messages'):
        return
    existing = {c['name'] for c in inspector.get_columns('internal_messages')}
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column('internal_messages', sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('internal_messages'):
        return
    existing = {c['name'] for c in inspector.get_columns('internal_messages')}
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column('internal_messages', name)
