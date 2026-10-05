"""
Farmhouse — buzón público del formulario del colaborador.

Un servicio PEQUEÑO y APARTE de Farmhouse Link, pensado para que lo que está expuesto a internet no
toque el sistema central:

- No tiene ninguna conexión, clave ni acceso a la base de datos de Farmhouse Link.
- Los datos llegan CIFRADOS desde el navegador del colaborador con una llave pública (ECDH P-256 +
  AES-GCM). Este servicio solo guarda sobres cifrados que no puede abrir: la llave privada vive
  únicamente en Farmhouse Link. Aunque alguien se llevara toda esta base, no vería ni una cédula.
- Farmhouse Link NO recibe nada de aquí: él mismo viene a buscar los sobres (con una clave de
  administración) y los borra de aquí al importarlos. El sistema central no abre ningún puerto nuevo.
- Solo se puede enviar con una invitación vigente, de un solo uso y con vencimiento. Aquí solo se
  guarda el hash de cada invitación.
- Sin cookies, sin sesión, sin terceros: política de seguridad estricta, límites de intentos y de
  tamaño, y respuestas idénticas para enlaces inválidos, vencidos o ya usados.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

logger = logging.getLogger("intake")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


# ==========================================================================
# Configuración (variables de entorno; se leen en cada uso para poder probar)
# ==========================================================================
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def database_url() -> str:
    url = _env("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'intake.db'}"
    if url.startswith("mysql://"):
        url = url.replace("mysql://", "mysql+pymysql://", 1)
    return url


ENGINE = create_engine(database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ENGINE)
Base = declarative_base()

PUBLIC_WINDOW_SECONDS = 600
PUBLIC_MAX_REQUESTS = 20            # por IP, en la ventana
PUBLIC_MAX_BAD_TOKENS = 6           # tokens inválidos por IP, en la ventana
ADMIN_WINDOW_SECONDS = 600
ADMIN_MAX_FAILURES = 8              # claves de administración erróneas por IP, en la ventana
MAX_BODY_BYTES = 30_000
MAX_ENVELOPE_FIELD = 24_000
MAX_INVITE_HOURS = 168
SUBMISSION_RETENTION_DAYS = 14      # un sobre que Farmhouse Link no recogió en 14 días se borra
INVITE_RETENTION_DAYS = 7           # invitaciones usadas o vencidas
MIN_ADMIN_KEY_LENGTH = 32
MAX_TRACKED_IPS = 5000

# Formulario ABIERTO (el enlace público, p. ej. en Instagram): cualquiera puede llegar, así que además de
# los límites de siempre lleva una prueba de trabajo (un cálculo corto que hace el navegador y que frena
# a los robots sin captchas ni servicios de terceros), un tiempo mínimo de llenado, un tope por IP, un
# tope diario y un tope de sobres pendientes. Se enciende y apaga desde Farmhouse Link.
OPEN_MARK = "open"                  # `invite_hash` de los sobres que llegaron por el formulario abierto
POW_BITS = 16                       # ceros iniciales que debe tener el hash (~65 mil intentos, un par de segundos)
POW_MIN_FILL_SECONDS = 20           # nadie llena todo el formulario en menos
POW_TTL_SECONDS = 1800              # el reto vence a los 30 minutos
OPEN_IP_WINDOW = 3600
OPEN_IP_MAX = 3                     # envíos por IP y hora
OPEN_BUFFER_CAP = 500               # sobres pendientes máximos (si Farmhouse Link no los recoge, se frena)

_public_hits: dict = defaultdict(list)
_open_hits: dict = defaultdict(list)
_public_bad: dict = defaultdict(list)
_admin_bad: dict = defaultdict(list)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==========================================================================
# Base de datos: solo invitaciones (hash) y sobres cifrados
# ==========================================================================
class Invite(Base):
    __tablename__ = "invites"
    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    label = Column(String(150), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    invite_hash = Column(String(64), nullable=False)
    envelope = Column(Text, nullable=False)          # JSON con el sobre cifrado: este servicio no puede abrirlo
    created_at = Column(DateTime, default=_now, nullable=False)
    __table_args__ = (Index("ix_submission_created", "created_at"),)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(40), primary_key=True)
    value = Column(String(200), nullable=False)


class ChallengeUse(Base):
    """Retos de prueba de trabajo ya usados: cada uno sirve para UN envío."""
    __tablename__ = "challenge_uses"
    id = Column(String(64), primary_key=True)
    created_at = Column(DateTime, default=_now, nullable=False)


Base.metadata.create_all(bind=ENGINE)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================================================
# Aplicación
# ==========================================================================
app = FastAPI(title="Farmhouse — buzón del colaborador", docs_url=None, redoc_url=None, openapi_url=None)

PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    if request.url.path.startswith("/api") or request.url.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
    if _env("ENVIRONMENT") == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def _client_ip(request: Request) -> str:
    """Detrás del proxy de Railway: la ÚLTIMA IP de X-Forwarded-For (la que agregó el proxy; la primera la escribe el cliente)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "0.0.0.0"


def _prune(bucket: dict, now: float, window: int) -> None:
    if len(bucket) > MAX_TRACKED_IPS:
        for k in [k for k, ts in bucket.items() if not ts or now - ts[-1] > window]:
            bucket.pop(k, None)


def _too_many() -> HTTPException:
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Demasiados intentos. Espera unos minutos e intenta de nuevo.")


def _invalid_link() -> HTTPException:
    # Inválido, vencido, ya usado o inexistente: la misma respuesta, para no revelar cuál es.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Este enlace no es válido o ya venció. Pide uno nuevo.")


async def public_guard(request: Request) -> str:
    """Se ejecuta ANTES de leer el cuerpo: tope de tamaño y de peticiones por IP."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Solicitud inválida.")
    if declared > MAX_BODY_BYTES or len(await request.body()) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="La solicitud es demasiado grande.")
    ip = _client_ip(request)
    now = time.time()
    _prune(_public_hits, now, PUBLIC_WINDOW_SECONDS)
    hits = [t for t in _public_hits[ip] if now - t < PUBLIC_WINDOW_SECONDS]
    if len(hits) >= PUBLIC_MAX_REQUESTS:
        _public_hits[ip] = hits
        raise _too_many()
    hits.append(now)
    _public_hits[ip] = hits
    return ip


def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _find_valid_invite(db: Session, token: str, ip: str) -> Invite:
    invite = db.query(Invite).filter(Invite.token_hash == hash_token(token)).first()
    if not invite or invite.used_at is not None or invite.expires_at <= _now():
        now = time.time()
        _prune(_public_bad, now, PUBLIC_WINDOW_SECONDS)
        bad = [t for t in _public_bad[ip] if now - t < PUBLIC_WINDOW_SECONDS] + [now]
        _public_bad[ip] = bad
        if len(bad) > PUBLIC_MAX_BAD_TOKENS:
            raise _too_many()
        raise _invalid_link()
    return invite


# ==========================================================================
# Público: la página y el envío
# ==========================================================================
_B64U = re.compile(r"^[A-Za-z0-9_\-]+$")


class Envelope(BaseModel):
    """Sobre cifrado en el navegador (ECDH P-256 + HKDF-SHA256 + AES-256-GCM). Aquí solo se valida su forma."""
    v: int = Field(..., ge=1, le=1)
    epk: str = Field(..., min_length=80, max_length=200)      # llave pública efímera (65 bytes en base64url)
    salt: str = Field(..., min_length=16, max_length=64)
    iv: str = Field(..., min_length=12, max_length=40)
    ct: str = Field(..., min_length=40, max_length=MAX_ENVELOPE_FIELD)

    @field_validator("epk", "salt", "iv", "ct")
    @classmethod
    def _b64url(cls, v: str) -> str:
        if not _B64U.match(v):
            raise ValueError("Formato inválido.")
        return v


class CheckIn(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)


class SubmitIn(CheckIn):
    envelope: Envelope
    website: Optional[str] = Field(None, max_length=200)      # señuelo anti-robots


@app.get("/", include_in_schema=False)
@app.get("/colaborador", include_in_schema=False)
def page():
    # La raíz abre el mismo formulario: el enlace para publicar puede ser la dirección sola, sin /colaborador.
    return FileResponse(str(STATIC_DIR / "colaborador.html"), headers={"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP})


@app.get("/assets/{name}", include_in_schema=False)
def asset(name: str):
    if name not in {"colaborador.css", "colaborador.js", "crypto.js", "logo.png"}:
        raise HTTPException(status_code=404)
    path = STATIC_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), headers={"Cache-Control": "public, max-age=300"})


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/api/public-key")
def public_key(ip: str = Depends(public_guard)):
    key = _env("PUBLIC_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="El formulario no está disponible por ahora.")
    return {"v": 1, "key": key}


@app.post("/api/check")
def check(body: CheckIn, ip: str = Depends(public_guard), db: Session = Depends(get_db)):
    invite = _find_valid_invite(db, body.token, ip)
    return {"ok": True, "label": invite.label}


@app.post("/api/submit", status_code=status.HTTP_201_CREATED)
def submit(body: SubmitIn, ip: str = Depends(public_guard), db: Session = Depends(get_db)):
    if body.website:
        # Señuelo: un robot lo rellena, una persona nunca lo ve. "Ok" sin guardar ni gastar la invitación.
        return {"ok": True}
    invite = _find_valid_invite(db, body.token, ip)
    # Se gasta de forma atómica: dos envíos a la vez con el mismo enlace no pueden pasar los dos.
    consumed = db.query(Invite).filter(Invite.id == invite.id, Invite.used_at.is_(None), Invite.expires_at > _now()) \
        .update({"used_at": _now()}, synchronize_session=False)
    if consumed != 1:
        db.rollback()
        raise _invalid_link()
    db.add(Submission(invite_hash=invite.token_hash, envelope=body.envelope.model_dump_json()))
    db.commit()
    return {"ok": True}


# ==========================================================================
# Público: formulario ABIERTO (el enlace que se publica, p. ej. en Instagram)
# ==========================================================================
def open_daily_cap() -> int:
    try:
        return max(1, int(_env("OPEN_DAILY_CAP", "150")))
    except ValueError:
        return 150


def _is_open(db: Session) -> bool:
    row = db.get(Setting, "open_form")
    return bool(row and row.value == "1")


def _challenge_key() -> bytes:
    # Derivada de la clave de administración: no hace falta otra variable. Sin clave, el modo abierto no funciona.
    admin = _env("ADMIN_KEY")
    if len(admin) < MIN_ADMIN_KEY_LENGTH:
        raise HTTPException(status_code=503, detail="El formulario no está disponible por ahora.")
    return hashlib.sha256(("farmhouse-intake-challenge:" + admin).encode()).digest()


def _sign(payload: str) -> str:
    mac = hmac.new(_challenge_key(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:16]).decode().rstrip("=")


def new_challenge(now: Optional[float] = None) -> str:
    payload = f"{int(now if now is not None else time.time())}.{secrets.token_urlsafe(12)}"
    return f"{payload}.{_sign(payload)}"


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def _verify_challenge(db: Session, challenge: str, nonce: str) -> None:
    """Reto firmado por este servidor, con edad entre 20 s y 30 min, resuelto (prueba de trabajo) y no usado antes."""
    parts = challenge.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not hmac.compare_digest(_sign(f"{parts[0]}.{parts[1]}"), parts[2]):
        raise HTTPException(status_code=400, detail="Verificación inválida. Recarga la página e intenta de nuevo.")
    age = time.time() - int(parts[0])
    if age < POW_MIN_FILL_SECONDS:
        raise HTTPException(status_code=400, detail="Enviaste demasiado rápido. Revisa tus datos y vuelve a intentar.")
    if age > POW_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="La página estuvo abierta demasiado tiempo. Recárgala e intenta de nuevo.")
    if _leading_zero_bits(hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()) < POW_BITS:
        raise HTTPException(status_code=400, detail="Verificación inválida. Recarga la página e intenta de nuevo.")
    db.add(ChallengeUse(id=hashlib.sha256(challenge.encode()).hexdigest()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Verificación inválida. Recarga la página e intenta de nuevo.")


class OpenSubmitIn(BaseModel):
    challenge: str = Field(..., min_length=20, max_length=120)
    nonce: str = Field(..., min_length=1, max_length=20, pattern=r"^[0-9]+$")
    envelope: Envelope
    website: Optional[str] = Field(None, max_length=200)


@app.get("/api/mode")
def mode(ip: str = Depends(public_guard), db: Session = Depends(get_db)):
    return {"open": _is_open(db)}


@app.get("/api/challenge")
def challenge(ip: str = Depends(public_guard), db: Session = Depends(get_db)):
    if not _is_open(db):
        raise HTTPException(status_code=404, detail="El formulario no está abierto.")
    return {"challenge": new_challenge(), "bits": POW_BITS}


@app.post("/api/submit-open", status_code=status.HTTP_201_CREATED)
def submit_open(body: OpenSubmitIn, ip: str = Depends(public_guard), db: Session = Depends(get_db)):
    if not _is_open(db):
        raise HTTPException(status_code=404, detail="El formulario no está abierto.")
    if body.website:
        return {"ok": True}                      # señuelo: un robot lo rellena
    now = time.time()
    _prune(_open_hits, now, OPEN_IP_WINDOW)
    hits = [t for t in _open_hits[ip] if now - t < OPEN_IP_WINDOW]
    if len(hits) >= OPEN_IP_MAX:
        _open_hits[ip] = hits
        raise _too_many()
    _verify_challenge(db, body.challenge, body.nonce)
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    if db.query(Submission).filter(Submission.invite_hash == OPEN_MARK, Submission.created_at >= today).count() >= open_daily_cap():
        raise HTTPException(status_code=429, detail="El formulario recibió muchos envíos hoy. Intenta de nuevo mañana.")
    if db.query(Submission).count() >= OPEN_BUFFER_CAP:
        raise HTTPException(status_code=503, detail="El formulario no está disponible por ahora. Intenta más tarde.")
    _open_hits[ip] = hits + [now]
    db.add(Submission(invite_hash=OPEN_MARK, envelope=body.envelope.model_dump_json()))
    db.commit()
    return {"ok": True}


# ==========================================================================
# Administración: solo Farmhouse Link, con la clave de administración
# ==========================================================================
def admin_guard(request: Request) -> None:
    key = _env("ADMIN_KEY")
    ip = _client_ip(request)
    now = time.time()
    _prune(_admin_bad, now, ADMIN_WINDOW_SECONDS)
    bad = [t for t in _admin_bad[ip] if now - t < ADMIN_WINDOW_SECONDS]
    _admin_bad[ip] = bad
    if len(bad) >= ADMIN_MAX_FAILURES:
        raise _too_many()
    if len(key) < MIN_ADMIN_KEY_LENGTH:
        # Sin una clave larga configurada, la administración queda apagada (falla cerrado).
        raise HTTPException(status_code=503, detail="Administración no disponible.")
    auth = request.headers.get("Authorization", "")
    provided = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    # Se comparan los hashes (misma longitud) en tiempo constante.
    ok = hmac.compare_digest(hashlib.sha256(provided.encode()).digest(), hashlib.sha256(key.encode()).digest())
    if not ok:
        _admin_bad[ip] = bad + [now]
        logger.warning("Clave de administración incorrecta desde %s", ip)
        raise HTTPException(status_code=401, detail="No autorizado.")


def _purge(db: Session) -> None:
    now = _now()
    db.query(Submission).filter(Submission.created_at < now - timedelta(days=SUBMISSION_RETENTION_DAYS)).delete(synchronize_session=False)
    db.query(ChallengeUse).filter(ChallengeUse.created_at < now - timedelta(seconds=POW_TTL_SECONDS * 2)).delete(synchronize_session=False)
    cutoff = now - timedelta(days=INVITE_RETENTION_DAYS)
    db.query(Invite).filter((Invite.expires_at < cutoff) | (Invite.used_at < cutoff)).delete(synchronize_session=False)
    db.commit()


class InviteIn(BaseModel):
    token_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    label: Optional[str] = Field(None, max_length=150)
    expires_at: datetime


class SubmissionOut(BaseModel):
    id: int
    invite_hash: str
    envelope: dict
    created_at: datetime


@app.post("/admin/invites", status_code=201, dependencies=[Depends(admin_guard)])
def admin_create_invite(body: InviteIn, db: Session = Depends(get_db)):
    expires = body.expires_at.replace(tzinfo=None) if body.expires_at.tzinfo is None else body.expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    if expires <= _now() or expires > _now() + timedelta(hours=MAX_INVITE_HOURS + 1):
        raise HTTPException(status_code=422, detail="Vencimiento fuera de rango.")
    _purge(db)
    if db.query(Invite).filter(Invite.token_hash == body.token_hash).first():
        raise HTTPException(status_code=409, detail="Esa invitación ya existe.")
    db.add(Invite(token_hash=body.token_hash, label=(body.label or "").strip() or None, expires_at=expires))
    db.commit()
    return {"ok": True}


@app.delete("/admin/invites/{token_hash}", status_code=204, dependencies=[Depends(admin_guard)])
def admin_revoke_invite(token_hash: str, db: Session = Depends(get_db)):
    if not re.fullmatch(r"[0-9a-f]{64}", token_hash):
        raise HTTPException(status_code=422, detail="Hash inválido.")
    db.query(Invite).filter(Invite.token_hash == token_hash).delete(synchronize_session=False)
    db.commit()
    return None


@app.get("/admin/submissions", response_model=List[SubmissionOut], dependencies=[Depends(admin_guard)])
def admin_list_submissions(limit: int = 50, db: Session = Depends(get_db)):
    import json
    _purge(db)
    rows = db.query(Submission).order_by(Submission.id.asc()).limit(max(1, min(limit, 200))).all()
    return [SubmissionOut(id=r.id, invite_hash=r.invite_hash, envelope=json.loads(r.envelope), created_at=r.created_at) for r in rows]


@app.delete("/admin/submissions/{submission_id}", status_code=204, dependencies=[Depends(admin_guard)])
def admin_ack_submission(submission_id: int, db: Session = Depends(get_db)):
    db.query(Submission).filter(Submission.id == submission_id).delete(synchronize_session=False)
    db.commit()
    return None


class OpenFormIn(BaseModel):
    enabled: bool


def _open_status(db: Session) -> dict:
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "enabled": _is_open(db),
        "today": db.query(Submission).filter(Submission.invite_hash == OPEN_MARK, Submission.created_at >= today).count(),
        "daily_cap": open_daily_cap(),
        "pending": db.query(Submission).count(),
    }


@app.get("/admin/open-form", dependencies=[Depends(admin_guard)])
def admin_open_form_status(db: Session = Depends(get_db)):
    return _open_status(db)


@app.put("/admin/open-form", dependencies=[Depends(admin_guard)])
def admin_set_open_form(body: OpenFormIn, db: Session = Depends(get_db)):
    row = db.get(Setting, "open_form")
    if row is None:
        db.add(Setting(key="open_form", value="1" if body.enabled else "0"))
    else:
        row.value = "1" if body.enabled else "0"
    db.commit()
    logger.info("Formulario abierto %s", "ENCENDIDO" if body.enabled else "apagado")
    return _open_status(db)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("Error no controlado")
    return JSONResponse(status_code=500, content={"detail": "Error del servidor."})
