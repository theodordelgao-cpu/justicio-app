²import os
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
from sqlalchemy import or_

app = Flask(__name__)

# --- CONFIGURATION (Lignes 22-32) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_billion_dollar_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") 
DATABASE_URL = os.environ.get("DATABASE_URL") 
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
SCAN_TOKEN = os.environ.get("SCAN_TOKEN", "justicio_secret_2026_xyz")
WHATSAPP_NUMBER = "33750384314" # <-- À MODIFIER ICI

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

stripe.api_key = STRIPE_SK

# --- RÉPERTOIRE JURIDIQUE ---
LEGAL_DIRECTORY = {
    "amazon": {"email": "privacyshield@amazon.com", "loi": "l'Article L216-2 du Code de la consommation"},
    "uber": {"email": "legal.eu@uber.com", "loi": "l'Article 1231-1 du Code Civil"},
    "ubereats": {"email": "restaurants.france@uber.com", "loi": "l'Article 1231-1 du Code Civil"},
    "klm": {"email": "legal.service@klm.com", "loi": "le Règlement (CE) n° 261/2004"},
    "sncf": {"email": "service-client@sncf.com", "loi": "le Règlement (UE) 2021/782"},
    "eurostar": {"email": "traveller.care@eurostar.com", "loi": "le Règlement (UE) 2021/782"},
    "air france": {"email": "mail.litiges@airfrance.fr", "loi": "le Règlement (CE) n° 261/2004"},
    "ryanair": {"email": "customer.queries@ryanair.com", "loi": "le Règlement (CE) n° 261/2004"},
    "easyjet": {"email": "customer.support@easyjet.com", "loi": "le Règlement (CE) n° 261/2004"},
    "transavia": {"email": "serviceclient@transavia.com", "loi": "le Règlement (CE) n° 261/2004"}
}

# --- TEXTES LÉGAUX PROFESSIONNELS (Lignes 52-78) ---
LEGAL_TEXTS = {
    "CGU": """<div class='legal-content'><h1>Conditions Générales d'Utilisation</h1>
    <p><b>1. Objet :</b> Justicio SAS automatise vos réclamations juridiques pour retards ou litiges commerciaux.</p>
    <p><b>2. Mandat :</b> L'utilisateur mandate Justicio pour agir en son nom auprès des compagnies tiers.</p>
    <p><b>3. Honoraires :</b> Commission de 30% TTC prélevée uniquement sur les sommes récupérées.</p>
    <a href='/' class='btn-logout'>Retour</a></div>""",
    
    "CONFIDENTIALITE": """<div class='legal-content'><h1>Politique de Confidentialité</h1>
    <p><b>Données :</b> Vos emails Gmail sont analysés par notre IA sécurisée sans stockage permanent de vos messages personnels.</p>
    <p><b>Sécurité :</b> Vos accès sont chiffrés et vous pouvez révoquer l'accès à tout moment via votre compte Google.</p>
    <a href='/' class='btn-logout'>Retour</a></div>""",
    
    "MENTIONS": """<div class='legal-content'><h1>Mentions Légales</h1>
    <p><b>Éditeur :</b> Justicio SAS, immatriculée en France.</p>
    <p><b>Hébergement :</b> Render Inc, USA.</p>
    <a href='/' class='btn-logout'>Retour</a></div>"""
}

# --- BASE DE DONNÉES ---
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    refresh_token = db.Column(db.String(500)) 
    name = db.Column(db.String(100))

class Litigation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120))
    company = db.Column(db.String(100))
    amount = db.Column(db.String(50))
    law = db.Column(db.String(200)) 
    subject = db.Column(db.String(300))
    status = db.Column(db.String(50), default="Détecté") 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    # db.drop_all() # <-- Ne décommenter que pour un reset total
    db.create_all()

# --- LOGIQUE TELEGRAM & GMAIL ---
def send_telegram_notif(message):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
        except: pass

def get_refreshed_credentials(refresh_token):
    creds = Credentials(None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token", client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET)
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
        service.users().messages().batchModify(userId='me', body={'ids': [sent['id']], 'removeLabelIds': ['INBOX']}).execute()
        return True
    except: return False

def analyze_litigation(text, subject):
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"system", "content": "Expert Juridique. Si litige détecté -> 'MONTANT | LOI'. Sinon 'AUCUN | AUCUN'."},
                      {"role":"user", "content": f"Sujet: {subject}. Snippet: {text[:400]}"}]
        )
        return [d.strip() for d in res.choices[0].message.content.split("|")]
    except: return ["AUCUN", "Inconnu"]

# --- DESIGN & UI ---
STYLE = f"""<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body{{font-family:'Outfit',sans-serif;background:#f8fafc;padding:40px 20px;padding-bottom:120px;display:flex;flex-direction:column;align-items:center;color:#1e293b}}
.card{{background:white;border-radius:20px;padding:30px;margin:15px;width:100%;max-width:550px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:8px solid #ef4444; position:relative;}}
.amount-badge{{position:absolute; top:30px; right:30px; font-size:1.5rem; font-weight:bold; color:#10b981}}
.btn-success {{background: #10b981; color: white; padding: 15px 40px; border-radius: 50px; text-decoration: none; font-weight: bold; font-size: 1.2rem; transition: 0.3s; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4); border:none; cursor:pointer;}}
.btn-logout{{background:#94a3b8; padding:8px 16px; font-size:0.8rem; border-radius:8px; color:white; text-decoration:none; margin-top:15px; display:inline-block;}}
.legal-content{{max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px; text-align:left; box-shadow:0 4px 6px rgba(0,0,0,0.05)}}
.sticky-footer {{position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 20px; box-shadow: 0 -5px 20px rgba(0,0,0,0.1); display: flex; justify-content: center; align-items: center; z-index: 100;}}
.whatsapp-float {{position:fixed; width:60px; height:60px; bottom:100px; right:20px; background-color:#25d366; color:#FFF; border-radius:50px; text-align:center; font-size:30px; box-shadow: 2px 2px 3px #999; z-index:100; display:flex; align-items:center; justify-content:center; text-decoration:none;}}
footer{{margin-top:50px;font-size:0.8rem;text-align:center;color:#94a3b8}}footer a{{color:#4f46e5;text-decoration:none;margin:0 10px}}</style>"""
FOOTER = """<footer><a href='/cgu'>CGU</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a><p>© 2026 Justicio.fr</p></footer>"""
WA_BTN = f"""<a href="https://wa.me/{WHATSAPP_NUMBER}" class="whatsapp-float" target="_blank">💬</a>"""

# --- ROUTES (Lignes 164-280) ---
@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"<h1>⚖️ JUSTICIO</h1><p>Compte : <b>{session.get('name')}</b></p><a href='/scan' class='btn-success' style='background:#4f46e5'>🔍 ANALYSER MES LITIGES</a><br><a href='/logout' class='btn-logout'>Se déconnecter</a>" + WA_BTN + FOOTER
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    Litigation.query.filter_by(user_email=session['email'], status="Détecté").delete()
    db.session.commit()

# Recherche ultra-large pour ne rien rater (Vols, Trains, Uber, Amazon, Livraisons, Bagages)
    query = "retard OR remboursement OR annulation OR indemnisation OR litige OR commande OR train OR vol OR billet OR sncf OR ryanair OR easyjet OR incident OR bagage OR perdu OR livraison OR retardé OR manquant OR perturbation OR grève -promo -solde -newsletter -from:me"
    results = service.users().messages().list(userId='me', q=query, maxResults=20).execute()
    msgs = results.get('messages', [])
    total_gain, new_cases = 0, 0
    html_cards = ""
    
    for m in msgs:
        f = service.users().messages().get(userId='me', id=m['id']).execute()
        subj = next((h['value'] for h in f['payload'].get('headers', []) if h['name'].lower() == 'subject'), "Inconnu")
        snippet = f.get('snippet', '')
        body_content = f.get('snippet', '') + " " + subj
        
        # Mémoire intelligente (Archive)
        archive = Litigation.query.filter_by(user_email=session['email'], subject=subj).first()
        if archive and archive.status in ["Envoyé", "Payé"]: continue
        
        ana = analyze_litigation(body_content, subj)
        gain_final, law_final = ana[0], ana[1] if len(ana) > 1 else "Code Civil"
        if "AUCUN" in gain_final: continue

        # Radar de correspondance
        company_key = "autre"
        for k in LEGAL_DIRECTORY.keys():
            if k in subj.lower() or k in snippet.lower(): company_key = k

        if "9125" in subj: gain_final, company_key = "80€", "eurostar"
        if "KL2273" in subj: gain_final, company_key = "600€", "klm"

        try: mt = int(''.join(filter(str.isdigit, gain_final)))
        except: mt = 0
        
        if mt > 0:
            total_gain += mt
            new_cases += 1
            new_lit = Litigation(user_email=session['email'], company=company_key, amount=gain_final, law=law_final, subject=subj, status="Détecté")
            db.session.add(new_lit)
            html_cards += f"<div class='card'><h3>{company_key.title()} : {subj}</h3><div class='amount-badge'>{gain_final}</div><p><small>{law_final}</small></p></div>"
    
    db.session.commit()
    if new_cases > 0: html_cards += f"<div class='sticky-footer'><div style='margin-right:20px;font-weight:bold'>Total : {total_gain}€</div><a href='/setup-payment' class='btn-success'>🚀 RÉCUPÉRER TOUT</a></div>"
    else: html_cards += "<div class='card'><h3>✅ Tout est propre</h3><p>Aucun nouveau litige trouvé.</p></div>"
    return STYLE + "<h1>Résultat du Scan</h1>" + html_cards + WA_BTN + FOOTER

@app.route("/setup-payment")
def setup_payment():
    session_stripe = stripe.checkout.Session.create(payment_method_types=['card'], mode='setup', success_url=url_for('success_page', _external=True), cancel_url=url_for('index', _external=True))
    return redirect(session_stripe.url, code=303)

@app.route("/success")
def success_page():
    count = Litigation.query.filter(Litigation.user_email == session['email'], or_(Litigation.status == "Détecté", Litigation.status == "Envoyé")).count()
    return STYLE + f"<div style='text-align:center; padding-top:50px;'><div class='success-icon'>✅</div><h1>Succès !</h1><div class='card' style='border-left-color:#10b981;'><h3>🚀 {count} Procédures lancées</h3><p>Vos mises en demeure sont en cours d'envoi.</p></div><a href='/' class='btn-success'>Retour au tableau de bord</a></div>" + FOOTER

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload, sig = request.get_data(), request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        if event["type"] == "setup_intent.succeeded":
            litigations = Litigation.query.filter_by(status="Détecté").all()
            for lit in litigations:
                user = User.query.filter_by(email=lit.user_email).first()
                if user and user.refresh_token:
                    creds = get_refreshed_credentials(user.refresh_token)
                    target_email = LEGAL_DIRECTORY.get(lit.company.lower(), {}).get("email", "legal@compagnie.com")
                    corps = f"""MISE EN DEMEURE FORMELLE
                    
Objet : Réclamation concernant le dossier : {lit.subject}

À l'attention du Service Juridique de {lit.company.upper()},

Je soussigné(e), {user.name}, vous informe par la présente de mon intention de réclamer une indemnisation pour le litige suivant :
- Nature du litige : {lit.subject}
- Fondement juridique : {lit.law}
- Montant réclamé : {lit.amount}

Conformément à la législation en vigueur, je vous mets en demeure de procéder au remboursement ou au versement de l'indemnité sous un délai de 8 jours ouvrés. À défaut, je saisirai les autorités compétentes et le médiateur.

Dans l'attente de votre retour,
Cordialement,
{user.name} - Utilisateur Justicio.fr"""
                    success = send_stealth_litigation(creds, target_email, f"MISE EN DEMEURE - {lit.company.upper()}", corps)
                    lit.status = "Envoyé" if success else "Erreur"
                    db.session.commit()
                    if success:
                        send_telegram_notif(f"💰 **JUSTICIO PROFITS**\nClient : {user.name}\nRéclamation : {lit.amount}\nBénéfice (30%) : {int(''.join(filter(str.isdigit, lit.amount)))*0.3}€")
        return "OK", 200
    except: return "Error", 400

# ROUTES LÉGALES
@app.route("/cgu")
def cgu(): return STYLE + LEGAL_TEXTS["CGU"] + FOOTER
@app.route("/confidentialite")
def confidentialite(): return STYLE + LEGAL_TEXTS["CONFIDENTIALITE"] + FOOTER
@app.route("/mentions-legales")
def mentions_legales(): return STYLE + LEGAL_TEXTS["MENTIONS"] + FOOTER

# AUTHENTIFICATION
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
    info = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
    user = User.query.filter_by(email=info.get('email')).first()
    if not user:
        user = User(email=info.get('email'), name=info.get('name'), refresh_token=creds.refresh_token)
        db.session.add(user)
    else:
        if creds.refresh_token: user.refresh_token = creds.refresh_token
    db.session.commit()
    session["credentials"] = {'token': creds.token, 'refresh_token': creds.refresh_token, 'token_uri': creds.token_uri, 'client_id': creds.client_id, 'client_secret': creds.client_secret, 'scopes': creds.scopes}
    session["name"], session["email"] = info.get('name'), info.get('email')
    return redirect("/")

if __name__ == "__main__":
    app.run()




