# config.py
import os
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Settings(BaseModel):
    # Branding / UI
    APP_NAME: str = os.getenv("APP_NAME", "Ausencias de profesores")
    INSTITUTION_NAME: str = os.getenv("INSTITUTION_NAME", "IES Antonio García Bellido")

    # Ruta REAL al logo para PDFs (ReportLab)
    LOGO_PATH: str = os.getenv(
        "LOGO_PATH",
        os.path.join(BASE_DIR, "static", "logo.png")
    )

    # Seguridad y DB
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-please")

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db?sslmode=require"
    )

    ADMIN_EMAIL_DOMAIN: str = os.getenv("ADMIN_EMAIL_DOMAIN", "")


settings = Settings()
