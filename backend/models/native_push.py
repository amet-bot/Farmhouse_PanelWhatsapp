from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class NativePushToken(Base):
    """
    El token de Firebase (FCM) de un celular con la app de Farmhouse Link instalada. Es el
    equivalente nativo de PushSubscription (Web Push del navegador): a quién le llega un aviso lo
    decide la misma audiencia; esto solo dice a qué teléfonos mandárselo.
    """
    __tablename__ = "native_push_tokens"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(255), unique=True, nullable=False)
    platform = Column(String(20), nullable=False, default="android")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", backref="native_push_tokens")
