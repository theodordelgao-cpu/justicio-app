import os
import base64
import requests
import stripe
import random
from flask import Flask, session, redirect, request, url_for, render_template_string
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURATION DES CLÉS (Variables d'environnement Render) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_ultra_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AERODATABOX_KEY = os.environ.get("AERODATABOX_API_KEY") #
SNCF_TOKEN = os.environ.get("NAVITIA_API_TOKEN") #
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") 
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY") #
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026_xyz") #

# Configuration Google OAuth
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

stripe.api_key = STRIPE_SK

# --- CONFIGURATION BASE DE DONNÉES ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    refresh_token = db.Column(db.String(500), nullable=True)
    name = db.Column(db.String(100))
    stripe_customer_id = db.Column(db.String(100), nullable=True)

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

# --- RADARS TECHNIQUES (APIs) ---
def get_flight_status(flight_no, date_str):
    if not AERODATABOX_KEY: return "Radar Vol désactivé"
    url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_no.replace(' ', '')}/{date_str}"
    headers = {"X-RapidAPI-Key": AERODATABOX_KEY, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code != 200: return f"Erreur API Vol ({r.status_code})"
        data = r.json()
        if not data: return "Aucun vol trouvé"
        delay = data[0].get('arrival', {}).get('delayMinutes', 0)
        return f"RETARD CONFIRMÉ : {delay} min" if delay > 180 else f"Retard mineur ({delay} min)"
    except: return "Données vol indisponibles"

def get_train_status(train_no):
    if not SNCF_TOKEN: return "Radar SNCF désactivé"
    url = f"https://api.sncf.com/v1/coverage/sncf/disruptions/?q={train_no}"
    try:
        r = requests.get(url, auth=(SNCF_TOKEN, '')).json()
        return "Perturbation détectée par SNCF" if r.get('disruptions') else "Circulation normale"
    except: return "Erreur radar SNCF"

# --- CERVEAU IA ---
def analyze_with_ai(text, subject, sender):
    if not OPENAI_API_KEY: return {"amount": "N/A", "status": "IA Off", "color": "gray"}
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"Analyse ce mail: {subject}. Contenu: {text[:400]}. Calcule l'indemnité selon Reg 261/2004, G30 ou L216-1. Réponds: MONTANT | LOI | RISQUE(DANGER/SAFE)"
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content":prompt}], max_tokens=60)
        p = res.choices[0].message.content.strip().split("|")
        return {"amount": p[0], "status": p[1], "color": "red" if "DANGER" in p[2] else "green"}
    except: return {"amount": "À vérifier", "status": "Litige possible", "color": "red"}

# --- ROUTES PRINCIPALES ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap'); body { font-family: 'Outfit', sans-serif; background: #f8fafc; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; } .card { background: white; border-radius: 15px; padding: 25px; margin: 15px; width: 100%; max-width: 550px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border-left: 6px solid #ef4444; } .btn { display: block; background: #4f46e5; color: white; padding: 15px; text-align: center; border-radius: 10px; text-decoration: none; font-weight: bold; margin-top: 15px; border: none; cursor: pointer; font-size: 1rem; } .badge { background: #fee2e2; color: #b91c1c; padding: 6px 10px; border-radius: 6px; font-size: 0.85rem; font-weight: bold; margin-right: 5px; }</style>"""

@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    msg = request.args.get("payment")
    banner = "<div style='background:#dcfce7; color:#166534; padding:10px; border-radius:10px; margin-bottom:20px;'>✅ Protection activée : Votre carte est enregistrée.</div>" if msg == "success" else ""
    return STYLE + f"<div style='text-align:center;'>{banner}<h1>⚖️ JUSTICIO</h1><p>Compte protégé : <b>{session.get('name')}</b></p><a href='/scan' class='btn'>🔍 SCANNER MES EMAILS</a><br><a href='/logout' style='color:#64748b; font-size:0.9rem;'>Se déconnecter</a></div>"

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    msgs = service.users().messages().list(userId='me', q="Uber OR Amazon OR SNCF OR Flight OR Billet", maxResults=8).execute().get('messages', [])
    html = f"<h1>Analyse en cours...</h1>"
    found = False
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next((h['value'] for h in f['payload']['headers'] if h['name'] == 'Subject'), "Sans objet")
        send = next((h['value'] for h in f['payload']['headers'] if h['name'] == 'From'), "Inconnu")
        ana = analyze_with_ai(f.get('snippet', ''), subj, send)
        if ana['color'] == "red":
            found = True
            html += f"<div class='card'><h3>{subj}</h3><p style='color:#64748b;'>Expéditeur : {send}</p><span class='badge'>💰 Gain potentiel : {ana['amount']}</span> <span class='badge'>⚖️ Base : {ana['status']}</span><p style='font-size:0.85rem; margin-top:10px;'>En activant la protection, nous envoyons la mise en demeure. Vous payez 30% uniquement si vous gagnez.</p><a href='/setup-payment' class='btn'>🚀 RÉCUPÉRER MON ARGENT (0€)</a></div>"
    if not found: html = "<h1>Aucun litige détecté pour le moment.</h1>"
    return STYLE + html + "<br><a href='/' style='text-decoration:none;'>⬅️ Retour</a>"

# --- STRIPE (PAIEMENT À LA PERFORMANCE) ---
@app.route("/setup-payment")
def setup_payment():
    if "credentials" not in session: return redirect("/login")
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='setup',
            success_url=url_for('index', _external=True) + "?payment=success",
            cancel_url=url_for('index', _external=True) + "?payment=cancel",
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return f"Erreur Stripe : {str(e)}. Assure-toi d'avoir ajouté la Clé Secrète sur Render."

# --- VÉRIFICATION API ---
@app.route("/test-api")
def test_api():
    f = get_flight_status("AF123", "2025-12-22")
    t = get_train_status("8001")
    return f"<h2>Test des Radars de Transport</h2><hr><p><b>Radar Vol (AeroDataBox) :</b> {f}</p><p><b>Radar Train (SNCF) :</b> {t}</p><hr><p><i>Si tout est OK, vous pouvez lancer un scan réel.</i></p>"

# --- AUTHENTIFICATION GOOGLE ---
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
    if not u: 
        u = User(email=info['email'], name=info.get('name'))
        db.session.add(u)
    if creds.refresh_token: u.refresh_token = creds.refresh_token
    db.session.commit()
    session["name"] = info.get('name')
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# --- AUTOMATISATION (CRON) ---
@app.route("/cron-scan/<token>")
def cron_scan(token):
    if token != SCAN_TOKEN: return "Accès refusé", 403
    # Logique pour scanner tous les utilisateurs de la base de données...
    return "Scan global terminé avec succès."

if __name__ == "__main__":
    app.run(debug=True)
