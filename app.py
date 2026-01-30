import os
import base64
import requests
import stripe
import json
import re
import traceback
from urllib.parse import urljoin, urlparse
from flask import Flask, session, redirect, request, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.exc import IntegrityError
from bs4 import BeautifulSoup

# ════════════════════════════════════════════════════════════════════════════════
# 🛡️ HELPER SÉCURISÉ - Parsing JSON infaillible (V2 - BRACE BALANCED)
# ════════════════════════════════════════════════════════════════════════════════

def _extract_first_json_object(text: str) -> str | None:
    """
    Extrait le 1er objet JSON {...} en respectant les accolades équilibrées.
    Supporte aussi les réponses dans des fences ```json ... ```
    """
    if not text:
        return None

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        inner = re.search(r"\{[\s\S]*\}", candidate)
        text = inner.group(0) if inner else candidate

    start = text.find("{")
    if start < 0:
        return None

    in_string = False
    escape = False
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i+1]

    return None

def _repair_common_json_issues(json_str: str) -> str:
    """
    Réparations "low risk" : virgules traînantes, quotes simples.
    """
    if not json_str:
        return json_str

    s = json_str.strip()
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)

    if "'" in s and '"' not in s:
        s = s.replace("'", '"')

    return s

def secure_json_parse(response_text, default_value=None):
    """
    🛡️ Parse une réponse IA en JSON de manière très robuste (ne crash jamais).
    Version V2 avec extraction brace-balanced.
    """
    if default_value is None:
        default_value = {"is_valid": False, "litige": False, "reason": "Parsing failed"}

    if not response_text:
        try:
            DEBUG_LOGS.append("🛡️ secure_json_parse: Réponse vide")
        except:
            pass
        return default_value

    try:
        json_str = _extract_first_json_object(response_text)
        if not json_str:
            try:
                DEBUG_LOGS.append(f"🛡️ secure_json_parse: Aucun objet JSON détecté: {response_text[:120]}...")
            except:
                pass
            return default_value

        try:
            obj = json.loads(json_str)
            return obj if isinstance(obj, dict) else default_value
        except json.JSONDecodeError:
            pass

        cleaned = _repair_common_json_issues(json_str)
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else default_value

    except Exception as e:
        try:
            DEBUG_LOGS.append(f"🛡️ secure_json_parse: Exception - {type(e).__name__}: {str(e)[:80]}")
        except:
            pass
        return default_value
# ========================================
# CONFIGURATION & INITIALISATION
# ========================================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "justicio_secret_key_secure")

# Variables d'environnement
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WHATSAPP_NUMBER = "33750384314"

if STRIPE_SK:
    stripe.api_key = STRIPE_SK

# ========================================
# SCOPES GMAIL API (LECTURE + ENVOI)
# ========================================
# IMPORTANT: Ces scopes doivent être autorisés dans Google Cloud Console
# Si vous passez de readonly à send, les utilisateurs devront se reconnecter
GMAIL_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',  # Lecture des emails
    'https://www.googleapis.com/auth/gmail.send',      # Envoi d'emails
    'https://www.googleapis.com/auth/gmail.modify',    # Modification (labels)
]

# Email support Justicio
SUPPORT_EMAIL = "support@justicio.fr"

# ════════════════════════════════════════════════════════════════════════════════
# 🤖 AGENT AVOCAT VIRTUEL - Génération de Mises en Demeure via GPT-4
# ════════════════════════════════════════════════════════════════════════════════

def generate_legal_letter_gpt(company, amount, motif, law, client_name, client_email, order_ref=None):
    """
    ⚖️ AGENT AVOCAT VIRTUEL - Génère une mise en demeure personnalisée via GPT-4
    
    Args:
        company: Nom de l'entreprise visée
        amount: Montant réclamé (ex: "42.99€")
        motif: Nature du litige (ex: "Colis non reçu depuis 3 semaines")
        law: Article de loi applicable (ex: "Règlement UE 261/2004")
        client_name: Nom du client
        client_email: Email du client
        order_ref: Numéro de commande (optionnel)
    
    Returns:
        dict: {"success": bool, "html_body": str, "text_body": str, "subject": str, "error": str}
    """
    
    if not OPENAI_API_KEY:
        DEBUG_LOGS.append("⚖️ Agent Avocat: ❌ Pas de clé API OpenAI")
        return {
            "success": False,
            "error": "API OpenAI non configurée",
            "html_body": None,
            "text_body": None,
            "subject": None
        }
    
    from datetime import timedelta
    today = datetime.now()
    today_str = today.strftime("%d/%m/%Y")
    deadline = (today + timedelta(days=8)).strftime("%d/%m/%Y")
    
    # Nettoyer le montant
    amount_clean = str(amount).replace('€', '').replace('EUR', '').strip()
    try:
        amount_num = float(amount_clean.replace(',', '.'))
        amount_formatted = f"{amount_num:.2f}"
    except:
        amount_formatted = amount_clean
    
    # Référence commande
    ref_text = f"Référence commande : {order_ref}" if order_ref else ""
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    system_prompt = """Tu es un avocat tenace et expérimenté, spécialisé en droit de la consommation et droit des transports européen.

TON RÔLE : Rédiger des mises en demeure formelles, professionnelles et juridiquement solides.

STYLE :
- Ton FROID et JURIDIQUE (jamais familier)
- Phrases courtes et percutantes
- Citations PRÉCISES des articles de loi
- Menaces légales claires (DGCCRF, Médiateur, Tribunal)
- Délai de réponse : 8 jours ouvrés

STRUCTURE OBLIGATOIRE :
1. Entête (Objet, Références)
2. Rappel des faits
3. Fondement juridique (articles PRÉCIS)
4. Demande formelle (remboursement/livraison)
5. Mise en demeure avec délai
6. Conséquences en cas de non-réponse
7. Formule de politesse sobre

SIGNATURE : "L'équipe Juridique Justicio, pour le compte de [NOM CLIENT]"

FORMAT : Réponds UNIQUEMENT avec le corps de la lettre en HTML bien formaté (utilise <p>, <strong>, <ul>, <li>). Pas de balises <html> ou <body>."""

    user_prompt = f"""Rédige une mise en demeure formelle pour les éléments suivants :

ENTREPRISE VISÉE : {company.upper()}
MONTANT RÉCLAMÉ : {amount_formatted} €
NATURE DU LITIGE : {motif}
FONDEMENT JURIDIQUE : {law}
{ref_text}

CLIENT :
- Nom : {client_name}
- Email : {client_email}

DATE : {today_str}
DÉLAI DE RÉPONSE : {deadline}

Génère une mise en demeure percutante et menaçante, avec les articles de loi précis."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # ou gpt-4 pour plus de qualité
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,  # Consistance juridique
            max_tokens=1500
        )
        
        letter_content = response.choices[0].message.content.strip()
        
        DEBUG_LOGS.append(f"⚖️ Agent Avocat: ✅ Lettre générée ({len(letter_content)} chars)")
        
        # Construire le HTML complet
        html_body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Georgia, serif; line-height: 1.6; color: #1e293b; max-width: 700px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #dc2626, #991b1b); color: white; padding: 25px; border-radius: 10px 10px 0 0; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 24px; letter-spacing: 2px; }}
        .content {{ background: white; padding: 30px; border: 1px solid #e2e8f0; }}
        .warning {{ background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 15px; margin: 20px 0; }}
        .warning strong {{ color: #dc2626; }}
        .footer {{ background: #1e293b; color: #94a3b8; padding: 20px; text-align: center; border-radius: 0 0 10px 10px; font-size: 12px; }}
        .amount {{ font-size: 24px; color: #dc2626; font-weight: bold; }}
        .deadline {{ color: #dc2626; font-weight: bold; }}
        ul {{ margin: 10px 0; padding-left: 20px; }}
        li {{ margin: 5px 0; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>⚖️ MISE EN DEMEURE</h1>
        <p style="margin:10px 0 0 0; font-size:14px;">Document juridique - Art. 1344 du Code Civil</p>
    </div>
    
    <div class="content">
        <p style="text-align:right; color:#64748b;">Paris, le {today_str}</p>
        
        <p><strong>À l'attention de :</strong> {company.upper()}</p>
        <p><strong>Objet :</strong> Mise en demeure - {motif[:60]}...</p>
        
        {letter_content}
        
        <div class="warning">
            <p><strong>⚠️ MISE EN DEMEURE</strong></p>
            <p>Sans réponse satisfaisante avant le <span class="deadline">{deadline}</span>, je me réserve le droit de :</p>
            <ul>
                <li>Saisir le <strong>Médiateur de la Consommation</strong></li>
                <li>Signaler cette pratique à la <strong>DGCCRF</strong></li>
                <li>Engager une <strong>procédure judiciaire</strong></li>
            </ul>
        </div>
        
        <p>Cordialement,</p>
        <p><strong>{client_name}</strong><br>
        <span style="color:#64748b;">{client_email}</span></p>
        
        <hr style="margin:25px 0; border:none; border-top:1px solid #e2e8f0;">
        <p style="font-size:12px; color:#64748b;">
            <strong>Montant réclamé :</strong> <span class="amount">{amount_formatted} €</span><br>
            <strong>Fondement juridique :</strong> {law}
        </p>
    </div>
    
    <div class="footer">
        <p><strong style="color:#fbbf24;">Justicio.fr</strong> - Protection des droits des consommateurs</p>
        <p>Ce document constitue une mise en demeure au sens juridique du terme.</p>
    </div>
</body>
</html>"""

        # Version texte brut pour fallback
        text_body = f"""MISE EN DEMEURE

Date : {today_str}
À l'attention de : {company.upper()}

{motif}

Montant réclamé : {amount_formatted} €
Fondement juridique : {law}

Délai de réponse : {deadline}

Sans réponse satisfaisante, je me réserve le droit de saisir le Médiateur de la Consommation, la DGCCRF, ou d'engager une procédure judiciaire.

{client_name}
{client_email}

---
Justicio.fr - Protection des droits des consommateurs
"""

        return {
            "success": True,
            "html_body": html_body,
            "text_body": text_body,
            "subject": f"⚖️ MISE EN DEMEURE - {company.upper()} - {motif[:50]}",
            "error": None
        }
        
    except Exception as e:
        error_msg = str(e)
        DEBUG_LOGS.append(f"⚖️ Agent Avocat: ❌ Erreur GPT: {error_msg[:100]}")
        return {
            "success": False,
            "error": error_msg[:100],
            "html_body": None,
            "text_body": None,
            "subject": None
        }


# ════════════════════════════════════════════════════════════════════════════════
# 📬 AGENT FACTEUR - Envoi RÉEL des emails via Gmail API
# ════════════════════════════════════════════════════════════════════════════════

def send_mise_en_demeure_gmail(user, target_email, subject, html_body, text_body=None, litigation_id=None):
    """
    📬 AGENT FACTEUR - Envoie la mise en demeure via Gmail API
    
    GARANTIES :
    - Envoi RÉEL via service.users().messages().send()
    - BCC à l'utilisateur pour preuve
    - Headers professionnels (anti-spam)
    - Gestion robuste des erreurs
    - Logging détaillé pour admin
    
    Args:
        user: Instance User avec refresh_token
        target_email: Email du destinataire (entreprise)
        subject: Sujet de l'email
        html_body: Corps HTML de la mise en demeure
        text_body: Corps texte (fallback)
        litigation_id: ID du litige pour tracking
    
    Returns:
        dict: {"success": bool, "message_id": str, "error": str, "error_type": str}
    """
    
    DEBUG_LOGS.append(f"📬 Agent Facteur: Préparation envoi vers {target_email}")
    
    # ═══════════════════════════════════════════════════════════════
    # VÉRIFICATIONS PRÉALABLES
    # ═══════════════════════════════════════════════════════════════
    
    if not user or not user.refresh_token:
        DEBUG_LOGS.append("📬 ❌ Erreur: Utilisateur non authentifié ou pas de refresh_token")
        return {
            "success": False,
            "message_id": None,
            "error": "Utilisateur non authentifié. Veuillez vous reconnecter.",
            "error_type": "AUTH_ERROR"
        }
    
    if not target_email or '@' not in target_email:
        DEBUG_LOGS.append(f"📬 ❌ Erreur: Email destinataire invalide: {target_email}")
        return {
            "success": False,
            "message_id": None,
            "error": f"Email destinataire invalide: {target_email}",
            "error_type": "INVALID_EMAIL"
        }
    
    # Nettoyer l'email destinataire
    if '<' in target_email and '>' in target_email:
        import re
        match = re.search(r'<([^>]+)>', target_email)
        if match:
            target_email = match.group(1)
    target_email = target_email.strip().lower()
    
    # ═══════════════════════════════════════════════════════════════
    # OBTENIR LES CREDENTIALS GMAIL
    # ═══════════════════════════════════════════════════════════════
    
    try:
        creds = get_refreshed_credentials(user.refresh_token)
        if not creds:
            raise Exception("Impossible de rafraîchir les credentials")
    except Exception as e:
        error_msg = str(e)
        DEBUG_LOGS.append(f"📬 ❌ Erreur credentials: {error_msg}")
        
        # Détecter le type d'erreur
        if "token" in error_msg.lower() or "expired" in error_msg.lower():
            return {
                "success": False,
                "message_id": None,
                "error": "Session expirée. Veuillez vous reconnecter.",
                "error_type": "TOKEN_EXPIRED"
            }
        return {
            "success": False,
            "message_id": None,
            "error": f"Erreur d'authentification: {error_msg[:50]}",
            "error_type": "AUTH_ERROR"
        }
    
    # ═══════════════════════════════════════════════════════════════
    # CONSTRUIRE LE MESSAGE MIME
    # ═══════════════════════════════════════════════════════════════
    
    try:
        # Message multipart pour HTML + texte
        message = MIMEMultipart('alternative')
        
        # Headers obligatoires
        message['To'] = target_email
        message['Subject'] = subject
        
        # BCC : Copie cachée à l'utilisateur (PREUVE)
        message['Bcc'] = user.email
        
        # From : Format professionnel pour éviter le spam
        user_name = user.name or user.email.split('@')[0].title()
        message['From'] = f'"{user_name} via Justicio" <{user.email}>'
        
        # Headers anti-spam et tracking
        message['X-Priority'] = '1'
        message['Importance'] = 'high'
        message['X-Justicio-Service'] = 'legal-notice'
        if litigation_id:
            message['X-Justicio-Case-ID'] = str(litigation_id)
        message['X-Mailer'] = 'Justicio Legal Services'
        
        # Ajouter le corps texte (fallback)
        if text_body:
            part_text = MIMEText(text_body, 'plain', 'utf-8')
            message.attach(part_text)
        
        # Ajouter le corps HTML (prioritaire)
        if html_body:
            part_html = MIMEText(html_body, 'html', 'utf-8')
            message.attach(part_html)
        
        # Encoder en base64 URL-safe
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        
    except Exception as e:
        DEBUG_LOGS.append(f"📬 ❌ Erreur construction MIME: {str(e)}")
        return {
            "success": False,
            "message_id": None,
            "error": f"Erreur construction email: {str(e)[:50]}",
            "error_type": "MIME_ERROR"
        }
    
    # ═══════════════════════════════════════════════════════════════
    # ENVOI RÉEL VIA GMAIL API
    # ═══════════════════════════════════════════════════════════════
    
    try:
        service = build('gmail', 'v1', credentials=creds)
        
        DEBUG_LOGS.append(f"📬 Envoi en cours: {target_email} (BCC: {user.email})")
        
        # ENVOI RÉEL !!!
        result = service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        message_id = result.get('id')
        
        if message_id:
            DEBUG_LOGS.append(f"📬 ✅ EMAIL ENVOYÉ ! Message ID: {message_id}")
            DEBUG_LOGS.append(f"📬 ✅ Destinataire: {target_email}")
            DEBUG_LOGS.append(f"📬 ✅ BCC (preuve): {user.email}")
            
            return {
                "success": True,
                "message_id": message_id,
                "error": None,
                "error_type": None
            }
        else:
            DEBUG_LOGS.append("📬 ❌ Envoi échoué - Pas de message_id retourné")
            return {
                "success": False,
                "message_id": None,
                "error": "L'API Gmail n'a pas confirmé l'envoi",
                "error_type": "NO_CONFIRMATION"
            }
            
    except Exception as e:
        error_msg = str(e)
        DEBUG_LOGS.append(f"📬 ❌ Erreur Gmail API: {error_msg[:150]}")
        
        # Analyser le type d'erreur
        error_lower = error_msg.lower()
        
        if "insufficient" in error_lower or "scope" in error_lower or "permission" in error_lower:
            return {
                "success": False,
                "message_id": None,
                "error": "Permissions insuffisantes. Reconnectez-vous pour autoriser l'envoi d'emails.",
                "error_type": "INSUFFICIENT_PERMISSIONS"
            }
        elif "quota" in error_lower or "rate" in error_lower:
            return {
                "success": False,
                "message_id": None,
                "error": "Limite d'envoi atteinte. Réessayez dans quelques minutes.",
                "error_type": "QUOTA_EXCEEDED"
            }
        elif "invalid" in error_lower and "recipient" in error_lower:
            return {
                "success": False,
                "message_id": None,
                "error": f"Adresse email invalide: {target_email}",
                "error_type": "INVALID_RECIPIENT"
            }
        else:
            return {
                "success": False,
                "message_id": None,
                "error": f"Erreur Gmail: {error_msg[:80]}",
                "error_type": "GMAIL_API_ERROR"
            }


# ════════════════════════════════════════════════════════════════════════════════
# 📧 ANNUAIRE OVERRIDE EMAILS ENTREPRISES (Priorité absolue)
# ════════════════════════════════════════════════════════════════════════════════

COMPANY_EMAIL_OVERRIDE = {
    "sncf": "relationclient@sncf.fr",
    "ouigo": "relationclient@ouigo.com",
    "eurostar": "contactcentre@eurostar.com",
    "air france": "customer@airfrance.fr",
    "easyjet": "customerservices@easyjet.com",
    "ryanair": "support@ryanair.com",
    "transavia": "service.client@transavia.com",
    "amazon": "cs-reply@amazon.fr",
    "zalando": "service@zalando.fr",
    "fnac": "serviceclient@fnac.com",
    "darty": "serviceclient@darty.com",
    "cdiscount": "clients@cdiscount.com",
}

def normalize_company_key(name: str) -> str:
    """Normalise le nom d'entreprise pour lookup dans l'annuaire"""
    if not name:
        return ""
    n = name.lower().strip()
    n = n.replace("&", "and")
    n = re.sub(r"\s+", " ", n)
    return n


def get_company_email(company_name, sender_email=None, to_field=None):
    """
    🔍 Trouve l'email de contact d'une entreprise
    
    Priorité :
    1) Override annuaire (priorité absolue)
    2) LEGAL_DIRECTORY (si présent)
    3) Variations (contains)
    4) Fallback support
    """
    company_key = normalize_company_key(company_name)

    if company_key in COMPANY_EMAIL_OVERRIDE:
        return COMPANY_EMAIL_OVERRIDE[company_key]

    for k, v in COMPANY_EMAIL_OVERRIDE.items():
        if k and k in company_key:
            return v

    if company_key in LEGAL_DIRECTORY and LEGAL_DIRECTORY[company_key].get("email"):
        return LEGAL_DIRECTORY[company_key]["email"]

    variations = {
        "air france": "customer@airfrance.fr",
        "airfrance": "customer@airfrance.fr",
        "easyjet": "customerservices@easyjet.com",
        "ryanair": "support@ryanair.com",
        "transavia": "service.client@transavia.com",
        "vueling": "clientes@vueling.com",
        "volotea": "contact@volotea.com",
        "eurostar": "contactcentre@eurostar.com",
        "ouigo": "relationclient@ouigo.com",
        "thalys": "contact@thalys.com",
        "uber": "support@uber.com",
        "bolt": "support@bolt.eu",
        "amazon": "cs-reply@amazon.fr",
        "zalando": "service@zalando.fr",
        "fnac": "serviceclient@fnac.com",
        "darty": "serviceclient@darty.com",
        "cdiscount": "clients@cdiscount.com",
    }
    for key, email in variations.items():
        if key in company_key:
            return email

    _dbg(f"🔍 Email non trouvé pour {company_name} - fallback support")
    return SUPPORT_EMAIL


def process_pending_litigations(user, litigations_data):
    """
    🚀 PROCESSEUR PRINCIPAL - Traite tous les litiges pending après paiement
    
    Pour chaque litige :
    1. Génère la mise en demeure (Agent Avocat GPT)
    2. Envoie l'email (Agent Facteur Gmail)
    3. Met à jour le statut en base
    4. Notifie via Telegram
    
    Args:
        user: Instance User
        litigations_data: Liste de dicts avec les données des litiges
    
    Returns:
        dict: {"sent": int, "errors": list, "details": list}
    """
    
    sent_count = 0
    errors = []
    details = []
    
    DEBUG_LOGS.append(f"🚀 Traitement de {len(litigations_data)} litige(s) pour {user.email}")
    
    for lit_data in litigations_data:
        company = lit_data.get('company', 'Inconnu')
        amount = lit_data.get('amount', '0€')
        motif = lit_data.get('subject', lit_data.get('proof', 'Litige non spécifié'))
        law = lit_data.get('law', 'Code de la consommation')
        message_id = lit_data.get('message_id')
        
        DEBUG_LOGS.append(f"📝 Traitement: {company} - {amount}")
        
        # ═══════════════════════════════════════════════════════════════
        # ÉTAPE 1 : Enregistrer en base de données
        # ═══════════════════════════════════════════════════════════════
        
        try:
            new_lit = Litigation(
                user_email=user.email,
                company=company,
                amount=amount,
                law=law,
                subject=motif,
                message_id=message_id,
                status="En traitement"
            )
            db.session.add(new_lit)
            db.session.commit()
            litigation_id = new_lit.id
            DEBUG_LOGS.append(f"   ✅ Dossier #{litigation_id} créé")
        except IntegrityError:
            db.session.rollback()
            errors.append(f"🔄 {company}: Doublon ignoré")
            continue
        except Exception as e:
            db.session.rollback()
            errors.append(f"❌ {company}: Erreur DB - {str(e)[:30]}")
            continue
        
        # ═══════════════════════════════════════════════════════════════
        # ÉTAPE 2 : Trouver l'email de l'entreprise
        # ═══════════════════════════════════════════════════════════════
        
        target_email = get_company_email(
            company,
            sender_email=lit_data.get("sender", ""),
            to_field=lit_data.get("to_field", "")
        )
        DEBUG_LOGS.append(f"   📧 Email cible: {target_email}")
        
        # ═══════════════════════════════════════════════════════════════
        # ÉTAPE 3 : Générer la mise en demeure (Agent Avocat)
        # ═══════════════════════════════════════════════════════════════
        
        user_name = user.name or user.email.split('@')[0].title()
        
        letter_result = generate_legal_letter_gpt(
            company=company,
            amount=amount,
            motif=motif,
            law=law,
            client_name=user_name,
            client_email=user.email,
            order_ref=None
        )
        
        if not letter_result["success"]:
            errors.append(f"⚠️ {company}: Échec génération lettre - {letter_result['error']}")
            new_lit.status = "Erreur génération"
            db.session.commit()
            continue
        
        DEBUG_LOGS.append(f"   ✅ Lettre générée")
        
        # ═══════════════════════════════════════════════════════════════
        # ÉTAPE 4 : Envoyer l'email (Agent Facteur)
        # ═══════════════════════════════════════════════════════════════
        
        send_result = send_mise_en_demeure_gmail(
            user=user,
            target_email=target_email,
            subject=letter_result["subject"],
            html_body=letter_result["html_body"],
            text_body=letter_result["text_body"],
            litigation_id=litigation_id
        )
        
        if send_result["success"]:
            # Succès !
            new_lit.status = "En attente de réponse"
            new_lit.legal_notice_sent = True
            new_lit.legal_notice_date = datetime.now()
            new_lit.legal_notice_message_id = send_result["message_id"]
            new_lit.merchant_email = target_email
            db.session.commit()
            
            sent_count += 1
            details.append({
                "company": company,
                "amount": amount,
                "email": target_email,
                "status": "✅ Envoyé"
            })
            
            DEBUG_LOGS.append(f"   ✅ ENVOYÉ ! Message ID: {send_result['message_id']}")
            
            # Notification Telegram
            send_telegram_notif(
                f"📧 MISE EN DEMEURE ENVOYÉE !\n\n"
                f"🏪 {company.upper()}\n"
                f"💰 {amount}\n"
                f"📬 Envoyé à: {target_email}\n"
                f"👤 Client: {user.email}"
            )
        else:
            # Échec
            error_detail = f"{send_result['error_type']}: {send_result['error']}"
            errors.append(f"❌ {company}: {send_result['error']}")
            new_lit.status = f"Erreur envoi: {send_result['error_type']}"
            db.session.commit()
            
            details.append({
                "company": company,
                "amount": amount,
                "email": target_email,
                "status": f"❌ {send_result['error_type']}"
            })
            
            DEBUG_LOGS.append(f"   ❌ Échec: {error_detail}")
    
    DEBUG_LOGS.append(f"🚀 Traitement terminé: {sent_count}/{len(litigations_data)} envoyé(s)")
    
    return {
        "sent": sent_count,
        "total": len(litigations_data),
        "errors": errors,
        "details": details
    }

# ========================================
# BLACKLIST ANTI-SPAM (PARE-FEU) - CORRIGÉ BUG N°2
# ========================================
# On garde UNIQUEMENT les termes liés au SPAM pur
# On retire les termes génériques qui causent des faux positifs

BLACKLIST_SENDERS = [
    # Sites e-commerce low-cost / spam
    "temu", "shein", "aliexpress", "vinted", "wish.com",
    # Réseaux sociaux (notifications)
    "linkedin", "pinterest", "tiktok", "facebook", "twitter", "instagram",
    # Newsletters génériques
    "newsletter@", "noreply@dribbble", "notifications@medium",
    # Marketing pur
    "marketing@", "promo@", "deals@", "offers@"
]

BLACKLIST_SUBJECTS = [
    # Offres commerciales pures
    "crédit offert", "crédit gratuit", "prêt personnel",
    "coupon exclusif", "code promo exclusif",
    "offre spéciale limitée", "vente flash",
    "soldes exceptionnelles",
    "félicitations vous avez gagné", "vous êtes sélectionné",
    "cadeau gratuit",
    # Newsletters
    "notre newsletter", "weekly digest", "bulletin hebdomadaire",
    # Sécurité compte (pas des litiges)
    "changement de mot de passe", "connexion inhabituelle",
    "vérifiez votre identité", "activate your account"
]

BLACKLIST_KEYWORDS = [
    # Désabonnement (signe de newsletter)
    "pour vous désabonner cliquez",
    "unsubscribe from this list",
    # Promos pures
    "jusqu'à -70%", "jusqu'à -50%",
    "-10% sur votre prochaine commande",
    "utilisez le code promo"
]

# ========================================
# RÉPERTOIRE JURIDIQUE COMPLET
# ========================================

LEGAL_DIRECTORY = {
    "amazon": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Droits des consommateurs)"},
    "apple": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 1999/44 (Garantie légale)"},
    "zalando": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Retour 14 jours)"},
    "shein": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Conformité)"},
    "zara": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Remboursement)"},
    "h&m": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Remboursement)"},
    "asos": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Retour)"},
    "fnac": {"email": "theodordelgao@gmail.com", "loi": "l'Article L217-4 du Code de la consommation"},
    "darty": {"email": "theodordelgao@gmail.com", "loi": "l'Article L217-4 du Code de la consommation"},
    "booking": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2015/2302 (Voyages à forfait)"},
    "airbnb": {"email": "theodordelgao@gmail.com", "loi": "le Règlement Rome I (Protection consommateur)"},
    "expedia": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2015/2302"},
    "ryanair": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "easyjet": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "lufthansa": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "air france": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "klm": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "british airways": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "sncf": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "eurostar": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "ouigo": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "uber": {"email": "theodordelgao@gmail.com", "loi": "le Droit Européen de la Consommation"},
    "deliveroo": {"email": "theodordelgao@gmail.com", "loi": "le Droit Européen de la Consommation"},
    "bolt": {"email": "theodordelgao@gmail.com", "loi": "le Droit Européen de la Consommation"}
}

# ========================================
# BASE DE DONNÉES
# ========================================

db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "connect_args": {"keepalives": 1}
}

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    refresh_token = db.Column(db.String(500))
    name = db.Column(db.String(100))
    stripe_customer_id = db.Column(db.String(100))

class Litigation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120), nullable=False)
    company = db.Column(db.String(100))
    amount = db.Column(db.String(50))
    law = db.Column(db.String(200))
    subject = db.Column(db.String(300))
    message_id = db.Column(db.String(100))
    status = db.Column(db.String(50), default="Détecté")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # ════════════════════════════════════════════════════════════════
    # NOUVEAUX CHAMPS POUR DÉCLARATION MANUELLE (V2)
    # ════════════════════════════════════════════════════════════════
    source = db.Column(db.String(20), default="SCAN")  # "SCAN" ou "MANUAL"
    url_site = db.Column(db.String(300))  # URL du site e-commerce
    order_id = db.Column(db.String(100))  # Numéro de commande
    order_date = db.Column(db.Date)  # Date de commande
    amount_float = db.Column(db.Float)  # Montant en float pour calculs
    problem_type = db.Column(db.String(50))  # Type de problème
    description = db.Column(db.Text)  # Description détaillée du litige
    
    # ════════════════════════════════════════════════════════════════
    # CHAMPS AGENT DÉTECTIVE (V3)
    # ════════════════════════════════════════════════════════════════
    merchant_email = db.Column(db.String(200))  # Email trouvé par le détective
    merchant_email_source = db.Column(db.String(100))  # Page où l'email a été trouvé
    
    # ════════════════════════════════════════════════════════════════
    # CHAMPS ENVOI MISE EN DEMEURE (V4)
    # ════════════════════════════════════════════════════════════════
    legal_notice_sent = db.Column(db.Boolean, default=False)  # Mise en demeure envoyée
    legal_notice_date = db.Column(db.DateTime)  # Date d'envoi
    legal_notice_message_id = db.Column(db.String(100))  # ID Gmail du message envoyé

with app.app_context():
    try:
        # Migration : Ajoute les colonnes manquantes
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('litigation')]
        
        if 'message_id' not in columns:
            print("🔄 Migration : Ajout de message_id...")
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE litigation ADD COLUMN message_id VARCHAR(100)'))
                conn.commit()
            print("✅ Colonne message_id ajoutée")
        
        if 'updated_at' not in columns:
            print("🔄 Migration : Ajout de updated_at...")
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE litigation ADD COLUMN updated_at TIMESTAMP DEFAULT NOW()'))
                conn.commit()
            print("✅ Colonne updated_at ajoutée")
        
        # ════════════════════════════════════════════════════════════════
        # MIGRATIONS V2 - Déclaration manuelle
        # ════════════════════════════════════════════════════════════════
        
        new_columns_v2 = {
            'source': 'VARCHAR(20) DEFAULT \'SCAN\'',
            'url_site': 'VARCHAR(300)',
            'order_id': 'VARCHAR(100)',
            'order_date': 'DATE',
            'amount_float': 'FLOAT',
            'problem_type': 'VARCHAR(50)',
            'description': 'TEXT'
        }
        
        for col_name, col_type in new_columns_v2.items():
            if col_name not in columns:
                print(f"🔄 Migration V2 : Ajout de {col_name}...")
                with db.engine.connect() as conn:
                    conn.execute(text(f'ALTER TABLE litigation ADD COLUMN {col_name} {col_type}'))
                    conn.commit()
                print(f"✅ Colonne {col_name} ajoutée")
        
        # ════════════════════════════════════════════════════════════════
        # MIGRATIONS V3 - Agent Détective
        # ════════════════════════════════════════════════════════════════
        
        new_columns_v3 = {
            'merchant_email': 'VARCHAR(200)',
            'merchant_email_source': 'VARCHAR(100)'
        }
        
        for col_name, col_type in new_columns_v3.items():
            if col_name not in columns:
                print(f"🔄 Migration V3 : Ajout de {col_name}...")
                with db.engine.connect() as conn:
                    conn.execute(text(f'ALTER TABLE litigation ADD COLUMN {col_name} {col_type}'))
                    conn.commit()
                print(f"✅ Colonne {col_name} ajoutée")
        
        # ════════════════════════════════════════════════════════════════
        # MIGRATIONS V4 - Envoi Mise en Demeure
        # ════════════════════════════════════════════════════════════════
        
        new_columns_v4 = {
            'legal_notice_sent': 'BOOLEAN DEFAULT FALSE',
            'legal_notice_date': 'TIMESTAMP',
            'legal_notice_message_id': 'VARCHAR(100)'
        }
        
        for col_name, col_type in new_columns_v4.items():
            if col_name not in columns:
                print(f"🔄 Migration V4 : Ajout de {col_name}...")
                with db.engine.connect() as conn:
                    conn.execute(text(f'ALTER TABLE litigation ADD COLUMN {col_name} {col_type}'))
                    conn.commit()
                print(f"✅ Colonne {col_name} ajoutée")
        
        db.create_all()
        print("✅ Base de données synchronisée (V4 - Envoi Mise en Demeure).")
    except Exception as e:
        print(f"❌ Erreur DB : {e}")

# ========================================
# GESTIONNAIRE D'ERREURS
# ========================================

DEBUG_LOGS = []

# ========================================
# 🔐 CONFIGURATION ADMIN
# ========================================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "justicio2026")  # À changer en production

# ========================================
# 👁️ MIDDLEWARE LOGGER - Espion de trafic
# ========================================

@app.before_request
def log_request():
    """
    👁️ LOGGER ESPION - Log chaque visite en temps réel
    Format: 👁️ [IP: xxx.xxx.xxx.xxx] a visité [METHOD /url] à [HEURE]
    
    Visible dans les logs Render/Console
    """
    # Ignorer les fichiers statiques et les health checks
    ignored_paths = ['/favicon.ico', '/static/', '/health', '/robots.txt']
    if any(request.path.startswith(p) for p in ignored_paths):
        return
    
    # Récupérer l'IP réelle (derrière proxy/Cloudflare)
    ip = request.headers.get('CF-Connecting-IP') or \
         request.headers.get('X-Real-IP') or \
         request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or \
         request.remote_addr or \
         'Unknown'
    
    # Timestamp
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Méthode et URL
    method = request.method
    url = request.path
    
    # Query string si présent (sans les tokens sensibles)
    if request.query_string:
        qs = request.query_string.decode('utf-8', errors='ignore')
        # Masquer les tokens sensibles
        if 'token=' in qs:
            qs = 'token=***'
        url = f"{url}?{qs}"
    
    # Log formaté
    log_line = f"👁️ [{timestamp}] [{ip}] {method} {url}"
    print(log_line)
    
    # Stocker dans DEBUG_LOGS (garder les 100 derniers)
    DEBUG_LOGS.append(log_line)
    if len(DEBUG_LOGS) > 500:
        DEBUG_LOGS.pop(0)

@app.errorhandler(Exception)
def handle_exception(e):
    error_trace = traceback.format_exc()
    DEBUG_LOGS.append(f"❌ {datetime.utcnow()}: {str(e)}")
    return f"""
    <div style='font-family:sans-serif; padding:20px; color:red; background:#fee2e2; border:2px solid red;'>
        <h1>❌ ERREUR CRITIQUE</h1>
        <p>Une erreur est survenue. Voici les détails techniques :</p>
        <pre style='background:#333; color:#fff; padding:15px; overflow:auto;'>{error_trace}</pre>
        <a href='/' style='display:inline-block; margin-top:20px; padding:10px; background:#333; color:white; text-decoration:none;'>Retour</a>
    </div>
    """, 500

# ========================================
# FONCTIONS UTILITAIRES
# ========================================

def send_telegram_notif(message):
    """Envoie une notification Telegram"""
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
                timeout=5
            )
        except:
            pass

def get_refreshed_credentials(refresh_token):
    """Rafraîchit les credentials Google"""
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET
    )
    creds.refresh(Request())
    return creds

def is_spam(sender, subject, body_snippet):
    """Vérifie si un email est un spam (PARE-FEU) - VERSION CORRIGÉE"""
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    body_lower = body_snippet.lower()
    
    # Check expéditeur
    for black in BLACKLIST_SENDERS:
        if black in sender_lower:
            return True, f"Sender blacklist: {black}"
    
    # Check sujet - on cherche des correspondances plus précises
    for black in BLACKLIST_SUBJECTS:
        if black in subject_lower:
            return True, f"Subject blacklist: {black}"
    
    # Check body - seulement si la phrase EXACTE est présente
    for black in BLACKLIST_KEYWORDS:
        if black in body_lower:
            return True, f"Body blacklist: {black}"
    
    return False, None

# ════════════════════════════════════════════════════════════════════════════════
# 🚀 HELPERS PERFORMANCE/ROBUSTESSE POUR SCANS
# ════════════════════════════════════════════════════════════════════════════════

def _dbg(msg: str):
    """Helper debug log sécurisé"""
    try:
        if isinstance(globals().get("DEBUG_LOGS"), list):
            DEBUG_LOGS.append(msg)
    except:
        pass

def contains_any(text: str, keywords) -> bool:
    """Vérifie si text contient au moins un keyword"""
    if not text:
        return False
    t = text.lower()
    return any(k.lower() in t for k in keywords)

# Mots-clés pour pré-filtrage rapide TRANSPORT
TRAVEL_FAST_INCLUDE = [
    "sncf", "ouigo", "inoui", "tgv", "ter", "eurostar", "thalys", "trenitalia",
    "air france", "airfrance", "easyjet", "ryanair", "transavia", "vueling", "lufthansa", "klm", "volotea",
    "vol ", "flight", "train", "billet",
    "retard", "delay", "annul", "cancel", "compensation", "indemn", "réclamation", "reclamation"
]

TRAVEL_FAST_EXCLUDE = [
    "amazon", "zalando", "cdiscount", "darty", "fnac", "temu", "shein", "aliexpress",
    "colis", "commande", "livraison", "order", "delivery", "package", "retour"
]

# Mots-clés pour pré-filtrage rapide E-COMMERCE
ECOM_FAST_INCLUDE = [
    "commande", "colis", "livraison", "order", "delivery", "package", "shipment",
    "non reçu", "pas reçu", "jamais reçu", "not received", "never received",
    "retard", "delay", "perdu", "lost", "manquant", "missing",
    "défectueux", "defective", "cassé", "broken", "abîmé", "damaged",
    "remboursement", "refund", "retour", "return", "réclamation", "complaint", "litige", "dispute",
]

ECOM_FAST_EXCLUDE = [
    "sncf", "ouigo", "tgv", "ter", "eurostar", "thalys",
    "air france", "easyjet", "ryanair", "transavia", "vueling",
    "vol ", "flight", "train", "billet", "embarquement", "gate", "boarding pass"
]

def fast_candidate_filter(scan_type: str, sender: str, subject: str, snippet: str) -> tuple:
    """
    Pré-filtre rapide AVANT appel IA - retourne (bool, reason)
    """
    subject = subject or ""
    sender = sender or ""
    snippet = snippet or ""
    blob = f"{sender} {subject} {snippet}".lower()

    if "mise en demeure" in subject.lower():
        return False, "Notre propre email"

    if contains_any(blob, KEYWORDS_SUCCESS):
        return False, "Déjà résolu (success keyword)"
    if contains_any(blob, KEYWORDS_REFUSAL):
        return False, "Refus détecté (refusal keyword)"

    if contains_any(subject, ["newsletter", "unsubscribe", "désabonner", "promo", "soldes", "mot de passe", "password"]):
        return False, "Spam évident"

    if scan_type == "travel":
        if contains_any(blob, TRAVEL_FAST_EXCLUDE):
            return False, "Exclusion e-commerce"
        if not contains_any(blob, TRAVEL_FAST_INCLUDE):
            return False, "Pas assez d'indices transport"
        return True, "Candidat transport"

    # scan_type == "ecommerce"
    if contains_any(blob, ECOM_FAST_EXCLUDE):
        return False, "Exclusion transport"
    if not contains_any(blob, ECOM_FAST_INCLUDE):
        return False, "Pas assez d'indices e-commerce"
    return True, "Candidat e-commerce"

def get_gmail_headers(headers, name: str, default=""):
    """Récupère un header Gmail par son nom"""
    name = name.lower()
    for h in headers or []:
        if h.get("name", "").lower() == name:
            return h.get("value", default)
    return default

def safe_extract_body_text(msg_data, limit_chars=4000) -> str:
    """Extrait le texte du body de manière sécurisée"""
    try:
        payload = msg_data.get("payload", {}) or {}

        def walk(part):
            text = ""
            if not part:
                return text
            if "parts" in part:
                for sp in part["parts"]:
                    text += walk(sp)
                return text
            mt = part.get("mimeType")
            if mt in ("text/plain", "text/html"):
                data = (part.get("body", {}) or {}).get("data", "")
                if data:
                    decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    return decoded
            return ""

        body_raw = walk(payload)
        if not body_raw:
            return (msg_data.get("snippet") or "")[:limit_chars]

        body_clean = re.sub(r"<[^>]+>", " ", body_raw)
        body_clean = re.sub(r"\s+", " ", body_clean).strip()
        return body_clean[:limit_chars]
    except Exception as e:
        _dbg(f"⚠️ safe_extract_body_text error: {type(e).__name__}: {str(e)[:60]}")
        return (msg_data.get("snippet") or "")[:limit_chars]

# ========================================
# 🕵️ AGENT DÉTECTIVE - Scraping Email Marchand
# ========================================

def find_merchant_email(url):
    """
    🕵️ AGENT DÉTECTIVE V3 - Trouve l'email de contact d'un site marchand
    
    Stratégie ULTIME :
    1. Scraping direct du site (accueil + liens contact)
    2. FALLBACK 1 : Chemins standards CMS (Shopify, WordPress, Prestashop)
    3. FALLBACK 2 : Recherche DuckDuckGo/Bing
    4. Priorise les emails "contact", "support", "sav"
    
    Retourne : {"email": str|None, "source": str, "all_emails": list}
    """
    
    # ═══════════════════════════════════════════════════════════════
    # MODE DEBUG - Affiche les logs dans la console
    # ═══════════════════════════════════════════════════════════════
    DEBUG_MODE = True
    
    def debug_log(message, level="INFO"):
        """Log de debug avec timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {
            "INFO": "🔍",
            "SUCCESS": "✅",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "HTTP": "🌐"
        }.get(level, "📝")
        
        log_msg = f"[{timestamp}] {prefix} [DETECTIVE] {message}"
        print(log_msg)  # Console
        DEBUG_LOGS.append(log_msg)  # Stockage pour /debug-logs
    
    if not url:
        debug_log("URL vide, abandon", "WARNING")
        return {"email": None, "source": None, "all_emails": []}
    
    debug_log(f"═══════════════════════════════════════════════════", "INFO")
    debug_log(f"DÉMARRAGE ANALYSE : {url}", "INFO")
    debug_log(f"═══════════════════════════════════════════════════", "INFO")
    
    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION - Headers identiques à Chrome réel
    # ═══════════════════════════════════════════════════════════════
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.google.com/',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'DNT': '1',
    }
    
    # Timeout
    TIMEOUT = 8
    
    # ═══════════════════════════════════════════════════════════════
    # BLACKLIST DOMAINES - Emails à rejeter systématiquement
    # ═══════════════════════════════════════════════════════════════
    # Ces domaines apparaissent souvent dans les résultats de recherche
    # mais ne sont JAMAIS des emails de marchands
    
    BLACKLIST_EMAIL_DOMAINS = [
        # Médias / Journaux
        'lefigaro.fr', 'lemonde.fr', 'liberation.fr', 'lexpress.fr',
        'leparisien.fr', 'lepoint.fr', 'francetvinfo.fr', 'bfmtv.com',
        'tf1.fr', 'france24.com', '20minutes.fr', 'lesechos.fr',
        'latribune.fr', 'lequipe.fr', 'huffpost.fr', 'mediapart.fr',
        'nouvelobs.com', 'marianne.net', 'challenges.fr', 'rtl.fr',
        'europe1.fr', 'rfi.fr', 'franceinter.fr', 'ouest-france.fr',
        'sudouest.fr', 'lavoixdunord.fr', 'ladepeche.fr',
        'nytimes.com', 'theguardian.com', 'bbc.com', 'cnn.com',
        'forbes.com', 'bloomberg.com', 'reuters.com', 'wsj.com',
        'washingtonpost.com', 'independent.co.uk', 'mirror.co.uk',
        
        # Réseaux sociaux
        'facebook.com', 'twitter.com', 'instagram.com', 'tiktok.com',
        'linkedin.com', 'youtube.com', 'pinterest.com', 'snapchat.com',
        'reddit.com', 'tumblr.com', 'twitch.tv', 'discord.com',
        
        # Email génériques (webmail)
        'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
        'live.com', 'msn.com', 'aol.com', 'protonmail.com',
        'icloud.com', 'me.com', 'mail.com', 'gmx.com', 'yandex.com',
        'orange.fr', 'free.fr', 'sfr.fr', 'laposte.net', 'wanadoo.fr',
        
        # Sites d'avis / comparateurs
        'trustpilot.com', 'avis-verifies.com', 'tripadvisor.com',
        'yelp.com', 'google.com', 'facebook.com', 'quechoisir.org',
        '60millions-mag.com', 'signal-arnaques.com',
        
        # Sites tech / forums
        'wikipedia.org', 'github.com', 'stackoverflow.com',
        'medium.com', 'wordpress.com', 'blogger.com', 'wix.com',
        
        # Gouvernement / institutions
        'gouv.fr', 'service-public.fr', 'economie.gouv.fr',
        'dgccrf.finances.gouv.fr', 'cnil.fr', 'europa.eu',
    ]
    
    def is_email_domain_valid(email, site_domain, brand_name):
        """
        🕵️ VALIDATION STRICTE DU DOMAINE EMAIL
        
        Règles :
        1. Rejeter si domaine dans blacklist (médias, gmail, etc.)
        2. Accepter si domaine email = domaine site (exact)
        3. Accepter si domaine email contient le nom de marque (≥3 chars)
        4. Accepter si nom de marque contient domaine email
        5. SINON : Rejeter
        """
        try:
            email_domain = email.split('@')[1].lower()
            site_clean = site_domain.lower().replace('www.', '')
            brand_clean = brand_name.lower().strip()
            
            # RÈGLE 1 : Blacklist
            for blacklisted in BLACKLIST_EMAIL_DOMAINS:
                if blacklisted in email_domain or email_domain in blacklisted:
                    debug_log(f"🚫 Email {email} BLACKLISTÉ (domaine média/générique)", "WARNING")
                    return False, "blacklist"
            
            # RÈGLE 2 : Correspondance exacte du domaine
            if site_clean == email_domain or site_clean.replace('.com', '') == email_domain.replace('.com', ''):
                return True, "exact_match"
            
            # Extraire la partie principale du domaine (sans TLD)
            email_domain_base = email_domain.split('.')[0]
            site_domain_base = site_clean.split('.')[0]
            
            # RÈGLE 3 : Le domaine email contient le nom de marque (min 3 chars)
            if len(brand_clean) >= 3 and brand_clean in email_domain_base:
                return True, "brand_in_email"
            
            # RÈGLE 4 : Le nom de marque contient le domaine email (min 3 chars)
            if len(email_domain_base) >= 3 and email_domain_base in brand_clean:
                return True, "email_in_brand"
            
            # RÈGLE 5 : Correspondance partielle domaine
            if len(site_domain_base) >= 3 and site_domain_base in email_domain_base:
                return True, "site_in_email"
            
            if len(email_domain_base) >= 3 and email_domain_base in site_domain_base:
                return True, "email_in_site"
            
            # SINON : Rejet
            debug_log(f"🚫 Email {email} REJETÉ - Domaine '{email_domain}' ne correspond pas à '{site_domain}'", "WARNING")
            return False, "no_match"
            
        except Exception as e:
            debug_log(f"Erreur validation email {email}: {str(e)}", "ERROR")
            return False, "error"
    
    # ═══════════════════════════════════════════════════════════════
    # CHEMINS CMS STANDARDS (Shopify, WordPress, Prestashop, etc.)
    # ═══════════════════════════════════════════════════════════════
    
    STANDARD_PATHS = [
        # Génériques
        '/contact',
        '/contact-us',
        '/contactez-nous',
        '/nous-contacter',
        '/mentions-legales',
        '/mentions-légales',
        '/legal',
        '/legal-notice',
        '/cgv',
        '/cgu',
        '/conditions-generales-de-vente',
        '/conditions-generales',
        '/terms',
        '/terms-of-service',
        '/terms-and-conditions',
        '/support',
        '/aide',
        '/help',
        '/a-propos',
        '/about',
        '/about-us',
        '/qui-sommes-nous',
        
        # SHOPIFY spécifiques
        '/pages/contact',
        '/pages/contactez-nous',
        '/pages/nous-contacter',
        '/pages/mentions-legales',
        '/pages/mentions-légales',
        '/pages/legal',
        '/pages/cgv',
        '/pages/cgu',
        '/pages/a-propos',
        '/pages/about',
        '/pages/about-us',
        '/pages/faq',
        '/policies/legal-notice',
        '/policies/terms-of-service',
        '/policies/privacy-policy',
        '/policies/refund-policy',
        '/policies/shipping-policy',
        
        # WORDPRESS / WOOCOMMERCE
        '/page/contact',
        '/page/mentions-legales',
        '/page/cgv',
        '/?page_id=contact',
        '/contact-2',
        '/contactez-nous-2',
        
        # PRESTASHOP
        '/nous-contacter',
        '/contactez-nous.html',
        '/content/1-livraison',
        '/content/2-mentions-legales',
        '/content/3-conditions-generales-de-vente',
        '/content/4-a-propos',
        '/info/contact',
        '/infos/contact',
        
        # MAGENTO
        '/contacts',
        '/contact-us.html',
        '/customer-service',
        
        # WIXWIX
        '/contact-1',
        '/blank',
        
        # Autres patterns
        '/fr/contact',
        '/fr/mentions-legales',
        '/fr/cgv',
        '/en/contact',
        '/service-client',
        '/customer-service',
        '/help-center',
        '/centre-aide',
        
        # SHOPIFY FR supplémentaires
        '/pages/service-client',
        '/pages/sav',
        '/pages/contactez-nous-2',
        '/pages/contact-us',
        '/pages/informations-legales',
        '/pages/qui-sommes-nous',
        '/policies/contact-information',
        
        # Patterns avec .html
        '/contact.html',
        '/mentions-legales.html',
        '/cgv.html',
        '/a-propos.html',
    ]
    
    # Regex pour extraire les emails (standard)
    EMAIL_REGEX = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    
    # Regex pour emails obfusqués (contact [at] domain [dot] com)
    EMAIL_OBFUSCATED_PATTERNS = [
        r'([a-zA-Z0-9._%+-]+)\s*\[\s*at\s*\]\s*([a-zA-Z0-9.-]+)\s*\[\s*dot\s*\]\s*([a-zA-Z]{2,})',
        r'([a-zA-Z0-9._%+-]+)\s*\(\s*at\s*\)\s*([a-zA-Z0-9.-]+)\s*\(\s*dot\s*\)\s*([a-zA-Z]{2,})',
        r'([a-zA-Z0-9._%+-]+)\s*\[at\]\s*([a-zA-Z0-9.-]+)\s*\[dot\]\s*([a-zA-Z]{2,})',
        r'([a-zA-Z0-9._%+-]+)\s*arobase\s*([a-zA-Z0-9.-]+)\s*point\s*([a-zA-Z]{2,})',
    ]
    
    # Emails à ignorer (parasites)
    BLACKLIST_PATTERNS = [
        'example.com', 'domain.com', 'email.com', 'test.com', 'exemple.com',
        'wixpress.com', 'sentry.io', 'schema.org', 'w3.org', 'googleapis.com',
        'shopify.com', 'myshopify.com',
        'facebook.com', 'twitter.com', 'instagram.com', 'google.com', 'youtube.com',
        'noreply@', 'no-reply@', 'no_reply@', 'mailer-daemon@', 'daemon@',
        'postmaster@', 'webmaster@', 'hostmaster@', 'admin@', 'root@',
        'abuse@', 'spam@', 'unsubscribe@', 'newsletter@', 'marketing@', 'notification@',
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js', '.woff',
        'sentry', 'bugsnag', 'raygun', 'trackjs', 'hotjar', 'clarity',
        '@2x', '@3x',
        'placeholder', 'dummy', 'fake',
    ]
    
    # Mots-clés de liens à visiter
    CONTACT_KEYWORDS = [
        'contact', 'nous-contacter', 'contactez', 'contactez-nous',
        'mentions-legales', 'mentions_legales', 'legal', 'legales', 'mention',
        'cgv', 'cgu', 'conditions', 'terms', 'policies', 'policy',
        'support', 'aide', 'help', 'faq', 'assistance',
        'a-propos', 'about', 'qui-sommes-nous',
        'service-client', 'sav', 'reclamation', 'réclamation',
        'footer', 'pied-de-page'  # Souvent les liens légaux sont dans le footer
    ]
    
    # Priorité des emails (plus le score est élevé, mieux c'est)
    EMAIL_PRIORITY = {
        'contact': 100,
        'support': 95,
        'sav': 95,
        'service-client': 90,
        'serviceclient': 90,
        'service.client': 90,
        'client': 85,
        'clients': 85,
        'info': 80,
        'infos': 80,
        'information': 80,
        'legal': 75,
        'juridique': 75,
        'reclamation': 70,
        'réclamation': 70,
        'hello': 60,
        'bonjour': 60,
        'salut': 55,
        'commercial': 50,
        'vente': 50,
        'ventes': 50,
        'sales': 50,
        'order': 45,
        'commande': 45,
        'shop': 40,
        'boutique': 40,
    }
    
    # ═══════════════════════════════════════════════════════════════
    # FONCTIONS UTILITAIRES
    # ═══════════════════════════════════════════════════════════════
    
    def clean_url(raw_url):
        """Nettoie et normalise une URL"""
        raw_url = raw_url.strip()
        if not raw_url:
            return None
        raw_url = raw_url.rstrip('/')
        if not raw_url.startswith(('http://', 'https://')):
            raw_url = 'https://' + raw_url
        return raw_url
    
    def get_base_domain(full_url):
        """Extrait le domaine de base (ex: https://www.site.com)"""
        parsed = urlparse(full_url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def get_domain_name(full_url):
        """Extrait juste le nom de domaine (ex: site.com)"""
        parsed = urlparse(full_url)
        domain = parsed.netloc.replace('www.', '')
        return domain
    
    def is_valid_email(email):
        """Vérifie si un email est valide et pas dans la blacklist"""
        email_lower = email.lower()
        
        for blacklisted in BLACKLIST_PATTERNS:
            if blacklisted in email_lower:
                return False
        
        if email.count('@') != 1:
            return False
        
        local, domain = email.split('@')
        
        if len(local) < 2 or len(domain) < 4:
            return False
        
        if '.' not in domain:
            return False
        
        if domain.endswith(('.png', '.jpg', '.gif', '.css', '.js')):
            return False
        
        return True
    
    def extract_mailto_emails(soup):
        """Extrait les emails des balises mailto:"""
        emails = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag.get('href', '')
            if href.lower().startswith('mailto:'):
                email = href[7:].split('?')[0].strip()
                if email and is_valid_email(email):
                    emails.append(email)
        return emails
    
    def extract_emails_from_text(text):
        """Extrait tous les emails valides d'un texte (y compris obfusqués)"""
        emails = []
        
        # 1. Emails standards
        found = re.findall(EMAIL_REGEX, text, re.IGNORECASE)
        emails.extend([e for e in found if is_valid_email(e)])
        
        # 2. Emails obfusqués ([at], [dot], arobase, etc.)
        for pattern in EMAIL_OBFUSCATED_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match) == 3:
                    reconstructed = f"{match[0]}@{match[1]}.{match[2]}"
                    if is_valid_email(reconstructed):
                        emails.append(reconstructed)
        
        # 3. Pattern spécial : "contact at domain.com" ou "contact(at)domain.com"
        special_pattern = r'([a-zA-Z0-9._%+-]+)\s*(?:\(at\)|at|@|chez)\s*([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
        special_matches = re.findall(special_pattern, text, re.IGNORECASE)
        for match in special_matches:
            if len(match) == 2:
                reconstructed = f"{match[0]}@{match[1]}"
                if is_valid_email(reconstructed) and reconstructed not in emails:
                    emails.append(reconstructed)
        
        return list(set(emails))  # Dédupliquer
    
    def score_email(email, site_domain=None):
        """Calcule un score de priorité pour un email"""
        email_lower = email.lower()
        local_part = email_lower.split('@')[0]
        domain_part = email_lower.split('@')[1]
        
        score = 0
        
        for keyword, priority in EMAIL_PRIORITY.items():
            if keyword in local_part:
                score = max(score, priority)
        
        # BONUS si le domaine de l'email correspond au site
        if site_domain:
            site_domain_clean = site_domain.replace('www.', '').lower()
            email_domain_clean = domain_part.replace('www.', '').lower()
            # Correspondance exacte ou partielle
            if site_domain_clean == email_domain_clean:
                score += 60  # Correspondance exacte
            elif site_domain_clean in email_domain_clean or email_domain_clean in site_domain_clean:
                score += 40  # Correspondance partielle
        
        return score if score > 0 else 10
    
    def get_page_content(page_url, timeout=TIMEOUT):
        """Récupère le contenu d'une page avec gestion des erreurs et logs détaillés"""
        debug_log(f"Tentative accès : {page_url}", "HTTP")
        
        try:
            response = requests.get(
                page_url, 
                headers=HEADERS, 
                timeout=timeout, 
                allow_redirects=True,
                verify=True
            )
            
            status = response.status_code
            content_length = len(response.text) if response.text else 0
            
            if status == 200:
                debug_log(f"Status: {status} OK | Contenu: {content_length} chars", "SUCCESS")
                return response.text
            elif status == 403:
                debug_log(f"Status: {status} BLOQUÉ (Forbidden) - Anti-bot actif?", "WARNING")
            elif status == 404:
                debug_log(f"Status: {status} Page non trouvée", "WARNING")
            elif status == 503:
                debug_log(f"Status: {status} Service indisponible", "WARNING")
            else:
                debug_log(f"Status: {status} - Réponse inattendue", "WARNING")
            
            return None
            
        except requests.exceptions.Timeout:
            debug_log(f"TIMEOUT après {timeout}s : {page_url[:50]}...", "ERROR")
            return None
        except requests.exceptions.SSLError as e:
            debug_log(f"Erreur SSL : {str(e)[:50]} - Retry sans SSL...", "WARNING")
            try:
                response = requests.get(page_url, headers=HEADERS, timeout=timeout, verify=False)
                if response.status_code == 200:
                    debug_log(f"Retry SSL OK | Contenu: {len(response.text)} chars", "SUCCESS")
                    return response.text
                else:
                    debug_log(f"Retry SSL échoué : Status {response.status_code}", "ERROR")
            except Exception as e2:
                debug_log(f"Retry SSL exception : {str(e2)[:50]}", "ERROR")
            return None
        except requests.exceptions.ConnectionError as e:
            debug_log(f"Erreur connexion : {str(e)[:50]}", "ERROR")
            return None
        except Exception as e:
            debug_log(f"Exception inattendue : {type(e).__name__} - {str(e)[:50]}", "ERROR")
            return None
    
    def find_contact_links(soup, base_url):
        """Trouve les liens vers les pages de contact"""
        links = set()
        base_domain = urlparse(base_url).netloc
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag.get('href', '').lower()
            text = a_tag.get_text().lower().strip()
            
            if not href or href.startswith(('javascript:', '#', 'tel:', 'mailto:')):
                continue
            
            for keyword in CONTACT_KEYWORDS:
                if keyword in href or keyword in text:
                    full_url = urljoin(base_url, a_tag['href'])
                    if urlparse(full_url).netloc == base_domain:
                        links.add(full_url)
                    break
        
        return list(links)[:20]
    
    def get_page_type(url):
        """Identifie le type de page pour le log"""
        url_lower = url.lower()
        if any(kw in url_lower for kw in ['contact', 'nous-contacter', 'contactez']):
            return "Contact"
        elif any(kw in url_lower for kw in ['legal', 'mention', 'cgv', 'cgu', 'conditions', 'policies', 'terms']):
            return "Mentions Légales"
        elif any(kw in url_lower for kw in ['support', 'aide', 'faq', 'help']):
            return "Support"
        elif any(kw in url_lower for kw in ['about', 'propos', 'qui-sommes']):
            return "À propos"
        return "Page"
    
    def search_duckduckgo(query):
        """
        🦆 Recherche DuckDuckGo HTML (fallback ultime)
        Retourne les snippets des résultats
        """
        debug_log(f"🦆 Recherche DuckDuckGo : {query}", "INFO")
        
        try:
            search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
            
            search_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
                'Referer': 'https://duckduckgo.com/',
            }
            
            response = requests.get(search_url, headers=search_headers, timeout=10)
            debug_log(f"🦆 DuckDuckGo Status: {response.status_code} | Taille: {len(response.text)} chars", "HTTP")
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                snippets = []
                
                # Extraire les snippets des résultats
                for result in soup.find_all('a', class_='result__snippet'):
                    text = result.get_text()
                    if text:
                        snippets.append(text)
                
                # Aussi chercher dans les titres et URLs
                for result in soup.find_all('a', class_='result__a'):
                    text = result.get_text()
                    href = result.get('href', '')
                    if text:
                        snippets.append(text)
                    if href:
                        snippets.append(href)
                
                # Chercher aussi dans les résultats classiques
                for result in soup.find_all(class_='result__body'):
                    text = result.get_text()
                    if text:
                        snippets.append(text)
                
                result_text = ' '.join(snippets[:15])
                debug_log(f"🦆 DuckDuckGo: {len(snippets)} snippets extraits", "SUCCESS" if snippets else "WARNING")
                
                # Log des emails trouvés dans les résultats
                found_emails = re.findall(EMAIL_REGEX, result_text, re.IGNORECASE)
                if found_emails:
                    debug_log(f"🦆 Emails trouvés dans résultats DDG: {found_emails[:3]}", "SUCCESS")
                
                return result_text
            else:
                debug_log(f"🦆 DuckDuckGo échec: Status {response.status_code}", "ERROR")
            
        except Exception as e:
            debug_log(f"🦆 DuckDuckGo Exception: {type(e).__name__} - {str(e)[:50]}", "ERROR")
        
        return ""
    
    def search_bing(query):
        """
        🔍 Recherche Bing (fallback alternatif)
        """
        debug_log(f"🔍 Recherche Bing : {query[:50]}...", "INFO")
        
        try:
            search_url = f"https://www.bing.com/search?q={requests.utils.quote(query)}"
            
            bing_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'fr-FR,fr;q=0.9',
                'Referer': 'https://www.bing.com/',
            }
            
            response = requests.get(search_url, headers=bing_headers, timeout=10)
            debug_log(f"🔍 Bing Status: {response.status_code} | Taille: {len(response.text)} chars", "HTTP")
            
            if response.status_code == 200:
                # Log des emails trouvés
                found_emails = re.findall(EMAIL_REGEX, response.text, re.IGNORECASE)
                if found_emails:
                    debug_log(f"🔍 Emails trouvés dans Bing: {found_emails[:3]}", "SUCCESS")
                return response.text
            else:
                debug_log(f"🔍 Bing échec: Status {response.status_code}", "ERROR")
                
        except Exception as e:
            debug_log(f"🔍 Bing Exception: {type(e).__name__} - {str(e)[:50]}", "ERROR")
        return ""
    
    # ═══════════════════════════════════════════════════════════════
    # EXÉCUTION DU SCRAPING
    # ═══════════════════════════════════════════════════════════════
    
    all_emails = {}
    pages_visited = set()
    
    try:
        # 1. Nettoyer l'URL
        base_url = clean_url(url)
        if not base_url:
            debug_log("URL invalide après nettoyage", "ERROR")
            return {"email": None, "source": None, "all_emails": []}
        
        base_domain = get_base_domain(base_url)
        site_domain = get_domain_name(base_url)
        
        debug_log(f"Base URL: {base_url}", "INFO")
        debug_log(f"Domaine: {site_domain}", "INFO")
        
        # 2. Récupérer la page d'accueil
        debug_log("═══ ÉTAPE 1: Page d'accueil ═══", "INFO")
        homepage_content = get_page_content(base_url)
        if not homepage_content:
            debug_log("Accueil inaccessible, essai avec/sans www...", "WARNING")
            alt_url = base_url.replace('://www.', '://') if '://www.' in base_url else base_url.replace('://', '://www.')
            homepage_content = get_page_content(alt_url)
            if homepage_content:
                base_url = alt_url
                base_domain = get_base_domain(alt_url)
        
        if not homepage_content:
            debug_log("Site inaccessible même avec www/sans www", "ERROR")
            debug_log("Passage direct au FALLBACK recherche web...", "WARNING")
            homepage_content = ""
        else:
            pages_visited.add(base_url)
            soup = BeautifulSoup(homepage_content, 'html.parser')
            debug_log(f"Page d'accueil chargée: {len(homepage_content)} chars", "SUCCESS")
            
            # 3. Extraire mailto: de l'accueil
            debug_log("Recherche des mailto: sur l'accueil...", "INFO")
            mailto_emails = extract_mailto_emails(soup)
            if mailto_emails:
                debug_log(f"Mailto trouvés sur accueil: {mailto_emails}", "SUCCESS")
            else:
                debug_log("Aucun mailto sur l'accueil", "WARNING")
            
            for email in mailto_emails:
                score = score_email(email, site_domain) + 30
                if email not in all_emails or all_emails[email]["score"] < score:
                    all_emails[email] = {"score": score, "source": "Accueil (mailto)"}
            
            # 4. Extraire emails du texte
            debug_log("Recherche emails dans le texte de l'accueil...", "INFO")
            homepage_emails = extract_emails_from_text(homepage_content)
            if homepage_emails:
                debug_log(f"Emails trouvés dans texte accueil: {homepage_emails}", "SUCCESS")
            else:
                debug_log("Aucun email dans le texte de l'accueil", "WARNING")
            
            for email in homepage_emails:
                if email not in all_emails:
                    all_emails[email] = {"score": score_email(email, site_domain), "source": "Accueil"}
            
            # 5. Visiter les liens contact trouvés
            debug_log("═══ ÉTAPE 2: Recherche liens contact ═══", "INFO")
            contact_links = find_contact_links(soup, base_url)
            debug_log(f"{len(contact_links)} liens contact détectés: {contact_links[:5]}", "INFO")
            
            for link in contact_links:
                if link in pages_visited:
                    continue
                pages_visited.add(link)
                
                page_content = get_page_content(link)
                if page_content:
                    page_soup = BeautifulSoup(page_content, 'html.parser')
                    page_type = get_page_type(link)
                    
                    page_mailto = extract_mailto_emails(page_soup)
                    if page_mailto:
                        debug_log(f"Mailto trouvés sur {page_type}: {page_mailto}", "SUCCESS")
                    
                    for email in page_mailto:
                        score = score_email(email, site_domain) + 40
                        if email not in all_emails or all_emails[email]["score"] < score:
                            all_emails[email] = {"score": score, "source": f"{page_type} (mailto)"}
                    
                    page_emails = extract_emails_from_text(page_content)
                    if page_emails:
                        debug_log(f"Emails trouvés sur {page_type}: {page_emails}", "SUCCESS")
                    
                    for email in page_emails:
                        score = score_email(email, site_domain) + 20
                        if email not in all_emails or all_emails[email]["score"] < score:
                            all_emails[email] = {"score": score, "source": page_type}
            
            # Log état actuel
            if all_emails:
                debug_log(f"État après étape 2: {len(all_emails)} emails trouvés", "SUCCESS")
            else:
                debug_log("Aucun email trouvé après étapes 1-2", "WARNING")
        
        # ═══════════════════════════════════════════════════════════════
        # FALLBACK 1 : Chemins CMS standards
        # ═══════════════════════════════════════════════════════════════
        
        if not all_emails:
            debug_log(f"═══ FALLBACK 1: Test des {len(STANDARD_PATHS)} chemins CMS ═══", "INFO")
            
            for path in STANDARD_PATHS:
                test_url = base_domain + path
                if test_url in pages_visited:
                    continue
                pages_visited.add(test_url)
                
                page_content = get_page_content(test_url, timeout=4)
                if page_content:
                    page_soup = BeautifulSoup(page_content, 'html.parser')
                    page_type = get_page_type(test_url)
                    
                    page_mailto = extract_mailto_emails(page_soup)
                    if page_mailto:
                        debug_log(f"CMS {path} → Mailto: {page_mailto}", "SUCCESS")
                    
                    for email in page_mailto:
                        score = score_email(email, site_domain) + 40
                        all_emails[email] = {"score": score, "source": f"{page_type} (mailto)"}
                    
                    page_emails = extract_emails_from_text(page_content)
                    if page_emails:
                        debug_log(f"CMS {path} → Emails texte: {page_emails}", "SUCCESS")
                    
                    for email in page_emails:
                        score = score_email(email, site_domain) + 20
                        if email not in all_emails or all_emails[email]["score"] < score:
                            all_emails[email] = {"score": score, "source": page_type}
                    
                    if all_emails:
                        debug_log(f"Email trouvé via CMS path: {path}", "SUCCESS")
                        break
            
            if not all_emails:
                debug_log("Aucun email trouvé après FALLBACK 1 (CMS paths)", "WARNING")
        
        # ═══════════════════════════════════════════════════════════════
        # FALLBACK 2 : Recherche DuckDuckGo / Bing
        # ═══════════════════════════════════════════════════════════════
        
        if not all_emails:
            debug_log(f"═══ FALLBACK 2: Recherche Web pour {site_domain} ═══", "INFO")
            
            # Extraire le nom de marque du domaine (archiduchesse.com -> archiduchesse)
            brand_name = site_domain.split('.')[0].replace('www', '').replace('-', ' ')
            debug_log(f"Nom de marque extrait: '{brand_name}'", "INFO")
            
            # Construire plusieurs requêtes de recherche
            search_queries = [
                f'"{site_domain}" email contact',
                f'"{brand_name}" email contact service client',
                f'"{site_domain}" mentions légales email',
                f'site:{site_domain} contact email "@"',
                f'"{brand_name}" contact support email france',
            ]
            
            for query in search_queries:
                debug_log(f"Requête: {query}", "INFO")
                
                # Essayer DuckDuckGo
                search_results = search_duckduckgo(query)
                
                if search_results:
                    search_emails = extract_emails_from_text(search_results)
                    debug_log(f"Emails extraits de DDG: {search_emails[:5] if search_emails else 'Aucun'}", "INFO")
                    
                    for email in search_emails:
                        # 🕵️ VALIDATION STRICTE DU DOMAINE
                        is_valid, reason = is_email_domain_valid(email, site_domain, brand_name)
                        
                        if is_valid:
                            debug_log(f"✅ Email VALIDÉ: {email} (raison: {reason})", "SUCCESS")
                            score = score_email(email, site_domain) + 25
                            if email not in all_emails or all_emails[email]["score"] < score:
                                all_emails[email] = {"score": score, "source": "Recherche Web"}
                        # else: déjà loggé par is_email_domain_valid
                
                # Si on a trouvé des emails, on arrête
                if all_emails:
                    debug_log("Email trouvé via recherche DuckDuckGo!", "SUCCESS")
                    break
                
                # Essayer Bing si DuckDuckGo n'a rien donné
                if not all_emails:
                    bing_results = search_bing(query)
                    if bing_results:
                        bing_emails = extract_emails_from_text(bing_results)
                        debug_log(f"Emails extraits de Bing: {bing_emails[:5] if bing_emails else 'Aucun'}", "INFO")
                        
                        for email in bing_emails:
                            # 🕵️ VALIDATION STRICTE DU DOMAINE
                            is_valid, reason = is_email_domain_valid(email, site_domain, brand_name)
                            
                            if is_valid:
                                debug_log(f"✅ Bing - Email VALIDÉ: {email} (raison: {reason})", "SUCCESS")
                                score = score_email(email, site_domain) + 20
                                if email not in all_emails or all_emails[email]["score"] < score:
                                    all_emails[email] = {"score": score, "source": "Recherche Bing"}
                            # else: déjà loggé par is_email_domain_valid
                
                if all_emails:
                    break
        
        # ═══════════════════════════════════════════════════════════════
        # RÉSULTAT FINAL
        # ═══════════════════════════════════════════════════════════════
        
        debug_log("═══════════════════════════════════════════════════", "INFO")
        debug_log("RÉSULTAT FINAL", "INFO")
        debug_log("═══════════════════════════════════════════════════", "INFO")
        
        if all_emails:
            sorted_emails = sorted(all_emails.items(), key=lambda x: x[1]["score"], reverse=True)
            best_email = sorted_emails[0][0]
            best_source = sorted_emails[0][1]["source"]
            best_score = sorted_emails[0][1]["score"]
            
            debug_log(f"✅ SUCCÈS: {best_email}", "SUCCESS")
            debug_log(f"   Source: {best_source}", "SUCCESS")
            debug_log(f"   Score: {best_score}", "SUCCESS")
            debug_log(f"   Tous les emails: {[e[0] for e in sorted_emails[:5]]}", "INFO")
            debug_log(f"   Pages visitées: {len(pages_visited)}", "INFO")
            
            return {
                "email": best_email,
                "source": best_source,
                "all_emails": [e[0] for e in sorted_emails[:5]]
            }
        
        debug_log(f"❌ ÉCHEC: Aucun email trouvé pour {site_domain}", "ERROR")
        debug_log(f"   Pages visitées: {len(pages_visited)}", "INFO")
        debug_log("   Suggestions: Vérifier si le site est accessible, si les emails sont en JS", "INFO")
        return {"email": None, "source": "Aucun email trouvé", "all_emails": []}
        
    except Exception as e:
        debug_log(f"EXCEPTION FATALE: {type(e).__name__} - {str(e)}", "ERROR")
        import traceback
        debug_log(f"Traceback: {traceback.format_exc()[:200]}", "ERROR")
        return {"email": None, "source": f"Erreur: {str(e)[:50]}", "all_emails": []}

# ========================================
# ⚖️ AGENT AVOCAT - Envoi Mise en Demeure
# ========================================

def send_legal_notice(dossier, user):
    """
    ⚖️ AGENT AVOCAT V2 - Envoie une mise en demeure légale au marchand
    
    Améliorations V2 :
    - Format HTML professionnel
    - Header From avec nom (anti-spam)
    - Nettoyage email destinataire
    - Correction double €
    
    Args:
        dossier: Instance Litigation avec merchant_email rempli
        user: Instance User avec refresh_token
    
    Returns:
        dict: {"success": bool, "message": str, "message_id": str|None}
    """
    
    DEBUG_LOGS.append(f"⚖️ Agent Avocat V2: Préparation mise en demeure pour {dossier.company}")
    
    # ═══════════════════════════════════════════════════════════════
    # FONCTIONS UTILITAIRES
    # ═══════════════════════════════════════════════════════════════
    
    def clean_email(email):
        """Nettoie une adresse email (enlève chevrons, espaces, etc.)"""
        if not email:
            return None
        # Enlever les espaces
        email = email.strip()
        # Extraire l'email si format "Nom <email@domain.com>"
        if '<' in email and '>' in email:
            import re
            match = re.search(r'<([^>]+)>', email)
            if match:
                email = match.group(1)
        # Enlever les chevrons orphelins
        email = email.replace('<', '').replace('>', '').strip()
        return email if '@' in email else None
    
    def format_amount(amount_value):
        """Formate le montant sans double €"""
        if amount_value is None:
            return "N/A"
        # Convertir en string
        amount_str = str(amount_value)
        # Enlever les € existants
        amount_str = amount_str.replace('€', '').replace('EUR', '').strip()
        # Si c'est un nombre, formater proprement
        try:
            amount_num = float(amount_str.replace(',', '.'))
            return f"{amount_num:.2f}"
        except:
            return amount_str
    
    # ═══════════════════════════════════════════════════════════════
    # VÉRIFICATIONS
    # ═══════════════════════════════════════════════════════════════
    
    # Nettoyer l'email destinataire
    merchant_email_clean = clean_email(dossier.merchant_email)
    
    if not merchant_email_clean:
        DEBUG_LOGS.append(f"⚖️ ❌ Email marchand invalide: {dossier.merchant_email}")
        return {"success": False, "message": "Email marchand invalide", "message_id": None}
    
    if not user or not user.refresh_token:
        DEBUG_LOGS.append("⚖️ ❌ Utilisateur non authentifié")
        return {"success": False, "message": "Utilisateur non authentifié", "message_id": None}
    
    # ═══════════════════════════════════════════════════════════════
    # PRÉPARATION DES DONNÉES
    # ═══════════════════════════════════════════════════════════════
    
    company = dossier.company or "Vendeur"
    order_ref = dossier.order_id or "N/A"
    amount = format_amount(dossier.amount_float or dossier.amount)
    problem_type = dossier.problem_type or "autre"
    description = dossier.description or ""
    user_name = user.name or user.email.split('@')[0].title()
    user_email = user.email
    
    # Date du jour et deadline (8 jours)
    from datetime import timedelta
    today = datetime.now()
    today_str = today.strftime("%d/%m/%Y")
    deadline = (today + timedelta(days=8)).strftime("%d/%m/%Y")
    
    # ═══════════════════════════════════════════════════════════════
    # TEMPLATES JURIDIQUES PAR TYPE DE PROBLÈME
    # ═══════════════════════════════════════════════════════════════
    
    LEGAL_TEMPLATES = {
        "non_recu": {
            "titre": "MISE EN DEMEURE",
            "objet": f"MISE EN DEMEURE - Commande {order_ref} non reçue",
            "loi": "Article L.216-6 du Code de la consommation",
            "article_detail": "L.216-6",
            "message": f"""La date de livraison contractuelle étant dépassée, et n'ayant toujours pas reçu ma commande malgré mes relances, je vous mets formellement en demeure de procéder :
            <ul>
                <li>Soit à la <strong>LIVRAISON EFFECTIVE</strong> de ma commande sous 8 jours,</li>
                <li>Soit au <strong>REMBOURSEMENT INTÉGRAL</strong> de la somme de <strong>{amount} €</strong>.</li>
            </ul>
            <p>Conformément à l'article L.216-6 du Code de la consommation, à défaut de livraison dans ce délai, le contrat pourra être considéré comme résolu et je serai en droit de demander le remboursement intégral des sommes versées.</p>"""
        },
        
        "defectueux": {
            "titre": "RÉCLAMATION - GARANTIE LÉGALE",
            "objet": f"RÉCLAMATION - Commande {order_ref} - Produit défectueux",
            "loi": "Articles L.217-3 et suivants du Code de la consommation",
            "article_detail": "L.217-3 à L.217-8",
            "message": f"""Le produit reçu présente un <strong>défaut de conformité</strong> le rendant impropre à l'usage auquel il est destiné.
            <p>En vertu de la <strong>Garantie Légale de Conformité</strong> (Articles L.217-3 et suivants), je vous demande de procéder à votre choix :</p>
            <ul>
                <li>À la <strong>RÉPARATION</strong> du produit,</li>
                <li>Ou à son <strong>REMPLACEMENT</strong> par un produit conforme.</li>
            </ul>
            <p>Si ces solutions s'avèrent impossibles ou disproportionnées, je demande le <strong>REMBOURSEMENT INTÉGRAL</strong> conformément à l'article L.217-8.</p>"""
        },
        
        "non_conforme": {
            "titre": "NON-CONFORMITÉ",
            "objet": f"NON-CONFORMITÉ - Commande {order_ref}",
            "loi": "Article L.217-4 du Code de la consommation",
            "article_detail": "L.217-4",
            "message": f"""Le produit reçu <strong>ne correspond pas aux caractéristiques présentées</strong> lors de la vente, constituant ainsi un défaut de conformité au sens de l'article L.217-4 du Code de la consommation.
            <p>Je vous mets en demeure de remédier à cette non-conformité sous 8 jours par :</p>
            <ul>
                <li>L'échange contre un produit <strong>CONFORME</strong> à la description,</li>
                <li>Ou le <strong>REMBOURSEMENT INTÉGRAL</strong> de <strong>{amount} €</strong>.</li>
            </ul>
            <p>À défaut, je me réserve le droit de saisir les juridictions compétentes et la DGCCRF.</p>"""
        },
        
        "retour_refuse": {
            "titre": "MISE EN DEMEURE - RÉTRACTATION",
            "objet": f"MISE EN DEMEURE - Commande {order_ref} - Refus de retour illégal",
            "loi": "Article L.221-18 du Code de la consommation",
            "article_detail": "L.221-18",
            "message": f"""Je vous rappelle que, conformément à l'<strong>article L.221-18 du Code de la consommation</strong>, je dispose d'un délai de <strong>14 jours</strong> pour exercer mon droit de rétractation, sans avoir à justifier de motif ni à payer de pénalités.
            <p>Votre refus de procéder au retour et au remboursement est donc <strong style="color:#b91c1c;">ILLÉGAL</strong>.</p>
            <p>Je vous mets en demeure d'accepter ce retour et de procéder au remboursement de <strong>{amount} €</strong> dans un délai de 8 jours, faute de quoi je saisirai la DGCCRF et les tribunaux compétents.</p>"""
        },
        
        "contrefacon": {
            "titre": "SIGNALEMENT - CONTREFAÇON",
            "objet": f"SIGNALEMENT URGENT - Commande {order_ref} - Suspicion de contrefaçon",
            "loi": "Code de la Propriété Intellectuelle (L.716-1)",
            "article_detail": "L.716-1 CPI",
            "message": f"""Le produit reçu présente toutes les caractéristiques d'une <strong style="color:#b91c1c;">CONTREFAÇON</strong> (qualité inférieure, absence de marquages officiels, emballage non conforme).
            <p>La vente de produits contrefaits constitue :</p>
            <ul>
                <li>Un <strong>défaut de conformité</strong> (Code de la consommation),</li>
                <li>Un <strong>délit pénal</strong> (Article L.716-1 du Code de la Propriété Intellectuelle).</li>
            </ul>
            <p>Je vous mets en demeure de procéder au <strong>REMBOURSEMENT INTÉGRAL</strong> de <strong>{amount} €</strong> sous 8 jours.</p>
            <p>À défaut, je procéderai au signalement auprès de la <strong>DGCCRF</strong> et des services de douanes, et me réserve le droit de porter plainte.</p>"""
        },
        
        "retard": {
            "titre": "RETARD DE LIVRAISON",
            "objet": f"RETARD DE LIVRAISON - Commande {order_ref}",
            "loi": "Article L.216-1 du Code de la consommation",
            "article_detail": "L.216-1",
            "message": f"""Les délais de livraison annoncés lors de ma commande <strong>ne sont pas respectés</strong>, en violation de l'article L.216-1 du Code de la consommation.
            <p>Je vous mets en demeure de :</p>
            <ul>
                <li>Procéder à la <strong>LIVRAISON IMMÉDIATE</strong> de ma commande,</li>
                <li>Ou, si celle-ci n'est plus possible, de me <strong>REMBOURSER INTÉGRALEMENT</strong>.</li>
            </ul>
            <p>Conformément à l'article L.216-6, à défaut d'exécution dans un délai de 8 jours, le contrat sera résolu de plein droit.</p>"""
        },
        
        "annulation_refusee": {
            "titre": "LITIGE - ANNULATION",
            "objet": f"LITIGE - Commande {order_ref} - Refus d'annulation illégal",
            "loi": "Articles L.221-18 et L.121-20 du Code de la consommation",
            "article_detail": "L.221-18 / L.121-20",
            "message": f"""J'ai demandé l'annulation de ma commande conformément à mes droits de consommateur, demande que vous avez refusée de manière <strong style="color:#b91c1c;">illégale</strong>.
            <p>Conformément aux articles L.221-18 et L.121-20 du Code de la consommation applicables à la vente à distance, je dispose du droit d'annuler ma commande.</p>
            <p>Je vous mets en demeure d'accepter cette annulation et de procéder au remboursement de <strong>{amount} €</strong> sous 8 jours.</p>"""
        },
        
        "autre": {
            "titre": "RÉCLAMATION FORMELLE",
            "objet": f"RÉCLAMATION FORMELLE - Commande {order_ref}",
            "loi": "Article 1103 du Code Civil",
            "article_detail": "1103 C.Civ",
            "message": f"""Je vous contacte concernant un <strong>problème rencontré avec ma commande</strong>, tel que décrit ci-dessous.
            <p>Conformément à l'article 1103 du Code Civil, les contrats légalement formés tiennent lieu de loi à ceux qui les ont faits.</p>
            <p>Je vous mets en demeure de résoudre ce litige de manière amiable sous 8 jours, faute de quoi je me réserve le droit d'engager toute procédure judiciaire nécessaire.</p>"""
        }
    }
    
    # Sélectionner le template
    template = LEGAL_TEMPLATES.get(problem_type, LEGAL_TEMPLATES["autre"])
    
    # ═══════════════════════════════════════════════════════════════
    # CONSTRUCTION DU MESSAGE HTML PROFESSIONNEL
    # ═══════════════════════════════════════════════════════════════
    
    description_html = ""
    if description:
        description_html = f"""
        <div style="background:#f8fafc; border-left:4px solid #64748b; padding:15px; margin:20px 0;">
            <p style="margin:0; color:#475569; font-style:italic;"><strong>Description du problème :</strong><br>{description}</p>
        </div>
        """
    
    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; font-family: Arial, Helvetica, sans-serif; background-color:#f3f4f6;">
    <div style="max-width:650px; margin:0 auto; padding:20px;">
        
        <!-- EN-TÊTE MISE EN DEMEURE -->
        <div style="background:linear-gradient(135deg, #1e293b 0%, #334155 100%); color:white; padding:25px; text-align:center; border-radius:10px 10px 0 0;">
            <h1 style="margin:0; font-size:28px; letter-spacing:2px; color:#fbbf24;">⚖️ {template['titre']}</h1>
            <p style="margin:10px 0 0 0; font-size:14px; color:#94a3b8;">Document à valeur juridique - Art. 1344 du Code Civil</p>
        </div>
        
        <!-- CORPS DU MESSAGE -->
        <div style="background:white; padding:30px; border-left:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">
            
            <!-- Date et destinataire -->
            <div style="text-align:right; color:#64748b; font-size:14px; margin-bottom:20px;">
                <p style="margin:0;">Paris, le {today_str}</p>
            </div>
            
            <div style="margin-bottom:25px;">
                <p style="margin:0; color:#64748b; font-size:14px;">
                    <strong>Destinataire :</strong> {company.upper()}<br>
                    <strong>Email :</strong> {merchant_email_clean}
                </p>
            </div>
            
            <!-- Objet -->
            <div style="background:#fef3c7; border-left:4px solid #f59e0b; padding:12px 15px; margin-bottom:25px;">
                <p style="margin:0; font-weight:bold; color:#92400e;">
                    📋 Objet : {template['objet']}
                </p>
            </div>
            
            <!-- Salutation -->
            <p style="color:#1e293b; line-height:1.6;">Madame, Monsieur,</p>
            
            <!-- Contenu juridique -->
            <div style="color:#1e293b; line-height:1.8; text-align:justify;">
                {template['message']}
            </div>
            
            <!-- Description utilisateur -->
            {description_html}
            
            <!-- Avertissement légal -->
            <div style="background:#fef2f2; border:1px solid #fecaca; border-radius:8px; padding:20px; margin:25px 0;">
                <p style="margin:0 0 10px 0; color:#991b1b; font-weight:bold;">⚠️ Cette mise en demeure vaut interpellation au sens de l'article 1344 du Code Civil.</p>
                <p style="margin:0; color:#7f1d1d; font-size:14px;">
                    Sans réponse satisfaisante de votre part avant le <strong>{deadline}</strong>, je me réserve le droit de :
                </p>
                <ul style="color:#7f1d1d; font-size:14px; margin:10px 0 0 0;">
                    <li>Saisir le <strong>Médiateur de la Consommation</strong></li>
                    <li>Signaler cette pratique à la <strong>DGCCRF</strong></li>
                    <li>Engager une <strong>procédure judiciaire</strong> devant le tribunal compétent</li>
                </ul>
            </div>
            
            <!-- Formule de politesse -->
            <p style="color:#1e293b; line-height:1.6; margin-top:25px;">
                Dans l'attente d'une réponse rapide, je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.
            </p>
            
            <!-- Signature -->
            <div style="margin-top:30px; padding-top:20px; border-top:1px solid #e2e8f0;">
                <p style="margin:0; font-weight:bold; color:#1e293b; font-size:16px;">{user_name}</p>
                <p style="margin:5px 0 0 0; color:#64748b; font-size:14px;">Email : {user_email}</p>
            </div>
        </div>
        
        <!-- RÉCAPITULATIF -->
        <div style="background:#f1f5f9; padding:20px; border-left:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">
            <table style="width:100%; font-size:14px; color:#475569;">
                <tr>
                    <td style="padding:5px 0;"><strong>📋 N° Commande :</strong></td>
                    <td style="padding:5px 0; text-align:right;">{order_ref}</td>
                </tr>
                <tr>
                    <td style="padding:5px 0;"><strong>💰 Montant :</strong></td>
                    <td style="padding:5px 0; text-align:right; font-weight:bold; color:#059669;">{amount} €</td>
                </tr>
                <tr>
                    <td style="padding:5px 0;"><strong>⚖️ Base légale :</strong></td>
                    <td style="padding:5px 0; text-align:right;">{template['article_detail']}</td>
                </tr>
                <tr>
                    <td style="padding:5px 0;"><strong>📅 Délai de réponse :</strong></td>
                    <td style="padding:5px 0; text-align:right; color:#dc2626; font-weight:bold;">{deadline}</td>
                </tr>
            </table>
        </div>
        
        <!-- PIED DE PAGE -->
        <div style="background:#1e293b; color:#94a3b8; padding:20px; text-align:center; border-radius:0 0 10px 10px; font-size:12px;">
            <p style="margin:0 0 10px 0;">
                <strong style="color:#fbbf24;">Justicio.fr</strong> - Protection des droits des consommateurs
            </p>
            <p style="margin:0; font-size:11px;">
                Ce document constitue une mise en demeure au sens juridique du terme.<br>
                Il a valeur probante en cas de procédure judiciaire ultérieure.
            </p>
        </div>
        
    </div>
</body>
</html>
"""

    # ═══════════════════════════════════════════════════════════════
    # ENVOI VIA GMAIL API
    # ═══════════════════════════════════════════════════════════════
    
    try:
        # Rafraîchir les credentials
        creds = get_refreshed_credentials(user.refresh_token)
        service = build('gmail', 'v1', credentials=creds)
        
        # Construire le message MIME en HTML
        message = MIMEText(html_body, 'html', 'utf-8')
        
        # Header TO : email propre
        message['to'] = merchant_email_clean
        
        # Header CC : copie à l'utilisateur
        message['cc'] = user_email
        
        # Header FROM : format professionnel (anti-spam)
        from_name = f"{user_name} via Justicio"
        message['from'] = f'"{from_name}" <{user_email}>'
        
        # Header SUBJECT
        message['subject'] = f"⚖️ {template['objet']}"
        
        # Headers additionnels pour le suivi
        message['X-Justicio-Case-ID'] = str(dossier.id)
        message['X-Justicio-Type'] = 'legal-notice'
        message['X-Priority'] = '1'  # Haute priorité
        message['Importance'] = 'high'
        
        # Encoder en base64 URL-safe
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        
        # Log avant envoi
        DEBUG_LOGS.append(f"⚖️ Envoi HTML à {merchant_email_clean} (CC: {user_email})")
        DEBUG_LOGS.append(f"⚖️ From: \"{from_name}\" <{user_email}>")
        
        # Envoyer
        result = service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        # Vérifier le succès
        message_id = result.get('id')
        
        if message_id:
            DEBUG_LOGS.append(f"⚖️ ✅ Mise en demeure envoyée! Message ID: {message_id}")
            
            # Mettre à jour le dossier
            dossier.legal_notice_sent = True
            dossier.legal_notice_date = datetime.now()
            dossier.legal_notice_message_id = message_id
            dossier.status = "En cours juridique"
            db.session.commit()
            
            return {
                "success": True,
                "message": f"Mise en demeure envoyée à {merchant_email_clean}",
                "message_id": message_id
            }
        else:
            DEBUG_LOGS.append("⚖️ ❌ Envoi échoué - Pas de message_id retourné")
            return {"success": False, "message": "Envoi échoué - Pas de confirmation", "message_id": None}
            
    except Exception as e:
        error_msg = str(e)
        DEBUG_LOGS.append(f"⚖️ ❌ Erreur envoi: {error_msg[:150]}")
        
        # Vérifier si c'est un problème de permissions
        if "insufficient" in error_msg.lower() or "scope" in error_msg.lower():
            return {
                "success": False,
                "message": "Permissions insuffisantes. Reconnectez-vous pour autoriser l'envoi d'emails.",
                "message_id": None
            }
        
        return {"success": False, "message": f"Erreur: {error_msg[:80]}", "message_id": None}


def extract_email_content(message_data):
    """Extrait le contenu textuel d'un email Gmail"""
    payload = message_data.get('payload', {})
    
    def get_text(part):
        text = ""
        if 'parts' in part:
            for sub_part in part['parts']:
                text += get_text(sub_part)
        elif part.get('mimeType') in ['text/plain', 'text/html']:
            data = part['body'].get('data', '')
            if data:
                decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                text += decoded
        return text
    
    body_raw = get_text(payload)
    if body_raw:
        clean_body = re.sub('<[^<]+?>', ' ', body_raw)
        clean_body = re.sub(r'\s+', ' ', clean_body).strip()
        return clean_body
    
    return message_data.get('snippet', '')

def analyze_litigation(text, subject, sender):
    """Analyse IA pour détecter un litige - VERSION LEGACY"""
    return analyze_litigation_v2(text, subject, sender, "", None, None)

def analyze_litigation_v2(text, subject, sender, to_field, detected_company, extracted_amount):
    """
    🕵️ AGENT 1 : LE CHASSEUR - Analyse IA des litiges
    But : Détecter les PROBLÈMES NON RÉSOLUS uniquement
    Retourne : [MONTANT, LOI, MARQUE, PREUVE]
    """
    if not OPENAI_API_KEY:
        return ["REJET", "Pas d'API", "Inconnu", ""]
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Préparer les infos contextuelles
    company_hint = ""
    if detected_company:
        company_hint = f"\n⚠️ INDICE : L'email est envoyé À {detected_company.upper()} (champ TO: {to_field})"
    
    amount_hint = ""
    if extracted_amount:
        amount_hint = f"\n⚠️ INDICE : Montant trouvé dans le texte : {extracted_amount}"
    
    try:
        prompt = f"""🕵️ Tu es le CHASSEUR - Expert Juridique spécialisé dans les litiges consommateurs NON RÉSOLUS.

⚠️ MISSION CRITIQUE : Tu cherches UNIQUEMENT les VRAIS problèmes transactionnels QUI N'ONT PAS ENCORE ÉTÉ RÉGLÉS.

INPUT :
- EXPÉDITEUR (FROM) : {sender}
- DESTINATAIRE (TO) : {to_field}
- SUJET : {subject}
- CONTENU : {text[:1800]}
{company_hint}
{amount_hint}

═══════════════════════════════════════════════════════════════
🚨 RÈGLE PRIORITAIRE N°0 : CLASSIFICATION TRANSACTION vs MARKETING
═══════════════════════════════════════════════════════════════

AVANT TOUTE AUTRE ANALYSE, détermine si cet email est :

📢 MARKETING (à REJETER IMMÉDIATEMENT) :
- Offres promotionnelles ("Profitez de -50%", "Offre spéciale")
- "Vous avez gagné", "Félicitations", "Crédit offert", "Cadeau"
- Newsletter, actualités, nouveautés
- "Le PDG vous offre", "Réduction exclusive"
- Langage promotionnel excessif, emojis commerciaux
- Temu, Shein, Wish et autres sites de promo agressifs
- "Cliquez ici pour réclamer", "Dernière chance"
- Emails de bienvenue, programmes de fidélité

Si c'est du MARKETING → Réponds IMMÉDIATEMENT :
"REJET | MARKETING | REJET | Email publicitaire/promotionnel"

═══════════════════════════════════════════════════════════════
🚨 RÈGLE PRIORITAIRE N°0.5 : REJETER LES FACTURES NORMALES
═══════════════════════════════════════════════════════════════

⚠️ UNE FACTURE N'EST PAS UN LITIGE ! Rejette immédiatement si c'est :

📄 FACTURE/NOTIFICATION DE PAIEMENT (à REJETER) :
- "Votre facture est disponible", "Facture N°..."
- "Prélèvement effectué", "Paiement accepté", "Paiement réussi"
- "Renouvellement automatique", "Abonnement renouvelé"
- "Confirmation de paiement", "Reçu de paiement"
- "Échéance prélevée", "Montant débité"
- Factures d'abonnement : IONOS, OVH, Netflix, Spotify, EDF, Free, Orange, SFR
- Notifications de prélèvement SEPA
- "Merci pour votre paiement", "Paiement bien reçu"

Si c'est une simple facture/notification de paiement SANS PROBLÈME mentionné :
"REJET | FACTURE | REJET | Notification de facturation normale"

═══════════════════════════════════════════════════════════════
🚨 RÈGLE PRIORITAIRE N°0.6 : EXIGER UN DÉCLENCHEUR DE LITIGE
═══════════════════════════════════════════════════════════════

⚠️ Un litige DOIT contenir au moins UN déclencheur. Sans déclencheur = PAS DE LITIGE.

🔥 DÉCLENCHEURS DE LITIGE (au moins UN requis) :
- RETARD : "retard", "en retard", "pas reçu", "jamais reçu", "non livré", "toujours pas"
- ANNULATION : "annulé", "annulation", "vol annulé", "train annulé", "commande annulée"
- PROBLÈME : "problème", "défectueux", "cassé", "abîmé", "endommagé", "ne fonctionne pas"
- REMBOURSEMENT : "remboursement", "rembourser", "je demande le remboursement"
- RETOUR : "retour", "retourner", "renvoyer", "colis retourné"
- AVOIR : "avoir", "geste commercial", "dédommagement", "compensation"
- RÉCLAMATION : "réclamation", "litige", "plainte", "contestation"
- ERREUR : "erreur", "facturé à tort", "double facturation", "montant incorrect"
- PERTE : "perdu", "égaré", "disparu", "volé"

Si AUCUN déclencheur n'est présent → L'argent n'est PAS dû au client :
"REJET | HORS SUJET | REJET | Aucun problème ou litige détecté"

═══════════════════════════════════════════════════════════════
🚨 RÈGLE PRIORITAIRE N°1 : DÉTECTER LES CAS DÉJÀ RÉSOLUS
═══════════════════════════════════════════════════════════════

Si l'email contient UN SEUL de ces indices, réponds IMMÉDIATEMENT :
"REJET | DÉJÀ PAYÉ | [MARQUE] | Email de confirmation de paiement"

MOTS-CLÉS DE RÉSOLUTION (= REJET DÉJÀ PAYÉ) :
- "virement effectué", "virement réalisé", "virement envoyé"
- "remboursement effectué", "remboursement validé", "remboursement confirmé"  
- "crédité sur votre compte", "créditée sur votre compte"
- "nous avons le plaisir de vous informer que votre remboursement"
- "votre compte a été crédité", "montant remboursé"
- "nous avons bien procédé au remboursement"
- "confirmation de remboursement", "avis de virement"
- "problème résolu", "dossier clôturé", "régularisation effectuée"

═══════════════════════════════════════════════════════════════
🚨 RÈGLE PRIORITAIRE N°2 : DÉTECTER LES REFUS DU SERVICE CLIENT
═══════════════════════════════════════════════════════════════

Si l'email est une RÉPONSE NÉGATIVE d'une entreprise, réponds :
"REJET | REFUS | [MARQUE] | [Citation du refus]"

MOTS-CLÉS DE REFUS (= REJET REFUS) :
- "malheureusement", "nous regrettons", "nous sommes au regret"
- "ne pouvons pas", "ne pouvons accéder", "impossible de"
- "votre demande ne peut être", "ne peut aboutir"
- "refusons", "refus de", "rejet de votre demande"
- "pas en mesure de", "dans l'impossibilité"
- "ne sera pas possible", "ne pouvons donner suite"
- "conditions non remplies", "hors délai", "hors garantie"

⚠️ Un refus N'EST PAS un litige gagnable - c'est une réponse définitive !

═══════════════════════════════════════════════════════════════
RÈGLES D'EXTRACTION (si PAS de marketing/résolution/refus)
═══════════════════════════════════════════════════════════════

1. MONTANT (Le nerf de la guerre) :
   - Cherche un montant EXPLICITE EN EUROS (ex: "42.99€", "120 EUR", "50 euros", "40€")
   - ⚠️ INTERDICTION D'ESTIMER. Si aucun chiffre visible : Écris "À déterminer"
   - ⚠️ INTERDICTION DE RENVOYER DES POURCENTAGES
   - Le montant peut être collé au symbole € (ex: "40€" = 40 euros)
   - EXCEPTION VOL ANNULÉ/RETARDÉ : Si compagnie aérienne ET (annulation OR retard > 3h) → "250€"
   - EXCEPTION TRAIN RETARDÉ : Si SNCF/Eurostar/Ouigo ET retard mentionné → "À déterminer"

2. MARQUE (PRIORITÉ AU DESTINATAIRE) :
   - RÈGLE N°1 : Si le champ TO contient @zalando.fr → c'est ZALANDO
   - RÈGLE N°2 : Si le champ TO contient @sncf.fr → c'est SNCF
   - RÈGLE N°3 : Si le champ TO contient @amazon.fr → c'est AMAZON
   - RÈGLE N°4 : Sinon, regarde le sujet/corps pour identifier l'entreprise

3. PREUVE (NOUVELLE RÈGLE IMPORTANTE) :
   - Extrais la PHRASE EXACTE du texte qui mentionne le montant OU le numéro de commande
   - Cette phrase sera affichée au client comme justification
   - Exemples : "Commande #12345 de 50€", "Ma commande de 89.99€ n'est jamais arrivée"
   - Si pas de phrase avec montant, cite la phrase décrivant le problème

4. AUTRES CRITÈRES DE REJET :
   - "REJET | SÉCURITÉ | REJET | Email de sécurité" si mot de passe/connexion
   - "REJET | HORS SUJET | REJET | Aucun litige détecté" si pas de problème

5. LOI APPLICABLE :
   - Vol aérien : "le Règlement (CE) n° 261/2004"
   - Train : "le Règlement (UE) 2021/782"
   - E-commerce : "la Directive UE 2011/83"
   - Défaut produit : "l'Article L217-4 du Code de la consommation"
   - Voyage/Hôtel : "la Directive UE 2015/2302"

═══════════════════════════════════════════════════════════════
FORMAT DE RÉPONSE (4 éléments séparés par |)
═══════════════════════════════════════════════════════════════

MONTANT | LOI | MARQUE | PREUVE

Exemples VALIDES (litiges à traiter - DÉCLENCHEUR PRÉSENT) :
- "42.99€ | la Directive UE 2011/83 | AMAZON | Commande #123456 de 42.99€ jamais reçue"
- "50€ | la Directive UE 2011/83 | ZALANDO | Je demande le remboursement de 50€ pour cet article défectueux"
- "250€ | le Règlement (CE) n° 261/2004 | AIR FRANCE | Mon vol AF1234 a été annulé sans préavis"
- "À déterminer | le Règlement (UE) 2021/782 | SNCF | Mon train a eu 2h de retard"

Exemples REJET :
- "REJET | MARKETING | REJET | Email publicitaire/promotionnel"
- "REJET | FACTURE | REJET | Notification de facturation normale"
- "REJET | FACTURE | IONOS | Simple facture d'abonnement sans problème"
- "REJET | HORS SUJET | REJET | Aucun problème ou litige détecté"
- "REJET | DÉJÀ PAYÉ | AMAZON | Votre remboursement de 42.99€ a été effectué"
- "REJET | REFUS | AIR FRANCE | Malheureusement, nous ne pouvons accéder à votre demande"
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200
        )
        
        result = response.choices[0].message.content.strip()
        parts = [p.strip() for p in result.split("|")]
        
        # S'assurer qu'on a toujours 4 éléments
        while len(parts) < 4:
            parts.append("")
        
        return parts[:4]
    
    except Exception as e:
        DEBUG_LOGS.append(f"Erreur IA: {str(e)}")
        return ["REJET", "Erreur IA", "Inconnu", ""]


# ════════════════════════════════════════════════════════════════
# 🚀 ANALYSE IA PERMISSIVE - MODE VOYAGE (INFAILLIBLE)
# ════════════════════════════════════════════════════════════════

def analyze_litigation_strict(text, subject, sender, to_field="", scan_type="ecommerce"):
    """
    🎯 ANALYSE IA STRICTE AVEC DOUBLE VÉRIFICATION
    
    Cette fonction garantit une séparation TOTALE entre :
    - scan_type="travel" → UNIQUEMENT transports (train/avion/VTC)
    - scan_type="ecommerce" → UNIQUEMENT produits physiques (colis/commandes)
    
    Retourne : {"is_valid": bool, "litige": bool, "company": str, "amount": str, "law": str, "proof": str, "category": str}
    """
    if not OPENAI_API_KEY:
        return {"is_valid": False, "litige": False, "reason": "Pas d'API"}
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # ════════════════════════════════════════════════════════════════
    # PROMPTS STRICTEMENT SÉPARÉS SELON LE TYPE DE SCAN
    # ════════════════════════════════════════════════════════════════
    
    if scan_type == "travel":
        system_prompt = """Tu es un AVOCAT EXPERT en Droit des Transports de Passagers (Règlement UE 261/2004 pour l'aérien, Règlement UE 2021/782 pour le ferroviaire).

🚨 RÈGLE ABSOLUE DE FILTRAGE 🚨
Tu ne traites QUE les problèmes de PASSAGERS :
- Retards/annulations de VOLS
- Retards/annulations de TRAINS
- Surbooking
- Bagages perdus/retardés
- Problèmes VTC (Uber, Bolt)

❌ REJETTE IMMÉDIATEMENT si l'email concerne :
- Un COLIS ou une COMMANDE de produit
- Un vêtement, chaussure, accessoire
- Amazon, Temu, Shein, Zalando, Fnac, AliExpress, Cdiscount, Asphalte
- Une livraison de marchandise
- Un achat en ligne (e-commerce)

Si c'est du E-COMMERCE → Réponds UNIQUEMENT : {"is_valid": false, "reason": "E-commerce, pas transport"}

═══════════════════════════════════════════════════════════════
💰 RÈGLE ABSOLUE SUR LES REMBOURSEMENTS 💰
═══════════════════════════════════════════════════════════════

🎫 SI LA COMPAGNIE PROPOSE UN BON D'ACHAT / AVOIR / VOUCHER / MILES :
   1. C'est un LITIGE → litige: true
   2. Le motif est : "La compagnie impose un avoir au lieu d'un remboursement financier"
   3. Le montant = la valeur du bon/avoir proposé (ex: "15€")
   4. Le passager a DROIT à un remboursement en ARGENT, pas en bons !

💳 SI LA COMPAGNIE A FAIT UN VIREMENT BANCAIRE ou CRÉDIT CARTE :
   1. Ce n'est PAS un litige → litige: false
   2. Le motif est : "Virement bancaire effectué"

⚠️ ATTENTION : Un email de "compensation" ou "indemnisation" n'est PAS forcément un succès !
   - Si c'est un BON → LITIGE
   - Si c'est un VIREMENT → Pas de litige

Mots-clés BON D'ACHAT (= LITIGE) : avoir, voucher, bon, crédit voyage, miles, points, geste commercial, compensation en bons
Mots-clés VIREMENT (= PAS LITIGE) : virement effectué, crédité sur votre compte bancaire, remboursement par virement, IBAN crédité

Réponds TOUJOURS en JSON valide."""

        user_prompt = f"""📧 EMAIL À ANALYSER (SCAN TRANSPORT) :

EXPÉDITEUR: {sender}
SUJET: {subject}
CONTENU: {text[:2500]}

═══════════════════════════════════════════════════════════════
🔍 ÉTAPE 1 : VÉRIFICATION DU TYPE (OBLIGATOIRE)
═══════════════════════════════════════════════════════════════

Cet email concerne-t-il un TRANSPORT DE PASSAGERS ?

✅ VALIDE si : Train, Avion, Vol, TGV, Eurostar, Ouigo, SNCF, Air France, 
               EasyJet, Ryanair, Transavia, Uber, Bolt, Thalys, KLM, Lufthansa

❌ INVALIDE si : Colis, Commande, Livraison, Produit, Vêtement, Article,
                 Amazon, Temu, Shein, Zalando, Fnac, AliExpress, Asphalte,
                 Chaussures, T-shirt, Pantalon, Accessoire

Si INVALIDE → {{"is_valid": false, "reason": "E-commerce/Livraison de produit"}}

═══════════════════════════════════════════════════════════════
🔍 ÉTAPE 2 : ANALYSE DU LITIGE TRANSPORT (si valide)
═══════════════════════════════════════════════════════════════

COMPAGNIES TRANSPORT ACCEPTÉES :
- Aérien : Air France, EasyJet, Ryanair, Transavia, Vueling, Volotea, 
           Lufthansa, British Airways, KLM, Iberia, TAP, Emirates
- Train : SNCF, Ouigo, Eurostar, Thalys, Trenitalia, TGV, TER, Intercités
- VTC : Uber, Bolt, Free Now, Kapten

═══════════════════════════════════════════════════════════════
💰 INDEMNISATIONS - RÈGLES STRICTES
═══════════════════════════════════════════════════════════════

🚨 RÈGLE CRITIQUE : TOUJOURS ESTIMER UN MONTANT THÉORIQUE 🚨

Même si la compagnie refuse l'indemnisation (grève, météo, "circonstance 
exceptionnelle", force majeure), TU DOIS calculer le montant THÉORIQUE 
selon le Règlement UE. Le passager a le droit de contester le refus.

⛔ NE JAMAIS RENVOYER 0€ pour un retard > 3h ou une annulation !

BARÈME VOL (Règlement UE 261/2004) :
- Distance < 1500 km : 250€
- Distance 1500-3500 km : 400€  
- Distance > 3500 km : 600€
- Retard < 3h : indemnisation réduite de 50%

BARÈME TRAIN (Règlement UE 2021/782) :
- Retard ≥60 min : 25% du prix du billet
- Retard ≥120 min : 50% du prix du billet
- Si prix du billet inconnu, estimer : 50€ à 150€ selon trajet

BAGAGE PERDU : jusqu'à 1300€

═══════════════════════════════════════════════════════════════
📋 FORMAT DE RÉPONSE JSON
═══════════════════════════════════════════════════════════════

Si E-COMMERCE (invalide) :
{{"is_valid": false, "reason": "Colis/Commande e-commerce"}}

Si TRANSPORT valide avec litige (retard/annulation) :
{{"is_valid": true, "litige": true, "company": "SNCF", "amount": "250€", "law": "Règlement UE 261/2004", "proof": "Vol annulé, la compagnie invoque la grève mais l'indemnisation reste due", "category": "transport"}}

Si TRANSPORT valide avec BON D'ACHAT (= litige !) :
{{"is_valid": true, "litige": true, "company": "SNCF", "amount": "15€", "law": "Règlement UE 2021/782", "proof": "La compagnie propose un bon d'achat de 15€ au lieu d'un remboursement financier", "category": "transport"}}

Si TRANSPORT valide sans litige (confirmation normale, vrai virement reçu) :
{{"is_valid": true, "litige": false, "reason": "Confirmation de réservation normale"}}

⚠️ RAPPEL 1 : Même si l'email dit "pas d'indemnisation due", calcule quand même le montant théorique !
⚠️ RAPPEL 2 : Un BON D'ACHAT ou AVOIR n'est PAS un remboursement valide → c'est un LITIGE !
"""

    else:  # scan_type == "ecommerce"
        system_prompt = """Tu es un EXPERT en Droit de la Consommation et Litiges E-commerce (Directive UE 2011/83, Code de la consommation).

🚨 RÈGLE ABSOLUE DE FILTRAGE 🚨
Tu ne traites QUE les problèmes de PRODUITS PHYSIQUES :
- Colis non livré
- Produit défectueux
- Remboursement non effectué
- Retour refusé
- Article non conforme

❌ REJETTE IMMÉDIATEMENT si l'email concerne :
- Un billet de TRAIN ou d'AVION
- Un retard de VOL ou de TGV
- SNCF, Air France, EasyJet, Ryanair, Eurostar, Uber, Bolt
- Un problème de PASSAGER (pas de colis)

Si c'est du TRANSPORT → Réponds UNIQUEMENT : {"is_valid": false, "reason": "Transport, pas e-commerce"}

Réponds TOUJOURS en JSON valide."""

        user_prompt = f"""📧 EMAIL À ANALYSER (SCAN E-COMMERCE) :

EXPÉDITEUR: {sender}
DESTINATAIRE: {to_field}
SUJET: {subject}
CONTENU: {text[:2500]}

═══════════════════════════════════════════════════════════════
🔍 ÉTAPE 1 : VÉRIFICATION DU TYPE (OBLIGATOIRE)
═══════════════════════════════════════════════════════════════

Cet email concerne-t-il un PRODUIT PHYSIQUE / COMMANDE E-COMMERCE ?

✅ VALIDE si : Colis, Commande, Livraison, Produit, Article, Achat,
               Amazon, Zalando, Fnac, Darty, Cdiscount, Temu, Shein, AliExpress

❌ INVALIDE si : Billet train, Billet avion, Vol, TGV, Eurostar, 
                 SNCF, Air France, EasyJet, Ryanair, Uber, Bolt

Si INVALIDE → {{"is_valid": false, "reason": "Transport/Billet"}}

═══════════════════════════════════════════════════════════════
🔍 ÉTAPE 2 : ANALYSE DU LITIGE E-COMMERCE (si valide)
═══════════════════════════════════════════════════════════════

ENTREPRISES E-COMMERCE :
Amazon, Zalando, Fnac, Darty, Cdiscount, AliExpress, Temu, Shein,
La Redoute, Asos, Zara, H&M, Mango, Vinted, eBay, Back Market, Asphalte...

MOTS-CLÉS DE LITIGE :
- "pas reçu", "jamais reçu", "colis perdu", "non livré"
- "défectueux", "cassé", "ne fonctionne pas"
- "remboursement", "retour refusé"
- "non conforme", "contrefaçon"

═══════════════════════════════════════════════════════════════
📋 FORMAT DE RÉPONSE JSON
═══════════════════════════════════════════════════════════════

Si TRANSPORT (invalide) :
{{"is_valid": false, "reason": "Billet train/avion"}}

Si E-COMMERCE valide avec litige :
{{"is_valid": true, "litige": true, "company": "AMAZON", "amount": "42.99€", "law": "Directive UE 2011/83", "proof": "Colis jamais reçu", "category": "ecommerce"}}

Si E-COMMERCE valide sans litige :
{{"is_valid": true, "litige": false, "reason": "Confirmation de commande normale"}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,  # Très strict
            max_tokens=350
        )
        
        ai_response = (response.choices[0].message.content or "").strip()
        DEBUG_LOGS.append(f"🤖 AI {scan_type}: {ai_response[:80]}...")

        DEFAULT = {"is_valid": False, "litige": False, "reason": "Parsing error"}
        result = secure_json_parse(ai_response, DEFAULT)

        result.setdefault("is_valid", False)
        result.setdefault("litige", False)
        result.setdefault("reason", "")

        if result.get("is_valid") and result.get("litige"):
            result.setdefault("company", "Inconnu")
            result.setdefault("amount", "À déterminer")
            result.setdefault("law", "Code de la consommation")
            result.setdefault("proof", subject[:120] if subject else "")
            result["category"] = scan_type

        return result
            
    except Exception as e:
        DEBUG_LOGS.append(f"❌ Erreur IA {scan_type}: {str(e)}")
        return {"is_valid": False, "litige": False, "reason": str(e)[:50]}


def analyze_ecommerce_flexible(text, subject, sender, to_field=""):
    """
    📦 ANALYSE IA FLEXIBLE POUR E-COMMERCE - GRAND FILET (VERSION BLINDÉE)
    
    Détecte TOUS les problèmes de commande, quelle que soit la marque.
    Capable d'extraire le nom du vendeur même pour des petites boutiques.
    
    BLINDAGES V2 :
    - Parsing JSON sécurisé avec secure_json_parse()
    - Fallback heuristique si l'IA échoue
    - Valeurs par défaut garanties
    
    Retourne : {"is_valid": bool, "litige": bool, "company": str, "amount": str, "law": str, "proof": str}
    """
    
    # Valeur par défaut en cas d'échec total
    DEFAULT_RESPONSE = {
        "is_valid": False, 
        "litige": False, 
        "reason": "Analyse impossible",
        "company": "Inconnu",
        "amount": "À compléter",
        "law": "Code de la consommation",
        "proof": ""
    }
    
    if not OPENAI_API_KEY:
        DEBUG_LOGS.append("📦 analyze_ecommerce_flexible: Pas d'API OpenAI")
        return DEFAULT_RESPONSE
    
    # Extraire le domaine de l'expéditeur pour aider à identifier l'entreprise
    sender_domain = ""
    if "@" in sender:
        try:
            sender_domain = sender.split("@")[1].split(">")[0].split(".")[0]
        except:
            pass
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    system_prompt = """Tu es un expert en détection de litiges e-commerce. Tu analyses des emails pour trouver des problèmes de commande.

🎯 TA MISSION : Détecter TOUT problème de livraison/commande, quelle que soit l'entreprise (grande marque OU petite boutique).

📦 MOTS-CLÉS DE LITIGE (1 seul suffit pour valider) :
- Livraison : "retard", "delay", "non reçu", "jamais reçu", "colis perdu", "non livré", "en attente"
- Commande : "annulée", "problème", "erreur", "manquant", "incomplet"
- Produit : "défectueux", "cassé", "abîmé", "non conforme", "contrefaçon"
- Remboursement : "remboursement", "refund", "pas remboursé", "en attente"
- Service : "réclamation", "plainte", "litige", "dispute"

🏪 EXTRACTION DE L'ENTREPRISE :
- Cherche le nom dans l'expéditeur (ex: "service@asphalte.com" → ASPHALTE)
- Cherche dans le sujet (ex: "Votre commande Nike" → NIKE)
- Cherche dans le corps (ex: "Boutique XYZ" → XYZ)
- Si petite boutique inconnue, utilise le nom de domaine de l'expéditeur
- NE METS JAMAIS "Inconnu" si tu peux extraire un nom !

💰 EXTRACTION DU MONTANT :
- Cherche des patterns : "150€", "150 euros", "EUR 150", "total: 150"
- Si pas de montant visible → mets "0" (on corrigera après)
- JAMAIS de texte dans le champ montant, UNIQUEMENT des chiffres ou "0"

❌ REJETTE UNIQUEMENT SI :
1. C'est une CONFIRMATION de commande NORMALE (sans problème mentionné)
2. C'est du MARKETING/PROMO/NEWSLETTER pur
3. C'est une simple FACTURE sans problème
4. Le remboursement est DÉJÀ EFFECTUÉ ("votre compte a été crédité")

⚠️ RÈGLE D'OR : En cas de doute, valide le litige. Mieux vaut un faux positif qu'un litige raté !

📋 RÉPONDS UNIQUEMENT EN JSON VALIDE (pas de texte avant/après) :

SI LITIGE :
{"is_valid": true, "litige": true, "company": "NOM_ENTREPRISE", "amount": "XX", "law": "Article applicable", "proof": "Phrase clé du problème"}

SI PAS DE LITIGE :
{"is_valid": true, "litige": false, "reason": "Raison courte"}

SI C'EST DU TRANSPORT (train/avion) :
{"is_valid": false, "reason": "Transport, pas e-commerce"}"""

    user_prompt = f"""📧 EMAIL À ANALYSER :

EXPÉDITEUR: {sender}
DOMAINE: {sender_domain}
DESTINATAIRE: {to_field}
SUJET: {subject}
CONTENU: {text[:2500]}

Analyse cet email et réponds UNIQUEMENT en JSON valide (pas de texte avant/après les accolades)."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=400
        )
        
        ai_response = response.choices[0].message.content.strip()
        DEBUG_LOGS.append(f"📦 AI E-commerce brut: {ai_response[:100]}...")
        
        # ════════════════════════════════════════════════════════════════
        # 🛡️ PARSING SÉCURISÉ avec fallback
        # ════════════════════════════════════════════════════════════════
        
        result = secure_json_parse(ai_response, DEFAULT_RESPONSE)
        
        # ════════════════════════════════════════════════════════════════
        # 🔧 POST-TRAITEMENT : Assurer les valeurs par défaut
        # ════════════════════════════════════════════════════════════════
        
        # S'assurer que tous les champs requis existent
        result.setdefault("is_valid", False)
        result.setdefault("litige", False)
        result.setdefault("company", "Vendeur")
        result.setdefault("amount", "0")
        result.setdefault("law", "Code de la consommation")
        result.setdefault("proof", subject[:100] if subject else "")
        result.setdefault("reason", "")
        
        # ════════════════════════════════════════════════════════════════
        # 🛡️ CORRECTION COMPANY : Ne jamais utiliser un provider mail
        # ════════════════════════════════════════════════════════════════
        company_raw = (result.get("company") or "").strip()
        company_l = company_raw.lower()
        
        # Si l'IA renvoie inconnu/vide OU un provider mail comme entreprise
        if company_l in ["", "inconnu", "unknown", "vendeur"] or company_l in MAIL_PROVIDERS:
            # Essayer d'extraire depuis TO/Sujet/From (pas depuis domaine mail provider)
            guessed = extract_company_from_recipient(to_field, subject, sender) if to_field else None
            if guessed:
                result["company"] = guessed.upper() if isinstance(guessed, str) else guessed
                DEBUG_LOGS.append(f"📦 Company extraite via extract_company_from_recipient: {result['company']}")
            elif sender_domain and sender_domain.lower() not in MAIL_PROVIDERS:
                # Fallback domaine uniquement si ce n'est PAS un provider mail
                result["company"] = sender_domain.upper()
                DEBUG_LOGS.append(f"📦 Company extraite du domaine non-provider: {result['company']}")
            else:
                result["company"] = "Vendeur"
                DEBUG_LOGS.append("📦 Company: aucune extraction possible, fallback Vendeur")
        
        # Nettoyer le montant (enlever le symbole €, garder que les chiffres)
        amount_str = str(result.get("amount", "0"))
        amount_clean = re.sub(r'[^\d.,]', '', amount_str).replace(',', '.')
        try:
            amount_num = float(amount_clean) if amount_clean else 0
            result["amount"] = f"{amount_num:.0f}€" if amount_num > 0 else "À compléter"
        except:
            result["amount"] = "À compléter"
        
        # Ajouter la catégorie
        if result.get("is_valid") and result.get("litige"):
            result["category"] = "ecommerce"
        
        DEBUG_LOGS.append(f"📦 AI E-commerce final: valid={result.get('is_valid')}, litige={result.get('litige')}, company={result.get('company')}")
        
        return result
        
    except Exception as e:
        DEBUG_LOGS.append(f"❌ Erreur IA E-commerce Flexible: {type(e).__name__} - {str(e)[:80]}")
        
        # ════════════════════════════════════════════════════════════════
        # 🆘 FALLBACK HEURISTIQUE : Analyse basique sans IA
        # ════════════════════════════════════════════════════════════════
        
        text_lower = (text + " " + subject + " " + sender).lower()
        
        # Mots-clés de litige
        litige_keywords = ["retard", "non reçu", "jamais reçu", "colis perdu", "défectueux", 
                          "cassé", "remboursement", "réclamation", "problème commande"]
        
        has_litige = any(kw in text_lower for kw in litige_keywords)
        
        if has_litige:
            DEBUG_LOGS.append("📦 FALLBACK HEURISTIQUE: Litige détecté par mots-clés")
            return {
                "is_valid": True,
                "litige": True,
                "company": sender_domain.capitalize() if sender_domain else "Vendeur",
                "amount": "À compléter",
                "law": "Code de la consommation",
                "proof": subject[:100],
                "category": "ecommerce"
            }
        
        return DEFAULT_RESPONSE

# ========================================
# MUR DE FILTRAGE - HARD FILTER EXPÉDITEURS
# ========================================

# Domaines d'entreprises à BLOQUER (emails de réponses/notifications)
BLACKLIST_COMPANY_DOMAINS = [
    # E-commerce
    "amazon", "fnac", "darty", "cdiscount", "zalando", "asos", "zara",
    "hm.com", "shein", "aliexpress", "temu", "vinted", "ebay", "wish",
    "rakuten", "priceminister", "leboncoin", "backmarket",
    # Transport
    "sncf", "c-sncf", "ouigo", "eurostar", "thalys", "trainline",
    "airfrance", "air-france", "klm", "easyjet", "ryanair", "vueling",
    "lufthansa", "british-airways", "transavia", "volotea",
    "uber", "bolt", "kapten", "heetch", "blablacar",
    # Livraison
    "deliveroo", "ubereats", "justeat", "chronopost", "colissimo",
    "dhl", "ups", "fedex", "mondialrelay", "relais-colis", "laposte",
    # Tech / Services
    "apple", "google", "microsoft", "paypal", "stripe", "booking",
    "airbnb", "expedia", "tripadvisor", "hotels.com", "kayak",
    "facebook", "instagram", "twitter", "linkedin", "tiktok",
    # Télécom
    "orange.com", "sfr.com", "bouygues", "sosh",
    # Banques / Assurances  
    "bnp", "societegenerale", "creditagricole", "lcl", "boursorama",
    "fortuneo", "ing", "revolut", "n26", "axa", "allianz", "maif"
]

# Préfixes d'adresses à BLOQUER (rôles automatisés)
BLACKLIST_EMAIL_PREFIXES = [
    "no-reply", "noreply", "ne-pas-repondre", "do-not-reply", "donotreply",
    "contact", "service", "support", "client", "customer", "help",
    "compta", "facture", "invoice", "billing", "payment", "paiement",
    "notification", "notifications", "alert", "alerts", "alerte",
    "info", "infos", "information", "news", "newsletter", "marketing",
    "team", "equipe", "admin", "system", "mailer", "daemon", "postmaster",
    "order", "orders", "commande", "commandes", "shipping", "livraison",
    "confirm", "confirmation", "verification", "security", "securite",
    "update", "updates", "mise-a-jour", "promo", "promotion", "pub"
]

# Domaines AUTORISÉS (particuliers uniquement)
WHITELIST_PERSONAL_DOMAINS = [
    "gmail.com", "googlemail.com", "yahoo.fr", "yahoo.com", "outlook.com",
    "outlook.fr", "hotmail.com", "hotmail.fr", "live.com", "live.fr",
    "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "aol.fr",
    "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net",
    "bbox.fr", "numericable.fr", "neuf.fr", "club-internet.fr",
    "protonmail.com", "protonmail.ch", "pm.me", "tutanota.com",
    "yandex.com", "gmx.com", "gmx.fr", "zoho.com", "mail.com"
]

# Mots-clés OBLIGATOIRES pour passer au filtrage IA
REQUIRED_KEYWORDS = [
    # Problèmes financiers
    "remboursement", "rembourser", "remboursé", "refund",
    "litige", "plainte", "réclamation", "reclamation",
    "argent", "euros", "€", "eur",
    "dédommagement", "dedommagement", "indemnisation", "indemnité",
    # Problèmes de service
    "retard", "retardé", "annulé", "annulation", "cancelled", "canceled",
    "non reçu", "pas reçu", "jamais reçu", "colis perdu", "commande perdue",
    "défectueux", "defectueux", "cassé", "abîmé", "endommagé",
    "arnaque", "escroquerie", "fraude", "volé",
    # Actions demandées
    "je demande", "je réclame", "je souhaite", "je veux",
    "mise en demeure", "avocat", "justice", "tribunal"
]

# ════════════════════════════════════════════════════════════════════════════
# 🕵️ AGENT 1 : LE CHASSEUR - Mots-clés de SUCCÈS à IGNORER
# Ces mots indiquent que le problème est RÉSOLU → Pas un litige à créer
# ════════════════════════════════════════════════════════════════════════════
KEYWORDS_SUCCESS = [
    # ⚠️ UNIQUEMENT les vrais VIREMENTS BANCAIRES / CRÉDITS CARTE
    # Ces termes indiquent un VRAI remboursement financier (pas un bon d'achat)
    "virement effectué", "virement réalisé", "virement envoyé",
    "virement bancaire effectué", "virement sur votre compte",
    "crédité sur votre compte bancaire", "créditée sur votre carte",
    "remboursement par virement", "remboursement carte bancaire",
    "remboursement crédité", "montant viré",
    "IBAN crédité", "RIB crédité"
]

# 🎫 MOTS-CLÉS VOUCHER/AVOIR - Ces emails DOIVENT être envoyés à l'IA !
# Ne JAMAIS skipper un email contenant ces termes
VOUCHER_KEYWORDS = [
    "bon d'achat", "bon achat", "avoir", "voucher", "crédit voyage",
    "code promo", "e-billet", "miles", "points fidélité",
    "geste commercial", "compensation", "dédommagement",
    "réduction accordée", "remise commerciale"
]

# ════════════════════════════════════════════════════════════════════════════
# 🕵️ AGENT 1 : LE CHASSEUR - Mots-clés de REFUS à IGNORER
# Ces mots indiquent que l'entreprise a REFUSÉ → Pas un litige gagnable
# ════════════════════════════════════════════════════════════════════════════
KEYWORDS_REFUSAL = [
    # Formules de refus polies
    "malheureusement", "nous regrettons", "nous sommes au regret",
    "ne pouvons pas accéder", "ne pouvons accéder", "ne pouvons pas donner suite",
    "impossible de vous rembourser", "impossible de procéder",
    "votre demande ne peut être acceptée", "ne peut aboutir",
    "nous ne sommes pas en mesure", "pas en mesure de",
    "dans l'impossibilité de", "ne sera pas possible",
    # Refus explicites
    "refusons votre demande", "refus de remboursement", "demande rejetée",
    "rejet de votre réclamation", "réclamation non recevable",
    # Conditions non remplies
    "conditions non remplies", "hors délai", "hors garantie",
    "délai dépassé", "garantie expirée", "non couvert",
    # Réponses négatives fermes
    "ne donnera pas lieu", "clôture sans suite", "sans suite favorable"
]

# ════════════════════════════════════════════════════════════════════════════════
# 📧 PROVIDERS MAIL - Ne jamais utiliser comme "company" de litige
# ════════════════════════════════════════════════════════════════════════════════
MAIL_PROVIDERS = {
    "gmail", "googlemail", "outlook", "hotmail", "live", "msn", "yahoo", "icloud", "me", "mac",
    "protonmail", "proton", "pm", "gmx", "zoho", "mail", "aol",
    "orange", "wanadoo", "free", "sfr", "laposte", "bbox", "neuf", "numericable"
}

# ════════════════════════════════════════════════════════════════════════════════
# 📄 FILTRE FACTURES NORMALES (éviter faux positifs)
# ════════════════════════════════════════════════════════════════════════════════
INVOICE_KEYWORDS = [
    "facture", "invoice", "reçu", "receipt", "confirmation de paiement", "payment confirmation",
    "paiement accepté", "payment successful", "merci pour votre paiement", "payment received",
    "renouvellement", "renewal", "abonnement", "subscription", "prélèvement", "sepa", "montant débité",
    "échéance", "mensualité", "paiement mensuel", "votre facture est disponible",
]

DISPUTE_TRIGGERS = [
    "pas reçu", "non reçu", "jamais reçu", "non livré", "pas livré", "colis perdu",
    "retard", "delay", "annulation", "cancel", "annulé",
    "pas remboursé", "remboursement refusé", "en attente de remboursement", "attente remboursement",
    "litige", "réclamation", "plainte", "dispute", "contestation",
    "défectueux", "cassé", "endommagé", "broken", "defective", "damaged", "abîmé",
    "non conforme", "contrefaçon", "arnaque", "erreur",
]

def is_invoice_without_dispute(subject: str, snippet: str) -> bool:
    """
    Détecte les factures/confirmations de paiement normales SANS litige.
    Retourne True si c'est une facture normale à ignorer.
    """
    blob = f"{subject or ''} {snippet or ''}".lower()
    if any(k in blob for k in INVOICE_KEYWORDS) and not any(t in blob for t in DISPUTE_TRIGGERS):
        return True
    return False

def is_ignored_sender(sender_email):
    """
    ÉTAPE 1A : Vérification de l'expéditeur (GRATUIT)
    Retourne (True, raison) si l'expéditeur doit être IGNORÉ
    Retourne (False, "OK") si c'est un particulier
    """
    if not sender_email:
        return True, "Expéditeur vide"
    
    sender_lower = sender_email.lower()
    
    # Extraire l'adresse email si format "Nom <email@domain.com>"
    email_match = re.search(r'<([^>]+)>', sender_lower)
    if email_match:
        email_address = email_match.group(1)
    else:
        email_address = sender_lower.strip()
    
    # Extraire le préfixe (avant @) et le domaine (après @)
    if '@' in email_address:
        prefix, domain = email_address.split('@', 1)
    else:
        return True, "Format email invalide"
    
    # CHECK 1 : Vérifier si le DOMAINE est une entreprise blacklistée
    for blacklisted in BLACKLIST_COMPANY_DOMAINS:
        if blacklisted in domain:
            return True, f"Domaine entreprise: {blacklisted}"
    
    # CHECK 2 : Vérifier si le PRÉFIXE est un rôle automatisé
    for blacklisted_prefix in BLACKLIST_EMAIL_PREFIXES:
        if blacklisted_prefix in prefix:
            return True, f"Préfixe automatisé: {blacklisted_prefix}"
    
    return False, "OK"

def has_required_keywords(subject, body_snippet):
    """
    ÉTAPE 1B : Vérification des mots-clés PROBLÈME (GRATUIT)
    Retourne True si l'email contient au moins un mot-clé de litige
    """
    text_to_check = (subject + " " + body_snippet).lower()
    
    for keyword in REQUIRED_KEYWORDS:
        if keyword.lower() in text_to_check:
            return True, keyword
    
    return False, None

def has_success_keywords(subject, body_snippet):
    """
    🕵️ AGENT 1 (CHASSEUR) - Détection des emails de SUCCÈS (GRATUIT)
    Retourne True si l'email indique que le problème est RÉSOLU
    → Ces emails doivent être IGNORÉS par le Chasseur (pas de litige à créer)
    → Ils seront traités par l'Encaisseur (CRON) pour valider les paiements
    """
    text_to_check = (subject + " " + body_snippet).lower()
    
    for keyword in KEYWORDS_SUCCESS:
        if keyword.lower() in text_to_check:
            return True, keyword
    
    return False, None

def has_refusal_keywords(subject, body_snippet):
    """
    🕵️ AGENT 1 (CHASSEUR) - Détection des emails de REFUS (GRATUIT)
    Retourne True si l'email est un REFUS du service client
    → Ces emails ne sont PAS des litiges gagnables (l'entreprise a dit NON)
    """
    text_to_check = (subject + " " + body_snippet).lower()
    
    for keyword in KEYWORDS_REFUSAL:
        if keyword.lower() in text_to_check:
            return True, keyword
    
    return False, None

def pre_filter_email(sender, subject, snippet):
    """
    🕵️ AGENT 1 : LE CHASSEUR - ENTONNOIR DE FILTRAGE (Python pur - GRATUIT)
    
    But : Trouver les PROBLÈMES NON RÉSOLUS uniquement
    
    Vérifie si l'email mérite d'être analysé par l'IA.
    Retourne (True, None) si l'email doit être analysé
    Retourne (False, raison) si l'email doit être SKIP
    """
    
    # CHECK 1 : L'expéditeur est-il un robot ou une entreprise ?
    is_ignored, ignore_reason = is_ignored_sender(sender)
    if is_ignored:
        return False, f"🤖 Expéditeur bloqué: {ignore_reason}"
    
    # CHECK 2 : L'email contient-il des mots-clés de SUCCÈS ?
    # → Si oui, le problème est RÉSOLU, pas besoin de créer un litige
    # → L'Encaisseur (CRON) s'en occupera pour valider les paiements
    is_success, success_keyword = has_success_keywords(subject, snippet)
    if is_success:
        return False, f"✅ Succès détecté (pour CRON): '{success_keyword}'"
    
    # CHECK 3 : L'email contient-il des mots-clés de REFUS ?
    # → Si oui, l'entreprise a déjà dit NON, pas un litige gagnable
    is_refusal, refusal_keyword = has_refusal_keywords(subject, snippet)
    if is_refusal:
        return False, f"🚫 Refus détecté: '{refusal_keyword}'"
    
    # CHECK 4 : L'email contient-il des mots-clés de PROBLÈME ?
    has_keywords, found_keyword = has_required_keywords(subject, snippet)
    if not has_keywords:
        return False, "❌ Aucun mot-clé litige trouvé"
    
    # L'email a passé le videur ! C'est un PROBLÈME NON RÉSOLU
    return True, f"🎯 Mot-clé litige: '{found_keyword}'"

def is_company_sender(sender):
    """Alias pour compatibilité - utilise le nouveau filtre strict"""
    is_ignored, reason = is_ignored_sender(sender)
    return is_ignored

def extract_company_from_recipient(to_field, subject, sender):
    """
    Extrait l'entreprise depuis le destinataire (TO) en priorité,
    sinon depuis le sujet ou l'expéditeur
    """
    to_lower = to_field.lower() if to_field else ""
    
    # Liste des entreprises connues
    companies = [
        "amazon", "fnac", "darty", "sncf", "air france", "airfrance",
        "zalando", "apple", "booking", "airbnb", "expedia", "ryanair",
        "easyjet", "lufthansa", "klm", "british airways", "eurostar",
        "ouigo", "uber", "deliveroo", "bolt", "zara", "h&m", "asos",
        "cdiscount", "ebay", "wish"
    ]
    
    # 1. Chercher dans le destinataire (TO) - PRIORITÉ
    for company in companies:
        company_clean = company.replace(" ", "")
        if company in to_lower or company_clean in to_lower:
            return company
    
    # 2. Chercher dans le sujet
    subject_lower = subject.lower()
    for company in companies:
        if company in subject_lower:
            return company
    
    # 3. Chercher dans l'expéditeur (pour les réponses)
    sender_lower = sender.lower()
    for company in companies:
        company_clean = company.replace(" ", "")
        if company in sender_lower or company_clean in sender_lower:
            return company
    
    return None

def extract_numeric_amount(amount_str):
    """
    Extrait le montant numérique d'une chaîne - VERSION AMÉLIORÉE
    Gère: "42.99€", "42,99€", "42 €", "42€", "42 EUR", "42 euros"
    """
    if not amount_str:
        return 0
    
    # Normaliser la chaîne
    amount_clean = amount_str.replace(",", ".").replace(" ", "")
    
    # Pattern pour capturer les montants avec décimales
    # Exemples: 42.99€, 42€, 42.99EUR, 42euros
    patterns = [
        r'(\d+[.,]?\d*)\s*€',           # 42.99€ ou 42€
        r'(\d+[.,]?\d*)\s*eur',          # 42.99EUR ou 42 eur
        r'€\s*(\d+[.,]?\d*)',            # €42.99
        r'(\d+[.,]?\d*)\s*euros?',       # 42 euros ou 42 euro
        r'(\d+[.,]?\d*)'                 # Fallback: juste un nombre
    ]
    
    for pattern in patterns:
        match = re.search(pattern, amount_str.lower())
        if match:
            try:
                value = float(match.group(1).replace(",", "."))
                return int(value)  # Arrondir à l'entier
            except:
                continue
    
    return 0

def is_valid_euro_amount(amount_str: str) -> bool:
    """
    Valide un montant type '42€' / '42.99€' / '42,99€'.
    Refuse 'À compléter' et montants nuls.
    """
    if not amount_str:
        return False
    s = str(amount_str).strip().lower()
    if "compl" in s:
        return False

    m = re.search(r"(\d+(?:[.,]\d{1,2})?)\s*(€|eur|euros?)\b", s)
    if not m:
        m = re.search(r"(\d+(?:[.,]\d{1,2})?)\s*€", s)
        if not m:
            return False

    try:
        val = float(m.group(1).replace(",", "."))
        return val > 0
    except:
        return False

def extract_amount_from_text(text):
    """
    Extrait un montant depuis un texte brut - VERSION AMÉLIORÉE
    Cherche les patterns de montant dans tout le texte
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    # Patterns pour trouver des montants en euros
    patterns = [
        r'(\d+[.,]?\d*)\s*€',
        r'(\d+[.,]?\d*)\s*eur(?:os?)?',
        r'€\s*(\d+[.,]?\d*)',
        r'montant[:\s]+(\d+[.,]?\d*)',
        r'remboursement[:\s]+(?:de\s+)?(\d+[.,]?\d*)',
        r'prix[:\s]+(\d+[.,]?\d*)',
        r'somme[:\s]+(?:de\s+)?(\d+[.,]?\d*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                value = float(match.group(1).replace(",", "."))
                if value > 0:
                    return f"{int(value)}€"
            except:
                continue
    
    return None

def send_litigation_email(creds, target_email, subject, body_text):
    """Envoie un email de mise en demeure"""
    try:
        service = build('gmail', 'v1', credentials=creds)
        message = MIMEText(body_text)
        message['to'] = target_email
        message['subject'] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except Exception as e:
        DEBUG_LOGS.append(f"Erreur envoi email: {str(e)}")
        return False

# ========================================
# TEMPLATES HTML
# ========================================

STYLE = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="google-site-verification" content="Qeh_EJmqe8ZdqRUxtJ_JjH1TFtnVUpCrAIhkOxNtkL0" />
    <meta name="description" content="Justicio - Récupérez votre argent automatiquement. Litiges e-commerce, retards de transport. 0€ d'avance, commission uniquement au succès.">
    <title>Justicio - Récupérez votre argent</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚖️</text></svg>">
</head>
<body>
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');

:root {
    --primary: #4f46e5;
    --primary-dark: #3730a3;
    --secondary: #0ea5e9;
    --success: #10b981;
    --warning: #f59e0b;
    --danger: #ef4444;
    --gold: #fbbf24;
    --dark: #0f172a;
    --card-bg: rgba(255,255,255,0.95);
}

* {
    box-sizing: border-box;
}

body {
    font-family: 'Outfit', sans-serif;
    background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #312e81 100%);
    min-height: 100vh;
    padding: 30px 20px 150px 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    color: #1e293b;
    margin: 0;
}

/* ═══════════════════════════════════════════════════════════════
   CARDS PREMIUM
═══════════════════════════════════════════════════════════════ */

.card {
    background: var(--card-bg);
    border-radius: 24px;
    padding: 30px;
    margin: 15px;
    width: 100%;
    max-width: 550px;
    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25), 
                0 0 0 1px rgba(255,255,255,0.1);
    border-left: 6px solid var(--danger);
    position: relative;
    backdrop-filter: blur(10px);
}

.card-glass {
    background: rgba(255,255,255,0.1);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 24px;
    padding: 30px;
    color: white;
}

/* ═══════════════════════════════════════════════════════════════
   ACTION CARDS (Page d'accueil)
═══════════════════════════════════════════════════════════════ */

.action-card {
    background: var(--card-bg);
    border-radius: 24px;
    padding: 35px 30px;
    width: 100%;
    max-width: 320px;
    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
    position: relative;
    overflow: hidden;
    transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    cursor: pointer;
    text-decoration: none;
    display: block;
    color: #1e293b;
}

.action-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 5px;
    background: linear-gradient(90deg, var(--primary), var(--secondary));
}

.action-card:hover {
    transform: translateY(-8px) scale(1.02);
    box-shadow: 0 35px 60px -15px rgba(0, 0, 0, 0.35);
}

.action-card.travel::before {
    background: linear-gradient(90deg, var(--gold), #f97316);
}

.action-card .icon {
    font-size: 3.5rem;
    margin-bottom: 15px;
}

.action-card .title {
    font-size: 1.4rem;
    font-weight: 700;
    margin-bottom: 8px;
    color: #0f172a;
}

.action-card .description {
    font-size: 0.95rem;
    color: #64748b;
    line-height: 1.5;
    margin-bottom: 15px;
}

.action-card .badge {
    display: inline-block;
    padding: 6px 14px;
    border-radius: 50px;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
}

.badge-fast {
    background: linear-gradient(135deg, #dbeafe, #e0e7ff);
    color: #3730a3;
}

.badge-premium {
    background: linear-gradient(135deg, #fef3c7, #fde68a);
    color: #92400e;
}

/* ═══════════════════════════════════════════════════════════════
   BUTTONS PREMIUM
═══════════════════════════════════════════════════════════════ */

.btn-success {
    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
    color: white;
    padding: 16px 40px;
    border-radius: 50px;
    text-decoration: none;
    font-weight: 600;
    font-size: 1.1rem;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: 0 10px 30px rgba(16, 185, 129, 0.4);
    border: none;
    cursor: pointer;
    display: inline-block;
    position: relative;
    overflow: hidden;
}

.btn-success::after {
    content: '';
    position: absolute;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: linear-gradient(45deg, transparent, rgba(255,255,255,0.2), transparent);
    transform: rotate(45deg);
    transition: all 0.5s;
}

.btn-success:hover {
    transform: translateY(-3px);
    box-shadow: 0 15px 40px rgba(16, 185, 129, 0.5);
}

.btn-success:hover::after {
    left: 100%;
}

.btn-primary {
    background: linear-gradient(135deg, #4f46e5 0%, #3730a3 100%);
    box-shadow: 0 10px 30px rgba(79, 70, 229, 0.4);
}

.btn-primary:hover {
    box-shadow: 0 15px 40px rgba(79, 70, 229, 0.5);
}

.btn-gold {
    background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);
    color: #1e293b;
    box-shadow: 0 10px 30px rgba(251, 191, 36, 0.4);
}

.btn-gold:hover {
    box-shadow: 0 15px 40px rgba(251, 191, 36, 0.5);
}

.btn-logout {
    background: rgba(255,255,255,0.1);
    backdrop-filter: blur(10px);
    padding: 10px 20px;
    font-size: 0.9rem;
    border-radius: 12px;
    color: white;
    text-decoration: none;
    border: 1px solid rgba(255,255,255,0.2);
    transition: all 0.3s;
}

.btn-logout:hover {
    background: rgba(255,255,255,0.2);
}

/* ═══════════════════════════════════════════════════════════════
   AMOUNT & BADGES
═══════════════════════════════════════════════════════════════ */

.amount-badge {
    position: absolute;
    top: 30px;
    right: 30px;
    font-size: 1.5rem;
    font-weight: 700;
    color: #10b981;
}

.amount-input {
    position: absolute;
    top: 30px;
    right: 30px;
    padding: 12px;
    border: 2px solid #ef4444;
    border-radius: 12px;
    width: 110px;
    font-weight: 700;
    font-size: 1.1rem;
    color: #ef4444;
    z-index: 10;
    background: white;
}

.amount-hint {
    color: #f59e0b;
    font-size: 0.75rem;
    position: absolute;
    top: 75px;
    right: 30px;
    width: 120px;
    text-align: right;
}

.radar-tag {
    background: linear-gradient(135deg, #e0f2fe, #dbeafe);
    color: #0369a1;
    padding: 5px 12px;
    border-radius: 8px;
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.proof-text {
    background: linear-gradient(135deg, #fef3c7, #fef9c3);
    padding: 15px;
    border-radius: 12px;
    border-left: 4px solid #f59e0b;
    margin: 15px 0;
    font-size: 0.95rem;
    color: #92400e;
    line-height: 1.6;
}

/* ═══════════════════════════════════════════════════════════════
   STICKY FOOTER & SUPPORT
═══════════════════════════════════════════════════════════════ */

.sticky-footer {
    position: fixed;
    bottom: 0;
    left: 0;
    width: 100%;
    background: rgba(15, 23, 42, 0.95);
    backdrop-filter: blur(20px);
    padding: 20px;
    box-shadow: 0 -10px 40px rgba(0,0,0,0.3);
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 100;
    border-top: 1px solid rgba(255,255,255,0.1);
}

.support-float {
    position: fixed;
    bottom: 100px;
    right: 20px;
    background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
    color: #FFF;
    border-radius: 50px;
    padding: 14px 22px;
    font-size: 0.9rem;
    font-weight: 600;
    box-shadow: 0 10px 30px rgba(79, 70, 229, 0.5);
    z-index: 100;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: all 0.3s;
}

.support-float:hover {
    transform: translateY(-3px) scale(1.05);
}

.whatsapp-float {
    position: fixed;
    width: 60px;
    height: 60px;
    bottom: 170px;
    right: 20px;
    background: linear-gradient(135deg, #25d366, #128c7e);
    color: #FFF;
    border-radius: 50%;
    text-align: center;
    font-size: 28px;
    box-shadow: 0 10px 30px rgba(37, 211, 102, 0.4);
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: center;
    text-decoration: none;
    transition: all 0.3s;
}

.whatsapp-float:hover {
    transform: scale(1.1);
}

/* ═══════════════════════════════════════════════════════════════
   FOOTER & DEBUG
═══════════════════════════════════════════════════════════════ */

footer {
    margin-top: 60px;
    font-size: 0.85rem;
    text-align: center;
    color: rgba(255,255,255,0.5);
}

footer a {
    color: rgba(255,255,255,0.7);
    text-decoration: none;
    margin: 0 12px;
    transition: color 0.3s;
}

footer a:hover {
    color: white;
}

.debug-section {
    margin-top: 50px;
    color: #64748b;
    background: rgba(255,255,255,0.9);
    padding: 25px;
    border-radius: 16px;
    max-width: 800px;
    font-size: 0.85rem;
}

/* ═══════════════════════════════════════════════════════════════
   🌟 LOADING OVERLAY - "Matrix Money" Effect
═══════════════════════════════════════════════════════════════ */

.loading-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(15, 23, 42, 0.95);
    backdrop-filter: blur(10px);
    z-index: 9999;
    display: none;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    color: white;
}

.loading-overlay.active {
    display: flex;
}

.loading-radar {
    width: 150px;
    height: 150px;
    border-radius: 50%;
    position: relative;
    margin-bottom: 40px;
}

.loading-radar::before {
    content: '';
    position: absolute;
    width: 100%;
    height: 100%;
    border-radius: 50%;
    border: 3px solid rgba(79, 70, 229, 0.3);
}

.loading-radar::after {
    content: '';
    position: absolute;
    width: 100%;
    height: 100%;
    border-radius: 50%;
    border: 3px solid transparent;
    border-top-color: #4f46e5;
    animation: radar-spin 1s linear infinite;
}

.loading-radar .pulse {
    position: absolute;
    width: 100%;
    height: 100%;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(79, 70, 229, 0.3) 0%, transparent 70%);
    animation: radar-pulse 2s ease-out infinite;
}

.loading-radar .icon {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 3rem;
    animation: icon-bounce 1s ease-in-out infinite;
}

@keyframes radar-spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}

@keyframes radar-pulse {
    0% { transform: scale(0.8); opacity: 1; }
    100% { transform: scale(1.5); opacity: 0; }
}

@keyframes icon-bounce {
    0%, 100% { transform: translate(-50%, -50%) scale(1); }
    50% { transform: translate(-50%, -50%) scale(1.1); }
}

.loading-text {
    font-size: 1.4rem;
    font-weight: 600;
    margin-bottom: 15px;
    text-align: center;
}

.loading-subtext {
    font-size: 1rem;
    color: rgba(255,255,255,0.6);
    text-align: center;
    max-width: 300px;
}

/* Money rain effect */
.money-rain {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    pointer-events: none;
}

.money {
    position: absolute;
    font-size: 2rem;
    animation: money-fall linear infinite;
    opacity: 0.3;
}

@keyframes money-fall {
    0% {
        transform: translateY(-100px) rotate(0deg);
        opacity: 0;
    }
    10% { opacity: 0.3; }
    90% { opacity: 0.3; }
    100% {
        transform: translateY(100vh) rotate(360deg);
        opacity: 0;
    }
}

/* Progress bar */
.loading-progress {
    width: 280px;
    height: 6px;
    background: rgba(255,255,255,0.1);
    border-radius: 10px;
    margin-top: 30px;
    overflow: hidden;
}

.loading-progress-bar {
    height: 100%;
    background: linear-gradient(90deg, #4f46e5, #06b6d4, #10b981);
    border-radius: 10px;
    animation: progress-animate 8s ease-in-out forwards;
}

@keyframes progress-animate {
    0% { width: 0%; }
    20% { width: 25%; }
    50% { width: 60%; }
    80% { width: 85%; }
    100% { width: 100%; }
}

</style>

<!-- ═══════════════════════════════════════════════════════════════
     LOADING OVERLAY HTML
═══════════════════════════════════════════════════════════════ -->
<div class="loading-overlay" id="loadingOverlay">
    <div class="money-rain" id="moneyRain"></div>
    
    <div class="loading-radar">
        <div class="pulse"></div>
        <div class="icon" id="loadingIcon">🔍</div>
    </div>
    
    <div class="loading-text" id="loadingText">Connexion à Gmail...</div>
    <div class="loading-subtext" id="loadingSubtext">Veuillez patienter quelques instants</div>
    
    <div class="loading-progress">
        <div class="loading-progress-bar"></div>
    </div>
</div>

<script>
// ═══════════════════════════════════════════════════════════════
// LOADING SCREEN CONTROLLER
// ═══════════════════════════════════════════════════════════════

const loadingMessages = [
    { text: "Connexion à Gmail...", sub: "Authentification sécurisée", icon: "🔐" },
    { text: "Recherche de transactions...", sub: "Analyse de votre boîte mail", icon: "📧" },
    { text: "Identification des marchands...", sub: "Amazon, SNCF, Booking...", icon: "🏪" },
    { text: "Analyse juridique en cours...", sub: "Vérification des lois applicables", icon: "⚖️" },
    { text: "Calcul des indemnités...", sub: "Estimation de vos droits", icon: "💰" },
    { text: "Litiges détectés !", sub: "Préparation des résultats...", icon: "🎯" }
];

function showLoading(type = 'ecommerce') {
    const overlay = document.getElementById('loadingOverlay');
    const textEl = document.getElementById('loadingText');
    const subEl = document.getElementById('loadingSubtext');
    const iconEl = document.getElementById('loadingIcon');
    
    overlay.classList.add('active');
    
    // Create money rain
    createMoneyRain();
    
    // Cycle through messages
    let index = 0;
    const interval = setInterval(() => {
        if (index < loadingMessages.length) {
            textEl.textContent = loadingMessages[index].text;
            subEl.textContent = loadingMessages[index].sub;
            iconEl.textContent = loadingMessages[index].icon;
            index++;
        } else {
            clearInterval(interval);
        }
    }, 1500);
}

function createMoneyRain() {
    const container = document.getElementById('moneyRain');
    container.innerHTML = '';
    const emojis = ['💶', '💵', '💴', '💷', '🪙', '💰'];
    
    for (let i = 0; i < 20; i++) {
        const money = document.createElement('div');
        money.className = 'money';
        money.textContent = emojis[Math.floor(Math.random() * emojis.length)];
        money.style.left = Math.random() * 100 + '%';
        money.style.animationDuration = (3 + Math.random() * 4) + 's';
        money.style.animationDelay = Math.random() * 3 + 's';
        container.appendChild(money);
    }
}

// Auto-show loading on scan links
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('a[href*="/scan"]').forEach(link => {
        link.addEventListener('click', function(e) {
            showLoading();
        });
    });
});
</script>
"""

# Email de support
SUPPORT_EMAIL = "support@justicio.fr"

FOOTER = """<footer>
    <a href='/cgu'>CGU</a> | 
    <a href='/confidentialite'>Confidentialité</a> | 
    <a href='/mentions-legales'>Mentions Légales</a>
    <p>© 2026 Justicio.fr - Tous droits réservés</p>
</footer>
<!-- BOUTON SUPPORT FLOTTANT -->
<a href='mailto:""" + SUPPORT_EMAIL + """?subject=Demande%20d%27aide%20Justicio' class='support-float'>
    🆘 Aide
</a>
</body>
</html>
"""

WA_BTN = f"""<a href="https://wa.me/{WHATSAPP_NUMBER}" class="whatsapp-float" target="_blank">💬</a>"""

# ========================================
# ROUTES PRINCIPALES
# ========================================

@app.route("/")
def index():
    """
    Page d'accueil - DOUBLE LOGIQUE :
    - Non connecté → Landing Page Marketing (pour validation Google)
    - Connecté → Dashboard avec cartes de scan
    """
    
    # ════════════════════════════════════════════════════════════════
    # LANDING PAGE PUBLIQUE (Non connecté)
    # ════════════════════════════════════════════════════════════════
    
    if "credentials" not in session:
        return STYLE + """
        <div style='max-width:1000px; margin:0 auto;'>
            
            <!-- HERO SECTION -->
            <div style='text-align:center; padding:60px 20px;'>
                <div style='font-size:5rem; margin-bottom:20px; 
                            text-shadow: 0 0 50px rgba(79, 70, 229, 0.5);'>⚖️</div>
                <h1 style='color:white; font-size:3.2rem; font-weight:800; margin:0 0 20px 0;
                           background: linear-gradient(135deg, #fff 0%, #a5b4fc 100%);
                           -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                           background-clip: text;'>JUSTICIO</h1>
                <p style='color:#fbbf24; font-size:1.6rem; font-weight:600; margin:0 0 10px 0;'>
                    Récupérez votre argent automatiquement
                </p>
                <p style='color:rgba(255,255,255,0.7); font-size:1.2rem; margin:0 0 40px 0;'>
                    Colis perdus • Retards de train • Vols annulés • Produits défectueux
                </p>
                
                <!-- CTA PRINCIPAL -->
                <a href='/login' style='display:inline-block; padding:20px 50px; 
                                        background:linear-gradient(135deg, #10b981 0%, #059669 100%);
                                        color:white; text-decoration:none; border-radius:50px;
                                        font-size:1.3rem; font-weight:700;
                                        box-shadow:0 15px 40px rgba(16, 185, 129, 0.4);
                                        transition:all 0.3s;'>
                    🚀 Commencer gratuitement
                </a>
                <p style='color:rgba(255,255,255,0.5); font-size:0.9rem; margin-top:15px;'>
                    Connexion sécurisée avec Google • Aucune carte bancaire requise
                </p>
            </div>
            
            <!-- PROPOSITION DE VALEUR -->
            <div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); 
                        gap:25px; padding:40px 20px;'>
                
                <div style='background:rgba(255,255,255,0.05); backdrop-filter:blur(10px);
                            padding:35px; border-radius:20px; text-align:center;
                            border:1px solid rgba(255,255,255,0.1);'>
                    <div style='font-size:3rem; margin-bottom:15px;'>💰</div>
                    <h3 style='color:white; font-size:1.3rem; margin:0 0 10px 0;'>0€ d'avance</h3>
                    <p style='color:rgba(255,255,255,0.6); margin:0; line-height:1.6;'>
                        Aucun frais à l'inscription. Vous ne payez que si nous récupérons votre argent.
                    </p>
                </div>
                
                <div style='background:rgba(255,255,255,0.05); backdrop-filter:blur(10px);
                            padding:35px; border-radius:20px; text-align:center;
                            border:1px solid rgba(255,255,255,0.1);'>
                    <div style='font-size:3rem; margin-bottom:15px;'>🤖</div>
                    <h3 style='color:white; font-size:1.3rem; margin:0 0 10px 0;'>100% Automatisé</h3>
                    <p style='color:rgba(255,255,255,0.6); margin:0; line-height:1.6;'>
                        Notre IA scanne vos emails et envoie des mises en demeure juridiques en votre nom.
                    </p>
                </div>
                
                <div style='background:rgba(255,255,255,0.05); backdrop-filter:blur(10px);
                            padding:35px; border-radius:20px; text-align:center;
                            border:1px solid rgba(255,255,255,0.1);'>
                    <div style='font-size:3rem; margin-bottom:15px;'>⚖️</div>
                    <h3 style='color:white; font-size:1.3rem; margin:0 0 10px 0;'>Juridiquement solide</h3>
                    <p style='color:rgba(255,255,255,0.6); margin:0; line-height:1.6;'>
                        Mises en demeure basées sur le Code de la Consommation et le règlement EC 261.
                    </p>
                </div>
                
            </div>
            
            <!-- COMMENT ÇA MARCHE -->
            <div style='padding:60px 20px; text-align:center;'>
                <h2 style='color:white; font-size:2rem; margin-bottom:50px;'>Comment ça marche ?</h2>
                
                <div style='display:flex; flex-wrap:wrap; justify-content:center; gap:40px;'>
                    
                    <div style='text-align:center; max-width:200px;'>
                        <div style='width:60px; height:60px; background:linear-gradient(135deg, #4f46e5, #3730a3);
                                    border-radius:50%; display:flex; align-items:center; justify-content:center;
                                    margin:0 auto 15px; font-size:1.5rem; color:white; font-weight:700;'>1</div>
                        <h4 style='color:white; margin:0 0 8px 0;'>Connectez Gmail</h4>
                        <p style='color:rgba(255,255,255,0.5); font-size:0.9rem; margin:0;'>
                            Connexion sécurisée OAuth 2.0
                        </p>
                    </div>
                    
                    <div style='text-align:center; max-width:200px;'>
                        <div style='width:60px; height:60px; background:linear-gradient(135deg, #4f46e5, #3730a3);
                                    border-radius:50%; display:flex; align-items:center; justify-content:center;
                                    margin:0 auto 15px; font-size:1.5rem; color:white; font-weight:700;'>2</div>
                        <h4 style='color:white; margin:0 0 8px 0;'>On scanne</h4>
                        <p style='color:rgba(255,255,255,0.5); font-size:0.9rem; margin:0;'>
                            L'IA détecte vos litiges
                        </p>
                    </div>
                    
                    <div style='text-align:center; max-width:200px;'>
                        <div style='width:60px; height:60px; background:linear-gradient(135deg, #4f46e5, #3730a3);
                                    border-radius:50%; display:flex; align-items:center; justify-content:center;
                                    margin:0 auto 15px; font-size:1.5rem; color:white; font-weight:700;'>3</div>
                        <h4 style='color:white; margin:0 0 8px 0;'>On agit</h4>
                        <p style='color:rgba(255,255,255,0.5); font-size:0.9rem; margin:0;'>
                            Mise en demeure automatique
                        </p>
                    </div>
                    
                    <div style='text-align:center; max-width:200px;'>
                        <div style='width:60px; height:60px; background:linear-gradient(135deg, #10b981, #059669);
                                    border-radius:50%; display:flex; align-items:center; justify-content:center;
                                    margin:0 auto 15px; font-size:1.5rem; color:white; font-weight:700;'>4</div>
                        <h4 style='color:white; margin:0 0 8px 0;'>Vous êtes remboursé</h4>
                        <p style='color:rgba(255,255,255,0.5); font-size:0.9rem; margin:0;'>
                            Commission de 30% au succès
                        </p>
                    </div>
                    
                </div>
            </div>
            
            <!-- TYPES DE LITIGES -->
            <div style='padding:40px 20px;'>
                <h2 style='color:white; font-size:2rem; margin-bottom:40px; text-align:center;'>
                    Quels litiges pouvons-nous résoudre ?
                </h2>
                
                <div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:20px;'>
                    
                    <div style='background:linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(5, 150, 105, 0.05));
                                padding:25px; border-radius:16px; border:1px solid rgba(16, 185, 129, 0.2);'>
                        <h3 style='color:#10b981; margin:0 0 10px 0; font-size:1.2rem;'>
                            📦 E-Commerce
                        </h3>
                        <p style='color:rgba(255,255,255,0.7); margin:0; line-height:1.6;'>
                            Colis non livré, produit défectueux, retour refusé, contrefaçon...
                            <br><b style='color:white;'>Amazon, Zalando, Fnac, AliExpress, Wish...</b>
                        </p>
                    </div>
                    
                    <div style='background:linear-gradient(135deg, rgba(251, 191, 36, 0.1), rgba(245, 158, 11, 0.05));
                                padding:25px; border-radius:16px; border:1px solid rgba(251, 191, 36, 0.2);'>
                        <h3 style='color:#fbbf24; margin:0 0 10px 0; font-size:1.2rem;'>
                            ✈️ Transport (jusqu'à 600€)
                        </h3>
                        <p style='color:rgba(255,255,255,0.7); margin:0; line-height:1.6;'>
                            Retard de vol/train, annulation, surbooking, bagage perdu...
                            <br><b style='color:white;'>SNCF, Air France, EasyJet, Ryanair, Eurostar...</b>
                        </p>
                    </div>
                    
                </div>
            </div>
            
            <!-- SOCIAL PROOF -->
            <div style='padding:50px 20px; text-align:center;'>
                <div style='background:rgba(255,255,255,0.05); backdrop-filter:blur(10px);
                            padding:40px; border-radius:24px; border:1px solid rgba(255,255,255,0.1);'>
                    <div style='display:flex; justify-content:center; gap:60px; flex-wrap:wrap;'>
                        <div>
                            <div style='font-size:2.5rem; font-weight:800; color:#10b981;'>15,420€</div>
                            <div style='color:rgba(255,255,255,0.5);'>Récupérés pour nos clients</div>
                        </div>
                        <div>
                            <div style='font-size:2.5rem; font-weight:800; color:#fbbf24;'>89%</div>
                            <div style='color:rgba(255,255,255,0.5);'>Taux de succès</div>
                        </div>
                        <div>
                            <div style='font-size:2.5rem; font-weight:800; color:#a78bfa;'>< 48h</div>
                            <div style='color:rgba(255,255,255,0.5);'>Délai moyen de réponse</div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- CTA FINAL -->
            <div style='text-align:center; padding:40px 20px 60px;'>
                <a href='/login' style='display:inline-block; padding:22px 60px; 
                                        background:linear-gradient(135deg, #4f46e5 0%, #3730a3 100%);
                                        color:white; text-decoration:none; border-radius:50px;
                                        font-size:1.4rem; font-weight:700;
                                        box-shadow:0 15px 40px rgba(79, 70, 229, 0.4);
                                        transition:all 0.3s;'>
                    🔍 Scanner mes emails gratuitement
                </a>
            </div>
            
        </div>
        """ + FOOTER
    
    # ════════════════════════════════════════════════════════════════
    # DASHBOARD CONNECTÉ
    # ════════════════════════════════════════════════════════════════
    
    active_count = Litigation.query.filter_by(user_email=session['email']).count()
    badge = f"<span style='background:linear-gradient(135deg, #ef4444, #dc2626); color:white; padding:4px 12px; border-radius:50px; font-size:0.85rem; font-weight:600; margin-left:8px;'>{active_count}</span>" if active_count > 0 else ""
    
    # Calculer les gains potentiels
    total_potential = 0
    for lit in Litigation.query.filter_by(user_email=session['email']).all():
        try:
            total_potential += extract_numeric_amount(lit.amount)
        except:
            pass
    
    return STYLE + f"""
    <div style='max-width:800px; margin:0 auto; text-align:center;'>
        
        <!-- HEADER PREMIUM -->
        <div style='margin-bottom:40px;'>
            <div style='font-size:4rem; margin-bottom:15px; 
                        text-shadow: 0 0 30px rgba(79, 70, 229, 0.5);'>⚖️</div>
            <h1 style='color:white; font-size:2.8rem; font-weight:800; margin:0 0 10px 0;
                       background: linear-gradient(135deg, #fff 0%, #a5b4fc 100%);
                       -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                       background-clip: text;'>JUSTICIO</h1>
            <p style='color:rgba(255,255,255,0.7); font-size:1.1rem; margin:0 0 5px 0;'>
                Bienvenue, <b style='color:white;'>{session.get('name')}</b>
            </p>
        </div>
        
        <!-- TAGLINE MARKETING -->
        <div style='background: linear-gradient(135deg, rgba(251, 191, 36, 0.2), rgba(245, 158, 11, 0.1));
                    border: 1px solid rgba(251, 191, 36, 0.3);
                    border-radius: 16px; padding: 20px; margin-bottom: 40px;'>
            <p style='color: #fbbf24; font-size: 1.25rem; font-weight: 600; margin: 0;'>
                💰 L'IA qui transforme vos galères en argent
            </p>
            <p style='color: rgba(255,255,255,0.6); font-size: 0.95rem; margin: 8px 0 0 0;'>
                0€ d'avance • Commission uniquement au succès
            </p>
        </div>
        
        <!-- CARTES D'ACTION -->
        <div style='display:flex; flex-wrap:wrap; justify-content:center; gap:25px; margin-bottom:40px;'>
            
            <!-- CARTE SCAN TRANSPORT -->
            <a href='/scan-all' class='action-card' onclick='showLoading("scan")'>
                <div class='icon'>✈️</div>
                <div class='title'>SCAN VOYAGES</div>
                <div class='description'>
                    Train, Avion, VTC uniquement<br>
                    <b>Retards, annulations, correspondances...</b>
                </div>
                <span class='badge badge-fast'>⚡ Analyse IA • 365 jours</span>
            </a>
            
            <!-- CARTE DÉCLARER (E-commerce + autres) -->
            <a href='/declare' class='action-card travel' onclick='showLoading("declare")'>
                <div class='icon'>📦</div>
                <div class='title'>DÉCLARER UN LITIGE</div>
                <div class='description'>
                    Colis, remboursement, e-commerce...<br>
                    <b>Déclarez manuellement tout autre litige</b>
                </div>
                <span class='badge badge-premium'>📝 Manuel</span>
            </a>
            
        </div>
        
        <!-- BOUTON DASHBOARD -->
        <a href='/dashboard' style='display:inline-flex; align-items:center; gap:10px;
                                     padding:18px 35px; background:rgba(255,255,255,0.1);
                                     backdrop-filter:blur(10px); color:white; 
                                     text-decoration:none; border-radius:50px; 
                                     font-weight:600; font-size:1.1rem;
                                     border:1px solid rgba(255,255,255,0.2);
                                     transition:all 0.3s; margin-bottom:25px;'>
            📂 MES DOSSIERS {badge}
        </a>
        
        <!-- STATS RAPIDES -->
        {"<div style='background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.3); border-radius:12px; padding:15px 25px; display:inline-block; margin-bottom:30px;'><span style='color:#10b981; font-weight:600;'>💰 " + f"{total_potential:.0f}€" + " en litiges détectés</span></div>" if total_potential > 0 else ""}
        
        <!-- FOOTER LINKS -->
        <div style='margin-top:20px;'>
            <a href='/logout' class='btn-logout'>Se déconnecter</a>
        </div>
        
        <!-- SOCIAL PROOF -->
        <div style='margin-top:50px; padding:30px; background:rgba(255,255,255,0.05); 
                    border-radius:20px; border:1px solid rgba(255,255,255,0.1);'>
            <div style='display:flex; justify-content:center; gap:40px; flex-wrap:wrap;'>
                <div style='text-align:center;'>
                    <div style='font-size:2rem; font-weight:700; color:#10b981;'>2,847€</div>
                    <div style='font-size:0.85rem; color:rgba(255,255,255,0.5);'>Récupéré ce mois</div>
                </div>
                <div style='text-align:center;'>
                    <div style='font-size:2rem; font-weight:700; color:#fbbf24;'>89%</div>
                    <div style='font-size:0.85rem; color:rgba(255,255,255,0.5);'>Taux de succès</div>
                </div>
                <div style='text-align:center;'>
                    <div style='font-size:2rem; font-weight:700; color:#a78bfa;'>< 48h</div>
                    <div style='font-size:0.85rem; color:rgba(255,255,255,0.5);'>Temps de réponse</div>
                </div>
            </div>
        </div>
        
    </div>
    """ + WA_BTN + FOOTER

@app.route("/logout")
def logout():
    """Déconnexion"""
    session.clear()
    return redirect("/")

# ========================================
# SCANNER INTELLIGENT - VERSION CORRIGÉE
# Les litiges ne sont PAS enregistrés en base lors du scan
# Ils sont stockés en session et enregistrés seulement après paiement
# ========================================

@app.route("/scan")
@app.route("/scan-ecommerce")
def scan():
    """Redirige vers /scan-all pour compatibilité"""
    return redirect("/scan-all")

# ========================================
# ✈️ SCAN VOYAGES - REDIRIGE VERS SCAN-ALL
# ========================================

@app.route("/scan-travel")
def scan_travel():
    """Redirige vers /scan-all pour compatibilité"""
    return redirect("/scan-all")

# ========================================
# ✈️ SCAN TRANSPORT UNIQUEMENT (Train/Avion/VTC)
# ========================================
# ⚠️ PIVOT STRATÉGIQUE: Le scan auto ne détecte QUE les litiges transport.
# L'e-commerce est géré exclusivement via /declare (déclaration manuelle).

# 🚀 TRANSPORT FORT - Ces mots-clés PASSENT OUTRE la blacklist e-commerce
# Si un de ces termes est détecté, on analyse TOUJOURS (priorité absolue)
TRANSPORT_STRONG_KEYWORDS = [
    # Compagnies ferroviaires (noms exacts)
    "sncf", "ouigo", "eurostar", "thalys", "tgv", "inoui", "intercités",
    "trainline", "trenitalia", "renfe", "deutsche bahn",
    # Compagnies aériennes (noms exacts)  
    "air france", "airfrance", "easyjet", "ryanair", "transavia", "vueling", "volotea",
    "lufthansa", "klm", "british airways", "tap portugal", "iberia", "swiss air",
    "emirates", "qatar airways", "turkish airlines", "norwegian", "wizzair",
    # VTC (noms exacts)
    "uber", "bolt", "kapten", "heetch", "freenow", "blablacar", "flixbus",
    # Termes TRANSPORT non ambigus
    "règlement 261", "ec 261", "règlement ue", "règlement européen",
    "bagage perdu", "lost baggage", "bagage endommagé",
    "vol annulé", "vol retardé", "flight cancelled", "flight delayed",
    "train annulé", "train retardé", "correspondance ratée", "missed connection"
]

# Mots-clés TRANSPORT génériques (utilisés si pas de blacklist)
TRANSPORT_KEYWORDS = [
    # Compagnies ferroviaires
    "sncf", "ouigo", "eurostar", "thalys", "ter", "tgv", "inoui", "intercités",
    "trainline", "trenitalia", "renfe", "deutsche bahn", "db",
    # Compagnies aériennes
    "air france", "easyjet", "ryanair", "transavia", "vueling", "volotea",
    "lufthansa", "klm", "british airways", "tap", "iberia", "swiss", "emirates",
    "qatar airways", "turkish airlines", "norwegian", "wizzair", "flybe",
    # VTC / Mobilité
    "uber", "bolt", "kapten", "heetch", "freenow", "blablacar", "flixbus",
    # Termes génériques transport
    "vol", "flight", "train", "rail", "avion", "aéroport", "airport",
    "embarquement", "boarding", "correspondance", "connection",
    "retard", "delay", "annulation", "cancel", "compensation", "indemnisation",
    "règlement 261", "ec 261", "règlement européen",
    "bagage perdu", "lost baggage", "bagage endommagé", "damaged luggage"
]

# Mots-clés E-COMMERCE (à BANNIR du scan auto SAUF si transport fort détecté)
ECOMMERCE_BLACKLIST = [
    # Termes e-commerce
    "commande", "order", "colis", "package", "livraison", "delivery",
    "panier", "cart", "achat", "purchase", "expédition", "shipment",
    # Plateformes e-commerce
    "amazon", "cdiscount", "fnac", "darty", "zalando", "asos", "zara",
    "vinted", "leboncoin", "aliexpress", "shein", "temu", "wish", "ebay",
    "rakuten", "backmarket", "boulanger", "ldlc", "materiel.net",
    "decathlon", "ikea", "leroy merlin", "castorama", "manomano",
    "veepee", "showroomprive", "asphalte", "sezane", "maje", "sandro",
    # Termes produits
    "article", "produit", "retour produit", "défectueux", "defective",
    "colis perdu", "lost package", "non reçu", "not received"
]

def is_strong_transport(text: str) -> bool:
    """
    🚀 Vérifie si le texte contient un mot-clé TRANSPORT FORT.
    Si oui, on passe outre la blacklist e-commerce.
    """
    text_lower = text.lower()
    return any(kw in text_lower for kw in TRANSPORT_STRONG_KEYWORDS)

def is_transport_email(subject: str, snippet: str, sender: str) -> bool:
    """
    Vérifie si un email concerne le TRANSPORT (et pas l'e-commerce).
    
    LOGIQUE AMÉLIORÉE:
    1. Si TRANSPORT FORT détecté → True (ignore la blacklist)
    2. Sinon, si E-COMMERCE détecté → False
    3. Sinon, si TRANSPORT générique détecté → True
    4. Sinon → False
    """
    blob = f"{subject or ''} {snippet or ''} {sender or ''}".lower()
    
    # 🚀 PRIORITÉ 1: Transport FORT → On analyse toujours
    if is_strong_transport(blob):
        return True
    
    # 🚫 PRIORITÉ 2: E-commerce détecté (et pas de transport fort) → Rejeter
    if any(kw in blob for kw in ECOMMERCE_BLACKLIST):
        return False
    
    # ✅ PRIORITÉ 3: Transport générique → Accepter
    if any(kw in blob for kw in TRANSPORT_KEYWORDS):
        return True
    
    return False

@app.route("/scan-all")
def scan_all():
    """
    ✈️ SCAN TRANSPORT V2 - Train / Avion / VTC UNIQUEMENT
    
    ⚠️ PIVOT STRATÉGIQUE: Ce scan ne détecte QUE les litiges de transport passagers.
    Les litiges e-commerce sont gérés via /declare (déclaration manuelle).
    
    Fonctionnement:
    - Query Gmail ciblée sur le transport (compagnies, retards, annulations)
    - Exclusion stricte des termes e-commerce
    - Analyse IA uniquement via analyze_litigation_strict(scan_type="travel")
    - Anti-doublon: ignore les emails déjà traités en BDD
    """
    if "credentials" not in session:
        return redirect("/login")
    
    try:
        creds = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=creds)
    except Exception as e:
        DEBUG_LOGS.append(f"❌ Scan Transport: Erreur auth Gmail - {str(e)[:50]}")
        return STYLE + f"""
        <div style='text-align:center; padding:50px;'>
            <h1 style='color:white;'>❌ Erreur d'authentification</h1>
            <p style='color:rgba(255,255,255,0.7);'>{str(e)[:100]}</p>
            <a href='/login' class='btn-success'>Se reconnecter</a>
        </div>
        """ + FOOTER
    
    # ════════════════════════════════════════════════════════════════
    # 🔒 ANTI-DOUBLON: Récupérer les IDs déjà en base pour cet utilisateur
    # ════════════════════════════════════════════════════════════════
    existing_ids = set()
    existing_cases_count = 0
    try:
        user_email = session.get('email', '')
        if user_email:
            existing_lits = Litigation.query.filter_by(user_email=user_email).all()
            existing_ids = {lit.message_id for lit in existing_lits if lit.message_id}
            existing_cases_count = len(existing_lits)
            DEBUG_LOGS.append(f"🔒 Anti-doublon: {len(existing_ids)} message_id déjà en base ({existing_cases_count} dossiers)")
    except Exception as e:
        DEBUG_LOGS.append(f"⚠️ Erreur récupération doublons: {str(e)[:50]}")
    
    # ════════════════════════════════════════════════════════════════
    # 📅 Query Gmail TRANSPORT UNIQUEMENT sur 365 jours
    # ════════════════════════════════════════════════════════════════
    from datetime import timedelta
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y/%m/%d")
    
    # Query ciblée TRANSPORT - Exclut explicitement l'e-commerce
    query = f"""
    label:INBOX 
    after:{one_year_ago}
    (
        sncf OR ouigo OR eurostar OR thalys OR tgv OR inoui OR trainline
        OR "air france" OR easyjet OR ryanair OR transavia OR vueling OR volotea
        OR lufthansa OR klm OR "british airways" OR wizzair OR flixbus
        OR uber OR bolt OR blablacar OR heetch
        OR (vol AND (retard OR annulation OR delay OR cancel))
        OR (train AND (retard OR annulation OR delay OR cancel))
        OR (flight AND (delay OR cancelled OR compensation))
        OR "bagage perdu" OR "lost baggage"
        OR compensation OR indemnisation
        OR "règlement 261" OR "ec 261"
    )
    -commande -colis -livraison -order -delivery -package -shipment
    -amazon -zalando -fnac -darty -shein -temu -aliexpress -vinted -cdiscount
    -asphalte -asos -zara -wish -ebay -leboncoin -rakuten -backmarket
    -category:promotions 
    -category:social
    -subject:"MISE EN DEMEURE"
    -subject:newsletter
    -subject:unsubscribe
    """
    
    print("\n" + "="*70)
    print("✈️ SCAN TRANSPORT V2 - DÉMARRAGE")
    print("🚫 E-commerce exclu - Transport passagers uniquement")
    print(f"🔒 {len(existing_ids)} emails déjà traités seront ignorés")
    print("="*70)
    
    DEBUG_LOGS.append(f"✈️ SCAN TRANSPORT lancé - Mode TRANSPORT UNIQUEMENT")
    
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=150).execute()
        messages = results.get('messages', [])
    except Exception as e:
        DEBUG_LOGS.append(f"❌ Scan Transport: Erreur liste Gmail - {str(e)[:50]}")
        return STYLE + f"<h1 style='color:white;'>Erreur lecture Gmail : {str(e)[:100]}</h1><a href='/login'>Se reconnecter</a>" + FOOTER
    
    print(f"📧 {len(messages)} emails transport trouvés")
    
    # ════════════════════════════════════════════════════════════════
    # 🔄 Analyse des emails TRANSPORT
    # ════════════════════════════════════════════════════════════════
    
    detected_litigations = []
    emails_scanned = 0
    emails_skipped = 0
    emails_skipped_ecommerce = 0
    emails_skipped_existing = 0  # Compteur pour les doublons BDD
    emails_errors = 0
    ai_calls = 0
    MAX_AI_CALLS = 40
    
    for msg in messages:
        try:
            # ════════════════════════════════════════════════════════════════
            # 🔒 ANTI-DOUBLON: Ignorer si déjà en base
            # ════════════════════════════════════════════════════════════════
            if msg['id'] in existing_ids:
                emails_skipped_existing += 1
                DEBUG_LOGS.append(f"⏩ Doublon ignoré (déjà en BDD): {msg['id'][:12]}...")
                continue
            
            # Récupérer metadata (Subject, From, To, snippet)
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata',
                                                       metadataHeaders=['Subject', 'From', 'To']).execute()
            
            headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
            subject = headers.get('Subject', '')
            sender = headers.get('From', '')
            to_field = headers.get('To', '')
            snippet = msg_data.get('snippet', '')
            
            emails_scanned += 1
            
            # ════════════════════════════════════════════════════════════════
            # 🛡️ FILTRAGE LOCAL (GRATUIT) - LOGIQUE AMÉLIORÉE
            # ════════════════════════════════════════════════════════════════
            
            blob = f"{subject} {snippet} {sender}".lower()
            
            # 🚀 PRIORITÉ 1: Vérifier si TRANSPORT FORT (SNCF, Air France, etc.)
            # Si oui, on passe outre TOUTE la blacklist e-commerce
            is_strong = is_strong_transport(blob)
            
            if is_strong:
                DEBUG_LOGS.append(f"🚀 Transport FORT détecté: {subject[:40]}...")
            else:
                # 🚫 BLOCAGE E-COMMERCE - Seulement si PAS de transport fort
                if any(kw in blob for kw in ECOMMERCE_BLACKLIST):
                    emails_skipped_ecommerce += 1
                    DEBUG_LOGS.append(f"🚫 E-commerce ignoré: {subject[:40]}...")
                    continue
                
                # Vérifier que c'est bien du transport (générique)
                if not is_transport_email(subject, snippet, sender):
                    emails_skipped += 1
                    continue
            
            # Ignorer nos propres mises en demeure
            if "mise en demeure" in blob and "justicio" in blob:
                emails_skipped += 1
                continue
            
            # Ignorer newsletters/promos
            if any(kw in blob for kw in ["newsletter", "unsubscribe", "désinscri", "promo", "offre exclusive"]):
                emails_skipped += 1
                continue
            
            # ════════════════════════════════════════════════════════════════
            # 🎫 DÉTECTION VOUCHER/AVOIR - PRIORITÉ ABSOLUE
            # Si l'email contient des mots de voucher → TOUJOURS envoyer à l'IA
            # ════════════════════════════════════════════════════════════════
            has_voucher_keywords = any(kw in blob for kw in VOUCHER_KEYWORDS)
            
            if has_voucher_keywords and is_strong:
                DEBUG_LOGS.append(f"🎫 VOUCHER + Transport détecté → Envoi à l'IA: {subject[:40]}...")
            
            # ⚠️ Ignorer SUCCESS (vrai virement) - SAUF SI :
            # - Transport fort détecté
            # - OU mots-clés voucher détectés (compensation, bon d'achat, etc.)
            if any(kw in blob for kw in KEYWORDS_SUCCESS):
                if is_strong or has_voucher_keywords:
                    # Transport fort OU voucher → On analyse quand même
                    DEBUG_LOGS.append(f"🔍 Succès apparent mais transport/voucher → Envoi à l'IA: {subject[:40]}...")
                else:
                    # Vrai succès sans transport fort ni voucher → Skip
                    emails_skipped += 1
                    DEBUG_LOGS.append(f"✅ Vrai virement détecté, skip: {subject[:40]}...")
                    continue
            
            # Ignorer factures normales sans litige (sauf si transport fort OU voucher)
            if not is_strong and not has_voucher_keywords and is_invoice_without_dispute(subject, snippet):
                emails_skipped += 1
                continue
            
            # ════════════════════════════════════════════════════════════════
            # 🤖 ANALYSE IA TRANSPORT (si quota pas atteint)
            # ════════════════════════════════════════════════════════════════
            
            if ai_calls >= MAX_AI_CALLS:
                DEBUG_LOGS.append(f"⚠️ Quota IA atteint ({MAX_AI_CALLS}), arrêt")
                break
            
            # Récupérer le corps complet
            try:
                full_msg = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
                body_text = safe_extract_body_text(full_msg)
            except:
                body_text = snippet
            
            # Appeler l'IA - UNIQUEMENT analyze_litigation_strict en mode TRAVEL
            ai_calls += 1
            result = analyze_litigation_strict(body_text, subject, sender, to_field, scan_type="travel")
            
            # Vérifier si litige TRANSPORT détecté
            if result.get("is_valid") and result.get("litige"):
                # Double vérification: L'IA a-t-elle détecté du transport ?
                company = result.get("company", "").lower()
                
                # Rejeter si l'IA a retourné une entreprise e-commerce par erreur
                ecommerce_companies = ["amazon", "zalando", "fnac", "darty", "cdiscount", "shein", "temu", "asphalte", "vinted"]
                if any(ec in company for ec in ecommerce_companies):
                    DEBUG_LOGS.append(f"🚫 IA a détecté e-commerce ({company}), ignoré")
                    continue
                
                # Éviter les doublons
                is_duplicate = False
                for existing in detected_litigations:
                    if existing.get('message_id') == msg['id']:
                        is_duplicate = True
                        break
                    if existing.get('company', '').lower() == result.get('company', '').lower() and \
                       existing.get('proof', '')[:50] == result.get('proof', '')[:50]:
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    detected_litigations.append({
                        "company": result.get("company", "Transporteur"),
                        "amount": result.get("amount", "À compléter"),
                        "law": result.get("law", "Règlement CE 261/2004"),
                        "proof": result.get("proof", subject[:100]),
                        "message_id": msg['id'],
                        "category": "travel",  # Toujours travel
                        "sender": sender,
                        "to_field": to_field
                    })
                    DEBUG_LOGS.append(f"✅ LITIGE TRANSPORT: {result.get('company')} - {result.get('amount')}")
            else:
                # 🔍 DEBUG: Logger les rejets IA pour comprendre pourquoi
                reason = result.get('reason', 'Pas de motif fourni')
                company_guess = result.get('company', 'Inconnu')
                is_valid = result.get('is_valid', False)
                has_litige = result.get('litige', False)
                
                if not is_valid:
                    DEBUG_LOGS.append(f"❌ REJET IA (invalide): {subject[:35]}... → {reason}")
                    print(f"❌ REJET IA (invalide) [{company_guess}]: {reason}")
                elif not has_litige:
                    DEBUG_LOGS.append(f"⚪ REJET IA (pas litige): {subject[:35]}... → {reason}")
                    print(f"⚪ REJET IA (pas litige) [{company_guess}]: {reason}")
        
        except Exception as e:
            emails_errors += 1
            tb_str = traceback.format_exc()[:600]
            DEBUG_LOGS.append(f"❌ Erreur email {msg.get('id', '?')[:8]}: {type(e).__name__}: {str(e)[:100]}")
            DEBUG_LOGS.append(f"   📋 Traceback: {tb_str}")
            continue
    
    # ════════════════════════════════════════════════════════════════
    # 💾 Stocker en session
    # ════════════════════════════════════════════════════════════════
    
    session['detected_litigations'] = detected_litigations
    
    # Calculer le gain total
    total_gain = 0
    for lit in detected_litigations:
        if is_valid_euro_amount(lit.get('amount', '')):
            total_gain += extract_numeric_amount(lit['amount'])
    
    session['total_gain'] = total_gain
    
    new_cases_count = len(detected_litigations)
    
    print(f"\n📊 RÉSUMÉ SCAN TRANSPORT")
    print(f"   Emails analysés: {emails_scanned}")
    print(f"   Emails ignorés (non-transport): {emails_skipped}")
    print(f"   Emails e-commerce bloqués: {emails_skipped_ecommerce}")
    print(f"   Emails déjà traités (doublons): {emails_skipped_existing}")
    print(f"   Erreurs: {emails_errors}")
    print(f"   Appels IA: {ai_calls}")
    print(f"   Litiges transport détectés: {new_cases_count}")
    print(f"   Gain potentiel: {total_gain}€")
    
    # ════════════════════════════════════════════════════════════════
    # 🎨 Générer l'interface résultat TRANSPORT - DESIGN V2 "TICKET DE VOL"
    # ════════════════════════════════════════════════════════════════
    
    html_cards = ""
    for i, lit in enumerate(detected_litigations):
        company = lit.get('company', 'Transporteur')
        amount_display = lit.get('amount', 'À compléter')
        amount_editable = not is_valid_euro_amount(amount_display)
        proof = lit.get('proof', '')[:120]
        law = lit.get('law', 'Règlement UE 261/2004')
        
        # Déterminer l'icône selon le type de transport
        company_lower = company.lower()
        if any(x in company_lower for x in ['sncf', 'tgv', 'ouigo', 'eurostar', 'thalys', 'train', 'ter', 'inoui']):
            transport_icon = "🚄"
            transport_type = "TRAIN"
        elif any(x in company_lower for x in ['uber', 'bolt', 'kapten', 'vtc', 'taxi', 'heetch']):
            transport_icon = "🚗"
            transport_type = "VTC"
        else:
            transport_icon = "✈️"
            transport_type = "VOL"
        
        # Montant : input si éditable, sinon affichage
        if amount_editable:
            amount_html = f"""
                <input type='number' id='amount-{i}' value='' placeholder='€' 
                       style='width:80px; padding:8px; border-radius:8px; border:2px solid #10b981; 
                              background:rgba(16,185,129,0.1); color:#10b981; font-size:1.5rem; 
                              font-weight:700; text-align:center;'
                       onchange='updateAmount({i})'>
                <span style='color:#10b981; font-size:1.5rem; font-weight:700;'>€</span>
            """
        else:
            amount_html = f"<span style='color:#10b981; font-size:2rem; font-weight:700;'>{amount_display}</span>"
        
        html_cards += f"""
        <!-- CARTE TICKET DE VOL #{i+1} -->
        <div style='background:white; border-radius:16px; margin-bottom:20px; overflow:hidden;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.15), 0 2px 10px rgba(0,0,0,0.1);
                    position:relative;'>
            
            <!-- EN-TÊTE : Compagnie + Montant -->
            <div style='background:linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%); 
                        padding:20px 25px; display:flex; justify-content:space-between; align-items:center;'>
                <div style='display:flex; align-items:center; gap:15px;'>
                    <span style='font-size:2.5rem;'>{transport_icon}</span>
                    <div>
                        <div style='color:rgba(255,255,255,0.6); font-size:0.75rem; text-transform:uppercase; letter-spacing:1px;'>
                            {transport_type}
                        </div>
                        <div style='color:white; font-size:1.4rem; font-weight:700;'>{company}</div>
                    </div>
                </div>
                <div style='text-align:right;'>
                    <div style='color:rgba(255,255,255,0.6); font-size:0.7rem; text-transform:uppercase; margin-bottom:4px;'>
                        Indemnisation estimée
                    </div>
                    {amount_html}
                </div>
            </div>
            
            <!-- SÉPARATEUR PERFORÉ -->
            <div style='position:relative; height:20px; background:#f8fafc;'>
                <div style='position:absolute; left:-10px; top:50%; transform:translateY(-50%); 
                            width:20px; height:20px; background:#0f172a; border-radius:50%;'></div>
                <div style='position:absolute; right:-10px; top:50%; transform:translateY(-50%); 
                            width:20px; height:20px; background:#0f172a; border-radius:50%;'></div>
                <div style='border-top:2px dashed #cbd5e1; position:absolute; top:50%; left:20px; right:20px;'></div>
            </div>
            
            <!-- CORPS : Preuve + Loi -->
            <div style='padding:20px 25px; background:#f8fafc;'>
                <!-- Preuve / Pourquoi -->
                <div style='margin-bottom:15px;'>
                    <div style='color:#64748b; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px;'>
                        📝 Motif du litige
                    </div>
                    <p style='color:#334155; font-size:0.95rem; font-style:italic; margin:0; line-height:1.5;'>
                        "{proof}..."
                    </p>
                </div>
                
                <!-- Base légale -->
                <div style='display:flex; align-items:center; gap:8px; padding:12px 15px; 
                            background:rgba(99,102,241,0.1); border-radius:10px; border-left:3px solid #6366f1;'>
                    <span style='font-size:1.2rem;'>⚖️</span>
                    <div>
                        <div style='color:#6366f1; font-size:0.7rem; text-transform:uppercase; font-weight:600;'>Base légale</div>
                        <div style='color:#4338ca; font-size:0.9rem; font-weight:500;'>{law}</div>
                    </div>
                </div>
            </div>
            
            <!-- FOOTER : Explication -->
            <div style='padding:12px 25px; background:#f1f5f9; border-top:1px solid #e2e8f0;'>
                <p style='margin:0; color:#94a3b8; font-size:0.75rem; text-align:center;'>
                    💡 Montant estimé selon la durée du retard et la distance parcourue (Règlement CE 261/2004)
                </p>
            </div>
            
            <!-- NUMÉRO DE DOSSIER -->
            <div style='position:absolute; top:25px; right:25px; background:rgba(255,255,255,0.2); 
                        padding:4px 10px; border-radius:5px;'>
                <span style='color:rgba(255,255,255,0.8); font-size:0.7rem; font-family:monospace;'>#{i+1}</span>
            </div>
        </div>
        """
    
    # Debug HTML
    debug_html = ""
    if DEBUG_LOGS:
        debug_html = f"""
        <details style='margin-top:30px; background:rgba(0,0,0,0.3); padding:15px; border-radius:10px;'>
            <summary style='color:#fbbf24; cursor:pointer;'>🔧 Debug ({len(DEBUG_LOGS)} logs)</summary>
            <pre style='color:rgba(255,255,255,0.6); font-size:0.75rem; white-space:pre-wrap; margin-top:10px;'>
{chr(10).join(DEBUG_LOGS[-50:])}
            </pre>
        </details>
        """
    
    # JavaScript pour mise à jour des montants
    update_script = """
    <script>
    function updateAmount(index) {
        const input = document.getElementById('amount-' + index);
        const amount = input.value;
        if (amount && amount > 0) {
            fetch('/update-detected-amount', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({index: index, amount: amount})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    input.style.borderColor = '#10b981';
                    input.style.background = 'rgba(16,185,129,0.2)';
                    const totalEl = document.getElementById('total-gain');
                    if (totalEl) totalEl.textContent = data.total + '€';
                }
            });
        }
    }
    </script>
    """
    
    if new_cases_count > 0:
        return STYLE + update_script + f"""
        <div style='text-align:center; padding:30px;'>
            <div style='font-size:4rem; margin-bottom:15px;'>✈️</div>
            <h1 style='color:white; margin-bottom:10px;'>Scan Transport Terminé !</h1>
            <p style='color:#f59e0b; font-size:1.4rem; font-weight:600;'>
                {new_cases_count} litige(s) de transport détecté(s) !
            </p>
            <p style='color:rgba(255,255,255,0.7);'>
                💰 Gain potentiel: <b id='total-gain' style='color:#10b981; font-size:1.6rem;'>{total_gain:.0f}€</b>
            </p>
            <p style='color:rgba(255,255,255,0.5); font-size:0.85rem;'>
                📧 {emails_scanned} emails analysés • 🤖 {ai_calls} appels IA
                {f" • 🚫 {emails_skipped_ecommerce} e-commerce ignorés" if emails_skipped_ecommerce > 0 else ""}
                {f" • ⚠️ {emails_errors} erreurs" if emails_errors > 0 else ""}
            </p>
        </div>
        
        <div style='max-width:500px; margin:0 auto; padding:0 15px;'>
            {html_cards}
        </div>
        
        <div style='text-align:center; margin:40px 0;'>
            <a href='/setup-payment' class='btn-success' style='padding:20px 50px; font-size:1.2rem;
                                                                 background:linear-gradient(135deg, #f59e0b, #d97706);
                                                                 box-shadow:0 15px 40px rgba(245, 158, 11, 0.4);'>
                🚀 RÉCUPÉRER MES {total_gain:.0f}€
            </a>
            <p style='color:rgba(255,255,255,0.5); margin-top:15px; font-size:0.9rem;'>
                Commission 25% uniquement en cas de succès
            </p>
        </div>
        
        <div style='text-align:center; margin-top:20px;'>
            <p style='color:rgba(255,255,255,0.4); font-size:0.85rem;'>
                📦 Un litige e-commerce ? <a href='/declare' style='color:#a78bfa;'>Déclarez-le manuellement</a>
            </p>
        </div>
        
        <!-- BOUTON RESET avec confirmation -->
        <div style='text-align:center; margin-top:40px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.1);'>
            <a href='#' onclick="if(confirm('⚠️ ATTENTION\\n\\nCette action va :\\n- Effacer les résultats du scan\\n- SUPPRIMER vos dossiers de la base de données\\n\\nÊtes-vous sûr ?')) window.location='/reset-scan';" 
               style='color:rgba(255,255,255,0.4); font-size:0.8rem; text-decoration:none;
                      display:inline-flex; align-items:center; gap:5px;
                      padding:8px 15px; border-radius:8px; transition:all 0.2s;'
               onmouseover="this.style.background='rgba(239,68,68,0.2)'; this.style.color='#f87171';"
               onmouseout="this.style.background='transparent'; this.style.color='rgba(255,255,255,0.4)';">
                🗑️ Effacer tout et rescanner (supprime les dossiers)
            </a>
        </div>
        """ + debug_html + WA_BTN + FOOTER
    else:
        # ════════════════════════════════════════════════════════════════
        # 📭 AUCUN NOUVEAU LITIGE - Message intelligent selon le contexte
        # ════════════════════════════════════════════════════════════════
        
        # Cas 1: Des dossiers existent déjà en base
        if existing_cases_count > 0:
            return STYLE + f"""
            <div style='text-align:center; padding:50px;'>
                <div style='font-size:4rem; margin-bottom:20px;'>✅</div>
                <h1 style='color:white;'>Aucun nouveau litige détecté</h1>
                
                <div style='background:linear-gradient(135deg, rgba(16,185,129,0.2), rgba(16,185,129,0.1)); 
                            padding:25px; border-radius:15px; margin:25px auto; max-width:450px;
                            border:1px solid rgba(16,185,129,0.3);'>
                    <p style='color:#10b981; font-size:1.1rem; margin:0 0 10px 0; font-weight:600;'>
                        📂 Vous avez déjà {existing_cases_count} dossier(s) en cours
                    </p>
                    <p style='color:rgba(255,255,255,0.6); font-size:0.9rem; margin:0;'>
                        {f"({emails_skipped_existing} email(s) déjà traité(s) ignoré(s))" if emails_skipped_existing > 0 else "Tous vos litiges sont en cours de traitement."}
                    </p>
                </div>
                
                <p style='color:rgba(255,255,255,0.5); font-size:0.85rem;'>
                    📧 {emails_scanned} emails analysés • 🤖 {ai_calls} appels IA
                    {f" • 🔒 {emails_skipped_existing} doublons ignorés" if emails_skipped_existing > 0 else ""}
                </p>
                
                <div style='display:flex; gap:15px; justify-content:center; flex-wrap:wrap; margin-top:25px;'>
                    <a href='/dashboard' class='btn-success' style='padding:15px 30px; font-size:1.1rem;'>
                        📂 Voir mes dossiers existants
                    </a>
                </div>
                
                <div style='margin-top:25px;'>
                    <a href='/' style='color:rgba(255,255,255,0.4); font-size:0.85rem;'>← Retour à l'accueil</a>
                </div>
            </div>
            """ + debug_html + FOOTER
        
        # Cas 2: Aucun dossier existant, vraiment rien trouvé
        else:
            return STYLE + f"""
            <div style='text-align:center; padding:50px;'>
                <div style='font-size:4rem; margin-bottom:20px;'>✈️</div>
                <h1 style='color:white;'>Aucun litige de transport détecté</h1>
                <p style='color:rgba(255,255,255,0.6);'>
                    Nous avons analysé {emails_scanned} emails sur les 12 derniers mois.<br>
                    Aucun retard/annulation de vol ou train n'a été identifié.
                </p>
                <p style='color:rgba(255,255,255,0.5); font-size:0.85rem;'>
                    📧 {emails_skipped} emails ignorés • 🤖 {ai_calls} appels IA
                    {f" • 🚫 {emails_skipped_ecommerce} e-commerce ignorés" if emails_skipped_ecommerce > 0 else ""}
                </p>
                <br>
                <div style='display:flex; gap:15px; justify-content:center; flex-wrap:wrap;'>
                    <a href='/' class='btn-success'>Retour à l'accueil</a>
                    <a href='/declare' class='btn-success' style='background:#a78bfa;'>📦 Déclarer un litige e-commerce</a>
                </div>
            </div>
            """ + debug_html + FOOTER
# ========================================
# MISE À JOUR MONTANT EN SESSION (avant paiement)
# ========================================

@app.route("/update-detected-amount", methods=["POST"])
def update_detected_amount():
    """Met à jour le montant d'un litige détecté (en session, pas encore en base)"""
    if "email" not in session:
        return jsonify({"error": "Non authentifié"}), 401
    
    data = request.json
    index = data.get("index")
    amount = data.get("amount")
    
    if index is None or not amount:
        return jsonify({"error": "Données manquantes"}), 400
    
    detected = session.get('detected_litigations', [])
    if index < 0 or index >= len(detected):
        return jsonify({"error": "Index invalide"}), 400
    
    # Mettre à jour le montant
    detected[index]['amount'] = f"{amount}€"
    session['detected_litigations'] = detected
    
    # Recalculer le total
    total = 0
    for lit in detected:
        if is_valid_euro_amount(lit['amount']):
            total += extract_numeric_amount(lit['amount'])
    
    session['total_gain'] = total
    
    return jsonify({"success": True, "amount": f"{amount}€", "total": total}), 200

# ========================================
# 🗑️ RESET SCAN - Effacer les résultats (SESSION + BDD)
# ========================================

@app.route("/reset-scan")
def reset_scan():
    """
    🗑️ HARD RESET - Efface TOUT :
    - Vide la session (detected_litigations, total_gain)
    - Vide les logs de debug
    - SUPPRIME TOUS les litiges de l'utilisateur en base de données
    
    ⚠️ Action destructive mais nécessaire pour le mode test/dev.
    """
    global DEBUG_LOGS
    deleted_count = 0
    
    # 1. Effacer les données de scan en session
    if 'detected_litigations' in session:
        del session['detected_litigations']
    if 'total_gain' in session:
        del session['total_gain']
    
    # 2. HARD DELETE - Supprimer TOUS les litiges en BDD pour cet utilisateur
    user_email = session.get('email')
    if user_email:
        try:
            # Méthode bulk delete (plus efficace)
            deleted_count = Litigation.query.filter_by(user_email=user_email).delete()
            db.session.commit()
            
            print(f"🗑️ HARD RESET: {deleted_count} litige(s) supprimé(s) de la BDD pour {user_email}")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Erreur suppression litiges: {str(e)[:100]}")
            # On continue quand même
    
    # 3. Vider les logs de debug pour un scan propre
    DEBUG_LOGS = []
    DEBUG_LOGS.append(f"🗑️ HARD RESET: session vidée + {deleted_count} litige(s) supprimé(s) de la BDD")
    
    # Rediriger vers l'accueil avec message flash
    return redirect("/")

# ========================================
# MISE À JOUR MONTANT (pour dossiers déjà en base)
# ========================================

@app.route("/update-amount", methods=["POST"])
def update_amount():
    """Met à jour le montant d'un litige déjà en base"""
    if "email" not in session:
        return jsonify({"error": "Non authentifié"}), 401
    
    data = request.json
    lit_id = data.get("id")
    amount = data.get("amount")
    
    if not lit_id or not amount:
        return jsonify({"error": "Données manquantes"}), 400
    
    lit = Litigation.query.get(lit_id)
    if not lit or lit.user_email != session['email']:
        return jsonify({"error": "Non autorisé"}), 403
    
    # Formater le montant avec le symbole euro
    lit.amount = f"{amount}€"
    lit.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({"success": True, "amount": lit.amount}), 200

# ========================================
# DASHBOARD
# ========================================

@app.route("/dashboard")
def dashboard():
    """Tableau de bord des litiges"""
    if "credentials" not in session:
        return redirect("/login")
    
    cases = Litigation.query.filter_by(user_email=session['email']).order_by(Litigation.created_at.desc()).all()
    
    html_rows = ""
    for case in cases:
        # ════════════════════════════════════════════════════════════════
        # GESTION DES STATUTS - Incluant Partiels, Bons d'achat et Annulations
        # ════════════════════════════════════════════════════════════════
        
        if case.status == "Remboursé":
            # Remboursement CASH complet
            color = "#10b981"  # Vert
            status_text = "✅ REMBOURSÉ - Commission prélevée"
            status_icon = "✅"
        
        elif case.status.startswith("Remboursé (Partiel:"):
            # Remboursement PARTIEL - Extraire les montants pour affichage
            color = "#f97316"  # Orange
            status_text = "⚠️ REMBOURSÉ PARTIELLEMENT - Com. ajustée"
            status_icon = "⚠️"
        
        elif case.status.startswith("Résolu (Bon d'achat:"):
            # BON D'ACHAT / VOUCHER - Pas de commission
            color = "#3b82f6"  # Bleu
            status_text = "🎫 BON D'ACHAT - Dossier fermé"
            status_icon = "🎫"
        
        elif case.status == "Annulé (sans débit)":
            # ANNULATION sans transaction financière - Pas de commission
            color = "#8b5cf6"  # Violet
            status_text = "🚫 ANNULÉ - Aucun débit"
            status_icon = "🚫"
        
        elif case.status == "En attente de remboursement":
            color = "#f59e0b"  # Jaune/Orange
            status_text = "⏳ En attente de remboursement"
            status_icon = "⏳"
        
        elif case.status == "En attente d'analyse":
            # Litige MANUEL en attente d'analyse IA
            color = "#0ea5e9"  # Bleu clair
            status_text = "🔬 En attente d'analyse IA"
            status_icon = "🔬"
        
        elif case.status in ["Envoyé", "En cours"]:
            color = "#8b5cf6"  # Violet
            status_text = "📧 Mise en demeure envoyée"
            status_icon = "📧"
        
        elif case.status == "En cours juridique":
            # Mise en demeure envoyée, attente de réponse
            color = "#3b82f6"  # Bleu
            status_text = "⚖️ En cours juridique"
            status_icon = "⚖️"
        
        else:
            color = "#94a3b8"  # Gris
            status_text = "🔍 Détecté - En attente d'action"
            status_icon = "🔍"
        
        # Afficher le statut brut pour les partiels/vouchers (avec le montant)
        detail_text = ""
        if "Partiel:" in case.status or "Bon d'achat:" in case.status:
            # Extraire la partie entre parenthèses
            import re
            match = re.search(r'\((.*?)\)', case.status)
            if match:
                detail_text = f"<div style='font-size:0.75rem; color:{color}; margin-top:3px;'>({match.group(1)})</div>"
        
        # Badge source (SCAN vs MANUAL)
        source = getattr(case, 'source', 'SCAN') or 'SCAN'
        source_badge = ""
        if source == "MANUAL":
            source_badge = "<span style='font-size:0.65rem; background:#dbeafe; color:#1d4ed8; padding:2px 6px; border-radius:4px; margin-left:8px;'>✍️ Manuel</span>"
        
        # Afficher merchant_email si trouvé (Agent Détective)
        merchant_email = getattr(case, 'merchant_email', None)
        merchant_badge = ""
        if merchant_email:
            merchant_badge = f"<div style='font-size:0.75rem; color:#059669; margin-top:3px;'>📧 {merchant_email}</div>"
        
        # Afficher la date d'envoi de mise en demeure si envoyée
        legal_notice_sent = getattr(case, 'legal_notice_sent', False)
        legal_notice_date = getattr(case, 'legal_notice_date', None)
        legal_notice_badge = ""
        if legal_notice_sent and legal_notice_date:
            date_str = legal_notice_date.strftime("%d/%m/%Y à %H:%M")
            legal_notice_badge = f"<div style='font-size:0.75rem; color:#3b82f6; margin-top:3px;'>⚖️ Envoyé le {date_str}</div>"
        
        # Bouton Éditer/Compléter si le dossier n'est pas finalisé
        edit_button = ""
        finalized_statuses = ["En cours juridique", "Remboursé", "Annulé (sans débit)"]
        is_finalized = case.status in finalized_statuses or case.status.startswith("Remboursé (") or case.status.startswith("Résolu (")
        
        if not is_finalized:
            # Dossier modifiable - afficher le bouton
            if not merchant_email:
                edit_label = "✏️ Compléter"
                edit_tooltip = "Ajouter l'email du marchand"
            else:
                edit_label = "📧 Renvoyer"
                edit_tooltip = "Modifier et renvoyer la mise en demeure"
            
            edit_button = f"""
                <a href='/edit_case/{case.id}' 
                   style='font-size:0.75rem; color:#3b82f6; text-decoration:none; margin-right:15px;'
                   title='{edit_tooltip}'>
                    {edit_label}
                </a>
            """
        
        html_rows += f"""
        <div style='background:white; padding:20px; margin-bottom:15px; border-radius:15px; 
                    border-left:5px solid {color}; box-shadow:0 2px 5px rgba(0,0,0,0.05); 
                    display:flex; justify-content:space-between; align-items:center;'>
            <div>
                <div style='font-weight:bold; font-size:1.1rem; color:#1e293b'>
                    {case.company.upper()} {source_badge}
                </div>
                <div style='font-size:0.9rem; color:#64748b'>
                    {case.subject[:50]}...
                </div>
                <div style='font-size:0.8rem; color:#94a3b8; margin-top:5px;'>
                    ⚖️ {case.law}
                </div>
                {merchant_badge}
                {legal_notice_badge}
            </div>
            <div style='text-align:right;'>
                <div style='font-size:1.2rem; font-weight:bold; color:{color}'>
                    {case.amount}
                </div>
                <div style='font-size:0.8rem; background:{color}20; color:{color}; 
                            padding:3px 8px; border-radius:5px; display:inline-block; margin-top:5px;'>
                    {status_text}
                </div>
                {detail_text}
                <div style='margin-top:8px;'>
                    {edit_button}
                    <a href='/delete-case/{case.id}' 
                       onclick="return confirm('🗑️ Supprimer ce dossier {case.company.upper()} ?\\n\\nCette action est irréversible.');"
                       style='font-size:0.75rem; color:#dc2626; text-decoration:none; opacity:0.6;'
                       onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.6'">
                        🗑️ Supprimer
                    </a>
                </div>
            </div>
        </div>
        """
    
    if not html_rows:
        html_rows = "<p style='text-align:center; color:#94a3b8; padding:40px;'>Aucun dossier enregistré.</p>"
    
    return STYLE + f"""
    <div style='max-width:600px; margin:0 auto;'>
        <h1>📂 Mes Dossiers</h1>
        <div style='margin-bottom:100px;'>
            {html_rows}
        </div>
        <div class='sticky-footer'>
            <a href='/scan-ecommerce' class='btn-success' style='background:#4f46e5; margin-right:10px;'>
                📦 E-COMMERCE
            </a>
            <a href='/scan-travel' class='btn-success' style='background:#f59e0b; margin-right:10px;'>
                ✈️ VOYAGES
            </a>
            <a href='/declare' class='btn-success' style='background:#10b981; margin-right:10px;'>
                ✍️ DÉCLARER
            </a>
            <a href='/' class='btn-logout'>Retour</a>
        </div>
    </div>
    """ + FOOTER

# ========================================
# ÉDITION MANUELLE D'UN DOSSIER
# ========================================

@app.route("/edit_case/<int:case_id>", methods=["GET", "POST"])
def edit_case(case_id):
    """
    ✏️ Permet de modifier un dossier et d'envoyer manuellement la mise en demeure
    
    Fonctionnalités :
    - Modifier l'email du marchand (si Agent Détective a échoué)
    - Corriger le montant
    - Envoyer/Renvoyer la mise en demeure
    """
    if "email" not in session:
        return redirect("/login")
    
    # Récupérer le dossier
    case = Litigation.query.filter_by(id=case_id, user_email=session['email']).first()
    
    if not case:
        return STYLE + """
        <div style='text-align:center; padding:50px;'>
            <h1>❌ Dossier introuvable</h1>
            <p>Ce dossier n'existe pas ou ne vous appartient pas.</p>
            <br>
            <a href='/dashboard' class='btn-success'>📂 Retour au dashboard</a>
        </div>
        """ + FOOTER
    
    user = User.query.filter_by(email=session['email']).first()
    
    # ════════════════════════════════════════════════════════════════
    # TRAITEMENT DU FORMULAIRE (POST)
    # ════════════════════════════════════════════════════════════════
    
    if request.method == "POST":
        # Récupérer les nouvelles valeurs
        new_merchant_email = request.form.get("merchant_email", "").strip()
        new_amount = request.form.get("amount", "").strip()
        send_notice = request.form.get("send_notice") == "on"
        
        # Mise à jour de l'email marchand
        old_email = case.merchant_email
        if new_merchant_email and '@' in new_merchant_email:
            case.merchant_email = new_merchant_email
            case.merchant_email_source = "Manuel"
            DEBUG_LOGS.append(f"✏️ Edit: Email marchand modifié: {old_email} → {new_merchant_email}")
        
        # Mise à jour du montant
        if new_amount:
            try:
                # Nettoyer et parser le montant
                amount_clean = new_amount.replace('€', '').replace(',', '.').strip()
                amount_float = float(amount_clean)
                case.amount = f"{amount_float:.2f}€"
                case.amount_float = amount_float
                DEBUG_LOGS.append(f"✏️ Edit: Montant modifié → {amount_float:.2f}€")
            except:
                pass
        
        db.session.commit()
        
        # ════════════════════════════════════════════════════════════════
        # ENVOI DE LA MISE EN DEMEURE (Si demandé et email présent)
        # ════════════════════════════════════════════════════════════════
        
        notice_result = None
        if send_notice and case.merchant_email:
            DEBUG_LOGS.append(f"⚖️ Edit: Envoi manuel de mise en demeure à {case.merchant_email}")
            notice_result = send_legal_notice(case, user)
            
            if notice_result["success"]:
                # Notification Telegram
                send_telegram_notif(f"📧 MISE EN DEMEURE MANUELLE 📧\n\n🏪 {case.company.upper()}\n💰 {case.amount}\n📧 {case.merchant_email}\n👤 {session['email']}\n\n⚖️ Envoi manuel réussi!")
        
        # Message de succès
        if notice_result and notice_result["success"]:
            success_message = f"""
            <div style='background:#d1fae5; padding:15px; border-radius:10px; margin-bottom:20px;
                        border-left:4px solid #10b981;'>
                <p style='margin:0; color:#065f46;'>
                    <b>✅ Mise en demeure envoyée avec succès !</b><br>
                    <span style='font-size:0.9rem;'>Destinataire : {case.merchant_email}</span>
                </p>
            </div>
            """
        elif notice_result and not notice_result["success"]:
            success_message = f"""
            <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:20px;
                        border-left:4px solid #f59e0b;'>
                <p style='margin:0; color:#92400e;'>
                    <b>⚠️ Dossier mis à jour, mais erreur d'envoi :</b><br>
                    <span style='font-size:0.9rem;'>{notice_result['message']}</span>
                </p>
            </div>
            """
        else:
            success_message = """
            <div style='background:#dbeafe; padding:15px; border-radius:10px; margin-bottom:20px;
                        border-left:4px solid #3b82f6;'>
                <p style='margin:0; color:#1e40af;'>
                    <b>💾 Dossier mis à jour !</b><br>
                    <span style='font-size:0.9rem;'>Les modifications ont été enregistrées.</span>
                </p>
            </div>
            """
        
        return STYLE + f"""
        <div style='max-width:500px; margin:0 auto; text-align:center; padding:30px;'>
            {success_message}
            
            <div style='background:white; padding:25px; border-radius:15px; text-align:left;
                        box-shadow:0 4px 15px rgba(0,0,0,0.1); margin-bottom:25px;'>
                <h3 style='margin-top:0; color:#1e293b;'>📋 Récapitulatif</h3>
                <p><b>🏪 Entreprise :</b> {case.company.upper()}</p>
                <p><b>💰 Montant :</b> {case.amount}</p>
                <p><b>📧 Email marchand :</b> {case.merchant_email or 'Non renseigné'}</p>
                <p><b>📊 Statut :</b> {case.status}</p>
            </div>
            
            <a href='/dashboard' class='btn-success' style='display:inline-block; padding:15px 30px;'>
                📂 Retour au dashboard
            </a>
        </div>
        """ + FOOTER
    
    # ════════════════════════════════════════════════════════════════
    # AFFICHAGE DU FORMULAIRE D'ÉDITION (GET)
    # ════════════════════════════════════════════════════════════════
    
    # Statut actuel avec couleur
    status_color = "#94a3b8"
    if case.status == "En cours juridique":
        status_color = "#3b82f6"
    elif case.status == "Remboursé":
        status_color = "#10b981"
    elif "En attente" in case.status:
        status_color = "#f59e0b"
    
    # Checkbox pour envoi auto
    send_notice_checked = "checked" if not case.legal_notice_sent else ""
    send_notice_label = "Envoyer la mise en demeure" if not case.legal_notice_sent else "Renvoyer la mise en demeure"
    
    # Info sur la dernière mise en demeure
    legal_notice_info = ""
    if case.legal_notice_sent and case.legal_notice_date:
        date_str = case.legal_notice_date.strftime("%d/%m/%Y à %H:%M")
        legal_notice_info = f"""
        <div style='background:#dbeafe; padding:15px; border-radius:10px; margin-bottom:20px;
                    border-left:4px solid #3b82f6;'>
            <p style='margin:0; color:#1e40af; font-size:0.9rem;'>
                <b>⚖️ Mise en demeure déjà envoyée</b><br>
                Le {date_str} à {case.merchant_email}
            </p>
        </div>
        """
    
    return STYLE + f"""
    <div style='max-width:500px; margin:0 auto; padding:20px;'>
        <h1 style='text-align:center;'>✏️ Modifier le dossier</h1>
        
        <div style='background:white; padding:25px; border-radius:15px; 
                    box-shadow:0 4px 15px rgba(0,0,0,0.1); margin-bottom:20px;'>
            
            <!-- Résumé du dossier -->
            <div style='background:#f8fafc; padding:15px; border-radius:10px; margin-bottom:20px;'>
                <h3 style='margin:0 0 10px 0; color:#1e293b;'>🏪 {case.company.upper()}</h3>
                <p style='margin:5px 0; color:#64748b; font-size:0.9rem;'>
                    <b>Sujet :</b> {case.subject[:80]}...
                </p>
                <p style='margin:5px 0; color:#64748b; font-size:0.9rem;'>
                    <b>Base légale :</b> {case.law}
                </p>
                <p style='margin:5px 0;'>
                    <b>Statut :</b> 
                    <span style='background:{status_color}20; color:{status_color}; padding:3px 8px; border-radius:5px;'>
                        {case.status}
                    </span>
                </p>
            </div>
            
            {legal_notice_info}
            
            <form method='POST'>
                <!-- Email marchand -->
                <div style='margin-bottom:20px;'>
                    <label style='font-weight:bold; color:#1e293b; display:block; margin-bottom:8px;'>
                        📧 Email du marchand *
                    </label>
                    <input type='email' name='merchant_email' 
                           value='{case.merchant_email or ""}'
                           placeholder='contact@marchand.com'
                           style='width:100%; padding:12px; border:1px solid #e2e8f0; border-radius:8px;
                                  font-size:1rem; box-sizing:border-box;'>
                    <p style='font-size:0.8rem; color:#64748b; margin:5px 0 0 0;'>
                        Si l'Agent Détective n'a pas trouvé l'email, entrez-le manuellement.
                    </p>
                </div>
                
                <!-- Montant -->
                <div style='margin-bottom:20px;'>
                    <label style='font-weight:bold; color:#1e293b; display:block; margin-bottom:8px;'>
                        💰 Montant du litige
                    </label>
                    <input type='text' name='amount' 
                           value='{case.amount.replace("€", "") if case.amount else ""}'
                           placeholder='150.00'
                           style='width:100%; padding:12px; border:1px solid #e2e8f0; border-radius:8px;
                                  font-size:1rem; box-sizing:border-box;'>
                    <p style='font-size:0.8rem; color:#64748b; margin:5px 0 0 0;'>
                        Corrigez si le montant scanné est incorrect.
                    </p>
                </div>
                
                <!-- Checkbox envoi mise en demeure -->
                <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:20px;
                            border-left:4px solid #f59e0b;'>
                    <label style='display:flex; align-items:center; cursor:pointer;'>
                        <input type='checkbox' name='send_notice' {send_notice_checked}
                               style='width:20px; height:20px; margin-right:10px;'>
                        <span style='color:#92400e;'>
                            <b>⚖️ {send_notice_label}</b><br>
                            <span style='font-size:0.85rem;'>
                                La mise en demeure sera envoyée à l'email ci-dessus.
                            </span>
                        </span>
                    </label>
                </div>
                
                <!-- Boutons -->
                <div style='display:flex; gap:10px;'>
                    <button type='submit' class='btn-success' 
                            style='flex:1; padding:15px; font-size:1rem; border:none; cursor:pointer;'>
                        💾 Enregistrer
                    </button>
                    <a href='/dashboard' class='btn-logout' 
                       style='flex:0.5; text-align:center; padding:15px; text-decoration:none;'>
                        Annuler
                    </a>
                </div>
            </form>
        </div>
        
        <!-- Aide -->
        <div style='background:#f1f5f9; padding:15px; border-radius:10px; text-align:center;'>
            <p style='margin:0; color:#64748b; font-size:0.85rem;'>
                💡 <b>Astuce :</b> Cherchez l'email de contact sur le site du marchand 
                (page Contact, Mentions Légales, CGV...).
            </p>
        </div>
    </div>
    """ + FOOTER

# ========================================
# DÉCLARATION MANUELLE DE LITIGE (V2)
# ========================================

# Types de problèmes disponibles
PROBLEM_TYPES = [
    ("non_recu", "📦 Colis non reçu", "Le colis n'a jamais été livré ou est marqué livré mais non reçu"),
    ("defectueux", "🔧 Produit défectueux", "Le produit reçu est cassé, ne fonctionne pas ou est endommagé"),
    ("non_conforme", "❌ Non conforme à la description", "Le produit ne correspond pas à ce qui était annoncé"),
    ("retour_refuse", "🚫 Retour refusé", "Le vendeur refuse d'accepter le retour ou de rembourser"),
    ("contrefacon", "⚠️ Contrefaçon", "Le produit reçu est une contrefaçon ou une imitation"),
    ("retard", "⏰ Retard de livraison important", "Le délai de livraison annoncé n'a pas été respecté"),
    ("annulation_refusee", "🔄 Annulation refusée", "Le vendeur refuse d'annuler une commande non expédiée"),
    ("autre", "❓ Autre problème", "Un autre type de litige non listé ci-dessus")
]

@app.route("/declare")
def declare_litige():
    """Formulaire de déclaration manuelle de litige"""
    if "email" not in session:
        return redirect("/login")
    
    # Générer les options du menu déroulant
    options_html = ""
    for value, label, description in PROBLEM_TYPES:
        options_html += f'<option value="{value}" data-description="{description}">{label}</option>'
    
    return STYLE + f"""
    <div style='max-width:600px; margin:0 auto;'>
        <h1>⚡ Déclarer un Litige & Lancer la Procédure</h1>
        
        <div style='background:linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); 
                    padding:25px; border-radius:15px; margin-bottom:25px;
                    border-left:4px solid #f59e0b; box-shadow:0 4px 15px rgba(245,158,11,0.2);'>
            <p style='margin:0; color:#92400e; font-size:1rem; line-height:1.6;'>
                <b style='font-size:1.1rem;'>🎯 Ne perdez plus de temps à chercher l'email du SAV.</b><br><br>
                Remplissez ce formulaire : <b>notre IA trouve le contact juridique</b> de l'entreprise, 
                <b>rédige la mise en demeure</b> (Code de la Consommation) et <b>l'envoie directement</b> 
                depuis votre adresse mail.<br><br>
                <span style='background:#fef3c7; padding:3px 8px; border-radius:5px; font-weight:600;'>
                    💪 On s'occupe de la pression juridique, vous récupérez votre argent.
                </span>
            </p>
        </div>
        
        <form action='/submit_litige' method='POST' style='background:white; padding:25px; border-radius:20px; box-shadow:0 4px 15px rgba(0,0,0,0.1);'>
            
            <!-- NOM DU SITE / ENTREPRISE -->
            <div style='margin-bottom:20px;'>
                <label style='display:block; font-weight:600; color:#1e293b; margin-bottom:8px;'>
                    🏪 Nom du site ou de l'entreprise *
                </label>
                <input type='text' name='company' required
                       placeholder='Ex: MaSuperBoutique, Amazon, Cdiscount...'
                       style='width:100%; padding:12px 15px; border:2px solid #e2e8f0; border-radius:10px; 
                              font-size:1rem; transition:border-color 0.2s;'
                       onfocus="this.style.borderColor='#3b82f6'" 
                       onblur="this.style.borderColor='#e2e8f0'">
            </div>
            
            <!-- URL DU SITE -->
            <div style='margin-bottom:20px;'>
                <label style='display:block; font-weight:600; color:#1e293b; margin-bottom:8px;'>
                    🌐 URL du site <span style='color:#94a3b8; font-weight:normal;'>(aide notre IA à trouver le contact)</span>
                </label>
                <input type='url' name='url_site'
                       placeholder='Ex: https://www.boutique-en-ligne.com'
                       style='width:100%; padding:12px 15px; border:2px solid #e2e8f0; border-radius:10px; 
                              font-size:1rem; transition:border-color 0.2s;'
                       onfocus="this.style.borderColor='#3b82f6'" 
                       onblur="this.style.borderColor='#e2e8f0'">
            </div>
            
            <!-- NUMÉRO DE COMMANDE -->
            <div style='margin-bottom:20px;'>
                <label style='display:block; font-weight:600; color:#1e293b; margin-bottom:8px;'>
                    📋 Numéro de commande *
                </label>
                <input type='text' name='order_id' required
                       placeholder='Ex: #123456, ORD-2024-789, etc.'
                       style='width:100%; padding:12px 15px; border:2px solid #e2e8f0; border-radius:10px; 
                              font-size:1rem; transition:border-color 0.2s;'
                       onfocus="this.style.borderColor='#3b82f6'" 
                       onblur="this.style.borderColor='#e2e8f0'">
            </div>
            
            <!-- DATE ET MONTANT (sur la même ligne) -->
            <div style='display:flex; gap:15px; margin-bottom:20px;'>
                <div style='flex:1;'>
                    <label style='display:block; font-weight:600; color:#1e293b; margin-bottom:8px;'>
                        📅 Date de commande *
                    </label>
                    <input type='date' name='order_date' required
                           style='width:100%; padding:12px 15px; border:2px solid #e2e8f0; border-radius:10px; 
                                  font-size:1rem; transition:border-color 0.2s;'
                           onfocus="this.style.borderColor='#3b82f6'" 
                           onblur="this.style.borderColor='#e2e8f0'">
                </div>
                <div style='flex:1;'>
                    <label style='display:block; font-weight:600; color:#1e293b; margin-bottom:8px;'>
                        💰 Montant (€) *
                    </label>
                    <input type='number' name='amount' required step='0.01' min='0.01'
                           placeholder='Ex: 89.99'
                           style='width:100%; padding:12px 15px; border:2px solid #e2e8f0; border-radius:10px; 
                                  font-size:1rem; transition:border-color 0.2s;'
                           onfocus="this.style.borderColor='#3b82f6'" 
                           onblur="this.style.borderColor='#e2e8f0'">
                </div>
            </div>
            
            <!-- TYPE DE PROBLÈME -->
            <div style='margin-bottom:20px;'>
                <label style='display:block; font-weight:600; color:#1e293b; margin-bottom:8px;'>
                    ⚠️ Type de problème *
                </label>
                <select name='problem_type' required id='problem_type'
                        style='width:100%; padding:12px 15px; border:2px solid #e2e8f0; border-radius:10px; 
                               font-size:1rem; transition:border-color 0.2s; background:white;'
                        onfocus="this.style.borderColor='#3b82f6'" 
                        onblur="this.style.borderColor='#e2e8f0'"
                        onchange="updateDescription()">
                    <option value=''>-- Sélectionnez le type de problème --</option>
                    {options_html}
                </select>
                <p id='problem_description' style='margin-top:8px; font-size:0.85rem; color:#64748b; font-style:italic;'></p>
            </div>
            
            <!-- DESCRIPTION DÉTAILLÉE -->
            <div style='margin-bottom:25px;'>
                <label style='display:block; font-weight:600; color:#1e293b; margin-bottom:8px;'>
                    📝 Décrivez votre problème *
                </label>
                <textarea name='description' required rows='5'
                          placeholder='Expliquez en détail ce qui s'est passé : quand avez-vous commandé, qu'avez-vous reçu (ou non), quelles démarches avez-vous déjà effectuées...'
                          style='width:100%; padding:12px 15px; border:2px solid #e2e8f0; border-radius:10px; 
                                 font-size:1rem; resize:vertical; min-height:120px; transition:border-color 0.2s;'
                          onfocus="this.style.borderColor='#3b82f6'" 
                          onblur="this.style.borderColor='#e2e8f0'"></textarea>
                <p style='margin-top:5px; font-size:0.8rem; color:#94a3b8;'>
                    Plus vous donnez de détails, plus notre IA pourra personnaliser votre mise en demeure.
                </p>
            </div>
            
            <!-- BOUTON SUBMIT -->
            <button type='submit' 
                    style='width:100%; padding:15px; background:linear-gradient(135deg, #10b981 0%, #059669 100%); 
                           color:white; border:none; border-radius:12px; font-size:1.1rem; font-weight:600;
                           cursor:pointer; transition:transform 0.2s, box-shadow 0.2s;
                           box-shadow:0 4px 15px rgba(16,185,129,0.3);'
                    onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 6px 20px rgba(16,185,129,0.4)';"
                    onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(16,185,129,0.3)';">
                ⚡ Lancer la procédure
            </button>
            
            <!-- Badge de réassurance -->
            <div style='display:flex; justify-content:center; gap:20px; margin-top:15px; flex-wrap:wrap;'>
                <span style='font-size:0.75rem; color:#64748b;'>🔒 Données sécurisées</span>
                <span style='font-size:0.75rem; color:#64748b;'>⚖️ Conforme RGPD</span>
                <span style='font-size:0.75rem; color:#64748b;'>🚀 Envoi automatique</span>
            </div>
        </form>
        
        <!-- LIEN SUPPORT -->
        <div style='background:#f1f5f9; padding:15px; border-radius:12px; margin-top:20px; text-align:center;'>
            <p style='margin:0; color:#64748b; font-size:0.9rem;'>
                🤔 <b>Vous ne savez pas quoi remplir ?</b><br>
                <a href='mailto:{SUPPORT_EMAIL}?subject=Aide%20pour%20déclarer%20un%20litige' 
                   style='color:#4f46e5; text-decoration:none; font-weight:600;'>
                    Contactez notre expert litige →
                </a>
                <span style='display:block; font-size:0.8rem; color:#94a3b8; margin-top:5px;'>Réponse sous 24h</span>
            </p>
        </div>
        
        <div style='text-align:center; margin-top:20px;'>
            <a href='/dashboard' style='color:#64748b; text-decoration:none;'>← Retour au Dashboard</a>
        </div>
        
        <script>
            function updateDescription() {{
                var select = document.getElementById('problem_type');
                var desc = document.getElementById('problem_description');
                var selectedOption = select.options[select.selectedIndex];
                if (selectedOption.value) {{
                    desc.textContent = selectedOption.getAttribute('data-description');
                }} else {{
                    desc.textContent = '';
                }}
            }}
            
            // 🛡️ FIX: Empêcher le retour navigateur vers /scan-all
            (function(){{
                try {{
                    var ref = document.referrer || "";
                    if (ref.includes("/scan") || ref.includes("/scan-all")) {{
                        history.pushState({{justicio:"declare"}}, "", window.location.href);
                        window.addEventListener("popstate", function(e){{
                            window.location.href = "/";
                        }});
                    }}
                }} catch(e) {{}}
            }})();
        </script>
    </div>
    """ + FOOTER


@app.route("/submit_litige", methods=["POST"])
def submit_litige():
    """Traite la soumission du formulaire de déclaration manuelle"""
    if "email" not in session:
        return redirect("/login")
    
    # ════════════════════════════════════════════════════════════════
    # 🔒 GATEKEEPER STRIPE - VÉRIFICATION STRICTE EN PREMIER
    # ════════════════════════════════════════════════════════════════
    # Cette vérification DOIT être faite AVANT tout traitement
    # pour empêcher le bypass via bouton "Retour" du navigateur
    
    user = User.query.filter_by(email=session['email']).first()
    
    if not user:
        return redirect("/login")
    
    # BLOCAGE STRICT : Pas de carte = Pas de service
    if not user.stripe_customer_id:
        print(f"⛔ REFUS : Utilisateur {user.email} sans carte tente de déclarer un litige.")
        DEBUG_LOGS.append(f"⛔ GATEKEEPER STRICT: Blocage {user.email} - Tentative sans carte")
        
        # Sauvegarder TOUT le formulaire en session
        session['pending_manual_litige'] = request.form.to_dict()
        session['pending_manual_litige']['created_at'] = datetime.now().isoformat()
        
        # Message d'avertissement
        session['payment_message'] = "🔒 Vous devez enregistrer un moyen de paiement pour lancer la procédure juridique."
        
        # ARRÊT TOTAL - Redirection forcée
        return redirect(url_for('setup_payment'))
    
    # Vérification supplémentaire : La carte est-elle toujours valide chez Stripe ?
    try:
        payment_methods = stripe.PaymentMethod.list(
            customer=user.stripe_customer_id,
            type="card",
            limit=1
        )
        if not payment_methods.data:
            print(f"⛔ REFUS : Utilisateur {user.email} - Customer Stripe sans carte active")
            DEBUG_LOGS.append(f"⛔ GATEKEEPER: {user.email} - Stripe customer sans carte valide")
            session['pending_manual_litige'] = request.form.to_dict()
            session['payment_message'] = "🔒 Votre carte n'est plus valide. Veuillez en enregistrer une nouvelle."
            return redirect(url_for('setup_payment'))
    except Exception as e:
        DEBUG_LOGS.append(f"⚠️ Gatekeeper: Erreur vérification Stripe: {str(e)[:50]}")
        # En cas d'erreur Stripe, on laisse passer (fail-open pour ne pas bloquer)
    
    DEBUG_LOGS.append(f"✅ GATEKEEPER: {user.email} autorisé - Carte valide ({user.stripe_customer_id})")
    
    # ════════════════════════════════════════════════════════════════
    # TRAITEMENT DU FORMULAIRE (Seulement si carte validée)
    # ════════════════════════════════════════════════════════════════
    
    try:
        # Récupérer les données du formulaire
        company = request.form.get("company", "").strip()
        url_site = request.form.get("url_site", "").strip()
        order_id = request.form.get("order_id", "").strip()
        order_date_str = request.form.get("order_date", "")
        amount_str = request.form.get("amount", "0")
        problem_type = request.form.get("problem_type", "")
        description = request.form.get("description", "").strip()
        
        # Validation
        if not company or not order_id or not problem_type or not description:
            return STYLE + """
            <div style='text-align:center; padding:50px;'>
                <h1>❌ Formulaire incomplet</h1>
                <p>Veuillez remplir tous les champs obligatoires.</p>
                <br>
                <a href='/declare' class='btn-success'>Réessayer</a>
            </div>
            """ + FOOTER
        
        # ════════════════════════════════════════════════════════════════
        # Suite du traitement normal (client authentifié avec carte)
        # ════════════════════════════════════════════════════════════════
        
        # Parser la date
        order_date = None
        if order_date_str:
            try:
                order_date = datetime.strptime(order_date_str, "%Y-%m-%d").date()
            except:
                pass
        
        # Parser le montant
        try:
            amount_float = float(amount_str.replace(",", "."))
        except:
            amount_float = 0
        
        # Déterminer la loi applicable selon le type de problème
        problem_to_law = {
            "non_recu": "la Directive UE 2011/83 (Livraison)",
            "defectueux": "la Directive UE 2019/771 (Garantie légale)",
            "non_conforme": "la Directive UE 2019/771 (Conformité)",
            "retour_refuse": "la Directive UE 2011/83 (Droit de rétractation)",
            "contrefacon": "le Code de la consommation (Contrefaçon)",
            "retard": "la Directive UE 2011/83 (Délai de livraison)",
            "annulation_refusee": "la Directive UE 2011/83 (Annulation)",
            "autre": "le Code de la consommation"
        }
        law = problem_to_law.get(problem_type, "le Code de la consommation")
        
        # Créer le résumé pour le champ subject
        problem_labels = {p[0]: p[1] for p in PROBLEM_TYPES}
        problem_label = problem_labels.get(problem_type, "Litige")
        subject = f"{problem_label} - {description[:100]}..."
        
        # Créer l'entrée en base de données
        new_case = Litigation(
            user_email=session["email"],
            company=company.upper(),
            amount=f"{amount_float:.2f}€",
            law=law,
            subject=subject,
            message_id=f"MANUAL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            status="En attente d'analyse",
            source="MANUAL",
            url_site=url_site if url_site else None,
            order_id=order_id,
            order_date=order_date,
            amount_float=amount_float,
            problem_type=problem_type,
            description=description
        )
        
        db.session.add(new_case)
        db.session.commit()
        
        # ════════════════════════════════════════════════════════════════
        # 🕵️ AGENT DÉTECTIVE - Recherche automatique de l'email marchand
        # ════════════════════════════════════════════════════════════════
        
        merchant_result = {"email": None, "source": None}
        detective_status = "non_lance"
        
        if url_site:
            DEBUG_LOGS.append(f"🕵️ Lancement Agent Détective pour {url_site}")
            merchant_result = find_merchant_email(url_site)
            
            if merchant_result["email"]:
                # Email trouvé ! Mettre à jour le dossier
                new_case.merchant_email = merchant_result["email"]
                new_case.merchant_email_source = merchant_result["source"]
                db.session.commit()
                detective_status = "succes"
                DEBUG_LOGS.append(f"🕵️ ✅ Email sauvegardé: {merchant_result['email']}")
            else:
                detective_status = "echec"
                DEBUG_LOGS.append(f"🕵️ ❌ Aucun email trouvé")
        
        # Préparer l'affichage du résultat détective
        detective_html = ""
        if detective_status == "succes":
            detective_html = f"""
            <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); 
                        padding:15px; border-radius:10px; margin-bottom:15px;
                        border-left:4px solid #10b981;'>
                <p style='margin:0; color:#065f46;'>
                    <b>🕵️ Agent Détective :</b> Email trouvé !<br>
                    <span style='font-family:monospace; background:#ecfdf5; padding:3px 8px; border-radius:4px;'>
                        {merchant_result['email']}
                    </span>
                    <span style='font-size:0.8rem; color:#047857;'> (via {merchant_result['source']})</span>
                </p>
            </div>
            """
        elif detective_status == "echec":
            detective_html = f"""
            <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:15px;
                        border-left:4px solid #f59e0b;'>
                <p style='margin:0; color:#92400e; font-size:0.9rem;'>
                    <b>🕵️ Agent Détective :</b> Aucun email trouvé automatiquement.<br>
                    <span style='font-size:0.85rem;'>Nous rechercherons manuellement le contact.</span>
                </p>
            </div>
            """
        
        # ════════════════════════════════════════════════════════════════
        # ⚖️ AGENT AVOCAT - Envoi automatique de la mise en demeure (V4)
        # ════════════════════════════════════════════════════════════════
        
        legal_notice_result = {"success": False, "message": "Non lancé"}
        legal_notice_html = ""
        
        if merchant_result["email"]:
            DEBUG_LOGS.append(f"⚖️ Lancement Agent Avocat pour {company}")
            
            # Récupérer l'utilisateur pour l'envoi
            user = User.query.filter_by(email=session['email']).first()
            
            if user and user.refresh_token:
                # Envoyer la mise en demeure
                legal_notice_result = send_legal_notice(new_case, user)
                
                if legal_notice_result["success"]:
                    DEBUG_LOGS.append(f"⚖️ ✅ Mise en demeure envoyée avec succès!")
                    legal_notice_html = f"""
                    <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); 
                                padding:15px; border-radius:10px; margin-bottom:15px;
                                border-left:4px solid #10b981;'>
                        <p style='margin:0; color:#065f46;'>
                            <b>⚖️ Agent Avocat :</b> Mise en demeure ENVOYÉE !<br>
                            <span style='font-size:0.85rem;'>Envoyé à {merchant_result['email']} (copie dans votre boîte mail)</span>
                        </p>
                    </div>
                    """
                else:
                    DEBUG_LOGS.append(f"⚖️ ❌ Échec envoi: {legal_notice_result['message']}")
                    legal_notice_html = f"""
                    <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:15px;
                                border-left:4px solid #f59e0b;'>
                        <p style='margin:0; color:#92400e; font-size:0.9rem;'>
                            <b>⚖️ Agent Avocat :</b> Envoi différé<br>
                            <span style='font-size:0.85rem;'>{legal_notice_result['message']}</span>
                        </p>
                    </div>
                    """
            else:
                DEBUG_LOGS.append(f"⚖️ ❌ Utilisateur non trouvé ou non authentifié")
                legal_notice_html = """
                <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:15px;
                            border-left:4px solid #f59e0b;'>
                    <p style='margin:0; color:#92400e; font-size:0.9rem;'>
                        <b>⚖️ Agent Avocat :</b> Reconnexion nécessaire<br>
                        <span style='font-size:0.85rem;'>Reconnectez-vous pour autoriser l'envoi d'emails.</span>
                    </p>
                </div>
                """
        
        # Notification Telegram avec résultat détective + avocat
        detective_notif = ""
        if merchant_result["email"]:
            detective_notif = f"\n\n🕵️ EMAIL TROUVÉ: {merchant_result['email']}"
            if legal_notice_result["success"]:
                detective_notif += "\n⚖️ MISE EN DEMEURE ENVOYÉE ✅"
            else:
                detective_notif += f"\n⚖️ Envoi différé: {legal_notice_result['message']}"
        else:
            detective_notif = "\n\n🕵️ Email non trouvé (recherche manuelle requise)"
        
        send_telegram_notif(f"📝 NOUVEAU LITIGE MANUEL 📝\n\n🏪 {company.upper()}\n💰 {amount_float:.2f}€\n📋 N° {order_id}\n⚠️ {problem_label}\n👤 {session['email']}{detective_notif}\n\n📄 Description:\n{description[:150]}...")
        
        # Déterminer le titre selon le résultat
        if legal_notice_result["success"]:
            success_title = "Mise en demeure envoyée !"
            success_icon = "✅"
            success_subtitle = "Le marchand a reçu votre réclamation officielle."
        elif merchant_result["email"]:
            success_title = "Procédure lancée !"
            success_icon = "⚡"
            success_subtitle = "L'envoi de la mise en demeure est en préparation."
        else:
            success_title = "Dossier créé !"
            success_icon = "📋"
            success_subtitle = "Nous recherchons le contact du marchand."
        
        # Page de succès avec résultat du détective et avocat
        return STYLE + f"""
        <div style='max-width:500px; margin:0 auto; text-align:center; padding:30px;'>
            <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); 
                        padding:30px; border-radius:20px; margin-bottom:25px;'>
                <div style='font-size:4rem; margin-bottom:15px;'>{success_icon}</div>
                <h1 style='color:#065f46; margin:0 0 10px 0;'>{success_title}</h1>
                <p style='color:#047857; margin:0;'>{success_subtitle}</p>
            </div>
            
            {detective_html}
            
            {legal_notice_html}
            
            <div style='background:white; padding:25px; border-radius:15px; text-align:left;
                        box-shadow:0 4px 15px rgba(0,0,0,0.1); margin-bottom:25px;'>
                <h3 style='margin-top:0; color:#1e293b;'>📋 Récapitulatif</h3>
                <p><b>🏪 Entreprise :</b> {company.upper()}</p>
                <p><b>💰 Montant réclamé :</b> {amount_float:.2f}€</p>
                <p><b>📋 N° Commande :</b> {order_id}</p>
                <p><b>⚖️ Base légale :</b> {law}</p>
                <p><b>📊 Statut :</b> <span style='background:#3b82f6; color:white; padding:3px 8px; border-radius:5px; font-size:0.85rem;'>{new_case.status}</span></p>
            </div>
            
            <div style='background:linear-gradient(135deg, #dbeafe 0%, #e0e7ff 100%); 
                        padding:20px; border-radius:15px; margin-bottom:25px;
                        border-left:4px solid #3b82f6;'>
                <h4 style='margin:0 0 10px 0; color:#1e40af;'>🤖 Progression</h4>
                <div style='text-align:left; color:#1e40af; font-size:0.9rem;'>
                    <p style='margin:5px 0;'>1️⃣ <b>Recherche contact</b> {"✅" if merchant_result["email"] else "⏳"}</p>
                    <p style='margin:5px 0;'>2️⃣ <b>Rédaction mise en demeure</b> {"✅" if legal_notice_result["success"] else ("⏳" if merchant_result["email"] else "⏸️")}</p>
                    <p style='margin:5px 0;'>3️⃣ <b>Envoi au marchand</b> {"✅" if legal_notice_result["success"] else "⏳"}</p>
                    <p style='margin:5px 0;'>4️⃣ <b>Suivi des réponses</b> ⏳</p>
                </div>
            </div>
            
            {"" if not legal_notice_result["success"] else '''
            <div style="background:#ecfdf5; padding:15px; border-radius:10px; margin-bottom:25px;
                        border-left:4px solid #10b981;">
                <p style="margin:0; color:#065f46; font-size:0.9rem;">
                    <b>📧 Email envoyé !</b><br>
                    <span style="font-size:0.85rem;">Une copie de la mise en demeure a été envoyée dans votre boîte mail.</span>
                </p>
            </div>
            '''}
            
            <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:25px;
                        border-left:4px solid #f59e0b;'>
                <p style='margin:0; color:#92400e; font-size:0.9rem;'>
                    <b>⏱️ Délai légal :</b> Le marchand dispose de 8 jours pour répondre.<br>
                    <span style='font-size:0.8rem;'>Nous surveillerons votre boîte mail pour détecter sa réponse.</span>
                </p>
            </div>
            
            <a href='/dashboard' class='btn-success' style='display:inline-block; padding:15px 30px;'>
                📂 Suivre mon dossier
            </a>
        </div>
        """ + FOOTER
        
    except Exception as e:
        DEBUG_LOGS.append(f"Erreur submit_litige: {str(e)}")
        return STYLE + f"""
        <div style='text-align:center; padding:50px;'>
            <h1>❌ Erreur</h1>
            <p>Une erreur est survenue lors de l'enregistrement : {str(e)}</p>
            <br>
            <a href='/declare' class='btn-success'>Réessayer</a>
            <br><br>
            <a href='mailto:{SUPPORT_EMAIL}?subject=Erreur%20lors%20de%20la%20déclaration' 
               style='color:#4f46e5; font-size:0.9rem;'>Contacter le support →</a>
        </div>
        """ + FOOTER

@app.route("/delete-case/<int:case_id>")
def delete_case(case_id):
    """Supprime un dossier spécifique"""
    if "email" not in session:
        return redirect("/login")
    
    try:
        # Récupérer le dossier en vérifiant qu'il appartient à l'utilisateur
        case = Litigation.query.filter_by(id=case_id, user_email=session['email']).first()
        
        if not case:
            return STYLE + """
            <div style='text-align:center; padding:50px;'>
                <h1>❌ Dossier Introuvable</h1>
                <p>Ce dossier n'existe pas ou ne vous appartient pas.</p>
                <br>
                <a href='/dashboard' class='btn-success'>Retour au Dashboard</a>
            </div>
            """ + FOOTER
        
        company_name = case.company.upper()
        amount = case.amount
        
        # Supprimer le dossier
        db.session.delete(case)
        db.session.commit()
        
        return STYLE + f"""
        <div style='text-align:center; padding:50px;'>
            <h1>🗑️ Dossier Supprimé</h1>
            <p>Le dossier <b>{company_name}</b> ({amount}) a été supprimé.</p>
            <br>
            <a href='/dashboard' class='btn-success'>Retour au Dashboard</a>
            <br><br>
            <a href='/scan-ecommerce' class='btn-logout'>Nouveau Scan E-commerce</a>
        </div>
        """ + FOOTER
        
    except Exception as e:
        return STYLE + f"""
        <div style='text-align:center; padding:50px;'>
            <h1>❌ Erreur</h1>
            <p>Impossible de supprimer le dossier : {str(e)}</p>
            <br>
            <a href='/dashboard' class='btn-success'>Retour</a>
        </div>
        """ + FOOTER

# ========================================
# RESET BASE DE DONNÉES
# ========================================

@app.route("/force-reset")
def force_reset():
    """Réinitialise tous les litiges (debug)"""
    if "email" not in session:
        return redirect("/login")
    
    try:
        num_deleted = Litigation.query.filter_by(user_email=session['email']).delete()
        db.session.commit()
        return STYLE + f"""
        <div style='text-align:center; padding:50px;'>
            <h1>✅ Base Nettoyée</h1>
            <p>{num_deleted} dossiers supprimés pour {session.get('email')}</p>
            <br>
            <a href='/scan-ecommerce' class='btn-success'>Relancer Scan E-commerce</a>
            <br><br>
            <a href='/' class='btn-logout'>Retour</a>
        </div>
        """ + FOOTER
    except Exception as e:
        return f"Erreur : {e}"

# ========================================
# AUTHENTIFICATION GOOGLE
# ========================================

@app.route("/login")
def login():
    """Initie le flux OAuth Google"""
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/gmail.modify",
            "openid"
        ],
        redirect_uri=url_for('callback', _external=True).replace("http://", "https://")
    )
    
    url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session["state"] = state
    return redirect(url)

@app.route("/callback")
def callback():
    """Callback OAuth Google"""
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/gmail.modify",
            "openid"
        ],
        redirect_uri=url_for('callback', _external=True).replace("http://", "https://")
    )
    
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    
    info = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
    email = info.get('email')
    name = info.get('name')
    
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name, refresh_token=creds.refresh_token)
        db.session.add(user)
    else:
        if creds.refresh_token:
            user.refresh_token = creds.refresh_token
    
    db.session.commit()
    
    session["credentials"] = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }
    session["name"] = name
    session["email"] = email
    
    return redirect("/")

# ========================================
# PAIEMENT STRIPE
# ========================================

@app.route("/setup-payment")
def setup_payment():
    """
    💳 Configure le paiement Stripe - ONE-CLICK si carte déjà enregistrée
    
    Logique :
    1. Si l'utilisateur a déjà une carte → Redirect direct vers /success (One-Click)
    2. Sinon → Créer session Stripe pour enregistrer la carte
    """
    if "email" not in session:
        return redirect("/login")
    
    try:
        user = User.query.filter_by(email=session['email']).first()
        
        # ════════════════════════════════════════════════════════════════
        # ONE-CLICK : Vérifier si une carte existe déjà
        # ════════════════════════════════════════════════════════════════
        
        if user.stripe_customer_id:
            try:
                # Vérifier si le client a au moins une carte valide
                payment_methods = stripe.PaymentMethod.list(
                    customer=user.stripe_customer_id,
                    type="card",
                    limit=1
                )
                
                if payment_methods.data:
                    # ✅ CARTE EXISTANTE → One-Click !
                    DEBUG_LOGS.append(f"💳 One-Click: {user.email} a déjà une carte, redirect vers /success")
                    
                    # Afficher une page de confirmation rapide
                    return STYLE + f"""
                    <div style='max-width:500px; margin:0 auto; text-align:center; padding:50px 20px;'>
                        <div style='font-size:4rem; margin-bottom:20px;'>💳</div>
                        <h1 style='color:white; margin-bottom:15px;'>Carte déjà enregistrée</h1>
                        <p style='color:rgba(255,255,255,0.7); margin-bottom:30px;'>
                            Votre carte se terminant par <b style='color:white;'>•••• {payment_methods.data[0].card.last4}</b> est déjà active.
                        </p>
                        
                        <div style='background:rgba(16, 185, 129, 0.1); border:1px solid rgba(16, 185, 129, 0.3);
                                    padding:20px; border-radius:15px; margin-bottom:30px;'>
                            <p style='color:#10b981; margin:0; font-size:1.1rem;'>
                                ✅ Prêt à lancer vos réclamations !
                            </p>
                        </div>
                        
                        <a href='/success' class='btn-success' style='display:inline-block; padding:18px 50px; font-size:1.2rem;'>
                            🚀 Continuer
                        </a>
                        
                        <div style='margin-top:25px;'>
                            <a href='/dashboard' style='color:rgba(255,255,255,0.5); font-size:0.9rem;'>
                                ← Retour au dashboard
                            </a>
                        </div>
                    </div>
                    """ + FOOTER
            except Exception as e:
                DEBUG_LOGS.append(f"⚠️ One-Click check error: {str(e)[:50]}")
        
        # ════════════════════════════════════════════════════════════════
        # NOUVEAU CLIENT : Créer customer Stripe si nécessaire
        # ════════════════════════════════════════════════════════════════
        
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(
                email=session.get('email'),
                name=session.get('name')
            )
            user.stripe_customer_id = customer.id
            db.session.commit()
            DEBUG_LOGS.append(f"💳 Nouveau customer Stripe créé: {customer.id}")
        
        # Récupérer le message flash si présent
        payment_message = session.pop('payment_message', None)
        is_manual_flow = 'pending_manual_litige' in session
        
        # Créer la session Stripe
        session_stripe = stripe.checkout.Session.create(
            customer=user.stripe_customer_id,
            payment_method_types=['card'],
            mode='setup',
            success_url=url_for('success_page', _external=True).replace("http://", "https://"),
            cancel_url=url_for('declare_litige', _external=True).replace("http://", "https://") if is_manual_flow else url_for('index', _external=True).replace("http://", "https://")
        )
        
        # Si c'est le flux manuel, afficher une page intermédiaire avec message
        if payment_message or is_manual_flow:
            company = session.get('pending_manual_litige', {}).get('company', 'votre litige')
            return STYLE + f"""
            <div style='max-width:500px; margin:0 auto; text-align:center; padding:30px;'>
                <div style='background:linear-gradient(135deg, #dbeafe 0%, #e0e7ff 100%); 
                            padding:30px; border-radius:20px; margin-bottom:25px;
                            border-left:5px solid #3b82f6;'>
                    <div style='font-size:3rem; margin-bottom:15px;'>🔒</div>
                    <h2 style='color:#1e40af; margin:0 0 15px 0;'>Sécurisez votre compte</h2>
                    <p style='color:#3730a3; margin:0;'>
                        {payment_message or "Enregistrez un moyen de paiement pour activer votre protection juridique."}
                    </p>
                </div>
                
                <div style='background:white; padding:25px; border-radius:15px; text-align:left;
                            box-shadow:0 4px 15px rgba(0,0,0,0.1); margin-bottom:25px;'>
                    <h4 style='margin-top:0; color:#1e293b;'>📋 Récapitulatif</h4>
                    <p style='color:#64748b;'><b>Dossier en attente :</b> {company.upper()}</p>
                    <p style='color:#64748b; margin-bottom:0;'><b>Montant prélevé maintenant :</b> <span style='color:#059669; font-weight:bold;'>0€</span></p>
                </div>
                
                <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:25px;
                            border-left:4px solid #f59e0b;'>
                    <p style='margin:0; color:#92400e; font-size:0.9rem;'>
                        <b>💳 Commission :</b> 25% uniquement en cas de remboursement obtenu.<br>
                        <span style='font-size:0.85rem;'>Aucun frais si nous n'obtenons pas satisfaction.</span>
                    </p>
                </div>
                
                <a href='{session_stripe.url}' class='btn-success' style='display:inline-block; padding:15px 40px; font-size:1.1rem;'>
                    💳 Enregistrer ma carte (0€)
                </a>
                
                <div style='margin-top:20px;'>
                    <a href='/declare' style='color:#64748b; font-size:0.9rem;'>← Annuler et revenir au formulaire</a>
                </div>
            </div>
            """ + FOOTER
        
        return redirect(session_stripe.url, code=303)
    
    except Exception as e:
        DEBUG_LOGS.append(f"❌ Erreur Stripe setup-payment: {str(e)}")
        return STYLE + f"""
        <div style='text-align:center; padding:50px;'>
            <h1 style='color:white;'>❌ Erreur de paiement</h1>
            <p style='color:rgba(255,255,255,0.7);'>Une erreur est survenue lors de la configuration du paiement.</p>
            <p style='color:#ef4444; font-size:0.9rem;'>{str(e)[:100]}</p>
            <br>
            <a href='/' class='btn-success'>Retour à l'accueil</a>
        </div>
        """ + FOOTER

@app.route("/success")
def success_page():
    """Page de succès - ENREGISTRE les litiges en base ET envoie les mises en demeure"""
    if "email" not in session:
        return redirect("/login")
    
    user = User.query.filter_by(email=session['email']).first()
    if not user or not user.refresh_token:
        return "Erreur : utilisateur non trouvé ou pas de refresh token"
    
    # ════════════════════════════════════════════════════════════════
    # 🔄 CALLBACK FLUX MANUEL - Traitement d'un litige en attente
    # ════════════════════════════════════════════════════════════════
    
    pending_litige = session.get('pending_manual_litige')
    
    if pending_litige:
        DEBUG_LOGS.append(f"🔄 Callback: Traitement du litige manuel en attente pour {pending_litige.get('company')}")
        
        try:
            # Récupérer les données sauvegardées
            company = pending_litige.get('company', '')
            url_site = pending_litige.get('url_site', '')
            order_id = pending_litige.get('order_id', '')
            order_date_str = pending_litige.get('order_date_str', '')
            amount_str = pending_litige.get('amount_str', '0')
            problem_type = pending_litige.get('problem_type', '')
            description = pending_litige.get('description', '')
            
            # Parser la date
            order_date = None
            if order_date_str:
                try:
                    order_date = datetime.strptime(order_date_str, "%Y-%m-%d").date()
                except:
                    pass
            
            # Parser le montant
            try:
                amount_float = float(amount_str.replace(",", "."))
            except:
                amount_float = 0
            
            # Déterminer la loi applicable
            problem_to_law = {
                "non_recu": "Article L.216-6 du Code de la consommation",
                "defectueux": "Articles L.217-3 et suivants (Garantie légale)",
                "non_conforme": "Article L.217-4 du Code de la consommation",
                "retour_refuse": "Article L.221-18 (Droit de rétractation)",
                "contrefacon": "Code de la Propriété Intellectuelle (L.716-1)",
                "retard": "Article L.216-1 du Code de la consommation",
                "annulation_refusee": "Articles L.221-18 et L.121-20",
                "autre": "Article 1103 du Code Civil"
            }
            law = problem_to_law.get(problem_type, "le Code de la consommation")
            
            # Créer le résumé
            problem_labels = {p[0]: p[1] for p in PROBLEM_TYPES}
            problem_label = problem_labels.get(problem_type, "Litige")
            subject = f"{problem_label} - {description[:100]}..."
            
            # Créer l'entrée en base de données
            new_case = Litigation(
                user_email=session['email'],
                company=company.lower().strip(),
                amount=f"{amount_float:.2f}€",
                amount_float=amount_float,
                law=law,
                subject=subject,
                source="MANUAL",
                url_site=url_site,
                order_id=order_id,
                order_date=order_date,
                problem_type=problem_type,
                description=description,
                status="En attente d'analyse"
            )
            
            db.session.add(new_case)
            db.session.commit()
            
            DEBUG_LOGS.append(f"✅ Callback: Dossier #{new_case.id} créé pour {company}")
            
            # ═══════════════════════════════════════════════════════════════
            # 🕵️ AGENT DÉTECTIVE
            # ═══════════════════════════════════════════════════════════════
            
            merchant_result = {"email": None, "source": None}
            detective_status = "non_lance"
            
            if url_site:
                DEBUG_LOGS.append(f"🕵️ Callback: Lancement Agent Détective pour {url_site}")
                merchant_result = find_merchant_email(url_site)
                
                if merchant_result["email"]:
                    new_case.merchant_email = merchant_result["email"]
                    new_case.merchant_email_source = merchant_result["source"]
                    db.session.commit()
                    detective_status = "succes"
                    DEBUG_LOGS.append(f"🕵️ Callback: ✅ Email trouvé: {merchant_result['email']}")
                else:
                    detective_status = "echec"
                    DEBUG_LOGS.append("🕵️ Callback: ❌ Aucun email trouvé")
            
            # ═══════════════════════════════════════════════════════════════
            # ⚖️ AGENT AVOCAT
            # ═══════════════════════════════════════════════════════════════
            
            legal_notice_result = {"success": False, "message": "Non lancé"}
            
            if merchant_result["email"]:
                DEBUG_LOGS.append(f"⚖️ Callback: Lancement Agent Avocat")
                legal_notice_result = send_legal_notice(new_case, user)
                
                if legal_notice_result["success"]:
                    DEBUG_LOGS.append("⚖️ Callback: ✅ Mise en demeure envoyée!")
                else:
                    DEBUG_LOGS.append(f"⚖️ Callback: ❌ {legal_notice_result['message']}")
            
            # ═══════════════════════════════════════════════════════════════
            # 📱 NOTIFICATION TELEGRAM
            # ═══════════════════════════════════════════════════════════════
            
            detective_notif = ""
            if merchant_result["email"]:
                detective_notif = f"\n\n🕵️ EMAIL: {merchant_result['email']}"
                if legal_notice_result["success"]:
                    detective_notif += "\n⚖️ MISE EN DEMEURE ENVOYÉE ✅"
            else:
                detective_notif = "\n\n🕵️ Email non trouvé"
            
            send_telegram_notif(f"📝 LITIGE MANUEL (post-paiement) 📝\n\n🏪 {company.upper()}\n💰 {amount_float:.2f}€\n📋 N° {order_id}\n⚠️ {problem_label}\n👤 {session['email']}{detective_notif}")
            
            # ═══════════════════════════════════════════════════════════════
            # 🧹 NETTOYER LA SESSION
            # ═══════════════════════════════════════════════════════════════
            
            session.pop('pending_manual_litige', None)
            
            # ═══════════════════════════════════════════════════════════════
            # 🎉 PAGE DE SUCCÈS
            # ═══════════════════════════════════════════════════════════════
            
            # Préparer les badges
            detective_html = ""
            if detective_status == "succes":
                detective_html = f"""
                <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); 
                            padding:15px; border-radius:10px; margin-bottom:15px;
                            border-left:4px solid #10b981;'>
                    <p style='margin:0; color:#065f46;'>
                        <b>🕵️ Agent Détective :</b> Email trouvé !<br>
                        <span style='font-family:monospace; background:#ecfdf5; padding:3px 8px; border-radius:4px;'>
                            {merchant_result['email']}
                        </span>
                    </p>
                </div>
                """
            elif detective_status == "echec":
                detective_html = """
                <div style='background:#fef3c7; padding:15px; border-radius:10px; margin-bottom:15px;
                            border-left:4px solid #f59e0b;'>
                    <p style='margin:0; color:#92400e; font-size:0.9rem;'>
                        <b>🕵️ Agent Détective :</b> Aucun email trouvé automatiquement.
                    </p>
                </div>
                """
            
            legal_html = ""
            if legal_notice_result["success"]:
                legal_html = f"""
                <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); 
                            padding:15px; border-radius:10px; margin-bottom:15px;
                            border-left:4px solid #10b981;'>
                    <p style='margin:0; color:#065f46;'>
                        <b>⚖️ Agent Avocat :</b> Mise en demeure ENVOYÉE !<br>
                        <span style='font-size:0.85rem;'>Copie dans votre boîte mail</span>
                    </p>
                </div>
                """
            
            # Titre dynamique
            if legal_notice_result["success"]:
                success_icon = "✅"
                success_title = "Mise en demeure envoyée !"
                success_subtitle = "Le marchand a reçu votre réclamation officielle."
            elif merchant_result["email"]:
                success_icon = "⚡"
                success_title = "Procédure lancée !"
                success_subtitle = "L'envoi est en préparation."
            else:
                success_icon = "📋"
                success_title = "Dossier créé !"
                success_subtitle = "Nous recherchons le contact du marchand."
            
            return STYLE + f"""
            <div style='max-width:500px; margin:0 auto; text-align:center; padding:30px;'>
                <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); 
                            padding:30px; border-radius:20px; margin-bottom:25px;'>
                    <div style='font-size:4rem; margin-bottom:15px;'>{success_icon}</div>
                    <h1 style='color:#065f46; margin:0 0 10px 0;'>{success_title}</h1>
                    <p style='color:#047857; margin:0;'>{success_subtitle}</p>
                </div>
                
                <div style='background:#ecfdf5; padding:15px; border-radius:10px; margin-bottom:20px;
                            border-left:4px solid #10b981;'>
                    <p style='margin:0; color:#065f46; font-size:0.9rem;'>
                        <b>💳 Paiement sécurisé !</b>
                    </p>
                </div>
                
                {detective_html}
                {legal_html}
                
                <div style='background:white; padding:25px; border-radius:15px; text-align:left;
                            box-shadow:0 4px 15px rgba(0,0,0,0.1); margin-bottom:25px;'>
                    <h3 style='margin-top:0; color:#1e293b;'>📋 Récapitulatif</h3>
                    <p><b>🏪 Entreprise :</b> {company.upper()}</p>
                    <p><b>💰 Montant :</b> {amount_float:.2f}€</p>
                    <p><b>📋 N° Commande :</b> {order_id}</p>
                    <p><b>⚖️ Base légale :</b> {law}</p>
                    <p><b>📊 Statut :</b> <span style='background:#3b82f6; color:white; padding:3px 8px; border-radius:5px;'>{new_case.status}</span></p>
                </div>
                
                <a href='/dashboard' class='btn-success' style='display:inline-block; padding:15px 30px;'>
                    📂 Suivre mon dossier
                </a>
            </div>
            """ + FOOTER
            
        except Exception as e:
            DEBUG_LOGS.append(f"❌ Callback: Erreur traitement litige manuel: {str(e)}")
            session.pop('pending_manual_litige', None)
            return STYLE + f"""
            <div style='text-align:center; padding:50px;'>
                <h1>❌ Erreur</h1>
                <p>Une erreur est survenue lors du traitement de votre dossier.</p>
                <p style='color:#dc2626; font-size:0.9rem;'>{str(e)[:100]}</p>
                <br>
                <a href='/declare' class='btn-success'>Réessayer</a>
            </div>
            """ + FOOTER
    
    # ════════════════════════════════════════════════════════════════
    # FLUX NORMAL - Traitement des litiges SCAN avec AGENTS GPT + GMAIL
    # ════════════════════════════════════════════════════════════════
    
    # Récupérer les litiges détectés depuis la session
    detected_litigations = session.get('detected_litigations', [])
    
    if not detected_litigations:
        return STYLE + """
        <div style='text-align:center; padding:50px;'>
            <h1>✅ Paiement enregistré</h1>
            <p>Votre carte a été enregistrée avec succès.</p>
            <br>
            <a href='/dashboard' class='btn-success' style='margin-right:10px;'>📂 Mes dossiers</a>
            <a href='/declare' class='btn-success' style='background:#10b981;'>✍️ Déclarer un litige</a>
        </div>
        """ + FOOTER
    
    DEBUG_LOGS.append(f"🚀 TRAITEMENT POST-PAIEMENT: {len(detected_litigations)} litige(s) pour {user.email}")
    
    # ════════════════════════════════════════════════════════════════
    # PRÉ-FILTRAGE : Vérifier les montants et doublons
    # ════════════════════════════════════════════════════════════════
    
    valid_litigations = []
    pre_errors = []
    
    for lit_data in detected_litigations:
        # Vérifier que le montant est valide
        if not is_valid_euro_amount(lit_data.get('amount', '')):
            pre_errors.append(f"⚠️ {lit_data.get('company', 'Inconnu')}: montant invalide ({lit_data.get('amount', 'N/A')})")
            continue
        
        # Vérification doublon
        company_normalized = lit_data.get('company', '').lower().strip()
        amount_numeric = extract_numeric_amount(lit_data.get('amount', '0'))
        
        is_duplicate = False
        if amount_numeric > 0:
            existing_cases = Litigation.query.filter_by(
                user_email=session['email'],
                company=company_normalized
            ).all()
            
            for existing in existing_cases:
                existing_amount = extract_numeric_amount(existing.amount)
                if existing_amount > 0 and abs(existing_amount - amount_numeric) <= 2:
                    is_duplicate = True
                    break
        
        if is_duplicate:
            pre_errors.append(f"🔄 {lit_data.get('company', '').upper()}: doublon ignoré")
            continue
        
        valid_litigations.append(lit_data)
    
    # ════════════════════════════════════════════════════════════════
    # 🚀 TRAITEMENT AVEC AGENTS (GPT + GMAIL)
    # ════════════════════════════════════════════════════════════════
    
    if valid_litigations:
        result = process_pending_litigations(user, valid_litigations)
        sent_count = result["sent"]
        errors = pre_errors + result["errors"]
        details = result["details"]
    else:
        sent_count = 0
        errors = pre_errors
        details = []
    
    # Vider la session
    session.pop('detected_litigations', None)
    session.pop('total_gain', None)
    
    # ════════════════════════════════════════════════════════════════
    # 📊 AFFICHAGE DU RAPPORT DÉTAILLÉ
    # ════════════════════════════════════════════════════════════════
    
    # Construire le rapport des envois
    report_html = ""
    if details:
        report_items = ""
        for d in details:
            if "✅" in d["status"]:
                status_style = "color:#10b981;"
                icon = "✅"
            else:
                status_style = "color:#dc2626;"
                icon = "❌"
            report_items += f"""
            <div style='display:flex; justify-content:space-between; align-items:center; 
                        padding:12px; margin:8px 0; background:#f8fafc; border-radius:8px;
                        border-left:4px solid {"#10b981" if "✅" in d["status"] else "#dc2626"};'>
                <div>
                    <strong style='text-transform:uppercase;'>{d["company"]}</strong>
                    <span style='color:#64748b; font-size:0.85rem; margin-left:10px;'>{d["amount"]}</span>
                </div>
                <div style='{status_style} font-weight:bold;'>{icon}</div>
            </div>
            """
        
        report_html = f"""
        <div style='background:white; padding:20px; border-radius:15px; margin:20px auto; max-width:450px;
                    box-shadow:0 4px 15px rgba(0,0,0,0.1);'>
            <h3 style='margin-top:0; color:#1e293b; border-bottom:2px solid #e2e8f0; padding-bottom:10px;'>
                📋 Rapport d'envoi
            </h3>
            {report_items}
        </div>
        """
    
    # Construire le bloc erreurs
    error_html = ""
    if errors:
        error_html = f"""
        <details style='margin:20px auto; max-width:450px;'>
            <summary style='cursor:pointer; color:#dc2626; font-size:0.9rem; padding:10px;
                          background:#fee2e2; border-radius:8px;'>
                ⚠️ {len(errors)} problème(s) rencontré(s)
            </summary>
            <div style='background:#fef2f2; padding:15px; border-radius:0 0 8px 8px; font-size:0.85rem;'>
                {"<br>".join(errors)}
            </div>
        </details>
        """
    
    # Message principal selon résultat
    if sent_count > 0:
        main_icon = "✅"
        main_title = f"{sent_count} Mise(s) en demeure envoyée(s) !"
        main_color = "#10b981"
        main_subtitle = "Les réclamations ont été envoyées aux entreprises concernées."
    elif valid_litigations:
        main_icon = "⚠️"
        main_title = "Envoi en cours de traitement"
        main_color = "#f59e0b"
        main_subtitle = "Certains envois nécessitent une vérification manuelle."
    else:
        main_icon = "ℹ️"
        main_title = "Aucun nouveau litige à traiter"
        main_color = "#3b82f6"
        main_subtitle = "Tous les litiges étaient déjà en cours de traitement."
    
    return STYLE + f"""
    <div style='max-width:550px; margin:0 auto; text-align:center; padding:30px;'>
        
        <!-- Badge succès principal -->
        <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); 
                    padding:40px; border-radius:20px; margin-bottom:25px;'>
            <div style='font-size:4rem; margin-bottom:15px;'>{main_icon}</div>
            <h1 style='color:#065f46; margin:0 0 10px 0;'>{main_title}</h1>
            <p style='color:#047857; margin:0;'>{main_subtitle}</p>
        </div>
        
        <!-- Info carte -->
        <div style='background:#ecfdf5; padding:15px; border-radius:10px; margin-bottom:20px;
                    border-left:4px solid #10b981;'>
            <p style='margin:0; color:#065f46; font-size:0.9rem;'>
                <b>💳 Paiement sécurisé !</b>
            </p>
        </div>
        
        <!-- Info BCC -->
        {f'''<div style='background:#dbeafe; padding:15px; border-radius:10px; margin-bottom:20px;
                    border-left:4px solid #3b82f6;'>
            <p style='margin:0; color:#1e40af; font-size:0.9rem;'>
                <b>📧 Copie dans votre boîte mail !</b><br>
                Vous recevez automatiquement une copie de chaque mise en demeure envoyée.
            </p>
        </div>''' if sent_count > 0 else ''}
        
        {report_html}
        {error_html}
        
        <!-- Actions -->
        <div style='margin-top:30px;'>
            <a href='/dashboard' class='btn-success' style='display:inline-block; padding:15px 30px; margin:5px;'>
                📂 VOIR MES DOSSIERS
            </a>
        </div>
        
    </div>
    """ + FOOTER

# ========================================
# WEBHOOK STRIPE
# ========================================

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    """Gère les webhooks Stripe"""
    DEBUG_LOGS.append(f"🔔 Webhook reçu à {datetime.utcnow()}")
    
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        
        if event["type"] == "setup_intent.succeeded":
            intent = event["data"]["object"]
            customer_id = intent.get("customer")
            
            litigations = Litigation.query.filter_by(status="Détecté").all()
            
            for lit in litigations:
                user = User.query.filter_by(email=lit.user_email).first()
                if not user or not user.refresh_token:
                    continue
                
                if not user.stripe_customer_id:
                    user.stripe_customer_id = customer_id
                    db.session.commit()
                
                # Vérifier que le montant est valide avant d'envoyer
                if not is_valid_euro_amount(lit.amount):
                    DEBUG_LOGS.append(f"⚠️ Montant invalide pour {lit.company}: {lit.amount}")
                    continue
                
                try:
                    creds = get_refreshed_credentials(user.refresh_token)
                    company_key = lit.company.lower()
                    legal_info = LEGAL_DIRECTORY.get(company_key, {
                        "email": "theodordelgao@gmail.com",
                        "loi": "le Droit Européen de la Consommation"
                    })
                    
                    target_email = legal_info["email"]
                    
                    corps = f"""MISE EN DEMEURE FORMELLE

Objet : Réclamation concernant le dossier : {lit.subject}

À l'attention du Service Juridique de {lit.company.upper()},

Je soussigné(e), {user.name}, vous informe par la présente de mon intention de réclamer une indemnisation pour le litige suivant :

- Nature du litige : {lit.subject}
- Fondement juridique : {lit.law}
- Montant réclamé : {lit.amount}

Conformément à la législation en vigueur, je vous mets en demeure de procéder au remboursement sous un délai de 8 jours ouvrés.

À défaut de réponse satisfaisante, je me réserve le droit de saisir les autorités compétentes.

Cordialement,
{user.name}
{user.email}
"""
                    
                    if send_litigation_email(creds, target_email, f"MISE EN DEMEURE - {lit.company.upper()}", corps):
                        lit.status = "En attente de remboursement"
                        send_telegram_notif(f"💰 **JUSTICIO** : Dossier {lit.amount} envoyé à {lit.company.upper()} !")
                        DEBUG_LOGS.append(f"✅ Mail envoyé pour {lit.company}")
                
                except Exception as e:
                    DEBUG_LOGS.append(f"❌ Erreur envoi {lit.company}: {str(e)}")
            
            db.session.commit()
    
    except Exception as e:
        DEBUG_LOGS.append(f"❌ Erreur webhook: {str(e)}")
    
    return "OK", 200

# ========================================
# CRON JOB - CHASSEUR DE REMBOURSEMENTS
# ========================================

SCAN_TOKEN = os.environ.get("SCAN_TOKEN")


def generate_company_variants(company_name: str) -> list:
    """
    🔍 Génère les variantes possibles d'un nom d'entreprise pour le filtrage.
    Ex: "Air France" → ["air france", "airfrance", "air-france", "af", "air france klm"]
    """
    company_lower = company_name.strip().lower()
    variants = [company_lower]
    
    # Sans espaces
    variants.append(company_lower.replace(" ", ""))
    
    # Avec tirets
    variants.append(company_lower.replace(" ", "-"))
    
    # Acronymes connus
    COMPANY_ACRONYMS = {
        "air france": ["af", "air france klm"],
        "sncf": ["ter", "tgv", "ouigo", "inoui", "intercités"],
        "british airways": ["ba"],
        "klm": ["klm royal dutch"],
        "lufthansa": ["lh"],
        "easy jet": ["easyjet"],
        "easyjet": ["easy jet"],
        "ryanair": ["ryr", "fr"],
        "transavia": ["to", "hv"],
    }
    
    if company_lower in COMPANY_ACRONYMS:
        variants.extend(COMPANY_ACRONYMS[company_lower])
    
    # Chercher si c'est un acronyme inverse
    for full_name, acronyms in COMPANY_ACRONYMS.items():
        if company_lower in acronyms:
            variants.append(full_name)
    
    # Mots individuels (si plus de 1 mot)
    words = company_lower.split()
    if len(words) > 1:
        variants.extend(words)
    
    return list(set(variants))  # Dédupliquer


@app.route("/cron/check-refunds")
def check_refunds():
    """
    💰 AGENT 2 : L'ENCAISSEUR
    Vérifie les remboursements et prélève la commission
    
    GÈRE 3 SCÉNARIOS :
    1. Remboursement PARTIEL → Accepter et facturer sur le montant réel
    2. Bon d'achat/Avoir → Fermer le dossier SANS facturer
    3. Remboursement IMPLICITE → Utiliser le montant du dossier
    
    V2: Query Gmail "Grand Filet" + Filtrage Python intelligent
    """
    
    # Vérification du token de sécurité
    token = request.args.get("token")
    if SCAN_TOKEN and token != SCAN_TOKEN:
        return "⛔ Accès refusé - Token invalide", 403
    
    logs = ["<h3>💰 AGENT ENCAISSEUR V2 - GRAND FILET</h3>"]
    logs.append(f"<p>🕐 Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>")
    
    # Statistiques
    stats = {
        "dossiers_scannes": 0,
        "remboursements_cash": 0,
        "remboursements_voucher": 0,
        "remboursements_partiels": 0,
        "annulations": 0,
        "commissions_prelevees": 0,
        "total_commission": 0,
        "rejets_securite": 0,
        "emails_filtres": 0,  # Emails non pertinents filtrés
        "montants_mis_a_jour": 0  # Montants corrigés en BDD
    }
    
    # ════════════════════════════════════════════════════════════════
    # FILTRE ÉLARGI : Surveiller TOUS les dossiers actifs
    # ════════════════════════════════════════════════════════════════
    
    STATUTS_ACTIFS = [
        "En attente de remboursement",
        "En cours juridique",
        "En cours",
        "Envoyé",
        "En attente d'analyse",
        "Détecté"
    ]
    
    active_cases = Litigation.query.filter(
        Litigation.status.in_(STATUTS_ACTIFS)
    ).all()
    
    logs.append(f"<p>📂 {len(active_cases)} dossier(s) actifs à surveiller</p>")
    logs.append(f"<p style='font-size:0.8rem; color:#64748b;'>Statuts surveillés : {', '.join(STATUTS_ACTIFS)}</p>")
    
    # ANTI-DOUBLON : Tracker les emails déjà utilisés
    used_email_ids = set()
    
    for case in active_cases:
        stats["dossiers_scannes"] += 1
        
        company_clean = case.company.strip().lower()
        company_variants = generate_company_variants(company_clean)  # Variantes du nom
        expected_amount = extract_numeric_amount(case.amount)
        
        logs.append(f"<hr>📂 <b>{company_clean.upper()}</b> - {case.amount} (attendu: {expected_amount}€)")
        logs.append(f"<p style='margin-left:20px; color:#6b7280; font-size:0.85rem;'>Variantes recherchées: {', '.join(company_variants[:5])}...</p>")
        
        user = User.query.filter_by(email=case.user_email).first()
        if not user or not user.refresh_token:
            logs.append("<p style='margin-left:20px; color:#dc2626;'>❌ Pas de refresh token</p>")
            continue
        
        if not user.stripe_customer_id:
            logs.append("<p style='margin-left:20px; color:#dc2626;'>❌ Pas de carte enregistrée</p>")
            continue
        
        try:
            creds = get_refreshed_credentials(user.refresh_token)
            service = build('gmail', 'v1', credentials=creds)
            
            # ════════════════════════════════════════════════════════════════
            # 🎣 QUERY "GRAND FILET" - Sans le nom d'entreprise !
            # Cherche TOUS les emails financiers sur 30 jours
            # ════════════════════════════════════════════════════════════════
            query = '''(
                subject:virement OR subject:remboursement OR subject:refund 
                OR subject:indemnisation OR subject:compensation OR subject:crédit
                OR "avis de virement" OR "compte crédité" OR "has been refunded"
                OR "remboursement effectué" OR "votre compte a été crédité"
                OR "montant remboursé" OR "refund processed" OR "payment received"
                OR "bon d'achat" OR "avoir" OR "voucher" OR "geste commercial"
                OR "code promo" OR "crédit boutique"
                OR "annulation" OR "annulée" OR "cancelled" OR "commande annulée"
                OR subject:TEST
            ) newer_than:30d -subject:"MISE EN DEMEURE"'''
            
            logs.append(f"<p style='margin-left:20px; color:#6b7280; font-size:0.85rem;'>🎣 Query GRAND FILET (30 jours, sans entreprise)</p>")
            
            results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
            messages = results.get('messages', [])
            
            logs.append(f"<p style='margin-left:20px;'>📧 <b>{len(messages)}</b> email(s) financiers trouvés</p>")
            
            if len(messages) == 0:
                logs.append("<p style='margin-left:20px; color:#f59e0b;'>⚠️ Aucun email financier détecté</p>")
                continue
            
            found_valid_refund = False
            
            for msg in messages:
                msg_id = msg['id']
                
                if msg_id in used_email_ids:
                    continue
                
                msg_data = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
                snippet = msg_data.get('snippet', '')
                
                headers = msg_data['payload'].get('headers', [])
                email_date = next((h['value'] for h in headers if h['name'].lower() == 'date'), "Date inconnue")
                email_subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "Sans sujet")
                email_from = next((h['value'] for h in headers if h['name'].lower() == 'from'), "")
                
                # Extraire le body pour analyse
                try:
                    body_text = safe_extract_body_text(msg_data)
                except:
                    body_text = snippet
                
                # ════════════════════════════════════════════════════════════════
                # 🔍 FILTRAGE PYTHON INTELLIGENT
                # Vérifier si l'email correspond à cette entreprise
                # ════════════════════════════════════════════════════════════════
                
                email_blob = f"{email_subject} {snippet} {body_text} {email_from}".lower()
                
                # Accepter si :
                # 1. Le nom de l'entreprise (ou variante) est dans l'email
                # 2. OU c'est un email de TEST (pour les tests admin)
                # 3. OU l'email vient d'un admin connu
                is_company_match = any(variant in email_blob for variant in company_variants)
                is_test_email = "test" in email_subject.lower() or "test" in email_from.lower()
                is_admin_email = any(admin in email_from.lower() for admin in ["admin@", "theodor", "justicio"])
                
                if not is_company_match and not is_test_email and not is_admin_email:
                    stats["emails_filtres"] += 1
                    continue  # Pas le bon email, passer au suivant
                
                if "MISE EN DEMEURE" in email_subject.upper():
                    continue
                
                logs.append(f"<p style='margin-left:30px;'>📩 <b>{email_subject[:60]}...</b></p>")
                logs.append(f"<p style='margin-left:40px; color:#6b7280; font-size:0.85rem;'>De: {email_from[:40]} | {email_date[:20]}</p>")
                
                if is_test_email or is_admin_email:
                    logs.append(f"<p style='margin-left:40px; color:#8b5cf6; font-size:0.85rem;'>🧪 Mode TEST accepté</p>")
                
                if not OPENAI_API_KEY:
                    logs.append("<p style='margin-left:30px; color:#dc2626;'>❌ Pas d'API OpenAI</p>")
                    continue
                
                # ANALYSE IA SÉCURISÉE
                verdict_result = analyze_refund_email(
                    company_clean, 
                    expected_amount, 
                    email_subject, 
                    snippet, 
                    email_from,
                    case_order_id=getattr(case, 'order_id', None)
                )
                
                verdict = verdict_result.get("verdict", "NON")
                montant_reel = verdict_result.get("montant_reel", 0)
                type_remboursement = verdict_result.get("type", "UNKNOWN")
                order_id_found = verdict_result.get("order_id", None)
                is_credit = verdict_result.get("is_credit", True)
                is_partial = verdict_result.get("is_partial", False)
                is_cancelled = verdict_result.get("is_cancelled", False)
                confidence = verdict_result.get("confidence", "LOW")
                raison = verdict_result.get("raison", "")
                
                logs.append(f"<p style='margin-left:30px;'>🤖 Verdict: <b>{verdict}</b> | Montant: <b>{montant_reel}€</b> | Type: <b>{type_remboursement}</b> | Partiel: <b>{'OUI' if is_partial else 'NON'}</b> | Confiance: <b>{confidence}</b></p>")
                if order_id_found:
                    logs.append(f"<p style='margin-left:40px; color:#6b7280; font-size:0.85rem;'>📦 N° Commande trouvé: {order_id_found}</p>")
                if raison:
                    logs.append(f"<p style='margin-left:40px; color:#6b7280; font-size:0.85rem;'>ℹ️ {raison[:100]}</p>")
                
                # ════════════════════════════════════════════════════════════════
                # 💾 MISE À JOUR DU MONTANT EN BDD (si différent)
                # ════════════════════════════════════════════════════════════════
                
                if verdict == "OUI" and montant_reel > 0 and montant_reel != expected_amount:
                    old_amount = case.amount
                    case.amount = f"{montant_reel}€"
                    db.session.commit()
                    stats["montants_mis_a_jour"] += 1
                    logs.append(f"<p style='margin-left:30px; color:#3b82f6;'>📝 MONTANT MIS À JOUR : {old_amount} → {montant_reel}€</p>")
                    # Recalculer expected_amount pour la suite
                    expected_amount = montant_reel
                
                # ═══════════════════════════════════════════════════════════
                # 🚫 CAS SPÉCIAL : ANNULATION SANS DÉBIT
                # ═══════════════════════════════════════════════════════════
                
                if verdict == "ANNULE" or is_cancelled or type_remboursement == "CANCELLED":
                    logs.append(f"<p style='margin-left:30px; color:#8b5cf6;'>🚫 ANNULATION DÉTECTÉE : Commande annulée sans débit</p>")
                    logs.append(f"<p style='margin-left:40px; color:#8b5cf6; font-size:0.85rem;'>→ Aucune transaction financière - Pas de commission à prélever</p>")
                    
                    used_email_ids.add(msg_id)
                    stats["annulations"] += 1
                    
                    case.status = "Annulé (sans débit)"
                    case.updated_at = datetime.utcnow()
                    db.session.commit()
                    
                    logs.append(f"<p style='margin-left:30px; color:#8b5cf6; font-weight:bold;'>✅ Dossier fermé - Annulation confirmée</p>")
                    
                    send_telegram_notif(f"🚫 ANNULATION DÉTECTÉE 🚫\n\n{company_clean.upper()} : Commande annulée sans débit\nClient: {user.email}\nDossier #{case.id}\n⚠️ PAS DE COMMISSION (0€)")
                    
                    found_valid_refund = True
                    break
                
                # ═══════════════════════════════════════════════════════════
                # 🔒 VALIDATIONS DE SÉCURITÉ - ANTI FAUX-POSITIFS
                # ═══════════════════════════════════════════════════════════
                
                if verdict == "OUI":
                    
                    # SÉCURITÉ 1 : Vérifier que c'est un CRÉDIT (pas une facture)
                    if not is_credit:
                        logs.append(f"<p style='margin-left:30px; color:#dc2626;'>🚫 REJET : C'est une FACTURE (débit), pas un remboursement (crédit)</p>")
                        stats["rejets_securite"] += 1
                        continue
                    
                    # SÉCURITÉ 2 : Vérifier le montant (règle des 90%)
                    # EXCEPTION : Si is_partial=True, accepter même si < 90%
                    if montant_reel > 0 and expected_amount > 0:
                        ratio = montant_reel / expected_amount
                        
                        # Si le montant trouvé est < 90% du montant attendu
                        if ratio < 0.90:
                            # L'IA ou Python a détecté un PARTIEL → ACCEPTER
                            if is_partial:
                                logs.append(f"<p style='margin-left:30px; color:#f59e0b;'>✅ PARTIEL DÉTECTÉ : {montant_reel}€ sur {expected_amount}€ ({ratio*100:.0f}%)</p>")
                                logs.append(f"<p style='margin-left:40px; color:#f59e0b; font-size:0.85rem;'>→ Contexte partiel identifié (geste commercial, frais déduits, etc.)</p>")
                                # CONTINUER - ne pas rejeter
                            else:
                                # Pas de contexte partiel → REJET (probablement autre commande)
                                logs.append(f"<p style='margin-left:30px; color:#dc2626;'>🚫 REJET SÉCURITÉ : Montant trouvé ({montant_reel}€) ≠ Montant dossier ({expected_amount}€)</p>")
                                logs.append(f"<p style='margin-left:40px; color:#dc2626; font-size:0.85rem;'>→ Ratio: {ratio*100:.0f}% < 90% et aucun contexte partiel - Probablement une AUTRE commande !</p>")
                                stats["rejets_securite"] += 1
                                continue
                        else:
                            logs.append(f"<p style='margin-left:30px; color:#10b981;'>✅ Montant validé : {montant_reel}€ ≈ {expected_amount}€ ({ratio*100:.0f}%)</p>")
                    
                    # SÉCURITÉ 3 : Comparer les numéros de commande (si disponibles)
                    case_order_id = getattr(case, 'order_id', None)
                    if case_order_id and order_id_found:
                        # Normaliser les deux IDs pour comparaison
                        case_id_clean = str(case_order_id).strip().lower().replace("#", "").replace("-", "")
                        found_id_clean = str(order_id_found).strip().lower().replace("#", "").replace("-", "")
                        
                        if case_id_clean != found_id_clean:
                            logs.append(f"<p style='margin-left:30px; color:#dc2626;'>🚫 REJET : Numéros de commande DIFFÉRENTS !</p>")
                            logs.append(f"<p style='margin-left:40px; color:#dc2626; font-size:0.85rem;'>→ Dossier: {case_order_id} | Email: {order_id_found}</p>")
                            stats["rejets_securite"] += 1
                            continue
                        else:
                            logs.append(f"<p style='margin-left:30px; color:#10b981;'>✅ Numéro de commande validé : {order_id_found}</p>")
                    
                    # SÉCURITÉ 4 : Niveau de confiance minimum
                    if confidence == "LOW":
                        logs.append(f"<p style='margin-left:30px; color:#f59e0b;'>⚠️ Confiance faible - Vérification manuelle recommandée</p>")
                    
                    # ═══════════════════════════════════════════════════════════
                    # ✅ TOUTES LES SÉCURITÉS PASSÉES - TRAITEMENT DU REMBOURSEMENT
                    # ═══════════════════════════════════════════════════════════
                    
                    used_email_ids.add(msg_id)
                    
                    # Utiliser is_partial de l'IA OU comparer les montants
                    is_partial_final = is_partial or (montant_reel < expected_amount * 0.99)  # 1% de tolérance
                    if is_partial_final:
                        stats["remboursements_partiels"] += 1
                        logs.append(f"<p style='margin-left:30px; color:#f59e0b;'>⚠️ PARTIEL CONFIRMÉ : {montant_reel}€ sur {expected_amount}€</p>")
                    
                    # CAS 1 : CASH → DÉBITER STRIPE
                    if type_remboursement == "CASH":
                        stats["remboursements_cash"] += 1
                        
                        if montant_reel <= 0:
                            logs.append("<p style='margin-left:30px; color:#dc2626;'>❌ Montant invalide</p>")
                            continue
                        
                        commission = max(1, int(montant_reel * 0.30))
                        logs.append(f"<p style='margin-left:30px;'>💰 Commission : <b>{commission}€</b> (30% de {montant_reel}€)</p>")
                        
                        try:
                            payment_methods = stripe.PaymentMethod.list(customer=user.stripe_customer_id, type="card")
                            
                            if not payment_methods.data:
                                logs.append("<p style='margin-left:30px; color:#dc2626;'>❌ Aucune carte</p>")
                                continue
                            
                            payment_intent = stripe.PaymentIntent.create(
                                amount=commission * 100,
                                currency='eur',
                                customer=user.stripe_customer_id,
                                payment_method=payment_methods.data[0].id,
                                off_session=True,
                                confirm=True,
                                description=f"Commission Justicio 30% - {company_clean.upper()} - Dossier #{case.id}"
                            )
                            
                            if payment_intent.status == "succeeded":
                                if is_partial_final:
                                    case.status = f"Remboursé (Partiel: {montant_reel}€/{expected_amount}€)"
                                else:
                                    case.status = "Remboursé"
                                case.updated_at = datetime.utcnow()
                                db.session.commit()
                                
                                stats["commissions_prelevees"] += 1
                                stats["total_commission"] += commission
                                
                                logs.append(f"<p style='margin-left:30px; color:#10b981; font-weight:bold;'>✅ JACKPOT ! {commission}€ PRÉLEVÉS !</p>")
                                
                                partial_info = f" (PARTIEL: {montant_reel}€/{expected_amount}€)" if is_partial_final else ""
                                send_telegram_notif(f"💰💰💰 JUSTICIO JACKPOT 💰💰💰\n\n{commission}€ prélevés sur {company_clean.upper()}{partial_info}\nClient: {user.email}\nDossier #{case.id}\nType: CASH")
                                
                                try:
                                    service.users().messages().modify(userId='me', id=msg_id, body={'removeLabelIds': ['INBOX']}).execute()
                                except:
                                    pass
                                
                                found_valid_refund = True
                                break
                            else:
                                logs.append(f"<p style='margin-left:30px; color:#dc2626;'>❌ Paiement non confirmé</p>")
                        
                        except stripe.error.CardError as e:
                            logs.append(f"<p style='margin-left:30px; color:#dc2626;'>❌ Erreur carte : {e.user_message}</p>")
                        except Exception as e:
                            logs.append(f"<p style='margin-left:30px; color:#dc2626;'>❌ Erreur : {str(e)[:50]}</p>")
                    
                    # CAS 2 : VOUCHER → NE PAS DÉBITER
                    elif type_remboursement == "VOUCHER":
                        stats["remboursements_voucher"] += 1
                        
                        case.status = f"Résolu (Bon d'achat: {montant_reel}€)"
                        case.updated_at = datetime.utcnow()
                        db.session.commit()
                        
                        logs.append(f"<p style='margin-left:30px; color:#f59e0b; font-weight:bold;'>🎫 BON D'ACHAT - Fermé SANS commission</p>")
                        
                        send_telegram_notif(f"🎫 VOUCHER DÉTECTÉ 🎫\n\n{company_clean.upper()} : bon d'achat de {montant_reel}€\nClient: {user.email}\nDossier #{case.id}\n⚠️ PAS DE COMMISSION")
                        
                        try:
                            service.users().messages().modify(userId='me', id=msg_id, body={'removeLabelIds': ['INBOX']}).execute()
                        except:
                            pass
                        
                        found_valid_refund = True
                        break
            
            if not found_valid_refund:
                logs.append(f"<p style='margin-left:20px; color:#6b7280;'>ℹ️ Aucun remboursement valide</p>")
        
        except Exception as e:
            logs.append(f"<p style='color:#dc2626;'>❌ Erreur : {str(e)[:80]}</p>")
            DEBUG_LOGS.append(f"CRON Error {company_clean}: {str(e)}")
    
    # RAPPORT FINAL
    logs.append("<hr>")
    logs.append("<h4>📊 Rapport de l'Encaisseur V2</h4>")
    logs.append(f"""
    <div style='background:#f8fafc; padding:15px; border-radius:10px; margin:10px 0;'>
        <p>📂 Dossiers scannés : <b>{stats['dossiers_scannes']}</b></p>
        <p>🎣 Emails filtrés (non pertinents) : <b>{stats['emails_filtres']}</b></p>
        <p>💵 Remboursements CASH : <b>{stats['remboursements_cash']}</b></p>
        <p>🎫 Remboursements VOUCHER : <b>{stats['remboursements_voucher']}</b> (sans commission)</p>
        <p>📉 Remboursements PARTIELS : <b>{stats['remboursements_partiels']}</b></p>
        <p style='color:#3b82f6;'>📝 Montants mis à jour : <b>{stats['montants_mis_a_jour']}</b></p>
        <p style='color:#8b5cf6;'>🚫 Annulations (sans débit) : <b>{stats['annulations']}</b> (pas de commission)</p>
        <p style='color:#dc2626;'>⚠️ Rejets SÉCURITÉ : <b>{stats['rejets_securite']}</b> (faux positifs évités)</p>
        <p style='color:#10b981; font-weight:bold;'>💰 Commissions prélevées : <b>{stats['commissions_prelevees']}</b> = <b>{stats['total_commission']}€</b></p>
    </div>
    """)
    
    if stats['rejets_securite'] > 0:
        logs.append(f"<p style='color:#f59e0b;'>⚠️ {stats['rejets_securite']} faux positif(s) évité(s) grâce aux validations de sécurité</p>")
    
    logs.append(f"<p>✅ Scan terminé à {datetime.utcnow().strftime('%H:%M:%S')} UTC</p>")
    
    return STYLE + "<br>".join(logs) + "<br><br><a href='/' class='btn-success'>Retour</a>"


def analyze_refund_email(company, expected_amount, subject, snippet, email_from, case_order_id=None):
    """
    💰 ANALYSEUR DE REMBOURSEMENT - Version SÉCURISÉE
    
    Retourne : {
        verdict: OUI/NON/ANNULE,
        montant_reel: float,
        type: CASH/VOUCHER/CANCELLED/NONE,
        order_id: str ou None,
        is_credit: bool (True = remboursement, False = facture/débit),
        is_partial: bool (True = remboursement partiel détecté),
        is_cancelled: bool (True = annulation sans débit),
        confidence: HIGH/MEDIUM/LOW,
        raison: str
    }
    
    SÉCURITÉS :
    1. Vérifie que c'est un CRÉDIT (remboursement) pas un DÉBIT (facture)
    2. Extrait le numéro de commande pour comparaison
    3. Détecte les partiels explicites ET implicites
    4. Détecte les annulations sans débit
    """
    
    if not OPENAI_API_KEY:
        return {"verdict": "NON", "montant_reel": 0, "type": "NONE", "order_id": None, "is_credit": False, "is_partial": False, "is_cancelled": False, "confidence": "LOW", "raison": "Pas d'API"}
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""Tu es un AUDITEUR FINANCIER EXPERT. Analyse cet email pour déterminer s'il confirme un REMBOURSEMENT EFFECTUÉ.

DOSSIER EN ATTENTE :
- Entreprise : {company.upper()}
- Montant attendu : {expected_amount}€
- Numéro de commande connu : {case_order_id or "NON RENSEIGNÉ"}

EMAIL À ANALYSER :
- Expéditeur : {email_from}
- Sujet : "{subject}"
- Contenu : "{snippet}"

═══════════════════════════════════════════════════════════════
🚨 RÈGLE PRIORITAIRE : ANNULATIONS SANS DÉBIT (CRUCIAL)
═══════════════════════════════════════════════════════════════

⚠️ Une ANNULATION avant expédition N'EST PAS un remboursement !
Si l'email indique qu'il n'y a AUCUN flux financier :

🚫 MOTS-CLÉS D'ANNULATION SANS DÉBIT :
- "ne sera pas débité", "will not be charged"
- "aucune transaction", "no transaction"
- "empreinte bancaire relâchée", "authorization released"
- "commande annulée avant expédition"
- "annulée sans frais", "cancelled without charge"
- "aucun prélèvement", "aucun montant prélevé"
- "votre carte ne sera pas débitée"
- "pas de facturation", "not billed"

→ Si tu détectes une ANNULATION SANS DÉBIT :
   Réponds : "ANNULE | 0 | CANCELLED | [ORDER_ID] | FALSE | HIGH"
   
⚠️ IMPORTANT : Récupérer 0€ sur une annulation est NORMAL !
   Ne force PAS un match avec le montant du dossier.

═══════════════════════════════════════════════════════════════
🚨 RÈGLES DE SÉCURITÉ CRITIQUES
═══════════════════════════════════════════════════════════════

1. CRÉDIT vs DÉBIT (OBLIGATOIRE) :
   ✅ CRÉDIT (remboursement) = argent VERS le client : "remboursé", "crédité", "virement effectué"
   ❌ DÉBIT (facture) = argent DU client : "facture", "prélèvement", "paiement effectué"
   → Si c'est un DÉBIT, réponds NON immédiatement !

2. CORRESPONDANCE ENTREPRISE :
   → L'email DOIT concerner {company.upper()} (pas une autre entreprise)

3. NUMÉRO DE COMMANDE (si présent) :
   → Extrais tout numéro de commande/référence du mail (ex: #12345, N°ABC123, Réf: XYZ)
   → Format: Juste le numéro sans préfixe

═══════════════════════════════════════════════════════════════
💡 DÉTECTION DES REMBOURSEMENTS PARTIELS (CRUCIAL)
═══════════════════════════════════════════════════════════════

Un remboursement PARTIEL est VALIDE même si le montant < {expected_amount}€ !
Détecte un PARTIEL si tu trouves UN de ces indices :

📝 VOCABULAIRE EXPLICITE :
- "remboursement partiel", "partiel", "acompte"
- "premier versement", "versement partiel"
- "en partie", "partie de", "une partie"

💼 VOCABULAIRE CONTEXTUEL (pas besoin du mot "partiel") :
- "ajustement en votre faveur"
- "remboursement de la différence"
- "remboursement des articles manquants"
- "remboursement des frais de port uniquement"
- "geste commercial", "dédommagement"
- "déduction faite des frais de retour"
- "frais retenus", "frais déduits"
- "solde restant", "reste à rembourser"
- "nous avons retenu X%", "retenue de X€"
- "remboursement pour l'article X" (si commande multi-articles)

🔢 ANALYSE MATHÉMATIQUE :
- Si montant trouvé < montant attendu ({expected_amount}€)
- ET que le contexte EXPLIQUE la différence (frais, articles spécifiques, retenue)
- ALORS c'est un PARTIEL VALIDE (pas un rejet !)

⚠️ EXEMPLES PARTIELS VALIDES :
- "Remboursement de 250€ après déduction de 50% de frais" sur dossier 500€ → PARTIEL OK
- "Remboursement des frais de port (15€)" sur dossier 89€ → PARTIEL OK
- "Geste commercial de 30€" sur dossier 120€ → PARTIEL OK
- "Remboursement article A (45€)" si commande contenait A+B → PARTIEL OK

═══════════════════════════════════════════════════════════════
📊 MONTANT & CONFIANCE
═══════════════════════════════════════════════════════════════

MONTANT :
- Extrais le montant EXACT mentionné (pas d'estimation)
- Si "remboursement intégral/total" sans montant → utilise {expected_amount}
- Si montant différent SANS explication → MEDIUM confidence

CONFIANCE :
- HIGH = Montant exact ({expected_amount}€) OU Partiel explicitement justifié
- MEDIUM = Montant différent avec explication partielle
- LOW = Promesse future, incertitude, ou montant inexpliqué

═══════════════════════════════════════════════════════════════
FORMAT DE RÉPONSE (6 éléments séparés par |)
═══════════════════════════════════════════════════════════════

VERDICT | MONTANT | TYPE | ORDER_ID | IS_PARTIAL | CONFIANCE

VERDICT : OUI (remboursement confirmé) ou NON (pas de remboursement)
MONTANT : Le montant en euros (nombre uniquement, ex: 42.99)
TYPE : CASH (virement/CB) ou VOUCHER (bon d'achat) ou NONE
ORDER_ID : Le numéro de commande extrait ou NONE
IS_PARTIAL : TRUE si c'est un remboursement partiel, FALSE sinon
CONFIANCE : HIGH, MEDIUM, ou LOW

═══════════════════════════════════════════════════════════════
EXEMPLES
═══════════════════════════════════════════════════════════════

Remboursement total Amazon 50€ :
→ "OUI | 50 | CASH | 123456 | FALSE | HIGH"

Remboursement partiel explicite 20€ sur 100€ :
→ "OUI | 20 | CASH | 789012 | TRUE | HIGH"

Geste commercial 30€ sur dossier 150€ :
→ "OUI | 30 | CASH | NONE | TRUE | HIGH"

Remboursement frais de port uniquement 8€ sur dossier 89€ :
→ "OUI | 8 | CASH | 456789 | TRUE | HIGH"

Remboursement 250€ avec "50% retenus" sur dossier 500€ :
→ "OUI | 250 | CASH | 111222 | TRUE | HIGH"

Email de FACTURE (pas remboursement) :
→ "NON | 0 | NONE | NONE | FALSE | LOW"

Bon d'achat Zalando 30€ :
→ "OUI | 30 | VOUCHER | 456789 | FALSE | HIGH"

Promesse future de remboursement :
→ "NON | 0 | NONE | NONE | FALSE | LOW"

ANNULATION sans débit ("ne sera pas débité") :
→ "ANNULE | 0 | CANCELLED | 123456 | FALSE | HIGH"

Commande annulée avant expédition :
→ "ANNULE | 0 | CANCELLED | 789012 | FALSE | HIGH"

Ta réponse (UNE SEULE LIGNE) :"""

    # Vocabulaire élargi pour détection Python des partiels
    PARTIAL_KEYWORDS = [
        # Explicites
        "partiel", "acompte", "premier versement", "versement partiel",
        "en partie", "partie de", "une partie",
        # Contextuels
        "ajustement", "différence", "articles manquants",
        "frais de port uniquement", "frais de retour",
        "geste commercial", "dédommagement", "compensation",
        "déduction", "déduit", "retenu", "retenue",
        "solde restant", "reste à", "frais retenus",
        "remboursement pour l'article", "remboursement de l'article",
        "50%", "pourcentage", "prorata"
    ]
    
    # Vocabulaire pour détection des annulations sans débit
    CANCELLED_NO_CHARGE_KEYWORDS = [
        "ne sera pas débité", "will not be charged",
        "aucune transaction", "no transaction",
        "empreinte bancaire relâchée", "authorization released",
        "annulée avant expédition", "cancelled before shipping",
        "annulée sans frais", "cancelled without charge",
        "aucun prélèvement", "aucun montant prélevé",
        "votre carte ne sera pas débitée", "carte non débitée",
        "pas de facturation", "not billed", "won't be charged",
        "commande annulée", "order cancelled", "order canceled"
    ]

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=100
        )
        
        result = response.choices[0].message.content.strip()
        parts = [p.strip() for p in result.split("|")]
        
        if len(parts) >= 5:
            # Gérer les 3 verdicts possibles : OUI, NON, ANNULE
            verdict_raw = parts[0].upper().strip()
            if verdict_raw.startswith("OUI"):
                verdict = "OUI"
            elif verdict_raw.startswith("ANNUL"):
                verdict = "ANNULE"
            else:
                verdict = "NON"
            
            # Montant
            try:
                montant_str = parts[1].replace("€", "").replace(",", ".").strip()
                montant_reel = float(montant_str)
            except:
                montant_reel = 0
            
            # Type - Inclut maintenant CANCELLED
            type_raw = parts[2].upper().strip()
            if "VOUCHER" in type_raw or "BON" in type_raw or "AVOIR" in type_raw:
                type_remboursement = "VOUCHER"
            elif "CANCEL" in type_raw:
                type_remboursement = "CANCELLED"
            elif "CASH" in type_raw or "VIREMENT" in type_raw:
                type_remboursement = "CASH"
            else:
                type_remboursement = "NONE"
            
            # Order ID
            order_id_raw = parts[3].strip()
            order_id = None if order_id_raw.upper() == "NONE" or order_id_raw == "" else order_id_raw
            
            # IS_PARTIAL (nouveau - index 4)
            is_partial_from_ia = False
            if len(parts) >= 5:
                is_partial_raw = parts[4].upper().strip()
                is_partial_from_ia = "TRUE" in is_partial_raw or "VRAI" in is_partial_raw or "OUI" in is_partial_raw
            
            # Confiance (index 5, ou index 4 si ancien format)
            if len(parts) >= 6:
                confidence_raw = parts[5].upper().strip()
            else:
                confidence_raw = parts[4].upper().strip()  # Fallback ancien format
            
            if "HIGH" in confidence_raw:
                confidence = "HIGH"
            elif "MEDIUM" in confidence_raw:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            
            # Détection Python des partiels (en complément de l'IA)
            text_to_check = (snippet + " " + subject).lower()
            is_partial_from_keywords = any(kw in text_to_check for kw in PARTIAL_KEYWORDS)
            
            # Détection mathématique : si montant < 90% du attendu, potentiellement partiel
            is_partial_from_math = False
            if montant_reel > 0 and expected_amount > 0:
                ratio = montant_reel / expected_amount
                if ratio < 0.90 and ratio > 0.01:  # Entre 1% et 90%
                    is_partial_from_math = True
            
            # Fusion : partiel si l'IA dit TRUE OU si keywords détectés OU si math + contexte
            is_partial = is_partial_from_ia or is_partial_from_keywords or (is_partial_from_math and is_partial_from_keywords)
            
            # Déterminer si c'est un crédit (remboursement) vs débit (facture)
            debit_keywords = ["facture", "prélèvement", "paiement effectué", "montant débité", "a été prélevé"]
            is_credit = not any(kw in text_to_check for kw in debit_keywords)
            
            # Détection Python des annulations sans débit (en complément de l'IA)
            is_cancelled_from_keywords = any(kw in text_to_check for kw in CANCELLED_NO_CHARGE_KEYWORDS)
            is_cancelled = (verdict == "ANNULE") or (type_remboursement == "CANCELLED") or is_cancelled_from_keywords
            
            # Si annulation détectée, forcer le montant à 0 et le type à CANCELLED
            if is_cancelled:
                montant_reel = 0
                type_remboursement = "CANCELLED"
                verdict = "ANNULE"
            
            return {
                "verdict": verdict,
                "montant_reel": montant_reel,
                "type": type_remboursement,
                "order_id": order_id,
                "is_credit": is_credit,
                "is_partial": is_partial,
                "is_cancelled": is_cancelled,
                "confidence": confidence,
                "raison": result
            }
        else:
            return {
                "verdict": "NON",
                "montant_reel": 0,
                "type": "NONE",
                "order_id": None,
                "is_credit": False,
                "is_partial": False,
                "is_cancelled": False,
                "confidence": "LOW",
                "raison": f"Format invalide: {result}"
            }
    
    except Exception as e:
        DEBUG_LOGS.append(f"Erreur analyze_refund: {str(e)}")
        return {"verdict": "NON", "montant_reel": 0, "type": "NONE", "order_id": None, "is_credit": False, "is_partial": False, "is_cancelled": False, "confidence": "LOW", "raison": str(e)}

# ========================================
# PAGES LÉGALES
# ========================================

@app.route("/cgu")
def cgu():
    return STYLE + """
    <div style='max-width:900px; margin:0 auto; padding:20px;'>
        <div style='background:white; padding:50px; border-radius:24px; box-shadow:0 25px 50px -12px rgba(0,0,0,0.15);'>
            
            <h1 style='color:#1e293b; margin-bottom:30px; font-size:2rem;'>
                📜 Conditions Générales d'Utilisation
            </h1>
            <p style='color:#64748b; margin-bottom:30px;'>Dernière mise à jour : Janvier 2026</p>
            
            <div style='line-height:1.8; color:#334155;'>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 1 - Objet du Service</h2>
                <p>Justicio est une plateforme de <b>recouvrement amiable automatisé</b> qui aide les consommateurs à faire valoir leurs droits face aux entreprises en cas de litige commercial (colis non livré, produit défectueux, retard de transport, etc.).</p>
                <p>Le service agit en tant que <b>mandataire du client</b> pour l'envoi de mises en demeure et le suivi des réclamations. Justicio n'est pas un cabinet d'avocats et ne fournit pas de conseil juridique personnalisé.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 2 - Inscription et Accès</h2>
                <p>L'inscription au service est <b>gratuite</b> et s'effectue via l'authentification Google (OAuth 2.0). L'utilisateur autorise Justicio à analyser ses emails pour détecter les transactions potentiellement litigieuses.</p>
                <p>L'utilisateur doit être majeur et disposer de la capacité juridique pour contracter.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 3 - Tarification ("No Win, No Fee")</h2>
                <div style='background:#f0fdf4; padding:20px; border-radius:12px; border-left:4px solid #10b981; margin:20px 0;'>
                    <p style='margin:0;'><b>✅ Inscription :</b> Gratuite</p>
                    <p style='margin:10px 0;'><b>✅ Analyse des emails :</b> Gratuite</p>
                    <p style='margin:10px 0;'><b>✅ Envoi des mises en demeure :</b> Gratuit</p>
                    <p style='margin:0;'><b>💰 Commission de succès :</b> 30% TTC du montant effectivement récupéré</p>
                </div>
                <p><b>Important :</b> La commission n'est prélevée QUE si le client obtient un remboursement. En l'absence de remboursement, le client ne paie rien ("No win, no fee").</p>
                <p>Le prélèvement s'effectue automatiquement via la carte bancaire enregistrée, dans les 48h suivant la détection du remboursement sur le compte du client.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 4 - Obligation de Moyens</h2>
                <p>Justicio s'engage à mettre en œuvre tous les moyens raisonnables pour obtenir le remboursement des sommes dues au client. Cependant, <b>Justicio a une obligation de moyens et non de résultat</b>.</p>
                <p>Le succès d'une réclamation dépend de nombreux facteurs externes (réponse de l'entreprise, validité juridique du litige, preuves disponibles, etc.) sur lesquels Justicio n'a pas de contrôle total.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 5 - Responsabilité</h2>
                <p>Justicio ne peut être tenu responsable :</p>
                <ul style='margin-left:20px;'>
                    <li>Des décisions prises par les entreprises tierces</li>
                    <li>Des retards de remboursement imputables aux entreprises</li>
                    <li>Des erreurs de détection liées à des informations incomplètes dans les emails</li>
                    <li>Des interruptions de service dues à des maintenances ou problèmes techniques</li>
                </ul>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 6 - Résiliation</h2>
                <p>L'utilisateur peut résilier son compte à tout moment en envoyant un email à <a href='mailto:support@justicio.fr' style='color:#4f46e5;'>support@justicio.fr</a>.</p>
                <p>Les dossiers en cours restent actifs jusqu'à leur conclusion. Les commissions dues sur les remboursements déjà obtenus restent exigibles.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 7 - Droit Applicable</h2>
                <p>Les présentes CGU sont régies par le <b>droit français</b>. En cas de litige, les tribunaux de Paris seront seuls compétents.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>Article 8 - Contact</h2>
                <p>Pour toute question relative aux présentes CGU :</p>
                <p>📧 Email : <a href='mailto:support@justicio.fr' style='color:#4f46e5;'>support@justicio.fr</a></p>
                
            </div>
            
            <div style='margin-top:40px; text-align:center;'>
                <a href='/' class='btn-logout' style='padding:12px 30px;'>← Retour à l'accueil</a>
            </div>
            
        </div>
    </div>
    """ + FOOTER

@app.route("/confidentialite")
def confidentialite():
    return STYLE + """
    <div style='max-width:900px; margin:0 auto; padding:20px;'>
        <div style='background:white; padding:50px; border-radius:24px; box-shadow:0 25px 50px -12px rgba(0,0,0,0.15);'>
            
            <h1 style='color:#1e293b; margin-bottom:30px; font-size:2rem;'>
                🔒 Politique de Confidentialité
            </h1>
            <p style='color:#64748b; margin-bottom:30px;'>Dernière mise à jour : Janvier 2026 | Conforme RGPD</p>
            
            <div style='line-height:1.8; color:#334155;'>
                
                <!-- ENCART GOOGLE OBLIGATOIRE -->
                <div style='background:#eff6ff; padding:25px; border-radius:12px; border:2px solid #3b82f6; margin-bottom:30px;'>
                    <h3 style='color:#1d4ed8; margin-top:0;'>🔵 Conformité Google API</h3>
                    <p style='margin-bottom:0;'><b>L'utilisation des données reçues des API Google respecte les <a href='https://developers.google.com/terms/api-services-user-data-policy' target='_blank' style='color:#1d4ed8;'>Google API Services User Data Policy</a>, y compris les exigences d'utilisation limitée.</b></p>
                </div>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>1. Responsable du Traitement</h2>
                <p><b>Justicio SAS</b> (en cours d'immatriculation)<br>
                Directeur de la publication : Theodor Delgado<br>
                Délégué à la Protection des Données (DPO) : <a href='mailto:support@justicio.fr' style='color:#4f46e5;'>support@justicio.fr</a></p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>2. Données Collectées</h2>
                
                <h3 style='color:#64748b; font-size:1.1rem;'>2.1 Données d'identification</h3>
                <ul style='margin-left:20px;'>
                    <li>Nom et prénom (via Google)</li>
                    <li>Adresse email (via Google)</li>
                    <li>Photo de profil (via Google)</li>
                </ul>
                
                <h3 style='color:#64748b; font-size:1.1rem;'>2.2 Données de paiement</h3>
                <ul style='margin-left:20px;'>
                    <li>Identifiant client Stripe (pas de numéro de carte stocké)</li>
                    <li>Historique des transactions de commission</li>
                </ul>
                
                <h3 style='color:#64748b; font-size:1.1rem;'>2.3 Données d'emails (Accès Gmail)</h3>
                <div style='background:#fef3c7; padding:20px; border-radius:12px; border-left:4px solid #f59e0b; margin:20px 0;'>
                    <p style='margin:0;'><b>⚠️ Important - Traitement des emails :</b></p>
                    <p style='margin:10px 0 0 0;'>Nous <b>ne stockons pas</b> vos emails. Nous analysons temporairement les messages pour détecter les transactions éligibles à un recours. <b>Seules les données relatives aux litiges confirmés</b> (Montant, Date, Entreprise, Base légale) <b>sont conservées</b> pour le traitement du dossier.</p>
                </div>
                <p>L'analyse s'effectue en temps réel et les contenus des emails ne sont jamais enregistrés dans notre base de données. Seuls les métadonnées nécessaires au traitement juridique sont extraites.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>3. Finalités du Traitement</h2>
                <table style='width:100%; border-collapse:collapse; margin:20px 0;'>
                    <tr style='background:#f8fafc;'>
                        <th style='padding:12px; text-align:left; border:1px solid #e2e8f0;'>Finalité</th>
                        <th style='padding:12px; text-align:left; border:1px solid #e2e8f0;'>Base légale</th>
                        <th style='padding:12px; text-align:left; border:1px solid #e2e8f0;'>Durée</th>
                    </tr>
                    <tr>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Détection des litiges</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Consentement</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Temps réel (non stocké)</td>
                    </tr>
                    <tr style='background:#f8fafc;'>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Gestion des dossiers</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Exécution du contrat</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>3 ans après clôture</td>
                    </tr>
                    <tr>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Facturation</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Obligation légale</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>10 ans</td>
                    </tr>
                </table>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>4. Partage des Données</h2>
                <p>Vos données peuvent être partagées avec :</p>
                <ul style='margin-left:20px;'>
                    <li><b>Stripe</b> : Traitement des paiements (certifié PCI-DSS)</li>
                    <li><b>OpenAI</b> : Analyse IA des emails (données anonymisées)</li>
                    <li><b>Google</b> : Authentification et accès emails</li>
                </ul>
                <p>Nous ne vendons jamais vos données à des tiers à des fins commerciales.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>5. Vos Droits (RGPD)</h2>
                <p>Conformément au Règlement Général sur la Protection des Données, vous disposez des droits suivants :</p>
                <div style='display:grid; grid-template-columns:repeat(2, 1fr); gap:15px; margin:20px 0;'>
                    <div style='background:#f8fafc; padding:15px; border-radius:8px;'>✅ Droit d'accès</div>
                    <div style='background:#f8fafc; padding:15px; border-radius:8px;'>✅ Droit de rectification</div>
                    <div style='background:#f8fafc; padding:15px; border-radius:8px;'>✅ Droit à l'effacement</div>
                    <div style='background:#f8fafc; padding:15px; border-radius:8px;'>✅ Droit à la portabilité</div>
                    <div style='background:#f8fafc; padding:15px; border-radius:8px;'>✅ Droit d'opposition</div>
                    <div style='background:#f8fafc; padding:15px; border-radius:8px;'>✅ Droit à la limitation</div>
                </div>
                <p>Pour exercer ces droits, contactez notre DPO : <a href='mailto:support@justicio.fr' style='color:#4f46e5;'>support@justicio.fr</a></p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>6. Sécurité des Données</h2>
                <ul style='margin-left:20px;'>
                    <li>Chiffrement SSL/TLS pour toutes les communications</li>
                    <li>Authentification OAuth 2.0 (pas de mot de passe stocké)</li>
                    <li>Hébergement sécurisé sur Render (certifié SOC 2)</li>
                    <li>Accès restreint aux données (principe du moindre privilège)</li>
                </ul>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>7. Cookies</h2>
                <p>Nous utilisons uniquement des cookies techniques essentiels au fonctionnement du service (session utilisateur). Aucun cookie de tracking publicitaire n'est utilisé.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>8. Modifications</h2>
                <p>Cette politique peut être mise à jour. Les utilisateurs seront informés par email en cas de modification substantielle.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>9. Contact & Réclamations</h2>
                <p>📧 DPO : <a href='mailto:support@justicio.fr' style='color:#4f46e5;'>support@justicio.fr</a></p>
                <p>Vous pouvez également déposer une réclamation auprès de la <b>CNIL</b> : <a href='https://www.cnil.fr' target='_blank' style='color:#4f46e5;'>www.cnil.fr</a></p>
                
            </div>
            
            <div style='margin-top:40px; text-align:center;'>
                <a href='/' class='btn-logout' style='padding:12px 30px;'>← Retour à l'accueil</a>
            </div>
            
        </div>
    </div>
    """ + FOOTER

@app.route("/mentions-legales")
def mentions_legales():
    return STYLE + """
    <div style='max-width:900px; margin:0 auto; padding:20px;'>
        <div style='background:white; padding:50px; border-radius:24px; box-shadow:0 25px 50px -12px rgba(0,0,0,0.15);'>
            
            <h1 style='color:#1e293b; margin-bottom:30px; font-size:2rem;'>
                📋 Mentions Légales
            </h1>
            <p style='color:#64748b; margin-bottom:30px;'>Informations légales obligatoires</p>
            
            <div style='line-height:1.8; color:#334155;'>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>1. Éditeur du Site</h2>
                <div style='background:#f8fafc; padding:25px; border-radius:12px; margin:20px 0;'>
                    <p style='margin:5px 0;'><b>Raison sociale :</b> Justicio SAS (en cours d'immatriculation)</p>
                    <p style='margin:5px 0;'><b>Forme juridique :</b> Société par Actions Simplifiée</p>
                    <p style='margin:5px 0;'><b>Capital social :</b> En cours de constitution</p>
                    <p style='margin:5px 0;'><b>Siège social :</b> France</p>
                    <p style='margin:5px 0;'><b>RCS :</b> En cours d'immatriculation</p>
                    <p style='margin:5px 0;'><b>N° TVA :</b> En cours d'attribution</p>
                </div>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>2. Directeur de la Publication</h2>
                <div style='background:#f8fafc; padding:25px; border-radius:12px; margin:20px 0;'>
                    <p style='margin:5px 0;'><b>Nom :</b> Theodor Delgado</p>
                    <p style='margin:5px 0;'><b>Qualité :</b> Président</p>
                    <p style='margin:5px 0;'><b>Email :</b> <a href='mailto:support@justicio.fr' style='color:#4f46e5;'>support@justicio.fr</a></p>
                </div>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>3. Hébergement</h2>
                <div style='background:#f8fafc; padding:25px; border-radius:12px; margin:20px 0;'>
                    <p style='margin:5px 0;'><b>Hébergeur :</b> Render Inc.</p>
                    <p style='margin:5px 0;'><b>Adresse :</b> 525 Brannan Street, Suite 300, San Francisco, CA 94107, USA</p>
                    <p style='margin:5px 0;'><b>Site web :</b> <a href='https://render.com' target='_blank' style='color:#4f46e5;'>https://render.com</a></p>
                    <p style='margin:5px 0;'><b>Certifications :</b> SOC 2 Type II</p>
                </div>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>4. Propriété Intellectuelle</h2>
                <p>L'ensemble des contenus présents sur le site Justicio (textes, images, logos, code source) sont protégés par le droit d'auteur et sont la propriété exclusive de Justicio SAS, sauf mention contraire.</p>
                <p>Toute reproduction, représentation, modification ou exploitation non autorisée est interdite et constitue une contrefaçon sanctionnée par le Code de la propriété intellectuelle.</p>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>5. Services Tiers Utilisés</h2>
                <table style='width:100%; border-collapse:collapse; margin:20px 0;'>
                    <tr style='background:#f8fafc;'>
                        <th style='padding:12px; text-align:left; border:1px solid #e2e8f0;'>Service</th>
                        <th style='padding:12px; text-align:left; border:1px solid #e2e8f0;'>Usage</th>
                        <th style='padding:12px; text-align:left; border:1px solid #e2e8f0;'>Société</th>
                    </tr>
                    <tr>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Google OAuth</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Authentification</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Google LLC</td>
                    </tr>
                    <tr style='background:#f8fafc;'>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Gmail API</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Lecture emails</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Google LLC</td>
                    </tr>
                    <tr>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Stripe</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Paiements</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Stripe Inc.</td>
                    </tr>
                    <tr style='background:#f8fafc;'>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>OpenAI</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>Analyse IA</td>
                        <td style='padding:12px; border:1px solid #e2e8f0;'>OpenAI LP</td>
                    </tr>
                </table>
                
                <h2 style='color:#4f46e5; margin-top:30px; font-size:1.3rem;'>6. Contact</h2>
                <p>Pour toute question concernant le site :</p>
                <p>📧 Email : <a href='mailto:support@justicio.fr' style='color:#4f46e5;'>support@justicio.fr</a></p>
                
            </div>
            
            <div style='margin-top:40px; text-align:center;'>
                <a href='/' class='btn-logout' style='padding:12px 30px;'>← Retour à l'accueil</a>
            </div>
            
        </div>
    </div>
    """ + FOOTER

# ========================================
# DEBUG
# ========================================

@app.route("/reset-stripe")
def reset_stripe():
    """Réinitialise le customer Stripe de l'utilisateur connecté"""
    if "email" not in session:
        return redirect("/login")
    
    user = User.query.filter_by(email=session['email']).first()
    if user:
        old_id = user.stripe_customer_id
        user.stripe_customer_id = None
        db.session.commit()
        return STYLE + f"""
        <div style='text-align:center; padding:50px;'>
            <h1>✅ Stripe Réinitialisé</h1>
            <p>Ancien Customer ID : <code>{old_id}</code></p>
            <p>Un nouveau sera créé lors du prochain paiement.</p>
            <br>
            <a href='/scan-ecommerce' class='btn-success'>Relancer le Scan</a>
            <br><br>
            <a href='/' class='btn-logout'>Retour</a>
        </div>
        """ + FOOTER
    
    return "Utilisateur non trouvé"

@app.route("/debug-logs")
def show_debug_logs():
    """Affiche les logs de debug"""
    if not DEBUG_LOGS:
        return "<h1>Aucun log</h1><a href='/'>Retour</a>"
    
    return STYLE + "<h1>🕵️ Logs Debug</h1>" + "<br>".join(reversed(DEBUG_LOGS[-50:])) + "<br><br><a href='/' class='btn-logout'>Retour</a>"

# ========================================
# 🔐 ESPACE ADMINISTRATEUR
# ========================================

@app.route("/admin_panel", methods=["GET", "POST"])
def admin_panel():
    """
    🔐 DASHBOARD ADMIN - Vision globale de l'activité
    
    Fonctionnalités :
    - KPIs : Utilisateurs, Litiges, Commissions
    - Activité récente : 10 derniers litiges
    - Actions : Lancer le Cron manuellement
    - Logs de trafic en temps réel
    """
    
    # ════════════════════════════════════════════════════════════════
    # AUTHENTIFICATION ADMIN
    # ════════════════════════════════════════════════════════════════
    
    # Vérifier si déjà authentifié
    if session.get('admin_authenticated') != True:
        # Vérifier le mot de passe soumis
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == ADMIN_PASSWORD:
                session['admin_authenticated'] = True
            else:
                return STYLE + """
                <div style='max-width:400px; margin:100px auto; text-align:center;'>
                    <h1>🔐 Accès Refusé</h1>
                    <p style='color:#dc2626;'>Mot de passe incorrect.</p>
                    <a href='/admin_panel' class='btn-success'>Réessayer</a>
                </div>
                """ + FOOTER
        else:
            # Afficher le formulaire de connexion
            return STYLE + """
            <div style='max-width:400px; margin:100px auto;'>
                <div style='background:white; padding:30px; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.1);'>
                    <h1 style='text-align:center; margin-bottom:25px;'>🔐 Admin Panel</h1>
                    <form method='POST'>
                        <label style='display:block; margin-bottom:8px; font-weight:600;'>Mot de passe :</label>
                        <input type='password' name='password' required autofocus
                               style='width:100%; padding:12px; border:2px solid #e2e8f0; border-radius:8px; 
                                      margin-bottom:20px; box-sizing:border-box;'>
                        <button type='submit' class='btn-success' style='width:100%; padding:15px;'>
                            🔓 Accéder
                        </button>
                    </form>
                </div>
            </div>
            """ + FOOTER
    
    # ════════════════════════════════════════════════════════════════
    # CALCUL DES KPIs
    # ════════════════════════════════════════════════════════════════
    
    # Nombre total d'utilisateurs
    total_users = User.query.count()
    users_with_card = User.query.filter(User.stripe_customer_id != None).count()
    
    # Nombre total de litiges
    total_cases = Litigation.query.count()
    cases_by_status = {}
    for status in ["En attente de remboursement", "En cours juridique", "Remboursé", "En attente d'analyse", "Détecté"]:
        count = Litigation.query.filter(Litigation.status == status).count()
        if count > 0:
            cases_by_status[status] = count
    
    # Litiges remboursés (pour calculer les commissions)
    refunded_cases = Litigation.query.filter(
        Litigation.status.in_(["Remboursé", "Remboursé (Partiel)"])
    ).all()
    
    # Calcul des commissions (25% du montant)
    total_refunded = 0
    total_commission = 0
    for case in refunded_cases:
        try:
            amount = extract_numeric_amount(case.amount)
            total_refunded += amount
            total_commission += amount * 0.25
        except:
            pass
    
    # Litiges remboursés partiels
    partial_count = Litigation.query.filter(Litigation.status.like("Remboursé (Partiel:%")).count()
    voucher_count = Litigation.query.filter(Litigation.status.like("Résolu (Bon d'achat:%")).count()
    
    # ════════════════════════════════════════════════════════════════
    # 10 DERNIERS LITIGES
    # ════════════════════════════════════════════════════════════════
    
    recent_cases = Litigation.query.order_by(Litigation.created_at.desc()).limit(10).all()
    
    recent_html = ""
    for case in recent_cases:
        # Couleur selon statut
        if case.status == "Remboursé" or case.status.startswith("Remboursé"):
            color = "#10b981"
        elif case.status == "En cours juridique":
            color = "#3b82f6"
        elif "En attente" in case.status:
            color = "#f59e0b"
        else:
            color = "#94a3b8"
        
        date_str = case.created_at.strftime("%d/%m %H:%M") if case.created_at else "N/A"
        
        recent_html += f"""
        <tr style='border-bottom:1px solid #e2e8f0;'>
            <td style='padding:10px; font-size:0.85rem;'>{date_str}</td>
            <td style='padding:10px; font-size:0.85rem;'>{case.user_email[:20]}...</td>
            <td style='padding:10px; font-weight:600;'>{case.company[:15].upper()}</td>
            <td style='padding:10px; font-weight:bold; color:#059669;'>{case.amount}</td>
            <td style='padding:10px;'>
                <span style='background:{color}20; color:{color}; padding:3px 8px; border-radius:5px; font-size:0.75rem;'>
                    {case.status[:20]}
                </span>
            </td>
            <td style='padding:10px;'>
                <a href='/admin/case/{case.id}' style='background:#8b5cf6; color:white; padding:5px 10px; 
                   border-radius:5px; font-size:0.75rem; text-decoration:none;'>
                    ⚡ Gérer
                </a>
            </td>
        </tr>
        """
    
    # ════════════════════════════════════════════════════════════════
    # LOGS RÉCENTS (Trafic)
    # ════════════════════════════════════════════════════════════════
    
    traffic_logs = "<br>".join(DEBUG_LOGS[-20:][::-1]) if DEBUG_LOGS else "<p style='color:#94a3b8;'>Aucun log</p>"
    
    # ════════════════════════════════════════════════════════════════
    # RENDU HTML
    # ════════════════════════════════════════════════════════════════
    
    return STYLE + f"""
    <div style='max-width:900px; margin:0 auto; padding:20px;'>
        <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:25px;'>
            <h1 style='margin:0;'>🔐 Admin Panel</h1>
            <a href='/admin_logout' style='color:#dc2626; font-size:0.9rem;'>🚪 Déconnexion</a>
        </div>
        
        <!-- KPIs -->
        <div style='display:grid; grid-template-columns: repeat(4, 1fr); gap:15px; margin-bottom:25px;'>
            <div style='background:linear-gradient(135deg, #dbeafe 0%, #e0e7ff 100%); padding:20px; border-radius:15px; text-align:center;'>
                <div style='font-size:2rem; font-weight:bold; color:#1e40af;'>{total_users}</div>
                <div style='color:#3730a3; font-size:0.9rem;'>👥 Utilisateurs</div>
                <div style='color:#6366f1; font-size:0.75rem;'>{users_with_card} avec carte</div>
            </div>
            <div style='background:linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); padding:20px; border-radius:15px; text-align:center;'>
                <div style='font-size:2rem; font-weight:bold; color:#92400e;'>{total_cases}</div>
                <div style='color:#b45309; font-size:0.9rem;'>📂 Litiges</div>
                <div style='color:#d97706; font-size:0.75rem;'>{len(refunded_cases)} remboursés</div>
            </div>
            <div style='background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); padding:20px; border-radius:15px; text-align:center;'>
                <div style='font-size:2rem; font-weight:bold; color:#065f46;'>{total_refunded:.0f}€</div>
                <div style='color:#047857; font-size:0.9rem;'>💰 Récupéré</div>
                <div style='color:#10b981; font-size:0.75rem;'>pour les clients</div>
            </div>
            <div style='background:linear-gradient(135deg, #fce7f3 0%, #fbcfe8 100%); padding:20px; border-radius:15px; text-align:center;'>
                <div style='font-size:2rem; font-weight:bold; color:#9d174d;'>{total_commission:.0f}€</div>
                <div style='color:#be185d; font-size:0.9rem;'>💎 Commissions</div>
                <div style='color:#ec4899; font-size:0.75rem;'>25% encaissé</div>
            </div>
        </div>
        
        <!-- Statuts détaillés -->
        <div style='background:white; padding:20px; border-radius:15px; margin-bottom:25px; box-shadow:0 2px 10px rgba(0,0,0,0.05);'>
            <h3 style='margin-top:0;'>📊 Répartition par Statut</h3>
            <div style='display:flex; flex-wrap:wrap; gap:10px;'>
                {"".join([f"<span style='background:#f1f5f9; padding:5px 12px; border-radius:8px; font-size:0.85rem;'>{status}: <b>{count}</b></span>" for status, count in cases_by_status.items()])}
                <span style='background:#fef3c7; padding:5px 12px; border-radius:8px; font-size:0.85rem;'>Partiels: <b>{partial_count}</b></span>
                <span style='background:#dbeafe; padding:5px 12px; border-radius:8px; font-size:0.85rem;'>Bons d'achat: <b>{voucher_count}</b></span>
            </div>
        </div>
        
        <!-- Actions rapides -->
        <div style='background:white; padding:20px; border-radius:15px; margin-bottom:25px; box-shadow:0 2px 10px rgba(0,0,0,0.05);'>
            <h3 style='margin-top:0;'>⚡ Actions Rapides</h3>
            <div style='display:flex; gap:10px; flex-wrap:wrap;'>
                <a href='/cron/check-refunds?token={SCAN_TOKEN or ""}' target='_blank' class='btn-success' style='padding:10px 20px;'>
                    💰 Lancer le Cron (Encaisseur)
                </a>
                <a href='/debug-logs' target='_blank' class='btn-success' style='background:#6366f1; padding:10px 20px;'>
                    🕵️ Voir tous les logs
                </a>
                <a href='/verif-user' target='_blank' class='btn-success' style='background:#8b5cf6; padding:10px 20px;'>
                    👥 Vérifier utilisateurs
                </a>
                <a href='/test-detective' target='_blank' class='btn-success' style='background:#0ea5e9; padding:10px 20px;'>
                    🔍 Test Détective
                </a>
            </div>
        </div>
        
        <!-- 10 derniers litiges -->
        <div style='background:white; padding:20px; border-radius:15px; margin-bottom:25px; box-shadow:0 2px 10px rgba(0,0,0,0.05);'>
            <h3 style='margin-top:0;'>📋 10 Derniers Litiges</h3>
            <div style='overflow-x:auto;'>
                <table style='width:100%; border-collapse:collapse;'>
                    <thead>
                        <tr style='background:#f8fafc; border-bottom:2px solid #e2e8f0;'>
                            <th style='padding:10px; text-align:left; font-size:0.85rem;'>Date</th>
                            <th style='padding:10px; text-align:left; font-size:0.85rem;'>Client</th>
                            <th style='padding:10px; text-align:left; font-size:0.85rem;'>Entreprise</th>
                            <th style='padding:10px; text-align:left; font-size:0.85rem;'>Montant</th>
                            <th style='padding:10px; text-align:left; font-size:0.85rem;'>Statut</th>
                            <th style='padding:10px; text-align:left; font-size:0.85rem;'>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {recent_html or "<tr><td colspan='6' style='padding:20px; text-align:center; color:#94a3b8;'>Aucun litige</td></tr>"}
                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- Logs trafic -->
        <div style='background:#1e293b; padding:20px; border-radius:15px; margin-bottom:25px;'>
            <h3 style='margin-top:0; color:white;'>👁️ Trafic en Temps Réel (20 derniers)</h3>
            <div style='font-family:monospace; font-size:0.8rem; color:#94a3b8; max-height:300px; overflow-y:auto;'>
                {traffic_logs}
            </div>
        </div>
        
        <!-- Footer -->
        <div style='text-align:center; color:#94a3b8; font-size:0.85rem;'>
            <p>Justicio Admin Panel v1.0 | {datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
        </div>
    </div>
    """ + FOOTER

@app.route("/admin_logout")
def admin_logout():
    """Déconnexion de l'admin panel"""
    session.pop('admin_authenticated', None)
    return redirect("/admin_panel")

# ========================================
# 🔱 GOD MODE - Gestion Admin des Dossiers
# ========================================

@app.route("/admin/case/<int:case_id>", methods=["GET", "POST"])
def admin_case_edit(case_id):
    """
    🔱 GOD MODE - Gérer un dossier client depuis l'admin
    
    Permet à l'admin de :
    - Modifier l'email marchand
    - Modifier le montant
    - Envoyer la mise en demeure AU NOM DU CLIENT
    """
    
    # Vérifier l'authentification admin
    if session.get('admin_authenticated') != True:
        return redirect("/admin_panel")
    
    # Récupérer le dossier
    case = Litigation.query.get(case_id)
    if not case:
        return STYLE + """
        <div style='text-align:center; padding:50px;'>
            <h1>❌ Dossier introuvable</h1>
            <p>Ce dossier n'existe pas.</p>
            <a href='/admin_panel' class='btn-success'>Retour Admin</a>
        </div>
        """ + FOOTER
    
    # Récupérer l'utilisateur associé
    user = User.query.filter_by(email=case.user_email).first()
    
    # ════════════════════════════════════════════════════════════════
    # TRAITEMENT DU FORMULAIRE (POST)
    # ════════════════════════════════════════════════════════════════
    
    if request.method == "POST":
        action = request.form.get("action", "")
        
        # Mise à jour des champs
        new_merchant_email = request.form.get("merchant_email", "").strip()
        new_amount = request.form.get("amount", "").strip()
        new_status = request.form.get("status", "").strip()
        
        # Mettre à jour l'email marchand
        if new_merchant_email and '@' in new_merchant_email:
            old_email = case.merchant_email
            case.merchant_email = new_merchant_email
            case.merchant_email_source = "Admin (God Mode)"
            DEBUG_LOGS.append(f"🔱 ADMIN: Email modifié pour dossier #{case_id}: {old_email} → {new_merchant_email}")
        
        # Mettre à jour le montant
        if new_amount:
            try:
                amount_clean = new_amount.replace('€', '').replace(',', '.').strip()
                amount_float = float(amount_clean)
                case.amount = f"{amount_float:.2f}€"
                case.amount_float = amount_float
                DEBUG_LOGS.append(f"🔱 ADMIN: Montant modifié pour dossier #{case_id} → {amount_float:.2f}€")
            except:
                pass
        
        # Mettre à jour le statut
        if new_status:
            old_status = case.status
            case.status = new_status
            DEBUG_LOGS.append(f"🔱 ADMIN: Statut modifié pour dossier #{case_id}: {old_status} → {new_status}")
        
        db.session.commit()
        
        # ════════════════════════════════════════════════════════════════
        # ACTION : ENVOYER LA MISE EN DEMEURE (Au nom du client)
        # ════════════════════════════════════════════════════════════════
        
        notice_result = None
        if action == "send_notice":
            if not case.merchant_email:
                notice_result = {"success": False, "message": "Email marchand manquant"}
            elif not user:
                notice_result = {"success": False, "message": "Utilisateur introuvable"}
            elif not user.refresh_token:
                notice_result = {"success": False, "message": "Utilisateur sans refresh_token Gmail"}
            else:
                DEBUG_LOGS.append(f"🔱 GOD MODE: Envoi mise en demeure au nom de {user.email} pour dossier #{case_id}")
                
                # 🎯 LA MAGIE : On utilise les credentials du CLIENT
                notice_result = send_legal_notice(case, user)
                
                if notice_result["success"]:
                    # Notification Telegram
                    send_telegram_notif(f"🔱 GOD MODE 🔱\n\n📧 Mise en demeure envoyée!\n\n🏪 {case.company.upper()}\n💰 {case.amount}\n📧 → {case.merchant_email}\n👤 Au nom de: {user.email}\n\n⚡ Envoyé par Admin")
        
        # Message de résultat
        if notice_result:
            if notice_result["success"]:
                result_html = f"""
                <div style='background:#d1fae5; padding:20px; border-radius:10px; margin-bottom:20px; border-left:4px solid #10b981;'>
                    <h3 style='margin:0 0 10px 0; color:#065f46;'>✅ Mise en demeure envoyée !</h3>
                    <p style='margin:0; color:#047857;'>
                        Destinataire : <b>{case.merchant_email}</b><br>
                        Au nom de : <b>{user.name if user else 'N/A'}</b> ({case.user_email})<br>
                        Message ID : {notice_result.get('message_id', 'N/A')}
                    </p>
                </div>
                """
            else:
                result_html = f"""
                <div style='background:#fef2f2; padding:20px; border-radius:10px; margin-bottom:20px; border-left:4px solid #dc2626;'>
                    <h3 style='margin:0 0 10px 0; color:#991b1b;'>❌ Échec de l'envoi</h3>
                    <p style='margin:0; color:#7f1d1d;'>{notice_result['message']}</p>
                </div>
                """
        else:
            result_html = """
            <div style='background:#dbeafe; padding:15px; border-radius:10px; margin-bottom:20px; border-left:4px solid #3b82f6;'>
                <p style='margin:0; color:#1e40af;'>💾 Modifications enregistrées.</p>
            </div>
            """
        
        # Rediriger vers la même page avec le résultat
        return STYLE + f"""
        <div style='max-width:600px; margin:0 auto; padding:20px;'>
            <h1>🔱 God Mode - Dossier #{case_id}</h1>
            {result_html}
            <div style='display:flex; gap:10px;'>
                <a href='/admin/case/{case_id}' class='btn-success'>🔄 Recharger</a>
                <a href='/admin_panel' class='btn-logout'>← Retour Admin</a>
            </div>
        </div>
        """ + FOOTER
    
    # ════════════════════════════════════════════════════════════════
    # AFFICHAGE DU FORMULAIRE (GET)
    # ════════════════════════════════════════════════════════════════
    
    # Statuts possibles
    status_options = [
        "Détecté",
        "En attente d'analyse",
        "En attente de remboursement",
        "En cours juridique",
        "Envoyé",
        "Remboursé",
        "Annulé (sans débit)"
    ]
    
    status_select = ""
    for status in status_options:
        selected = "selected" if case.status == status else ""
        status_select += f"<option value='{status}' {selected}>{status}</option>"
    
    # Couleur du statut actuel
    status_color = "#94a3b8"
    if case.status == "Remboursé":
        status_color = "#10b981"
    elif case.status == "En cours juridique":
        status_color = "#3b82f6"
    elif "En attente" in case.status:
        status_color = "#f59e0b"
    
    # Info utilisateur
    user_info = ""
    if user:
        has_token = "✅ Oui" if user.refresh_token else "❌ Non"
        has_card = "✅ Oui" if user.stripe_customer_id else "❌ Non"
        user_info = f"""
        <div style='background:#f8fafc; padding:15px; border-radius:10px; margin-bottom:20px;'>
            <h4 style='margin:0 0 10px 0;'>👤 Client Associé</h4>
            <p style='margin:5px 0; font-size:0.9rem;'><b>Nom :</b> {user.name or 'N/A'}</p>
            <p style='margin:5px 0; font-size:0.9rem;'><b>Email :</b> {user.email}</p>
            <p style='margin:5px 0; font-size:0.9rem;'><b>Refresh Token :</b> {has_token}</p>
            <p style='margin:5px 0; font-size:0.9rem;'><b>Carte Stripe :</b> {has_card}</p>
        </div>
        """
    else:
        user_info = """
        <div style='background:#fef2f2; padding:15px; border-radius:10px; margin-bottom:20px; border-left:4px solid #dc2626;'>
            <p style='margin:0; color:#991b1b;'>⚠️ <b>Utilisateur introuvable !</b> Impossible d'envoyer la mise en demeure.</p>
        </div>
        """
    
    # Info mise en demeure
    legal_notice_info = ""
    if case.legal_notice_sent and case.legal_notice_date:
        date_str = case.legal_notice_date.strftime("%d/%m/%Y à %H:%M")
        legal_notice_info = f"""
        <div style='background:#dbeafe; padding:15px; border-radius:10px; margin-bottom:20px; border-left:4px solid #3b82f6;'>
            <p style='margin:0; color:#1e40af; font-size:0.9rem;'>
                <b>⚖️ Mise en demeure déjà envoyée</b><br>
                Le {date_str} à {case.merchant_email}<br>
                <span style='font-size:0.8rem;'>Message ID: {case.legal_notice_message_id or 'N/A'}</span>
            </p>
        </div>
        """
    
    # Bouton envoi disponible ?
    can_send = user and user.refresh_token and case.merchant_email
    send_button_style = "" if can_send else "opacity:0.5; cursor:not-allowed;"
    send_button_disabled = "" if can_send else "disabled"
    
    return STYLE + f"""
    <div style='max-width:600px; margin:0 auto; padding:20px;'>
        <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;'>
            <h1 style='margin:0;'>🔱 God Mode</h1>
            <a href='/admin_panel' style='color:#64748b;'>← Retour Admin</a>
        </div>
        
        <!-- Résumé du dossier -->
        <div style='background:white; padding:25px; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.1); margin-bottom:20px;'>
            <div style='display:flex; justify-content:space-between; align-items:start; margin-bottom:15px;'>
                <div>
                    <h2 style='margin:0 0 5px 0; color:#1e293b;'>🏪 {case.company.upper()}</h2>
                    <p style='margin:0; color:#64748b; font-size:0.85rem;'>Dossier #{case.id} | Créé le {case.created_at.strftime("%d/%m/%Y") if case.created_at else "N/A"}</p>
                </div>
                <span style='background:{status_color}20; color:{status_color}; padding:5px 12px; border-radius:8px; font-weight:600;'>
                    {case.status}
                </span>
            </div>
            
            <div style='background:#f8fafc; padding:15px; border-radius:10px; margin-bottom:15px;'>
                <p style='margin:5px 0; font-size:0.9rem;'><b>📋 Sujet :</b> {case.subject[:100]}...</p>
                <p style='margin:5px 0; font-size:0.9rem;'><b>⚖️ Base légale :</b> {case.law}</p>
                <p style='margin:5px 0; font-size:0.9rem;'><b>🔗 Source :</b> {case.merchant_email_source or 'N/A'}</p>
            </div>
            
            {user_info}
            {legal_notice_info}
        </div>
        
        <!-- Formulaire d'édition -->
        <div style='background:white; padding:25px; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.1);'>
            <h3 style='margin-top:0;'>✏️ Modifier le Dossier</h3>
            
            <form method='POST'>
                <!-- Email marchand -->
                <div style='margin-bottom:20px;'>
                    <label style='font-weight:bold; color:#1e293b; display:block; margin-bottom:8px;'>
                        📧 Email du marchand
                    </label>
                    <input type='email' name='merchant_email' 
                           value='{case.merchant_email or ""}'
                           placeholder='contact@marchand.com'
                           style='width:100%; padding:12px; border:2px solid #e2e8f0; border-radius:8px;
                                  font-size:1rem; box-sizing:border-box;'>
                </div>
                
                <!-- Montant -->
                <div style='margin-bottom:20px;'>
                    <label style='font-weight:bold; color:#1e293b; display:block; margin-bottom:8px;'>
                        💰 Montant
                    </label>
                    <input type='text' name='amount' 
                           value='{case.amount.replace("€", "") if case.amount else ""}'
                           placeholder='150.00'
                           style='width:100%; padding:12px; border:2px solid #e2e8f0; border-radius:8px;
                                  font-size:1rem; box-sizing:border-box;'>
                </div>
                
                <!-- Statut -->
                <div style='margin-bottom:20px;'>
                    <label style='font-weight:bold; color:#1e293b; display:block; margin-bottom:8px;'>
                        📊 Statut
                    </label>
                    <select name='status' style='width:100%; padding:12px; border:2px solid #e2e8f0; 
                                                  border-radius:8px; font-size:1rem; box-sizing:border-box;'>
                        {status_select}
                    </select>
                </div>
                
                <!-- Boutons -->
                <div style='display:flex; gap:10px; margin-bottom:20px;'>
                    <button type='submit' name='action' value='save' class='btn-success' 
                            style='flex:1; padding:15px; border:none; cursor:pointer;'>
                        💾 Enregistrer
                    </button>
                </div>
                
                <!-- Bouton GOD MODE : Envoyer la mise en demeure -->
                <div style='background:linear-gradient(135deg, #7c3aed 0%, #5b21b6 100%); 
                            padding:20px; border-radius:10px; text-align:center;'>
                    <p style='margin:0 0 15px 0; color:white; font-size:0.9rem;'>
                        🔱 <b>Action Admin Spéciale</b><br>
                        <span style='font-size:0.8rem; opacity:0.8;'>
                            Envoie un email juridique au nom du client, depuis SON compte Gmail.
                        </span>
                    </p>
                    <button type='submit' name='action' value='send_notice' {send_button_disabled}
                            style='background:#fbbf24; color:#1e293b; padding:15px 30px; border:none; 
                                   border-radius:8px; font-size:1rem; font-weight:bold; cursor:pointer;
                                   {send_button_style}'>
                        🚀 ENVOYER LA MISE EN DEMEURE (Au nom du client)
                    </button>
                    {"<p style='margin:10px 0 0 0; color:#fecaca; font-size:0.8rem;'>⚠️ Prérequis manquant (email/token)</p>" if not can_send else ""}
                </div>
            </form>
        </div>
    </div>
    """ + FOOTER

@app.route("/verif-user")
def verif_user():
    """Vérifie les utilisateurs et leurs cartes"""
    users = User.query.all()
    html = ["<h1>👥 Utilisateurs</h1>"]
    
    for u in users:
        carte_status = f"✅ CARTE OK ({u.stripe_customer_id})" if u.stripe_customer_id else "❌ PAS DE CARTE"
        html.append(f"<p><b>{u.name}</b> ({u.email}) - {carte_status}</p>")
    
    return STYLE + "".join(html) + "<br><a href='/' class='btn-logout'>Retour</a>"

@app.route("/test-detective")
def test_detective():
    """Page de test pour l'Agent Détective avec logs détaillés"""
    url = request.args.get("url", "")
    
    if not url:
        return STYLE + """
        <div style='max-width:500px; margin:0 auto; padding:30px;'>
            <h1>🕵️ Test Agent Détective V3</h1>
            <p style='color:#64748b; margin-bottom:20px;'>
                Teste le scraping d'email sur n'importe quel site e-commerce.
                Les logs détaillés s'afficheront après l'analyse.
            </p>
            <form method='GET' style='background:white; padding:25px; border-radius:15px;'>
                <label style='display:block; margin-bottom:10px; font-weight:600;'>URL du site à analyser :</label>
                <input type='url' name='url' required placeholder='https://www.exemple.com' 
                       style='width:100%; padding:12px; border:2px solid #e2e8f0; border-radius:8px; margin-bottom:15px;'>
                <button type='submit' class='btn-success' style='width:100%;'>🔍 Lancer l'analyse</button>
            </form>
            <div style='margin-top:20px; background:#f1f5f9; padding:15px; border-radius:10px;'>
                <p style='margin:0; font-size:0.85rem; color:#64748b;'>
                    <b>Sites de test suggérés :</b><br>
                    • archiduchesse.com (Shopify FR)<br>
                    • asphalte.com (Shopify FR)<br>
                    • lemahieu.com (E-commerce FR)
                </p>
            </div>
            <br>
            <a href='/' style='color:#64748b;'>← Retour</a>
        </div>
        """ + FOOTER
    
    # Marquer le début des logs pour ce test
    log_start_index = len(DEBUG_LOGS)
    
    # Lancer l'analyse
    result = find_merchant_email(url)
    
    # Récupérer les logs générés pendant l'analyse
    test_logs = DEBUG_LOGS[log_start_index:]
    
    # Afficher les résultats
    email_found = result.get("email")
    source = result.get("source", "N/A")
    all_emails = result.get("all_emails", [])
    
    status_html = ""
    if email_found:
        status_html = f"""
        <div style='background:#d1fae5; padding:20px; border-radius:10px; margin:20px 0;'>
            <h3 style='color:#065f46; margin:0;'>✅ Email trouvé !</h3>
            <p style='font-size:1.3rem; font-family:monospace; margin:10px 0; background:#ecfdf5; padding:10px; border-radius:5px;'>{email_found}</p>
            <p style='color:#047857; font-size:0.9rem;'>Source : {source}</p>
        </div>
        """
    else:
        status_html = f"""
        <div style='background:#fef3c7; padding:20px; border-radius:10px; margin:20px 0;'>
            <h3 style='color:#92400e; margin:0;'>❌ Aucun email trouvé</h3>
            <p style='color:#92400e; font-size:0.9rem;'>{source}</p>
        </div>
        """
    
    all_emails_html = ""
    if all_emails:
        all_emails_html = "<h4>📧 Tous les emails trouvés :</h4><ul>"
        for e in all_emails:
            all_emails_html += f"<li><code>{e}</code></li>"
        all_emails_html += "</ul>"
    
    # Formater les logs pour l'affichage
    logs_html = ""
    if test_logs:
        logs_html = "<div style='background:#1e293b; color:#e2e8f0; padding:15px; border-radius:10px; font-family:monospace; font-size:0.8rem; max-height:400px; overflow-y:auto; white-space:pre-wrap;'>"
        for log in test_logs:
            # Coloriser selon le type
            if "SUCCESS" in log or "✅" in log:
                logs_html += f"<div style='color:#4ade80;'>{log}</div>"
            elif "ERROR" in log or "❌" in log:
                logs_html += f"<div style='color:#f87171;'>{log}</div>"
            elif "WARNING" in log or "⚠️" in log:
                logs_html += f"<div style='color:#fbbf24;'>{log}</div>"
            elif "HTTP" in log or "🌐" in log:
                logs_html += f"<div style='color:#60a5fa;'>{log}</div>"
            else:
                logs_html += f"<div>{log}</div>"
        logs_html += "</div>"
    
    return STYLE + f"""
    <div style='max-width:800px; margin:0 auto; padding:30px;'>
        <h1>🕵️ Résultats Agent Détective V3</h1>
        <p style='color:#64748b;'>URL analysée : <code style='background:#f1f5f9; padding:3px 8px; border-radius:4px;'>{url}</code></p>
        
        {status_html}
        
        <div style='background:white; padding:20px; border-radius:10px; margin-bottom:20px;'>
            {all_emails_html if all_emails_html else "<p>Aucun email trouvé sur ce site.</p>"}
        </div>
        
        <h3>📋 Logs de Debug ({len(test_logs)} entrées)</h3>
        {logs_html if logs_html else "<p style='color:#94a3b8;'>Aucun log disponible</p>"}
        
        <div style='margin-top:20px;'>
            <a href='/test-detective' class='btn-success' style='margin-right:10px;'>🔄 Nouveau test</a>
            <a href='/debug-logs' class='btn-logout' style='margin-right:10px;'>📋 Tous les logs</a>
            <a href='/' class='btn-logout'>Retour</a>
        </div>
    </div>
    """ + FOOTER

# ========================================
# 🧪 ROUTE ADMIN - TESTS SCAN
# ========================================

@app.route("/admin/test-scan")
def admin_test_scan():
    """
    🧪 Tests automatisés des fonctions de filtrage TRANSPORT.
    Protégé par session admin_authenticated.
    
    ⚠️ PIVOT: Le scan auto ne détecte QUE le transport.
    """
    if not session.get('admin_authenticated'):
        return STYLE + """
        <div style='text-align:center; padding:50px;'>
            <h1 style='color:white;'>🔐 Accès Admin Requis</h1>
            <p style='color:rgba(255,255,255,0.6);'>Cette page est réservée aux administrateurs.</p>
            <a href='/' class='btn-success'>Retour</a>
        </div>
        """ + FOOTER
    
    # ════════════════════════════════════════════════════════════════
    # 📋 CAS DE TEST - TRANSPORT vs E-COMMERCE (pivot stratégique)
    # ════════════════════════════════════════════════════════════════
    
    test_cases = [
        # (subject, snippet, sender, should_be_transport, description)
        # ✈️ TRANSPORT - Doivent être détectés
        ("Vol AF1234 retardé de 4h", "Air France vous informe d'un retard", "noreply@airfrance.fr", True, "✈️ Vol retardé Air France - TRANSPORT"),
        ("Réclamation train SNCF - TGV annulé", "Votre TGV Paris-Lyon a été annulé", "sncf@sncf.fr", True, "🚄 Train annulé SNCF - TRANSPORT"),
        ("Bagage perdu vol EasyJet", "Votre bagage n'est pas arrivé", "support@easyjet.com", True, "🧳 Bagage perdu - TRANSPORT"),
        ("Retard Eurostar compensation", "Votre train a eu 2h de retard", "eurostar@eurostar.com", True, "🚄 Eurostar retard - TRANSPORT"),
        ("Uber course annulée", "Votre chauffeur a annulé", "noreply@uber.com", True, "🚗 VTC annulé - TRANSPORT"),
        
        # 📦 E-COMMERCE - Ne doivent PAS être détectés (pivot)
        ("Colis non reçu - Commande Amazon", "Votre colis n'a pas été livré", "shipping@amazon.fr", False, "📦 Colis Amazon - E-COMMERCE (ignoré)"),
        ("Problème livraison SHEIN", "Commande jamais reçue", "support@shein.com", False, "📦 SHEIN - E-COMMERCE (ignoré)"),
        ("Remboursement refusé Zalando", "Votre retour a été refusé", "service@zalando.fr", False, "📦 Zalando - E-COMMERCE (ignoré)"),
        ("Commande Asphalte défectueuse", "Produit non conforme", "contact@asphalte.com", False, "📦 Asphalte - E-COMMERCE (ignoré)"),
        
        # ❌ REJETS - Ne doivent PAS être détectés
        ("Votre facture Orange", "Prélèvement SEPA le 20/01", "facture@orange.fr", False, "📄 Facture normale - IGNORÉ"),
        ("Newsletter SNCF - Promos", "Voyagez moins cher cet été", "newsletter@sncf.fr", False, "📧 Newsletter - IGNORÉ"),
    ]
    
    # ════════════════════════════════════════════════════════════════
    # 🧪 EXÉCUTION DES TESTS
    # ════════════════════════════════════════════════════════════════
    
    results_html = ""
    passed = 0
    failed = 0
    
    for i, (subject, snippet, sender, should_be_transport, description) in enumerate(test_cases):
        # Test: is_transport_email
        actual_is_transport = is_transport_email(subject, snippet, sender)
        test_pass = (actual_is_transport == should_be_transport)
        
        if test_pass:
            passed += 1
            status_icon = "✅"
            status_color = "#10b981"
        else:
            failed += 1
            status_icon = "❌"
            status_color = "#ef4444"
        
        results_html += f"""
        <div style='background:rgba(255,255,255,0.05); border-radius:10px; padding:15px; margin-bottom:10px;
                    border-left:4px solid {status_color};'>
            <div style='display:flex; justify-content:space-between; align-items:center;'>
                <span style='color:white; font-weight:600;'>{status_icon} Test #{i+1}: {description}</span>
            </div>
            <div style='color:rgba(255,255,255,0.6); font-size:0.85rem; margin-top:8px;'>
                <div>📧 Subject: <code>{subject[:50]}...</code></div>
                <div>👤 Sender: <code>{sender}</code></div>
                <div style='margin-top:5px;'>
                    ✈️ is_transport_email: <span style='color:{"#10b981" if test_pass else "#ef4444"};'>
                        attendu={should_be_transport}, obtenu={actual_is_transport}
                    </span>
                </div>
            </div>
        </div>
        """
    
    # ════════════════════════════════════════════════════════════════
    # 📊 RÉSUMÉ
    # ════════════════════════════════════════════════════════════════
    
    total = passed + failed
    success_rate = (passed / total * 100) if total > 0 else 0
    summary_color = "#10b981" if success_rate >= 80 else "#f59e0b" if success_rate >= 50 else "#ef4444"
    
    return STYLE + f"""
    <div style='text-align:center; padding:30px;'>
        <div style='font-size:4rem; margin-bottom:15px;'>🧪</div>
        <h1 style='color:white;'>Tests Scan Transport - Résultats</h1>
        <p style='color:rgba(255,255,255,0.5);'>⚠️ PIVOT: Le scan auto ne détecte QUE le transport</p>
        <div style='display:flex; justify-content:center; gap:30px; margin:20px 0;'>
            <div style='background:rgba(16,185,129,0.2); padding:20px 30px; border-radius:10px;'>
                <div style='font-size:2rem; color:#10b981; font-weight:700;'>{passed}</div>
                <div style='color:rgba(255,255,255,0.6);'>Passés</div>
            </div>
            <div style='background:rgba(239,68,68,0.2); padding:20px 30px; border-radius:10px;'>
                <div style='font-size:2rem; color:#ef4444; font-weight:700;'>{failed}</div>
                <div style='color:rgba(255,255,255,0.6);'>Échoués</div>
            </div>
            <div style='background:rgba(255,255,255,0.1); padding:20px 30px; border-radius:10px;'>
                <div style='font-size:2rem; color:{summary_color}; font-weight:700;'>{success_rate:.0f}%</div>
                <div style='color:rgba(255,255,255,0.6);'>Taux de réussite</div>
            </div>
        </div>
    </div>
    
    <div style='max-width:800px; margin:0 auto; padding:0 20px;'>
        <h2 style='color:white; margin-bottom:20px;'>📋 Détail des tests</h2>
        {results_html}
    </div>
    
    <div style='text-align:center; margin:40px 0;'>
        <a href='/admin' class='btn-success'>← Retour Admin</a>
    </div>
    """ + FOOTER

# ========================================
# LANCEMENT
# ========================================

if __name__ == "__main__":
    app.run(debug=False)

# ════════════════════════════════════════════════════════════════════════════════
# 🧪 TEST CASES - Sujets et bodies à s'envoyer pour valider le scan
# ════════════════════════════════════════════════════════════════════════════════
#
# INSTRUCTIONS: Envoyez ces emails depuis Proton/Outlook/Gmail vers votre adresse Gmail
# connectée à Justicio. Le scan doit détecter les 5 travel + 5 ecommerce et ignorer les 3 rejets.
#
# ═══════════════════════════════════════════════════════════════════════════════
# ✈️ TRAVEL - DOIVENT ÊTRE DÉTECTÉS
# ═══════════════════════════════════════════════════════════════════════════════
#
# TEST TRAVEL 1 - Vol retardé Air France
# Subject: Vol AF1234 retardé de 4 heures - Information passager
# Body: Cher passager, nous vous informons que votre vol AF1234 Paris-Nice prévu le 15/01/2026 
#       a subi un retard de 4 heures. Nouveau départ à 18h30. Air France vous présente ses excuses.
#       Numéro de réservation: XYZABC. Montant du billet: 189€.
#
# TEST TRAVEL 2 - Train SNCF annulé
# Subject: Annulation de votre TGV INOUI - Réservation 7894561
# Body: Votre TGV INOUI n°6234 du 20/01/2026 Paris Gare de Lyon → Marseille a été annulé.
#       Vous pouvez prétendre à une compensation selon le règlement européen.
#       Prix du billet: 79€. Veuillez contacter le service client SNCF.
#
# TEST TRAVEL 3 - Bagage perdu EasyJet  
# Subject: Réclamation bagage - Vol EZY4567
# Body: Suite à votre vol EasyJet EZY4567 Londres-Paris du 10/01/2026, nous avons enregistré
#       votre déclaration de bagage perdu. Référence PIR: CDGEZ12345. 
#       Valeur déclarée des effets: 450€. Nous recherchons activement votre bagage.
#
# TEST TRAVEL 4 - Correspondance ratée Ryanair
# Subject: Missed connection compensation request - FR8901
# Body: Dear passenger, due to the delay of flight FR8901, you missed your connection FR8902.
#       According to EC261/2004, you may be entitled to compensation up to 250€.
#       Booking reference: ABC123. Please submit your claim within 30 days.
#
# TEST TRAVEL 5 - Retard Eurostar
# Subject: Votre Eurostar retardé - Indemnisation possible
# Body: Votre Eurostar 9014 Paris-Londres du 25/01/2026 est arrivé avec 2h30 de retard.
#       Conformément à nos conditions, vous pouvez demander une compensation de 50% du prix.
#       Billet: 145€. Référence: EURXYZ789.
#
# ═══════════════════════════════════════════════════════════════════════════════
# 📦 E-COMMERCE - ⚠️ NE SERONT PAS DÉTECTÉS PAR LE SCAN AUTO (PIVOT STRATÉGIQUE)
# Ces litiges doivent être déclarés manuellement via /declare
# ═══════════════════════════════════════════════════════════════════════════════
#
# TEST ECOMMERCE 1 - Colis non reçu Amazon → DÉCLARER MANUELLEMENT
# Subject: Problème avec votre commande Amazon #123-4567890
# Body: Bonjour, vous nous avez signalé ne pas avoir reçu votre colis. Commande #123-4567890
#       passée le 05/01/2026. Montant: 67,99€. Livraison prévue le 10/01.
#       Si vous n'avez toujours pas reçu votre colis, merci de nous recontacter.
#
# TEST ECOMMERCE 2 - Produit défectueux Cdiscount → DÉCLARER MANUELLEMENT
# Subject: Réclamation produit défectueux - Commande CD789456
# Body: Suite à votre réclamation concernant l'article défectueux reçu (TV Samsung 55"),
#       nous vous informons que votre demande de remboursement de 499€ est en cours d'examen.
#       Commande CD789456 du 01/01/2026.
#
# TEST ECOMMERCE 3 - Remboursement refusé Zalando → DÉCLARER MANUELLEMENT
# Subject: Votre demande de retour Zalando - Refusée
# Body: Cher client, votre demande de retour pour la commande ZAL2024-1234 (chaussures Nike, 129€)
#       a été refusée car l'article présente des traces d'usure. 
#       Si vous contestez cette décision, vous pouvez faire une réclamation.
#
# TEST ECOMMERCE 4 - Article manquant Fnac → DÉCLARER MANUELLEMENT
# Subject: Article manquant dans votre colis Fnac
# Body: Nous avons bien reçu votre signalement. Il manque 1 article dans votre commande FNAC-567890.
#       Article manquant: Casque Sony WH-1000XM5 (349€). 
#       Notre service client traite votre dossier sous 48h.
#
# TEST ECOMMERCE 5 - Livraison jamais reçue SHEIN → DÉCLARER MANUELLEMENT
# Subject: Where is my SHEIN order? Never received!
# Body: Order #SH987654321 placed on January 3rd, 2026. Total: 45.99€.
#       Tracking shows delivered but I never received my package!
#       I've been waiting for 3 weeks. Please refund or reship my order.
#
# ═══════════════════════════════════════════════════════════════════════════════
# ❌ REJETS - NE DOIVENT PAS ÊTRE DÉTECTÉS (factures normales, newsletters, success)
# ═══════════════════════════════════════════════════════════════════════════════
#
# TEST REJET 1 - Facture normale Orange
# Subject: Votre facture Orange du 15/01/2026
# Body: Bonjour, votre facture Orange de janvier est disponible. Montant: 45,99€.
#       Prélèvement SEPA le 20/01/2026. Merci pour votre confiance.
#
# TEST REJET 2 - Confirmation de commande (pas de problème)
# Subject: Confirmation de votre commande Amazon #111-2222333
# Body: Merci pour votre commande! Votre colis sera livré le 18/01/2026.
#       Total: 89,99€. Suivez votre livraison sur notre site.
#
# TEST REJET 3 - Remboursement déjà effectué (SUCCESS)
# Subject: Votre remboursement a été effectué - Commande FNAC-123
# Body: Bonne nouvelle! Nous avons procédé au remboursement de 149€ sur votre compte.
#       Le crédit apparaîtra sous 3-5 jours ouvrés. Merci de votre patience.
#
# ═══════════════════════════════════════════════════════════════════════════════
# 📋 RÉSUMÉ PIVOT STRATÉGIQUE
# ═══════════════════════════════════════════════════════════════════════════════
# 
# ✈️ SCAN AUTO (/scan-all) : Détecte UNIQUEMENT le transport (train, avion, VTC)
#    - Tests TRAVEL 1-5 → Doivent être détectés
#    - Tests ECOMMERCE 1-5 → Doivent être IGNORÉS par le scan
#    - Tests REJET 1-3 → Doivent être IGNORÉS
#
# 📦 DÉCLARATION MANUELLE (/declare) : Pour TOUS les litiges e-commerce
#    - Colis perdus, produits défectueux, remboursements refusés...
#    - L'utilisateur déclare manuellement et lance la procédure
#
# ═══════════════════════════════════════════════════════════════════════════════
# FIN DES TEST CASES
# ═══════════════════════════════════════════════════════════════════════════════
