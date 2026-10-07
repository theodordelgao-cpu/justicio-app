"""Lecture du contenu des documents déposés : texte des PDF, OCR des scans et des photos."""

import logging
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader

log = logging.getLogger(__name__)

PDF_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EXT = PDF_EXT | IMAGE_EXT

MAX_CHARS = 6000          # on ne garde que le début : suffisant pour classer
MAX_PDF_PAGES_TEXT = 5
MAX_PDF_PAGES_OCR = 2

_ocr_lang = None


def _lang():
    """Utilise le français si les données Tesseract sont installées (paquet tesseract-ocr-fra)."""
    global _ocr_lang
    if _ocr_lang is None:
        try:
            import pytesseract
            langs = set(pytesseract.get_languages(config=""))
            _ocr_lang = "fra+eng" if "fra" in langs else "eng"
        except Exception:
            _ocr_lang = "eng"
    return _ocr_lang


def _ocr(image: Image.Image) -> str:
    try:
        import pytesseract
        return pytesseract.image_to_string(image, lang=_lang())
    except Exception as exc:  # OCR indisponible : on classe sur le nom de fichier
        log.warning("OCR impossible : %s", exc)
        return ""


def _pdf_text(path: Path) -> tuple[str, int]:
    reader = PdfReader(str(path))
    pages = len(reader.pages)
    parts = []
    for page in reader.pages[:MAX_PDF_PAGES_TEXT]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            pass
    text = "\n".join(parts).strip()

    # PDF scanné (pas de couche texte) : OCR des premières pages
    if len(text) < 50:
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(path))
            for i in range(min(len(pdf), MAX_PDF_PAGES_OCR)):
                img = pdf[i].render(scale=2).to_pil()
                text += "\n" + _ocr(img)
        except Exception as exc:
            log.warning("Rendu PDF pour OCR impossible (%s) : %s", path.name, exc)
    return text[:MAX_CHARS], pages


def extract(path: Path) -> dict:
    """Retourne {"text", "pages", "kind"} pour un fichier déposé."""
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        text, pages = _pdf_text(path)
        return {"text": text, "pages": pages, "kind": "pdf"}
    if ext in IMAGE_EXT:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            text = _ocr(img)
        return {"text": text[:MAX_CHARS], "pages": 1, "kind": "image"}
    raise ValueError(f"Format non pris en charge : {ext}")
