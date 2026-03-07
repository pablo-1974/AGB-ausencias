# database.py
import os
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from config import settings  # si prefieres, podrías usar os.getenv("DATABASE_URL")

RAW_URL = settings.DATABASE_URL
if not RAW_URL:
    raise RuntimeError("DATABASE_URL no está definido")

connect_args = {}
clean_url = RAW_URL

# Si usamos asyncpg, limpiamos la query y pasamos ssl por connect_args
if RAW_URL.startswith("postgresql+asyncpg://"):
    parts = urlsplit(RAW_URL)
    qs = dict(parse_qsl(parts.query, keep_blank_values=True))
    # Evitar que SQLAlchemy intente pasar estos kwargs al DBAPI:
    qs.pop("sslmode", None)   # propio de psycopg/libpq
    qs.pop("ssl", None)       # lo gestionamos nosotros
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment))
    # Forzamos SSL para Neon
    connect_args = {"ssl": "require"}

engine = create_async_engine(
    clean_url,
    pool_pre_ping=True,
    future=True,
    connect_args=connect_args,
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
