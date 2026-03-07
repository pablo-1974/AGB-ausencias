# ====== 1) Base ======
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1

# Paquetes del sistema: libpq, fuentes (reportlab)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# ====== 2) Dependencias ======
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# ====== 3) Código ======
COPY . .

# ====== 4) Arranque ======
EXPOSE 10000
# Render inyecta $PORT; en local toma 10000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "${PORT:-10000}"]
