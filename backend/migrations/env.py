from logging.config import fileConfig
import os
import sys
from pathlib import Path

# Agregar directorio backend al path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import engine_from_config, pool, text
from alembic import context

from config import settings
from database import Base
import models  # Cargar todos los modelos

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def get_url():
    return settings.get_database_url()

def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Los IDs de revisión de este proyecto (ej. "007_hardening_security_and_orders",
        # 33 caracteres) superan el ancho VARCHAR(32) que Alembic usa por defecto para
        # alembic_version.version_num. MySQL trunca el valor en silencio al guardarlo,
        # rompiendo el control de versiones (la siguiente actualización ya no encuentra
        # la fila esperada). Se ensancha la columna antes de migrar; es una operación
        # idempotente y segura de repetir en cada arranque.
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "version_num VARCHAR(255) NOT NULL, "
            "PRIMARY KEY (version_num))"
        ))
        connection.execute(text(
            "ALTER TABLE alembic_version MODIFY COLUMN version_num VARCHAR(255) NOT NULL"
        ))
        connection.commit()

        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
