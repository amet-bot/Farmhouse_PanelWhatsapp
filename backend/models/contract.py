from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Index
from datetime import datetime, timezone
from database import Base
from services.field_crypto import EncryptedText, EncryptedDate, EncryptedDecimal

"""
Contratos de trabajo de los colaboradores (página /contratos) y el formulario con el que cada persona
llena sus propios datos (/colaborador).

Son datos personales, de salud y bancarios: por eso
- Se cifran en la base (services/field_crypto.py): cédula/pasaporte, fecha de nacimiento, teléfonos,
  correo, dirección, tipo de sangre, persona de contacto, banco, cuenta, salario, dependientes y
  notas. Lo que queda en claro es lo que hace falta para listar y ordenar (nombre, puesto, tipo y
  fechas del contrato).
- La cédula se busca por un índice ciego (`id_number_hash`), no en claro.
- Solo el administrador (`contracts.manage`) los ve; toda lectura de detalle queda en auditoría.

Una fila de `employee_contracts` es UN contrato, no una persona: quien renueva tiene dos, por eso la
cédula no es única. Lo que sí se evita es cargar dos veces el mismo contrato (misma identificación y
misma fecha de inicio).
"""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EmployeeContract(Base):
    __tablename__ = "employee_contracts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # Datos personales
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    birth_date = Column(EncryptedDate, nullable=False)
    gender = Column(String(1), nullable=False)               # "M" / "F"
    nationality = Column(String(60), nullable=False)
    marital_status = Column(String(20), nullable=False)
    blood_type = Column(EncryptedText, nullable=False)       # "O+", "A-"... (dato de salud)
    id_type = Column(String(20), nullable=False)             # "Cedula" / "Pasaporte"
    id_number = Column(EncryptedText, nullable=False)
    id_number_hash = Column(String(64), nullable=False)      # índice ciego (HMAC) de id_number
    dv = Column(EncryptedText, nullable=True)
    phone = Column(EncryptedText, nullable=False)
    email = Column(EncryptedText, nullable=False)
    address = Column(EncryptedText, nullable=False)

    # Persona de contacto (emergencias)
    emergency_contact_name = Column(EncryptedText, nullable=False)
    emergency_contact_phone = Column(EncryptedText, nullable=False)
    emergency_contact_relationship = Column(EncryptedText, nullable=False)

    # Datos bancarios
    bank_name = Column(EncryptedText, nullable=False)
    account_type = Column(String(20), nullable=False)        # "Ahorros" / "Corriente"
    account_number = Column(EncryptedText, nullable=False)

    # Contrato
    position = Column(String(100), nullable=False)
    contract_type = Column(String(30), nullable=False)       # Definido / Temporal / Indefinido / Servicios Profesionales
    # "Sucursal" o "Administrativo": decide la plantilla del Word (sucursal = Definido, administración = Indefinido).
    staff_area = Column(String(20), nullable=False, default="Sucursal", server_default="Sucursal")
    salary = Column(EncryptedDecimal, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)                   # obligatoria en Definido y Temporal
    notes = Column(EncryptedText, nullable=True)
    # Lista JSON de {"name", "age", "relationship"}; vacía = "no tiene dependientes".
    dependents_json = Column(EncryptedText, nullable=True)
    # Funciones del cargo, una por línea: van en la cláusula TERCERA del contrato.
    duties = Column(Text, nullable=True)
    document_url = Column(String(500), nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        Index("ix_employee_contract_person", "id_type", "id_number_hash"),
        Index("ix_employee_contract_dates", "start_date", "end_date"),
    )


class EmployeeInvite(Base):
    """
    Enlace de invitación para que UNA persona llene el formulario (/colaborador#t=...). El formulario
    no es abierto al público: sin un enlace vigente no se puede enviar nada.

    - El token (256 bits al azar) se muestra una sola vez al crear la invitación; en la base solo está
      su hash (SHA-256), así que una filtración de la tabla no da enlaces utilizables.
    - Vence (por defecto en 72 h) y se consume al enviar el formulario: sirve una sola vez.
    """
    __tablename__ = "employee_invites"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    label = Column(String(150), nullable=True)               # para quién es (solo para que el admin la reconozca)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)

    __table_args__ = (
        Index("ix_employee_invite_expires", "expires_at"),
    )


class EmployeeIntake(Base):
    """
    Lo que el colaborador llenó en el formulario público: datos personales, persona de contacto, banco
    y dependientes. Es una SOLICITUD, no un contrato: el administrador la revisa en Contratos, le pone
    puesto, salario y fechas, y la convierte en contrato. Al convertirla o descartarla la solicitud se
    BORRA (los datos ya viven en el contrato; no se guardan dos copias). Una solicitud que nadie
    atiende en 30 días también se borra.

    No guarda la IP ni nada del navegador de quien la envía: solo lo que él escribió.
    """
    __tablename__ = "employee_intakes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invite_id = Column(Integer, ForeignKey("employee_invites.id", ondelete="SET NULL"), nullable=True)
    # Id del sobre en el servicio público: importar dos veces el mismo (si falló el aviso de recibido) es imposible.
    remote_id = Column(Integer, nullable=True, unique=True)

    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    birth_date = Column(EncryptedDate, nullable=False)
    gender = Column(String(1), nullable=False)
    nationality = Column(String(60), nullable=False)
    marital_status = Column(String(20), nullable=False)
    blood_type = Column(EncryptedText, nullable=False)
    id_type = Column(String(20), nullable=False)
    id_number = Column(EncryptedText, nullable=False)
    id_number_hash = Column(String(64), nullable=False)
    dv = Column(EncryptedText, nullable=True)
    phone = Column(EncryptedText, nullable=False)
    email = Column(EncryptedText, nullable=False)
    address = Column(EncryptedText, nullable=False)
    emergency_contact_name = Column(EncryptedText, nullable=False)
    emergency_contact_phone = Column(EncryptedText, nullable=False)
    emergency_contact_relationship = Column(EncryptedText, nullable=False)
    bank_name = Column(EncryptedText, nullable=False)
    account_type = Column(String(20), nullable=False)
    account_number = Column(EncryptedText, nullable=False)
    dependents_json = Column(EncryptedText, nullable=True)

    created_at = Column(DateTime, default=_now, nullable=False)

    __table_args__ = (
        Index("ix_employee_intake_created", "created_at"),
    )
