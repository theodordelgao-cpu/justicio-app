import os
import stripe
from flask import Flask, session, redirect, request, url_for, render_template_string
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURATION (Render Environment Variables) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_billion_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL") # Ton PostgreSQL
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

stripe.api_key = STRIPE_SK

# --- BASE DE DONNÉES (Double usage : Tokens + Litiges) ---
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
db = SQLAlchemy(app)

class User(db.Model):
    """Table pour le scan des 12h : stocke les accès Gmail"""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    refresh_token = db.Column(db.String(500)) # Clé pour le robot
    name = db.Column(db.String(100))

class Litigation(db.Model):
    """Table pour ton business : suit l'argent et les dossiers"""
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120))
    company = db.Column(db.String(100))
    amount = db.Column(db.String(50))
    date_found = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default="Détecté") # Détecté / Carte Enregistrée / Envoyé

with app.app_context():
    db.create_all()

# --- DESIGN & PAGES LÉGALES ---
STYLE = """<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body { font-family: 'Outfit', sans-serif; background: #f8fafc; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; }
.card { background: white; border-radius: 20px; padding: 30px; margin: 15px; width: 100%; max-width: 550px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border-left: 8px solid #ef4444; }
.btn { background: #4f46e5; color: white; padding: 16px 32px; border-radius: 12px; text-decoration: none; font-weight: bold; display: inline-block; margin-top: 20px; border:none; cursor:pointer; }
footer { margin-top: 50px; font-size: 0.8rem; color: #94a3b8; text-align:center; }
footer a { color: #4f46e5; text-decoration: none; margin: 0 10px; }
</style>"""

FOOTER = """<footer>
    <a href='/cgu'>Conditions Générales</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a>
    <p>© 2025 Justicio.fr - Carcassonne</p>
</footer>"""

@app.route("/cgu")
def cgu():
    return STYLE + "<h1>Conditions Générales de Vente</h1><p>Justicio prélève une commission de 30% uniquement en cas de succès du remboursement. Aucun frais n'est dû en cas d'échec.</p><a href='/'>Retour</a>"

@app.route("/confidentialite")
def confidentialite():
    return STYLE + "<h1>Politique de Confidentialité</h1><p>Nous utilisons vos accès Gmail uniquement pour identifier des litiges de consommation. Vos données ne sont jamais vendues.</p><a href='/'>Retour</a>"

@app.route("/mentions-legales")
def mentions_legales():
    return STYLE + "<h1>Mentions Légales</h1><p>Éditeur : Justicio. Siège social : 5 rue peire cardenal, 11000 Carcassonne.</p><a href='/'>Retour</a>"

# --- LOGIQUE DE SCAN ---
@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    # Simulation du scan pour l'exemple (ton code Gmail actuel s'insère ici)
    # On enregistre le litige dans la DB s'il est nouveau
    return STYLE + "<h1>Litiges Identifiés</h1>" + f"<div class='card'><h3>Exemple Amazon</h3><p>Gain : 89,99€</p><a href='/pre-payment?amount=89.99€&subject=Amazon' class='btn'>Récupérer</a></div>" + FOOTER

# --- OAUTH : SAUVEGARDE DES TOKENS POUR LE ROBOT ---
@app.route("/callback")
def callback():
    # ... (code flow.fetch_token habituel) ...
    creds = flow.credentials
    info = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
    
    # SAUVEGARDE DU REFRESH TOKEN DANS LA DB
    user = User.query.filter_by(email=info['email']).first()
    if not user:
        user = User(email=info['email'], name=info.get('name'))
        db.session.add(user)
    
    if creds.refresh_token:
        user.refresh_token = creds.refresh_token # C'est ça qui permet le scan des 12h !
    
    db.session.commit()
    session["name"] = info.get('name')
    return redirect("/")

# --- LE SCAN AUTOMATIQUE (CRON) ---
@app.route("/cron-scan/<token>")
def cron_scan(token):
    if token != os.environ.get("SCAN_TOKEN"): return "Interdit", 403
    users = User.query.all()
    for u in users:
        # Ici ton robot utilise u.refresh_token pour scanner sans que l'user soit là
        print(f"Scan en cours pour {u.email}...")
    return "Scan terminé"

# ... (Routes login / logout / setup-payment identiques) ...
