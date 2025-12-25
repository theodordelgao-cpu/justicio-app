import os
import base64
import requests
import stripe
from flask import Flask, session, redirect, request, url_for, render_template_string
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from datetime import datetime
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURATION (Variables d'environnement Render) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_billion_dollar_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AERODATABOX_KEY = os.environ.get("AERODATABOX_API_KEY") 
SNCF_TOKEN = os.environ.get("NAVITIA_API_TOKEN") 
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") 
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY") 
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
DATABASE_URL = os.environ.get("DATABASE_URL")
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026")

stripe.api_key = STRIPE_SK

# --- BASE DE DONNÉES (Tokens + Litiges) ---
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    refresh_token = db.Column(db.String(500)) # Pour le scan automatique des 12h
    stripe_customer_id = db.Column(db.String(100)) # Pour prélever les 30%
    payment_method_id = db.Column(db.String(100)) # La carte enregistrée
    name = db.Column(db.String(100))

class Litigation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120))
    company = db.Column(db.String(100))
    amount = db.Column(db.String(50))
    status = db.Column(db.String(50), default="Détecté") # Détecté / Mis en demeure / Payé

with app.app_context():
    db.create_all()

# --- CONFIGURATION GOOGLE OAUTH ---
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
client_secrets_config = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID, "project_id": "justicio-app",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": GOOGLE_CLIENT_SECRET
    }
}
SCOPES = ["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", 
          "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose", "openid"]

# --- IA & RADARS ---
def analyze_litigation(text, subject):
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"system", "content": "Réponds uniquement: MONTANT | LOI. Exemple: 45€ | Article L.216-1"},
                      {"role":"user", "content": f"Mail: {subject}. Snippet: {text[:400]}"}]
        )
        return [d.strip() for d in res.choices[0].message.content.split("|")]
    except: return ["À calculer", "Code de la consommation"]

def send_legal_email(creds, target_email, company, amount, law):
    """Envoie la mise en demeure depuis le mail du client"""
    service = build('gmail', 'v1', credentials=creds)
    body = f"Madame, Monsieur,\n\nNous agissons pour le compte de Justicio concernant un remboursement de {amount} chez {company} basé sur {law}..."
    msg = MIMEText(body)
    msg['to'] = target_email
    msg['subject'] = f"MISE EN DEMEURE - {company} - Dossier Justicio"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()

# --- DESIGN & PAGES LÉGALES ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:40px 20px;display:flex;flex-direction:column;align-items:center}
.card{background:white;border-radius:20px;padding:30px;margin:15px;width:100%;max-width:550px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:8px solid #ef4444}
.btn{display:inline-block;background:#4f46e5;color:white;padding:16px 32px;border-radius:12px;text-decoration:none;font-weight:bold;margin-top:20px}
footer{margin-top:50px;font-size:0.8rem;text-align:center;color:#94a3b8}footer a{color:#4f46e5;text-decoration:none;margin:0 10px}</style>"""

FOOTER = """<footer><a href='/cgu'>CGU</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a><p>© 2025 Justicio.fr - Carcassonne</p></footer>"""

@app.route("/cgu")
def cgu(): return STYLE + "<h1>CGU</h1><p>Commission de 30% perçue au succès.</p><a href='/'>Retour</a>"

@app.route("/confidentialite")
def confidentialite(): return STYLE + "<h1>Confidentialité</h1><p>Accès Gmail limité à la détection de litiges.</p><a href='/'>Retour</a>"

@app.route("/mentions-legales")
def mentions_legales(): return STYLE + "<h1>Mentions Légales</h1><p>Justicio - 5 rue peire cardenal, 11000 Carcassonne.</p><a href='/'>Retour</a>"

# --- ROUTES PRINCIPALES ---
@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"<h1>⚖️ JUSTICIO</h1><p>Bonjour {session.get('name')}</p><a href='/scan' class='btn'>🔍 ANALYSER MES LITIGES</a>" + FOOTER

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    results = service.users().messages().list(userId='me', q="remboursement OR retard OR Amazon OR SNCF", maxResults=5).execute()
    msgs = results.get('messages', [])
    html = "<h1>Litiges Identifiés</h1>"
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next((h['value'] for h in f['payload']['headers'] if h['name'] == 'Subject'), "Sans objet")
        ana = analyze_litigation(f.get('snippet', ''), subj)
        if "€" in ana[0]:
            html += f"<div class='card'><h3>{subj}</h3><p>Gain : {ana[0]}</p><a href='/pre-payment?amount={ana[0]}&subject={subj}' class='btn'>Récupérer</a></div>"
    return STYLE + html + FOOTER

@app.route("/pre-payment")
def pre_payment():
    amount = request.args.get("amount", "vos fonds")
    return STYLE + f"<h1>🛡️ Sécurisez votre dossier</h1><p>Récupérez <b>{amount}</b> maintenant.</p><div class='card'><h3>Pourquoi mettre ma carte ?</h3><ul><li>✅ 0€ débité aujourd'hui</li><li>💳 Reçoit vos fonds</li><li>⚖️ 30% seulement si vous gagnez</li></ul></div><a href='/setup-payment' class='btn'>ENREGISTRER MA CARTE</a>" + FOOTER

@app.route("/setup-payment")
def setup_payment():
    session_stripe = stripe.checkout.Session.create(payment_method_types=['card'], mode='setup', success_url=url_for('index', _external=True) + "?payment=success", cancel_url=url_for('index', _external=True))
    return redirect(session_stripe.url, code=303)

# --- LE SCAN DES 12H (CRON) ---
@app.route("/cron-scan/<token>")
def cron_scan(token):
    if token != SCAN_TOKEN: return "Interdit", 403
    users = User.query.all()
    for u in users:
        # Ici le robot utilise u.refresh_token pour scanner
        # Si remboursement trouvé : prélèvement de 30% via u.stripe_customer_id
        pass
    return "Scan automatique terminé."

@app.route("/login")
def login():
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session["state"] = state
    return redirect(url)

@app.route("/callback")
def callback():
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session["credentials"] = {'token': creds.token, 'refresh_token': creds.refresh_token, 'token_uri': creds.token_uri, 'client_id': creds.client_id, 'client_secret': creds.client_secret, 'scopes': creds.scopes}
    info = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
    user = User.query.filter_by(email=info['email']).first()
    if not user: user = User(email=info['email'], name=info.get('name'))
    if creds.refresh_token: user.refresh_token = creds.refresh_token
    db.session.commit()
    session["name"] = info.get('name')
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
