FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r /app/requirements.txt

# Solo lo que el servicio necesita: nada del sistema central.
COPY main.py /app/main.py
COPY static /app/static

# Sin privilegios de administrador dentro del contenedor.
RUN useradd --create-home --uid 10001 intake && mkdir -p /data && chown -R intake /app /data
USER intake

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
