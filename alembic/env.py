# alembic/env.py

from __future__ import annotations
import asyncio
from logging.config import fileConfig
import os
import sys


from sqlalchemy import pool
from sqlalchemy.engine import engine_from_config
from sqlalchemy.ext.asyncio import 

from alembic import context

# Fuerza a que la raíz del repo (donde vive models.py) esté en sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Importar la metadata de tu app
# (models.py está en la raíz del proyecto)
from models import Base


# ------------------------------------------------------------
# Configuración base de Alembic
# ------------------------------------------------------------
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata para autogenerar migraciones
target_metadata = Base.metadata


# ------------------------------------------------------------
# Helper: obtener URL de conexión
# Render / Neon → DATABASE_URL asyncpg
# Para offline migration, Alembic necesita versión sync
# ------------------------------------------------------------
def get_url_sync():
    """
    Convierte:
        postgresql+asyncpg://user:pass@host/db?sslmode=require
    a:
        postgresql://user:pass@host/db?sslmode=require
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL no está definido")

    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://")

    return url


def get_url_async():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL no está definido")
    return url


# ------------------------------------------------------------
# OFFLINE MODE
# ------------------------------------------------------------
def run_migrations_offline():
    """Ejecutar migraciones sin conexión a DB."""
    url = get_url_sync()

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ------------------------------------------------------------
# ONLINE MODE (ASYNC)
# ------------------------------------------------------------
def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,     # detectar cambios en tipos
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    url = get_url_async()

    connectable = AsyncEngine(
        engine_from_config(
            {},
            prefix="sqlalchemy.",
            url=url,
            future=True,
            poolclass=pool.NullPool,
        )
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# ------------------------------------------------------------
# EJECUCIÓN
# ------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())


