import os
import base64
import requests
import stripe
from datetime import datetime
from flask import Flask, session, redirect, request, url_for, render_template_string
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURATION (Variables d'environnement Render) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_ultra_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AERODATABOX_KEY = os.environ.get("AERODATABOX_API_KEY") 
SNCF_TOKEN = os.environ.get("NAVITIA_API_TOKEN") 
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") 
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY") 
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026_xyz")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

stripe.api_key = STRIPE_SK

# --- BASE DE DONNÉES ---
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

# Configuration Google OAuth
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

# --- RADARS TECHNIQUES ---
def get_flight_status(flight_no, date_str):
    if not AERODATABOX_KEY: return "Radar Vol désactivé"
    url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_no.replace(' ', '')}/{date_str}"
    headers = {"X-RapidAPI-Key": AERODATABOX_KEY, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 204: return "Aucun vol trouvé (204)"
        data = r.json()
        delay = data[0].get('arrival', {}).get('delayMinutes', 0)
        return f"RETARD : {delay} min"
    except: return "Radar Vol Indisponible"

def get_train_status(train_no):
    if not SNCF_TOKEN: return "Radar SNCF désactivé"
    url = f"https://api.sncf.com/v1/coverage/sncf/disruptions/?q={train_no}"
    try:
        r = requests.get(url, auth=(SNCF_TOKEN, '')).json()
        return "Perturbation détectée" if r.get('disruptions') else "Circulation normale"
    except: return "Radar SNCF Indisponible"

# --- IA : ANALYSE ET RÉDACTION ---
def analyze_litigation(text, subject):
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"Analyse ce mail: {subject}. Contenu: {text[:500]}. Dis-moi si c'est un litige. Réponds: MONTANT | LOI | ENTREPRISE"
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content":prompt}])
        return res.choices[0].message.content.split("|")
    except: return ["0€", "Inconnue", "Inconnue"]

# --- ROUTES ---
STYLE = """<style>body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:20px;display:flex;flex-direction:column;align-items:center}.card{background:white;border-radius:15px;padding:20px;margin:10px;width:100%;max-width:500px;box-shadow:0 4px 6px rgba(0,0,0,0.1);border-left:5px solid #ef4444}.btn{display:block;background:#4f46e5;color:white;padding:12px;text-align:center;border-radius:8px;text-decoration:none;font-weight:bold;margin-top:10px}</style>"""

@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"<h1>⚖️ JUSTICIO</h1><p>Bienvenue {session.get('name')}</p><a href='/scan' class='btn'>🔍 SCANNER MES LITIGES</a>"

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    msgs = service.users().messages().list(userId='me', q="retard OR remboursement OR Amazon OR SNCF", maxResults=5).execute().get('messages', [])
    html = "<h1>Litiges détectés</h1>"
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next(h['value'] for h in f['payload']['headers'] if h['name'] == 'Subject')
        ana = analyze_litigation(f.get('snippet', ''), subj)
        html += f"<div class='card'><h3>{subj}</h3><p>Indemnité estimée : {ana[0]}</p><a href='/setup-payment' class='btn'>🚀 RÉCUPÉRER MES {ana[0]}</a></div>"
    return STYLE + html

@app.route("/setup-payment")
def setup_payment():
    session_stripe = stripe.checkout.Session.create(
        payment_method_types=['card'],
        mode='setup',
        success_url=url_for('payment_success', _external=True),
        cancel_url=url_for('index', _external=True)
    )
    return redirect(session_stripe.url, code=303)

@app.route("/payment-success")
def payment_success():
    # Ici, tu pourrais appeler ta fonction d'envoi de mail de mise en demeure
    return STYLE + "<h1>🛡️ PROTECTION ACTIVÉE</h1><p>Votre carte est enregistrée. La mise en demeure a été envoyée.</p><a href='/'>Retour</a>"

@app.route("/test-api")
def test_api():
    f = get_flight_status("AF123", "2025-12-22")
    t = get_train_status("8001")
    return f"Radars : Vol ({f}) | SNCF ({t})"

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
    session["name"] = info.get('name')
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
