"""
Contratos de colaboradores (página /contratos).

Seguridad (son cédulas, cuentas bancarias, salarios y datos de salud de todo el personal):
- Todo exige el permiso `contracts.manage` (administrador y Recursos Humanos).
- Los datos sensibles van cifrados en la base (services/field_crypto.py). Las listas devuelven un
  resumen (sin banco, salud, contacto ni salario, y con la cédula enmascarada); el detalle completo
  solo sale al abrir un contrato o solicitud, y cada lectura queda en auditoría.
- El formulario que llena el colaborador NO vive aquí: es un servicio aparte (carpeta public_intake/,
  otro proyecto) sin ninguna conexión a este sistema. Los datos viajan cifrados desde el navegador del
  colaborador con una llave pública; este sistema va a BUSCARLOS (no recibe nada de internet) y los
  abre con la llave privada, que solo existe aquí (services/intake_client.py, intake_crypto.py).
- Las invitaciones son enlaces de un solo uso con vencimiento; aquí y en el servicio público solo
  queda el hash del token.
- Ninguna respuesta se guarda en caché (Cache-Control: no-store).
- La auditoría guarda quién y qué, nunca cédulas, cuentas ni salarios.
"""
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models.contract import EmployeeContract, EmployeeIntake, EmployeeInvite
from models.user import User
from schemas.contract import (
    ContractIn, ContractResponse, ContractSummary, Dependent, IntakeResponse, IntakeSummary,
    InviteCreate, InviteResponse, OpenFormIn, PersonData,
)
from security.permissions import require_permission
from services import intake_client, intake_crypto
from services.audit import log_audit_event
from services.field_crypto import blind_index, mask_id
from services.intake_client import IntakeServiceError
from services.intake_crypto import IntakeCryptoError, IntakeKeyError

logger = logging.getLogger("farmhouse.contracts")


def _no_store(response: Response) -> None:
    # Datos personales: ni el navegador, ni un proxy, ni el botón "atrás" deben guardarlos.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(prefix="/contracts", tags=["Contratos"], dependencies=[Depends(_no_store)])

INTAKE_RETENTION_DAYS = 30          # una solicitud que nadie atiende se borra a los 30 días
INVITE_PURGE_DAYS = 7               # las invitaciones usadas o vencidas se limpian a los 7 días

_ADMIN = Depends(require_permission("contracts.manage"))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==========================================================================
# Serialización
# ==========================================================================
def _deps(raw: Optional[str]) -> List[Dependent]:
    try:
        return [Dependent(**d) for d in (json.loads(raw) if raw else [])]
    except (ValueError, TypeError):
        return []


_PERSON_FIELDS = [
    "first_name", "last_name", "birth_date", "gender", "nationality", "marital_status", "blood_type",
    "id_type", "id_number", "dv", "phone", "email", "address",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship",
    "bank_name", "account_type", "account_number",
]


def _set_person(obj, data: PersonData) -> None:
    for field in _PERSON_FIELDS:
        setattr(obj, field, getattr(data, field))
    obj.id_number_hash = blind_index(data.id_number)
    obj.dependents_json = json.dumps([d.model_dump() for d in data.dependents], ensure_ascii=False) if data.dependents else None


def _person_dict(obj) -> dict:
    out = {f: getattr(obj, f) for f in _PERSON_FIELDS}
    out["dependents"] = _deps(obj.dependents_json)
    return out


def _contract_detail(c: EmployeeContract) -> ContractResponse:
    return ContractResponse(
        id=c.id, **_person_dict(c),
        position=c.position, contract_type=c.contract_type, staff_area=c.staff_area or "Sucursal", salary=c.salary,
        start_date=c.start_date, end_date=c.end_date, notes=c.notes, duties=c.duties,
        document_url=c.document_url, created_at=c.created_at, updated_at=c.updated_at,
    )


def _contract_summary(c: EmployeeContract) -> ContractSummary:
    return ContractSummary(
        id=c.id, first_name=c.first_name, last_name=c.last_name, id_type=c.id_type,
        id_number_masked=mask_id(c.id_number), position=c.position, contract_type=c.contract_type,
        staff_area=c.staff_area or "Sucursal", start_date=c.start_date, end_date=c.end_date, document_url=c.document_url,
    )


def _intake_detail(i: EmployeeIntake) -> IntakeResponse:
    return IntakeResponse(id=i.id, created_at=i.created_at, **_person_dict(i))


def _intake_summary(i: EmployeeIntake) -> IntakeSummary:
    return IntakeSummary(
        id=i.id, first_name=i.first_name, last_name=i.last_name, id_type=i.id_type,
        id_number_masked=mask_id(i.id_number), created_at=i.created_at, open_form=i.invite_id is None,
    )


def _full_name(obj) -> str:
    return f"{obj.first_name} {obj.last_name}"


def _audit(db: Session, user: Optional[User], action: str, entity: str, entity_id: Optional[int] = None, meta: Optional[dict] = None) -> None:
    log_audit_event(db, user.id if user else None, None, action, entity, entity_id, meta)


def _purge(db: Session) -> None:
    """Retención mínima: solicitudes sin atender (30 días) e invitaciones usadas o vencidas (7 días)."""
    now = _now()
    old_intakes = db.query(EmployeeIntake).filter(EmployeeIntake.created_at < now - timedelta(days=INTAKE_RETENTION_DAYS))
    n_intakes = old_intakes.count()
    if n_intakes:
        old_intakes.delete(synchronize_session=False)
        _audit(db, None, "intake.purge", "employee_intake", None, {"count": n_intakes})
    cutoff = now - timedelta(days=INVITE_PURGE_DAYS)
    db.query(EmployeeInvite).filter(or_(EmployeeInvite.expires_at < cutoff, EmployeeInvite.used_at < cutoff)).delete(synchronize_session=False)
    db.commit()


def _unavailable(exc: IntakeServiceError) -> HTTPException:
    code = status.HTTP_503_SERVICE_UNAVAILABLE if not intake_client.is_configured() else status.HTTP_502_BAD_GATEWAY
    return HTTPException(status_code=code, detail=str(exc))


# ==========================================================================
# Formulario público (servicio aparte): este sistema va a buscar lo que llenaron
# ==========================================================================
def _ack(remote_id: int) -> None:
    try:
        intake_client.ack_submission(remote_id)
    except IntakeServiceError:
        # No pasa nada: `remote_id` es único, así que el mismo sobre nunca se importa dos veces.
        logger.warning("[Intake] No se pudo avisar de recibido el sobre %s; se reintenta en la próxima.", remote_id)


def sync_intakes(db: Session) -> dict:
    """
    Trae los sobres cifrados del servicio público, los abre, valida cada dato con las mismas reglas del
    contrato y los guarda como solicitudes; luego los borra del servicio. Devuelve
    {"configured", "ok", "imported", "rejected"}. Un fallo del servicio no rompe nada: se reintenta.
    """
    result = {"configured": intake_client.is_configured() and intake_crypto.is_configured(), "ok": True, "imported": 0, "rejected": 0}
    if not result["configured"]:
        return result
    try:
        submissions = intake_client.fetch_submissions()
    except IntakeServiceError:
        result["ok"] = False
        return result
    for sub in submissions:
        remote_id = sub.get("id")
        if not isinstance(remote_id, int):
            continue
        if db.query(EmployeeIntake.id).filter(EmployeeIntake.remote_id == remote_id).first():
            _ack(remote_id)
            continue
        try:
            data = PersonData.model_validate(intake_crypto.decrypt_envelope(sub["envelope"]))
        except IntakeKeyError:
            # Problema de configuración, no del sobre: no se descarta nada, se avisa y se deja para después.
            logger.error("[Intake] La llave privada no es válida: no se importa nada.")
            result["ok"] = False
            return result
        except (IntakeCryptoError, ValidationError, KeyError, TypeError):
            # Un sobre alterado o con datos que el formulario real nunca manda (alguien lo armó a mano): se descarta.
            _audit(db, None, "intake.rejected", "employee_intake", None, {"remote_id": remote_id})
            db.commit()
            _ack(remote_id)
            result["rejected"] += 1
            continue
        invite = db.query(EmployeeInvite).filter(EmployeeInvite.token_hash == sub.get("invite_hash")).first()
        intake = EmployeeIntake(invite_id=invite.id if invite else None, remote_id=remote_id)
        _set_person(intake, data)
        if invite is not None and invite.used_at is None:
            invite.used_at = _now()
        db.add(intake)
        db.flush()
        _audit(db, None, "intake.submit", "employee_intake", intake.id, {"invite_id": invite.id if invite else None})
        db.commit()
        _ack(remote_id)
        result["imported"] += 1
    return result


@router.post("/intakes/sync")
def sync_now(db: Session = Depends(get_db), current_user: User = _ADMIN):
    return sync_intakes(db)


# ==========================================================================
# Administración: formulario ABIERTO (el enlace que se publica, p. ej. en Instagram)
# ==========================================================================
def _open_form_payload(state: dict) -> dict:
    return {**state, "link": intake_client.open_form_link()}


@router.get("/open-form")
def get_open_form(current_user: User = _ADMIN):
    try:
        return _open_form_payload(intake_client.get_open_form())
    except IntakeServiceError as exc:
        raise _unavailable(exc)


@router.put("/open-form")
def set_open_form(body: OpenFormIn, db: Session = Depends(get_db), current_user: User = _ADMIN):
    try:
        state = intake_client.set_open_form(body.enabled)
    except IntakeServiceError as exc:
        raise _unavailable(exc)
    _audit(db, current_user, "openform.enable" if body.enabled else "openform.disable", "employee_invite", None)
    db.commit()
    return _open_form_payload(state)


# ==========================================================================
# Administración: invitaciones
# ==========================================================================
@router.post("/invites", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
def create_invite(data: InviteCreate, db: Session = Depends(get_db), current_user: User = _ADMIN):
    _purge(db)
    token = secrets.token_urlsafe(32)
    token_hash = intake_client.hash_token(token)
    expires_at = _now() + timedelta(hours=data.hours)
    try:
        # Al servicio público solo viaja el HASH: ni él conoce el token hasta que la persona lo usa.
        intake_client.register_invite(token_hash, data.label, expires_at)
    except IntakeServiceError as exc:
        raise _unavailable(exc)
    invite = EmployeeInvite(token_hash=token_hash, label=data.label, created_by_user_id=current_user.id, expires_at=expires_at)
    db.add(invite)
    db.flush()
    _audit(db, current_user, "invite.create", "employee_invite", invite.id, {"hours": data.hours})
    db.commit()
    db.refresh(invite)
    return InviteResponse(id=invite.id, label=invite.label, expires_at=invite.expires_at, created_at=invite.created_at,
                          link=intake_client.invite_link(token))


@router.get("/invites", response_model=List[InviteResponse])
def list_invites(db: Session = Depends(get_db), current_user: User = _ADMIN):
    _purge(db)
    rows = db.query(EmployeeInvite).filter(EmployeeInvite.used_at.is_(None), EmployeeInvite.expires_at > _now()) \
        .order_by(EmployeeInvite.created_at.desc()).all()
    return [InviteResponse(id=r.id, label=r.label, expires_at=r.expires_at, created_at=r.created_at) for r in rows]


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(invite_id: int, db: Session = Depends(get_db), current_user: User = _ADMIN):
    invite = db.query(EmployeeInvite).filter(EmployeeInvite.id == invite_id).first()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitación no encontrada.")
    if intake_client.is_configured():
        try:
            intake_client.revoke_invite(invite.token_hash)
        except IntakeServiceError:
            # Si no se pudo anular allá, el enlace seguiría funcionando: no se borra acá para poder reintentar.
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                                detail="No se pudo anular en el formulario público. El enlace sigue vigente: intenta de nuevo.")
    _audit(db, current_user, "invite.revoke", "employee_invite", invite.id)
    db.delete(invite)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ==========================================================================
# Administración: solicitudes (lo que llenaron los colaboradores)
# ==========================================================================
@router.get("/intakes", response_model=List[IntakeSummary])
def list_intakes(db: Session = Depends(get_db), current_user: User = _ADMIN):
    _purge(db)
    rows = db.query(EmployeeIntake).order_by(EmployeeIntake.created_at.asc(), EmployeeIntake.id.asc()).all()
    _audit(db, current_user, "intake.list", "employee_intake", None, {"count": len(rows)})
    db.commit()
    return [_intake_summary(i) for i in rows]


@router.get("/intakes/{intake_id}", response_model=IntakeResponse)
def get_intake(intake_id: int, db: Session = Depends(get_db), current_user: User = _ADMIN):
    i = db.query(EmployeeIntake).filter(EmployeeIntake.id == intake_id).first()
    if not i:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada.")
    _audit(db, current_user, "intake.read", "employee_intake", i.id)
    db.commit()
    return _intake_detail(i)


@router.delete("/intakes/{intake_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_intake(intake_id: int, db: Session = Depends(get_db), current_user: User = _ADMIN):
    i = db.query(EmployeeIntake).filter(EmployeeIntake.id == intake_id).first()
    if not i:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada.")
    _audit(db, current_user, "intake.dismiss", "employee_intake", i.id)
    db.delete(i)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ==========================================================================
# Administración: contratos
# ==========================================================================
def _reject_duplicate(db: Session, data: ContractIn, ignore_id: Optional[int] = None) -> None:
    """El mismo contrato (misma identificación y misma fecha de inicio) no se carga dos veces."""
    q = db.query(EmployeeContract.id).filter(
        EmployeeContract.id_type == data.id_type,
        EmployeeContract.id_number_hash == blind_index(data.id_number),
        EmployeeContract.start_date == data.start_date,
    )
    if ignore_id is not None:
        q = q.filter(EmployeeContract.id != ignore_id)
    if q.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya hay un contrato de esta persona con la misma fecha de inicio.")


def _apply_contract(c: EmployeeContract, data: ContractIn) -> None:
    _set_person(c, data)
    c.position = data.position
    c.contract_type = data.contract_type
    c.staff_area = data.staff_area
    c.salary = data.salary
    c.start_date = data.start_date
    c.end_date = data.end_date
    c.notes = data.notes
    c.duties = data.duties
    c.document_url = data.document_url


def _get_or_404(db: Session, contract_id: int) -> EmployeeContract:
    c = db.query(EmployeeContract).filter(EmployeeContract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrato no encontrado.")
    return c


@router.get("", response_model=List[ContractSummary])
def list_contracts(
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = _ADMIN,
):
    query = db.query(EmployeeContract)
    if q and q.strip():
        like = f"%{q.strip()}%"
        conds = [EmployeeContract.first_name.like(like), EmployeeContract.last_name.like(like), EmployeeContract.position.like(like)]
        h = blind_index(q)
        if h:
            conds.append(EmployeeContract.id_number_hash == h)   # la cédula (cifrada) se busca exacta por su índice ciego
        query = query.filter(or_(*conds))
    rows = query.order_by(EmployeeContract.start_date.asc(), EmployeeContract.id.asc()).limit(limit).all()
    _audit(db, current_user, "contract.list", "employee_contract", None, {"count": len(rows)})
    db.commit()
    return [_contract_summary(c) for c in rows]


@router.get("/export", response_model=List[ContractResponse])
def export_contracts(db: Session = Depends(get_db), current_user: User = _ADMIN):
    """Todos los contratos con todos sus datos (para el CSV). Queda en auditoría."""
    rows = db.query(EmployeeContract).order_by(EmployeeContract.start_date.asc(), EmployeeContract.id.asc()).all()
    _audit(db, current_user, "contract.export", "employee_contract", None, {"count": len(rows)})
    db.commit()
    return [_contract_detail(c) for c in rows]


@router.post("", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
def create_contract(data: ContractIn, db: Session = Depends(get_db), current_user: User = _ADMIN):
    _reject_duplicate(db, data)
    intake = None
    if data.intake_id is not None:
        intake = db.query(EmployeeIntake).filter(EmployeeIntake.id == data.intake_id).first()
        if not intake:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Esa solicitud ya no existe (alguien la atendió o venció).")
    c = EmployeeContract(created_by_user_id=current_user.id)
    _apply_contract(c, data)
    db.add(c)
    db.flush()
    _audit(db, current_user, "contract.create", "employee_contract", c.id, {"name": _full_name(c), "contract_type": c.contract_type})
    if intake is not None:
        # Los datos ya viven en el contrato: la solicitud no se queda como segunda copia.
        _audit(db, current_user, "intake.convert", "employee_intake", intake.id, {"contract_id": c.id})
        db.delete(intake)
    db.commit()
    db.refresh(c)
    return _contract_detail(c)


@router.get("/{contract_id}", response_model=ContractResponse)
def get_contract(contract_id: int, db: Session = Depends(get_db), current_user: User = _ADMIN):
    c = _get_or_404(db, contract_id)
    _audit(db, current_user, "contract.read", "employee_contract", c.id)
    db.commit()
    return _contract_detail(c)


@router.put("/{contract_id}", response_model=ContractResponse)
def update_contract(contract_id: int, data: ContractIn, db: Session = Depends(get_db), current_user: User = _ADMIN):
    c = _get_or_404(db, contract_id)
    _reject_duplicate(db, data, ignore_id=c.id)
    _apply_contract(c, data)
    _audit(db, current_user, "contract.update", "employee_contract", c.id, {"name": _full_name(c), "contract_type": c.contract_type})
    db.commit()
    db.refresh(c)
    return _contract_detail(c)


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(contract_id: int, db: Session = Depends(get_db), current_user: User = _ADMIN):
    c = _get_or_404(db, contract_id)
    _audit(db, current_user, "contract.delete", "employee_contract", c.id, {"name": _full_name(c)})
    db.delete(c)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
