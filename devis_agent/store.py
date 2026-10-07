"""Stockage des devis : un fichier JSON par devis (MVP, comme Iris)."""

import json
import secrets
from datetime import datetime
from pathlib import Path

from flask import current_app


def root() -> Path:
    base = Path(current_app.config.get("HUGO_STORAGE", Path(current_app.instance_path) / "hugo_devis"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _ok(i: str) -> bool:
    return bool(i) and i.replace("-", "").replace("_", "").isalnum() and len(i) < 40


def create(data: dict) -> dict:
    now = datetime.now()
    d = {
        "id": secrets.token_urlsafe(9),
        "token": secrets.token_urlsafe(12),
        "numero": f"D-{now:%Y%m%d}-{secrets.randbelow(9000) + 1000}",
        "created_at": now.isoformat(timespec="seconds"),
        "statut": "brouillon",
        "acceptation": None,
        "envois": [],
        **data,
    }
    save(d)
    return d


def save(d: dict) -> None:
    path = root() / f"{d['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load(devis_id: str) -> dict:
    if not _ok(devis_id):
        raise FileNotFoundError(devis_id)
    path = root() / f"{devis_id}.json"
    if not path.exists():
        raise FileNotFoundError(devis_id)
    return json.loads(path.read_text(encoding="utf-8"))


def by_token(token: str) -> dict:
    if not _ok(token):
        raise FileNotFoundError(token)
    for p in root().glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if secrets.compare_digest(d.get("token", ""), token):
            return d
    raise FileNotFoundError(token)
