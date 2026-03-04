import os

class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://localhost/postgres")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "super-secret-key-change-me")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

settings = Settings()