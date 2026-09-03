# Imágenes del catálogo

Esta carpeta se sirve públicamente en `/static/catalog/` (ver `backend/main.py`).

Cada producto de `database/farmhouse_catalog_meta.csv` referencia su imagen como:

```
https://farmhousepanelwhatsapp-production.up.railway.app/static/catalog/{SKU}.jpg
```

Para que el catálogo de Meta y los mensajes de WhatsApp muestren fotos reales,
sube aquí un archivo `{SKU}.jpg` por cada producto (el SKU es la columna `id`
del CSV, ej. `SAL_CAESAR_REG.jpg`). Sin estos archivos, Meta no podrá
descargar las imágenes al sincronizar el catálogo.
