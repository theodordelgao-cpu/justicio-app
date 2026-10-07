"""Appels à OpenAI pour Iris, en HTTP direct (pas de dépendance à la version du SDK).

- chat_json : classement d'un document (réponse JSON)
- lire_images : lecture d'un scan ou d'une photo de document (vision)

Tout est optionnel : sans OPENAI_API_KEY, ou en cas d'erreur, les fonctions
renvoient None et Iris se rabat sur ses règles.
"""

import base64
import io
import json
import logging
import os

import requests
from PIL import Image

log = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 60


def disponible() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _model() -> str:
    return os.environ.get("IRIS_LLM_MODEL", "gpt-4o-mini")


def _post(messages, json_mode=False, max_tokens=1200):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    body = {"model": _model(), "temperature": 0, "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(API_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("OpenAI %s : %s", r.status_code, r.text[:300])
            return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("OpenAI injoignable : %s", exc)
        return None


def diagnostic() -> dict:
    """Petit appel de test, sans exposer la clé : statut HTTP et type d'erreur éventuel."""
    import re as _re
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return {"cle": False}
    try:
        r = requests.post(API_URL, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT,
                          json={"model": _model(), "messages": [{"role": "user", "content": "Réponds OK"}], "max_tokens": 5})
        out = {"cle": True, "modele": _model(), "http": r.status_code}
        if r.status_code != 200:
            err = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            out["erreur"] = {k: _re.sub(r"sk-[A-Za-z0-9_*-]+", "sk-***", str(err.get(k, ""))) for k in ("type", "code", "message")}
        return out
    except Exception as exc:
        return {"cle": True, "exception": type(exc).__name__, "detail": str(exc)[:200]}


def chat_json(prompt: str, max_tokens: int = 200):
    content = _post([{"role": "user", "content": prompt}], json_mode=True, max_tokens=max_tokens)
    if not content:
        return None
    try:
        return json.loads(content)
    except ValueError:
        return None


def _data_uri(img: Image.Image) -> str:
    img = img.convert("RGB")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


_VISION_PROMPT = (
    "Ces images sont les pages d'un document de chantier (BTP) : fiche technique, PV, attestation, "
    "notice, plan, facture, photo… Recopie fidèlement tout le texte lisible, en français, sans commentaire. "
    "Si c'est une photo de chantier sans texte, réponds exactement : PHOTO DE CHANTIER, puis une phrase "
    "qui décrit ce qu'on voit."
)


def lire_images(images) -> str | None:
    """Transcrit le texte d'une ou plusieurs images (pages d'un scan, photo)."""
    if not images:
        return None
    parts = [{"type": "text", "text": _VISION_PROMPT}]
    for img in images[:3]:
        parts.append({"type": "image_url", "image_url": {"url": _data_uri(img), "detail": "high"}})
    return _post([{"role": "user", "content": parts}], max_tokens=1500)
