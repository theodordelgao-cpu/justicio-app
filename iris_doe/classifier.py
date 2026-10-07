"""Classement d'un document dans une catégorie du DOE.

Deux moteurs :
- LLM (OpenAI) si OPENAI_API_KEY est défini : plus fin, donne aussi un titre propre ;
- mots-clés (référentiel) sinon, ou si l'appel LLM échoue. La démo marche donc sans clé.
"""

import logging
import re
import unicodedata

from .referentiel import CATEGORIES, CATEGORY_LABELS

log = logging.getLogger(__name__)


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("’", "'")
    return re.sub(r"\s+", " ", s)


def contains(text_norm: str, keyword: str) -> bool:
    """Mot-clé présent en tant que mot (pluriel en -s/-x toléré)."""
    kw = re.escape(normalize(keyword))
    return re.search(rf"(?<![a-z0-9]){kw}[sx]?(?![a-z0-9])", text_norm) is not None


def titre_depuis_fichier(filename: str) -> str:
    base = filename.rsplit(".", 1)[0]
    base = re.sub(r"[_\-]+", " ", base).strip()
    return base[:1].upper() + base[1:] if base else filename


_GENERIC_NAME = re.compile(r"^(doc|document|scan|numerisation|img|image|dsc|pxl|photo|fichier|sans titre|whatsapp)(?![a-z])[\s_\-]*[\d\s_\-]*", re.I)


def _titre(filename: str, text: str) -> str:
    """Nom de fichier parlant → on le garde ; nom générique (scan0042, doc1) → 1re ligne lisible du contenu."""
    base = filename.rsplit(".", 1)[0]
    if _GENERIC_NAME.match(normalize(base)):
        for line in (text or "").splitlines():
            line = re.sub(r"\s+", " ", line).strip(" -:_.")
            if len(line) >= 8 and sum(ch.isalpha() for ch in line) >= 6:
                return line[:1].upper() + line[1:].lower()[:79]
    return titre_depuis_fichier(filename)


def _par_mots_cles(filename: str, text: str, kind: str) -> dict:
    name_n = normalize(filename)
    text_n = normalize(text)

    # Photo avec très peu de texte lisible (ou reconnue comme telle par l'IA) : photo de chantier
    if kind == "image" and (len(text_n.strip()) < 40 or text_n.strip().startswith("photo de chantier")):
        return {"categorie": "photos", "titre": titre_depuis_fichier(filename), "source": "regles"}

    scores = {}
    for cat in CATEGORIES:
        score = 0
        for kw in cat["keywords"]:
            if contains(name_n, kw):
                score += 3          # le nom de fichier est un signal fort
            if contains(text_n, kw):
                score += 1
        if score:
            scores[cat["key"]] = score

    categorie = max(scores, key=scores.get) if scores else ("photos" if kind == "image" else "autres")
    return {"categorie": categorie, "titre": _titre(filename, text), "source": "regles"}


_PROMPT = """Tu aides à monter un DOE (Dossier des Ouvrages Exécutés) pour une entreprise du bâtiment.
Classe le document ci-dessous dans UNE catégorie parmi :
{cats}

Réponds uniquement en JSON : {{"categorie": "<clé>", "titre": "<titre court et clair du document, 8 mots max>"}}

Nom du fichier : {filename}
Type : {kind}
Début du contenu :
\"\"\"{text}\"\"\""""


def _par_llm(filename: str, text: str, kind: str) -> dict | None:
    from . import ai
    if not ai.disponible():
        return None
    cats = "\n".join(f"- {c['key']} : {c['label']}" for c in CATEGORIES)
    data = ai.chat_json(_PROMPT.format(cats=cats, filename=filename, kind=kind, text=(text or "")[:3000]))
    if not data or data.get("categorie") not in CATEGORY_LABELS:
        return None
    titre = (data.get("titre") or "").strip() or titre_depuis_fichier(filename)
    return {"categorie": data["categorie"], "titre": titre[:120], "source": "ia"}


def classify(filename: str, text: str, kind: str) -> dict:
    return _par_llm(filename, text, kind) or _par_mots_cles(filename, text, kind)
