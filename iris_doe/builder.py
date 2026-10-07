"""Assemblage du DOE final en un seul PDF :
page de garde, sommaire paginé, intercalaire par section, documents, pied de page numéroté."""

import io
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .referentiel import CATEGORY_LABELS, CATEGORY_ORDER, LOTS

INK = HexColor("#1b2430")
MUTED = HexColor("#6b7480")
ACCENT = HexColor("#2f6f5e")
W, H = A4
TOC_LINES_PER_PAGE = 34


# ---------- pages générées ----------

def _wrap(c, text, font, size, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if c.stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _cover(meta: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFillColor(ACCENT)
    c.rect(0, H - 18 * mm, W, 18 * mm, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("Helvetica", 11)
    c.drawString(25 * mm, H - 60 * mm, "DOSSIER DES OUVRAGES EXÉCUTÉS")
    c.setFont("Helvetica-Bold", 26)
    y = H - 78 * mm
    for line in _wrap(c, meta["nom"], "Helvetica-Bold", 26, W - 50 * mm):
        c.drawString(25 * mm, y, line)
        y -= 11 * mm

    y -= 12 * mm
    rows = [
        ("Maître d'ouvrage", meta.get("maitre_ouvrage")),
        ("Entreprise", meta.get("entreprise")),
        ("Adresse du chantier", meta.get("adresse")),
        ("Lots", ", ".join(LOTS[l]["label"] for l in meta.get("lots", []))),
        ("Date d'édition", date.today().strftime("%d/%m/%Y")),
    ]
    for label, value in rows:
        if not value:
            continue
        c.setFont("Helvetica", 9)
        c.setFillColor(MUTED)
        c.drawString(25 * mm, y, label.upper())
        c.setFont("Helvetica", 12)
        c.setFillColor(INK)
        for line in _wrap(c, value, "Helvetica", 12, W - 50 * mm):
            y -= 6 * mm
            c.drawString(25 * mm, y, line)
        y -= 10 * mm

    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawString(25 * mm, 15 * mm, "Dossier assemblé avec Iris · Standia")
    c.showPage()
    c.save()
    return buf.getvalue()


def _toc(entries: list[tuple[str, str, int]], n_pages: int) -> bytes:
    """entries : (niveau 'section'|'doc', libellé, page de début)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    def header():
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(25 * mm, H - 30 * mm, "Sommaire")
        return H - 45 * mm

    y = header()
    count = 0
    for level, label, page in entries:
        if count and count % TOC_LINES_PER_PAGE == 0:
            c.showPage()
            y = header()
        if level == "section":
            y -= 3 * mm
            c.setFont("Helvetica-Bold", 11)
            c.setFillColor(ACCENT)
            x = 25 * mm
        else:
            c.setFont("Helvetica", 10)
            c.setFillColor(INK)
            x = 31 * mm
        font = "Helvetica-Bold" if level == "section" else "Helvetica"
        size = 11 if level == "section" else 10
        max_w = W - x - 45 * mm
        while label and c.stringWidth(label, font, size) > max_w:
            label = label[:-2]
        c.drawString(x, y, label)
        c.drawRightString(W - 25 * mm, y, str(page))
        # pointillés
        c.setFillColor(MUTED)
        start = x + c.stringWidth(label, font, size) + 2 * mm
        end = W - 25 * mm - c.stringWidth(str(page), font, size) - 2 * mm
        c.setFont("Helvetica", 8)
        dots = "." * max(0, int((end - start) / c.stringWidth(".", "Helvetica", 8)))
        c.drawString(start, y, dots)
        y -= 6.5 * mm
        count += 1

    c.showPage()
    # garantit le nombre de pages prévu (calcul de pagination)
    pages_written = max(1, -(-count // TOC_LINES_PER_PAGE))
    for _ in range(n_pages - pages_written):
        c.showPage()
    c.save()
    return buf.getvalue()


def _divider(numero: int, label: str, nb_docs: int) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFillColor(ACCENT)
    c.rect(0, 0, 12 * mm, H, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 12)
    c.drawString(30 * mm, H / 2 + 20 * mm, f"SECTION {numero}")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 22)
    y = H / 2 + 6 * mm
    for line in _wrap(c, label, "Helvetica-Bold", 22, W - 55 * mm):
        c.drawString(30 * mm, y, line)
        y -= 10 * mm
    c.setFont("Helvetica", 11)
    c.setFillColor(MUTED)
    c.drawString(30 * mm, y - 4 * mm, f"{nb_docs} document{'s' if nb_docs > 1 else ''}")
    c.showPage()
    c.save()
    return buf.getvalue()


def _image_page(path: Path, legende: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((2000, 2000))
        iw, ih = img.size
        margin, top, bottom = 18 * mm, 28 * mm, 25 * mm
        max_w, max_h = W - 2 * margin, H - top - bottom
        ratio = min(max_w / iw, max_h / ih)
        dw, dh = iw * ratio, ih * ratio
        c.drawImage(ImageReader(img), (W - dw) / 2, bottom + (max_h - dh) / 2, dw, dh)
    c.setFont("Helvetica", 10)
    c.setFillColor(INK)
    c.drawString(margin, H - 18 * mm, legende[:110])
    c.showPage()
    c.save()
    return buf.getvalue()


def _footer(width, height, text: str) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUTED)
    c.drawString(18 * mm, 7 * mm, text[0])
    c.drawRightString(width - 12 * mm, 7 * mm, text[1])
    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf)


# ---------- assemblage ----------

def build_doe(meta: dict, files_dir: Path) -> bytes:
    # 1. Regroupe les documents par section, dans l'ordre du référentiel
    sections = []
    for key in CATEGORY_ORDER:
        docs = [d for d in meta["documents"] if d["categorie"] == key]
        if docs:
            sections.append((key, sorted(docs, key=lambda d: d["titre"].lower())))

    # 2. Prépare chaque document en PDF et compte ses pages
    prepared = {}
    for _, docs in sections:
        for d in docs:
            path = files_dir / d["stored_name"]
            if d["kind"] == "image":
                data = _image_page(path, d["titre"])
            else:
                data = path.read_bytes()
            try:
                n = len(PdfReader(io.BytesIO(data)).pages)
            except Exception:
                continue  # PDF illisible : ignoré plutôt que de faire planter le dossier
            prepared[d["id"]] = (data, n)
    sections = [(k, [d for d in docs if d["id"] in prepared]) for k, docs in sections]
    sections = [(k, docs) for k, docs in sections if docs]

    # 3. Pagination : garde (1) + sommaire + intercalaires + documents
    n_entries = sum(1 + sum(1 for d in docs if d["id"] in prepared) for _, docs in sections)
    toc_pages = max(1, -(-n_entries // TOC_LINES_PER_PAGE))
    page = 1 + toc_pages + 1
    entries = []
    for key, docs in sections:
        entries.append(("section", CATEGORY_LABELS[key], page))
        page += 1
        for d in docs:
            if d["id"] in prepared:
                entries.append(("doc", d["titre"], page))
                page += prepared[d["id"]][1]
    total = page - 1

    # 4. Assemble
    writer = PdfWriter()

    def append(data: bytes):
        for p in PdfReader(io.BytesIO(data)).pages:
            writer.add_page(p)

    append(_cover(meta))
    append(_toc(entries, toc_pages))
    for i, (key, docs) in enumerate(sections, 1):
        kept = [d for d in docs if d["id"] in prepared]
        if not kept:
            continue
        append(_divider(i, CATEGORY_LABELS[key], len(kept)))
        for d in kept:
            append(prepared[d["id"]][0])

    # 5. Pied de page numéroté (sauf page de garde)
    left = f"DOE · {meta['nom']}"[:90]
    for i, p in enumerate(writer.pages):
        if i == 0:
            continue
        try:
            p.transfer_rotation_to_content()
        except Exception:
            pass
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        overlay = _footer(w, h, (left, f"Page {i + 1} / {total}"))
        p.merge_page(overlay.pages[0])

    writer.add_metadata({"/Title": f"DOE - {meta['nom']}", "/Producer": "Iris · Standia"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
