"""contratos de colaboradores (página /contratos)

Revision ID: 069_employee_contracts
Revises: 068_consumption_movements

Pedido del negocio (2026-10-05): registrar a cada colaborador con sus datos personales, bancarios,
persona de contacto y condiciones del contrato, y generar el contrato en Word desde esos datos.
Ver models.contract.EmployeeContract y routers/contracts.py.

Los datos sensibles van CIFRADOS (services/field_crypto.py), por eso esas columnas son TEXT: guardan
el texto cifrado, no el dato. `id_number_hash` es el índice ciego para buscar por cédula.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "069_employee_contracts"
down_revision: Union[str, None] = "068_consumption_movements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "employee_contracts" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "employee_contracts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
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
        sa.Column("position", sa.String(100), nullable=False),
        sa.Column("contract_type", sa.String(30), nullable=False),
        sa.Column("salary", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("dependents_json", sa.Text(), nullable=True),
        sa.Column("duties", sa.Text(), nullable=True),
        sa.Column("document_url", sa.String(500), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_employee_contract_person", "employee_contracts", ["id_type", "id_number_hash"])
    op.create_index("ix_employee_contract_dates", "employee_contracts", ["start_date", "end_date"])


def downgrade() -> None:
    bind = op.get_bind()
    if "employee_contracts" in sa.inspect(bind).get_table_names():
        op.drop_index("ix_employee_contract_dates", table_name="employee_contracts")
        op.drop_index("ix_employee_contract_person", table_name="employee_contracts")
        op.drop_table("employee_contracts")
