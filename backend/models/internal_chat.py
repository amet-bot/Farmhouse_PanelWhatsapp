from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class InternalThread(Base):
    """
    Conversación del sistema de Comunicación Interna: personal entre agentes, sin pasar por
    WhatsApp ni por el número enrutador.

    Tablas propias a propósito. Conversation/Message existen para el Centro WhatsApp y están
    atados a un Contact (un cliente con su número), a una sucursal dueña y a estados de
    atención ("abierta", "asignada"). Acá el interlocutor es un User, no hay cliente, no hay
    número y no hay bandeja que atender: colgar esto de esos modelos habría significado
    ensuciarlos con columnas nulas y con ramas "si es interno" por todos lados.

    Dos formas de hilo:
      · kind="direct" — dos personas, sin importar la sucursal de cada una.
      · kind="branch" — el canal del equipo de una sucursal. Uno por sucursal, creado solo si
        alguien lo usa, y sus participantes se sincronizan solos (ver ensure_branch_thread en
        routers/internal_chat.py) para que dar de alta a alguien no obligue a administrarlo.
    """
    __tablename__ = "internal_threads"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    kind = Column(String(20), nullable=False, default="direct")  # "direct" | "branch"
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)  # solo si kind="branch"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    # Se actualiza con cada mensaje: la bandeja ordena por esto sin tener que mirar los mensajes.
    last_message_at = Column(DateTime, nullable=True, index=True)

    branch = relationship("Branch")
    participants = relationship("InternalParticipant", back_populates="thread", cascade="all, delete-orphan")
    messages = relationship("InternalMessage", back_populates="thread", cascade="all, delete-orphan")


class InternalParticipant(Base):
    """
    Quién pertenece a un hilo y hasta dónde leyó.

    `last_read_at` es la única fuente de los no leídos: se cuentan los mensajes del hilo
    posteriores a esa marca. Guardar un contador incrementable habría necesitado mantenerlo
    en sincronía desde varios lugares (enviar, leer, entrar, borrar) y se desincroniza al
    primer camino que alguien olvide.
    """
    __tablename__ = "internal_participants"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("internal_threads.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    last_read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    thread = relationship("InternalThread", back_populates="participants")
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uq_internal_participant"),
        Index("ix_internal_participant_user", "user_id"),
    )


class InternalMessage(Base):
    __tablename__ = "internal_messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("internal_threads.id", ondelete="CASCADE"), nullable=False)
    sender_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Un mensaje que es solo un adjunto guarda "" acá, no NULL: la columna nació NOT NULL y
    # aflojarla habría significado un ALTER sobre una tabla ya en producción para ganar una
    # distinción que el código no usa (nadie pregunta "¿es vacío o es nulo?", preguntan si hay
    # texto). Quien lee siempre tiene un str.
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Adjunto opcional: una foto de un faltante, una factura. El archivo vive en disco bajo
    # media/internal/ y acá queda la URL; media_name conserva el nombre original con el que se
    # subió, que es el que ve y descarga la gente.
    media_url = Column(String(500), nullable=True)
    media_mime_type = Column(String(120), nullable=True)
    media_name = Column(String(255), nullable=True)
    media_size = Column(Integer, nullable=True)

    thread = relationship("InternalThread", back_populates="messages")
    sender = relationship("User")

    __table_args__ = (
        Index("ix_internal_message_thread_created", "thread_id", "created_at"),
    )
