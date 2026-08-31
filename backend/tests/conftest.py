import sys
import os

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from database import Base, get_db
from main import app
from models.branch import Branch
from models.user import User
from models.device import Device
from models.contact import Contact
from models.conversation import Conversation
from security.auth import get_password_hash, create_access_token

# Base de datos SQLite en memoria para tests aislados
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture
def clayton_branch(db_session):
    branch = Branch(id=1, code="CLY", name="Clayton", color="#d97706", active=True)
    db_session.add(branch)
    db_session.commit()
    db_session.refresh(branch)
    return branch

@pytest.fixture
def obarrio_branch(db_session):
    branch = Branch(id=2, code="OBR", name="Obarrio", color="#2563eb", active=True)
    db_session.add(branch)
    db_session.commit()
    db_session.refresh(branch)
    return branch

@pytest.fixture
def admin_user(db_session):
    admin = User(
        id=1,
        username="admin",
        name="Admin General",
        email="admin@farmhouse.pa",
        password_hash=get_password_hash("Admin123!"),
        role="admin",
        branch_id=None,
        active=True
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin

@pytest.fixture
def clayton_agent(db_session, clayton_branch):
    agent = User(
        id=2,
        username="agente_clayton",
        name="Agente Clayton",
        email="agente.clayton@farmhouse.pa",
        password_hash=get_password_hash("Agent123!"),
        role="agent",
        branch_id=clayton_branch.id,
        active=True
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent

@pytest.fixture
def obarrio_agent(db_session, obarrio_branch):
    agent = User(
        id=3,
        username="agente_obarrio",
        name="Agente Obarrio",
        email="agente.obarrio@farmhouse.pa",
        password_hash=get_password_hash("Agent123!"),
        role="agent",
        branch_id=obarrio_branch.id,
        active=True
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent

@pytest.fixture
def clayton_device(db_session, clayton_branch):
    dev = Device(
        id=1,
        device_id="FH-DEVICE-CLY01",
        name="Tablet Clayton",
        device_type="tablet",
        branch_id=clayton_branch.id,
        status="active"
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    return dev

@pytest.fixture
def obarrio_device(db_session, obarrio_branch):
    dev = Device(
        id=2,
        device_id="FH-DEVICE-OBR01",
        name="PC Obarrio",
        device_type="computadora",
        branch_id=obarrio_branch.id,
        status="active"
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    return dev

@pytest.fixture
def revoked_device(db_session, clayton_branch):
    dev = Device(
        id=3,
        device_id="FH-DEVICE-REVOKED",
        name="Tablet Revocada",
        device_type="tablet",
        branch_id=clayton_branch.id,
        status="revoked"
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    return dev

@pytest.fixture
def supervisor_user(db_session, clayton_branch):
    supervisor = User(
        id=4,
        username="supervisor_clayton",
        name="Supervisor Clayton",
        email="supervisor.clayton@farmhouse.pa",
        password_hash=get_password_hash("Supervisor123!"),
        role="supervisor",
        branch_id=clayton_branch.id,
        active=True
    )
    db_session.add(supervisor)
    db_session.commit()
    db_session.refresh(supervisor)
    return supervisor

@pytest.fixture
def inactive_user(db_session):
    inactive = User(
        id=5,
        username="usuario_inactivo",
        name="Usuario Desactivado",
        email="inactivo@farmhouse.pa",
        password_hash=get_password_hash("Inactive123!"),
        role="agent",
        branch_id=1,
        active=False
    )
    db_session.add(inactive)
    db_session.commit()
    db_session.refresh(inactive)
    return inactive

def auth_headers_for(user: User, device_id: str = None) -> dict:
    token = create_access_token(subject=user.id)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Requested-With": "XMLHttpRequest"
    }
    if device_id:
        headers["X-Device-ID"] = device_id
    return headers

