import re
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

GENDERS = {"M", "F"}
BLOOD_TYPES = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}
ID_TYPES = {"Cedula", "Pasaporte"}
MARITAL_STATUSES = {"Soltero", "Casado", "Unido", "Divorciado", "Viudo"}
ACCOUNT_TYPES = {"Ahorros", "Corriente"}
CONTRACT_TYPES = {"Definido", "Temporal", "Indefinido", "Servicios Profesionales"}
# Los que llevan fecha de vencimiento (y para los que existe la plantilla de contrato en Word).
FIXED_TERM_TYPES = {"Definido", "Temporal"}

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_RE = re.compile(r"^\d{4}-?\d{3,4}$")
_CEDULA_RE = re.compile(r"^(\d{1,2}|E|PE|N|PI|AV)(-\d{1,4}){2}$|^(E|PE|N|PI)-\d{1,4}-\d{1,6}$")
_PASSPORT_RE = re.compile(r"^[A-Z0-9]{5,15}$")


def _normalize_phone(value: str, label: str) -> str:
    value = (value or "").strip()
    if not _PHONE_RE.match(value):
        raise ValueError(f"{label}: ingresa 7 u 8 dígitos (ej. 6123-4567).")
    digits = value.replace("-", "")
    return f"{digits[:4]}-{digits[4:]}"


class Dependent(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    age: Optional[int] = Field(None, ge=0, le=120)
    relationship: str = Field("Dependiente", min_length=1, max_length=40)

    @field_validator("name", "relationship")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("No puede estar vacío.")
        return v


class PersonData(BaseModel):
    """Lo que el colaborador sabe de sí mismo: datos personales, persona de contacto y banco.
    Es lo que llena él mismo en el formulario público (IntakeIn) y la base del contrato (ContractIn)."""
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    birth_date: date
    gender: str
    nationality: str = Field(..., min_length=1, max_length=60)
    marital_status: str
    blood_type: str
    id_type: str
    id_number: str = Field(..., min_length=1, max_length=30)
    dv: Optional[str] = None
    phone: str
    email: str = Field(..., max_length=150)
    address: str = Field(..., min_length=1, max_length=500)

    emergency_contact_name: str = Field(..., min_length=1, max_length=150)
    emergency_contact_phone: str
    emergency_contact_relationship: str = Field(..., min_length=1, max_length=40)

    bank_name: str = Field(..., min_length=1, max_length=80)
    account_type: str
    account_number: str
    dependents: List[Dependent] = Field(default_factory=list, max_length=20)

    @field_validator("first_name", "last_name", "nationality", "address", "bank_name",
                     "emergency_contact_name", "emergency_contact_relationship")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("No puede estar vacío.")
        return v

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: str) -> str:
        if v not in GENDERS:
            raise ValueError("Género inválido (M o F).")
        return v

    @field_validator("blood_type")
    @classmethod
    def _blood(cls, v: str) -> str:
        if v not in BLOOD_TYPES:
            raise ValueError("Tipo de sangre inválido.")
        return v

    @field_validator("marital_status")
    @classmethod
    def _marital(cls, v: str) -> str:
        if v not in MARITAL_STATUSES:
            raise ValueError("Estado civil inválido.")
        return v

    @field_validator("id_type")
    @classmethod
    def _id_type(cls, v: str) -> str:
        if v not in ID_TYPES:
            raise ValueError("Tipo de identificación inválido (Cedula o Pasaporte).")
        return v

    @field_validator("account_type")
    @classmethod
    def _account_type(cls, v: str) -> str:
        if v not in ACCOUNT_TYPES:
            raise ValueError("Tipo de cuenta inválido (Ahorros o Corriente).")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("El correo no tiene un formato válido.")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _normalize_phone(v, "Teléfono")

    @field_validator("emergency_contact_phone")
    @classmethod
    def _contact_phone(cls, v: str) -> str:
        return _normalize_phone(v, "Teléfono del contacto")

    @field_validator("dv")
    @classmethod
    def _dv(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if not v:
            return None
        if not re.fullmatch(r"\d{1,2}", v):
            raise ValueError("El DV solo admite 1 o 2 dígitos.")
        return v

    @field_validator("account_number")
    @classmethod
    def _account(cls, v: str) -> str:
        v = (v or "").strip()
        if not re.fullmatch(r"\d{6,20}", v):
            raise ValueError("La cuenta bancaria debe tener solo dígitos (6 a 20).")
        return v

    @model_validator(mode="after")
    def _person_checks(self):
        self.id_number = self.id_number.strip().upper()
        if self.id_type == "Pasaporte":
            if not _PASSPORT_RE.match(self.id_number):
                raise ValueError("Pasaporte inválido (letras y números, 5 a 15 caracteres).")
        elif not _CEDULA_RE.match(self.id_number):
            raise ValueError("Formato de cédula inválido (ej. 8-123-456).")
        if self.birth_date >= date.today() or self.birth_date.year < 1900:
            raise ValueError("La fecha de nacimiento no es válida.")
        return self


class ContractIn(PersonData):
    position: str = Field(..., min_length=1, max_length=100)
    contract_type: str
    salary: Decimal = Field(..., gt=0, lt=1000000, decimal_places=2)
    start_date: date
    end_date: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=2000)
    duties: Optional[str] = Field(None, max_length=6000)
    document_url: Optional[str] = Field(None, max_length=500)
    # Si el contrato nace de una solicitud que el colaborador llenó, su id: al guardar, la solicitud
    # queda como "convertida" y deja de aparecer en pendientes.
    intake_id: Optional[int] = None

    @field_validator("position")
    @classmethod
    def _strip_position(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("No puede estar vacío.")
        return v

    @field_validator("contract_type")
    @classmethod
    def _contract_type(cls, v: str) -> str:
        if v not in CONTRACT_TYPES:
            raise ValueError("Tipo de contrato inválido.")
        return v

    @field_validator("document_url")
    @classmethod
    def _url(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if not v:
            return None
        if not re.match(r"^https?://", v, re.IGNORECASE):
            raise ValueError("El enlace debe empezar con http:// o https://")
        return v

    @field_validator("notes", "duties")
    @classmethod
    def _optional_text(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None

    @model_validator(mode="after")
    def _contract_checks(self):
        if self.end_date is not None and self.end_date <= self.start_date:
            raise ValueError("La fecha de vencimiento debe ser posterior al inicio.")
        if self.contract_type in FIXED_TERM_TYPES:
            if self.end_date is None:
                raise ValueError("Un contrato Definido o Temporal necesita fecha de vencimiento.")
            if not self.duties:
                raise ValueError("Un contrato Definido o Temporal necesita las funciones del cargo.")
        return self


class IntakeResponse(PersonData):
    """Detalle de una solicitud (solo administrador, queda en auditoría)."""
    id: int
    created_at: datetime


class IntakeSummary(BaseModel):
    """Para la lista: lo justo para reconocer la solicitud, sin cédula completa ni datos bancarios."""
    id: int
    first_name: str
    last_name: str
    id_type: str
    id_number_masked: str
    created_at: datetime
    # True si llegó por el enlace público abierto (p. ej. el de Instagram); False si fue con una invitación personal.
    open_form: bool = False


class ContractSummary(BaseModel):
    """Para la tabla de contratos: nada de datos bancarios, de salud, de contacto ni salario."""
    id: int
    first_name: str
    last_name: str
    id_type: str
    id_number_masked: str
    position: str
    contract_type: str
    start_date: date
    end_date: Optional[date] = None
    document_url: Optional[str] = None


class OpenFormIn(BaseModel):
    enabled: bool


class InviteCreate(BaseModel):
    label: Optional[str] = Field(None, max_length=150)
    hours: int = Field(72, ge=1, le=168)

    @field_validator("label")
    @classmethod
    def _label(cls, v: Optional[str]) -> Optional[str]:
        v = re.sub(r"\s+", " ", v or "").strip()
        return v or None


class InviteResponse(BaseModel):
    id: int
    label: Optional[str] = None
    expires_at: datetime
    created_at: datetime
    # Solo viene al crearla: después no hay forma de recuperarlo (aquí solo queda el hash del token).
    link: Optional[str] = None


class ContractResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    birth_date: date
    gender: str
    nationality: str
    marital_status: str
    blood_type: str
    id_type: str
    id_number: str
    dv: Optional[str] = None
    phone: str
    email: str
    address: str
    emergency_contact_name: str
    emergency_contact_phone: str
    emergency_contact_relationship: str
    bank_name: str
    account_type: str
    account_number: str
    position: str
    contract_type: str
    salary: Decimal
    start_date: date
    end_date: Optional[date] = None
    notes: Optional[str] = None
    dependents: List[Dependent] = Field(default_factory=list)
    duties: Optional[str] = None
    document_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
