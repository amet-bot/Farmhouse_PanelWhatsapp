import os
import sys
import tempfile
from pathlib import Path

# El servicio lee su configuración del entorno al importarse: se fija ANTES de importar `main`.
_TMP = tempfile.mkdtemp(prefix="intake-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP, 'intake.db').as_posix()}"
os.environ["ADMIN_KEY"] = "a" * 48
os.environ["PUBLIC_KEY"] = "BPublicKeyDePruebaBPublicKeyDePruebaBPublicKeyDePruebaBPublicKeyDePruebaBPublicKeyDePrueba"
os.environ.pop("ENVIRONMENT", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import main as intake_main  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    for bucket in (intake_main._public_hits, intake_main._public_bad, intake_main._admin_bad, intake_main._open_hits):
        bucket.clear()
    db = intake_main.SessionLocal()
    db.query(intake_main.Submission).delete()
    db.query(intake_main.Invite).delete()
    db.query(intake_main.Setting).delete()
    db.query(intake_main.ChallengeUse).delete()
    db.commit()
    db.close()
    monkeypatch.setenv("ADMIN_KEY", "a" * 48)
    monkeypatch.setenv("PUBLIC_KEY", os.environ["PUBLIC_KEY"])
    yield


@pytest.fixture
def client():
    return TestClient(intake_main.app)


@pytest.fixture
def admin():
    return {"Authorization": "Bearer " + "a" * 48}
