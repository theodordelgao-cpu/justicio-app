import html
import io
from datetime import datetime
from urllib.parse import quote

from flask import abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from iris_doe import ai

from . import bp, mail, store
from .engine import euros, generer, parse_lignes, totaux
from .grilles import EXEMPLES, METIERS, TVA_OPTIONS, grille_json
from .pdf import build

MAX_ENVOIS = 20


def _abs(endpoint, **kw):
    path = url_for(endpoint, **kw)
    host = request.host
    scheme = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"
    return f"{scheme}://{host}{path}"


def _load(devis_id):
    try:
        return store.load(devis_id)
    except FileNotFoundError:
        abort(404)


def _by_token(token):
    try:
        return store.by_token(token)
    except FileNotFoundError:
        abort(404)


def _pdf_name(d):
    return f"Devis_{d['numero']}.pdf"


def _txt(name, limit=200):
    return (request.form.get(name) or "").strip()[:limit]


# ------------------------------------------------------------------ éditeur

@bp.get("/")
def index():
    source = None
    if request.args.get("from"):
        try:
            source = store.load(request.args["from"])
        except FileNotFoundError:
            source = None
    return render_template(
        "hugo/index.html", ia=ai.disponible(), source=source,
        metiers={k: {"label": v["label"], "grille": grille_json(k)} for k, v in METIERS.items()},
        exemples=EXEMPLES, tva_options=TVA_OPTIONS,
    )


@bp.post("/api/preparer")
def api_preparer():
    data = request.get_json(silent=True) or {}
    metier = data.get("metier") if data.get("metier") in METIERS else "plomberie"
    demande = str(data.get("demande") or "").strip()[:3000]
    if len(demande) < 8:
        return jsonify({"erreur": "Décrivez la demande du client en une phrase au moins."}), 400
    grille = []
    for g in (data.get("grille") or [])[:80]:
        try:
            grille.append({"libelle": str(g["libelle"])[:140], "unite": str(g.get("unite") or "unité")[:20],
                           "prix": float(g.get("prix") or 0), "mots": [str(m)[:40] for m in (g.get("mots") or [])][:12]})
        except (KeyError, TypeError, ValueError):
            continue
    grille = grille or grille_json(metier)
    return jsonify(generer(METIERS[metier]["label"], demande, grille))


@bp.post("/devis")
def creer():
    lignes = parse_lignes(request.form.get("lignes_json"))
    if not lignes:
        flash("Ajoutez au moins une ligne au devis.", "erreur")
        return redirect(url_for(".index"))
    try:
        tva = float(request.form.get("tva") or 20)
    except ValueError:
        tva = 20.0
    if tva not in (0, 5.5, 10, 20):
        tva = 20.0

    def _int(name, default, lo, hi):
        try:
            return max(lo, min(hi, int(request.form.get(name) or default)))
        except ValueError:
            return default

    d = store.create({
        "metier": request.form.get("metier") if request.form.get("metier") in METIERS else "plomberie",
        "entreprise": {k: _txt("e_" + k, 300) for k in ("nom", "siret", "adresse", "tel", "email", "assurance", "tva_intra")},
        "client": {k: _txt("c_" + k, 300) for k in ("nom", "adresse", "email", "tel")},
        "description": _txt("description", 1200),
        "lignes": lignes, "tva": tva,
        "acompte": _int("acompte", 30, 0, 100), "validite_jours": _int("validite", 30, 1, 365),
        "delai": _txt("delai", 120), "origine": _txt("origine", 20) or "hugo",
    })
    return redirect(url_for(".devis", devis_id=d["id"]))


# ------------------------------------------------------------------ côté artisan

@bp.get("/devis/<devis_id>")
def devis(devis_id):
    d = _load(devis_id)
    lien = _abs(".client", token=d["token"])
    prenom = (d["entreprise"].get("nom") or "votre artisan")
    msg = f"Bonjour {d['client'].get('nom') or ''}, voici votre devis {d['numero']} de {prenom} : {lien}"
    tel = "".join(ch for ch in (d["client"].get("tel") or "") if ch.isdigit() or ch == "+")
    if tel.startswith("0") and len(tel) == 10:
        tel = "33" + tel[1:]
    return render_template("hugo/devis.html", d=d, t=totaux(d["lignes"], d["tva"]), euros=euros, lien=lien,
                           whatsapp_url=f"https://wa.me/{tel.lstrip('+')}?text=" + quote(msg),
                           sms_body=quote(msg), mail_ok=mail.configure())


@bp.get("/devis/<devis_id>/devis.pdf")
def devis_pdf(devis_id):
    d = _load(devis_id)
    return send_file(io.BytesIO(build(d)), mimetype="application/pdf", download_name=_pdf_name(d),
                     as_attachment=request.args.get("dl") == "1")


@bp.get("/devis/<devis_id>/apercu.png")
def devis_apercu(devis_id):
    """Aperçu image de la 1re page : s'affiche partout, y compris sur mobile."""
    import pypdfium2 as pdfium
    d = _load(devis_id)
    page = pdfium.PdfDocument(build(d))[0]
    img = page.render(scale=1.6).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    resp = send_file(buf, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/devis/<devis_id>/envoyer")
def envoyer(devis_id):
    d = _load(devis_id)
    if len(d["envois"]) >= MAX_ENVOIS:
        flash("Limite d'envois atteinte pour ce devis.", "erreur")
        return redirect(url_for(".devis", devis_id=devis_id))
    dest = (request.form.get("email") or d["client"].get("email") or "").strip()
    e = d["entreprise"]
    t = totaux(d["lignes"], d["tva"])
    lien = _abs(".client", token=d["token"])
    nom_ent = html.escape(e.get("nom") or "Votre artisan")
    corps = f"""<div style="font-family:Arial,sans-serif;color:#14202e;max-width:560px">
      <p>Bonjour {html.escape(d['client'].get('nom') or '')},</p>
      <p>{nom_ent} vous adresse le devis <b>{d['numero']}</b> d'un montant de <b>{euros(t['ttc'])}</b>{' TTC' if d['tva'] else ''}.</p>
      <p style="margin:22px 0"><a href="{lien}" style="background:#e8a317;color:#1d1400;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:bold">Voir et accepter le devis</a></p>
      <p style="font-size:13px;color:#5f6b7a">Le devis est aussi joint en PDF. Vous pouvez l'accepter en ligne en un clic.</p>
      <p style="font-size:12px;color:#5f6b7a;margin-top:26px">Envoyé pour {nom_ent} par Hugo · Standia</p></div>"""
    texte = f"Bonjour,\n\n{e.get('nom') or 'Votre artisan'} vous adresse le devis {d['numero']} ({euros(t['ttc'])}).\nVoir et accepter : {lien}\n"
    ok, info = mail.envoyer(dest, f"Devis {d['numero']} · {e.get('nom') or 'votre artisan'}", corps, texte,
                            (_pdf_name(d), build(d)), reply_to=e.get("email"), nom_expediteur=f"{e.get('nom') or 'Votre artisan'} via Hugo")
    if ok:
        d["envois"].append({"to": dest, "at": datetime.now().isoformat(timespec="seconds")})
        if d["statut"] == "brouillon":
            d["statut"] = "envoye"
        store.save(d)
    flash(info, "ok" if ok else "erreur")
    return redirect(url_for(".devis", devis_id=devis_id))


@bp.post("/devis/<devis_id>/statut")
def marquer_envoye(devis_id):
    d = _load(devis_id)
    if d["statut"] == "brouillon":
        d["statut"] = "envoye"
        store.save(d)
    return ("", 204)


# ------------------------------------------------------------------ côté client

@bp.get("/c/<token>")
def client(token):
    d = _by_token(token)
    return render_template("hugo/client.html", d=d, t=totaux(d["lignes"], d["tva"]), euros=euros)


@bp.get("/c/<token>/devis.pdf")
def client_pdf(token):
    d = _by_token(token)
    return send_file(io.BytesIO(build(d)), mimetype="application/pdf", download_name=_pdf_name(d),
                     as_attachment=request.args.get("dl") == "1")


@bp.post("/c/<token>/accepter")
def accepter(token):
    d = _by_token(token)
    nom = _txt("nom", 120)
    if d.get("acceptation"):
        return redirect(url_for(".client", token=token))
    if not nom or request.form.get("accord") != "1":
        flash("Indiquez votre nom et cochez la case « bon pour accord ».", "erreur")
        return redirect(url_for(".client", token=token) + "#accepter")
    d["acceptation"] = {"nom": nom, "date": datetime.now().strftime("%d/%m/%Y à %H:%M")}
    d["statut"] = "accepte"
    store.save(d)
    e = d["entreprise"]
    if e.get("email") and mail.configure():
        t = totaux(d["lignes"], d["tva"])
        mail.envoyer(e["email"], f"✅ Devis {d['numero']} accepté par {nom}",
                     f"<p>Bonne nouvelle : <b>{html.escape(nom)}</b> a accepté le devis <b>{d['numero']}</b> ({euros(t['ttc'])}).</p>"
                     f"<p><a href=\"{_abs('.devis', devis_id=d['id'])}\">Ouvrir le devis</a></p>",
                     f"{nom} a accepté le devis {d['numero']} ({euros(t['ttc'])}).")
    flash("Merci, votre accord a bien été enregistré. L'artisan est prévenu.", "ok")
    return redirect(url_for(".client", token=token))


# ------------------------------------------------------------------ démo Léo → Hugo

@bp.get("/demo-leo")
def demo_leo():
    ex = EXEMPLES[0]
    res = generer(METIERS[ex["metier"]]["label"], ex["demande"], grille_json(ex["metier"]))
    if res["source"] != "regles":
        from .engine import _par_regles
        res = _par_regles(ex["demande"], grille_json(ex["metier"]))
    d = {
        "numero": "D-DEMO-0001", "created_at": datetime.now().isoformat(timespec="seconds"),
        "entreprise": {"nom": "Plomberie Martin", "siret": "[SIRET de l'artisan]", "adresse": "Toulouse",
                       "tel": "", "email": "", "assurance": ""},
        "client": {"nom": ex["client"], "adresse": ex["adresse"], "email": "", "tel": ""},
        "description": "Recherche et réparation d'une fuite sous l'évier de la cuisine, remplacement du siphon et du flexible d'alimentation.",
        "lignes": [{k: l[k] for k in ("libelle", "unite", "quantite", "prix")} for l in res["lignes"]],
        "tva": 10.0, "acompte": 0, "validite_jours": 30, "delai": "sous 48 h",
    }
    return render_template("hugo/demo_leo.html", d=d, t=totaux(d["lignes"], d["tva"]), euros=euros, ex=ex)


@bp.get("/demo-leo/devis.pdf")
def demo_pdf():
    ex = EXEMPLES[0]
    from .engine import _par_regles
    res = _par_regles(ex["demande"], grille_json(ex["metier"]))
    d = {"numero": "D-DEMO-0001", "created_at": datetime.now().isoformat(timespec="seconds"),
         "entreprise": {"nom": "Plomberie Martin (exemple)", "siret": "[SIRET de l'artisan]", "adresse": "Toulouse"},
         "client": {"nom": ex["client"], "adresse": ex["adresse"]},
         "description": "Recherche et réparation d'une fuite sous l'évier de la cuisine, remplacement du siphon et du flexible d'alimentation.",
         "lignes": [{k: l[k] for k in ("libelle", "unite", "quantite", "prix")} for l in res["lignes"]],
         "tva": 10.0, "acompte": 0, "validite_jours": 30, "delai": "sous 48 h"}
    return send_file(io.BytesIO(build(d)), mimetype="application/pdf", download_name="Devis_exemple_Leo.pdf")
