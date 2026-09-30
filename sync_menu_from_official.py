#!/usr/bin/env python3
"""
Sincroniza el menú del farmhouse-whatsapp-center con la página oficial.
Usa la información de site.ts de Página Oficial farmhouse para actualizar
el catálogo y las imágenes.
"""

import csv
import re
from pathlib import Path
from decimal import Decimal

# Mapeo de nombres de imágenes de site.ts a SKU del catálogo
IMAGE_MAPPING = {
    # Ensaladas
    "ensalada-sassy-caesar.webp": "SAL_CAESAR",
    "ensalada-la-lupita.webp": "SAL_LUPITA",
    "ensalada-la-greca.webp": "SAL_GRECA",

    # Bowls
    "bowl-la-cosecha.webp": "BWL_COSECHA",
    "bowl-crispy-sai.webp": "BWL_CRISPYSAI",
    "bowl-la-levantina.webp": "BWL_LEVANTINA",
    "bowl-la-otona.webp": "BWL_OTONA",
    "bowl-la-buff-con-pollo.webp": "BWL_BUFF",
    "bowl-la-tipica.webp": "BWL_TIPICA",
    "bowl-la-lady-mama.webp": "BWL_LADY_MAMA",

    # Wraps
    "wrap-el-cesar.webp": "WRP_CESAR",
    "wrap-el-lupito.webp": "WRP_LUPITO",

    # Build Your Own
    "byo-pollo-rostizado.webp": "PRM_POLLO_ROSTIZADO",
    "byo-pollo-spiced.webp": "PRM_POLLO_SPICED",
    "byo-salmon-bulgogi.webp": "PRM_SALMON_BULGOGI",
    "byo-tofu-bulgogi.webp": "PRM_TOFU_BULGOGI",
    "byo-steak.webp": "PRM_STEAK",
    "byo-parmesan-crumble.webp": "PRM_PARMESAN_CRUMBLE",
    "byo-queso-de-cabra.webp": "PRM_QUESO_CABRA",
    "byo-whipped-feta.webp": "PRM_WHIPPED_FETA",
    "byo-avo-smash.webp": "PRM_AVO_SMASH",
    "byo-huevo-duro.webp": "PRM_HUEVO_DURO",
    "byo-hummus.webp": "PRM_HUMMUS",
    "byo-chilli-crunch.webp": "BYO_CHILLI_CRUNCH",

    # Toasties
    "toastie-avotuna.webp": "TOA_AVOTUNA",
    "toastie-hot-tuna.webp": "TOA_HOTTUNA",
    "toastie-pesto-chicken.webp": "TOA_PESTOCHICKEN",
    "toastie-serranito-margarita.webp": "TOA_SERRANITO",
    "toastie-turkey-melt.webp": "TOA_TURKEYMELT",
    "toastie-avopesto.webp": "TOA_AVOPESTO",

    # Smoothies
    "smoothie-supernova.webp": "SMO_SUPERNOVA",
    "smoothie-marble-acai.webp": "SMO_MARBLE_ACAI",
    "smoothie-choco-shake.webp": "SMO_CHOCO_SHAKE",
    "smoothie-frostee.webp": "SMO_FROSTEE",
    "smoothie-mango.webp": "SMO_MANGO",
    "smoothie-papaya.webp": "SMO_PAPAYA",
    "smoothie-pretty-in-pink.webp": "SMO_PRETTY_IN_PINK",
    "smoothie-nutty-chai.webp": "SMO_NUTTY_CHAI",
    "smoothie-tropicalia.webp": "SMO_TROPICALIA",
    "smoothie-evergreen.webp": "SMO_EVERGREEN",
    "smoothie-blush.webp": "SMO_BLUSH",
    "smoothie-summertime.webp": "SMO_SUMMERTIME",
    "smoothie-extra-proteina.webp": "SMO_EXTRA_PROTEIN",
    "smoothie-extra-crema-frutos-secos.webp": "SMO_EXTRA_ALMOND",
    "smoothie-extra-mantequilla-mani.webp": "SMO_EXTRA_PEANUT",
    "smoothie-extra-cacao-nibs.webp": "SMO_EXTRA_CACAO",

    # Bebidas
    "bebida-latte.webp": "BEB_LATTE",
    "bebida-cold-brew.webp": "BEB_COLD_BREW",
    "bebida-matcha-latte.webp": "BEB_MATCHA_LATTE",
    "bebida-limonada-de-coco.webp": "BEB_LIMONADA_COCO",
    "bebida-golden-milk.webp": "BEB_GOLDEN_MILK",
    "bebida-gold-cold-pressed.webp": "BEB_GOLD",
    "bebida-detox-shot.webp": "BEB_DETOX_SHOT",
    "bebida-chicha-de-saril.webp": "BEB_CHICHA_SARIL",

    # Foamies
    "foamies.webp": "FOA_MENU",

    # Vitrina
    "vitrina-banana-bread.webp": "VIT_BANANA_BREAD",
    "vitrina-galleta-choco-chip.webp": "VIT_GALLETA_CHOCO",
    "vitrina-galleta-avena-choco.webp": "VIT_GALLETA_AVENA",
    "vitrina-galleta-walnut-brownie.webp": "VIT_GALLETA_WALNUT",
}

def update_image_urls(csv_path):
    """Actualiza las URLs de imágenes en el CSV."""

    with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Actualizar URLs de imagen para items con mapeo
    for row in rows:
        sku = row.get('id', '').strip()
        # Buscar si el SKU tiene un mapeo de imagen
        for img_name, sku_prefix in IMAGE_MAPPING.items():
            if sku.startswith(sku_prefix):
                row['image_link'] = f"/frontend/static/images/menu/{img_name}"
                break

    # Reescribir el CSV
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"✓ Actualizado: {csv_path}")

if __name__ == "__main__":
    project_root = Path(__file__).parent
    csv_path = project_root / "database" / "farmhouse_catalog_meta.csv"

    if csv_path.exists():
        update_image_urls(csv_path)
        print("✓ Menú sincronizado con imágenes WebP de Página Oficial")
    else:
        print(f"✗ No se encontró el archivo CSV: {csv_path}")
