import os
import re
import json
import base64
import requests
import stripe
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Flask, session, redirect, request, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.flask_client import OAuth
from openai import OpenAI

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "justicio-secret-2024")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///justicio.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PREFERRED_URL_SCHEME'] = 'https'

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

db = SQLAlchemy(app)

# ══════════════════════════════════════════════════════════════════════════════
# OAUTH GOOGLE
# ══════════════════════════════════════════════════════════════════════════════

oauth = OAuth(app)
SCOPES = ['openid', 'email', 'profile', 'https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.send']

google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': ' '.join(SCOPES)}
)

# ══════════════════════════════════════════════════════════════════════════════
# MODÈLES DB
# ══════════════════════════════════════════════════════════════════════════════

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255))
    access_token = db.Column(db.Text)
    refresh_token = db.Column(db.Text)
    token_expires_at = db.Column(db.DateTime)
    stripe_customer_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Litigation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(255), nullable=False)
    company = db.Column(db.String(255))
    amount = db.Column(db.String(50))
    amount_float = db.Column(db.Float, default=0)
    law = db.Column(db.Text)
    proof = db.Column(db.Text)
    message_id = db.Column(db.String(255))
    merchant_email = db.Column(db.String(255))
    category = db.Column(db.String(50), default='ecommerce')
    status = db.Column(db.String(50), default='detected')
    legal_notice_sent = db.Column(db.Boolean, default=False)
    legal_notice_date = db.Column(db.DateTime)
    refund_detected = db.Column(db.Boolean, default=False)
    refund_amount = db.Column(db.Float)
    commission_charged = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# ══════════════════════════════════════════════════════════════════════════════
# EMAILS ENTREPRISES
# ══════════════════════════════════════════════════════════════════════════════

COMPANY_EMAILS = {
    "sncf": "service.client@sncf.fr", "air france": "customer@airfrance.fr",
    "easyjet": "customerservices@easyjet.com", "ryanair": "support@ryanair.com",
    "transavia": "service.client@transavia.com", "eurostar": "contactcentre@eurostar.com",
    "ouigo": "relationclient@ouigo.com", "uber": "support@uber.com", "bolt": "support@bolt.eu",
    "amazon": "cs-reply@amazon.fr", "zalando": "service@zalando.fr", "fnac": "serviceclient@fnac.com",
    "darty": "serviceclient@darty.com", "cdiscount": "clients@cdiscount.com",
    "asphalte": "contact@asphalte.com", "asos": "customercare@asos.com",
    "nike": "services@nike.com", "adidas": "service@adidas.fr",
}

# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

DEBUG_LOGS = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    DEBUG_LOGS.append(f"[{ts}] {msg}")
    print(f"[{ts}] {msg}")
    if len(DEBUG_LOGS) > 300:
        DEBUG_LOGS.pop(0)

def telegram(msg):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=5)
        except: pass

def extract_amount(text):
    for p in [r'(\d+[.,]?\d*)\s*€', r'€\s*(\d+[.,]?\d*)', r'(\d+[.,]?\d*)\s*euros?']:
        m = re.search(p, text, re.I)
        if m:
            try: return float(m.group(1).replace(',', '.'))
            except: pass
    return 0

def get_company_email(company):
    c = company.lower().strip()
    for k, v in COMPANY_EMAILS.items():
        if k in c: return v
    return f"contact@{re.sub(r'[^a-z0-9]', '', c)}.com"

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_email' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

# ══════════════════════════════════════════════════════════════════════════════
# GMAIL API
# ══════════════════════════════════════════════════════════════════════════════

def refresh_token(user):
    if user.token_expires_at and user.token_expires_at < datetime.utcnow() and user.refresh_token:
        try:
            r = requests.post('https://oauth2.googleapis.com/token', data={
                'client_id': GOOGLE_CLIENT_ID, 'client_secret': GOOGLE_CLIENT_SECRET,
                'refresh_token': user.refresh_token, 'grant_type': 'refresh_token'
            })
            if r.status_code == 200:
                t = r.json()
                user.access_token = t['access_token']
                user.token_expires_at = datetime.utcnow() + timedelta(seconds=t.get('expires_in', 3600))
                db.session.commit()
        except Exception as e: log(f"Refresh error: {e}")

def gmail_search(user, query, max_results=50):
    refresh_token(user)
    try:
        r = requests.get('https://gmail.googleapis.com/gmail/v1/users/me/messages',
            headers={'Authorization': f'Bearer {user.access_token}'}, params={'q': query, 'maxResults': max_results})
        return r.json().get('messages', []) if r.status_code == 200 else []
    except: return []

def gmail_get(user, msg_id):
    refresh_token(user)
    try:
        r = requests.get(f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}',
            headers={'Authorization': f'Bearer {user.access_token}'}, params={'format': 'full'})
        return r.json() if r.status_code == 200 else None
    except: return None

def gmail_send(user, to_email, subject, html_body):
    refresh_token(user)
    try:
        msg = MIMEMultipart('alternative')
        msg['To'] = to_email
        msg['From'] = f'"{user.name}" <{user.email}>'
        msg['Subject'] = subject
        msg['Bcc'] = user.email
        msg['X-Priority'] = '1'
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = requests.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
            headers={'Authorization': f'Bearer {user.access_token}', 'Content-Type': 'application/json'},
            json={'raw': raw})
        return {"success": True, "id": r.json().get('id')} if r.status_code == 200 else {"success": False, "error": r.text[:100]}
    except Exception as e: return {"success": False, "error": str(e)[:100]}

# ══════════════════════════════════════════════════════════════════════════════
# OPENAI
# ══════════════════════════════════════════════════════════════════════════════

def ai_analyze_ecommerce(text, subject, sender):
    if not OPENAI_API_KEY: return {"is_valid": False}
    client = OpenAI(api_key=OPENAI_API_KEY)
    domain = sender.split('@')[1].split('>')[0].split('.')[0] if '@' in sender else ''
    prompt = f"""Analyse email e-commerce pour litige.
EXPÉDITEUR: {sender}
SUJET: {subject}
CONTENU: {text[:2000]}
DÉTECTE: Vendeur, Montant, Problème, Preuve
REJETTE: Transport, Marketing, Confirmation sans problème
JSON: {{"is_valid": true/false, "litige": true/false, "company": "NOM", "amount": "XX€", "law": "Article", "proof": "Phrase", "reason": "si rejet"}}"""
    try:
        r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=400)
        m = re.search(r'\{.*\}', r.choices[0].message.content, re.DOTALL)
        if m:
            res = json.loads(m.group())
            if res.get("company", "").lower() in ["inconnu", "unknown", ""] and domain not in ["gmail", "yahoo", "outlook"]:
                res["company"] = domain.capitalize()
            return res
    except Exception as e: log(f"AI error: {e}")
    return {"is_valid": False, "litige": False}

def ai_analyze_travel(text, subject, sender):
    if not OPENAI_API_KEY: return {"is_valid": False}
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""Expert droit transports (UE 261/2004).
EMAIL: {sender} | {subject} | {text[:2000]}
DÉTECTE: Retard/annulation VOL/TRAIN, Bagages
REJETTE: Colis, E-commerce, Confirmation
INDEMNITÉS: Vol >3h: 250-600€, Train >1h: 25-50%
JSON: {{"is_valid": true/false, "litige": true/false, "company": "COMPAGNIE", "amount": "XX€", "law": "Règlement", "proof": "Phrase", "reason": "si rejet"}}"""
    try:
        r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.1, max_tokens=400)
        m = re.search(r'\{.*\}', r.choices[0].message.content, re.DOTALL)
        if m: return json.loads(m.group())
    except Exception as e: log(f"AI error: {e}")
    return {"is_valid": False, "litige": False}

def generate_letter(company, amount, motif, law, client_name, client_email):
    if not OPENAI_API_KEY: return None
    client = OpenAI(api_key=OPENAI_API_KEY)
    today = datetime.now().strftime("%d/%m/%Y")
    deadline = (datetime.now() + timedelta(days=8)).strftime("%d/%m/%Y")
    prompt = f"""Mise en demeure juridique.
DESTINATAIRE: {company.upper()} | MONTANT: {amount} | MOTIF: {motif} | LOI: {law}
CLIENT: {client_name} ({client_email}) | DATE: {today} | DÉLAI: {deadline}
Style: Froid, juridique. Cite articles. Mentionne: Médiateur, DGCCRF, Tribunal.
HTML (<p>, <strong>, <ul>)."""
    try:
        r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1200)
        content = r.choices[0].message.content
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="font-family:Georgia,serif;max-width:700px;margin:0 auto;padding:20px;color:#1e293b;">
<div style="background:linear-gradient(135deg,#dc2626,#991b1b);color:white;padding:25px;border-radius:10px 10px 0 0;text-align:center;"><h1 style="margin:0;font-size:24px;">⚖️ MISE EN DEMEURE</h1></div>
<div style="background:white;padding:30px;border:1px solid #e2e8f0;"><p style="text-align:right;color:#64748b;">Paris, le {today}</p><p><strong>À:</strong> {company.upper()}</p>{content}
<div style="background:#fef2f2;border:1px solid #fecaca;padding:15px;margin:20px 0;border-radius:8px;"><p style="margin:0;color:#dc2626;"><strong>⚠️ Sans réponse avant le {deadline}, je saisirai le Médiateur, la DGCCRF, et/ou le Tribunal.</strong></p></div>
<p>Cordialement,</p><p><strong>{client_name}</strong><br><span style="color:#64748b;">{client_email}</span></p></div>
<div style="background:#1e293b;color:#94a3b8;padding:15px;text-align:center;border-radius:0 0 10px 10px;font-size:12px;"><strong style="color:#fbbf24;">Justicio.fr</strong></div></body></html>"""
    except: return None

# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES HTML
# ══════════════════════════════════════════════════════════════════════════════

STYLE = """<!DOCTYPE html><html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="google-site-verification" content="Qeh_EJmqe8ZdqRUxtJ_JjH1TFtnVUpCrAIhkOxNtkL0"/>
<title>Justicio</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚖️</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--primary:#4f46e5;--success:#10b981;--warning:#f59e0b;--danger:#ef4444}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#0f172a,#1e1b4b,#312e81);min-height:100vh;color:white}
.container{max-width:800px;margin:0 auto;padding:30px 20px}
.card{background:rgba(255,255,255,0.95);border-radius:20px;padding:30px;margin:20px 0;color:#1e293b;box-shadow:0 25px 50px -12px rgba(0,0,0,0.25)}
.btn{display:inline-block;padding:15px 30px;border-radius:12px;text-decoration:none;font-weight:600;transition:all 0.3s;border:none;cursor:pointer;font-size:1rem}
.btn-primary{background:linear-gradient(135deg,var(--primary),#3730a3);color:white}
.btn-success{background:linear-gradient(135deg,var(--success),#059669);color:white}
.btn-warning{background:linear-gradient(135deg,var(--warning),#d97706);color:white}
.btn:hover{transform:translateY(-2px);box-shadow:0 10px 30px rgba(0,0,0,0.3)}
.scan-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:25px;margin:30px 0}
.scan-card{background:rgba(255,255,255,0.1);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.2);border-radius:24px;padding:35px 25px;text-align:center;cursor:pointer;transition:all 0.4s;text-decoration:none;color:white}
.scan-card:hover{transform:translateY(-8px);background:rgba(255,255,255,0.15);box-shadow:0 30px 60px rgba(0,0,0,0.3)}
.scan-card.ecommerce{border-left:4px solid var(--success)}
.scan-card.travel{border-left:4px solid var(--warning)}
.scan-card .icon{font-size:4rem;margin-bottom:20px}
.scan-card .title{font-size:1.4rem;font-weight:700;margin-bottom:10px}
.scan-card .desc{font-size:0.9rem;opacity:0.8;margin-bottom:15px}
.badge{display:inline-block;padding:6px 14px;border-radius:20px;font-size:0.75rem;font-weight:600}
.badge-success{background:var(--success)}
.badge-warning{background:var(--warning)}
.litige-card{background:white;border-radius:16px;padding:25px;margin:15px 0;border-left:5px solid var(--success);box-shadow:0 4px 20px rgba(0,0,0,0.1);color:#1e293b}
.litige-card.travel{border-left-color:var(--warning)}
#loadingOverlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:linear-gradient(135deg,#0f172a,#1e1b4b);z-index:9999;justify-content:center;align-items:center;flex-direction:column}
#loadingOverlay.active{display:flex}
.loader{width:80px;height:80px;border:4px solid rgba(255,255,255,0.1);border-top:4px solid var(--success);border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
footer{text-align:center;padding:40px 20px;color:rgba(255,255,255,0.5);font-size:0.85rem}
footer a{color:rgba(255,255,255,0.7);text-decoration:none;margin:0 10px}
@media(max-width:600px){.scan-cards{grid-template-columns:1fr}}
</style></head><body>
<div id="loadingOverlay"><div class="loader"></div><div style="margin-top:30px;font-size:1.3rem;">Analyse en cours...</div></div>
<script>function showLoading(){document.getElementById('loadingOverlay').classList.add('active');}</script>
"""

FOOTER = """<footer><a href="/cgu">CGU</a> | <a href="/confidentialite">Confidentialité</a> | <a href="/mentions-legales">Mentions Légales</a><p>© 2024 Justicio.fr</p></footer></body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES - AUTH
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    if 'user_email' in session: return redirect('/dashboard')
    return STYLE + """<div class="container" style="text-align:center;padding-top:60px;">
<div style="font-size:5rem;margin-bottom:20px;">⚖️</div><h1 style="font-size:3rem;font-weight:800;margin-bottom:15px;">JUSTICIO</h1>
<p style="font-size:1.3rem;opacity:0.8;margin-bottom:40px;">Récupérez votre argent automatiquement</p>
<div style="background:rgba(255,255,255,0.1);border-radius:20px;padding:40px;max-width:500px;margin:0 auto;">
<div style="display:flex;justify-content:center;gap:40px;margin-bottom:30px;">
<div style="text-align:center;"><div style="font-size:2.5rem;font-weight:800;color:#10b981;">847€</div><div style="font-size:0.85rem;opacity:0.7;">Moyenne récupérée</div></div>
<div style="text-align:center;"><div style="font-size:2.5rem;font-weight:800;color:#fbbf24;">89%</div><div style="font-size:0.85rem;opacity:0.7;">Taux de succès</div></div>
</div>
<a href="/login" class="btn btn-primary" style="display:block;padding:18px;font-size:1.1rem;">🔐 Connexion avec Google</a>
<p style="margin-top:20px;font-size:0.85rem;opacity:0.6;">0€ d'avance • Commission au succès</p>
</div>
<div style="margin-top:60px;display:flex;justify-content:center;gap:60px;flex-wrap:wrap;">
<div style="text-align:center;"><div style="font-size:3rem;">📦</div><div style="font-weight:600;margin-top:10px;">E-commerce</div></div>
<div style="text-align:center;"><div style="font-size:3rem;">✈️</div><div style="font-weight:600;margin-top:10px;">Transport</div></div>
<div style="text-align:center;"><div style="font-size:3rem;">⚡</div><div style="font-weight:600;margin-top:10px;">Automatique</div></div>
</div></div>""" + FOOTER

@app.route("/login")
def login():
    return google.authorize_redirect(url_for('callback', _external=True, _scheme='https'), access_type='offline', prompt='consent')

@app.route("/callback")
def callback():
    try:
        token = google.authorize_access_token()
        userinfo = token.get('userinfo') or google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        email, name = userinfo['email'], userinfo.get('name', userinfo['email'].split('@')[0])
        user = User.query.filter_by(email=email).first()
        if not user: user = User(email=email); db.session.add(user)
        user.name, user.access_token = name, token['access_token']
        user.refresh_token = token.get('refresh_token') or user.refresh_token
        user.token_expires_at = datetime.utcnow() + timedelta(seconds=token.get('expires_in', 3600))
        db.session.commit()
        session['user_email'], session['user_name'] = email, name
        log(f"Login: {email}"); telegram(f"👤 Connexion: {email}")
        return redirect('/dashboard')
    except Exception as e:
        log(f"Callback error: {e}")
        return f"Erreur: {e}<br><a href='/'>Retour</a>"

@app.route("/logout")
def logout():
    session.clear()
    return redirect('/')

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES - DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    user = User.query.filter_by(email=session['user_email']).first()
    lits = Litigation.query.filter_by(user_email=session['user_email']).all()
    detected = len([l for l in lits if l.status in ['detected', 'pending']])
    sent = len([l for l in lits if l.status == 'sent'])
    refunded = sum(l.refund_amount or 0 for l in lits if l.refund_detected)
    badge = f"<span style='background:#ef4444;color:white;padding:3px 10px;border-radius:20px;font-size:0.8rem;margin-left:10px;'>{detected}</span>" if detected > 0 else ""
    return STYLE + f"""<div class="container" style="text-align:center;">
<div style="margin-bottom:30px;"><div style="font-size:3rem;margin-bottom:10px;">⚖️</div><h1>Bonjour {user.name.split()[0] if user.name else ''} !</h1></div>
<div style="display:flex;justify-content:center;gap:30px;margin-bottom:40px;flex-wrap:wrap;">
<div style="background:rgba(255,255,255,0.1);padding:20px 30px;border-radius:15px;"><div style="font-size:2rem;font-weight:700;color:#10b981;">{detected}</div><div style="font-size:0.85rem;opacity:0.7;">En attente</div></div>
<div style="background:rgba(255,255,255,0.1);padding:20px 30px;border-radius:15px;"><div style="font-size:2rem;font-weight:700;color:#fbbf24;">{sent}</div><div style="font-size:0.85rem;opacity:0.7;">Envoyés</div></div>
<div style="background:rgba(255,255,255,0.1);padding:20px 30px;border-radius:15px;"><div style="font-size:2rem;font-weight:700;color:#10b981;">{refunded:.0f}€</div><div style="font-size:0.85rem;opacity:0.7;">Récupéré</div></div>
</div>
<div class="scan-cards">
<a href="/scan-litiges" class="scan-card ecommerce" onclick="showLoading();return true;"><div class="icon">📦</div><div class="title">SCAN E-COMMERCE</div><div class="desc">Colis, retours, défauts...<br><b>Toutes marques</b></div><span class="badge badge-success">⚡ Grand Filet</span></a>
<a href="/scan-travel" class="scan-card travel" onclick="showLoading();return true;"><div class="icon">✈️</div><div class="title">SCAN VOYAGES</div><div class="desc">Retards, annulations...<br><b>Trains & Avions</b></div><span class="badge badge-warning">💎 Jusqu'à 600€</span></a>
</div>
<div style="margin-top:30px;"><a href="/mes-dossiers" class="btn btn-primary" style="margin:5px;">📂 Mes Dossiers {badge}</a></div>
<div style="margin-top:40px;"><a href="/logout" style="color:rgba(255,255,255,0.5);font-size:0.9rem;">Se déconnecter</a></div>
</div>""" + FOOTER

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES - SCANS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/scan-litiges")
@login_required
def scan_litiges():
    user = User.query.filter_by(email=session['user_email']).first()
    log(f"📦 Scan E-commerce: {user.email}")
    query = """(commande OR colis OR livraison OR order OR delivery) (retard OR delay OR problème OR remboursement OR refund OR "non reçu" OR annulé OR défectueux OR manquant OR perdu) -label:trash -category:promotions -(train OR vol OR SNCF)"""
    messages = gmail_search(user, query, 60)
    log(f"📧 {len(messages)} emails")
    if not messages:
        return STYLE + """<div class="container" style="text-align:center;"><div style="font-size:4rem;">📦</div><h1>Aucun email trouvé</h1><a href="/dashboard" class="btn btn-primary" style="margin-top:30px;">Retour</a></div>""" + FOOTER
    detected = []
    existing = {l.message_id for l in Litigation.query.filter_by(user_email=user.email).all() if l.message_id}
    spam = ['temu', 'shein', 'wish', 'newsletter', 'promo@', 'marketing@']
    for msg in messages[:40]:
        mid = msg.get('id')
        if mid in existing: continue
        data = gmail_get(user, mid)
        if not data: continue
        headers = data.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
        snippet = data.get('snippet', '')
        if any(s in sender.lower() for s in spam): continue
        body = snippet
        payload = data.get('payload', {})
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/plain':
                    d = part.get('body', {}).get('data', '')
                    if d:
                        try: body = base64.urlsafe_b64decode(d).decode('utf-8', errors='ignore')[:3000]
                        except: pass
                        break
        analysis = ai_analyze_ecommerce(body, subject, sender)
        if analysis.get('is_valid') and analysis.get('litige'):
            company, amount = analysis.get('company', 'Inconnu'), analysis.get('amount', 'À déterminer')
            amount_float = extract_amount(amount) or extract_amount(body)
            detected.append({'message_id': mid, 'company': company, 'amount': amount, 'amount_float': amount_float, 'law': analysis.get('law', 'Code de la consommation'), 'proof': analysis.get('proof', snippet[:150]), 'category': 'ecommerce'})
            log(f"✅ {company} - {amount}")
    if not detected:
        return STYLE + """<div class="container" style="text-align:center;"><div style="font-size:4rem;">✅</div><h1>Aucun litige détecté</h1><a href="/dashboard" class="btn btn-primary" style="margin-top:30px;">Retour</a></div>""" + FOOTER
    session['detected_litigations'] = detected
    total = sum(d['amount_float'] for d in detected if d['amount_float'] > 0)
    cards = ""
    for d in detected:
        amt = f"{d['amount_float']:.0f}€" if d['amount_float'] > 0 else "À déterminer"
        cards += f"""<div class="litige-card"><div style="display:flex;justify-content:space-between;"><div><span class="badge badge-success">📦</span><div style="font-size:1.2rem;font-weight:700;margin-top:10px;">{d['company'].upper()}</div></div><div style="font-size:1.8rem;font-weight:800;color:#10b981;">{amt}</div></div><div style="background:#f8fafc;padding:15px;border-radius:10px;margin:15px 0;border-left:3px solid #f59e0b;">📝 {d['proof'][:200]}...</div><div style="font-size:0.85rem;color:#64748b;">⚖️ {d['law']}</div></div>"""
    return STYLE + f"""<div class="container"><div style="text-align:center;margin-bottom:30px;"><div style="font-size:4rem;">🎉</div><h1>{len(detected)} Litige(s) !</h1><p style="color:#10b981;font-size:1.3rem;font-weight:600;">💰 {total:.0f}€</p></div>{cards}<div style="text-align:center;margin-top:40px;"><a href="/setup-payment" class="btn btn-success" style="padding:20px 50px;font-size:1.2rem;">🚀 RÉCUPÉRER</a></div><div style="text-align:center;margin-top:20px;"><a href="/dashboard" style="color:rgba(255,255,255,0.6);">← Retour</a></div></div>""" + FOOTER

@app.route("/scan-travel")
@login_required
def scan_travel():
    user = User.query.filter_by(email=session['user_email']).first()
    log(f"✈️ Scan Voyages: {user.email}")
    query = """(SNCF OR "Air France" OR EasyJet OR Ryanair OR Transavia OR Eurostar OR Ouigo OR Uber OR Bolt OR train OR vol OR flight) (retard OR delay OR annulé OR cancelled OR compensation OR bagage OR perdu) -amazon -zalando -asos -fnac -colis -commande -label:trash newer_than:365d"""
    messages = gmail_search(user, query, 80)
    log(f"📧 {len(messages)} emails")
    if not messages:
        return STYLE + """<div class="container" style="text-align:center;"><div style="font-size:4rem;">✈️</div><h1>Aucun email transport</h1><a href="/dashboard" class="btn btn-primary" style="margin-top:30px;">Retour</a></div>""" + FOOTER
    detected = []
    existing = {l.message_id for l in Litigation.query.filter_by(user_email=user.email).all() if l.message_id}
    blacklist = ['amazon', 'zalando', 'asos', 'fnac', 'darty', 'cdiscount']
    for msg in messages[:50]:
        mid = msg.get('id')
        if mid in existing: continue
        data = gmail_get(user, mid)
        if not data: continue
        headers = data.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
        snippet = data.get('snippet', '')
        if any(b in f"{sender}{subject}{snippet}".lower() for b in blacklist): continue
        body = snippet
        payload = data.get('payload', {})
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/plain':
                    d = part.get('body', {}).get('data', '')
                    if d:
                        try: body = base64.urlsafe_b64decode(d).decode('utf-8', errors='ignore')[:3000]
                        except: pass
                        break
        analysis = ai_analyze_travel(body, subject, sender)
        if analysis.get('is_valid') and analysis.get('litige'):
            company, amount = analysis.get('company', 'Transporteur'), analysis.get('amount', '250€')
            amount_float = extract_amount(amount)
            if amount_float == 0:
                c = company.lower()
                amount_float = 250 if any(a in c for a in ['air', 'easyjet', 'ryanair']) else (50 if any(t in c for t in ['sncf', 'ouigo', 'eurostar']) else 100)
                amount = f"{amount_float}€"
            detected.append({'message_id': mid, 'company': company, 'amount': amount, 'amount_float': amount_float, 'law': analysis.get('law', 'Règlement UE 261/2004'), 'proof': analysis.get('proof', snippet[:150]), 'category': 'travel'})
            log(f"✅ {company} - {amount}")
    if not detected:
        return STYLE + """<div class="container" style="text-align:center;"><div style="font-size:4rem;">✅</div><h1>Aucun litige transport</h1><a href="/dashboard" class="btn btn-primary" style="margin-top:30px;">Retour</a></div>""" + FOOTER
    session['detected_litigations'] = detected
    total = sum(d['amount_float'] for d in detected)
    cards = ""
    for d in detected:
        cards += f"""<div class="litige-card travel"><div style="display:flex;justify-content:space-between;"><div><span class="badge badge-warning">✈️</span><div style="font-size:1.2rem;font-weight:700;margin-top:10px;">{d['company'].upper()}</div></div><div style="font-size:1.8rem;font-weight:800;color:#f59e0b;">{d['amount']}</div></div><div style="background:#f8fafc;padding:15px;border-radius:10px;margin:15px 0;border-left:3px solid #f59e0b;">📝 {d['proof'][:200]}...</div><div style="font-size:0.85rem;color:#64748b;">⚖️ {d['law']}</div></div>"""
    return STYLE + f"""<div class="container"><div style="text-align:center;margin-bottom:30px;"><div style="font-size:4rem;">🎉</div><h1>{len(detected)} Litige(s) Transport !</h1><p style="color:#f59e0b;font-size:1.3rem;font-weight:600;">💰 {total:.0f}€</p></div>{cards}<div style="text-align:center;margin-top:40px;"><a href="/setup-payment" class="btn btn-warning" style="padding:20px 50px;font-size:1.2rem;">🚀 RÉCUPÉRER</a></div><div style="text-align:center;margin-top:20px;"><a href="/dashboard" style="color:rgba(255,255,255,0.6);">← Retour</a></div></div>""" + FOOTER

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES - PAIEMENT
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/setup-payment")
@login_required
def setup_payment():
    user = User.query.filter_by(email=session['user_email']).first()
    if not STRIPE_SECRET_KEY: return "Stripe non configuré"
    detected = session.get('detected_litigations', [])
    if not detected: return redirect('/dashboard')
    try:
        if user.stripe_customer_id:
            customer = stripe.Customer.retrieve(user.stripe_customer_id)
            if customer.get('invoice_settings', {}).get('default_payment_method'):
                log(f"💳 One-click: {user.email}")
                return redirect('/success')
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(email=user.email, name=user.name)
            user.stripe_customer_id = customer.id
            db.session.commit()
        checkout = stripe.checkout.Session.create(customer=user.stripe_customer_id, payment_method_types=['card'], mode='setup', success_url=url_for('success', _external=True, _scheme='https'), cancel_url=url_for('dashboard', _external=True, _scheme='https'))
        log(f"💳 Checkout: {user.email}")
        return redirect(checkout.url)
    except Exception as e:
        log(f"Stripe error: {e}")
        return f"Erreur: {e}<br><a href='/dashboard'>Retour</a>"

@app.route("/success")
@login_required
def success():
    user = User.query.filter_by(email=session['user_email']).first()
    detected = session.get('detected_litigations', [])
    log(f"🚀 Success: {len(detected)} litiges ({user.email})")
    sent_count, errors, details = 0, [], []
    for d in detected:
        company, amount, amount_float = d.get('company', 'Inconnu'), d.get('amount', '0€'), d.get('amount_float', 0)
        law, proof, message_id, category = d.get('law', ''), d.get('proof', ''), d.get('message_id'), d.get('category', 'ecommerce')
        existing = Litigation.query.filter_by(message_id=message_id, user_email=user.email).first() if message_id else None
        if not existing:
            lit = Litigation(user_email=user.email, company=company, amount=amount, amount_float=amount_float, law=law, proof=proof, message_id=message_id, category=category, status='pending')
            db.session.add(lit); db.session.commit()
        else: lit = existing
        letter = generate_letter(company, amount, proof, law, user.name or user.email.split('@')[0], user.email)
        if not letter: errors.append(f"❌ {company}: Génération"); continue
        target = get_company_email(company)
        lit.merchant_email = target
        result = gmail_send(user, target, f"⚖️ MISE EN DEMEURE - {company.upper()} - {amount}", letter)
        if result.get('success'):
            lit.status, lit.legal_notice_sent, lit.legal_notice_date = 'sent', True, datetime.utcnow()
            db.session.commit(); sent_count += 1
            details.append({'company': company, 'amount': amount, 'status': '✅'})
            log(f"📧 Envoyé: {company} -> {target}")
            telegram(f"📧 MISE EN DEMEURE!\n🏪 {company.upper()}\n💰 {amount}\n📬 {target}\n👤 {user.email}")
        else:
            errors.append(f"❌ {company}: {result.get('error', '')[:30]}")
            details.append({'company': company, 'amount': amount, 'status': '❌'})
    session.pop('detected_litigations', None)
    report = ""
    for d in details:
        color = '#10b981' if d['status'] == '✅' else '#ef4444'
        report += f"""<div style="display:flex;justify-content:space-between;padding:12px;background:#f8fafc;margin:8px 0;border-radius:8px;border-left:4px solid {color};"><strong>{d['company'].upper()}</strong> {d['amount']}<span style="color:{color};">{d['status']}</span></div>"""
    return STYLE + f"""<div class="container" style="text-align:center;">
<div style="background:linear-gradient(135deg,#d1fae5,#a7f3d0);padding:40px;border-radius:20px;margin-bottom:30px;"><div style="font-size:4rem;">{"✅" if sent_count > 0 else "⚠️"}</div><h1 style="color:#065f46;margin:15px 0;">{sent_count} Mise(s) en demeure envoyée(s) !</h1></div>
<div class="card"><h3 style="margin-bottom:20px;">📋 Rapport</h3>{report}</div>
{"<div class='card' style='background:#fef2f2;border-left:4px solid #ef4444;'><h4>⚠️ Erreurs</h4><p>" + "<br>".join(errors) + "</p></div>" if errors else ""}
<div style="background:rgba(59,130,246,0.1);padding:15px;border-radius:10px;margin:20px 0;"><p style="color:white;margin:0;">📧 Copie envoyée dans votre boîte mail.</p></div>
<a href="/mes-dossiers" class="btn btn-primary" style="margin-top:20px;">📂 MES DOSSIERS</a></div>""" + FOOTER

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES - DOSSIERS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/mes-dossiers")
@login_required
def mes_dossiers():
    lits = Litigation.query.filter_by(user_email=session['user_email']).order_by(Litigation.created_at.desc()).all()
    if not lits:
        return STYLE + """<div class="container" style="text-align:center;"><div style="font-size:4rem;">📂</div><h1>Aucun dossier</h1><a href="/dashboard" class="btn btn-primary" style="margin-top:30px;">Retour</a></div>""" + FOOTER
    rows = ""
    for l in lits:
        badges = {'detected': ('⏳', '#fbbf24'), 'pending': ('🔄', '#3b82f6'), 'sent': ('📧', '#10b981'), 'refunded': ('💰', '#059669')}
        icon, color = badges.get(l.status, ('❓', '#64748b'))
        cat_icon = '✈️' if l.category == 'travel' else '📦'
        rows += f"""<div class="card" style="margin:15px 0;padding:20px;"><div style="display:flex;justify-content:space-between;align-items:center;"><div><span style="font-size:1.5rem;">{cat_icon}</span><strong style="margin-left:10px;">{l.company.upper() if l.company else 'Inconnu'}</strong></div><div style="text-align:right;"><div style="font-size:1.4rem;font-weight:700;color:#10b981;">{l.amount or '?'}</div><span style="background:{color};color:white;padding:3px 10px;border-radius:10px;font-size:0.8rem;">{icon} {l.status}</span></div></div><div style="margin-top:15px;padding-top:15px;border-top:1px solid #e2e8f0;font-size:0.85rem;color:#64748b;">⚖️ {l.law or 'N/A'} | 📅 {l.created_at.strftime('%d/%m/%Y') if l.created_at else 'N/A'}</div></div>"""
    return STYLE + f"""<div class="container"><h1 style="text-align:center;margin-bottom:30px;">📂 Mes Dossiers</h1>{rows}<div style="text-align:center;margin-top:30px;"><a href="/dashboard" class="btn btn-primary">← Retour</a></div></div>""" + FOOTER

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES - CRON
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cron/check-refunds")
def cron_check_refunds():
    log("🔄 CRON: Check refunds")
    pending = Litigation.query.filter_by(status='sent', refund_detected=False).all()
    found, charged = 0, 0
    for lit in pending:
        user = User.query.filter_by(email=lit.user_email).first()
        if not user or not user.access_token: continue
        company = lit.company.lower() if lit.company else ''
        query = f"""from:{company} (remboursement OR virement OR crédit OR refund OR "votre compte a été crédité") newer_than:30d"""
        messages = gmail_search(user, query, 20)
        for msg in messages:
            data = gmail_get(user, msg['id'])
            if not data: continue
            snippet = data.get('snippet', '')
            amount = extract_amount(snippet)
            if amount > 0:
                lit.refund_detected, lit.refund_amount, lit.status = True, amount, 'refunded'
                found += 1
                log(f"💰 Remboursement: {lit.company} - {amount}€ ({user.email})")
                if user.stripe_customer_id and not lit.commission_charged:
                    commission = round(amount * 0.30, 2)
                    cents = int(commission * 100)
                    if cents >= 50:
                        try:
                            customer = stripe.Customer.retrieve(user.stripe_customer_id)
                            pm = customer.get('invoice_settings', {}).get('default_payment_method')
                            if pm:
                                pi = stripe.PaymentIntent.create(amount=cents, currency='eur', customer=user.stripe_customer_id, payment_method=pm, off_session=True, confirm=True, description=f"Commission Justicio - {lit.company}")
                                if pi.status == 'succeeded':
                                    lit.commission_charged = True; charged += 1
                                    log(f"💳 Commission: {commission}€ ({user.email})")
                                    telegram(f"💳 COMMISSION!\n🏪 {lit.company}\n💰 {amount}€\n📊 {commission}€\n👤 {user.email}")
                        except Exception as e: log(f"Commission error: {e}")
                db.session.commit()
                break
    db.session.commit()
    return jsonify({'status': 'ok', 'refunds': found, 'commissions': charged})

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES - PAGES LÉGALES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cgu")
def cgu():
    return STYLE + """<div class="container"><div class="card"><h1>CGU</h1><p>Commission 30% au succès uniquement.</p></div><div style="text-align:center;"><a href="/" class="btn btn-primary">Retour</a></div></div>""" + FOOTER

@app.route("/confidentialite")
def confidentialite():
    return STYLE + """<div class="container"><div class="card"><h1>Confidentialité</h1><p>Données: Email, tokens Gmail. RGPD conforme.</p></div><div style="text-align:center;"><a href="/" class="btn btn-primary">Retour</a></div></div>""" + FOOTER

@app.route("/mentions-legales")
def mentions_legales():
    return STYLE + """<div class="container"><div class="card"><h1>Mentions Légales</h1><p>Justicio SAS, Paris. Contact: support@justicio.fr</p></div><div style="text-align:center;"><a href="/" class="btn btn-primary">Retour</a></div></div>""" + FOOTER

@app.route("/debug-logs")
def debug_logs():
    logs = "<br>".join(DEBUG_LOGS[-100:]) if DEBUG_LOGS else "Aucun log"
    return f"""<html><body style="font-family:monospace;background:#1e293b;color:#10b981;padding:20px;"><h1>🔧 Logs</h1><pre style="background:#0f172a;padding:20px;border-radius:10px;">{logs}</pre><br><a href="/" style="color:#3b82f6;">← Retour</a></body></html>"""

@app.route("/health")
def health():
    return jsonify({'status': 'healthy', 'stripe': bool(STRIPE_SECRET_KEY), 'openai': bool(OPENAI_API_KEY), 'google': bool(GOOGLE_CLIENT_ID)})

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

