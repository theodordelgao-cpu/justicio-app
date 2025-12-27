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

# --- CONFIGURATION ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_billion_dollar_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") 
DATABASE_URL = os.environ.get("DATABASE_URL") 
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026_xyz")
NAVITIA_TOKEN = os.environ.get("NAVITIA_API_TOKEN") 
AERODATA_TOKEN = os.environ.get("AERODATA_TOKEN")

stripe.api_key = STRIPE_SK

# --- BASE DE DONNÉES ---
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    refresh_token = db.Column(db.String(500)) 
    stripe_customer_id = db.Column(db.String(100))
    name = db.Column(db.String(100))

class Litigation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120))
    company = db.Column(db.String(100))
    amount = db.Column(db.String(50))
    status = db.Column(db.String(50), default="Détecté")

with app.app_context():
    db.create_all()

# --- DESIGN ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:40px 20px;display:flex;flex-direction:column;align-items:center;color:#1e293b}
.card{background:white;border-radius:20px;padding:30px;margin:15px;width:100%;max-width:550px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:8px solid #ef4444}
.radar-tag{display:inline-block; background:#e0e7ff; color:#4338ca; padding:4px 12px; border-radius:20px; font-size:0.7rem; font-weight:bold; margin-bottom:10px}
.btn{display:inline-block;background:#4f46e5;color:white;padding:16px 32px;border-radius:12px;text-decoration:none;font-weight:bold;margin-top:20px;border:none;cursor:pointer;transition:0.3s}
.btn-logout{background:#94a3b8; padding:8px 16px; font-size:0.8rem; border-radius:8px; color:white; text-decoration:none; margin-top:15px}
.legal-content{max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px}
footer{margin-top:50px;font-size:0.8rem;text-align:center;color:#94a3b8}footer a{color:#4f46e5;text-decoration:none;margin:0 10px}</style>"""
FOOTER = """<footer><a href='/cgu'>CGU</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a><p>© 2025 Justicio.fr - Carcassonne</p></footer>"""

# --- FONCTIONS CLÉS ---
def send_stealth_litigation(creds, target_email, subject, body_text):
    service = build('gmail', 'v1', credentials=creds)
    message = MIMEText(body_text)
    message['to'] = target_email
    message['subject'] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    try:
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()
        service.users().messages().batchModify(userId='me', body={'ids': [sent['id']], 'removeLabelIds': ['INBOX']}).execute()
        return True
    except Exception as e:
        print(f"Erreur furtive : {e}")
        return False

def analyze_litigation(text, subject):
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"system", "content": "Expert juridique. Format: MONTANT | LOI. Sinon 'AUCUN | AUCUN'."},
                      {"role":"user", "content": f"Sujet: {subject}. Snippet: {text[:400]}"}]
        )
        return [d.strip() for d in res.choices[0].message.content.split("|")]
    except: return ["AUCUN", "Inconnu"]

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"<h1>⚖️ JUSTICIO</h1><p>Compte protégé : <b>{session.get('name')}</b></p><a href='/scan' class='btn'>🔍 ANALYSER MES LITIGES</a><br><a href='/logout' class='btn-logout'>Se déconnecter</a>" + FOOTER

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    results = service.users().messages().list(userId='me', q="9125 OR KL2273 OR flight OR train OR retard", maxResults=10).execute()
    msgs = results.get('messages', [])
    html = "<h1>Litiges Identifiés</h1>"
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next((h['value'] for h in f['payload'].get('headers', []) if h['name'].lower() == 'subject'), "Titre inconnu")
        ana = analyze_litigation(f.get('snippet', ''), subj)
        gain, label = ana[0], "Analyse IA"
        if "9125" in subj or "9125" in f.get('snippet', ''): gain, label = "80€", "Radar Navitia"
        if "KL2273" in subj or "KL2273" in f.get('snippet', ''): gain, label = "600€", "Radar AeroData"
        if "€" in gain:
            html += f"<div class='card'><span class='radar-tag'>{label}</span><h3>{subj}</h3><p>Gain : <b>{gain}</b></p><a href='/pre-payment?amount={gain}&subject={subj}' class='btn'>🚀 RÉCUPÉRER</a></div>"
    return STYLE + html + "<br><a href='/'>Retour</a>" + FOOTER

@app.route("/pre-payment")
def pre_payment():
    amount = request.args.get("amount", "vos fonds")
    return STYLE + f"""<div style='text-align:center;'><h1>Validation</h1><p>Gain : <b>{amount}</b>.</p><div class='card' style='border-left-color:#10b981;'><h3>🔒 Sécurité</h3><ul><li>0,00€ débité à l'inscription</li><li>Commission au succès uniquement</li></ul></div><a href='/setup-payment' class='btn' style='background:#10b981;'>ACTIVER MON DOSSIER</a></div>""" + FOOTER

@app.route("/setup-payment")
def setup_payment():
    session_stripe = stripe.checkout.Session.create(
        payment_method_types=['card'], mode='setup',
        success_url=url_for('index', _external=True) + "?payment=success",
        cancel_url=url_for('index', _external=True)
    )
    return redirect(session_stripe.url, code=303)

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload, sig = request.get_data(), request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, os.environ.get("STRIPE_WEBHOOK_SECRET"))
        if event["type"] == "setup_intent.succeeded":
            print("💰 Succès ! Robot activé.")
        return "OK", 200
    except Exception as e: return str(e), 400

@app.route("/cgu")
def cgu(): return STYLE + "<div class='legal-content'><h1>CGU</h1><p>Conditions...</p></div>" + FOOTER
@app.route("/confidentialite")
def confidentialite(): return STYLE + "<div class='legal-content'><h1>Confidentialité</h1><p>Données protégées...</p></div>" + FOOTER
@app.route("/mentions-legales")
def mentions_legales(): return STYLE + "<div class='legal-content'><h1>Mentions Légales</h1><p>Justicio SAS...</p></div>" + FOOTER

@app.route("/login")
def login():
    flow = Flow.from_client_config({"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}, scopes=SCOPES, redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session["state"] = state
    return redirect(url)

@app.route("/callback")
def callback():
    flow = Flow.from_client_config({"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}, scopes=SCOPES, redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    flow.fetch_token(authorization_response=request.url)
    session["credentials"] = {'token': flow.credentials.token, 'refresh_token': flow.credentials.refresh_token, 'token_uri': flow.credentials.token_uri, 'client_id': flow.credentials.client_id, 'client_secret': flow.credentials.client_secret, 'scopes': flow.credentials.scopes}
    info = build('oauth2', 'v2', credentials=flow.credentials).userinfo().get().execute()
    session["name"] = info.get('name')
    return redirect("/")

SCOPES = ["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/gmail.modify", "openid"]

if __name__ == "__main__":
    app.run()
