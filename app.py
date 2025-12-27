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

# --- CONFIGURATION (Render Environment Variables) ---
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

# --- DESIGN & LÉGAL ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:40px 20px;display:flex;flex-direction:column;align-items:center;color:#1e293b}
.card{background:white;border-radius:20px;padding:30px;margin:15px;width:100%;max-width:550px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:8px solid #ef4444}
.btn{display:inline-block;background:#4f46e5;color:white;padding:16px 32px;border-radius:12px;text-decoration:none;font-weight:bold;margin-top:20px;border:none;cursor:pointer;transition:0.3s}
.btn:hover{background:#3730a3;transform:translateY(-2px)}
.legal-content{max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px; box-shadow:0 4px 6px rgba(0,0,0,0.05)}
footer{margin-top:50px;font-size:0.8rem;text-align:center;color:#94a3b8}footer a{color:#4f46e5;text-decoration:none;margin:0 10px}</style>"""
FOOTER = """<footer><a href='/cgu'>CGU</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a><p>© 2025 Justicio.fr - Carcassonne</p></footer>"""

# --- 🕵️ FONCTION D'ENVOI FURTIVE ---
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

# --- IA DE DÉTECTION (Strict) ---
def analyze_litigation(text, subject):
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"system", "content": "Expert juridique. Si PAS de litige transport/consommation clair, réponds 'AUCUN | AUCUN'. Sinon, format: MONTANT | LOI."},
                      {"role":"user", "content": f"Sujet: {subject}. Contenu: {text[:400]}"}]
        )
        return [d.strip() for d in res.choices[0].message.content.split("|")]
    except: return ["À calculer", "Code Civil"]

# --- ROUTES PRINCIPALES ---
@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"<h1>⚖️ JUSTICIO</h1><p>Compte protégé : <b>{session.get('name')}</b></p><a href='/scan' class='btn'>🔍 ANALYSER MES LITIGES</a>" + FOOTER

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    try:
        creds = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=creds)
        query = "flight delay OR 'votre train' OR 'indemnisation' OR 'KL2273' OR 'vol retardé'"
        results = service.users().messages().list(userId='me', q=query, maxResults=8).execute()
        msgs = results.get('messages', [])
        html = "<h1>Litiges Identifiés</h1>"
        if not msgs: html += "<p>Aucun litige trouvé. Envoyez-vous un mail de test !</p>"
        for m in msgs:
            f = service.users().messages().get(userId='me', id=m['id']).execute()
            subj = next((h['value'] for h in f['payload'].get('headers', []) if h['name'].lower() == 'subject'), "Titre inconnu")
            ana = analyze_litigation(f.get('snippet', ''), subj)
            if "€" in ana[0]:
                html += f"<div class='card'><h3>{subj}</h3><p>Gain estimé : <b>{ana[0]}</b></p><a href='/pre-payment?amount={ana[0]}&subject={subj}' class='btn'>🚀 RÉCUPÉRER</a></div>"
        return STYLE + html + "<br><a href='/'>Retour</a>" + FOOTER
    except Exception as e: return f"Erreur de scan : {str(e)}"

@app.route("/pre-payment")
def pre_payment():
    amount = request.args.get("amount", "vos fonds")
    return STYLE + f"""<div style='text-align:center;'><h1>Validation</h1><p>Gain identifié : <b>{amount}</b>.</p><div class='card' style='border-left-color:#10b981; background:#f0fdf4;'><h3>🔒 Sécurité bancaire</h3><ul style='text-align:left;'><li>0,00€ débité à l'inscription.</li><li>Commission de 30% uniquement au succès.</li></ul></div><a href='/setup-payment' class='btn' style='background:#10b981;'>ACTIVER MON DOSSIER</a></div>""" + FOOTER

@app.route("/setup-payment")
def setup_payment():
    session_stripe = stripe.checkout.Session.create(
        payment_method_types=['card'], mode='setup',
        success_url=url_for('index', _external=True) + "?payment=success",
        cancel_url=url_for('index', _external=True)
    )
    return redirect(session_stripe.url, code=303)

# --- 🛡️ ROUTES LÉGALES (Nouveau !) ---
@app.route("/cgu")
def cgu():
    return STYLE + "<div class='legal-content'><h1>Conditions Générales d'Utilisation</h1><p>Justicio.fr automatise la détection de litiges de transport. En utilisant ce service, vous acceptez que notre robot scanne et archive vos mails de réclamation...</p></div>" + FOOTER

@app.route("/confidentialite")
def confidentialite():
    return STYLE + "<div class='legal-content'><h1>Politique de Confidentialité</h1><p>Nous utilisons l'API Google pour identifier vos litiges. Aucune donnée n'est vendue à des tiers. Vos emails sont traités de manière furtive pour votre confort...</p></div>" + FOOTER

@app.route("/mentions-legales")
def mentions_legales():
    return STYLE + "<div class='legal-content'><h1>Mentions Légales</h1><p><b>Éditeur :</b> Justicio SAS, Carcassonne.<br><b>Hébergeur :</b> Render.com<br><b>Directeur :</b> Théo.</p></div>" + FOOTER

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = os.environ.get("STRIPE_WEBHOOK_SECRET") 
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        if event["type"] == "setup_intent.succeeded":
            print(f"💰 Carte validée ! Le robot Justicio est prêt.")
        return "OK", 200
    except Exception as e: return str(e), 400

# --- AUTH ---
SCOPES = ["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/gmail.modify", "openid"]

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

if __name__ == "__main__":
    app.run()
