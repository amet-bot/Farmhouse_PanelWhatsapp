import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status,
)
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal, get_db
from models.branch import Branch
from models.internal_chat import InternalThread, InternalParticipant, InternalMessage
from models.user import User
from schemas.internal_chat import (
    DirectThreadCreate, InternalMessageCreate, InternalMessageResponse,
    InternalThreadDetail, InternalThreadResponse, InternalUserBrief,
)
from security.auth import get_current_authorized_user
from services import push_service
from services.media_storage import (
    ALLOWED_ATTACHMENT_MIMES, MAX_ATTACHMENT_BYTES, save_internal_attachment,
)
from services.websocket_manager import ws_manager

logger = logging.getLogger("farmhouse.internal")

router = APIRouter(prefix="/internal", tags=["Comunicación Interna"])

PREVIEW_LENGTH = 90


# ==========================================================================
# Ayudantes
# ==========================================================================
def _brief(user: User, online_ids: set) -> InternalUserBrief:
    return InternalUserBrief(
        id=user.id,
        name=user.name,
        role=user.role,
        branch_id=user.branch_id,
        branch_name=(user.branch.name if user.branch else None),
        online=user.id in online_ids,
    )


def ensure_branch_thread(db: Session, user: User) -> Optional[InternalThread]:
    """
    Garantiza que exista el canal del equipo de la sucursal del usuario y que TODOS los
    activos de esa sucursal figuren como participantes.

    Se sincroniza acá, al abrir la bandeja, en vez de crear el canal por migración o al dar
    de alta a alguien: así dar de alta un agente nuevo no obliga a acordarse de meterlo en
    ningún lado, y una sucursal que todavía no usó el sistema no arrastra un hilo vacío.
    Un admin o supervisor global no tiene sucursal propia, así que no tiene canal de equipo.
    """
    if not user.branch_id:
        return None

    thread = db.query(InternalThread).filter(
        InternalThread.kind == "branch",
        InternalThread.branch_id == user.branch_id,
    ).first()

    if not thread:
        thread = InternalThread(kind="branch", branch_id=user.branch_id)
        db.add(thread)
        db.flush()

    member_ids = {
        row.id for row in
        db.query(User.id).filter(User.branch_id == user.branch_id, User.active == True).all()
    }
    existing_ids = {
        row.user_id for row in
        db.query(InternalParticipant.user_id).filter(InternalParticipant.thread_id == thread.id).all()
    }
    for missing in member_ids - existing_ids:
        db.add(InternalParticipant(thread_id=thread.id, user_id=missing))

    db.commit()
    db.refresh(thread)
    return thread


def _preview(message: InternalMessage) -> str:
    """
    Lo que se lee en la bandeja. Un adjunto sin texto no puede quedar como una fila en blanco,
    así que lo describe: el nombre del archivo dice más que "archivo adjunto".
    """
    if message.body:
        body = message.body
        return body if len(body) <= PREVIEW_LENGTH else body[:PREVIEW_LENGTH].rstrip() + "…"
    if message.media_url:
        if (message.media_mime_type or "").startswith("image/"):
            return "📷 Foto"
        return f"📎 {message.media_name or 'Archivo'}"
    return ""


def _message_response(message: InternalMessage, sender_name: str) -> InternalMessageResponse:
    """
    Único lugar donde un InternalMessage se vuelve respuesta.

    Estaba escrito a mano en dos endpoints (el historial y el envío) y al aparecer los adjuntos
    una de las dos copias se quedó sin los campos nuevos: el mensaje recién mandado mostraba la
    foto y el mismo mensaje, al recargar, aparecía como una burbuja vacía.
    """
    return InternalMessageResponse(
        id=message.id,
        thread_id=message.thread_id,
        sender_user_id=message.sender_user_id,
        sender_name=sender_name,
        body=message.body,
        created_at=message.created_at,
        media_url=message.media_url,
        media_mime_type=message.media_mime_type,
        media_name=message.media_name,
        media_size=message.media_size,
    )


def _participant_or_403(db: Session, thread_id: int, user: User) -> InternalParticipant:
    participant = db.query(InternalParticipant).filter(
        InternalParticipant.thread_id == thread_id,
        InternalParticipant.user_id == user.id,
    ).first()
    if not participant:
        # Mismo mensaje exista o no el hilo: un 404 distinto del 403 delataría qué ids existen.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés acceso a esta conversación.",
        )
    return participant


def _serialize_thread(db: Session, thread: InternalThread, me: User, online_ids: set) -> InternalThreadResponse:
    last = db.query(InternalMessage).filter(
        InternalMessage.thread_id == thread.id
    ).order_by(InternalMessage.created_at.desc(), InternalMessage.id.desc()).first()

    participant = next((p for p in thread.participants if p.user_id == me.id), None)
    unread_q = db.query(func.count(InternalMessage.id)).filter(
        InternalMessage.thread_id == thread.id,
        InternalMessage.sender_user_id != me.id,
    )
    if participant and participant.last_read_at:
        unread_q = unread_q.filter(InternalMessage.created_at > participant.last_read_at)
    unread = unread_q.scalar() or 0

    preview = None
    sender_name = None
    if last:
        preview = _preview(last)
        sender_name = "Vos" if last.sender_user_id == me.id else last.sender.name.split(" ")[0]

    if thread.kind == "branch":
        members = [p for p in thread.participants]
        return InternalThreadResponse(
            id=thread.id,
            kind="branch",
            title=f"Equipo {thread.branch.name}" if thread.branch else "Equipo",
            subtitle=f"{len(members)} integrantes",
            branch_id=thread.branch_id,
            member_count=len(members),
            last_message_at=thread.last_message_at,
            last_message_preview=preview,
            last_message_sender=sender_name,
            unread_count=unread,
        )

    other = next((p.user for p in thread.participants if p.user_id != me.id), None)
    subtitle = None
    if other:
        subtitle = other.branch.name if other.branch else "Sin sucursal"
        if other.role != "agent":
            subtitle = f"{subtitle} · {other.role.capitalize()}"

    return InternalThreadResponse(
        id=thread.id,
        kind="direct",
        title=other.name if other else "Conversación",
        subtitle=subtitle,
        branch_id=(other.branch_id if other else None),
        counterpart=(_brief(other, online_ids) if other else None),
        last_message_at=thread.last_message_at,
        last_message_preview=preview,
        last_message_sender=sender_name,
        unread_count=unread,
    )


# ==========================================================================
# Directorio
# ==========================================================================
@router.get("/directory", response_model=List[InternalUserBrief])
def get_directory(
    q: str = Query("", max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Todo el personal activo, de cualquier sucursal, menos uno mismo.

    Endpoint propio y no /users/, que le recorta la lista a los agentes dejándoles ver solo
    su propia sucursal: acá el objetivo es justamente que Costa del Este pueda escribirle a
    Clayton. Devuelve lo justo para una fila del directorio, nunca correos ni hashes.
    """
    query = db.query(User).options(joinedload(User.branch)).filter(
        User.active == True,
        User.id != current_user.id,
    )
    q = q.strip()
    if q:
        query = query.filter(User.name.ilike(f"%{q}%"))

    users = query.order_by(User.name.asc()).limit(200).all()
    online_ids = ws_manager.online_user_ids()
    return [_brief(u, online_ids) for u in users]


# ==========================================================================
# Hilos
# ==========================================================================
@router.get("/threads", response_model=List[InternalThreadResponse])
def list_threads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    ensure_branch_thread(db, current_user)

    threads = db.query(InternalThread).join(
        InternalParticipant, InternalParticipant.thread_id == InternalThread.id
    ).filter(
        InternalParticipant.user_id == current_user.id
    ).options(
        joinedload(InternalThread.participants).joinedload(InternalParticipant.user).joinedload(User.branch),
        joinedload(InternalThread.branch),
    ).all()

    online_ids = ws_manager.online_user_ids()
    items = [_serialize_thread(db, t, current_user, online_ids) for t in threads]
    # Primero lo que tiene actividad, y el canal del equipo arriba cuando nadie escribió aún.
    items.sort(key=lambda t: (t.last_message_at is None, -(t.last_message_at.timestamp() if t.last_message_at else 0)))
    return items


@router.post("/threads/direct", response_model=InternalThreadResponse, status_code=status.HTTP_201_CREATED)
def open_direct_thread(
    payload: DirectThreadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Abre (o recupera) el hilo directo con otra persona. Idempotente a propósito."""
    if payload.user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No podés abrir un chat con vos mismo.")

    other = db.query(User).filter(User.id == payload.user_id, User.active == True).first()
    if not other:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Esa persona no existe o está inactiva.")

    mine = db.query(InternalParticipant.thread_id).filter(InternalParticipant.user_id == current_user.id).subquery()
    existing = db.query(InternalThread).join(
        InternalParticipant, InternalParticipant.thread_id == InternalThread.id
    ).filter(
        InternalThread.kind == "direct",
        InternalParticipant.user_id == other.id,
        InternalThread.id.in_(db.query(mine.c.thread_id)),
    ).first()

    if not existing:
        existing = InternalThread(kind="direct")
        db.add(existing)
        db.flush()
        db.add(InternalParticipant(thread_id=existing.id, user_id=current_user.id))
        db.add(InternalParticipant(thread_id=existing.id, user_id=other.id))
        db.commit()
        db.refresh(existing)

    return _serialize_thread(db, existing, current_user, ws_manager.online_user_ids())


@router.get("/threads/{thread_id}", response_model=InternalThreadDetail)
def get_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _participant_or_403(db, thread_id, current_user)
    thread = db.query(InternalThread).filter(InternalThread.id == thread_id).first()
    online_ids = ws_manager.online_user_ids()
    base = _serialize_thread(db, thread, current_user, online_ids)
    return InternalThreadDetail(
        **base.model_dump(),
        members=[_brief(p.user, online_ids) for p in thread.participants],
    )


# ==========================================================================
# Mensajes
# ==========================================================================
@router.get("/threads/{thread_id}/messages", response_model=List[InternalMessageResponse])
def list_messages(
    thread_id: int,
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _participant_or_403(db, thread_id, current_user)

    query = db.query(InternalMessage).options(joinedload(InternalMessage.sender)).filter(
        InternalMessage.thread_id == thread_id
    )
    if before_id:
        query = query.filter(InternalMessage.id < before_id)

    # Se piden los más nuevos y se devuelven en orden de lectura (viejo → nuevo).
    rows = query.order_by(InternalMessage.id.desc()).limit(limit).all()
    rows.reverse()
    return [_message_response(m, m.sender.name) for m in rows]


def _send_internal_push_background(thread_id: int, thread_title: str, sender_name: str,
                                   body: str, recipient_user_ids: list) -> None:
    """
    Manda las push fuera del ciclo de la petición, con su propia sesión de base.

    El que escribe no tiene que esperar a que los servidores de push de Google y Apple
    contesten uno por uno para ver su mensaje en pantalla; y si uno falla, el mensaje ya
    quedó enviado igual.
    """
    db = SessionLocal()
    try:
        push_service.notify_internal_message(
            db=db,
            thread_id=thread_id,
            thread_title=thread_title,
            sender_name=sender_name,
            body=body,
            recipient_user_ids=recipient_user_ids,
        )
    except Exception as e:
        logger.error(f"[Push interno] Falló el aviso del hilo {thread_id}: {e}", exc_info=True)
    finally:
        db.close()


def _persist_message(db: Session, thread: InternalThread, participant: InternalParticipant,
                     sender: User, body: str, media: Optional[dict] = None) -> InternalMessage:
    """
    Graba el mensaje y deja el hilo al día. Único lugar donde se escribe en internal_messages:
    mandar texto y mandar un adjunto son la misma operación con distinto contenido, y tenerlo
    duplicado era la forma segura de que un día el adjunto no actualizara `last_message_at` y
    la bandeja lo dejara enterrado.
    """
    now = datetime.now(timezone.utc)
    media = media or {}
    message = InternalMessage(
        thread_id=thread.id,
        sender_user_id=sender.id,
        body=body,
        created_at=now,
        media_url=media.get("url"),
        media_mime_type=media.get("mime_type"),
        media_name=media.get("name"),
        media_size=media.get("size"),
    )
    db.add(message)
    thread.last_message_at = now
    # Quien escribe ya leyó lo suyo: si no, su propio mensaje le contaría como no leído.
    participant.last_read_at = now
    db.commit()
    db.refresh(message)
    return message


async def _deliver(db: Session, thread: InternalThread, message: InternalMessage, sender: User,
                   background: BackgroundTasks) -> InternalMessageResponse:
    """Difunde el mensaje recién grabado: en vivo por WebSocket y por push a quien no está."""
    response = _message_response(message, sender.name)

    # Difusión dirigida: uno por uno a los participantes, no por sala de sucursal. Un hilo
    # directo cruza sucursales, así que la regla de audiencia de notification_audience.py
    # (pensada para eventos de una sucursal) no aplica acá — el destinatario es el hilo.
    event = {
        "type": "internal_message",
        "thread_id": thread.id,
        "thread_kind": thread.kind,
        "message": response.model_dump(mode="json"),
    }
    for p in thread.participants:
        await ws_manager.send_personal_message(event, p.user_id)

    # La push va a todos menos a quien escribe: nadie necesita que el celular le avise de su
    # propio mensaje. Quien está mirando la pantalla igual la recibe — el navegador exige
    # mostrarla (userVisibleOnly) y el servidor no tiene forma confiable de saber si esa
    # pestaña está a la vista; el `tag` por hilo evita que se apilen.
    recipients = [p.user_id for p in thread.participants if p.user_id != sender.id]
    if recipients:
        title = f"Equipo {thread.branch.name}" if thread.kind == "branch" and thread.branch else sender.name
        background.add_task(
            _send_internal_push_background,
            thread.id, title, sender.name, _preview(message), recipients,
        )

    logger.info(f"Mensaje interno #{message.id} en hilo {thread.id} de {sender.name}")
    return response


@router.post("/threads/{thread_id}/messages", response_model=InternalMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    thread_id: int,
    payload: InternalMessageCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    participant = _participant_or_403(db, thread_id, current_user)

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mensaje está vacío.")

    thread = db.query(InternalThread).filter(InternalThread.id == thread_id).first()
    message = _persist_message(db, thread, participant, current_user, body)
    return await _deliver(db, thread, message, current_user, background)


@router.post("/threads/{thread_id}/attachments", response_model=InternalMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_attachment(
    thread_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    caption: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Sube una foto o un PDF y lo manda como un mensaje más del hilo.

    Endpoint aparte del envío de texto y no un `send_message` multipart: obligar a todo mensaje
    de texto a viajar como formulario para que uno de cada cien lleve un archivo habría hecho
    más ruidoso el camino que se usa siempre.
    """
    participant = _participant_or_403(db, thread_id, current_user)

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_ATTACHMENT_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden mandar imágenes (JPG, PNG, WEBP, GIF, HEIC) o PDF.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío.")
    # Se mide acá y no solo en el navegador: el límite del cliente es una cortesía, no un control.
    if len(data) > MAX_ATTACHMENT_BYTES:
        mb = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"El archivo pesa más de {mb} MB. Mandá una versión más liviana.",
        )

    url = save_internal_attachment(data, mime)
    # Solo el nombre, sin carpetas: lo que llega del navegador puede traer una ruta entera.
    original_name = Path(file.filename or "").name[:255] or "archivo"

    thread = db.query(InternalThread).filter(InternalThread.id == thread_id).first()
    message = _persist_message(
        db, thread, participant, current_user, (caption or "").strip()[:4000],
        media={"url": url, "mime_type": mime, "name": original_name, "size": len(data)},
    )
    return await _deliver(db, thread, message, current_user, background)


@router.post("/threads/{thread_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    participant = _participant_or_403(db, thread_id, current_user)
    participant.last_read_at = datetime.now(timezone.utc)
    db.commit()
    return None
