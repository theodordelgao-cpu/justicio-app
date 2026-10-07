"""Hugo : transforme une demande client en lignes de devis, à partir de la grille de l'artisan.

Deux moteurs :
- IA (OpenAI, via iris_doe.ai) : comprend la demande, choisit les lignes de la grille,
  estime les quantités et liste les questions à poser au client ;
- règles : mots-clés de la grille + quantités lues dans le texte (« 3 prises », « 95 m2 »).
  Utilisé sans clé ou si l'IA ne répond pas : la démo marche toujours.
Une prestation hors grille n'est jamais chiffrée au hasard : elle sort avec un prix vide.
"""

import json
import re
import unicodedata

from iris_doe import ai

NOMBRES = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return s.replace("œ", "oe").replace("’", "'").replace("m²", "m2")


def _num(tok: str):
    tok = tok.replace(",", ".")
    try:
        return float(tok)
    except ValueError:
        return NOMBRES.get(tok)


def _quantite(text_n: str, mots, unite: str):
    """Cherche une quantité liée au mot-clé : « 3 prises », « murs environ 95 m2 »."""
    for mot in mots:
        m = norm(mot)
        if unite.startswith("m"):  # m², ml
            hit = re.search(re.escape(m) + r"[^.\d]{0,30}?(\d+(?:[.,]\d+)?)\s*m2?", text_n)
            if hit:
                return _num(hit.group(1))
        hit = re.search(r"(\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s+(?:\w+\s+)?" + re.escape(m), text_n)
        if hit:
            q = _num(hit.group(1))
            if q and q < 100:
                return q
    return None


def _par_regles(demande: str, grille: list[dict]) -> dict:
    text_n = norm(demande)
    lignes, questions = [], []
    for item in grille:
        mots = item.get("mots") or [item["libelle"].split()[0]]
        if not any(norm(m) in text_n for m in mots):
            continue
        q = _quantite(text_n, mots, item["unite"])
        lignes.append({"libelle": item["libelle"], "unite": item["unite"], "quantite": q, "prix": item["prix"], "origine": "grille"})

    # surfaces : une ligne sans surface reprend le total des surfaces citées (préparation, protection…)
    surface = sum(l["quantite"] for l in lignes if l["unite"].startswith("m") and l["quantite"])
    for l in lignes:
        if l["quantite"] is None:
            if l["unite"].startswith("m"):
                l["quantite"] = surface or 1
                if not surface:
                    questions.append(f"Surface à confirmer pour « {l['libelle']} ».")
            else:
                l["quantite"] = 1

    # le déplacement accompagne toute intervention, s'il est dans la grille
    depl = next((i for i in grille if "placement" in norm(i["libelle"])), None)
    if depl and lignes and not any(l["libelle"] == depl["libelle"] for l in lignes):
        lignes.insert(0, {"libelle": depl["libelle"], "unite": depl["unite"], "quantite": 1, "prix": depl["prix"], "origine": "grille"})
    if not lignes:
        questions.append("Aucune prestation de votre grille ne correspond : ajoutez les lignes à la main.")
    if re.search(r"\b\d+(e|eme|er)\s+etage\b", text_n) and "ascenseur" in text_n and "sans" in text_n:
        questions.append("Étage sans ascenseur : prévoir une majoration d'accès ?")
    questions.append("Photos de l'installation reçues ? Elles permettent de confirmer le devis sans déplacement.")
    return {"lignes": lignes, "description": demande.strip()[:600], "questions": questions[:4], "source": "regles"}


_PROMPT = """Tu es Hugo, l'assistant devis d'un artisan ({metier}) en France.
À partir de la demande du client, prépare les lignes d'un devis en utilisant EN PRIORITÉ la grille de prix de l'artisan.

Grille de l'artisan (libellé | unité | prix unitaire HT en €) :
{grille}

Demande du client :
\"\"\"{demande}\"\"\"

Règles :
- Reprends exactement le libellé et le prix de la grille quand une ligne correspond.
- Estime des quantités réalistes à partir de la demande (surfaces, nombre d'éléments, heures).
- Ajoute le déplacement s'il existe dans la grille et qu'une intervention sur place est nécessaire.
- Si une prestation nécessaire n'est pas dans la grille, ajoute-la avec "prix": null (l'artisan la chiffrera). N'invente jamais de prix.
- "description" : 1 à 2 phrases claires décrivant les travaux, pour le client.
- "questions" : 1 à 3 questions précises à poser au client pour fiabiliser le devis.

Réponds uniquement en JSON :
{{"description": "...", "lignes": [{{"libelle": "...", "unite": "...", "quantite": 1, "prix": 0.0, "origine": "grille|hors_grille"}}], "questions": ["..."]}}"""


def _par_ia(metier_label: str, demande: str, grille: list[dict]):
    if not ai.disponible():
        return None
    g = "\n".join(f"- {i['libelle']} | {i['unite']} | {i['prix']:.2f}" for i in grille)
    data = ai.chat_json(_PROMPT.format(metier=metier_label, grille=g, demande=demande[:2000]), max_tokens=900)
    if not data or not isinstance(data.get("lignes"), list):
        return None
    lignes = []
    for l in data["lignes"][:25]:
        try:
            q = float(l.get("quantite") or 1)
        except (TypeError, ValueError):
            q = 1
        prix = l.get("prix")
        try:
            prix = None if prix is None else round(float(prix), 2)
        except (TypeError, ValueError):
            prix = None
        lib = str(l.get("libelle") or "").strip()[:140]
        if lib:
            lignes.append({"libelle": lib, "unite": str(l.get("unite") or "unité")[:20], "quantite": q,
                           "prix": prix, "origine": "grille" if l.get("origine") == "grille" else "hors_grille"})
    if not lignes:
        return None
    qs = [str(q)[:200] for q in (data.get("questions") or [])][:3]
    return {"lignes": lignes, "description": str(data.get("description") or demande)[:600], "questions": qs, "source": "ia"}


def generer(metier_label: str, demande: str, grille: list[dict]) -> dict:
    return _par_ia(metier_label, demande, grille) or _par_regles(demande, grille)


def totaux(lignes: list[dict], tva: float) -> dict:
    ht = round(sum((l.get("quantite") or 0) * (l.get("prix") or 0) for l in lignes), 2)
    montant_tva = round(ht * tva / 100, 2)
    return {"ht": ht, "tva": montant_tva, "ttc": round(ht + montant_tva, 2)}


def euros(v: float) -> str:
    s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} €"


def parse_lignes(raw: str) -> list[dict]:
    """Lignes envoyées par l'éditeur (JSON), nettoyées."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for l in data[:60]:
        lib = str(l.get("libelle") or "").strip()[:140]
        if not lib:
            continue
        try:
            q = max(0.0, float(str(l.get("quantite") or 0).replace(",", ".")))
            p = max(0.0, float(str(l.get("prix") or 0).replace(",", ".")))
        except ValueError:
            continue
        out.append({"libelle": lib, "unite": str(l.get("unite") or "unité")[:20], "quantite": q, "prix": round(p, 2)})
    return out
