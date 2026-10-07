import io
import logging
import secrets

from flask import (abort, flash, redirect, render_template, request, send_file,
                   url_for)

from . import bp, storage
from .builder import build_doe
from .classifier import classify
from .extraction import ALLOWED_EXT, extract
from .referentiel import CATEGORIES, CATEGORY_LABELS, CATEGORY_ORDER, LOTS

log = logging.getLogger(__name__)
MAX_FILE_BYTES = 25 * 1024 * 1024


def _chantier_or_404(cid):
    try:
        return storage.load(cid)
    except FileNotFoundError:
        abort(404)


@bp.get("/")
def index():
    return render_template("iris/index.html", lots=LOTS)


@bp.post("/chantier")
def creer_chantier():
    nom = (request.form.get("nom") or "").strip()
    if not nom:
        flash("Indiquez au moins le nom du chantier.", "erreur")
        return redirect(url_for(".index"))
    meta = storage.create({
        "nom": nom[:200],
        "maitre_ouvrage": (request.form.get("maitre_ouvrage") or "").strip()[:200],
        "entreprise": (request.form.get("entreprise") or "").strip()[:200],
        "adresse": (request.form.get("adresse") or "").strip()[:300],
        "lots": request.form.getlist("lots"),
    })
    return redirect(url_for(".chantier", cid=meta["id"]))


@bp.get("/chantier/<cid>")
def chantier(cid):
    meta = _chantier_or_404(cid)
    groupes = []
    for key in CATEGORY_ORDER:
        docs = [d for d in meta["documents"] if d["categorie"] == key]
        if docs:
            groupes.append((key, CATEGORY_LABELS[key], docs))
    bilan = storage.bilan(meta)
    return render_template(
        "iris/chantier.html", meta=meta, groupes=groupes, bilan=bilan,
        manquantes=[b for b in bilan if not b["ok"]], categories=CATEGORIES,
        lots_labels=[LOTS[l]["label"] for l in meta["lots"]],
        accept=",".join(sorted(ALLOWED_EXT)),
    )


@bp.post("/chantier/<cid>/upload")
def upload(cid):
    meta = _chantier_or_404(cid)
    dest = storage.files_dir(cid)
    ajoutes, refuses = 0, []

    for f in request.files.getlist("fichiers"):
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
            "id": doc_id,
            "original_name": name,
            "stored_name": stored,
            "kind": info["kind"],
            "pages": info["pages"],
            "categorie": cls["categorie"],
            "titre": cls["titre"],
            "source": cls["source"],
            "text_excerpt": info["text"][:2000],
        })
        ajoutes += 1

    storage.save(meta)
    if ajoutes:
        flash(f"Iris a lu et classé {ajoutes} document{'s' if ajoutes > 1 else ''}.", "ok")
    for r in refuses:
        flash(f"Non ajouté : {r}", "erreur")
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
    nom = "".join(c if c.isalnum() else "_" for c in meta["nom"])[:60].strip("_") or "chantier"
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     download_name=f"DOE_{nom}.pdf", as_attachment=request.args.get("dl") == "1")
