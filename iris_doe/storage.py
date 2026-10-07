"""Stockage des chantiers sur disque : un dossier par chantier avec meta.json + fichiers.

Volontairement simple pour le MVP (pas de migration de base). À passer sur
PostgreSQL quand Iris aura ses premiers clients.
"""

import json
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from flask import current_app

from .classifier import contains, normalize
from .referentiel import LOTS, PIECES_COMMUNES


def root() -> Path:
    base = Path(current_app.config.get(
        "IRIS_STORAGE", Path(current_app.instance_path) / "iris_chantiers"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _dir(chantier_id: str) -> Path:
    # les identifiants sont générés par nous : on refuse tout autre format
    if not chantier_id.replace("-", "").replace("_", "").isalnum():
        raise FileNotFoundError(chantier_id)
    return root() / chantier_id


def files_dir(chantier_id: str) -> Path:
    d = _dir(chantier_id) / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(data: dict) -> dict:
    cid = secrets.token_urlsafe(9)
    meta = {
        "id": cid,
        "nom": data["nom"],
        "maitre_ouvrage": data.get("maitre_ouvrage", ""),
        "entreprise": data.get("entreprise", ""),
        "adresse": data.get("adresse", ""),
        "lots": [l for l in data.get("lots", []) if l in LOTS],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "documents": [],
    }
    files_dir(cid)
    save(meta)
    return meta


def load(chantier_id: str) -> dict:
    path = _dir(chantier_id) / "meta.json"
    if not path.exists():
        raise FileNotFoundError(chantier_id)
    return json.loads(path.read_text(encoding="utf-8"))


def save(meta: dict) -> None:
    path = _dir(meta["id"]) / "meta.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def delete_document(meta: dict, doc_id: str) -> None:
    for doc in list(meta["documents"]):
        if doc["id"] == doc_id:
            (files_dir(meta["id"]) / doc["stored_name"]).unlink(missing_ok=True)
            meta["documents"].remove(doc)


def delete_chantier(chantier_id: str) -> None:
    shutil.rmtree(_dir(chantier_id), ignore_errors=True)


def pieces_attendues(meta: dict) -> list[dict]:
    pieces = list(PIECES_COMMUNES)
    for lot in meta.get("lots", []):
        for p in LOTS[lot]["pieces"]:
            pieces.append({**p, "lot": LOTS[lot]["label"]})
    return pieces


def bilan(meta: dict) -> list[dict]:
    """Pour chaque pièce attendue : trouvée ou manquante, et dans quel document."""
    resultats = []
    for piece in pieces_attendues(meta):
        trouve = None
        for doc in meta["documents"]:
            if doc["categorie"] != piece["categorie"]:
                continue
            if not piece["keywords"]:
                trouve = doc
                break
            haystack = normalize(doc["original_name"] + " " + doc["titre"] + " " + doc.get("text_excerpt", ""))
            if any(contains(haystack, kw) for kw in piece["keywords"]):
                trouve = doc
                break
        resultats.append({**piece, "ok": trouve is not None, "doc_titre": trouve["titre"] if trouve else None})
    return resultats
