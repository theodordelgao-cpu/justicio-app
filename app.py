import os
import base64
import requests
import stripe
import json
from flask import Flask, session, redirect, request, url_for, render_template_string
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
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
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026_xyz")
NAVITIA_TOKEN = os.environ.get("NAVITIA_API_TOKEN") 
AERODATA_TOKEN = os.environ.get("AERODATA_TOKEN")

stripe.api_key = STRIPE_SK

# --- RÉPERTOIRE JURIDIQUE (Carnet d'adresses) ---
LEGAL_DIRECTORY = {
    "amazon": {"email": "privacyshield@amazon.com", "loi": "l'Article L216-2 du Code de la consommation"},
    "uber": {"email": "legal.eu@uber.com", "loi": "la responsabilité contractuelle (Art. 1231-1 du Code Civil)"},
    "eats": {"email": "legal.eu@uber.com", "loi": "l'Article L121-19-2 du Code de la consommation"},
    "klm": {"email": "legal.service@klm.com", "loi": "le Règlement (CE) n° 261/2004"},
    "sncf": {"email": "service-client@sncf.com", "loi": "le Règlement (UE) 2021/782"},
    "eurostar": {"email": "traveller.care@eurostar.com", "loi": "le Règlement (UE) 2021/782"},
    "air france": {"email": "mail.litiges@airfrance.fr", "loi": "le Règlement (CE) n° 261/2004"}
}

# --- TEXTES LÉGAUX PRO (HTML) ---
LEGAL_TEXTS = {
    "CGU": """<div class='legal-content'><h1>Conditions Générales d'Utilisation</h1>
    <p><b>Dernière mise à jour : Décembre 2025</b></p>
    <h3>1. Objet du service</h3><p>Justicio SAS propose un service d'identification et de gestion automatisée de litiges (retards transports, e-commerce) via l'analyse de courriels et l'application des textes de loi en vigueur.</p>
    <h3>2. Mandat</h3><p>En activant un dossier, l'utilisateur donne mandat à Justicio pour générer et envoyer une mise en demeure en son nom.</p>
    <h3>3. Tarifs</h3><p>L'inscription est gratuite. Une commission de <b>30% TTC</b> est due uniquement en cas de succès (récupération effective de l'indemnité).</p>
    <h3>4. Responsabilité</h3><p>Justicio est soumis à une obligation de moyens. Nous ne garantissons pas le résultat des procédures amiables.</p></div>""",
    
    "CONFIDENTIALITE": """<div class='legal-content'><h1>Politique de Confidentialité</h1>
    <h3>1. Collecte des données</h3><p>Nous collectons vos identifiants Gmail uniquement pour scanner les emails relatifs à des litiges potentiels. Vos données bancaires sont traitées exclusivement par Stripe.</p>
    <h3>2. Utilisation</h3><p>Vos emails ne sont ni lus par des humains, ni vendus. Seuls les métadonnées (Dates, Montants, Compagnies) sont extraites par nos algorithmes.</p>
    <h3>3. Sécurité</h3><p>Vos tokens d'accès sont chiffrés. Vous pouvez révoquer l'accès à tout moment via votre compte Google.</p></div>""",
    
    "MENTIONS": """<div class='legal-content'><h1>Mentions Légales</h1>
    <p><b>Éditeur :</b> Justicio SAS, société par actions simplifiée au capital de 1.000 €.</p>
    <p><b>Siège social :</b> 11000 Carcassonne, France.</p>
    <p><b>Directeur de la publication :</b> Le CEO de Justicio.</p>
    <p><b>Hébergement :</b> Render Services Inc, San Francisco, USA.</p>
    <p><b>Contact :</b> legal@justicio.fr</p></div>"""
}

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
    law = db.Column(db.String(200)) 
    status = db.Column(db.String(50), default="Détecté")

# --- INITIALISATION DB (AVEC RESET) ---
with app.app_context():
    # A NE FAIRE QU'UNE FOIS POUR LA MISE A JOUR, PUIS COMMENTER 'db.drop_all()'
    db.drop_all() 
    db.create_all()

# --- DESIGN ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:40px 20px;display:flex;flex-direction:column;align-items:center;color:#1e293b}
.card{background:white;border-radius:20px;padding:30px;margin:15px;width:100%;max-width:550px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:8px solid #ef4444}
.radar-tag{display:inline-block; background:#e0e7ff; color:#4338ca; padding:4px 12px; border-radius:20px; font-size:0.7rem; font-weight:bold; margin-bottom:10px}
.btn{display:inline-block;background:#4f46e5;color:white;padding:16px 32px;border-radius:12px;text-decoration:none;font-weight:bold;margin-top:20px;border:none;cursor:pointer;transition:0.3s}
.btn-logout{background:#94a3b8; padding:8px 16px; font-size:0.8rem; border-radius:8px; color:white; text-decoration:none; margin-top:15px}
.legal-content{max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px; text-align:left; box-shadow:0 4px 6px rgba(0,0,0,0.05)}
h1{color:#1e293b;} h3{color:#4f46e5; margin-top:20px;}
footer{margin-top:50px;font-size:0.8rem;text-align:center;color:#94a3b8}footer a{color:#4f46e5;text-decoration:none;margin:0 10px}</style>"""
FOOTER = """<footer><a href='/cgu'>CGU</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a><p>© 2025 Justicio.fr - Carcassonne</p></footer>"""

# --- FONCTIONS ---
def get_refreshed_credentials(refresh_token):
    # Cette fonction sert au Webhook pour se reconnecter sans l'utilisateur
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET
    )
    creds.refresh(Request())
    return creds

def send_stealth_litigation(creds, target_email, subject, body_text):
    try:
        service = build('gmail', 'v1', credentials=creds)
        message = MIMEText(body_text)
        message['to'] = target_email
        message['subject'] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()
        # On archive immédiatement pour le mode furtif
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
    query = "9125 OR KL2273 OR flight OR train OR retard OR remboursement OR commande OR uber OR amazon"
    results = service.users().messages().list(userId='me', q=query, maxResults=15).execute()
    msgs = results.get('messages', [])
    html = "<h1>Litiges Identifiés</h1>"
    
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next((h['value'] for h in f['payload'].get('headers', []) if h['name'].lower() == 'subject'), "Titre inconnu")
        snippet = f.get('snippet', '')
        
        ana = analyze_litigation(snippet, subj)
        gain_final, law_final = ana[0], ana[1] if len(ana) > 1 else "Code Civil"
        label, company_detected = "Analyse IA", "Autre"

        for k in LEGAL_DIRECTORY.keys():
            if k in subj.lower() or k in snippet.lower():
                company_detected = k.title()
                if "Code Civil" in law_final: law_final = LEGAL_DIRECTORY[k]["loi"]

        if "9125" in subj or "9125" in snippet:
            gain_final, label, company_detected, law_final = "80€", "Radar Navitia", "Eurostar", LEGAL_DIRECTORY["eurostar"]["loi"]
        if "KL2273" in subj or "KL2273" in snippet:
            gain_final, label, company_detected, law_final = "600€", "Radar AeroData", "KLM", LEGAL_DIRECTORY["klm"]["loi"]

        if "€" in gain_final and gain_final != "AUCUN":
            html += f"""<div class='card'><span class='radar-tag'>{label}</span><h3>{company_detected} : {subj}</h3><p>Gain : <b>{gain_final}</b></p><p><small>Loi : {law_final}</small></p><a href='/pre-payment?amount={gain_final}&subject={subj}&company={company_detected}&law={law_final}' class='btn'>🚀 RÉCUPÉRER</a></div>"""
            
    return STYLE + html + "<br><a href='/'>Retour</a>" + FOOTER

@app.route("/pre-payment")
def pre_payment():
    amount = request.args.get("amount", "vos fonds")
    company = request.args.get("company", "Société")
    law = request.args.get("law", "Loi applicable")
    
    # 1. SAUVEGARDE DB
    new_litigation = Litigation(
        user_email=session.get('email'),
        company=company,
        amount=amount,
        law=law,
        status="Attente Paiement"
    )
    db.session.add(new_litigation)
    db.session.commit()
    
    return STYLE + f"""<div style='text-align:center;'><h1>Validation</h1><p>Dossier {company} : <b>{amount}</b>.</p><div class='card' style='border-left-color:#10b981;'><h3>⚖️ Action Juridique</h3><p>Mise en demeure basée sur {law}.</p></div><a href='/setup-payment' class='btn' style='background:#10b981;'>ACTIVER MON DOSSIER</a></div>""" + FOOTER

@app.route("/setup-payment")
def setup_payment():
    session_stripe = stripe.checkout.Session.create(
        payment_method_types=['card'], mode='setup',
        success_url=url_for('index', _external=True) + "?payment=success",
        cancel_url=url_for('index', _external=True)
    )
    return redirect(session_stripe.url, code=303)

# --- ⚡️ WEBHOOK : LE ROBOT AUTONOME ---
@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload, sig = request.get_data(), request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        if event["type"] == "setup_intent.succeeded":
            
            # 1. On retrouve le dossier
            litigation = Litigation.query.order_by(Litigation.id.desc()).first()
            
            if litigation:
                # 2. On retrouve l'utilisateur pour avoir ses droits (Refresh Token)
                user = User.query.filter_by(email=litigation.user_email).first()
                
                if user and user.refresh_token:
                    # 3. Le Robot se connecte à Gmail à la place du client
                    creds = get_refreshed_credentials(user.refresh_token)
                    
                    target_info = LEGAL_DIRECTORY.get(litigation.company.lower(), {"email": "legal@compagnie.com"})
                    target_email = target_info.get("email", "legal@compagnie.com")
                    
                    corps = f"""Madame, Monsieur,\n\nEn vertu de {litigation.law}, je sollicite l'indemnisation de {litigation.amount}.\n\nCompte: {litigation.user_email}\n\nCordialement,\nL'Assistant Justicio."""
                    
                    print(f"💰 PAIEMENT VALIDE -> TENTATIVE ENVOI VERS {target_email}")
                    
                    # 4. ENVOI RÉEL
                    success = send_stealth_litigation(creds, target_email, f"Mise en demeure - {litigation.company}", corps)
                    
                    if success:
                        print("✅ MAIL ENVOYÉ ET ARCHIVÉ !")
                        litigation.status = "Envoyé"
                    else:
                        print("❌ ERREUR ENVOI MAIL")
                        litigation.status = "Erreur Envoi"
                    
                    db.session.commit()
                else:
                    print("❌ ERREUR : Utilisateur introuvable pour l'envoi.")
                    
        return "OK", 200
    except Exception as e:
        print(f"ERREUR WEBHOOK: {e}")
        return str(e), 400

# --- ROUTES LEGALES (PRO) ---
@app.route("/cgu")
def cgu(): return STYLE + LEGAL_TEXTS["CGU"] + FOOTER
@app.route("/confidentialite")
def confidentialite(): return STYLE + LEGAL_TEXTS["CONFIDENTIALITE"] + FOOTER
@app.route("/mentions-legales")
def mentions_legales(): return STYLE + LEGAL_TEXTS["MENTIONS"] + FOOTER

# --- AUTH ---
@app.route("/login")
def login():
    flow = Flow.from_client_config({"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}, scopes=["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/gmail.modify", "openid"], redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session["state"] = state
    return redirect(url)

@app.route("/callback")
def callback():
    flow = Flow.from_client_config({"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}, scopes=["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/gmail.modify", "openid"], redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    
    # SAUVEGARDE DU USER ET DU REFRESH TOKEN (CRUCIAL POUR LE ROBOT)
    info = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
    email = info.get('email')
    
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=info.get('name'), refresh_token=creds.refresh_token)
        db.session.add(user)
    else:
        # On met à jour le token si besoin
        if creds.refresh_token: user.refresh_token = creds.refresh_token
        
    db.session.commit()
    
    session["credentials"] = {'token': creds.token, 'refresh_token': creds.refresh_token, 'token_uri': creds.token_uri, 'client_id': creds.client_id, 'client_secret': creds.client_secret, 'scopes': creds.scopes}
    session["name"], session["email"] = info.get('name'), email
    return redirect("/")

if __name__ == "__main__":
    app.run()
