"""Mise en page du devis en PDF, avec les mentions attendues sur un devis de travaux."""

import io
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .engine import euros, totaux

INK = colors.HexColor("#14202e")
MUTED = colors.HexColor("#5f6b7a")
LINE = colors.HexColor("#d9dee5")
ACCENT = colors.HexColor("#e8a317")
SOFT = colors.HexColor("#f5f7fa")

S = {
    "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK),
    "b": ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK),
    "bb": ParagraphStyle("bb", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=INK),
    "m": ParagraphStyle("m", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=MUTED),
    "lab": ParagraphStyle("lab", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED),
    "r": ParagraphStyle("r", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK, alignment=TA_RIGHT),
}


def esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text, style="b"):
    """Texte utilisateur : échappé."""
    return Paragraph(esc(text).replace("\n", "<br/>"), S[style])


def _m(markup, style="b"):
    """Texte déjà balisé (les parties utilisateur passent par esc)."""
    return Paragraph(markup.replace("\n", "<br/>"), S[style])


def _fmt_q(q):
    return f"{q:g}".replace(".", ",")


def build(d: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"Devis {d['numero']}", author=d["entreprise"].get("nom", ""))
    e, c = d["entreprise"], d["client"]
    tva = float(d.get("tva", 20))
    created = datetime.fromisoformat(d["created_at"])
    validite = created + timedelta(days=int(d.get("validite_jours") or 30))
    t = totaux(d["lignes"], tva)
    story = []

    # en-tête : entreprise | numéro
    ent = [f"<b>{esc(e.get('nom')) or '[Nom de l’entreprise]'}</b>", esc(e.get("adresse")),
           esc(" · ".join(x for x in [e.get("tel"), e.get("email")] if x)),
           f"SIRET {esc(e.get('siret'))}" if e.get("siret") else "SIRET : [à compléter]"]
    if e.get("tva_intra") and tva > 0:
        ent.append(f"TVA intracommunautaire : {esc(e['tva_intra'])}")
    left = Paragraph("<br/>".join(x for x in ent if x), S["b"])
    right = Table([[Paragraph("DEVIS", S["h"])], [_p(f"N° {d['numero']}", "bb")],
                   [_p(f"Date : {created:%d/%m/%Y}", "r")], [_p(f"Valable jusqu'au {validite:%d/%m/%Y}", "r")]],
                  colWidths=[60 * mm])
    right.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    head = Table([[left, right]], colWidths=[118 * mm, 60 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 4 * mm)]
    bar = Table([[""]], colWidths=[178 * mm], rowHeights=[2])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    story += [bar, Spacer(1, 5 * mm)]

    # client / chantier
    cli = Table([[_p("CLIENT", "lab"), _p("ADRESSE DU CHANTIER", "lab")],
                 [_m(f"<b>{esc(c.get('nom')) or '[Client]'}</b>\n" + "\n".join(esc(x) for x in [c.get("email"), c.get("tel")] if x)),
                  _p(c.get("adresse") or "[Adresse]")]], colWidths=[89 * mm, 89 * mm])
    cli.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [cli, Spacer(1, 5 * mm)]
    if d.get("description"):
        story += [_p("OBJET DES TRAVAUX", "lab"), Spacer(1, 1.5 * mm), _p(d["description"]), Spacer(1, 5 * mm)]

    # lignes
    rows = [[_p("DÉSIGNATION", "lab"), _p("QTÉ", "lab"), _p("UNITÉ", "lab"), _p("PU HT", "lab"), _p("TOTAL HT", "lab")]]
    for l in d["lignes"]:
        rows.append([_p(l["libelle"]), _p(_fmt_q(l["quantite"]), "r"), _p(l["unite"]), _p(euros(l["prix"]), "r"),
                     _p(euros(l["quantite"] * l["prix"]), "r")])
    tab = Table(rows, colWidths=[86 * mm, 16 * mm, 20 * mm, 27 * mm, 29 * mm], repeatRows=1)
    tab.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1, INK), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [tab, Spacer(1, 4 * mm)]

    # totaux
    tot = [["Total HT", euros(t["ht"])]]
    if tva > 0:
        tot.append([f"TVA {str(tva).rstrip('0').rstrip('.').replace('.', ',')} %", euros(t["tva"])])
    tot.append(["Total TTC" if tva > 0 else "Total à payer", euros(t["ttc"])])
    tt = Table([[_p(a, "b"), _p(b, "r")] for a, b in tot], colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 1, INK), ("BACKGROUND", (0, -1), (-1, -1), SOFT),
                            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [tt]
    if tva == 0:
        story += [Spacer(1, 1.5 * mm), Paragraph("TVA non applicable, art. 293 B du CGI.", ParagraphStyle("x", parent=S["m"], alignment=TA_RIGHT))]
    acompte = int(d.get("acompte") or 0)
    if acompte:
        story += [Spacer(1, 1.5 * mm), Paragraph(f"Acompte à la signature ({acompte} %) : <b>{euros(t['ttc'] * acompte / 100)}</b>",
                                                 ParagraphStyle("y", parent=S["b"], alignment=TA_RIGHT))]
    story += [Spacer(1, 6 * mm)]

    # conditions
    cond = [f"Devis gratuit, valable {int(d.get('validite_jours') or 30)} jours.",
            f"Conditions de paiement : {'acompte de ' + str(acompte) + ' % à la signature, solde ' if acompte else 'paiement '}à la fin des travaux, par virement ou chèque.",
            "En cas de retard de paiement, pénalités au taux légal en vigueur et indemnité forfaitaire de 40 € pour frais de recouvrement (clients professionnels)."]
    if d.get("delai"):
        cond.insert(1, f"Délai d'intervention prévu : {d['delai']}.")
    if e.get("assurance"):
        cond.append(f"Assurance décennale : {e['assurance']}.")
    else:
        cond.append("Assurance professionnelle / décennale : [assureur, n° de contrat, zone couverte à compléter].")
    story += [_p("CONDITIONS", "lab"), Spacer(1, 1.5 * mm)] + [_p("• " + x, "m") for x in cond] + [Spacer(1, 6 * mm)]

    # bon pour accord
    acc = d.get("acceptation")
    if acc:
        sig = _m(f"<b>Accepté en ligne</b> le {esc(acc['date'])} par {esc(acc['nom'])}.\nMention : « Devis reçu avant l'exécution des travaux, bon pour accord ».")
    else:
        sig = _p("Date, signature et mention manuscrite :\n« Devis reçu avant l'exécution des travaux, bon pour accord »\n\n\n")
    box = Table([[_p("BON POUR ACCORD DU CLIENT", "lab")], [sig]], colWidths=[100 * mm], hAlign="RIGHT")
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, INK), ("TOPPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#eef9f1") if acc else colors.white), ("BOTTOMPADDING", (0, 1), (-1, 1), 14)]))
    story += [KeepTogether([box])]

    def footer(canv, _doc):
        canv.saveState()
        canv.setFont("Helvetica", 7.5)
        canv.setFillColor(MUTED)
        canv.drawString(16 * mm, 9 * mm, f"Devis {d['numero']} · {e.get('nom') or ''}")
        canv.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {canv.getPageNumber()} · préparé avec Hugo · Standia")
        canv.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
