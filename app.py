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
# BLACKLIST ANTI-SPAM (PARE-FEU)
# ========================================

BLACKLIST_SENDERS = [
    "temu", "shein", "aliexpress", "vinted", "ionos", "dribbble", 
    "linkedin", "pinterest", "tiktok", "newsletter", "no-reply@accounts.google.com",
    "notifications@", "noreply@", "donotreply@", "marketing@", "promo@"
]

BLACKLIST_SUBJECTS = [
    "crédit", "coupon", "offer", "offre", "promo", "solde", "félicitations",
    "gagné", "cadeau", "newsletter", "sélectionné", "mise à jour", "security",
    "connexion", "facture", "invoice", "bienvenue", "welcome", "confirmation d'inscription",
    "verify your", "vérifiez votre", "activate", "activer"
]

BLACKLIST_KEYWORDS = [
    "unsubscribe", "se désabonner", "cliquez ici", "click here", 
    "téléchargez", "download", "10% off", "réduction"
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
    """Vérifie si un email est un spam (PARE-FEU)"""
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    body_lower = body_snippet.lower()
    
    for black in BLACKLIST_SENDERS:
        if black in sender_lower:
            return True, f"Sender blacklist: {black}"
    
    for black in BLACKLIST_SUBJECTS:
        if black in subject_lower:
            return True, f"Subject blacklist: {black}"
    
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
    """Analyse IA pour détecter un litige"""
    if not OPENAI_API_KEY:
        return ["REJET", "Pas d'API", "Inconnu"]
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    try:
        prompt = f"""Tu es un Expert Comptable rigoureux spécialisé en litiges consommateurs.

INPUT :
- EXPÉDITEUR : {sender}
- SUJET : {subject}
- CONTENU : {text[:1800]}

RÈGLES STRICTES :

1. MONTANT (Le nerf de la guerre) :
   - Cherche un montant EXPLICITE (ex: "42.99€", "120 EUR", "50 dollars")
   - ⚠️ INTERDICTION D'ESTIMER. Si aucun chiffre visible : Écris "À déterminer"
   - EXCEPTION VOL ANNULÉ/RETARDÉ : Si compagnie aérienne (Air France, Ryanair, EasyJet, Lufthansa, KLM, British Airways...) 
     ET (annulation OR retard > 3h) → Mets automatiquement "250€"
   - EXCEPTION TRAIN RETARDÉ : Si SNCF/Eurostar/Ouigo ET retard > 60min → Mets "25% du billet" 
     (sauf si montant précis visible)

2. MARQUE :
   - Extrais depuis l'adresse email (@amazon.fr → AMAZON)
   - Si impossible, regarde le sujet/corps
   - Si "Colis" générique sans marque → Mets "AMAZON" par défaut

3. CRITÈRES DE REJET (réponds "REJET" si) :
   - Email de confirmation de paiement réussi ("Virement effectué", "Remboursement validé", "Payment received")
   - Email publicitaire (promo, soldes, newsletter, offre spéciale)
   - Email de sécurité (changement mot de passe, connexion suspecte)
   - Email de bienvenue/inscription
   - Absence totale de problème consommateur

4. LOI APPLICABLE :
   - Vol aérien : "le Règlement (CE) n° 261/2004"
   - Train : "le Règlement (UE) 2021/782"
   - E-commerce : "la Directive UE 2011/83"
   - Défaut produit : "l'Article L217-4 du Code de la consommation"
   - Voyage/Hôtel : "la Directive UE 2015/2302"

FORMAT DE RÉPONSE (3 éléments séparés par |) :
MONTANT | LOI | MARQUE

Exemples :
- "42.99€ | la Directive UE 2011/83 | AMAZON"
- "250€ | le Règlement (CE) n° 261/2004 | AIR FRANCE"
- "À déterminer | l'Article L217-4 | FNAC"
- "REJET | PAYÉ | REJET" (si déjà remboursé)
- "REJET | PUB | REJET" (si publicité)
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150
        )
        
        result = response.choices[0].message.content.strip()
        parts = [p.strip() for p in result.split("|")]
        
        if len(parts) < 3:
            return parts + ["Inconnu"] * (3 - len(parts))
        
        return parts[:3]
    
    except Exception as e:
        DEBUG_LOGS.append(f"Erreur IA: {str(e)}")
        return ["REJET", "Erreur IA", "Inconnu"]

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
# SCANNER INTELLIGENT
# ========================================

@app.route("/scan")
def scan():
    """Scanner de litiges avec pare-feu anti-spam"""
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
    """
    
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
        messages = results.get('messages', [])
    except Exception as e:
        return f"Erreur lecture Gmail : {e}"
    
    total_gain = 0
    new_cases_count = 0
    html_cards = ""
    debug_rejected = ["<h3>🗑️ Rapport de Filtrage</h3>"]
    
    existing_litigations = {}
    for lit in Litigation.query.filter_by(user_email=session['email']).all():
        if lit.message_id:
            existing_litigations[lit.message_id] = lit
    
    for msg in messages:
        try:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = msg_data['payload'].get('headers', [])
            
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "Sans sujet")
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), "Inconnu")
            snippet = msg_data.get('snippet', '')
            message_id = msg['id']
            
            spam_detected, spam_reason = is_spam(sender, subject, snippet)
            if spam_detected:
                debug_rejected.append(f"<p>🛑 <b>SPAM BLOQUÉ :</b> {subject}<br><small>{sender}</small><br><i>Raison: {spam_reason}</i></p>")
                continue
            
            if message_id in existing_litigations:
                dossier = existing_litigations[message_id]
                
                if dossier.status in ["Envoyé", "Payé"]:
                    continue
                
                if "€" in dossier.amount and "déterminer" not in dossier.amount.lower():
                    amount_display = f"<div class='amount-badge'>{dossier.amount}</div>"
                    try:
                        total_gain += int(re.search(r'\d+', dossier.amount).group())
                    except:
                        pass
                else:
                    val = dossier.amount.replace("€", "").replace("À déterminer", "").strip()
                    amount_display = f"<input type='number' value='{val}' placeholder='Prix €' class='amount-input' onchange='saveAmount({dossier.id}, this.value)'>"
                
                html_cards += f"""
                <div class='card'>
                    {amount_display}
                    <span class='radar-tag'>{dossier.company.upper()}</span>
                    <h3>{subject}</h3>
                    <p><i>Dossier existant (scan précédent)</i></p>
                    <small>⚖️ {dossier.law}</small>
                </div>
                """
                new_cases_count += 1
                continue
            
            body_text = extract_email_content(msg_data)
            analysis = analyze_litigation(body_text, subject, sender)
            extracted_amount, law_final, company_detected = analysis[0], analysis[1], analysis[2]
            
            if "REJET" in extracted_amount or "REJET" in company_detected:
                debug_rejected.append(f"<p>❌ <b>IA REJET :</b> {subject}<br><small>Raison: {extracted_amount} / {company_detected}</small></p>")
                continue
            
            company_normalized = company_detected.lower().strip()
            
            new_lit = Litigation(
                user_email=session['email'],
                company=company_normalized,
                amount=extracted_amount,
                law=law_final,
                subject=subject,
                message_id=message_id,
                status="Détecté"
            )
            
            try:
                db.session.add(new_lit)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                debug_rejected.append(f"<p>⚠️ Doublon ignoré : {subject}</p>")
                continue
            
            if "déterminer" in extracted_amount.lower():
                amount_display = f"<input type='number' placeholder='Prix €' class='amount-input' onchange='saveAmount({new_lit.id}, this.value)'>"
            else:
                amount_display = f"<div class='amount-badge'>{extracted_amount}</div>"
                try:
                    total_gain += int(re.search(r'\d+', extracted_amount).group())
                except:
                    pass
            
            html_cards += f"""
            <div class='card'>
                {amount_display}
                <span class='radar-tag'>{company_normalized.upper()}</span>
                <h3>{subject}</h3>
                <p><i>{snippet[:100]}...</i></p>
                <small>⚖️ {law_final}</small>
            </div>
            """
            new_cases_count += 1
            
        except Exception as e:
            debug_rejected.append(f"<p>❌ Erreur traitement : {str(e)}</p>")
            continue
    
    action_btn = ""
    if new_cases_count > 0 and STRIPE_SK:
        action_btn = f"""
        <div class='sticky-footer'>
            <div style='margin-right:20px; font-size:1.2em;'>
                <b>Total Validé : <span id='total-display'>{total_gain}</span>€</b>
            </div>
            <a href='/setup-payment' class='btn-success'>🚀 RÉCUPÉRER TOUT</a>
        </div>
        """
    
    script_js = """
    <script>
    function saveAmount(id, value) {
        if (!value || value <= 0) return;
        
        fetch('/update-amount', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id, amount: value})
        }).then(res => {
            if(res.ok) {
                const input = event.target;
                input.style.borderColor = '#10b981';
                input.style.color = '#10b981';
                
                let total = parseInt(document.getElementById('total-display').textContent) || 0;
                total += parseInt(value);
                document.getElementById('total-display').textContent = total;
            }
        });
    }
    </script>
    """
    
    debug_html = "<div class='debug-section'>" + "".join(debug_rejected) + "</div>"
    
    if new_cases_count > 0:
        return STYLE + "<h1>✅ Résultat du Scan</h1>" + html_cards + action_btn + debug_html + script_js + WA_BTN + FOOTER
    else:
        return STYLE + "<h1>Aucun litige détecté</h1>" + debug_html + "<br><a href='/' class='btn-success'>Retour</a>" + FOOTER

# ========================================
# MISE À JOUR MONTANT (AJAX)
# ========================================

@app.route("/update-amount", methods=["POST"])
def update_amount():
    """Met à jour le montant d'un litige"""
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
        if case.status == "Payé":
            color, status_text = "#10b981", "✅ VIREMENT REÇU"
        elif case.status in ["Envoyé", "En cours"]:
            color, status_text = "#f59e0b", "Traitement en cours..."
        else:
            color, status_text = "#3b82f6", "En attente action"
        
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
            payment_method_options={'card': {'setup_future_usage': 'off_session'}},
            success_url=url_for('success_page', _external=True).replace("http://", "https://"),
            cancel_url=url_for('index', _external=True).replace("http://", "https://")
        )
        
        return redirect(session_stripe.url, code=303)
    
    except Exception as e:
        return f"Erreur Stripe: {e}<br><a href='/'>Retour</a>"

@app.route("/success")
def success_page():
    """Page de succès après configuration paiement"""
    if "email" not in session:
        return redirect("/login")
    
    count = Litigation.query.filter_by(user_email=session['email'], status="Détecté").count()
    
    return STYLE + f"""
    <div style='text-align:center; padding:50px;'>
        <h1>✅ Succès !</h1>
        <div class='card' style='max-width:400px; margin:20px auto;'>
            <h3>🚀 {count} Procédures Prêtes</h3>
            <p>Votre carte est enregistrée. Les réclamations seront envoyées automatiquement.</p>
        </div>
        <a href='/dashboard' class='btn-success'>VOIR MES DOSSIERS</a>
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
                        lit.status = "Envoyé"
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

@app.route("/cron/check-refunds")
def check_refunds():
    """Vérifie les remboursements et prélève la commission"""
    logs = ["<h3>🔍 CHASSEUR ACTIF</h3>"]
    
    active_cases = Litigation.query.filter(
        Litigation.status.in_(["Envoyé", "En cours"])
    ).all()
    
    for case in active_cases:
        logs.append(f"<hr>📂 <b>{case.company.upper()}</b> - {case.amount}")
        
        user = User.query.filter_by(email=case.user_email).first()
        if not user or not user.refresh_token:
            logs.append("❌ Pas de refresh token")
            continue
        
        try:
            creds = get_refreshed_credentials(user.refresh_token)
            service = build('gmail', 'v1', credentials=creds)
            
            query = f'label:INBOX "{case.company}" (remboursement OR refund OR virement OR payment OR paiement)'
            results = service.users().messages().list(userId='me', q=query, maxResults=10).execute()
            messages = results.get('messages', [])
            
            for msg in messages:
                msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
                snippet = msg_data.get('snippet', '')
                
                if not OPENAI_API_KEY:
                    continue
                
                client = OpenAI(api_key=OPENAI_API_KEY)
                prompt = f"""Tu es contrôleur financier. 

Email de {case.company} : "{snippet}"

Question : Est-ce que ce mail confirme qu'un REMBOURSEMENT/VIREMENT a été EFFECTUÉ ?

Réponds uniquement par OUI ou NON."""

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=10
                )
                
                verdict = response.choices[0].message.content.strip().upper()
                logs.append(f"🤖 IA Verdict : {verdict}")
                
                if "OUI" in verdict and user.stripe_customer_id:
                    amount_match = re.search(r'\d+', case.amount)
                    if not amount_match:
                        logs.append("❌ Montant non trouvé")
                        continue
                    
                    amount = int(amount_match.group())
                    commission = int(amount * 0.30)
                    
                    try:
                        payment_methods = stripe.PaymentMethod.list(
                            customer=user.stripe_customer_id,
                            type="card"
                        )
                        
                        if not payment_methods.data:
                            logs.append("❌ Pas de carte enregistrée")
                            continue
                        
                        stripe.PaymentIntent.create(
                            amount=commission * 100,
                            currency='eur',
                            customer=user.stripe_customer_id,
                            payment_method=payment_methods.data[0].id,
                            payment_method_types=['card'],
                            off_session=True,
                            confirm=True,
                            description=f"Commission Justicio - {case.company}"
                        )
                        
                        case.status = "Payé"
                        db.session.commit()
                        
                        logs.append(f"✅ <b>JACKPOT : {commission}€ PRÉLEVÉS !</b>")
                        send_telegram_notif(f"💰 **{commission}€** prélevés sur {case.company.upper()} !")
                        
                        service.users().messages().modify(
                            userId='me',
                            id=msg['id'],
                            body={'removeLabelIds': ['INBOX']}
                        ).execute()
                        
                        break
                    
                    except stripe.error.CardError as e:
                        logs.append(f"❌ Erreur carte : {e.user_message}")
                    except Exception as e:
                        logs.append(f"❌ Erreur prélèvement : {str(e)}")
        
        except Exception as e:
            logs.append(f"❌ Erreur : {str(e)}")
    
    return STYLE + "<br>".join(logs) + "<br><br><a href='/' class='btn-success'>Retour</a>"

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
