"""horario_quick_reply_text

Texto nuevo de la respuesta rápida global /horario ("Horarios y sucursales") del Centro WhatsApp,
tal como lo pidió el usuario el 2026-10-09: una línea por sucursal con su horario.

Solo toca la global (branch_id NULL); si alguna sucursal creó la suya, se respeta.

Revision ID: 076_horario_quick_reply_text
Revises: 075_quick_replies_and_tags
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "076_horario_quick_reply_text"
down_revision: Union[str, None] = "075_quick_replies_and_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HORARIO_BODY = (
    "Horario de las sucursales:\n"
    "farmhouse | Obarrio — 8:00 a. m. a 9:00 p. m., todos los días.\n"
    "farmhouse | San Francisco — 8:00 a. m. a 9:00 p. m., todos los días.\n"
    "farmhouse | Costa del Este (Torre MMG) — 8:00 a. m. a 9:00 p. m., todos los días.\n"
    "farmhouse | Clayton — 8:00 a. m. a 9:00 p. m., todos los días.\n"
    "farmhouse by the park | Parque Omar — 6:00 a. m. a 9:00 p. m., todos los días."
)

SEDES_BODY = (
    "Sucursales:\n\n"
    "Aquí puede ver las ubicaciones de nuestras sedes:\n"
    "Instagram: https://www.instagram.com/somosfarmhouse/\n"
    "•  Costa del Este: https://maps.app.goo.gl/8cKwdQ1b4Kh3RH6c9\n"
    "•  San Francisco: https://maps.app.goo.gl/hQsn2NkxkhfukYcJA\n"
    "•  Clayton: https://maps.app.goo.gl/4dfQp1r1pphThU4S6\n"
    "•  Obarrio: https://maps.app.goo.gl/y37SjxELSRJok3Km7\n"
    "•  Vía Porras (express): https://maps.app.goo.gl/1SSJvWX8yQGgA6VG6"
)

OLD_BODY = (
    "Nuestro horario es de Lunes a Domingo, 8:00 AM a 9:30 PM (Vía Porras y Obarrio abren desde "
    "las 6:00 AM). ¿Te comparto la dirección de la sucursal más cercana?"
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE quick_replies SET body = :body WHERE shortcut = 'horario' AND branch_id IS NULL")
        .bindparams(body=HORARIO_BODY)
    )
    # Nueva: /sedes con los enlaces de Maps de cada sucursal (solo si nadie la creó ya).
    existe = bind.execute(sa.text("SELECT COUNT(*) FROM quick_replies WHERE shortcut = 'sedes'")).scalar()
    if not existe:
        bind.execute(
            sa.text(
                "INSERT INTO quick_replies (shortcut, title, body, branch_id, active, sort_order) "
                "VALUES ('sedes', 'Sucursales y ubicaciones', :body, NULL, 1, 3)"
            ).bindparams(body=SEDES_BODY)
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE quick_replies SET body = :body WHERE shortcut = 'horario' AND branch_id IS NULL")
        .bindparams(body=OLD_BODY)
    )
    bind.execute(sa.text("DELETE FROM quick_replies WHERE shortcut = 'sedes' AND branch_id IS NULL"))
