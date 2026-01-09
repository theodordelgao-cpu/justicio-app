import os
import base64
import requests
import stripe
import json
import re
import traceback
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

# --- VARIABLE GLOBALE POUR LE MOUCHARD ---
DEBUG_LOGS = [] 

app = Flask(__name__)

# --- MOUCHARD D'ERREUR (Anti-Erreur 500) ---
@app.errorhandler(Exception)
def handle_exception(e):
    return f"""
    <div style='font-family:sans-serif; padding:20px; color:red; background:#fee2e2; border:2px solid red;'>
        <h1>❌ ERREUR CRITIQUE</h1>
        <p>Une erreur est survenue. Voici les détails techniques :</p>
        <pre style='background:#333; color:#fff; padding:15px; overflow:auto;'>{traceback.format_exc()}</pre>
        <a href='/' style='display:inline-block; margin-top:20px; padding:10px; background:#333; color:white; text-decoration:none;'>Retour</a>
    </div>
    """, 500

# --- CONFIGURATION ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_secret_key_secure")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
WHATSAPP_NUMBER = "33750384314"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if STRIPE_SK:
    stripe.api_key = STRIPE_SK

# --- RÉPERTOIRE JURIDIQUE ---
LEGAL_DIRECTORY = {
    "amazon": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Droits des consommateurs)"},
    "apple": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 1999/44 (Garantie légale)"},
    "zalando": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Retour 14 jours)"},
    "shein": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Conformité)"},
    "zara": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Remboursement)"},
    "h&m": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Remboursement)"},
    "asos": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Retour)"},
    "fnac": {"email": "theodordelgao@gmail.com", "loi": "l'Article L217-4 du Code de la consommation"},
    "darty": {"email": "theodordelgao@gmail.com", "loi": "l'Article L217-4 du Code de la consommation"},
    "booking": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2015/2302 (Voyages à forfait)"},
    "airbnb": {"email": "theodordelgao@gmail.com", "loi": "le Règlement Rome I (Protection consommateur)"},
    "expedia": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2015/2302"},
    "ryanair": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "easyjet": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "lufthansa": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "air france": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "klm": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "british airways": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "sncf": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "eurostar": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "ouigo": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "uber": {"email": "theodordelgao@gmail.com", "loi": "le Droit Européen de la Consommation"},
    "deliveroo": {"email": "theodordelgao@gmail.com", "loi": "le Droit Européen de la Consommation"},
    "bolt": {"email": "theodordelgao@gmail.com", "loi": "le Droit Européen de la Consommation"}
}

# --- TEXTES LÉGAUX ---
LEGAL_TEXTS = {
    "CGU": """<div class='legal-content'><h1>Conditions Générales d'Utilisation</h1>
    <p><b>1. Objet :</b> Justicio SAS automatise vos réclamations juridiques.</p>
    <p><b>2. Honoraires :</b> Commission de 30% TTC prélevée uniquement sur les sommes récupérées.</p>
    <a href='/' class='btn-logout'>Retour</a></div>""",
    "CONFIDENTIALITE": """<div class='legal-content'><h1>Politique de Confidentialité</h1>
    <p>Vos emails sont analysés par notre IA sécurisée sans stockage permanent.</p>
    <a href='/' class='btn-logout'>Retour</a></div>""",
    "MENTIONS": """<div class='legal-content'><h1>Mentions Légales</h1>
    <p>Justicio SAS, France. Hébergement : Render Inc.</p>
    <a href='/' class='btn-logout'>Retour</a></div>"""
}

# --- BASE DE DONNÉES ---
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True, "pool_recycle": 300, "connect_args": {"keepalives": 1}}

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    refresh_token = db.Column(db.String(500)) 
    name = db.Column(db.String(100))
    stripe_customer_id = db.Column(db.String(100))

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
    try:
        db.create_all()
        print("✅ Base de données synchronisée.")
    except Exception as e:
        print(f"❌ Erreur DB : {e}")

# --- FONCTIONS UTILITAIRES ---
def send_telegram_notif(message):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
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
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except: return False

def analyze_litigation(text, subject, sender):
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        # PROMPT EXPERT : TRI SÉLECTIF ET SÉCURITÉ
        prompt = f"""
        Tu es un Auditeur Juridique Senior pour une société de protection des consommateurs.
        Ton rôle est de filtrer les emails entrants pour identifier UNIQUEMENT les litiges non résolus qui méritent une intervention.

        DONNÉES DU MAIL :
        - EXPÉDITEUR : {sender}
        - SUJET : {subject}
        - CORPS : {text[:1000]}

        --- RÈGLES D'ÉLIMINATION (SOIS IMPITOYABLE) ---
        1. ⛔ REJET PUB : Si c'est une newsletter, une promo ("Soldes", "Offre"), ou un spam -> "REJET | PUB | REJET".
        2. ⛔ REJET RÉSOLU : Si le mail confirme que l'argent a DÉJÀ été envoyé ("Virement effectué", "Remboursement validé", "Compte crédité") -> "REJET | DÉJÀ PAYÉ | REJET".
        3. ⛔ REJET ARNAQUE : Si l'expéditeur semble suspect pour une grande marque (ex: "service@amazon-remboursement-bizarre.com"), rejette-le -> "REJET | SUSPECT | REJET".
        4. ⛔ REJET ADMIN : Si c'est juste un changement de mot de passe ou une notif de connexion -> "REJET | NON PERTINENT | REJET".

        --- RÈGLES DE SÉLECTION (LE LITIGE ACTIF) ---
        Si le mail indique un PROBLÈME (Retard, Annulation, Colis non reçu, Produit défectueux, Demande de retour) :
        
        1. IDENTIFIE LA MARQUE :
           - Regarde le DOMAINE de l'email expéditeur (ex: "@zara.com" -> ZARA). C'est la priorité.
           - Si l'expéditeur est générique (ex: testeur), cherche la marque dans le texte.
           - Si le texte dit "Colis" sans marque -> Déduis "AMAZON" ou le transporteur.
           - Si le texte dit "Vol" ou "AF..." -> Déduis "AIR FRANCE".
           
        2. IDENTIFIE LE MONTANT :
           - Trouve la somme en jeu (ex: "prix du billet", "valeur du colis").
           - Si introuvable -> Écris "À déterminer".
           
        3. IDENTIFIE LA LOI :
           - Vol annulé/retardé -> "Règlement CE 261/2004"
           - Colis/Retour -> "Directive UE 2011/83"
           - Train -> "Règlement UE 2021/782"

        FORMAT DE RÉPONSE FINAL :
        MONTANT | LOI | MARQUE
        """
        
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"user", "content": prompt}],
            temperature=0 
        )
        parts = [d.strip() for d in res.choices[0].message.content.split("|")]
        # Sécurité format
        if len(parts) < 3: return parts + ["Inconnu"] * (3 - len(parts))
        return parts
    except: return ["REJET", "Inconnu", "Inconnu"]

# --- STYLE CSS ---
STYLE = f"""<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body{{font-family:'Outfit',sans-serif;background:#f8fafc;padding:40px 20px;padding-bottom:120px;display:flex;flex-direction:column;align-items:center;color:#1e293b}}
.card{{background:white;border-radius:20px;padding:30px;margin:15px;width:100%;max-width:550px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:8px solid #ef4444; position:relative;}}
.amount-badge{{position:absolute; top:30px; right:30px; font-size:1.5rem; font-weight:bold; color:#10b981}}
.radar-tag{{background:#e0f2fe; color:#0284c7; padding:4px 10px; border-radius:8px; font-size:0.8rem; font-weight:bold; text-transform:uppercase; letter-spacing:1px;}}
.btn-success {{background: #10b981; color: white; padding: 15px 40px; border-radius: 50px; text-decoration: none; font-weight: bold; font-size: 1.2rem; transition: 0.3s; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4); border:none; cursor:pointer;}}
.btn-logout{{background:#94a3b8; padding:8px 16px; font-size:0.8rem; border-radius:8px; color:white; text-decoration:none; margin-top:15px; display:inline-block;}}
.legal-content{{max-width:800px; line-height:1.6; background:white; padding:40px; border-radius:20px; text-align:left; box-shadow:0 4px 6px rgba(0,0,0,0.05)}}
.sticky-footer {{position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 20px; box-shadow: 0 -5px 20px rgba(0,0,0,0.1); display: flex; justify-content: center; align-items: center; z-index: 100;}}
.whatsapp-float {{position:fixed; width:60px; height:60px; bottom:100px; right:20px; background-color:#25d366; color:#FFF; border-radius:50px; text-align:center; font-size:30px; box-shadow: 2px 2px 3px #999; z-index:100; display:flex; align-items:center; justify-content:center; text-decoration:none;}}
footer{{margin-top:50px;font-size:0.8rem;text-align:center;color:#94a3b8}}footer a{{color:#4f46e5;text-decoration:none;margin:0 10px}}</style>"""
FOOTER = """<footer><a href='/cgu'>CGU</a> | <a href='/confidentialite'>Confidentialité</a> | <a href='/mentions-legales'>Mentions Légales</a><p>© 2026 Justicio.fr</p></footer>"""
WA_BTN = f"""<a href="https://wa.me/{WHATSAPP_NUMBER}" class="whatsapp-float" target="_blank">💬</a>"""

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"<h1>⚖️ JUSTICIO</h1><p>Compte : <b>{session.get('name')}</b></p><a href='/scan' class='btn-success' style='background:#4f46e5'>🔍 ANALYSER MES LITIGES</a><br><a href='/logout' class='btn-logout'>Se déconnecter</a>" + WA_BTN + FOOTER

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# --- SCANNER INTELLIGENT (Avec Synonymes & Déductions) ---
@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    
    # On vide la table pour le test (pour ne pas avoir de doublons visuels)
    Litigation.query.filter_by(user_email=session['email'], status="Détecté").delete()
    db.session.commit()

    # Requête large pour ne rien rater
    query = "label:INBOX (litige OR remboursement OR refund OR annulation OR retard OR delay OR colis OR commande OR livraison OR sncf OR airfrance OR easyjet OR ryanair OR amazon OR zalando OR zara OR booking OR uber)"
    
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=40).execute()
        msgs = results.get('messages', [])
    except Exception as e: return f"Erreur Gmail : {e}"
    
    total_gain, new_cases = 0, 0
    html_cards = ""
    debug_rejected = ["<h3>🗑️ Rapport de Rejet (Mails ignorés)</h3>"]
    
    for m in msgs:
        try:
            f = service.users().messages().get(userId='me', id=m['id'], format='full').execute()
            snippet = f.get('snippet', '')
            headers = f['payload'].get('headers', [])
            
            # --- EXTRACTION DES PREUVES ---
            subj = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "Inconnu")
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), "Inconnu") 
            
            # Lecture du corps
            payload = f.get('payload', {})
            body_data = ""
            def get_text(parts):
                t = ""
                for p in parts:
                    if p.get('parts'): t += get_text(p['parts'])
                    if p['mimeType'] == 'text/plain': 
                        d = p['body'].get('data', '')
                        if d: t += base64.urlsafe_b64decode(d).decode('utf-8')
                return t

            if 'parts' in payload: body_data = get_text(payload['parts'])
            elif 'body' in payload and payload['body'].get('data'):
                body_data = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
            
            clean_body = re.sub('<[^<]+?>', ' ', body_data) if body_data else snippet
            
            # --- ENVOI A L'EXPERT (IA) ---
            ana = analyze_litigation(clean_body, subj, sender)
            
            extracted_amount = ana[0]
            law_final = ana[1]
            company_detected = ana[2]

            # SI L'IA REJETTE LE MAIL
            if "REJET" in extracted_amount or "REJET" in company_detected:
                debug_rejected.append(f"<p>❌ <b>{subj}</b><br><small>Expéditeur: {sender}<br>Motif: {extracted_amount}</small></p>")
                continue
            
            # Vérification si dossier déjà existant
            archive = Litigation.query.filter_by(user_email=session['email'], subject=subj).first()
            if archive and archive.status in ["Envoyé", "Payé"]: continue
            
            # Nettoyage montant
            mt = 0
            try: mt = int(re.search(r'\d+', extracted_amount).group())
            except: mt = 0
            total_gain += mt
            new_cases += 1
            
            new_lit = Litigation(user_email=session['email'], company=company_detected, amount=extracted_amount, law=law_final, subject=subj, status="Détecté")
            db.session.add(new_lit)
            
            html_cards += f"<div class='card'><div class='amount-badge'>{extracted_amount}</div><span class='radar-tag'>{company_detected.upper()}</span><h3>{subj}</h3><p><i>{snippet[:100]}...</i></p><small>⚖️ {law_final}</small></div>"

        except: continue

    db.session.commit()
    
    stripe_btn = ""
    if os.environ.get("STRIPE_SECRET_KEY"):
         stripe_btn = f"<div class='sticky-footer'><div style='margin-right:20px;'>Total : {total_gain}€</div><a href='/setup-payment' class='btn-success'>🚀 RÉCUPÉRER TOUT</a></div>"

    debug_html = "<div style='margin-top:50px;color:#64748b;background:#e2e8f0;padding:20px;border-radius:10px;'>" + "".join(debug_rejected) + "</div>"
    
    if new_cases > 0: return STYLE + "<h1>Résultat du Scan</h1>" + html_cards + stripe_btn + debug_html
    else: return STYLE + "<h1>Rien à signaler</h1>" + debug_html + "<br><a href='/' class='btn-success'>Retour</a>"

@app.route("/setup-payment")
def setup_payment():
    try:
        # On crée le client
        customer = stripe.Customer.create(
            email=session.get('email'),
            name=session.get('name')
        )
        
        session_stripe = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=['card'],
            mode='setup',
            # 👇 C'EST LA LIGNE MAGIQUE POUR QUE LA BANQUE ACCEPTE LE PRÉLÈVEMENT FUTUR 👇
            payment_method_options={'card': {'setup_future_usage': 'off_session'}},
            success_url=url_for('success_page', _external=True),
            cancel_url=url_for('index', _external=True)
        )
        return redirect(session_stripe.url, code=303)
    except Exception as e:
        return f"Erreur Stripe : {str(e)}"

@app.route("/success")
def success_page():
    count = Litigation.query.filter(Litigation.user_email == session['email'], or_(Litigation.status == "Détecté", Litigation.status == "Envoyé")).count()
    return STYLE + f"<div style='text-align:center; padding-top:50px;'><h1>Succès !</h1><div class='card'><h3>🚀 {count} Procédures lancées</h3></div><a href='/' class='btn-success'>Retour</a></div>" + FOOTER

# --- LE WEBHOOK ESPION (Mouchard) ---
@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    global DEBUG_LOGS
    if len(DEBUG_LOGS) > 20: DEBUG_LOGS.pop(0)
    
    DEBUG_LOGS.append(f"🔔 WEBHOOK REÇU à {datetime.utcnow()} ! Tentative de lecture...")
    
    payload, sig = request.get_data(), request.headers.get("Stripe-Signature")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        DEBUG_LOGS.append(f"👉 Type d'événement : {event['type']}")
        
        if event["type"] == "setup_intent.succeeded":
            intent = event["data"]["object"]
            customer_id = intent.get("customer")
            DEBUG_LOGS.append(f"💳 Customer ID reçu : {customer_id}")
            
            litigations = Litigation.query.filter_by(status="Détecté").all()
            DEBUG_LOGS.append(f"📂 Dossiers 'Détecté' trouvés : {len(litigations)}")
            
            sauvegarde_ok = False
            
            for lit in litigations:
                user = User.query.filter_by(email=lit.user_email).first()
                if user:
                    # 1. Sauvegarde de la carte
                    user.stripe_customer_id = customer_id
                    db.session.commit()
                    DEBUG_LOGS.append(f"✅ CARTE SAUVEGARDÉE pour {user.email}")
                    sauvegarde_ok = True
                    
                    # 2. Envoi du mail
                    if user.refresh_token:
                        creds = get_refreshed_credentials(user.refresh_token)
                        target_email = LEGAL_DIRECTORY.get(lit.company.lower(), {}).get("email", "legal@compagnie.com")
                        
                        corps = f"""MISE EN DEMEURE FORMELLE\nObjet : Réclamation concernant le dossier : {lit.subject}\n\nÀ l'attention du Service Juridique de {lit.company.upper()},\n\nJe soussigné(e), {user.name}, vous informe par la présente de mon intention de réclamer une indemnisation pour le litige suivant :\n- Nature du litige : {lit.subject}\n- Fondement juridique : {lit.law}\n- Montant réclamé : {lit.amount}\n\nConformément à la législation en vigueur, je vous mets en demeure de procéder au remboursement sous un délai de 8 jours ouvrés.\n\nCordialement,\n{user.name}"""
                        
                        success = send_stealth_litigation(creds, target_email, f"MISE EN DEMEURE - {lit.company.upper()}", corps)
                        lit.status = "Envoyé" if success else "Erreur"
                        DEBUG_LOGS.append(f"📧 Mail envoyé pour {lit.company} : {success}")
                        
                        if success:
                            send_telegram_notif(f"💰 **JUSTICIO EMPREINTE PRIS**\nClient : {user.name}\nLitige : {lit.amount}")
            
            if not sauvegarde_ok:
                DEBUG_LOGS.append("⚠️ ALERTE : ID Stripe reçu mais aucun User/Dossier trouvé pour l'associer.")

    except Exception as e:
        DEBUG_LOGS.append(f"❌ ERREUR WEBHOOK : {str(e)}")
        return "Error", 400
        
    return "OK", 200

# --- ROUTE POUR LIRE LE MOUCHARD ---
@app.route("/debug-logs")
def show_debug_logs():
    return "<h1>🕵️ Journal du Webhook</h1>" + "<br><br>".join(reversed(DEBUG_LOGS))

# LOGIN / LOGOUT
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

@app.route("/cgu")
def cgu(): return STYLE + LEGAL_TEXTS["CGU"] + FOOTER
@app.route("/confidentialite")
def confidentialite(): return STYLE + LEGAL_TEXTS["CONFIDENTIALITE"] + FOOTER
@app.route("/mentions-legales")
def mentions_legales(): return STYLE + LEGAL_TEXTS["MENTIONS"] + FOOTER

# --- LE CHASSEUR (VERSION LIVE & TEST COMPATIBLE) ---
@app.route("/cron/check-refunds")
def check_refunds():
    logs = ["<h3>🔍 DIAGNOSTIC FINAL</h3>"]
    active_cases = Litigation.query.all()
    logs.append(f"👉 <b>ETAPE 1 :</b> Dossiers totaux : <b>{len(active_cases)}</b>")
    
    if not active_cases: return "❌ Base de données vide."
    
    for case in active_cases:
        logs.append(f"<hr>📂 <b>Dossier : {case.company}</b>")
        user = User.query.filter_by(email=case.user_email).first()
        if not user or not user.refresh_token: continue
        
        try:
            creds = get_refreshed_credentials(user.refresh_token)
            service = build('gmail', 'v1', credentials=creds)
            company_domain = case.company.lower()
            query = f"label:INBOX {company_domain}" 
            results = service.users().messages().list(userId='me', q=query, maxResults=3).execute()
            messages = results.get('messages', [])
            logs.append(f"📧 Mails '{company_domain}' trouvés : <b>{len(messages)}</b>")

            for msg in messages:
                # Lecture
                f = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
                payload = f.get('payload', {})
                body_data = ""
                if 'parts' in payload:
                    for part in payload['parts']:
                        if part['mimeType'] == 'text/plain':
                            data = part['body'].get('data', '')
                            if data: body_data = base64.urlsafe_b64decode(data).decode('utf-8')
                if not body_data and 'body' in payload:
                    data = payload['body'].get('data', '')
                    if data: body_data = base64.urlsafe_b64decode(data).decode('utf-8')
                final_content = body_data if body_data else f.get('snippet', '')
                logs.append(f"📝 <b>Contenu :</b> <i>{final_content[:50]}...</i>")
                
                client = OpenAI(api_key=OPENAI_API_KEY)
                prompt = f"""Tu es un contrôleur financier. Voici un email de {case.company}. CONTENU : "{final_content}". EST-CE QUE LE REMBOURSEMENT EST VALIDÉ/EFFECTUÉ ? Réponds OUI ou NON."""
                res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content": prompt}])
                verdict = res.choices[0].message.content.strip()
                logs.append(f"🤖 <b>VERDICT IA :</b> {verdict}")
                
                if "OUI" in verdict:
                    if user.stripe_customer_id:
                        try:
                            # Récupération carte
                            payment_methods = stripe.PaymentMethod.list(customer=user.stripe_customer_id, type="card")
                            if payment_methods and len(payment_methods.data) > 0:
                                carte_id = payment_methods.data[0].id
                                logs.append(f"💳 Carte trouvée : {carte_id}")
                                
                                mt_str = re.search(r'\d+', case.amount)
                                amount_total = int(mt_str.group()) if mt_str else 0
                                if amount_total == 0: amount_total = 100 
                                commission_cents = int((amount_total * 0.30) * 100)
                                
                                # Prélèvement
                                stripe.PaymentIntent.create(
                                    amount=commission_cents, 
                                    currency='eur', 
                                    customer=user.stripe_customer_id, 
                                    payment_method=carte_id,
                                    payment_method_types=['card'],
                                    off_session=True, 
                                    confirm=True, 
                                    description=f"Commission Justicio - Succès {case.company}"
                                )
                                case.status = "Payé"
                                logs.append(f"✅ <b>JACKPOT :</b> {amount_total*0.3}€ prélevés !")
                                service.users().messages().modify(userId='me', id=msg['id'], body={'removeLabelIds': ['INBOX']}).execute()
                                break 
                            else: logs.append("⚠️ ID Client ok mais pas de carte trouvée.")
                        except stripe.error.CardError as e:
                            logs.append(f"❌ <b>CARTE REFUSÉE (Banque) :</b> {e.error.message}")
                        except Exception as e:
                            logs.append(f"❌ <b>ERREUR STRIPE :</b> {str(e)}")
                    else: logs.append("⚠️ Pas de Customer ID.")
        except Exception as e: logs.append(f"❌ Erreur technique : {str(e)}")
            
    db.session.commit()
    return "<br>".join(logs)

# --- LE MARTEAU (RESET FORCE) ---
@app.route("/force-reset")
def force_reset():
    lit = Litigation.query.first()
    if lit:
        nom = lit.company
        lit.status = "Détecté"
        db.session.commit()
        return f"✅ Dossier '<b>{nom}</b>' remis à l'état 'Détecté'.<br><br>👉 Tu peux retourner sur l'accueil et remettre ta carte."
    return "❌ La base de données est vide."

@app.route("/verif-user")
def verif_user():
    users = User.query.all()
    res = []
    for u in users:
        etat_carte = f"✅ CARTE OK ({u.stripe_customer_id})" if u.stripe_customer_id else "❌ PAS DE CARTE"
        res.append(f"Utilisateur : {u.name} | {u.email} | {etat_carte}")
    return "<br>".join(res)

if __name__ == "__main__":
    app.run()










