import os
import base64
import random
import requests
from flask import Flask, session, redirect, request, url_for
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURATION DES SECRETS ---
app.secret_key = os.environ.get("SECRET_KEY", "azerty_super_secret_key")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AERODATABOX_KEY = os.environ.get("AERODATABOX_API_KEY")
NAVITIA_TOKEN = os.environ.get("NAVITIA_API_TOKEN")
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026")

# --- CONFIGURATION BASE DE DONNÉES ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

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

SCOPES = ["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose", "openid"]

# --- DÉTECTIVES TECHNIQUES (APIs) ---
def get_flight_status(flight_no, date):
    """Vérifie le retard réel d'un vol via AeroDataBox"""
    if not AERODATABOX_KEY: return "Vérification impossible (Clé manquante)"
    url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_no}/{date}"
    headers = {"X-RapidAPI-Key": AERODATABOX_KEY, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"}
    try:
        resp = requests.get(url, headers=headers).json()
        delay = resp[0].get('arrival', {}).get('delayMinutes', 0)
        return f"RETARD CONFIRMÉ : {delay} min" if delay > 0 else "Vol à l'heure selon les relevés"
    except: return "Données de vol non trouvées"

def get_train_status(train_no):
    """Vérifie les perturbations SNCF via Navitia"""
    if not NAVITIA_TOKEN: return "Vérification train impossible"
    url = f"https://api.navitia.io/v1/coverage/fr/disruptions/?q={train_no}"
    try:
        resp = requests.get(url, auth=(NAVITIA_TOKEN, '')).json()
        return "Perturbation SNCF détectée sur ce trajet" if resp.get('disruptions') else "Pas de perturbation majeure"
    except: return "Erreur radar SNCF"

# --- CERVEAU JURIDIQUE IA ---
def analyze_with_ai(text, subject, sender):
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""Analyse ce mail: {subject}. Contenu: {text[:600]}.
    Calcule le cash à récupérer :
    - VOL: Retard >3h = 250€/400€/600€ (Reg 261/2004).
    - TRAIN: G30 (25% à 75% du billet).
    - AMAZON/UBER: Art L216-1 (Remboursement total).
    Réponds: MONTANT | LOI APPLICABLE | RISQUE(DANGER/SAFE)"""
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content":prompt}], max_tokens=60)
        p = res.choices[0].message.content.strip().split("|")
        return {"amount": p[0], "status": p[1], "color": "red" if "DANGER" in p[2] else "green"}
    except: return {"amount": "?", "status": "Litige possible", "color": "red"}

def generate_agency_email(text, subject, sender, user_name, proof=""):
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""Rédige une MISE EN DEMEURE AGRESSIVE de JUSTICIO pour {user_name} contre {sender}.
    Preuve technique: {proof}. Sujet: {subject}. 
    Cite les lois: Reg 261/2004 (Avion), G30 (Train), Art L216-1 (Amazon/Uber Eats).
    Signe: SERVICE CONTENTIEUX JUSTICIO. Pas de Markdown."""
    res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content":prompt}], max_tokens=800)
    return res.choices[0].message.content.strip()

# --- DESIGN & ROUTES ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap'); :root { --primary: #4f46e5; --danger: #dc2626; --bg: #f8fafc; } body { font-family: 'Outfit', sans-serif; background-color: var(--bg); margin: 0; padding: 40px 20px; color: #0f172a; display: flex; flex-direction: column; align-items: center; min-height: 100vh; } .container { width: 100%; max-width: 650px; } h1 { font-weight: 800; font-size: 2.5rem; text-align: center; color: var(--primary); } .card { background: white; border-radius: 20px; padding: 25px; margin-bottom: 20px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border-left: 8px solid var(--danger); background: #fff1f2; } .badge { display: inline-block; padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; font-weight: 600; background: white; border: 1px solid #fca5a5; color: #991b1b; } .btn { display: block; width: 100%; padding: 15px; border-radius: 12px; text-align: center; text-decoration: none; font-weight: 600; cursor: pointer; border: none; font-size: 1rem; } .btn-primary { background: var(--primary); color: white; } .btn-danger { background: var(--danger); color: white; }</style>"""

@app.route("/")
def index():
    if "credentials" in session:
        return STYLE + f"<div class='container'><h1>⚖️ JUSTICIO</h1><p style='text-align:center;'>Bonjour {session.get('name')}.</p><div class='card' style='background:white; border-left: 2px solid var(--primary);'><h3 style='text-align:center;'>🛡️ Protection Active</h3><a href='/scan'><button class='btn btn-primary'>🚀 LANCER LE SCAN</button></a></div><div style='text-align:center;'><a href='/logout'>Déconnexion</a></div></div>"
    return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    q = "subject:(Uber OR Amazon OR SNCF OR Air France OR Retard OR Problème)"
    msgs = service.users().messages().list(userId='me', q=q, maxResults=10).execute().get('messages', [])
    html = ""
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next(h['value'] for h in f['payload']['headers'] if h['name'] == 'Subject')
        send = next(h['value'] for h in f['payload']['headers'] if h['name'] == 'From')
        ana = analyze_with_ai(f.get('snippet', ''), subj, send)
        if ana['color'] == "red":
            html += f"<div class='card'><h3>{subj}</h3><p>{send}</p><span class='badge'>💰 {ana['amount']}</span> <span class='badge'>⚖️ {ana['status']}</span><a href='/auto_send/{m['id']}'><button class='btn btn-danger' style='margin-top:10px;'>⚡ RÉCLAMER</button></a></div>"
    return STYLE + f"<div class='container'><h1>📂 Résultats</h1>{html or '<p>Rien trouvé</p>'}</div>"

@app.route("/auto_send/<msg_id>")
def auto_send(msg_id):
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    f = service.users().messages().get(userId='me', id=msg_id).execute()
    subj = next(h['value'] for h in f['payload']['headers'] if h['name'] == 'Subject')
    send = next(h['value'] for h in f['payload']['headers'] if h['name'] == 'From')
    # Détection simplifiée pour le démo (Idéalement extraire via IA)
    proof = get_flight_status("AF123", "2025-12-22") if "Air France" in send else ""
    body = generate_agency_email(f.get('snippet', ''), subj, send, session.get('name'), proof)
    msg = MIMEText(body); msg['to'] = send; msg['from'] = "me"; msg['subject'] = f"MISE EN DEMEURE : {subj}"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={'raw': raw}).execute()
    return "<h1>Succès ! Mise en demeure envoyée.</h1><a href='/'>Retour</a>"

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
    u = User.query.filter_by(email=info['email']).first()
    if not u: u = User(email=info['email'], name=info.get('name')); db.session.add(u)
    if creds.refresh_token: u.refresh_token = creds.refresh_token
    db.session.commit()
    session["name"] = info.get('name')
    return redirect("/")

@app.route("/cron-scan/<token>")
def cron_scan(token):
    if token != SCAN_TOKEN: return "Refusé", 403
    users = User.query.filter(User.refresh_token != None).all()
    for u in users:
        print(f"Scan auto pour {u.email}...")
    return "Scan terminé"

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

if __name__ == "__main__": app.run(debug=True)
