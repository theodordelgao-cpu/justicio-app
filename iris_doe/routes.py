import io
import logging
import secrets
from datetime import date, datetime
from urllib.parse import quote

from flask import (abort, flash, redirect, render_template, request, send_file,
                   url_for)

from . import ai, bp, mailer, storage
from .builder import build_doe
from .classifier import classify
from .extraction import ALLOWED_EXT, extract
from .referentiel import CATEGORIES, CATEGORY_LABELS, CATEGORY_ORDER, LOTS

log = logging.getLogger(__name__)
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_ENVOIS = 20  # emails par chantier, contre les abus


def _chantier_or_404(cid):
    try:
        return storage.load(cid)
    except FileNotFoundError:
        abort(404)


def _abs(endpoint, **kw):
    """URL absolue, en https sur le vrai domaine (Render est derrière un proxy)."""
    path = url_for(endpoint, **kw)
    host = request.host
    scheme = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"
    return f"{scheme}://{host}{path}"


def _pdf_name(meta):
    nom = "".join(c if c.isalnum() else "_" for c in meta["nom"])[:60].strip("_") or "chantier"
    return f"DOE_{nom}.pdf"


def _ajouter_fichiers(meta, fichiers):
    dest = storage.files_dir(meta["id"])
    ajoutes, refuses = 0, []
    for f in fichiers:
        if not f or not f.filename:
            continue
        name = f.filename.replace("\\", "/").rsplit("/", 1)[-1]
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext not in ALLOWED_EXT:
            refuses.append(f"{name} (format non pris en charge)")
            continue
        doc_id = secrets.token_hex(6)
        stored = f"{doc_id}{ext}"
        path = dest / stored
        f.save(path)
        if path.stat().st_size > MAX_FILE_BYTES:
            path.unlink(missing_ok=True)
            refuses.append(f"{name} (plus de 25 Mo)")
            continue
        try:
            info = extract(path)
        except Exception as exc:
            log.warning("Lecture impossible de %s : %s", name, exc)
            path.unlink(missing_ok=True)
            refuses.append(f"{name} (fichier illisible)")
            continue
        cls = classify(name, info["text"], info["kind"])
        meta["documents"].append({
            "id": doc_id, "original_name": name, "stored_name": stored,
            "kind": info["kind"], "pages": info["pages"], "lecture": info.get("lecture", "texte"),
            "categorie": cls["categorie"], "titre": cls["titre"], "source": cls["source"],
            "text_excerpt": info["text"][:2000],
        })
        ajoutes += 1
    storage.save(meta)
    if ajoutes:
        flash(f"Iris a lu et classé {ajoutes} document{'s' if ajoutes > 1 else ''}.", "ok")
    for r in refuses:
        flash(f"Non ajouté : {r}", "erreur")


# ---------------------------------------------------------------- accueil

@bp.get("/")
def index():
    return render_template("iris/index.html", lots=LOTS, accept=",".join(sorted(ALLOWED_EXT)),
                           ia=ai.disponible())


@bp.post("/chantier")
def creer_chantier():
    nom = (request.form.get("nom") or "").strip() or f"Chantier du {date.today():%d/%m/%Y}"
    meta = storage.create({
        "nom": nom[:200],
        "maitre_ouvrage": (request.form.get("maitre_ouvrage") or "").strip()[:200],
        "entreprise": (request.form.get("entreprise") or "").strip()[:200],
        "adresse": (request.form.get("adresse") or "").strip()[:300],
        "lots": request.form.getlist("lots"),
    })
    fichiers = request.files.getlist("fichiers")
    if any(f and f.filename for f in fichiers):
        _ajouter_fichiers(meta, fichiers)
    return redirect(url_for(".chantier", cid=meta["id"]))


# ---------------------------------------------------------------- chantier

@bp.get("/chantier/<cid>")
def chantier(cid):
    meta = _chantier_or_404(cid)
    groupes = []
    for key in CATEGORY_ORDER:
        docs = [d for d in meta["documents"] if d["categorie"] == key]
        if docs:
            groupes.append((key, CATEGORY_LABELS[key], docs))
    bilan = storage.bilan(meta)
    ok = sum(1 for b in bilan if b["ok"])
    lien_partage = _abs(".partage", token=meta["share_token"])
    message_wa = (f"Bonjour, voici le DOE du chantier « {meta['nom']} »"
                  f"{' (' + meta['entreprise'] + ')' if meta.get('entreprise') else ''} : {lien_partage}")
    return render_template(
        "iris/chantier.html", meta=meta, groupes=groupes, bilan=bilan, ok=ok,
        progress=(ok / len(bilan)) if bilan else 0,
        manquantes=[b for b in bilan if not b["ok"]], categories=CATEGORIES, lots=LOTS,
        lots_labels=[LOTS[l]["label"] for l in meta["lots"]],
        accept=",".join(sorted(ALLOWED_EXT)), lien_partage=lien_partage,
        whatsapp_url="https://wa.me/?text=" + quote(message_wa),
        mail_ok=mailer.configure(), ia=ai.disponible(),
        total_pages=sum(d.get("pages", 1) for d in meta["documents"]),
    )


@bp.post("/chantier/<cid>/upload")
def upload(cid):
    meta = _chantier_or_404(cid)
    _ajouter_fichiers(meta, request.files.getlist("fichiers"))
    return redirect(url_for(".chantier", cid=cid))


@bp.post("/chantier/<cid>/infos")
def modifier_infos(cid):
    meta = _chantier_or_404(cid)
    storage.update_infos(meta, {
        "nom": request.form.get("nom"), "maitre_ouvrage": request.form.get("maitre_ouvrage"),
        "entreprise": request.form.get("entreprise"), "adresse": request.form.get("adresse"),
        "lots": request.form.getlist("lots"),
    })
    storage.save(meta)
    flash("Infos du chantier mises à jour.", "ok")
    return redirect(url_for(".chantier", cid=cid))


@bp.post("/chantier/<cid>/document/<doc_id>")
def modifier_document(cid, doc_id):
    meta = _chantier_or_404(cid)
    for doc in meta["documents"]:
        if doc["id"] == doc_id:
            cat = request.form.get("categorie")
            if cat in CATEGORY_LABELS:
                if cat != doc["categorie"]:
                    doc["source"] = "manuel"
                doc["categorie"] = cat
            titre = (request.form.get("titre") or "").strip()
            if titre:
                doc["titre"] = titre[:120]
            storage.save(meta)
            break
    return redirect(url_for(".chantier", cid=cid) + f"#doc-{doc_id}")


@bp.post("/chantier/<cid>/document/<doc_id>/supprimer")
def supprimer_document(cid, doc_id):
    meta = _chantier_or_404(cid)
    storage.delete_document(meta, doc_id)
    storage.save(meta)
    return redirect(url_for(".chantier", cid=cid))


@bp.get("/chantier/<cid>/document/<doc_id>")
def voir_document(cid, doc_id):
    meta = _chantier_or_404(cid)
    doc = next((d for d in meta["documents"] if d["id"] == doc_id), None)
    if not doc:
        abort(404)
    return send_file(storage.files_dir(cid) / doc["stored_name"], download_name=doc["original_name"])


@bp.get("/chantier/<cid>/doe.pdf")
def telecharger_doe(cid):
    meta = _chantier_or_404(cid)
    if not meta["documents"]:
        flash("Ajoutez des documents avant de générer le DOE.", "erreur")
        return redirect(url_for(".chantier", cid=cid))
    pdf = build_doe(meta, storage.files_dir(cid))
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     download_name=_pdf_name(meta), as_attachment=request.args.get("dl") == "1")


@bp.post("/chantier/<cid>/envoyer")
def envoyer(cid):
    meta = _chantier_or_404(cid)
    if not meta["documents"]:
        flash("Ajoutez des documents avant d'envoyer le DOE.", "erreur")
        return redirect(url_for(".chantier", cid=cid) + "#partager")
    if len(meta["envois"]) >= MAX_ENVOIS:
        flash("Limite d'envois atteinte pour ce chantier.", "erreur")
        return redirect(url_for(".chantier", cid=cid) + "#partager")
    dest = (request.form.get("email") or "").strip()
    message = (request.form.get("message") or "").strip()[:1000]
    pdf = build_doe(meta, storage.files_dir(cid))
    ok, info = mailer.envoyer_doe(dest, meta, _abs(".partage", token=meta["share_token"]), pdf, _pdf_name(meta), message)
    if ok:
        meta["envois"].append({"to": dest, "at": datetime.now().isoformat(timespec="seconds")})
        storage.save(meta)
    flash(info, "ok" if ok else "erreur")
    return redirect(url_for(".chantier", cid=cid) + "#partager")


# ---------------------------------------------------------------- partage (lecture seule)

def _partage_or_404(token):
    try:
        return storage.find_by_share_token(token)
    except FileNotFoundError:
        abort(404)


@bp.get("/partage/<token>")
def partage(token):
    meta = _partage_or_404(token)
    sections = []
    for key in CATEGORY_ORDER:
        n = sum(1 for d in meta["documents"] if d["categorie"] == key)
        if n:
            sections.append((CATEGORY_LABELS[key], n))
    return render_template("iris/partage.html", meta=meta, sections=sections,
                           total_pages=sum(d.get("pages", 1) for d in meta["documents"]),
                           lots_labels=[LOTS[l]["label"] for l in meta["lots"] if l in LOTS])


@bp.get("/partage/<token>/doe.pdf")
def partage_pdf(token):
    meta = _partage_or_404(token)
    if not meta["documents"]:
        abort(404)
    pdf = build_doe(meta, storage.files_dir(meta["id"]))
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", download_name=_pdf_name(meta),
                     as_attachment=request.args.get("dl") == "1")
