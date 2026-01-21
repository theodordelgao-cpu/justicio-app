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
from sqlalchemy.exc import IntegrityError
from bs4 import BeautifulSoup

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
/* BOUTON SUPPORT FLOTTANT */
.support-float {
    position: fixed;
    bottom: 100px;
    right: 20px;
    background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
    color: #FFF;
    border-radius: 50px;
    padding: 12px 20px;
    font-size: 0.9rem;
    font-weight: 600;
    box-shadow: 0 4px 15px rgba(79, 70, 229, 0.4);
    z-index: 100;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: all 0.3s;
}
.support-float:hover {
    transform: translateY(-3px);
    box-shadow: 0 6px 20px rgba(79, 70, 229, 0.5);
}
.whatsapp-float {
    position: fixed;
    width: 60px;
    height: 60px;
    bottom: 160px;
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

# Email de support
SUPPORT_EMAIL = "support@justicio.fr"

FOOTER = """<footer>
    <a href='/cgu'>CGU</a> | 
    <a href='/confidentialite'>Confidentialité</a> | 
    <a href='/mentions-legales'>Mentions Légales</a>
    <p>© 2026 Justicio.fr</p>
</footer>
<!-- BOUTON SUPPORT FLOTTANT -->
<a href='mailto:""" + SUPPORT_EMAIL + """?subject=Demande%20d%27aide%20Justicio' class='support-float'>
    🆘 Aide
</a>
"""

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
            
            # 3. PRÉ-FILTRE MARKETING - Expéditeurs connus comme publicitaires
            sender_lower = sender.lower()
            MARKETING_SENDERS = [
                "temu", "shein", "wish", "aliexpress", "banggood", "gearbest",
                "groupon", "veepee", "showroomprive", "vente-privee",
                "newsletter", "promo@", "marketing@", "noreply@", "no-reply@",
                "info@", "news@", "deals@", "offers@", "sale@"
            ]
            is_marketing_sender = any(ms in sender_lower for ms in MARKETING_SENDERS)
            
            # Aussi vérifier le sujet pour les patterns marketing
            MARKETING_SUBJECTS = [
                "offre", "promo", "solde", "réduction", "-50%", "-70%", "gratuit",
                "gagnez", "félicitations", "cadeau", "offert", "exclusif",
                "dernière chance", "expire", "limité", "flash", "black friday",
                "le pdg", "ceo", "founder"
            ]
            is_marketing_subject = any(ms in subject_lower for ms in MARKETING_SUBJECTS)
            
            if is_marketing_sender and is_marketing_subject:
                print(f"   📢 SKIP (marketing évident): {sender[:30]} + sujet promo")
                emails_filtered_free += 1
                debug_rejected.append(f"<p>📢 <b>MARKETING (pré-filtre) :</b> {subject}<br><small>De: {sender[:40]}</small></p>")
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
            
            # ════════════════════════════════════════════════════════════════
            # GESTION DES REJETS IA (MARKETING, FACTURE, DÉJÀ PAYÉ, REFUS, etc.)
            # ════════════════════════════════════════════════════════════════
            if "REJET" in extracted_amount.upper() or "REJET" in company_detected.upper():
                reject_reason = law_final.upper() if law_final else "INCONNU"
                reject_detail = proof_sentence if proof_sentence else ""
                
                # Catégoriser le type de rejet pour les logs
                if "MARKETING" in reject_reason:
                    print(f"   📢 REJETÉ (MARKETING/PUB): {subject[:40]}")
                    debug_rejected.append(f"<p>📢 <b>MARKETING :</b> {subject}<br><small style='color:#f59e0b;'>Email publicitaire ignoré</small></p>")
                elif "FACTURE" in reject_reason:
                    print(f"   📄 REJETÉ (FACTURE): Simple notification de paiement")
                    debug_rejected.append(f"<p>📄 <b>FACTURE :</b> {subject}<br><small style='color:#6b7280;'>Notification de facturation (pas de litige)</small></p>")
                elif "HORS SUJET" in reject_reason:
                    print(f"   ⏭️ REJETÉ (HORS SUJET): Aucun déclencheur de litige")
                    debug_rejected.append(f"<p>⏭️ <b>HORS SUJET :</b> {subject}<br><small style='color:#6b7280;'>Aucun problème détecté</small></p>")
                elif "DÉJÀ PAYÉ" in reject_reason or "DEJA PAYE" in reject_reason:
                    print(f"   ✅ REJETÉ (DÉJÀ PAYÉ): Succès pour le CRON")
                    debug_rejected.append(f"<p>✅ <b>DÉJÀ REMBOURSÉ :</b> {subject}<br><small style='color:#10b981;'>{reject_detail[:80]}</small></p>")
                elif "REFUS" in reject_reason:
                    print(f"   🚫 REJETÉ (REFUS): Non gagnable")
                    debug_rejected.append(f"<p>🚫 <b>REFUS ENTREPRISE :</b> {subject}<br><small style='color:#dc2626;'>{reject_detail[:80]}</small></p>")
                else:
                    print(f"   ❌ REJETÉ PAR L'IA: {reject_reason}")
                    debug_rejected.append(f"<p>❌ <b>REJET ({reject_reason}) :</b> {subject}<br><small>{reject_detail[:80]}</small></p>")
                
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
            <a href='/scan' class='btn-success' style='background:#4f46e5; margin-right:10px;'>
                🔍 SCANNER
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
            <a href='/scan' class='btn-logout'>Nouveau Scan</a>
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
    """Configure le paiement Stripe - Gatekeeper pour les nouvelles déclarations"""
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
            <h1>❌ Erreur de paiement</h1>
            <p>Une erreur est survenue lors de la configuration du paiement.</p>
            <p style='color:#dc2626; font-size:0.9rem;'>{str(e)[:100]}</p>
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
                        <b>💳 Paiement sécurisé !</b><br>
                        Votre carte est enregistrée. Commission uniquement sur résultat.
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
    # FLUX NORMAL - Traitement des litiges SCAN
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
        "annulations": 0,  # Annulations sans débit
        "commissions_prelevees": 0,
        "total_commission": 0,
        "rejets_securite": 0
    }
    
    # ════════════════════════════════════════════════════════════════
    # FILTRE ÉLARGI : Surveiller TOUS les dossiers actifs
    # - "En attente de remboursement" : Dossiers SCAN classiques
    # - "En cours juridique" : Mise en demeure envoyée (Agent Avocat)
    # - "En cours" / "Envoyé" : Anciens statuts de compatibilité
    # - "En attente d'analyse" : Dossiers manuels en cours
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
            
            # QUERY COMPLÈTE - Remboursements, bons d'achat, ET annulations
            query = f'"{company_clean}" (remboursement OR refund OR virement OR "a été crédité" OR "has been refunded" OR "montant remboursé" OR "votre compte a été crédité" OR "remboursement effectué" OR "refund processed" OR "bon d\'achat" OR "avoir" OR "voucher" OR "carte cadeau" OR "gift card" OR "crédit boutique" OR "store credit" OR "code promo" OR "geste commercial" OR annulation OR annulée OR cancelled OR canceled OR voided OR "commande annulée" OR "order cancelled" OR "ne sera pas débité" OR "will not be charged") -subject:"MISE EN DEMEURE"'
            
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
                
                # ANALYSE IA SÉCURISÉE - Extrait maintenant numéro de commande et confiance
                verdict_result = analyze_refund_email(
                    company_clean, 
                    expected_amount, 
                    email_subject, 
                    snippet, 
                    email_from,
                    case_order_id=getattr(case, 'order_id', None)  # Numéro de commande du dossier si disponible
                )
                
                verdict = verdict_result.get("verdict", "NON")
                montant_reel = verdict_result.get("montant_reel", 0)
                type_remboursement = verdict_result.get("type", "UNKNOWN")
                order_id_found = verdict_result.get("order_id", None)
                is_credit = verdict_result.get("is_credit", True)
                is_partial = verdict_result.get("is_partial", False)
                is_cancelled = verdict_result.get("is_cancelled", False)  # Nouveau champ
                confidence = verdict_result.get("confidence", "LOW")
                raison = verdict_result.get("raison", "")
                
                logs.append(f"<p style='margin-left:30px;'>🤖 Verdict: <b>{verdict}</b> | Montant: <b>{montant_reel}€</b> | Type: <b>{type_remboursement}</b> | Partiel: <b>{'OUI' if is_partial else 'NON'}</b> | Confiance: <b>{confidence}</b></p>")
                if order_id_found:
                    logs.append(f"<p style='margin-left:40px; color:#6b7280; font-size:0.85rem;'>📦 N° Commande trouvé: {order_id_found}</p>")
                if raison:
                    logs.append(f"<p style='margin-left:40px; color:#6b7280; font-size:0.85rem;'>ℹ️ {raison[:100]}</p>")
                
                # ═══════════════════════════════════════════════════════════
                # 🚫 CAS SPÉCIAL : ANNULATION SANS DÉBIT
                # ═══════════════════════════════════════════════════════════
                
                if verdict == "ANNULE" or is_cancelled or type_remboursement == "CANCELLED":
                    logs.append(f"<p style='margin-left:30px; color:#8b5cf6;'>🚫 ANNULATION DÉTECTÉE : Commande annulée sans débit</p>")
                    logs.append(f"<p style='margin-left:40px; color:#8b5cf6; font-size:0.85rem;'>→ Aucune transaction financière - Pas de commission à prélever</p>")
                    
                    # Marquer l'email comme utilisé pour ne pas le retraiter
                    used_email_ids.add(msg_id)
                    stats["annulations"] += 1
                    
                    # Fermer le dossier sans commission
                    case.status = "Annulé (sans débit)"
                    case.updated_at = datetime.utcnow()
                    db.session.commit()
                    
                    logs.append(f"<p style='margin-left:30px; color:#8b5cf6; font-weight:bold;'>✅ Dossier fermé - Annulation confirmée</p>")
                    
                    # Notification Telegram
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
    logs.append("<h4>📊 Rapport de l'Encaisseur</h4>")
    logs.append(f"""
    <div style='background:#f8fafc; padding:15px; border-radius:10px; margin:10px 0;'>
        <p>📂 Dossiers scannés : <b>{stats['dossiers_scannes']}</b></p>
        <p>💵 Remboursements CASH : <b>{stats['remboursements_cash']}</b></p>
        <p>🎫 Remboursements VOUCHER : <b>{stats['remboursements_voucher']}</b> (sans commission)</p>
        <p>📉 Remboursements PARTIELS : <b>{stats['remboursements_partiels']}</b></p>
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
# LANCEMENT
# ========================================

if __name__ == "__main__":
    app.run(debug=False)
