"""Lecture du contenu des documents déposés.

Ordre d'essai pour chaque fichier :
1. texte intégré au PDF (gratuit, instantané) ;
2. si le fichier est un scan ou une photo (pas ou peu de texte) :
   - lecture par GPT (vision) si OPENAI_API_KEY est définie ;
   - sinon OCR Tesseract s'il est installé sur la machine.
"""

import logging
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader

from . import ai

log = logging.getLogger(__name__)

PDF_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EXT = PDF_EXT | IMAGE_EXT

MAX_CHARS = 6000          # le début suffit pour classer
MAX_PDF_PAGES_TEXT = 5
MAX_PAGES_IMAGE = 3       # pages envoyées à la lecture d'image
MIN_TEXT = 60             # en dessous, on considère que c'est un scan

_ocr_lang = None


def _tesseract(image: Image.Image) -> str:
    global _ocr_lang
    try:
        import pytesseract
        if _ocr_lang is None:
            langs = set(pytesseract.get_languages(config=""))
            _ocr_lang = "fra+eng" if "fra" in langs else "eng"
        return pytesseract.image_to_string(image, lang=_ocr_lang)
    except Exception:
        return ""


def _lire_images(images) -> tuple[str, str]:
    """Retourne (texte, méthode)."""
    if ai.disponible():
        text = ai.lire_images(images)
        if text:
            return text, "ia"
    text = "\n".join(_tesseract(img) for img in images)
    return text, ("ocr" if text.strip() else "aucune")


def _pdf_images(path: Path):
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        return [pdf[i].render(scale=1.6).to_pil() for i in range(min(len(pdf), MAX_PAGES_IMAGE))]
    except Exception as exc:
        log.warning("Rendu PDF impossible (%s) : %s", path.name, exc)
        return []


def extract(path: Path) -> dict:
    """Retourne {"text", "pages", "kind", "lecture"} pour un fichier déposé."""
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        parts = []
        for page in reader.pages[:MAX_PDF_PAGES_TEXT]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                pass
        text, lecture = "\n".join(parts).strip(), "texte"
        if len(text) < MIN_TEXT:
            scanned, lecture = _lire_images(_pdf_images(path))
            text = (text + "\n" + scanned).strip()
        return {"text": text[:MAX_CHARS], "pages": pages, "kind": "pdf", "lecture": lecture}

    if ext in IMAGE_EXT:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            text, lecture = _lire_images([img])
        return {"text": text[:MAX_CHARS], "pages": 1, "kind": "image", "lecture": lecture}

    raise ValueError(f"Format non pris en charge : {ext}")
