import sys
import os

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from database import SessionLocal, engine, Base
import models
from models.branch import Branch
from models.user import User
from security.auth import get_password_hash

import logging

logger = logging.getLogger("farmhouse.seed")

def seed_database():
    """
    Pobla datos iniciales indispensables (sucursales y admin) de manera idempotente.
    No ejecuta create_all() para respetar las migraciones de Alembic como única fuente de verdad.
    """
    db = SessionLocal()
    try:
        logger.info("[SEED] Verificando sucursales oficiales y usuario administrador...")

        # 1. SUCURSALES OFICIALES DE FARMHOUSE
        branches_data = [
            {"code": "CDE", "name": "Costa del Este", "color": "#16a34a"},
            {"code": "SF", "name": "San Francisco", "color": "#0d9488"},
            {"code": "CLY", "name": "Clayton", "color": "#d97706"},
            {"code": "OBR", "name": "Obarrio", "color": "#2563eb"},
            {"code": "VP", "name": "Via Porras", "color": "#9333ea"},
            {"code": "CAT", "name": "Catering", "color": "#e11d48"},
        ]

        for b_data in branches_data:
            existing = db.query(Branch).filter(
                (Branch.name == b_data["name"]) | (Branch.code == b_data["code"])
            ).first()
            if not existing:
                b = Branch(
                    code=b_data["code"],
                    name=b_data["name"],
                    color=b_data["color"],
                    active=True
                )
                db.add(b)
                db.commit()
                db.refresh(b)
                logger.info(f"  [OK] Sucursal creada: {b.name} (ID: {b.id})")
            else:
                logger.debug(f"  [OK] Sucursal existente: {existing.name} (ID: {existing.id})")

        # 2. USUARIO ADMINISTRADOR PRINCIPAL (Punto 12)
        admin_username = "admin"
        initial_admin_pwd = os.environ.get("ADMIN_INITIAL_PASSWORD", "Admin123!")
        admin = db.query(User).filter(User.username == admin_username).first()
        if not admin:
            admin = User(
                username=admin_username,
                name="Administrador Farmhouse",
                email="admin@farmhouse.pa",
                password_hash=get_password_hash(initial_admin_pwd),
                role="admin",
                branch_id=None,
                active=True
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            logger.info(f"  [OK] Usuario Administrador creado: @{admin.username} (ID: {admin.id})")
        else:
            # Preservar la contraseña existente configurada por el usuario (no sobreescribir)
            if not admin.active:
                admin.active = True
                db.commit()
            logger.info(f"  [OK] Usuario Administrador verificado y activo (contraseña preservada): @{admin.username} (ID: {admin.id})")

        logger.info("[SEED] Proceso de seed completado exitosamente.")
    except Exception as e:
        db.rollback()
        logger.error(f"[ERROR SEED] {e}", exc_info=True)
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()