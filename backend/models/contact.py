from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import relationship
from sqlalchemy import Numeric
from datetime import datetime, timezone
from database import Base

class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(150), nullable=False)
    phone = Column(String(50), unique=True, nullable=False, index=True) # "+507 6000-1234"
    avatar_url = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    address = Column(String(500), nullable=True)
    building_or_house = Column(String(150), nullable=True)
    floor_or_unit = Column(String(100), nullable=True)
    address_reference = Column(String(300), nullable=True)
    latitude = Column(Numeric(10, 7), nullable=True)
    longitude = Column(Numeric(10, 7), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_interaction = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at = Column(DateTime, nullable=True)

    # Relaciones
    conversations = relationship("Conversation", back_populates="contact")

