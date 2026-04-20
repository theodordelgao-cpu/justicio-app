"""
Justicio Video Pipeline
Rotation automatique de 5 formats vidéo → 30 vidéos/mois
+ Format screen_anime : navigation démo enregistrée en vidéo TikTok
"""

import os
import json
import random
import asyncio
import subprocess
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
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
    "screen_anime": {
        "label": "Démo Animée (25s)",
        "description": "Navigation live sur les pages démo Justicio, enregistrée en vidéo TikTok",
        "hook": "Ton opérateur te doit de l'argent — voilà comment le récupérer en 60 secondes",
        "structure": [
            "Page d'accueil /demo (5s)",
            "Scan Gmail /demo/scan (10s)",
            "Litiges détectés /demo/litiges (8s)",
            "Analyse IA /demo/analyse (12s)",
            "Mise en demeure /demo/mise-en-demeure (8s)",
            "Tableau de suivi /demo/suivi (7s)",
        ],
        "per_month": 4,
        "video_mode": "playwright",
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

# ─── Screen Anime : pages de navigation ───────────────────────────────────────

DEMO_PAGES = [
    ("https://justicio.fr/demo",               5_000),   # 5s  → accueil
    ("https://justicio.fr/demo/scan",          10_000),  # 10s → scan Gmail
    ("https://justicio.fr/demo/litiges",        8_000),  # 8s  → litiges
    ("https://justicio.fr/demo/analyse",       12_000),  # 12s → analyse IA (typewriter)
    ("https://justicio.fr/demo/mise-en-demeure", 8_000), # 8s  → lettre
    ("https://justicio.fr/demo/suivi",          7_000),  # 7s  → dashboard
]
# Total navigation : 50s → accéléré ×2 = 25s

SCREEN_NARRATION = (
    "Ton opérateur, ta banque ou Amazon te doit peut-être de l'argent. "
    "Justicio scanne automatiquement ta boîte Gmail "
    "et détecte tes litiges en quelques secondes. "
    "Ici : trois dossiers détectés — SNCF, Amazon, Air France — "
    "quatre cent quatre-vingt-sept euros récupérables. "
    "L'intelligence artificielle analyse chaque dossier, "
    "rédige une mise en demeure juridiquement valide "
    "et l'envoie directement à l'entreprise. "
    "Résultat : remboursé en six jours, sans rien faire toi-même. "
    "Essaie gratuitement sur justicio point fr."
)

# ─── Playwright : enregistrement de la navigation ─────────────────────────────

async def _record_playwright(video_dir: str) -> str:
    """Navigue sur les pages démo et retourne le chemin du fichier vidéo brut."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("Playwright manquant. Lance : pip install playwright && playwright install chromium")

    print("  🌐 Lancement du navigateur Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 390, "height": 844},
            record_video_dir=video_dir,
            record_video_size={"width": 390, "height": 844},
        )
        page = await context.new_page()

        for url, wait_ms in DEMO_PAGES:
            print(f"    → {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            except Exception:
                await page.goto(url, timeout=15_000)

            # Scroll doux vers le bas puis remonte (effet cinématique)
            await page.wait_for_timeout(800)
            await page.evaluate("window.scrollTo({top: 500, behavior: 'smooth'})")
            await page.wait_for_timeout(wait_ms - 1_800)
            await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            await page.wait_for_timeout(1_000)

        video = page.video
        await context.close()
        await browser.close()

        video_path = await video.path()
        return str(video_path)


# ─── TTS OpenAI ───────────────────────────────────────────────────────────────

def generate_tts_audio(text: str, output_path: str, speed: float = 1.15) -> str:
    """Génère l'audio voix-off via OpenAI TTS-1 (voix nova, ~25s)."""
    print("  🎙️ Génération voix TTS (OpenAI nova)...")
    response = client.audio.speech.create(
        model="tts-1-hd",
        voice="nova",
        input=text,
        speed=speed,
    )
    response.stream_to_file(output_path)
    return output_path


def get_audio_duration(audio_path: str) -> float:
    """Retourne la durée audio en secondes via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


# ─── Sous-titres ASS style TikTok (mot par mot) ───────────────────────────────

def _secs_to_ass(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def generate_ass_subtitles(text: str, audio_duration: float, output_path: str) -> str:
    """
    Génère un fichier ASS avec sous-titres mot par mot style TikTok.
    Chaque groupe de 3 mots est affiché, le mot courant est surligné en jaune.
    """
    words = [w for w in text.replace("\n", " ").split() if w]
    n = len(words)
    secs_per_word = audio_duration / n

    header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 390
PlayResY: 844
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTok,Arial,46,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,3,2,2,20,20,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = []
    for i, word in enumerate(words):
        start = i * secs_per_word
        end = (i + 1) * secs_per_word

        # Fenêtre glissante : mot précédent + courant + suivant
        group = []
        for j in range(max(0, i - 1), min(n, i + 2)):
            if j == i:
                group.append(r"{\c&H00FFFF&\b1}" + words[j] + r"{\c&HFFFFFF&\b0}")
            else:
                group.append(words[j])

        dialogue = (
            f"Dialogue: 0,{_secs_to_ass(start)},{_secs_to_ass(end)},"
            f"TikTok,,0,0,0,,{' '.join(group)}"
        )
        lines.append(dialogue)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines))

    return output_path


# ─── Post-production ffmpeg ───────────────────────────────────────────────────

def _escape_ass_path(path: str) -> str:
    """Échappe le chemin pour le filtre subtitles= de ffmpeg (Windows-safe)."""
    return path.replace("\\", "/").replace(":", "\\:")


def process_screen_video(
    raw_video: str,
    audio_path: str,
    subs_path: str,
    output_path: str,
    max_duration: float = 25.0,
) -> str:
    """
    1. Accélère la vidéo brute ×2 (setpts=0.5*PTS)
    2. Ajoute le voiceover TTS
    3. Incruste les sous-titres ASS
    4. Tronque à max_duration secondes
    """
    tmp_sped = str(Path(output_path).parent / "_sped.mp4")
    tmp_with_subs = str(Path(output_path).parent / "_subs.mp4")

    print("  ⚡ Accélération ×2...")
    subprocess.run([
        "ffmpeg", "-y", "-i", raw_video,
        "-vf", "setpts=0.5*PTS",
        "-an",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        tmp_sped,
    ], check=True, capture_output=True)

    print("  📝 Incrustation sous-titres...")
    escaped = _escape_ass_path(subs_path)
    subprocess.run([
        "ffmpeg", "-y", "-i", tmp_sped,
        "-vf", f"ass={escaped}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        tmp_with_subs,
    ], check=True, capture_output=True)

    print("  🎵 Mixage audio + vidéo...")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", tmp_with_subs,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-t", str(max_duration),
        output_path,
    ], check=True, capture_output=True)

    # Nettoyage fichiers intermédiaires
    for f in (tmp_sped, tmp_with_subs):
        try:
            os.remove(f)
        except OSError:
            pass

    return output_path


# ─── Entrée principale : screen_anime ─────────────────────────────────────────

def run_screen_anime(output_path: str = None) -> str:
    """
    Pipeline complet screen_anime :
      1. Enregistre la navigation Playwright sur les pages /demo
      2. Génère le voiceover TTS
      3. Génère les sous-titres ASS mot par mot
      4. Assemble en vidéo finale 25s accélérée ×2
    """
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"justicio_screen_anime_{ts}.mp4"

    output_path = str(Path(output_path).resolve())
    work_dir = str(Path(output_path).parent / "_screen_anime_tmp")
    os.makedirs(work_dir, exist_ok=True)

    try:
        print("\n🎬 [1/4] Enregistrement navigation Playwright (~50s)...")
        raw_video = asyncio.run(_record_playwright(work_dir))
        print(f"  ✓ Vidéo brute : {raw_video}")

        print("\n🎙️ [2/4] Génération voiceover TTS...")
        audio_path = os.path.join(work_dir, "narration.mp3")
        generate_tts_audio(SCREEN_NARRATION, audio_path, speed=1.15)
        audio_dur = get_audio_duration(audio_path)
        print(f"  ✓ Audio : {audio_dur:.1f}s")

        print("\n📝 [3/4] Sous-titres TikTok mot par mot...")
        subs_path = os.path.join(work_dir, "subs.ass")
        generate_ass_subtitles(SCREEN_NARRATION, min(audio_dur, 25.0), subs_path)
        print(f"  ✓ Sous-titres générés ({len(SCREEN_NARRATION.split())} mots)")

        print("\n⚙️  [4/4] Post-production ffmpeg (×2, sous-titres, mix audio)...")
        process_screen_video(raw_video, audio_path, subs_path, output_path)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"\n✅ Vidéo finale : {output_path}  ({size_mb:.1f} MB)")
    return output_path


# ─── Génération de script GPT-4o ──────────────────────────────────────────────

def generate_script(format_key: str, topic: str, custom_context: str = "") -> dict:
    fmt = FORMATS[format_key]
    structure_str = " → ".join(fmt["structure"])

    system_prompt = """Tu es expert en contenu juridique vulgarisé pour TikTok/Reels/Shorts.
Tu rédiges des scripts courts, percutants, 100% actionnables pour Justicio (IA qui gère les litiges).
Ton style : direct, sans jargon, avec émotion et urgence. Jamais de conditionnel.
Tu tutoies l'audience."""

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
        if video["format"] == "screen_anime":
            video["status"] = "video_mode"
            continue
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
    if format_key not in FORMATS:
        raise ValueError(f"Format inconnu. Choisir parmi : {list(FORMATS.keys())}")
    if format_key == "screen_anime":
        print("Format screen_anime → lancement de l'enregistrement vidéo...")
        run_screen_anime()
        return {}
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

    elif args[0] == "screen_anime":
        output = args[1] if len(args) > 1 else None
        run_screen_anime(output)

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
        print("Commandes :")
        print("  calendar [year] [month] [--no-scripts]  Génère le calendrier mensuel")
        print("  screen_anime [output.mp4]               Enregistre la démo en vidéo TikTok")
        print("  single <format> <topic> [contexte]      Génère un script unique")
        print("  formats                                  Liste les formats disponibles")
