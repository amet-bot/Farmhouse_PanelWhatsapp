import json
import logging
from datetime import date as date_cls, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.prep import PrepTemplate, PrepTemplateItem, PrepCheck, PrepCheckEntry
from models.user import User
from schemas.prep import (
    PrepTemplateCreate, PrepTemplateResponse, PrepTemplateSummary, PrepTemplateItemResponse,
    PrepCheckSubmit, PrepCheckResponse, PrepCheckEntryResponse,
)
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid
from services.audit import log_audit_event

logger = logging.getLogger("farmhouse.prep")

router = APIRouter(prefix="/prep", tags=["Prep"])


def _is_global(current_user: User) -> bool:
    return current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None)


def _require_own_branch_or_admin(current_user: User, branch_id: int, detail: str):
    if not _is_global(current_user) and current_user.branch_id != branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _require_can_edit_template(current_user: User, branch_id: int):
    """
    Editar la plantilla (los pars) es cosa de supervisor/admin — un agente puede llenar el
    checklist del día, pero no redefinir cuánto tiene que haber de cada cosa.
    """
    if current_user.role == "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un supervisor o administrador puede editar la plantilla de prep."
        )
    _require_own_branch_or_admin(current_user, branch_id, "No tienes permiso para editar la plantilla de prep de otra sucursal.")


def _visible_branch_filter(current_user: User, branch_id: Optional[int]):
    if _is_global(current_user):
        return branch_id
    return current_user.branch_id


def _serialize_template(template: PrepTemplate) -> PrepTemplateResponse:
    return PrepTemplateResponse(
        id=template.id,
        branch_id=template.branch_id,
        branch_name=template.branch.name,
        name=template.name,
        checkpoints=json.loads(template.checkpoints_json),
        items=[
            PrepTemplateItemResponse(
                id=i.id, section=i.section, name=i.name, unit_label=i.unit_label,
                par_target=i.par_target, notes=i.notes, sort_order=i.sort_order,
            ) for i in template.items
        ],
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _get_template_or_404(db: Session, template_id: int) -> PrepTemplate:
    template = db.query(PrepTemplate).options(
        joinedload(PrepTemplate.items), joinedload(PrepTemplate.branch)
    ).filter(PrepTemplate.id == template_id, PrepTemplate.active == "Y").first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plantilla de prep no encontrada.")
    return template


@router.post("/templates", response_model=PrepTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    template_in: PrepTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _require_can_edit_template(current_user, template_in.branch_id)
    check_target_branch_valid(db, template_in.branch_id)

    if not template_in.checkpoints or any(not c.strip() for c in template_in.checkpoints):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Los checkpoints no pueden estar vacíos.")

    template = PrepTemplate(
        branch_id=template_in.branch_id,
        name=template_in.name,
        checkpoints_json=json.dumps(template_in.checkpoints),
        active="Y",
    )
    for idx, item in enumerate(template_in.items):
        template.items.append(PrepTemplateItem(
            section=item.section, name=item.name, unit_label=item.unit_label,
            par_target=item.par_target, notes=item.notes, sort_order=idx,
        ))

    db.add(template)
    db.flush()
    log_audit_event(db, current_user.id, template.branch_id, "prep_template.create", "prep_template", template.id, {"items": len(template.items)})
    db.commit()
    db.refresh(template)
    logger.info(f"Plantilla de prep #{template.id} ({template.name}) creada en sucursal {template.branch_id} por {current_user.name}")
    return _serialize_template(template)


@router.get("/templates", response_model=List[PrepTemplateSummary])
def list_templates(
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(PrepTemplate).options(joinedload(PrepTemplate.items), joinedload(PrepTemplate.branch)).filter(PrepTemplate.active == "Y")
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter(PrepTemplate.branch_id == efectiva)
    templates = query.order_by(PrepTemplate.name).all()
    return [
        PrepTemplateSummary(
            id=t.id, branch_id=t.branch_id, branch_name=t.branch.name, name=t.name,
            checkpoints=json.loads(t.checkpoints_json), item_count=len(t.items),
        ) for t in templates
    ]


@router.get("/templates/{template_id}", response_model=PrepTemplateResponse)
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    template = _get_template_or_404(db, template_id)
    _require_own_branch_or_admin(current_user, template.branch_id, "No tienes permiso para ver esta plantilla de prep.")
    return _serialize_template(template)


@router.put("/templates/{template_id}", response_model=PrepTemplateResponse)
def update_template(
    template_id: int,
    template_in: PrepTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Reemplaza nombre, checkpoints e ítems completos — editar la plantilla es poco frecuente, no vale la pena un diff fino."""
    template = _get_template_or_404(db, template_id)
    _require_can_edit_template(current_user, template.branch_id)
    if template_in.branch_id != template.branch_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No se puede mover una plantilla a otra sucursal.")
    if not template_in.checkpoints or any(not c.strip() for c in template_in.checkpoints):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Los checkpoints no pueden estar vacíos.")

    template.name = template_in.name
    template.checkpoints_json = json.dumps(template_in.checkpoints)
    template.items.clear()
    db.flush()
    for idx, item in enumerate(template_in.items):
        template.items.append(PrepTemplateItem(
            section=item.section, name=item.name, unit_label=item.unit_label,
            par_target=item.par_target, notes=item.notes, sort_order=idx,
        ))
    log_audit_event(db, current_user.id, template.branch_id, "prep_template.update", "prep_template", template.id, {"items": len(template_in.items)})
    db.commit()
    db.refresh(template)
    return _serialize_template(template)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    template = _get_template_or_404(db, template_id)
    _require_can_edit_template(current_user, template.branch_id)
    template.active = "N"
    log_audit_event(db, current_user.id, template.branch_id, "prep_template.delete", "prep_template", template.id)
    db.commit()
    return None


# ==========================================================================
# Checklist diario
# ==========================================================================
def _serialize_check(check: PrepCheck) -> PrepCheckResponse:
    entries = []
    for entry in check.entries:
        item = entry.template_item
        par = item.par_target
        ready = par is None or Decimal(entry.on_hand) >= Decimal(par)
        entries.append(PrepCheckEntryResponse(
            template_item_id=item.id, item_name=item.name, section=item.section,
            unit_label=item.unit_label, par_target=par, on_hand=entry.on_hand,
            note=entry.note, ready=ready,
        ))
    return PrepCheckResponse(
        id=check.id, template_id=check.template_id, branch_id=check.branch_id,
        checkpoint=check.checkpoint, check_date=check.check_date,
        filled_by_user_id=check.filled_by_user_id, filled_by_name=check.filled_by_user.name,
        entries=entries, created_at=check.created_at, updated_at=check.updated_at,
    )


@router.post("/templates/{template_id}/checks", response_model=PrepCheckResponse, status_code=status.HTTP_201_CREATED)
def submit_check(
    template_id: int,
    submission: PrepCheckSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Llena (o corrige, si ya existía) un checkpoint del día. Volver a mandar el mismo
    plantilla+checkpoint+día actualiza las cantidades en vez de duplicar el registro.
    """
    template = _get_template_or_404(db, template_id)
    _require_own_branch_or_admin(current_user, template.branch_id, "No tienes permiso para llenar el prep de otra sucursal.")

    checkpoints = json.loads(template.checkpoints_json)
    if submission.checkpoint not in checkpoints:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{submission.checkpoint}' no es un checkpoint de esta plantilla. Válidos: {checkpoints}"
        )

    valid_item_ids = {i.id for i in template.items}
    for entry in submission.entries:
        if entry.template_item_id not in valid_item_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El ítem {entry.template_item_id} no pertenece a esta plantilla."
            )

    check_date = submission.check_date or date_cls.today()
    existing = db.query(PrepCheck).filter(
        PrepCheck.template_id == template_id,
        PrepCheck.checkpoint == submission.checkpoint,
        PrepCheck.check_date == check_date,
    ).first()

    if existing:
        existing.entries.clear()
        db.flush()
        for entry in submission.entries:
            existing.entries.append(PrepCheckEntry(template_item_id=entry.template_item_id, on_hand=entry.on_hand, note=entry.note))
        existing.filled_by_user_id = current_user.id
        check = existing
        action = "prep_check.update"
    else:
        check = PrepCheck(
            template_id=template_id, branch_id=template.branch_id, checkpoint=submission.checkpoint,
            check_date=check_date, filled_by_user_id=current_user.id,
        )
        for entry in submission.entries:
            check.entries.append(PrepCheckEntry(template_item_id=entry.template_item_id, on_hand=entry.on_hand, note=entry.note))
        db.add(check)
        action = "prep_check.create"

    db.flush()
    log_audit_event(db, current_user.id, template.branch_id, action, "prep_check", check.id, {"checkpoint": submission.checkpoint, "entries": len(submission.entries)})
    db.commit()
    db.refresh(check)
    logger.info(f"Prep '{template.name}' checkpoint '{submission.checkpoint}' del {check_date} en sucursal {template.branch_id} por {current_user.name}")
    return _serialize_check(check)


@router.get("/templates/{template_id}/checks", response_model=List[PrepCheckResponse])
def list_checks(
    template_id: int,
    check_date: Optional[date_cls] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    template = _get_template_or_404(db, template_id)
    _require_own_branch_or_admin(current_user, template.branch_id, "No tienes permiso para ver el prep de otra sucursal.")

    query = db.query(PrepCheck).options(
        joinedload(PrepCheck.entries).joinedload(PrepCheckEntry.template_item),
        joinedload(PrepCheck.filled_by_user),
    ).filter(PrepCheck.template_id == template_id)
    if check_date:
        query = query.filter(PrepCheck.check_date == check_date)
    else:
        query = query.filter(PrepCheck.check_date == date_cls.today())
    checks = query.order_by(PrepCheck.checkpoint).all()
    return [_serialize_check(c) for c in checks]
