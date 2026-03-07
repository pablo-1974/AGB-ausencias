# alembic/env.py
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# --- Añade la raíz del repo al sys.path para poder importar models.py ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Importa tu Base desde models.py en la raíz (ajusta si es otro módulo)
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
# Helpers para URL
# ------------------------------------------------------------
def _get_raw_url() -> str:
    # Prioriza env var; NO uses sqlalchemy.url del ini (déjalo vacío)
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("No se encontró DATABASE_URL ni sqlalchemy.url en alembic.ini")
    return url

def _clean_asyncpg_url_and_args(raw_url: str) -> tuple[str, dict]:
    """
    Si el driver es asyncpg, limpia sslmode/ssl de la query para no pasarlos al DBAPI
    y devuelve connect_args apropiados (ssl='require' para Neon).
    """
    connect_args: dict = {}
    clean_url = raw_url

    if raw_url.startswith("postgresql+asyncpg://"):
        parts = urlsplit(raw_url)
        qs = dict(parse_qsl(parts.query, keep_blank_values=True))
        # Evita que Alembic/SQLAlchemy propaguen estos a asyncpg.connect(...)
        qs.pop("sslmode", None)
        qs.pop("ssl", None)
        clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment))
        # Para Neon: TLS obligatorio
        connect_args = {"ssl": "require"}

    return clean_url, connect_args

def get_url_offline() -> str:
    """
    Devuelve la URL para modo offline.
    Alembic offline no entiende asyncpg → convierte a 'postgresql://'
    y escapa % para el parser de Alembic.
    """
    url = _get_raw_url()
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url.replace("%", "%%")

def get_url_online() -> tuple[str, dict]:
    """
    Devuelve (url, connect_args) para modo online (async).
    """
    raw_url = _get_raw_url()
    url, connect_args = _clean_asyncpg_url_and_args(raw_url)
    return url, connect_args

# ------------------------------------------------------------
# OFFLINE MODE
# ------------------------------------------------------------
def run_migrations_offline() -> None:
    """Ejecutar migraciones sin conexión a DB."""
    url = get_url_offline()
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
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    url, connect_args = get_url_online()
    connectable: AsyncEngine = create_async_engine(
        url,
        poolclass=pool.NullPool,
        future=True,
        connect_args=connect_args,
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
