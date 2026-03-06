# config.py
import os
from pydantic import BaseModel

class Settings(BaseModel):
    # Branding / UI
    APP_NAME: str = os.getenv("APP_NAME", "Ausencias de profesores")
    INSTITUTION_NAME: str = os.getenv("INSTITUTION_NAME", "IES Antonio García Bellido")
    LOGO_PATH: str = os.getenv("LOGO_PATH", "/static/logo.png")

    # Seguridad y DB
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-please")
    # Ejemplo: postgresql+asyncpg://USER:PASS@HOST/DB?sslmode=require
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db?sslmode=require"
    )

    # (Opcional) restringir dominio del primer admin
    ADMIN_EMAIL_DOMAIN: str = os.getenv("ADMIN_EMAIL_DOMAIN", "")

settings = Settings()
