import os
import base64
import requests
import stripe
import json
import re
import traceback
from flask import Flask, session, redirect, request, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from datetime import datetime
from email.mime.text import MIMEText
from sqlalchemy.exc import IntegrityError

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

with app.app_context():
    try:
        # Migration : Ajoute message_id si manquant
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
        
        db.create_all()
        print("✅ Base de données synchronisée.")
    except Exception as e:
        print(f"❌ Erreur DB : {e}")

# ========================================
# GESTIONNAIRE D'ERREURS
# ========================================

DEBUG_LOGS = []

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

⚠️ MISSION CRITIQUE : Tu cherches UNIQUEMENT les problèmes QUI N'ONT PAS ENCORE ÉTÉ RÉGLÉS.

INPUT :
- EXPÉDITEUR (FROM) : {sender}
- DESTINATAIRE (TO) : {to_field}
- SUJET : {subject}
- CONTENU : {text[:1800]}
{company_hint}
{amount_hint}

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
RÈGLES D'EXTRACTION (si PAS de résolution/refus détecté)
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
   - Extrais la PHRASE EXACTE du texte qui mentionne le montant
   - Cette phrase sera affichée au client comme justification
   - Exemples : "Je demande le remboursement de 50€", "Ma commande de 89.99€ n'est jamais arrivée"
   - Si pas de phrase avec montant, cite la phrase décrivant le problème

4. AUTRES CRITÈRES DE REJET :
   - "REJET | PUB | REJET | Email publicitaire" si publicité/newsletter
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

Exemples VALIDES (litiges à traiter) :
- "42.99€ | la Directive UE 2011/83 | AMAZON | Ma commande de 42.99€ n'est jamais arrivée"
- "50€ | la Directive UE 2011/83 | ZALANDO | Je demande le remboursement de 50€ pour cet article défectueux"
- "250€ | le Règlement (CE) n° 261/2004 | AIR FRANCE | Mon vol AF1234 a été annulé sans préavis"
- "À déterminer | le Règlement (UE) 2021/782 | SNCF | Mon train a eu 2h de retard"

Exemples REJET :
- "REJET | DÉJÀ PAYÉ | AMAZON | Votre remboursement de 42.99€ a été effectué"
- "REJET | REFUS | AIR FRANCE | Malheureusement, nous ne pouvons accéder à votre demande"
- "REJET | PUB | REJET | Email publicitaire"
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

def is_valid_euro_amount(amount_str):
    """
    FONCTION HELPER - BUG N°3 CORRIGÉ
    Vérifie si le montant est un montant valide en euros (pas un pourcentage, pas "À déterminer")
    Retourne True si on peut afficher un badge vert, False si on doit afficher un input
    """
    if not amount_str:
        return False
    
    amount_clean = amount_str.strip().lower()
    
    # Rejeter si contient un pourcentage
    if "%" in amount_clean:
        return False
    
    # Rejeter si "à déterminer" ou similaire
    if "déterminer" in amount_clean or "determiner" in amount_clean:
        return False
    
    # Rejeter si "inconnu" ou "rejet"
    if "inconnu" in amount_clean or "rejet" in amount_clean:
        return False
    
    # Doit contenir un symbole euro ET un chiffre
    has_euro = "€" in amount_str or "eur" in amount_clean
    has_digit = re.search(r'\d+', amount_str) is not None
    
    return has_euro and has_digit

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
    # Confirmations de paiement
    "virement effectué", "virement réalisé", "virement envoyé",
    "remboursement effectué", "remboursement validé", "remboursement confirmé",
    "crédité sur votre compte", "créditée sur votre compte",
    "avis de virement", "confirmation de virement",
    "confirmation de remboursement",
    # Formules positives entreprises
    "nous avons le plaisir", "nous avons bien procédé",
    "votre remboursement a été", "le remboursement a été effectué",
    "nous vous confirmons le remboursement",
    "montant remboursé", "somme remboursée",
    "votre compte a été crédité", "compte crédité",
    # Résolutions
    "problème résolu", "dossier clôturé", "réclamation traitée",
    "nous avons fait le nécessaire", "régularisation effectuée",
    "geste commercial accordé", "avoir crédité",
    # Bons d'achat (pas du vrai argent mais résolution)
    "bon d'achat", "code promo offert", "réduction accordée"
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

STYLE = """<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body {
    font-family: 'Outfit', sans-serif;
    background: #f8fafc;
    padding: 40px 20px;
    padding-bottom: 120px;
    display: flex;
    flex-direction: column;
    align-items: center;
    color: #1e293b;
    margin: 0;
}
.card {
    background: white;
    border-radius: 20px;
    padding: 30px;
    margin: 15px;
    width: 100%;
    max-width: 550px;
    box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);
    border-left: 8px solid #ef4444;
    position: relative;
}
.amount-badge {
    position: absolute;
    top: 30px;
    right: 30px;
    font-size: 1.5rem;
    font-weight: bold;
    color: #10b981;
}
.amount-input {
    position: absolute;
    top: 30px;
    right: 30px;
    padding: 10px;
    border: 2px solid #ef4444;
    border-radius: 10px;
    width: 100px;
    font-weight: bold;
    font-size: 1.1rem;
    color: #ef4444;
    z-index: 10;
}
.amount-hint {
    color: #f59e0b;
    font-size: 0.75rem;
    margin-top: 5px;
    position: absolute;
    top: 70px;
    right: 30px;
    width: 120px;
    text-align: right;
}
.radar-tag {
    background: #e0f2fe;
    color: #0284c7;
    padding: 4px 10px;
    border-radius: 8px;
    font-size: 0.8rem;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.proof-text {
    background: #fef3c7;
    padding: 12px 15px;
    border-radius: 8px;
    border-left: 4px solid #f59e0b;
    margin: 15px 0;
    font-size: 0.95rem;
    color: #92400e;
    line-height: 1.5;
}
.btn-success {
    background: #10b981;
    color: white;
    padding: 15px 40px;
    border-radius: 50px;
    text-decoration: none;
    font-weight: bold;
    font-size: 1.2rem;
    transition: 0.3s;
    box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4);
    border: none;
    cursor: pointer;
    display: inline-block;
}
.btn-success:hover {
    background: #059669;
    transform: translateY(-2px);
}
.btn-logout {
    background: #94a3b8;
    padding: 8px 16px;
    font-size: 0.8rem;
    border-radius: 8px;
    color: white;
    text-decoration: none;
    margin-top: 15px;
    display: inline-block;
}
.sticky-footer {
    position: fixed;
    bottom: 0;
    left: 0;
    width: 100%;
    background: white;
    padding: 20px;
    box-shadow: 0 -5px 20px rgba(0,0,0,0.1);
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 100;
}
.whatsapp-float {
    position: fixed;
    width: 60px;
    height: 60px;
    bottom: 100px;
    right: 20px;
    background-color: #25d366;
    color: #FFF;
    border-radius: 50px;
    text-align: center;
    font-size: 30px;
    box-shadow: 2px 2px 3px #999;
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: center;
    text-decoration: none;
}
footer {
    margin-top: 50px;
    font-size: 0.8rem;
    text-align: center;
    color: #94a3b8;
}
footer a {
    color: #4f46e5;
    text-decoration: none;
    margin: 0 10px;
}
.debug-section {
    margin-top: 50px;
    color: #64748b;
    background: #e2e8f0;
    padding: 20px;
    border-radius: 10px;
    max-width: 800px;
    font-size: 0.85rem;
}
</style>"""

FOOTER = """<footer>
    <a href='/cgu'>CGU</a> | 
    <a href='/confidentialite'>Confidentialité</a> | 
    <a href='/mentions-legales'>Mentions Légales</a>
    <p>© 2026 Justicio.fr</p>
</footer>"""

WA_BTN = f"""<a href="https://wa.me/{WHATSAPP_NUMBER}" class="whatsapp-float" target="_blank">💬</a>"""

# ========================================
# ROUTES PRINCIPALES
# ========================================

@app.route("/")
def index():
    """Page d'accueil"""
    if "credentials" not in session:
        return redirect("/login")
    
    active_count = Litigation.query.filter_by(user_email=session['email']).count()
    badge = f"<span style='background:red; color:white; padding:2px 8px; border-radius:50px; font-size:0.8rem; vertical-align:top;'>{active_count}</span>" if active_count > 0 else ""
    
    return STYLE + f"""
    <div style='text-align:center; margin-top:50px;'>
        <div style='font-size:3rem; margin-bottom:10px;'>⚖️</div>
        <h1 style='margin-bottom:5px;'>JUSTICIO</h1>
        <p style='color:#64748b; margin-bottom:40px;'>Bienvenue, <b>{session.get('name')}</b></p>
        
        <a href='/scan' class='btn-success' style='display:block; max-width:300px; margin:0 auto 20px auto; background:#4f46e5; box-shadow:0 10px 20px rgba(79, 70, 229, 0.3);'>
            🔍 LANCER UN SCAN
        </a>
        
        <a href='/dashboard' style='display:block; max-width:300px; margin:0 auto; padding:15px; background:white; color:#334155; text-decoration:none; border-radius:50px; font-weight:bold; box-shadow:0 4px 10px rgba(0,0,0,0.05);'>
            📂 SUIVRE MES LITIGES {badge}
        </a>
        
        <br><br>
        <a href='/logout' class='btn-logout'>Se déconnecter</a>
        <br><br>
        <a href='/force-reset' style='color:red; font-size:0.8rem;'>⚠️ Réinitialiser la base (Debug)</a>
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
def scan():
    """Scanner de litiges - Détection SANS enregistrement en base"""
    if "credentials" not in session:
        return redirect("/login")
    
    try:
        creds = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=creds)
    except Exception as e:
        return f"Erreur d'authentification Gmail : {e}<br><a href='/login'>Se reconnecter</a>"
    
    query = """
    label:INBOX 
    (litige OR remboursement OR refund OR annulation OR retard OR delay OR 
     colis OR commande OR livraison OR sncf OR airfrance OR easyjet OR 
     ryanair OR amazon OR zalando OR booking OR uber OR deliveroo OR bolt OR
     fnac OR darty OR zara OR asos OR lufthansa OR klm OR eurostar OR ouigo)
    -category:promotions -category:social
    -subject:"MISE EN DEMEURE"
    """
    
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
        messages = results.get('messages', [])
    except Exception as e:
        return f"Erreur lecture Gmail : {e}"
    
    total_gain = 0
    new_cases_count = 0
    html_cards = ""
    debug_rejected = ["<h3>📋 Rapport d'Analyse</h3>"]
    
    # Compteurs pour statistiques
    emails_scanned = 0
    emails_filtered_free = 0  # Spam évidents seulement
    emails_sent_to_ai = 0
    
    # ════════════════════════════════════════════════════════════════
    # LOGIQUE ANTI-DOUBLON : Company + Montant
    # On autorise plusieurs dossiers du même marchand si montants différents
    # ════════════════════════════════════════════════════════════════
    
    # Charger les message_id DÉJÀ EN BASE (pour ne pas re-scanner le même email)
    existing_message_ids = set()
    
    # Charger les combinaisons company + amount existantes (pour détecter les vrais doublons)
    # Format: {company: [liste de montants]}
    existing_company_amounts_dict = {}
    
    print("\n📂 CHARGEMENT DES DOSSIERS EXISTANTS:")
    for lit in Litigation.query.filter_by(user_email=session['email']).all():
        if lit.message_id:
            existing_message_ids.add(lit.message_id)
        # Stocker les montants par company
        company_key = lit.company.lower().strip() if lit.company else ""
        amount_value = extract_numeric_amount(lit.amount) if lit.amount else 0
        print(f"   → {company_key.upper()}: '{lit.amount}' → {amount_value}€")
        if company_key not in existing_company_amounts_dict:
            existing_company_amounts_dict[company_key] = []
        existing_company_amounts_dict[company_key].append(amount_value)
    
    DEBUG_LOGS.append(f"📊 Dossiers existants : {len(existing_message_ids)} emails")
    for comp, amounts in existing_company_amounts_dict.items():
        DEBUG_LOGS.append(f"   → {comp.upper()}: {amounts}")
    
    # Liste temporaire des litiges détectés (stockée en session)
    detected_litigations = []
    
    print("\n" + "="*60)
    print("🔍 DÉBUT DU SCAN - LOGS DE DÉBOGAGE")
    print("="*60)
    print(f"📧 Nombre total d'emails à analyser : {len(messages)}")
    print(f"📂 Dossiers existants (company → [montants]) : {existing_company_amounts_dict}")
    print("="*60 + "\n")
    
    for msg in messages:
        try:
            message_id = msg['id']
            emails_scanned += 1
            
            # ════════════════════════════════════════════════════════════════
            # SEUL CHECK PRÉALABLE : Ne pas re-scanner un email déjà traité
            # (basé sur message_id, PAS sur le marchand)
            # ════════════════════════════════════════════════════════════════
            if message_id in existing_message_ids:
                print(f"⏭️ SKIP (email déjà traité) : message_id={message_id[:20]}...")
                continue
            
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = msg_data['payload'].get('headers', [])
            
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "Sans sujet")
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), "Inconnu")
            to_field = next((h['value'] for h in headers if h['name'].lower() == 'to'), "")
            snippet = msg_data.get('snippet', '')
            
            print(f"\n{'─'*50}")
            print(f"📩 EMAIL TROUVÉ : {subject[:60]}")
            print(f"   De: {sender[:50]}")
            print(f"   To: {to_field[:50]}")
            print(f"   Snippet: {snippet[:80]}...")
            print(f"{'─'*50}")
            
            # ════════════════════════════════════════════════════════════════
            # SEULS FILTRES CONSERVÉS (absolument nécessaires)
            # ════════════════════════════════════════════════════════════════
            
            # 1. Ignorer nos propres mises en demeure
            if "MISE EN DEMEURE" in subject.upper():
                print(f"   ⏭️ SKIP (notre mise en demeure)")
                debug_rejected.append(f"<p>📤 <b>IGNORÉ (notre email) :</b> {subject}</p>")
                continue
            
            # 2. Ignorer les spams évidents (mots de passe, newsletters)
            subject_lower = subject.lower()
            if any(spam_word in subject_lower for spam_word in ["mot de passe", "password", "newsletter", "unsubscribe", "désabonner"]):
                print(f"   ⏭️ SKIP (spam évident)")
                emails_filtered_free += 1
                debug_rejected.append(f"<p>🛑 <b>SPAM évident :</b> {subject}</p>")
                continue
            
            # ════════════════════════════════════════════════════════════════
            # ANALYSE IA SYSTÉMATIQUE - Plus de filtre économique !
            # On envoie TOUT à l'IA pour extraire marchand + montant précis
            # ════════════════════════════════════════════════════════════════
            
            print(f"   🤖 ENVOI À L'IA (analyse systématique)...")
            emails_sent_to_ai += 1
            
            # Extraire le contenu complet
            body_text = extract_email_content(msg_data)
            
            # Détecter l'entreprise depuis le destinataire (TO) en priorité
            detected_company = extract_company_from_recipient(to_field, subject, sender)
            print(f"   🏢 Entreprise détectée (TO/sujet): {detected_company or 'Aucune'}")
            
            # Essayer d'extraire le montant directement du texte
            extracted_amount_from_text = extract_amount_from_text(body_text)
            print(f"   💶 Montant extrait (regex): {extracted_amount_from_text or 'Aucun'}")
            
            # APPEL IA - Retourne 4 valeurs : MONTANT | LOI | MARQUE | PREUVE
            analysis = analyze_litigation_v2(body_text, subject, sender, to_field, detected_company, extracted_amount_from_text)
            extracted_amount = analysis[0]
            law_final = analysis[1]
            company_detected = analysis[2]
            proof_sentence = analysis[3] if len(analysis) > 3 else snippet  # La preuve ou le snippet par défaut
            
            print(f"   🤖 EXTRACTION IA:")
            print(f"      → Marchand: {company_detected}")
            print(f"      → Montant: {extracted_amount}")
            print(f"      → Loi: {law_final}")
            print(f"      → Preuve: {proof_sentence[:50] if proof_sentence else 'Aucune'}...")
            
            # Vérifier si l'IA a rejeté ce mail (DÉJÀ PAYÉ, REFUS, PUB, etc.)
            if "REJET" in extracted_amount.upper() or "REJET" in company_detected.upper():
                print(f"   ❌ REJETÉ PAR L'IA: {law_final}")
                # Afficher la raison détaillée du rejet
                reject_reason = law_final  # La raison est dans le 2ème champ (DÉJÀ PAYÉ, REFUS, PUB...)
                reject_detail = proof_sentence if proof_sentence else ""
                debug_rejected.append(f"<p>❌ <b>IA REJET ({reject_reason}) :</b> {subject}<br><small>{reject_detail}</small></p>")
                continue
            
            # Utiliser l'entreprise détectée par TO si l'IA n'a pas trouvé mieux
            if detected_company and (company_detected.lower() == "inconnu" or company_detected.lower() == "amazon"):
                company_detected = detected_company
                print(f"   🔄 Entreprise corrigée: {company_detected}")
            
            company_normalized = company_detected.lower().strip()
            
            # Si le montant de l'IA est "À déterminer" mais qu'on l'a trouvé dans le texte
            if not is_valid_euro_amount(extracted_amount) and extracted_amount_from_text:
                extracted_amount = extracted_amount_from_text
                print(f"   🔄 Montant corrigé (depuis texte): {extracted_amount}")
            
            # ════════════════════════════════════════════════════════════════
            # VÉRIFICATION DOUBLON PAR COMPANY + MONTANT
            # Permet plusieurs dossiers du même marchand si montants différents
            # ════════════════════════════════════════════════════════════════
            amount_numeric = extract_numeric_amount(extracted_amount)
            
            print(f"\n   🔍 COMPARAISON DOUBLON:")
            print(f"      → Nouveau: {company_normalized.upper()} = {amount_numeric}€ (brut: '{extracted_amount}')")
            
            # RÈGLE IMPORTANTE : Si le montant est 0 ou invalide, ce n'est JAMAIS un doublon
            # On laisse passer pour que l'utilisateur puisse saisir le montant manuellement
            if amount_numeric == 0:
                print(f"      → Montant = 0, pas de vérification de doublon (montant à saisir manuellement)")
                is_duplicate = False
            else:
                # Vérifier si cette combinaison existe déjà EN BASE
                is_duplicate = False
                if company_normalized in existing_company_amounts_dict:
                    existing_amounts = existing_company_amounts_dict[company_normalized]
                    print(f"      → Existants en base pour {company_normalized.upper()}: {existing_amounts}€")
                    for existing_amt in existing_amounts:
                        # IGNORER les montants existants à 0 (non valides)
                        if existing_amt == 0:
                            print(f"         Skip montant existant = 0 (invalide)")
                            continue
                        diff = abs(existing_amt - amount_numeric)
                        print(f"         Comparaison: |{amount_numeric} - {existing_amt}| = {diff} (tolérance: 1€)")
                        # Tolérance de 1€ pour considérer comme doublon
                        if diff <= 1:
                            is_duplicate = True
                            print(f"         ⚠️ DOUBLON DÉTECTÉ ! ({amount_numeric}€ ≈ {existing_amt}€)")
                            DEBUG_LOGS.append(f"🔄 Doublon détecté: {company_normalized} {amount_numeric}€ ≈ {existing_amt}€ en base")
                            break
                        else:
                            print(f"         ✅ Montants différents ({diff}€ > 1€) → PAS un doublon")
                else:
                    print(f"      → Aucun dossier existant pour {company_normalized.upper()} → PAS un doublon")
            
            if is_duplicate:
                print(f"   ❌ REJETÉ (DOUBLON)")
                debug_rejected.append(f"<p>🔄 <b>DOUBLON IGNORÉ :</b> {company_normalized.upper()} - {extracted_amount}<br><small>Un dossier identique (même marchand + montant similaire) existe déjà.</small></p>")
                continue
            else:
                print(f"   ✅ PAS UN DOUBLON → Création autorisée")
            
            # Log si même marchand mais montant différent (nouveau dossier autorisé)
            if company_normalized in existing_company_amounts_dict:
                existing_amounts = existing_company_amounts_dict[company_normalized]
                print(f"   ✅ NOUVEAU DOSSIER AUTORISÉ pour {company_normalized.upper()} : {amount_numeric}€ (existants: {existing_amounts}€)")
                DEBUG_LOGS.append(f"✅ Nouveau dossier autorisé: {company_normalized.upper()} {amount_numeric}€ (existants: {existing_amounts}€)")
            
            # Vérifier aussi dans les litiges détectés DANS CE SCAN (éviter doublons dans la session)
            already_in_session = False
            if amount_numeric > 0:  # Ne vérifier que si on a un montant valide
                for existing_lit in detected_litigations:
                    existing_company = existing_lit['company'].lower().strip()
                    existing_amount = extract_numeric_amount(existing_lit['amount'])
                    # Ignorer les montants à 0
                    if existing_amount == 0:
                        continue
                    # Tolérance de 1€
                    if existing_company == company_normalized and abs(existing_amount - amount_numeric) <= 1:
                        already_in_session = True
                        print(f"   ⚠️ Doublon détecté dans ce scan: {company_normalized} {amount_numeric}€ ≈ {existing_amount}€")
                        break
            
            if already_in_session:
                print(f"   ❌ REJETÉ (doublon dans ce scan)")
                debug_rejected.append(f"<p>🔄 <b>DOUBLON SCAN :</b> {company_normalized.upper()} - {extracted_amount}<br><small>Déjà détecté dans ce scan.</small></p>")
                continue
            
            # Nettoyer la preuve si vide ou trop courte
            if not proof_sentence or len(proof_sentence) < 10:
                proof_sentence = snippet[:150] if snippet else subject
            
            # Ajouter au dict pour éviter les doublons dans ce scan
            if company_normalized not in existing_company_amounts_dict:
                existing_company_amounts_dict[company_normalized] = []
            existing_company_amounts_dict[company_normalized].append(amount_numeric)
            
            # STOCKER EN MÉMOIRE (pas en base !)
            litigation_data = {
                "message_id": message_id,
                "company": company_normalized,
                "amount": extracted_amount,
                "law": law_final,
                "subject": subject,
                "snippet": snippet,
                "proof": proof_sentence  # La preuve extraite par l'IA
            }
            detected_litigations.append(litigation_data)
            
            print(f"\n   ✅✅✅ LITIGE DÉTECTÉ ET STOCKÉ ✅✅✅")
            print(f"      → {company_normalized.upper()} - {extracted_amount}")
            print(f"      → Total litiges détectés jusqu'ici: {len(detected_litigations)}")
            
            # Construire l'affichage
            if is_valid_euro_amount(extracted_amount):
                amount_display = f"<div class='amount-badge'>{extracted_amount}</div>"
                total_gain += extract_numeric_amount(extracted_amount)
            else:
                hint_text = ""
                if "%" in extracted_amount:
                    hint_text = "<div class='amount-hint'>⚠️ Pourcentage détecté. Calculez le montant en euros.</div>"
                else:
                    hint_text = "<div class='amount-hint'>⚠️ Montant non trouvé. Indiquez le prix.</div>"
                
                amount_display = f"<input type='number' placeholder='Prix €' class='amount-input' data-index='{new_cases_count}' onchange='updateAmount(this)'>{hint_text}"
            
            # Afficher la PREUVE au lieu du snippet générique
            proof_display = proof_sentence[:200] + "..." if len(proof_sentence) > 200 else proof_sentence
            
            html_cards += f"""
            <div class='card'>
                {amount_display}
                <span class='radar-tag'>{company_normalized.upper()}</span>
                <h3>{subject}</h3>
                <p class='proof-text'><i>📝 "{proof_display}"</i></p>
                <small>⚖️ {law_final}</small>
            </div>
            """
            new_cases_count += 1
            
        except Exception as e:
            debug_rejected.append(f"<p>❌ Erreur traitement : {str(e)}</p>")
            continue
    
    # ═══════════════════════════════════════════════════════════════
    # FIN DU SCAN - RÉSUMÉ
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "="*60)
    print("📊 RÉSUMÉ DU SCAN")
    print("="*60)
    print(f"📧 Emails scannés: {emails_scanned}")
    print(f"🚫 Filtrés (gratuit): {emails_filtered_free}")
    print(f"🤖 Envoyés à l'IA: {emails_sent_to_ai}")
    print(f"✅ LITIGES DÉTECTÉS: {len(detected_litigations)}")
    for lit in detected_litigations:
        print(f"   → {lit['company'].upper()} - {lit['amount']}")
    print("="*60 + "\n")
    
    # Stocker les litiges détectés en session (pour les enregistrer après paiement)
    session['detected_litigations'] = detected_litigations
    session['total_gain'] = total_gain
    
    # Bouton d'action sticky
    action_btn = ""
    if new_cases_count > 0 and STRIPE_SK:
        action_btn = f"""
        <div class='sticky-footer'>
            <div style='margin-right:20px; font-size:1.2em;'>
                <b>Total Détecté : <span id='total-display'>{total_gain}</span>€</b>
            </div>
            <a href='/setup-payment' class='btn-success'>🚀 RÉCUPÉRER TOUT</a>
        </div>
        """
    
    # Script JS pour mise à jour des montants en session
    script_js = """
    <script>
    function updateAmount(input) {
        const index = input.getAttribute('data-index');
        const value = input.value;
        if (!value || value <= 0) return;
        
        fetch('/update-detected-amount', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({index: parseInt(index), amount: value})
        }).then(res => {
            if(res.ok) {
                input.style.borderColor = '#10b981';
                input.style.color = '#10b981';
                
                // Mettre à jour le total affiché
                res.json().then(data => {
                    document.getElementById('total-display').textContent = data.total;
                });
            }
        });
    }
    </script>
    """
    
    # Statistiques - Mode Analyse Systématique
    stats_html = f"""
    <div style='background:#dbeafe; padding:15px; border-radius:10px; margin-bottom:20px;'>
        <h4 style='margin:0 0 10px 0; color:#1e40af; text-align:center;'>🔬 Mode Analyse Systématique (Précision Max)</h4>
        
        <div style='display:flex; justify-content:space-around; margin-bottom:10px;'>
            <div style='text-align:center;'>
                <div style='font-size:1.5rem; font-weight:bold; color:#1e40af;'>{emails_scanned}</div>
                <div style='font-size:0.8rem; color:#3b82f6;'>📧 Emails scannés</div>
            </div>
            <div style='text-align:center;'>
                <div style='font-size:1.5rem; font-weight:bold; color:#7c3aed;'>{emails_sent_to_ai}</div>
                <div style='font-size:0.8rem; color:#8b5cf6;'>🤖 Analysés par IA</div>
            </div>
            <div style='text-align:center;'>
                <div style='font-size:1.5rem; font-weight:bold; color:#10b981;'>{new_cases_count}</div>
                <div style='font-size:0.8rem; color:#059669;'>✅ Litiges détectés</div>
            </div>
        </div>
        
        <div style='background:#bfdbfe; padding:8px; border-radius:5px; text-align:center;'>
            <span style='font-weight:bold; color:#1e40af;'>🎯 Chaque email est analysé par l'IA pour ne rater aucun litige</span>
        </div>
    </div>
    """
    
    debug_html = stats_html + "<div class='debug-section'>" + "".join(debug_rejected) + "</div>"
    
    # Ajouter info sur les dossiers existants pour debug
    existing_info = ""
    if existing_company_amounts_dict:
        existing_info = "<div style='background:#f1f5f9; padding:10px; border-radius:8px; margin-top:10px;'><b>📂 Dossiers existants :</b><ul style='margin:5px 0;'>"
        for comp, amounts in existing_company_amounts_dict.items():
            existing_info += f"<li>{comp.upper()}: {amounts}€</li>"
        existing_info += "</ul></div>"
    
    if new_cases_count > 0:
        return STYLE + f"<h1>✅ {new_cases_count} Litige(s) Détecté(s)</h1>" + html_cards + action_btn + debug_html + existing_info + script_js + WA_BTN + FOOTER
    else:
        # Vérifier s'il y a des dossiers en cours
        existing_count = Litigation.query.filter_by(user_email=session['email']).count()
        if existing_count > 0:
            return STYLE + f"""
            <div style='text-align:center; padding:50px;'>
                <h1>✅ Aucun nouveau litige</h1>
                <p>Vous avez déjà <b>{existing_count} dossier(s)</b> en cours de traitement.</p>
                {existing_info}
                <br>
                <a href='/dashboard' class='btn-success'>📂 VOIR MES DOSSIERS</a>
            </div>
            """ + debug_html + FOOTER
        else:
            return STYLE + "<h1>Aucun litige détecté</h1><p>Votre boîte mail ne contient pas de litiges identifiables.</p>" + debug_html + "<br><a href='/' class='btn-success'>Retour</a>" + FOOTER

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
        # GESTION DES STATUTS - Incluant Partiels et Bons d'achat
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
        
        elif case.status == "En attente de remboursement":
            color = "#f59e0b"  # Jaune/Orange
            status_text = "⏳ En attente de remboursement"
            status_icon = "⏳"
        
        elif case.status in ["Envoyé", "En cours"]:
            color = "#8b5cf6"  # Violet
            status_text = "📧 Mise en demeure envoyée"
            status_icon = "📧"
        
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
        
        html_rows += f"""
        <div style='background:white; padding:20px; margin-bottom:15px; border-radius:15px; 
                    border-left:5px solid {color}; box-shadow:0 2px 5px rgba(0,0,0,0.05); 
                    display:flex; justify-content:space-between; align-items:center;'>
            <div>
                <div style='font-weight:bold; font-size:1.1rem; color:#1e293b'>
                    {case.company.upper()}
                </div>
                <div style='font-size:0.9rem; color:#64748b'>
                    {case.subject[:50]}...
                </div>
                <div style='font-size:0.8rem; color:#94a3b8; margin-top:5px;'>
                    ⚖️ {case.law}
                </div>
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
            <a href='/scan' class='btn-success' style='background:#4f46e5; margin-right:10px;'>
                🔍 SCANNER
            </a>
            <a href='/' class='btn-logout'>Retour Accueil</a>
        </div>
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
            <a href='/scan' class='btn-success'>Relancer Scan</a>
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
    """Configure le paiement Stripe"""
    if "email" not in session:
        return redirect("/login")
    
    try:
        user = User.query.filter_by(email=session['email']).first()
        
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(
                email=session.get('email'),
                name=session.get('name')
            )
            user.stripe_customer_id = customer.id
            db.session.commit()
        
        session_stripe = stripe.checkout.Session.create(
            customer=user.stripe_customer_id,
            payment_method_types=['card'],
            mode='setup',
            success_url=url_for('success_page', _external=True).replace("http://", "https://"),
            cancel_url=url_for('index', _external=True).replace("http://", "https://")
        )
        
        return redirect(session_stripe.url, code=303)
    
    except Exception as e:
        return f"Erreur Stripe: {e}<br><a href='/'>Retour</a>"

@app.route("/success")
def success_page():
    """Page de succès - ENREGISTRE les litiges en base ET envoie les mises en demeure"""
    if "email" not in session:
        return redirect("/login")
    
    user = User.query.filter_by(email=session['email']).first()
    if not user or not user.refresh_token:
        return "Erreur : utilisateur non trouvé ou pas de refresh token"
    
    # Récupérer les litiges détectés depuis la session
    detected_litigations = session.get('detected_litigations', [])
    
    if not detected_litigations:
        return STYLE + """
        <div style='text-align:center; padding:50px;'>
            <h1>⚠️ Aucun litige à traiter</h1>
            <p>Veuillez d'abord scanner votre boîte mail.</p>
            <br>
            <a href='/scan' class='btn-success'>🔍 SCANNER</a>
        </div>
        """ + FOOTER
    
    sent_count = 0
    errors = []
    
    for lit_data in detected_litigations:
        # Vérifier que le montant est valide avant d'enregistrer
        if not is_valid_euro_amount(lit_data['amount']):
            errors.append(f"⚠️ {lit_data['company']}: montant invalide ({lit_data['amount']}) - non enregistré")
            continue
        
        # ════════════════════════════════════════════════════════════════
        # VÉRIFICATION DOUBLON PAR COMPANY + MONTANT
        # Permet plusieurs dossiers du même marchand si montants différents
        # ════════════════════════════════════════════════════════════════
        company_normalized = lit_data['company'].lower().strip()
        amount_numeric = extract_numeric_amount(lit_data['amount'])
        
        print(f"\n📝 Création dossier: {company_normalized.upper()} - {amount_numeric}€")
        
        # RÈGLE : Si montant = 0, on ne vérifie pas les doublons
        is_real_duplicate = False
        if amount_numeric > 0:
            # Vérifier si un dossier avec MÊME company ET MÊME montant existe déjà
            existing_duplicate = Litigation.query.filter_by(
                user_email=session['email'],
                company=company_normalized
            ).all()
            
            for existing in existing_duplicate:
                existing_amount = extract_numeric_amount(existing.amount)
                # Ignorer les montants à 0
                if existing_amount == 0:
                    continue
                diff = abs(existing_amount - amount_numeric)
                print(f"   Comparaison: |{amount_numeric} - {existing_amount}| = {diff}")
                # Tolérance de 1€ pour considérer comme doublon
                if diff <= 1:
                    is_real_duplicate = True
                    print(f"   ⚠️ DOUBLON ! Montants identiques")
                    break
                else:
                    print(f"   ✅ Montants différents → PAS un doublon")
        
        if is_real_duplicate:
            errors.append(f"🔄 {lit_data['company'].upper()} ({lit_data['amount']}): doublon ignoré (même marchand + même montant)")
            continue
        
        print(f"   ✅ Création autorisée")
        
        # ÉTAPE 1: Enregistrer en base de données
        new_lit = Litigation(
            user_email=session['email'],
            company=lit_data['company'],
            amount=lit_data['amount'],
            law=lit_data['law'],
            subject=lit_data['subject'],
            message_id=lit_data['message_id'],
            status="Détecté"
        )
        
        try:
            db.session.add(new_lit)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            errors.append(f"⚠️ {lit_data['company']}: doublon ignoré")
            continue
        
        # ÉTAPE 2: Envoyer la mise en demeure
        try:
            creds = get_refreshed_credentials(user.refresh_token)
            company_key = lit_data['company'].lower()
            legal_info = LEGAL_DIRECTORY.get(company_key, {
                "email": "theodordelgao@gmail.com",
                "loi": "le Droit Européen de la Consommation"
            })
            
            target_email = legal_info["email"]
            
            corps = f"""MISE EN DEMEURE FORMELLE

Objet : Réclamation concernant le dossier : {lit_data['subject']}

À l'attention du Service Juridique de {lit_data['company'].upper()},

Je soussigné(e), {user.name}, vous informe par la présente de mon intention de réclamer une indemnisation pour le litige suivant :

- Nature du litige : {lit_data['subject']}
- Fondement juridique : {lit_data['law']}
- Montant réclamé : {lit_data['amount']}

Conformément à la législation en vigueur, je vous mets en demeure de procéder au remboursement sous un délai de 8 jours ouvrés.

À défaut de réponse satisfaisante, je me réserve le droit de saisir les autorités compétentes.

Cordialement,
{user.name}
{user.email}
"""
            
            if send_litigation_email(creds, target_email, f"MISE EN DEMEURE - {lit_data['company'].upper()}", corps):
                new_lit.status = "En attente de remboursement"
                db.session.commit()
                sent_count += 1
                send_telegram_notif(f"📧 **JUSTICIO** : Mise en demeure {lit_data['amount']} envoyée à {lit_data['company'].upper()} !")
                DEBUG_LOGS.append(f"✅ Mail envoyé pour {lit_data['company']}")
            else:
                errors.append(f"❌ {lit_data['company']}: échec d'envoi email")
        
        except Exception as e:
            errors.append(f"❌ {lit_data['company']}: {str(e)}")
            DEBUG_LOGS.append(f"❌ Erreur envoi {lit_data['company']}: {str(e)}")
    
    # Vider la session des litiges détectés (ils sont maintenant en base)
    session.pop('detected_litigations', None)
    session.pop('total_gain', None)
    
    # Affichage du résultat
    error_html = ""
    if errors:
        error_html = "<div style='background:#fee2e2; padding:15px; border-radius:10px; margin-top:20px;'>" + "<br>".join(errors) + "</div>"
    
    return STYLE + f"""
    <div style='text-align:center; padding:50px;'>
        <h1>✅ Succès !</h1>
        <div class='card' style='max-width:400px; margin:20px auto;'>
            <h3>🚀 {sent_count} Mise(s) en demeure envoyée(s) !</h3>
            <p>Votre carte est enregistrée. Les réclamations ont été envoyées aux entreprises concernées.</p>
            <p style='color:#10b981; font-weight:bold;'>Vous recevrez une copie dans vos emails envoyés.</p>
            <p style='color:#64748b; font-size:0.9rem; margin-top:15px;'>
                💡 Notre système surveille automatiquement votre boîte mail et vous notifiera dès qu'un remboursement sera détecté.
            </p>
        </div>
        {error_html}
        <a href='/dashboard' class='btn-success'>📂 VOIR MES DOSSIERS</a>
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


@app.route("/cron/check-refunds")
def check_refunds():
    """
    💰 AGENT 2 : L'ENCAISSEUR
    Vérifie les remboursements et prélève la commission
    
    GÈRE 3 SCÉNARIOS :
    1. Remboursement PARTIEL → Accepter et facturer sur le montant réel
    2. Bon d'achat/Avoir → Fermer le dossier SANS facturer
    3. Remboursement IMPLICITE → Utiliser le montant du dossier
    """
    
    # Vérification du token de sécurité
    token = request.args.get("token")
    if SCAN_TOKEN and token != SCAN_TOKEN:
        return "⛔ Accès refusé - Token invalide", 403
    
    logs = ["<h3>💰 AGENT ENCAISSEUR ACTIF</h3>"]
    logs.append(f"<p>🕐 Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>")
    
    # Statistiques
    stats = {
        "dossiers_scannes": 0,
        "remboursements_cash": 0,
        "remboursements_voucher": 0,
        "remboursements_partiels": 0,
        "commissions_prelevees": 0,
        "total_commission": 0
    }
    
    # Chercher les litiges en attente de remboursement
    active_cases = Litigation.query.filter(
        Litigation.status == "En attente de remboursement"
    ).all()
    
    logs.append(f"<p>📂 {len(active_cases)} dossier(s) en attente de remboursement</p>")
    
    # ANTI-DOUBLON : Tracker les emails déjà utilisés
    used_email_ids = set()
    
    for case in active_cases:
        stats["dossiers_scannes"] += 1
        
        company_clean = case.company.strip().lower()
        expected_amount = extract_numeric_amount(case.amount)
        
        logs.append(f"<hr>📂 <b>{company_clean.upper()}</b> - {case.amount} (attendu: {expected_amount}€)")
        
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
            
            # QUERY AMÉLIORÉE - Inclut bons d'achat, avoirs, vouchers
            query = f'"{company_clean}" (remboursement OR refund OR virement OR "a été crédité" OR "has been refunded" OR "montant remboursé" OR "votre compte a été crédité" OR "remboursement effectué" OR "refund processed" OR "bon d\'achat" OR "avoir" OR "voucher" OR "carte cadeau" OR "gift card" OR "crédit boutique" OR "store credit" OR "code promo" OR "geste commercial") -subject:"MISE EN DEMEURE"'
            
            logs.append(f"<p style='margin-left:20px; color:#6b7280; font-size:0.85rem;'>🔍 Query: <code>{query[:100]}...</code></p>")
            
            results = service.users().messages().list(userId='me', q=query, maxResults=15).execute()
            messages = results.get('messages', [])
            
            logs.append(f"<p style='margin-left:20px;'>📧 <b>{len(messages)}</b> email(s) trouvé(s)</p>")
            
            if len(messages) == 0:
                logs.append("<p style='margin-left:20px; color:#f59e0b;'>⚠️ Aucun email détecté</p>")
                continue
            
            found_valid_refund = False
            
            for msg in messages:
                msg_id = msg['id']
                
                if msg_id in used_email_ids:
                    logs.append(f"<p style='margin-left:30px; color:#f59e0b;'>⏭️ Email déjà utilisé - SKIP</p>")
                    continue
                
                msg_data = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
                snippet = msg_data.get('snippet', '')
                
                headers = msg_data['payload'].get('headers', [])
                email_date = next((h['value'] for h in headers if h['name'].lower() == 'date'), "Date inconnue")
                email_subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "Sans sujet")
                email_from = next((h['value'] for h in headers if h['name'].lower() == 'from'), "")
                
                if "MISE EN DEMEURE" in email_subject.upper():
                    continue
                
                logs.append(f"<p style='margin-left:30px;'>📩 <b>{email_subject[:60]}...</b></p>")
                logs.append(f"<p style='margin-left:40px; color:#6b7280; font-size:0.85rem;'>De: {email_from[:40]} | {email_date[:20]}</p>")
                
                if not OPENAI_API_KEY:
                    logs.append("<p style='margin-left:30px; color:#dc2626;'>❌ Pas d'API OpenAI</p>")
                    continue
                
                # ANALYSE IA AMÉLIORÉE
                verdict_result = analyze_refund_email(company_clean, expected_amount, email_subject, snippet, email_from)
                
                verdict = verdict_result.get("verdict", "NON")
                montant_reel = verdict_result.get("montant_reel", 0)
                type_remboursement = verdict_result.get("type", "UNKNOWN")
                raison = verdict_result.get("raison", "")
                
                logs.append(f"<p style='margin-left:30px;'>🤖 Verdict: <b>{verdict}</b> | Montant: <b>{montant_reel}€</b> | Type: <b>{type_remboursement}</b></p>")
                if raison:
                    logs.append(f"<p style='margin-left:40px; color:#6b7280; font-size:0.85rem;'>ℹ️ {raison[:80]}</p>")
                
                if verdict == "OUI":
                    used_email_ids.add(msg_id)
                    
                    is_partial = montant_reel < expected_amount
                    if is_partial:
                        stats["remboursements_partiels"] += 1
                        logs.append(f"<p style='margin-left:30px; color:#f59e0b;'>⚠️ PARTIEL : {montant_reel}€ sur {expected_amount}€</p>")
                    
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
                                if is_partial:
                                    case.status = f"Remboursé (Partiel: {montant_reel}€/{expected_amount}€)"
                                else:
                                    case.status = "Remboursé"
                                case.updated_at = datetime.utcnow()
                                db.session.commit()
                                
                                stats["commissions_prelevees"] += 1
                                stats["total_commission"] += commission
                                
                                logs.append(f"<p style='margin-left:30px; color:#10b981; font-weight:bold;'>✅ JACKPOT ! {commission}€ PRÉLEVÉS !</p>")
                                
                                partial_info = f" (PARTIEL: {montant_reel}€/{expected_amount}€)" if is_partial else ""
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
    logs.append("<h4>📊 Rapport de l'Encaisseur</h4>")
    logs.append(f"""
    <div style='background:#f8fafc; padding:15px; border-radius:10px; margin:10px 0;'>
        <p>📂 Dossiers scannés : <b>{stats['dossiers_scannes']}</b></p>
        <p>💵 Remboursements CASH : <b>{stats['remboursements_cash']}</b></p>
        <p>🎫 Remboursements VOUCHER : <b>{stats['remboursements_voucher']}</b> (sans commission)</p>
        <p>📉 Remboursements PARTIELS : <b>{stats['remboursements_partiels']}</b></p>
        <p style='color:#10b981; font-weight:bold;'>💰 Commissions prélevées : <b>{stats['commissions_prelevees']}</b> = <b>{stats['total_commission']}€</b></p>
    </div>
    """)
    logs.append(f"<p>✅ Scan terminé à {datetime.utcnow().strftime('%H:%M:%S')} UTC</p>")
    
    return STYLE + "<br>".join(logs) + "<br><br><a href='/' class='btn-success'>Retour</a>"


def analyze_refund_email(company, expected_amount, subject, snippet, email_from):
    """
    💰 ANALYSEUR DE REMBOURSEMENT - Version commerciale
    
    Retourne : {verdict, montant_reel, type, raison}
    
    GÈRE :
    1. Remboursement PARTIEL → Accepte avec montant réel
    2. Bon d'achat → TYPE = VOUCHER
    3. Remboursement IMPLICITE → Utilise expected_amount
    """
    
    if not OPENAI_API_KEY:
        return {"verdict": "NON", "montant_reel": 0, "type": "NONE", "raison": "Pas d'API"}
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""Tu es un AUDITEUR FINANCIER COMMERCIAL. Analyse si cet email confirme un remboursement.

DOSSIER : {company.upper()} - Montant initial : {expected_amount}€

EMAIL :
- De : {email_from}
- Sujet : "{subject}"
- Contenu : "{snippet}"

RÈGLES :

1. CORRESPONDANCE ENTITÉ : L'email concerne-t-il {company.upper()} ?

2. TYPE DE REMBOURSEMENT :
   TYPE = "CASH" si : virement, CB remboursée, crédité sur compte bancaire
   TYPE = "VOUCHER" si : bon d'achat, avoir, voucher, carte cadeau, crédit boutique, code promo

3. MONTANT RÉEL :
   - Si montant EXPLICITE (ex: "20€") → Utilise ce montant
   - Si "remboursement total/intégral" confirmé SANS montant → Utilise {expected_amount}
   - Si remboursement partiel → Utilise le montant partiel
   
   ⚠️ ACCEPTE LES PARTIELS ! 20€ sur 100€ = VALIDE

4. REJET si : autre entreprise, promesse future, refus, aucun remboursement

FORMAT : VERDICT | MONTANT | TYPE

Exemples :
- "OUI | 100 | CASH" (virement 100€)
- "OUI | 20 | CASH" (partiel 20€)
- "OUI | {expected_amount} | CASH" (total implicite)
- "OUI | 50 | VOUCHER" (bon d'achat)
- "NON | 0 | NONE" (pas valide)

Ta réponse (une seule ligne) :"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=50
        )
        
        result = response.choices[0].message.content.strip()
        parts = [p.strip() for p in result.split("|")]
        
        if len(parts) >= 3:
            verdict = "OUI" if parts[0].upper().startswith("OUI") else "NON"
            
            try:
                montant_str = parts[1].replace("€", "").replace(",", ".").strip()
                montant_reel = float(montant_str)
            except:
                montant_reel = expected_amount if verdict == "OUI" else 0
            
            type_raw = parts[2].upper().strip()
            if "VOUCHER" in type_raw or "BON" in type_raw or "AVOIR" in type_raw:
                type_remboursement = "VOUCHER"
            elif "CASH" in type_raw or "VIREMENT" in type_raw:
                type_remboursement = "CASH"
            else:
                type_remboursement = "CASH" if verdict == "OUI" else "NONE"
            
            return {
                "verdict": verdict,
                "montant_reel": montant_reel,
                "type": type_remboursement,
                "raison": result
            }
        else:
            return {"verdict": "NON", "montant_reel": 0, "type": "NONE", "raison": f"Format invalide: {result}"}
    
    except Exception as e:
        DEBUG_LOGS.append(f"Erreur analyze_refund: {str(e)}")
        return {"verdict": "NON", "montant_reel": 0, "type": "NONE", "raison": str(e)}

# ========================================
# PAGES LÉGALES
# ========================================

@app.route("/cgu")
def cgu():
    return STYLE + """
    <div class='legal-content' style='max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px; margin:0 auto;'>
        <h1>Conditions Générales d'Utilisation</h1>
        <p><b>1. Objet :</b> Justicio SAS automatise vos réclamations juridiques auprès des entreprises.</p>
        <p><b>2. Honoraires :</b> Commission de 30% TTC prélevée uniquement sur les sommes effectivement récupérées.</p>
        <p><b>3. Protection :</b> Aucune avance de frais. Vous ne payez que si nous gagnons.</p>
        <br>
        <a href='/' class='btn-logout'>Retour</a>
    </div>
    """ + FOOTER

@app.route("/confidentialite")
def confidentialite():
    return STYLE + """
    <div class='legal-content' style='max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px; margin:0 auto;'>
        <h1>Politique de Confidentialité</h1>
        <p>Vos emails sont analysés par notre IA sécurisée sans stockage permanent.</p>
        <p>Seules les métadonnées des litiges (montant, entreprise, loi) sont conservées.</p>
        <p>Conformité RGPD totale.</p>
        <br>
        <a href='/' class='btn-logout'>Retour</a>
    </div>
    """ + FOOTER

@app.route("/mentions-legales")
def mentions_legales():
    return STYLE + """
    <div class='legal-content' style='max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px; margin:0 auto;'>
        <h1>Mentions Légales</h1>
        <p><b>Éditeur :</b> Justicio SAS, France</p>
        <p><b>Hébergement :</b> Render Inc.</p>
        <p><b>Contact :</b> theodordelgao@gmail.com</p>
        <br>
        <a href='/' class='btn-logout'>Retour</a>
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
            <a href='/scan' class='btn-success'>Relancer le Scan</a>
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

@app.route("/verif-user")
def verif_user():
    """Vérifie les utilisateurs et leurs cartes"""
    users = User.query.all()
    html = ["<h1>👥 Utilisateurs</h1>"]
    
    for u in users:
        carte_status = f"✅ CARTE OK ({u.stripe_customer_id})" if u.stripe_customer_id else "❌ PAS DE CARTE"
        html.append(f"<p><b>{u.name}</b> ({u.email}) - {carte_status}</p>")
    
    return STYLE + "".join(html) + "<br><a href='/' class='btn-logout'>Retour</a>"

# ========================================
# LANCEMENT
# ========================================

if __name__ == "__main__":
    app.run(debug=False)
