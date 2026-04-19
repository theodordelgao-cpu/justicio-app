"""
Justicio Video Pipeline
Rotation automatique de 5 formats vidéo → 30 vidéos/mois
Intégration GPT-4o pour la génération de scripts
"""

import os
import json
import random
from datetime import datetime, timedelta
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ─── Formats vidéo ────────────────────────────────────────────────────────────

FORMATS = {
    "conseil_express": {
        "label": "Conseil Express (60s)",
        "description": "Un conseil juridique actionnable en 60 secondes",
        "hook": "Vous ne saviez probablement pas que...",
        "structure": ["Hook (5s)", "Problème (10s)", "Conseil juridique (35s)", "CTA Justicio (10s)"],
        "per_month": 10,
    },
    "cas_pratique": {
        "label": "Cas Pratique (90s)",
        "description": "Un litige réel résolu étape par étape",
        "hook": "Ce client a récupéré {montant}€ grâce à cette technique...",
        "structure": ["Situation initiale (15s)", "Démarche Justicio (50s)", "Résultat (15s)", "CTA (10s)"],
        "per_month": 8,
    },
    "droit_en_clair": {
        "label": "Droit en Clair (45s)",
        "description": "Un article de loi expliqué simplement",
        "hook": "L'article {numero} du Code de la consommation dit exactement ça :",
        "structure": ["Loi brute (10s)", "Traduction simple (25s)", "Comment l'utiliser (10s)"],
        "per_month": 6,
    },
    "faq_litige": {
        "label": "FAQ Litige (30s)",
        "description": "Réponse directe à une question fréquente",
        "hook": "Question : {question}",
        "structure": ["Question (5s)", "Réponse directe (20s)", "CTA (5s)"],
        "per_month": 4,
    },
    "comparatif": {
        "label": "Comparatif Démarches (120s)",
        "description": "Justicio vs démarche classique (avocat, seul, etc.)",
        "hook": "Voici pourquoi 80% des gens perdent leur litige seuls...",
        "structure": ["Problème commun (20s)", "Démarche classique (30s)", "Avec Justicio (50s)", "CTA (20s)"],
        "per_month": 2,
    },
}

TOPICS = {
    "transport": [
        "vol annulé sans remboursement", "retard train > 2h", "bagage perdu SNCF",
        "surréservation avion", "retard vol low-cost", "annulation TGV dernière minute",
    ],
    "ecommerce": [
        "colis perdu Colissimo", "produit non conforme Amazon", "remboursement refusé Cdiscount",
        "vendeur marketplace introuvable", "délai de livraison non respecté", "vice caché e-commerce",
    ],
    "logement": [
        "dépôt de garantie non rendu", "travaux non réalisés", "charges abusives",
        "préavis contesté", "état des lieux litigieux", "loyer impayé côté bailleur",
    ],
    "services": [
        "abonnement impossible à résilier", "facturation erronée opérateur",
        "prestation non réalisée", "devis non respecté", "garantie constructeur refusée",
    ],
}

# ─── Génération de script GPT-4o ──────────────────────────────────────────────

def generate_script(format_key: str, topic: str, custom_context: str = "") -> dict:
    fmt = FORMATS[format_key]
    structure_str = " → ".join(fmt["structure"])

    system_prompt = """Tu es expert en contenu juridique vulgarisé pour TikTok/Reels/Shorts.
Tu rédiges des scripts courts, percutants, 100% actionnables pour Justicio (IA qui gère les litiges).
Ton style : direct, sans jargon, avec émotion et urgence. Jamais de conditionnel.
Tu tutoyais l'audience."""

    user_prompt = f"""Crée un script vidéo format "{fmt['label']}" sur ce sujet : {topic}

Structure imposée : {structure_str}
Hook suggéré : {fmt['hook']}
{f"Contexte supplémentaire : {custom_context}" if custom_context else ""}

Réponds en JSON avec ces clés :
- title: titre accrocheur (max 60 chars)
- hook: première phrase (max 15 mots, doit accrocher en 2 secondes)
- script: texte complet découpé par segment selon la structure
- hashtags: liste de 8 hashtags pertinents
- cta: call-to-action final
- estimated_duration: durée estimée en secondes"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.8,
    )

    result = json.loads(response.choices[0].message.content)
    result["format"] = format_key
    result["topic"] = topic
    result["generated_at"] = datetime.now().isoformat()
    return result


# ─── Calendrier mensuel ───────────────────────────────────────────────────────

def build_monthly_calendar(year: int, month: int) -> list[dict]:
    """Génère le calendrier de 30 vidéos pour le mois donné."""
    from calendar import monthrange
    _, days_in_month = monthrange(year, month)
    publish_days = sorted(random.sample(range(1, days_in_month + 1), 30))

    slots = []
    for fmt_key, fmt in FORMATS.items():
        slots.extend([fmt_key] * fmt["per_month"])
    random.shuffle(slots)

    all_topics = [t for topics in TOPICS.values() for t in topics]

    calendar = []
    for i, day in enumerate(publish_days):
        fmt_key = slots[i]
        topic = random.choice(all_topics)
        calendar.append({
            "id": i + 1,
            "publish_date": f"{year:04d}-{month:02d}-{day:02d}",
            "format": fmt_key,
            "format_label": FORMATS[fmt_key]["label"],
            "topic": topic,
            "status": "pending",
            "script": None,
        })

    return calendar


def generate_calendar_scripts(calendar: list[dict]) -> list[dict]:
    """Génère les scripts GPT-4o pour toutes les vidéos du calendrier."""
    for i, video in enumerate(calendar):
        print(f"[{i+1:02d}/30] Génération : {video['format_label']} — {video['topic']}")
        try:
            video["script"] = generate_script(video["format"], video["topic"])
            video["status"] = "script_ready"
        except Exception as e:
            video["status"] = "error"
            video["error"] = str(e)
            print(f"  ⚠ Erreur : {e}")
    return calendar


# ─── Sauvegarde ───────────────────────────────────────────────────────────────

def save_calendar(calendar: list[dict], path: str = None) -> str:
    if path is None:
        month_str = calendar[0]["publish_date"][:7]
        path = f"video_calendar_{month_str}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False, indent=2)
    print(f"\nCalendrier sauvegardé : {path}")
    return path


def print_summary(calendar: list[dict]):
    print("\n" + "═" * 60)
    print("  JUSTICIO VIDEO PIPELINE — RÉSUMÉ DU MOIS")
    print("═" * 60)
    fmt_counts = {}
    for v in calendar:
        fmt_counts[v["format_label"]] = fmt_counts.get(v["format_label"], 0) + 1
    for label, count in fmt_counts.items():
        print(f"  {label:<35} {count:>2} vidéos")
    print("─" * 60)
    print(f"  TOTAL : {len(calendar)} vidéos")
    ready = sum(1 for v in calendar if v["status"] == "script_ready")
    print(f"  Scripts prêts : {ready}/{len(calendar)}")
    print("═" * 60 + "\n")


# ─── Génération unitaire ──────────────────────────────────────────────────────

def generate_single(format_key: str, topic: str, custom_context: str = "") -> dict:
    """Génère et affiche un script unique."""
    if format_key not in FORMATS:
        raise ValueError(f"Format inconnu. Choisir parmi : {list(FORMATS.keys())}")
    print(f"Génération : {FORMATS[format_key]['label']} — {topic}\n")
    script = generate_script(format_key, topic, custom_context)
    print(json.dumps(script, ensure_ascii=False, indent=2))
    return script


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]

    if not args or args[0] == "calendar":
        now = datetime.now()
        year = int(args[1]) if len(args) > 1 else now.year
        month = int(args[2]) if len(args) > 2 else now.month
        generate_scripts = "--no-scripts" not in args

        print(f"\nCréation du calendrier {year}-{month:02d} ({30} vidéos)...")
        cal = build_monthly_calendar(year, month)

        if generate_scripts:
            cal = generate_calendar_scripts(cal)

        print_summary(cal)
        save_calendar(cal)

    elif args[0] == "single":
        if len(args) < 3:
            print("Usage : python justicio_video_pipeline.py single <format> <topic> [contexte]")
            print(f"Formats disponibles : {list(FORMATS.keys())}")
            sys.exit(1)
        fmt = args[1]
        topic = args[2]
        ctx = args[3] if len(args) > 3 else ""
        generate_single(fmt, topic, ctx)

    elif args[0] == "formats":
        print("\nFormats disponibles :")
        for key, fmt in FORMATS.items():
            print(f"  {key:<20} {fmt['label']} — {fmt['per_month']} vidéos/mois")

    else:
        print("Commandes : calendar [year] [month] [--no-scripts] | single <format> <topic> | formats")
