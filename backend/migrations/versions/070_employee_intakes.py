"""invitaciones y solicitudes de colaboradores (formulario /colaborador)

Revision ID: 070_employee_intakes
Revises: 069_employee_contracts

Pedido del negocio (2026-10-05): que el colaborador llene sus propios datos desde un enlace y le
lleguen al sistema; el administrador los revisa y arma el contrato. Solo con una invitación vigente
(un solo uso, vence). El formulario vive en un servicio aparte (public_intake/); aquí solo quedan las
invitaciones y las solicitudes ya importadas. Ver models.contract.EmployeeInvite / EmployeeIntake y
routers/contracts.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "070_employee_intakes"
down_revision: Union[str, None] = "069_employee_contracts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "employee_invites" not in tables:
        op.create_table(
            "employee_invites",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("label", sa.String(150), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_employee_invite_expires", "employee_invites", ["expires_at"])
    if "employee_intakes" not in tables:
        op.create_table(
            "employee_intakes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("invite_id", sa.Integer(), sa.ForeignKey("employee_invites.id", ondelete="SET NULL"), nullable=True),
            sa.Column("remote_id", sa.Integer(), nullable=True, unique=True),
            sa.Column("first_name", sa.String(100), nullable=False),
            sa.Column("last_name", sa.String(100), nullable=False),
            sa.Column("birth_date", sa.Text(), nullable=False),
            sa.Column("gender", sa.String(1), nullable=False),
            sa.Column("nationality", sa.String(60), nullable=False),
            sa.Column("marital_status", sa.String(20), nullable=False),
            sa.Column("blood_type", sa.Text(), nullable=False),
            sa.Column("id_type", sa.String(20), nullable=False),
            sa.Column("id_number", sa.Text(), nullable=False),
            sa.Column("id_number_hash", sa.String(64), nullable=False),
            sa.Column("dv", sa.Text(), nullable=True),
            sa.Column("phone", sa.Text(), nullable=False),
            sa.Column("email", sa.Text(), nullable=False),
            sa.Column("address", sa.Text(), nullable=False),
            sa.Column("emergency_contact_name", sa.Text(), nullable=False),
            sa.Column("emergency_contact_phone", sa.Text(), nullable=False),
            sa.Column("emergency_contact_relationship", sa.Text(), nullable=False),
            sa.Column("bank_name", sa.Text(), nullable=False),
            sa.Column("account_type", sa.String(20), nullable=False),
            sa.Column("account_number", sa.Text(), nullable=False),
            sa.Column("dependents_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_employee_intake_created", "employee_intakes", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "employee_intakes" in tables:
        op.drop_index("ix_employee_intake_created", table_name="employee_intakes")
        op.drop_table("employee_intakes")
    if "employee_invites" in tables:
        op.drop_index("ix_employee_invite_expires", table_name="employee_invites")
        op.drop_table("employee_invites")
