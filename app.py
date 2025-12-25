import os
import stripe
import base64
from flask import Flask, session, redirect, request, url_for
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
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") # Ta clé sk_live
DATABASE_URL = os.environ.get("DATABASE_URL") #
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026_xyz")

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
body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:40px 20px;display:flex;flex-direction:column;align-items:center}
.card{background:white;border-radius:20px;padding:30px;margin:15px;width:100%;max-width:550px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:8px solid #ef4444}
.btn{display:inline-block;background:#4f46e5;color:white;padding:16px 32px;border-radius:12px;text-decoration:none;font-weight:bold;margin-top:20px;border:none;cursor:pointer}
footer{margin-top:50px;font-size:0.8rem;text-align:center;color:#94a3b8}footer a{color:#4f46e5;text-decoration:none;margin:0 10px}</style>"""

FOOTER = """<footer><a href='/cgu'>CGU</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a><p>© 2025 Justicio.fr - Carcassonne</p></footer>"""

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"<h1>⚖️ JUSTICIO</h1><p>Compte de versement : <b>{session.get('name')}</b></p><a href='/scan' class='btn'>🔍 SCANNER MES LITIGES</a>" + FOOTER

@app.route("/pre-payment")
def pre_payment():
    """Page de réassurance ultra-professionnelle"""
    amount = request.args.get("amount", "vos fonds")
    return STYLE + f"""
    <div style='max-width:600px; text-align:center;'>
        <div style='font-size: 3.5rem;'>🏦</div>
        <h1>Validation de versement</h1>
        <p>Nous avons identifié une créance de <b>{amount}</b> en votre faveur.</p>
        <div class='card' style='text-align:left; border-left-color:#10b981; background:#f0fdf4;'>
            <h3 style='color:#166534; margin-top:0;'>🔒 Pourquoi sécuriser votre compte ?</h3>
            <ul style='color:#166534; padding-left:20px; line-height:1.6;'>
                <li><b>Vérification d'identité :</b> Pour valider que vous êtes bien le titulaire du compte de remboursement.</li>
                <li><b>Empreinte de 0,00€ :</b> Aucune somme n'est prélevée. Cela active simplement votre dossier juridique.</li>
                <li><b>Zéro frais cachés :</b> Notre commission de 30% est déduite <u>uniquement</u> au succès.</li>
            </ul>
        </div>
        <a href='/setup-payment' class='btn' style='background:#10b981;'>ACTIVER MON DOSSIER & RECEVOIR {amount}</a>
    </div>
    """ + FOOTER

@app.route("/setup-payment")
def setup_payment():
    session_stripe = stripe.checkout.Session.create(
        payment_method_types=['card'], mode='setup',
        success_url=url_for('index', _external=True) + "?payment=success",
        cancel_url=url_for('index', _external=True)
    )
    return redirect(session_stripe.url, code=303)

# --- ROUTES LÉGALES (Pour Stripe) ---
@app.route("/cgu")
def cgu(): return STYLE + "<h1>CGU</h1><p>Commission de 30% perçue au succès uniquement.</p><a href='/'>Retour</a>"

# ... (Garder les routes login / callback / scan habituelles)

if __name__ == "__main__":
    app.run(debug=True)
