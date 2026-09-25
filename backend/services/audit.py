import json
from typing import Optional
from sqlalchemy.orm import Session

from models.audit import AuditEvent


def log_audit_event(
    db: Session,
    actor_user_id: Optional[int],
    branch_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    """
    Agrega el evento a la sesión sin hacer commit — se guarda junto con la escritura que lo
    origina (mismo commit atómico), no aparte. El caller decide cuándo confirmar.
    """
    db.add(AuditEvent(
        actor_user_id=actor_user_id,
        branch_id=branch_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata_json=json.dumps(metadata) if metadata is not None else None,
    ))
