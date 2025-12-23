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
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURATION DES CLÉS (Variables d'environnement Render) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_ultra_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AERODATABOX_KEY = os.environ.get("AERODATABOX_API_KEY") #
SNCF_TOKEN = os.environ.get("NAVITIA_API_TOKEN") #
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") # Ta clé manquante à mettre sur Render plus tard
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY") #
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026_xyz") #

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

# --- RADARS TECHNIQUES (APIs) ---
def get_flight_status(flight_no, date_str):
    if not AERODATABOX_KEY: return "Radar Vol désactivé"
    url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_no.replace(' ', '')}/{date_str}"
    headers = {"X-RapidAPI-Key": AERODATABOX_KEY, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"}
    try:
        r = requests.get(url, headers=headers).json()
        delay = r[0].get('arrival', {}).get('delayMinutes', 0)
        return f"RETARD CONFIRMÉ : {delay} min" if delay > 180 else f"Retard mineur ({delay} min)"
    except: return "Données vol indisponibles"

def get_train_status(train_no):
    if not SNCF_TOKEN: return "Radar SNCF désactivé"
    url = f"https://api.sncf.com/v1/coverage/sncf/disruptions/?q={train_no}"
    try:
        r = requests.get(url, auth=(SNCF_TOKEN, '')).json()
        return "Perturbation détectée" if r.get('disruptions') else "Circulation normale"
    except: return "Erreur radar SNCF"

# --- CERVEAU IA ---
def analyze_with_ai(text, subject, sender):
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"Analyse ce mail de {sender}: {subject}. Snippet: {text[:400]}. Calcule l'indemnité (Reg 261/2004, G30 ou L216-1). Réponds format: MONTANT | LOI | RISQUE(DANGER/SAFE)"
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content":prompt}], max_tokens=60)
        p = res.choices[0].message.content.strip().split("|")
        return {"amount": p[0], "status": p[1], "color": "red" if "DANGER" in p[2] else "green"}
    except: return {"amount": "À vérifier", "status": "Litige détecté", "color": "red"}

# --- ROUTES PRINCIPALES ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap'); body { font-family: 'Outfit', sans-serif; background: #f1f5f9; padding: 20px; display: flex; flex-direction: column; align-items: center; } .card { background: white; border-radius: 15px; padding: 20px; margin: 10px; width: 100%; max-width: 500px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); border-left: 5px solid #ef4444; } .btn { display: block; background: #4f46e5; color: white; padding: 12px; text-align: center; border-radius: 8px; text-decoration: none; font-weight: bold; margin-top: 10px; border: none; cursor: pointer; } .badge { background: #fee2e2; color: #b91c1c; padding: 4px 8px; border-radius: 5px; font-size: 0.8rem; font-weight: bold; }</style>"""

@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    msg = request.args.get("payment")
    banner = "<p style='color:green;'>✅ Carte enregistrée avec succès !</p>" if msg == "success" else ""
    return STYLE + f"<div style='text-align:center;'>{banner}<h1>⚖️ JUSTICIO</h1><p>Bonjour {session.get('name')}</p><a href='/scan' class='btn'>🔍 SCANNER MES LITIGES</a><br><a href='/logout'>Déconnexion</a></div>"

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    msgs = service.users().messages().list(userId='me', q="Uber OR Amazon OR SNCF OR Flight", maxResults=5).execute().get('messages', [])
    html = f"<h1>Résultats du Scan</h1>"
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next(h['value'] for h in f['payload']['headers'] if h['name'] == 'Subject')
        send = next(h['value'] for h in f['payload']['headers'] if h['name'] == 'From')
        ana = analyze_with_ai(f.get('snippet', ''), subj, send)
        if ana['color'] == "red":
            html += f"<div class='card'><h3>{subj}</h3><p>{send}</p><span class='badge'>💰 {ana['amount']}</span> <span class='badge'>⚖️ {ana['status']}</span><a href='/setup-payment' class='btn'>🚀 ACTIVER LA PROTECTION (0€)</a></div>"
    return STYLE + html + "<a href='/'>Retour</a>"

# --- STRIPE (PAIEMENT À LA PERFORMANCE) ---
@app.route("/setup-payment")
def setup_payment():
    if "credentials" not in session: return redirect("/login")
    # Création d'une session de 'Setup' pour enregistrer la carte sans débiter
    session_stripe = stripe.checkout.Session.create(
        payment_method_types=['card'],
        mode='setup',
        success_url=url_for('index', _external=True) + "?payment=success",
        cancel_url=url_for('index', _external=True) + "?payment=cancel",
    )
    return redirect(session_stripe.url, code=303)

# --- VÉRIFICATION API ---
@app.route("/test-api")
def test_api():
    f = get_flight_status("AF123", "2025-12-22")
    t = get_train_status("8001")
    return f"<h2>Test des Radars</h2><p>Vol: {f}</p><p>SNCF: {t}</p>"

# --- AUTHENTIFICATION GOOGLE ---
# (Garder tes fonctions login / callback / logout habituelles ici)
# ... [Code OAuth identique au précédent] ...

@app.route("/cron-scan/<token>")
def cron_scan(token):
    if token != SCAN_TOKEN: return "Interdit", 403
    return "Scan automatique en cours..."

if __name__ == "__main__":
    app.run(debug=True)
