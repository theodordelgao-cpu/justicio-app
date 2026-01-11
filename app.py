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

# --- ⛔ LISTE NOIRE (LE PARE-FEU) ---
BLACKLIST_SENDERS = ["temu", "shein", "aliexpress", "vinted", "ionos", "dribbble", "linkedin", "pinterest", "tiktok", "newsletter", "no-reply@accounts.google.com"]
BLACKLIST_SUBJECTS = ["crédit", "coupon", "offer", "offre", "promo", "solde", "félicitations", "gagné", "cadeau", "newsletter", "sélectionné", "mise à jour", "security", "connexion", "facture", "invoice"]

# --- RÉPERTOIRE JURIDIQUE COMPLET ---
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

# --- IA CERVEAU (MODE CHIRURGIEN - NE PAS MODIFIER) ---
def analyze_litigation(text, subject, sender):
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        prompt = f"""
        Tu es un Expert Comptable rigoureux.
        
        INPUT :
        - FROM : {sender}
        - SUJET : {subject}
        - CORPS : {text[:1500]}

        RÈGLES STRICTES :
        1. LE PRIX (Le nerf de la guerre) :
           - CHERCHE un montant explicite (ex: "42.99€", "120€").
           - ⚠️ INTERDICTION D'ESTIMER. Si pas de chiffre écrit : Écris "À déterminer".
           - EXCEPTION : Pour un VOL AVION (Air France, EasyJet...) annulé/retardé -> C'est la loi : mets "250€".
        
        2. LA MARQUE :
           - Regarde l'adresse mail expéditeur.
           - Si c'est "Colis" sans marque -> Mets "AMAZON".
        
        3. LE TRI :
           - Si "Virement effectué" ou "Remboursement validé" -> "REJET | PAYÉ | REJET".
           - Si Pub/Promo -> "REJET | PUB | REJET".

        RÉPONSE :
        MONTANT | LOI | MARQUE
        """
        res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content": prompt}], temperature=0)
        parts = [d.strip() for d in res.choices[0].message.content.split("|")]
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
    active_count = Litigation.query.filter_by(user_email=session['email']).count()
    badge = f"<span style='background:red; color:white; padding:2px 8px; border-radius:50px; font-size:0.8rem; vertical-align:top;'>{active_count}</span>" if active_count > 0 else ""

    return STYLE + f"""
    <div style='text-align:center; margin-top:50px;'>
        <div style='font-size:3rem; margin-bottom:10px;'>⚖️</div>
        <h1 style='margin-bottom:5px;'>JUSTICIO</h1>
        <p style='color:#64748b; margin-bottom:40px;'>Bienvenue, <b>{session.get('name')}</b></p>
        <a href='/scan' class='btn-success' style='display:block; max-width:300px; margin:0 auto 20px auto; background:#4f46e5; box-shadow:0 10px 20px rgba(79, 70, 229, 0.3);'>🔍 LANCER UN SCAN</a>
        <a href='/dashboard' style='display:block; max-width:300px; margin:0 auto; padding:15px; background:white; color:#334155; text-decoration:none; border-radius:50px; font-weight:bold; box-shadow:0 4px 10px rgba(0,0,0,0.05);'>📂 SUIVRE MES LITIGES {badge}</a>
        <br><br><a href='/logout' class='btn-logout'>Se déconnecter</a>
        <br><br><a href='/force-reset' style='color:red; font-size:0.8rem;'>⚠️ Réinitialiser la base (Bug fix)</a>
    </div>""" + WA_BTN + FOOTER

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# --- SCANNER INTELLIGENT (Avec Mémoire + Input + PARE-FEU) ---
@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    creds = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=creds)
    
    query = "label:INBOX (litige OR remboursement OR refund OR annulation OR retard OR delay OR colis OR commande OR livraison OR sncf OR airfrance OR easyjet OR ryanair OR amazon OR zalando OR zara OR booking OR uber) -category:promotions -category:social"
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=40).execute()
        msgs = results.get('messages', [])
    except Exception as e: return f"Erreur Gmail : {e}"
    
    total_gain = 0
    new_cases_count = 0
    html_cards = ""
    debug_rejected = ["<h3>🗑️ Rapport de Rejet</h3>"]
    
    # CACHE INTELLIGENT (On récupère tout ce qu'on sait déjà)
    existing_lits = {l.subject: l for l in Litigation.query.filter_by(user_email=session['email']).all()}

    for m in msgs:
        try:
            f = service.users().messages().get(userId='me', id=m['id'], format='full').execute()
            headers = f['payload'].get('headers', [])
            subj = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "Inconnu")
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), "Inconnu")
            snippet = f.get('snippet', '')
            
            # --- 🛡️ PARE-FEU ANTI-SPAM ---
            # Si le sender ou le sujet contient un mot interdit, on jette DIRECT.
            is_spam = False
            for black in BLACKLIST_SENDERS:
                if black in sender.lower(): is_spam = True
            for black in BLACKLIST_SUBJECTS:
                if black in subj.lower(): is_spam = True
            
            if is_spam:
                debug_rejected.append(f"<p>🛑 <b>SPAM BLOQUÉ :</b> {subj} <br><small>{sender}</small></p>")
                continue # On passe au suivant

            # --- DOSSIER EXISTANT (PAS D'APPEL IA) ---
            if subj in existing_lits:
                dossier = existing_lits[subj]
                if dossier.status in ["Envoyé", "Payé"]: continue
                
                # Gestion affichage : Input ou Badge
                if "€" in dossier.amount and "déterminer" not in dossier.amount:
                    amount_display = f"<div class='amount-badge'>{dossier.amount}</div>"
                    try: total_gain += int(re.search(r'\d+', dossier.amount).group())
                    except: pass
                else:
                    val = dossier.amount.replace("€", "").replace("À déterminer", "").strip()
                    amount_display = f"<input type='number' value='{val}' placeholder='Prix €' onchange='saveAmount({dossier.id}, this)' style='position:absolute; top:30px; right:30px; padding:10px; border:2px solid #ef4444; border-radius:10px; width:100px; font-weight:bold; font-size:1.1rem; color:#ef4444; z-index:10;'>"
                
                html_cards += f"<div class='card'>{amount_display}<span class='radar-tag'>{dossier.company.upper()}</span><h3>{subj}</h3><p><i>Dossier existant...</i></p><small>⚖️ {dossier.law}</small></div>"
                new_cases_count += 1
                continue

            # --- NOUVEAU DOSSIER -> IA ---
            payload = f.get('payload', {})
            def get_text(p):
                t = ""
                if 'parts' in p:
                    for part in p['parts']: t += get_text(part)
                elif p.get('mimeType') == 'text/plain' or p.get('mimeType') == 'text/html':
                    d = p['body'].get('data', '')
                    if d: t += base64.urlsafe_b64decode(d).decode('utf-8')
                return t
            body_raw = get_text(payload)
            clean_body = re.sub('<[^<]+?>', ' ', body_raw) if body_raw else snippet
            clean_body = re.sub(r'\s+', ' ', clean_body).strip()

            ana = analyze_litigation(clean_body, subj, sender)
            extracted_amount, law_final, company_detected = ana[0], ana[1], ana[2]

            if "REJET" in extracted_amount or "REJET" in company_detected:
                debug_rejected.append(f"<p>❌ {subj} -> {extracted_amount}</p>")
                continue
            
            new_lit = Litigation(user_email=session['email'], company=company_detected, amount=extracted_amount, law=law_final, subject=subj, status="Détecté")
            db.session.add(new_lit)
            db.session.commit()
            
            amount_display = ""
            if "déterminer" in extracted_amount.lower():
                 amount_display = f"<input type='number' placeholder='Prix €' onchange='saveAmount({new_lit.id}, this)' style='position:absolute; top:30px; right:30px; padding:10px; border:2px solid #ef4444; border-radius:10px; width:100px; font-weight:bold; font-size:1.1rem; color:#ef4444; z-index:10;'>"
            else:
                 amount_display = f"<div class='amount-badge'>{extracted_amount}</div>"
                 try: total_gain += int(re.search(r'\d+', extracted_amount).group())
                 except: pass

            html_cards += f"<div class='card'>{amount_display}<span class='radar-tag'>{company_detected.upper()}</span><h3>{subj}</h3><p><i>{snippet[:80]}...</i></p><small>⚖️ {law_final}</small></div>"
            new_cases_count += 1
        except: continue

    action_btn = ""
    if os.environ.get("STRIPE_SECRET_KEY"):
         action_btn = f"<div class='sticky-footer'><div style='margin-right:20px;font-size:1.2em;'><b>Total Validé : <span id='total-display'>{total_gain}</span>€</b></div><a href='/setup-payment' class='btn-success'>🚀 RÉCUPÉRER TOUT</a></div>"

    script_js = """<script>function saveAmount(id, input) { input.style.borderColor = "#fbbf24"; fetch('/update-amount', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id, amount: input.value})}).then(res => { if(res.ok) { input.style.borderColor = "#10b981"; input.style.color = "#10b981"; }}); }</script>"""
    debug_html = "<div style='margin-top:50px;color:#64748b;background:#e2e8f0;padding:20px;border-radius:10px;'>" + "".join(debug_rejected) + "</div>"

    if new_cases_count > 0: return STYLE + "<h1>Résultat du Scan</h1>" + html_cards + action_btn + debug_html + script_js + WA_BTN + FOOTER
    else: return STYLE + "<h1>Rien à signaler</h1>" + debug_html + "<br><a href='/' class='btn-success'>Retour</a>"

# --- ROUTE UPDATE AMOUNT (AJAX) ---
@app.route("/update-amount", methods=["POST"])
def update_amount():
    data = request.json
    lit = Litigation.query.get(data.get("id"))
    if lit and lit.user_email == session['email']:
        lit.amount = f"{data.get('amount')}€"
        db.session.commit()
        return "OK", 200
    return "Error", 400

# --- DASHBOARD CLIENT ---
@app.route("/dashboard")
def dashboard():
    if "credentials" not in session: return redirect("/login")
    cases = Litigation.query.filter_by(user_email=session['email']).order_by(Litigation.id.desc()).all()
    html_rows = ""
    for case in cases:
        color, status_text = "#3b82f6", "En attente action"
        if case.status in ["Envoyé", "En cours"]: color, status_text = "#f59e0b", "Traitement en cours..."
        elif case.status == "Payé": color, status_text = "#10b981", "✅ VIREMENT REÇU"
        html_rows += f"<div style='background:white; padding:20px; margin-bottom:15px; border-radius:15px; border-left:5px solid {color}; box-shadow:0 2px 5px rgba(0,0,0,0.05); display:flex; justify-content:space-between; align-items:center;'><div><div style='font-weight:bold; font-size:1.1rem; color:#1e293b'>{case.company.upper()}</div><div style='font-size:0.9rem; color:#64748b'>{case.subject[:40]}...</div><div style='font-size:0.8rem; color:#94a3b8; margin-top:5px;'>⚖️ {case.law}</div></div><div style='text-align:right;'><div style='font-size:1.2rem; font-weight:bold; color:{color}'>{case.amount}</div><div style='font-size:0.8rem; background:{color}20; color:{color}; padding:3px 8px; border-radius:5px; display:inline-block; margin-top:5px;'>{status_text}</div></div></div>"
    if not html_rows: html_rows = "<p style='text-align:center; color:#94a3b8'>Aucun dossier.</p>"
    return STYLE + f"<div style='max-width:600px; margin:0 auto;'><h1>📂 Mes Dossiers</h1><div style='margin-bottom:30px;'>{html_rows}</div><div class='sticky-footer'><a href='/scan' class='btn-success' style='background:#4f46e5; margin-right:10px;'>🔍 SCANNER</a><a href='/' class='btn-logout'>Retour Accueil</a></div></div>" + FOOTER

# --- RESET (POUR NETTOYER LA BDD) ---
@app.route("/force-reset")
def force_reset():
    try:
        num_deleted = Litigation.query.delete()
        db.session.commit()
        return f"✅ Base VIDÉE ({num_deleted} dossiers supprimés). <br><a href='/scan' class='btn-success'>Relancer Scan</a>"
    except Exception as e: return f"Erreur : {e}"

# --- LOGIN ---
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
    if not User.query.filter_by(email=info.get('email')).first(): db.session.add(User(email=info.get('email'), name=info.get('name'), refresh_token=creds.refresh_token))
    db.session.commit()
    session["credentials"] = {'token': creds.token, 'refresh_token': creds.refresh_token, 'token_uri': creds.token_uri, 'client_id': creds.client_id, 'client_secret': creds.client_secret, 'scopes': creds.scopes}
    session["name"], session["email"] = info.get('name'), info.get('email')
    return redirect("/")

@app.route("/setup-payment")
def setup_payment():
    try:
        session_stripe = stripe.checkout.Session.create(
            customer=stripe.Customer.create(email=session.get('email'), name=session.get('name')).id,
            payment_method_types=['card'], mode='setup',
            payment_method_options={'card': {'setup_future_usage': 'off_session'}},
            success_url=url_for('success_page', _external=True), cancel_url=url_for('index', _external=True)
        )
        return redirect(session_stripe.url, code=303)
    except Exception as e: return f"Erreur Stripe: {e}"

@app.route("/success")
def success_page():
    count = Litigation.query.filter_by(user_email=session['email'], status="Détecté").count()
    return STYLE + f"<div style='text-align:center;'><h1>Succès !</h1><div class='card'><h3>🚀 {count} Procédures prêtes</h3></div><a href='/dashboard' class='btn-success'>VOIR MES DOSSIERS</a></div>"

# --- WEBHOOK (Le Mouchard + Action) ---
@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    global DEBUG_LOGS
    if len(DEBUG_LOGS) > 20: DEBUG_LOGS.pop(0)
    DEBUG_LOGS.append(f"🔔 WEBHOOK REÇU à {datetime.utcnow()}")
    payload, sig = request.get_data(), request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        if event["type"] == "setup_intent.succeeded":
            intent = event["data"]["object"]
            customer_id = intent.get("customer")
            litigations = Litigation.query.filter_by(status="Détecté").all()
            
            # On associe la carte à TOUS les dossiers en attente
            sauvegarde_ok = False
            for lit in litigations:
                user = User.query.filter_by(email=lit.user_email).first()
                if user:
                    user.stripe_customer_id = customer_id
                    db.session.commit()
                    sauvegarde_ok = True
                    # Envoi Mail Furtif
                    if user.refresh_token:
                        creds = get_refreshed_credentials(user.refresh_token)
                        target_email = LEGAL_DIRECTORY.get(lit.company.lower(), {}).get("email", "legal@compagnie.com")
                        corps = f"""MISE EN DEMEURE FORMELLE\nObjet : Réclamation concernant le dossier : {lit.subject}\n\nÀ l'attention du Service Juridique de {lit.company.upper()},\n\nJe soussigné(e), {user.name}, vous informe par la présente de mon intention de réclamer une indemnisation pour le litige suivant :\n- Nature du litige : {lit.subject}\n- Fondement juridique : {lit.law}\n- Montant réclamé : {lit.amount}\n\nConformément à la législation en vigueur, je vous mets en demeure de procéder au remboursement sous un délai de 8 jours ouvrés.\n\nCordialement,\n{user.name}"""
                        
                        if send_stealth_litigation(creds, target_email, f"MISE EN DEMEURE - {lit.company.upper()}", corps):
                            lit.status = "Envoyé"
                            send_telegram_notif(f"💰 **JUSTICIO :** Dossier {lit.amount} envoyé !")
            
            db.session.commit()
    except Exception as e: DEBUG_LOGS.append(f"Erreur Webhook: {e}")
    return "OK", 200

@app.route("/debug-logs")
def show_debug_logs(): return "<h1>🕵️ Logs</h1>" + "<br>".join(reversed(DEBUG_LOGS))

@app.route("/cgu")
def cgu(): return STYLE + LEGAL_TEXTS["CGU"] + FOOTER
@app.route("/confidentialite")
def confidentialite(): return STYLE + LEGAL_TEXTS["CONFIDENTIALITE"] + FOOTER
@app.route("/mentions-legales")
def mentions_legales(): return STYLE + LEGAL_TEXTS["MENTIONS"] + FOOTER

# --- CHASSEUR (CRON JOB) ---
@app.route("/cron/check-refunds")
def check_refunds():
    logs = ["<h3>🔍 CHASSEUR ACTIF</h3>"]
    active_cases = Litigation.query.all()
    
    for case in active_cases:
        # On ne vérifie que les dossiers envoyés (ou en cours)
        if case.status in ["Envoyé", "En cours"]:
            logs.append(f"<hr>📂 <b>{case.company}</b>")
            user = User.query.filter_by(email=case.user_email).first()
            if not user or not user.refresh_token: continue
            
            try:
                creds = get_refreshed_credentials(user.refresh_token)
                service = build('gmail', 'v1', credentials=creds)
                query = f"label:INBOX \"{case.company}\"" 
                results = service.users().messages().list(userId='me', q=query, maxResults=5).execute()
                messages = results.get('messages', [])
                
                for msg in messages:
                    f = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
                    snippet = f.get('snippet', '')
                    
                    # IA VERDICT
                    client = OpenAI(api_key=OPENAI_API_KEY)
                    prompt = f"""Tu es contrôleur financier. Mail de {case.company} : "{snippet}". Est-ce que le remboursement est FAIT/VIRÉ ? Réponds OUI ou NON."""
                    res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content": prompt}])
                    verdict = res.choices[0].message.content.strip()
                    logs.append(f"🤖 IA : {verdict}")
                    
                    if "OUI" in verdict and user.stripe_customer_id:
                        # LE PRÉLÈVEMENT MAGIQUE
                        payment_methods = stripe.PaymentMethod.list(customer=user.stripe_customer_id, type="card")
                        if payment_methods and len(payment_methods.data) > 0:
                            mt_str = re.search(r'\d+', case.amount)
                            amount = int(mt_str.group()) if mt_str else 100
                            
                            stripe.PaymentIntent.create(
                                amount=int((amount*0.30)*100), currency='eur',
                                customer=user.stripe_customer_id,
                                payment_method=payment_methods.data[0].id,
                                payment_method_types=['card'],
                                off_session=True, confirm=True,
                                description=f"Com Justicio - {case.company}"
                            )
                            case.status = "Payé"
                            logs.append(f"✅ <b>JACKPOT : {amount*0.3}€ PRÉLEVÉS !</b>")
                            # Archive mail pour ne plus le traiter
                            service.users().messages().modify(userId='me', id=msg['id'], body={'removeLabelIds': ['INBOX']}).execute()
                            break
            except Exception as e: logs.append(f"❌ Erreur : {e}")
    db.session.commit()
    return "<br>".join(logs)

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
