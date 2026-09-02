#!/bin/bash
set -e

echo "[STARTUP] Ejecutando migraciones de base de datos..."
cd /app/backend || cd backend
alembic upgrade head

echo "[STARTUP] Ejecutando seed de datos iniciales (sucursales y admin)..."
python seeds/seed_data.py

echo "[STARTUP] Arrancando servidor FastAPI en puerto ${PORT:-8000}..."
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
