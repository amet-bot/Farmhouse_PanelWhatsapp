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
            {"code": "CDE", "name": "Costa del Este", "color": "#16a34a", "address": "Torre MMG, planta baja, Costa del Este", "latitude": 9.0083064, "longitude": -79.4773394, "accepts_delivery": True},
            {"code": "SF", "name": "San Francisco", "color": "#0d9488", "address": "Plaza 76, San Francisco", "latitude": 8.9912804, "longitude": -79.5031756, "accepts_delivery": True},
            {"code": "CLY", "name": "Clayton", "color": "#d97706", "address": "Plaza Clayton Mall", "latitude": 9.0038590, "longitude": -79.5730430, "accepts_delivery": True},
            {"code": "OBR", "name": "Obarrio", "color": "#2563eb", "address": "Adison House, Calle Abel Bravo, Obarrio", "latitude": 8.9863531, "longitude": -79.5196357, "accepts_delivery": True},
            {"code": "VP", "name": "Via Porras", "color": "#9333ea", "address": "Vía Porras, Parque Omar", "latitude": 8.9967623, "longitude": -79.5065669, "accepts_delivery": True},
            {"code": "CAT", "name": "Catering", "color": "#e11d48", "address": None, "latitude": None, "longitude": None, "accepts_delivery": False},
        ]

        for b_data in branches_data:
            existing = db.query(Branch).filter(
                (Branch.name == b_data["name"]) | (Branch.code == b_data["code"])
            ).first()
            if not existing:
                b = Branch(
                    **b_data,
                    active=True,
                )
                db.add(b)
                db.commit()
                db.refresh(b)
                logger.info(f"  [OK] Sucursal creada: {b.name} (ID: {b.id})")
            else:
                # Las coordenadas son configuración operacional, no datos del cliente.
                # Se actualizan de forma idempotente al desplegar para que el mapa y el
                # cálculo del servidor siempre utilicen la misma fuente.
                for field in ("address", "latitude", "longitude", "accepts_delivery"):
                    setattr(existing, field, b_data[field])
                db.commit()
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

        # 3. USUARIO DE SOL (encargada de Pedidos Corporativos / Eventos, sucursal Catering)
        # Datos FICTICIOS a propósito: el cliente pidió dejarlos así por ahora y reemplazarlos
        # por los reales de Sol más adelante (username, email y password deben actualizarse).
        sol_username = "sol.eventos"
        cat_branch = db.query(Branch).filter(Branch.code == "CAT").first()
        sol = db.query(User).filter(User.username == sol_username).first()
        if not sol and cat_branch:
            sol = User(
                username=sol_username,
                name="Sol (Eventos y Corporativo)",
                email="sol.eventos@farmhouse.pa",
                password_hash=get_password_hash(os.environ.get("SOL_INITIAL_PASSWORD", "CambiarSol123!")),
                role="agent",
                branch_id=cat_branch.id,
                active=True
            )
            db.add(sol)
            db.commit()
            db.refresh(sol)
            logger.info(f"  [OK] Usuario ficticio de Sol creado: @{sol.username} (ID: {sol.id}) — actualizar con sus datos reales.")
        elif sol:
            logger.debug(f"  [OK] Usuario de Sol existente: @{sol.username} (ID: {sol.id})")

        logger.info("[SEED] Proceso de seed completado exitosamente.")
    except Exception as e:
        db.rollback()
        logger.error(f"[ERROR SEED] {e}", exc_info=True)
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
