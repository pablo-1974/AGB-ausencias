# alembic/env.py
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import engine_from_config
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context

# --- Añade la raíz del repo al sys.path para poder importar models.py ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Importa tu metadata (Base) desde models.py en la raíz
from models import Base  # noqa: E402

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
# Helpers de URL
# ------------------------------------------------------------
def get_url_sync() -> str:
    """
    Devuelve la URL síncrona para modo offline.
    Convierte postgresql+asyncpg:// → postgresql:// si es necesario.
    """
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("No se encontró DATABASE_URL ni sqlalchemy.url en alembic.ini")

    # Alembic offline no entiende asyncpg → convierte a 'postgresql://'
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url


def get_url_async() -> str:
    """
    Devuelve la URL asíncrona para modo online (con asyncpg).
    """
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("No se encontró DATABASE_URL ni sqlalchemy.url en alembic.ini")
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
        compare_type=True,
        compare_server_default=True,
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
        compare_type=True,
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
