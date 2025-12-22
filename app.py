import os
import base64
import random
import time
from flask import Flask, session, redirect, request, url_for
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURATION ---
app.secret_key = os.environ.get("SECRET_KEY", "azerty_super_secret_key")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
# CLÉ SECRÈTE POUR LE SCAN (Choisis ce que tu veux dans Render)
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "mon_code_secret_123")

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODÈLE DE LA BASE ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    refresh_token = db.Column(db.String(500), nullable=True)
    name = db.Column(db.String(100))

with app.app_context():
    db.create_all()

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

client_secrets_config = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "project_id": "justicio-app",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": GOOGLE_CLIENT_SECRET
    }
}

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose", 
    "openid"
]

# --- STYLE (INCHANGÉ) ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap'); :root { --primary: #4f46e5; --danger: #dc2626; --bg: #f8fafc; } body { font-family: 'Outfit', sans-serif; background-color: var(--bg); margin: 0; padding: 40px 20px; color: #0f172a; display: flex; flex-direction: column; align-items: center; min-height: 100vh; } .container { width: 100%; max-width: 650px; } h1 { font-weight: 800; font-size: 2.5rem; text-align: center; margin-bottom: 10px; color: var(--primary); } .subtitle { text-align: center; color: #64748b; margin-bottom: 40px; font-size: 1.1rem; } .card { background: white; border-radius: 20px; padding: 25px; margin-bottom: 20px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #fee2e2; border-left: 8px solid var(--danger); background: #fff1f2; animation: popIn 0.5s ease; } h3 { margin: 0 0 5px 0; font-size: 1.2rem; } .sender { color: #64748b; font-size: 0.9rem; margin-bottom: 15px; font-weight: 600; } .badge { display: inline-block; padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; font-weight: 600; margin-right: 5px; background: white; border: 1px solid #fca5a5; color: #991b1b; } .btn { display: block; width: 100%; padding: 15px; border-radius: 12px; text-align: center; text-decoration: none; font-weight: 600; margin-top: 15px; cursor: pointer; border: none; font-size: 1rem; } .btn-primary { background: var(--primary); color: white; } .btn-danger { background: var(--danger); color: white; box-shadow: 0 4px 6px rgba(220, 38, 38, 0.2); animation: pulse 2s infinite; } .empty-state { text-align: center; padding: 40px; background: white; border-radius: 20px; border: 2px dashed #cbd5e1; } @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.7); } 70% { box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); } 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); } } @keyframes popIn { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }</style>"""

def credentials_to_dict(credentials):
    return {'token': credentials.token, 'refresh_token': credentials.refresh_token, 'token_uri': credentials.token_uri, 'client_id': credentials.client_id, 'client_secret': credentials.client_secret, 'scopes': credentials.scopes}

# --- LOGIQUE DE SCAN AUTOMATIQUE ---
def run_automated_scan():
    users = User.query.filter(User.refresh_token != None).all()
    results_summary = []
    for user in users:
        try:
            creds = Credentials(None, refresh_token=user.refresh_token, token_uri="https://oauth2.googleapis.com/token", client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET)
            creds.refresh(Request())
            service = build('gmail', 'v1', credentials=creds)
            query = "subject:(Remboursement OR Refund OR 'virement effectué') (Uber OR Amazon OR SNCF OR Air France)"
            msgs = service.users().messages().list(userId='me', q=query, maxResults=3).execute().get('messages', [])
            if msgs: results_summary.append(f"💰 Trouvé pour {user.email}")
            else: results_summary.append(f"✅ OK pour {user.email}")
        except Exception as e:
            results_summary.append(f"❌ Erreur {user.email}: {str(e)}")
    return results_summary

# --- ROUTES ---
@app.route("/cron-scan/<token>")
def cron_scan(token):
    if token != SCAN_TOKEN:
        return "Accès refusé", 403
    summary = run_automated_scan()
    return {"status": "success", "results": summary}

@app.route("/")
def index():
    if "credentials" in session:
        return STYLE + f"<div class='container'><h1>⚖️ JUSTICIO</h1><p class='subtitle'>Bonjour <strong>{session.get('name', 'Utilisateur')}</strong>.</p><div class='card' style='text-align:center; border: 2px solid var(--primary); background:white; border-left:none;'><h3 style='font-size: 1.5rem; margin-bottom: 10px;'>🛡️ Protection Active</h3><p style='color:#64748b;'>Scannez vos emails pour détecter les anomalies.</p><a href='/scan'><button class='btn btn-primary'>🚀 LANCER LE SCAN</button></a></div><div style='text-align:center; margin-top:30px;'><a href='/logout' style='color:#94a3b8; text-decoration:none;'>Déconnexion</a></div></div>"
    return redirect("/login")

# ... (Garde tes autres routes scan, auto_send, login, callback, logout identiques) ...
# Assure-toi juste de bien copier la nouvelle version de callback avec db.commit() vue précédemment.

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    try:
        credentials = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=credentials)
        query = "subject:(Uber OR Amazon OR SNCF OR Temu OR Facture OR Commande OR Problème)"
        messages = service.users().messages().list(userId='me', q=query, maxResults=15).execute().get('messages', [])
        # ... (Reste de ton code scan_emails actuel)
        return "Code de scan ici" # À remplacer par ton bloc complet

@app.route("/login")
def login():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
    session["state"] = state
    return redirect(auth_url)

@app.route("/callback")
def callback():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session["credentials"] = credentials_to_dict(creds)
    service = build('oauth2', 'v2', credentials=creds)
    info = service.userinfo().get().execute()
    user = User.query.filter_by(email=info['email']).first()
    if not user:
        user = User(email=info['email'], name=info.get('name'))
        db.session.add(user)
    if creds.refresh_token: user.refresh_token = creds.refresh_token
    db.session.commit()
    session["name"] = info.get('name')
    return redirect("/")

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
