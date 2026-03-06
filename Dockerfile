# ====== 1) Base ======
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1

# Paquetes del sistema: libpq, fuentes (reportlab), locales básicos
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# ====== 2) Depencias ======
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# ====== 3) Código ======
COPY . .

# (Opcional) Si usas alembic.ini en la raíz, ya está copiado
# Si tu alembic.ini no está en raíz, ajusta el working dir o la ruta en el CMD

# ====== 4) Arranque ======
# Render provee $PORT; en local toma 10000 por defecto
EXPOSE 10000

CMD /bin/sh -lc "alembic upgrade head && uvicorn project.app:app --host 0.0.0.0 --port ${PORT:-10000}"
