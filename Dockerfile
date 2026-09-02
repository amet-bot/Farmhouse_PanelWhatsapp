FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias del sistema mínimas necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/backend/requirements.txt

# Copiar todo el código del proyecto (backend, frontend, database)
COPY . /app

# Puerto por defecto para Railway / Cloud
ENV PORT=8000
EXPOSE 8000

# Directorio de trabajo en backend para Alembic y FastAPI
WORKDIR /app/backend

# Comando de inicio: ejecuta migraciones y arranca FastAPI
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
