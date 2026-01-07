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
    # E-COMMERCE
    "amazon": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Droits des consommateurs)"},
    "apple": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 1999/44 (Garantie légale)"},
    "zalando": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Retour 14 jours)"},
    "shein": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Conformité)"},
    "zara": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Remboursement)"},
    "h&m": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Remboursement)"},
    "asos": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2011/83 (Retour)"},
    "fnac": {"email": "theodordelgao@gmail.com", "loi": "l'Article L217-4 du Code de la consommation"},
    "darty": {"email": "theodordelgao@gmail.com", "loi": "l'Article L217-4 du Code de la consommation"},

    # VOYAGE & HÔTELS
    "booking": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2015/2302 (Voyages à forfait)"},
    "airbnb": {"email": "theodordelgao@gmail.com", "loi": "le Règlement Rome I (Protection consommateur)"},
    "expedia": {"email": "theodordelgao@gmail.com", "loi": "la Directive UE 2015/2302"},

    # TRANSPORTS
    "ryanair": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "easyjet": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "lufthansa": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "air france": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "klm": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "british airways": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (CE) n° 261/2004"},
    "sncf": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "eurostar": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},
    "ouigo": {"email": "theodordelgao@gmail.com", "loi": "le Règlement (UE) 2021/782"},

    # VTC / FOOD
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

# --- BASE DE DONNÉES (Configuration Blindée & Anti-Timeout) ---
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Options vitales pour Render
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "connect_args": {
        "keepalives": 1,
    }
}

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
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except: return False

def analyze_litigation(text, subject):
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        # PROMPT "MODE FILTRE ANTI-SPAM"
        prompt = f"""
        Tu es un avocat expert chargé de filtrer les emails.
        Analyse ce mail :
        Sujet: {subject}
        Contenu: {text[:800]}

        RÈGLES STRICTES :
        1. Si c'est une PUBLICITÉ, une NEWSLETTER, une notification de compte (mot de passe, connexion), une offre d'essai ("trial", "upgrade") ou une confirmation normale : Réponds UNIQUEMENT "REJET | REJET".
        2. Si c'est un VRAI LITIGE (Demande de remboursement, Colis non reçu, Vol annulé/retardé) : Réponds "MONTANT | LOI".
        
        Exemple Pub : "Profitez des soldes" -> REJET | REJET
        Exemple Litige : "Je n'ai pas reçu ma commande de 142€" -> 142€ | Directive UE 2011/83
        """
        
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"user", "content": prompt}],
            temperature=0  # Zéro créativité, on veut du binaire
        )
        return [d.strip() for d in res.choices[0].message.content.split("|")]
    except: return ["REJET", "Inconnu"]

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

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    
    # Nettoyage visuel BDD
    Litigation.query.filter_by(user_email=session['email'], status="Détecté").delete()
    db.session.commit()

    # QUERY BLINDÉE (Anti-Pub)
    query = (
        "label:INBOX "
        "(retard OR delay OR annulation OR cancelled OR remboursement OR refund OR "
        "litige OR claim OR bagage OR lost OR endommagé OR damaged OR vol OR flight OR "
        "train OR commande OR order OR livraison OR delivery OR colis OR package OR "
        "sncf OR ryanair OR easyjet OR airfrance OR klm OR lufthansa OR uber OR amazon OR "
        "zalando OR shein OR zara OR booking OR airbnb) "
        "-promo -solde -soldes -newsletter -publicité -advertising -no-reply -from:mailer-daemon "
        "-trial -upgrade -features -checklist -welcome -invitation -update -prime "
        "-subject:\"MISE EN DEMEURE\" -subject:\"Delivery Status\""
    )

    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
        msgs = results.get('messages', [])
    except Exception as e:
        return f"Erreur Gmail API : {str(e)}"
    
    total_gain, new_cases = 0, 0
    html_cards = ""
    
    for m in msgs:
        try:
            f = service.users().messages().get(userId='me', id=m['id']).execute()
            snippet = f.get('snippet', '')
            subj = next((h['value'] for h in f['payload'].get('headers', []) if h['name'].lower() == 'subject'), "Inconnu")
            
            payload = f.get('payload', {})
            body_data = ""
            if 'parts' in payload:
                for part in payload['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body'].get('data', '')
                        if data:
                            body_data = base64.urlsafe_b64decode(data).decode('utf-8')
            body_content = (body_data if body_data else snippet) + " " + subj
            
            # --- INTELLIGENCE (LE JUGE) ---
            ana = analyze_litigation(body_content, subj)
            extracted_amount = ana[0] # Peut contenir "REJET"
            law_final = ana[1] if len(ana) > 1 else "Code Civil"
            
            # FILTRE : SI L'IA DIT "REJET", ON IGNORE CE MAIL
            if "REJET" in extracted_amount or "REJET" in law_final:
                continue

            gain_final, company_key = "AUCUN", "Inconnu"

            # 1. AVION (250€)
            airlines = ["ryanair", "lufthansa", "air france", "easyjet", "klm", "volotea", "vueling", "transavia", "british airways"]
            if any(air in subj.lower() for air in airlines):
                gain_final = "250€"
                for air in airlines:
                    if air in subj.lower(): company_key = air

            # 2. COMMERCE
            else:
                targets = ["sncf", "booking", "airbnb", "uber", "deliveroo", "zara", "amazon", "apple", "zalando", "shein", "asos", "fnac", "darty"]
                for target in targets:
                    if target in subj.lower() or target in body_content.lower():
                        company_key = target
                        
                        # A. Prix IA (Si c'est un chiffre)
                        if any(char.isdigit() for char in extracted_amount):
                            gain_final = extracted_amount
                        
                        # B. Prix Regex (Secours)
                        if "AUCUN" in gain_final or "Déterminer" in gain_final:
                            match = re.search(r'(\d+[.,]?\d*)\s?€', body_content)
                            if match:
                                gain_final = f"{match.group(1)}€"
                            else:
                                gain_final = "À déterminer"

            # 3. VERDICT FINAL (PAS D'ARCHIVAGE ICI !)
            if company_key != "Inconnu" and "AUCUN" not in gain_final:
                # Doublon ?
                archive = Litigation.query.filter_by(user_email=session['email'], subject=subj).first()
                if archive and archive.status in ["Envoyé", "Payé"]: continue
                
                # Total
                mt = 0
                try:
                    mt = int(re.search(r'\d+', gain_final).group())
                except: mt = 0
                total_gain += mt
                new_cases += 1
                
                if company_key in LEGAL_DIRECTORY:
                    law_final = LEGAL_DIRECTORY[company_key]["loi"]
                
                new_lit = Litigation(user_email=session['email'], company=company_key, amount=gain_final, law=law_final, subject=subj, status="Détecté")
                db.session.add(new_lit)
                
                # J'AI SUPPRIMÉ LE BLOC ARCHIVAGE ICI (Comme demandé)

                html_cards += f"<div class='card'><div class='amount-badge'>{gain_final}</div><span class='radar-tag'>{company_key.upper()}</span><h3 style='margin:10px 0; font-size:1.1rem'>{subj}</h3><p style='color:#64748b; font-size:0.9rem; background:#f1f5f9; padding:10px; border-radius:10px'><i>\"{snippet[:120]}...\"</i></p><p><small>⚖️ {law_final}</small></p></div>"
        except:
            continue

    db.session.commit()
    
    stripe_btn = ""
    if os.environ.get("STRIPE_SECRET_KEY"):
         stripe_btn = f"<div class='sticky-footer'><div style='margin-right:20px;font-weight:bold'>Total : {total_gain}€</div><a href='/setup-payment' class='btn-success'>🚀 RÉCUPÉRER TOUT</a></div>"
    else:
         stripe_btn = "<div class='sticky-footer' style='background:#fee2e2; color:#b91c1c; padding:10px;'>⚠️ Clé Stripe manquante sur Render</div>"

    if new_cases > 0: 
        return STYLE + "<h1>Résultat du Scan</h1>" + html_cards + stripe_btn + WA_BTN + FOOTER
    else: 
        return STYLE + "<h1>✅ Tout est propre</h1><p>Aucun litige détecté (les publicités ont été ignorées).</p><a href='/' class='btn-logout'>Retour</a>" + FOOTER

@app.route("/setup-payment")
def setup_payment():
    try:
        session_stripe = stripe.checkout.Session.create(payment_method_types=['card'], mode='setup', success_url=url_for('success_page', _external=True), cancel_url=url_for('index', _external=True))
        return redirect(session_stripe.url, code=303)
    except Exception as e:
        return f"Erreur Stripe : {str(e)}"

@app.route("/success")
def success_page():
    count = Litigation.query.filter(Litigation.user_email == session['email'], or_(Litigation.status == "Détecté", Litigation.status == "Envoyé")).count()
    return STYLE + f"<div style='text-align:center; padding-top:50px;'><h1>Succès !</h1><div class='card'><h3>🚀 {count} Procédures lancées</h3></div><a href='/' class='btn-success'>Retour</a></div>" + FOOTER

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload, sig = request.get_data(), request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        
        # Quand le client rentre sa carte (Setup Intent)
        if event["type"] == "setup_intent.succeeded":
            
            # 1. On récupère l'ID Client Stripe (L'empreinte de la carte)
            setup_intent = event["data"]["object"]
            customer_id = setup_intent.get("customer")
            
            litigations = Litigation.query.filter_by(status="Détecté").all()
            for lit in litigations:
                user = User.query.filter_by(email=lit.user_email).first()
                
                # 2. ON SAUVEGARDE L'EMPREINTE DANS LA BDD (Pour prélever plus tard)
                if user and customer_id:
                    user.stripe_customer_id = customer_id
                    db.session.commit()
                
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
                    
                    if success:
                        try:
                            # Notif Telegram
                            mt_str = re.search(r'\d+', lit.amount)
                            mt = int(mt_str.group()) if mt_str else 0
                            gain_estime = mt * 0.3
                            send_telegram_notif(f"💰 **JUSTICIO EMPREINTE PRIS**\nClient : {user.name}\nCarte enregistrée pour futur prélèvement.\nLitige : {lit.amount}")

                            # Archivage du mail (On nettoie l'Inbox)
                            try:
                                service = build('gmail', 'v1', credentials=creds)
                                q_search = f"subject:\"{lit.subject}\" label:INBOX"
                                found = service.users().messages().list(userId='me', q=q_search, maxResults=1).execute()
                                msgs_found = found.get('messages', [])
                                if msgs_found:
                                    service.users().messages().modify(userId='me', id=msgs_found[0]['id'], body={'removeLabelIds': ['INBOX', 'UNREAD']}).execute()
                            except: pass
                        except: pass
                        
            db.session.commit()
        return "OK", 200
    except: return "Error", 400

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

if __name__ == "__main__":
    app.run()

