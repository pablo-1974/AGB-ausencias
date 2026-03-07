#!/usr/bin/env bash
set -e  # parar si hay error

# Ejecutar migraciones
alembic upgrade head

# Lanzar la app
exec uvicorn app:app --host 0.0.0.0 --port "$PORT"
