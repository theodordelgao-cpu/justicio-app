import os
import base64
import random
from flask import Flask, session, redirect, request, url_for
from flask_sqlalchemy import SQLAlchemy # AJOUT : Pour la base de données
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURATION ---
app.secret_key = os.environ.get("SECRET_KEY", "azerty_super_secret_key")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# AJOUT : Configuration de la base de données PostgreSQL
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODÈLE DE LA BASE (La mémoire de Justicio) ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    refresh_token = db.Column(db.String(500), nullable=True)
    name = db.Column(db.String(100))

# Création de la table au démarrage si elle n'existe pas
with app.app_context():
    db.create_all()

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

client_secrets_config = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "project_id": "justicio-app",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": GOOGLE_CLIENT_SECRET
    }
}

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose", 
    "openid"
]

# --- DESIGN ÉPURÉ (STYLE INCHANGÉ) ---
STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap');
:root { --primary: #4f46e5; --danger: #dc2626; --bg: #f8fafc; }
body { font-family: 'Outfit', sans-serif; background-color: var(--bg); margin: 0; padding: 40px 20px; color: #0f172a; display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
.container { width: 100%; max-width: 650px; }
h1 { font-weight: 800; font-size: 2.5rem; text-align: center; margin-bottom: 10px; color: var(--primary); }
.subtitle { text-align: center; color: #64748b; margin-bottom: 40px; font-size: 1.1rem; }

.card { background: white; border-radius: 20px; padding: 25px; margin-bottom: 20px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #fee2e2; border-left: 8px solid var(--danger); background: #fff1f2; animation: popIn 0.5s ease; }
h3 { margin: 0 0 5px 0; font-size: 1.2rem; }
.sender { color: #64748b; font-size: 0.9rem; margin-bottom: 15px; font-weight: 600; }
.badge { display: inline-block; padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; font-weight: 600; margin-right: 5px; background: white; border: 1px solid #fca5a5; color: #991b1b; }
.btn { display: block; width: 100%; padding: 15px; border-radius: 12px; text-align: center; text-decoration: none; font-weight: 600; margin-top: 15px; cursor: pointer; border: none; font-size: 1rem; }
.btn-primary { background: var(--primary); color: white; }
.btn-danger { background: var(--danger); color: white; box-shadow: 0 4px 6px rgba(220, 38, 38, 0.2); animation: pulse 2s infinite; }
.empty-state { text-align: center; padding: 40px; background: white; border-radius: 20px; border: 2px dashed #cbd5e1; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.7); } 70% { box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); } 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); } }
@keyframes popIn { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }
</style>
"""

def credentials_to_dict(credentials):
    return {'token': credentials.token, 'refresh_token': credentials.refresh_token, 'token_uri': credentials.token_uri, 'client_id': credentials.client_id, 'client_secret': credentials.client_secret, 'scopes': credentials.scopes}

# --- CERVEAU ---
def analyze_with_ai(text, subject, sender):
    sujet_low = subject.lower()
    if "problème" in sujet_low or "probleme" in sujet_low:
        return {"amount": "ACTION REQUISE", "status": "LITIGE DÉTECTÉ", "color": "red"}

    if not OPENAI_API_KEY: return {"amount": "?", "status": "No Key", "color": "gray"}
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""
    Analyse ce mail. Sujet: "{subject}".
    Règles:
    1. Si retard, perte, vol, problème, non reçu -> DANGER (Rouge).
    2. Si livré, expédié, en route -> SAFE (Vert).
    Réponds: MONTANT | STATUT | RISQUE
    """
    try:
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], max_tokens=50)
        parts = response.choices[0].message.content.strip().split("|")
        if len(parts) == 3: return {"amount": parts[0].strip(), "status": parts[1].strip(), "color": "red" if "DANGER" in parts[2] else "green"}
        return {"amount": "?", "status": "Inconnu", "color": "gray"}
    except: return {"amount": "Err", "status": "Erreur", "color": "gray"}

def generate_agency_email(text, subject, sender, user_name):
    client = OpenAI(api_key=OPENAI_API_KEY)
    case_num = random.randint(10000, 99999)
    prompt = f"Tu es le SERVICE JURIDIQUE JUSTICIO. Rédige MISE EN DEMEURE pour '{user_name}' contre '{sender}'. Sujet: '{subject}'. Contexte: '{text[:500]}'. Ton menaçant. Cite Art L.216-1 Code Conso. Exige remboursement. Signe: 'SERVICE CONTENTIEUX JUSTICIO, Dossier #{case_num}, Mandataire de {user_name}'. Pas de Markdown."
    response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], max_tokens=600)
    return response.choices[0].message.content.replace("```", "").strip()

def send_email_directly(service, user_email, to_email, subject, body):
    msg = MIMEText(body)
    msg['to'] = to_email
    msg['from'] = user_email
    msg['subject'] = f"MISE EN DEMEURE - DOSSIER JUSTICIO (Réf: {subject})"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={'raw': raw}).execute()

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" in session:
        return STYLE + f"""
        <div class='container'>
            <h1>⚖️ JUSTICIO</h1>
            <p class='subtitle'>Bonjour <strong>{session.get('name', 'Utilisateur')}</strong>.</p>
            <div class='card' style='text-align:center; border: 2px solid var(--primary); background:white; border-left:none;'>
                <h3 style='font-size: 1.5rem; margin-bottom: 10px;'>🛡️ Protection Active</h3>
                <p style='color:#64748b;'>Scannez vos emails pour détecter les anomalies.</p>
                <a href='/scan'><button class='btn btn-primary'>🚀 LANCER LE SCAN</button></a>
            </div>
            <div style='text-align:center; margin-top:30px;'><a href='/logout' style='color:#94a3b8; text-decoration:none;'>Déconnexion</a></div>
        </div>
        """
    return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    try:
        credentials = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=credentials)
        
        query = "subject:(Uber OR Amazon OR SNCF OR Temu OR Facture OR Commande OR Problème)"
        results = service.users().messages().list(userId='me', q=query, maxResults=15).execute()
        messages = results.get('messages', [])
        
        litiges_html = ""
        count = 0
        
        for msg in messages:
            full = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            if "<" in sender: sender = sender.split("<")[0].replace('"', '')

            snippet = full.get('snippet', '')
            analysis = analyze_with_ai(snippet, subject, sender)
            
            if analysis['color'] == "red":
                count += 1
                litiges_html += f"""
                <div class='card'>
                    <h3>{subject}</h3>
                    <div class='sender'>Contre : {sender}</div>
                    <div><span class='badge'>💰 {analysis['amount']}</span><span class='badge'>📝 {analysis['status']}</span></div>
                    <a href='/auto_send/{msg['id']}'><button class='btn btn-danger'>⚡ RÉCLAMER MAINTENANT</button></a>
                </div>
                """
        
        if count == 0:
            result_content = f"""
            <div class='empty-state'>
                <div style='font-size: 4rem;'>✅</div>
                <h2>Tout est parfait.</h2>
                <p style='color:#64748b;'>Aucun litige détecté sur vos 15 dernières commandes.</p>
            </div>
            """
        else:
            result_content = litiges_html

        return STYLE + f"""
        <div class='container'>
            <h1>📂 Résultats</h1>
            <p class='subtitle'>{count} dossier(s) à risque détecté(s).</p>
            {result_content}
            <a href='/'><button class='btn' style='background:#e2e8f0; color:#475569;'>Retour</button></a>
        </div>
        """
    except Exception as e: return f"Erreur: {e} <a href='/logout'>Reset</a>"

@app.route("/auto_send/<msg_id>")
def auto_send(msg_id):
    if "credentials" not in session: return redirect("/login")
    credentials = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=credentials)
    
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = msg['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
    sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
    if "<" in sender: sender = sender.split("<")[1].replace(">", "")
    snippet = msg.get('snippet', '')
    
    legal_body = generate_agency_email(snippet, subject, sender, session.get('name', 'Client'))
    
    try:
        send_email_directly(service, "me", sender, subject, legal_body)
        return STYLE + f"<div class='container' style='text-align:center; margin-top:50px;'><h1 style='color:var(--primary);'>Succès !</h1><div class='card' style='background:white; border-left:none;'><p>Justicio a envoyé la mise en demeure.</p></div><a href='/scan'><button class='btn btn-primary'>Retour</button></a></div>"
    except Exception as e: return f"Erreur: {e}"

@app.route("/login")
def login():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
    session["state"] = state
    return redirect(auth_url)

@app.route("/callback")
def callback():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(authorization_response=request.url)
    
    creds = flow.credentials
    session["credentials"] = credentials_to_dict(creds)

    # --- NOUVEAU : Sauvegarde dans la base de données ---
    # Récupère l'email via Google
    service = build('oauth2', 'v2', credentials=creds)
    user_info = service.userinfo().get().execute()
    email = user_info['email']
    name = user_info.get('name', 'Utilisateur')

    # Vérifie si l'utilisateur existe déjà
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name)
        db.session.add(user)
    
    # Enregistre le précieux Refresh Token
    if creds.refresh_token:
        user.refresh_token = creds.refresh_token
    
    user.name = name
    db.session.commit()

    session["name"] = name
    return redirect("/")

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
