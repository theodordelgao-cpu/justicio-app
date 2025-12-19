import os
import base64
import random
from flask import Flask, session, redirect, request, url_for
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

# --- LE DESIGN PREMIUM (CSS) ---
STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;700&display=swap');

:root {
    --primary: #4f46e5;
    --primary-dark: #4338ca;
    --danger: #ef4444;
    --success: #10b981;
    --bg: #f8fafc;
    --card-bg: #ffffff;
    --text-main: #1e293b;
    --text-light: #64748b;
}

body {
    font-family: 'Outfit', sans-serif;
    background-color: var(--bg);
    margin: 0;
    padding: 0;
    color: var(--text-main);
    display: flex;
    flex-direction: column;
    min-height: 100vh;
    align-items: center;
}

.container {
    width: 100%;
    max-width: 600px;
    padding: 20px;
    margin-top: 40px;
    animation: fadeIn 0.8s ease-out;
}

h1 {
    font-weight: 700;
    font-size: 2.5rem;
    text-align: center;
    margin-bottom: 10px;
    background: linear-gradient(135deg, #1e293b 0%, #4f46e5 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

h3 { font-weight: 500; margin-top: 0; font-size: 1.1rem; }

.status-badge {
    display: inline-block;
    padding: 6px 12px;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 600;
    margin-right: 8px;
}
.badge-money { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
.badge-info { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }

.card {
    background: var(--card-bg);
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    transition: transform 0.2s;
    border: 1px solid #e2e8f0;
}

.card:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1); }

.card-danger { border-left: 5px solid var(--danger); }
.card-safe { border-left: 5px solid var(--success); }

.btn {
    display: inline-block;
    padding: 14px 28px;
    border-radius: 12px;
    font-weight: 600;
    text-decoration: none;
    cursor: pointer;
    border: none;
    transition: all 0.2s;
    font-size: 1rem;
    width: 100%;
    text-align: center;
}

.btn-primary { background: var(--primary); color: white; }
.btn-primary:hover { background: var(--primary-dark); }

.btn-danger { background: var(--danger); color: white; box-shadow: 0 4px 10px rgba(239, 68, 68, 0.3); }
.btn-danger:hover { transform: scale(1.02); }

.meta { color: var(--text-light); font-size: 0.9rem; margin-bottom: 15px; }
.subtext { text-align: center; color: var(--text-light); margin-bottom: 30px; }

@keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
</style>
"""

def credentials_to_dict(credentials):
    return {'token': credentials.token, 'refresh_token': credentials.refresh_token, 'token_uri': credentials.token_uri, 'client_id': credentials.client_id, 'client_secret': credentials.client_secret, 'scopes': credentials.scopes}

# --- IA & LOGIQUE (Cerveau) ---
def analyze_with_ai(text, subject, sender):
    if not OPENAI_API_KEY: return {"amount": "?", "status": "Pas de clé", "color": "gray"}
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # PROMPT INTELLIGENT : Il ne s'énerve que si c'est grave
    prompt = f"""
    Analyse ce mail de {sender} : "{subject}".
    Contenu : "{text[:300]}..."
    
    Règles :
    1. Si livré (Delivered), Expédié, En route, Avis -> SAFE (Vert).
    2. Si Retard, Perdu, Annulé, Remboursement, Problème -> DANGER (Rouge).
    
    Réponds UNIQUEMENT : MONTANT | STATUT | RISQUE
    """
    try:
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], max_tokens=50)
        parts = response.choices[0].message.content.strip().split("|")
        if len(parts) == 3: return {"amount": parts[0].strip(), "status": parts[1].strip(), "color": "red" if "DANGER" in parts[2] else "green"}
        return {"amount": "?", "status": "Inconnu", "color": "gray"}
    except: return {"amount": "Err", "status": "Erreur IA", "color": "gray"}

def generate_agency_email(text, subject, sender, user_name):
    client = OpenAI(api_key=OPENAI_API_KEY)
    case_number = random.randint(10000, 99999)
    prompt = f"Tu es le SERVICE JURIDIQUE JUSTICIO. Rédige une MISE EN DEMEURE pour le client '{user_name}' contre '{sender}'. Sujet: '{subject}'. Contexte: '{text[:500]}'. Ton autoritaire. Cite Art L.216-1 Conso. Exige remboursement. Signature: 'SERVICE CONTENTIEUX JUSTICIO, Dossier #{case_number}, Mandataire de {user_name}'. Pas de Markdown."
    response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], max_tokens=600)
    return response.choices[0].message.content.replace("```", "").strip()

def send_email_directly(service, user_email, to_email, subject, body):
    official_subject = f"MISE EN DEMEURE - DOSSIER JUSTICIO (Réf: {subject})"
    message = MIMEText(body)
    message['to'] = to_email
    message['from'] = user_email
    message['subject'] = official_subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={'raw': raw}).execute()

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" in session:
        return STYLE + f"""
        <div class='container'>
            <h1>⚖️ JUSTICIO</h1>
            <p class='subtext'>Bienvenue, <strong>{session.get('name', 'Utilisateur')}</strong></p>
            
            <div class='card' style='text-align: center; border: 2px solid var(--primary);'>
                <h3>Analyse de Recouvrement</h3>
                <p style='color: #64748b; margin-bottom: 25px;'>
                    Scannez vos emails pour détecter les litiges éligibles à une indemnisation.
                </p>
                <a href='/scan'><button class='btn btn-primary'>🚀 LANCER LE SCAN</button></a>
            </div>
            
            <div style='text-align: center; margin-top: 20px;'>
                <a href='/logout' style='color: #94a3b8; text-decoration: none; font-size: 0.9rem;'>Se déconnecter</a>
            </div>
        </div>
        """
    return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    try:
        credentials = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=credentials)
        results = service.users().messages().list(userId='me', q="subject:(Uber OR Amazon OR SNCF OR Temu OR Facture OR Commande)", maxResults=10).execute()
        messages = results.get('messages', [])
        
        if not messages: return STYLE + "<div class='container'><h1>Aucun email trouvé</h1><a href='/'><button class='btn btn-primary'>Retour</button></a></div>"

        html = STYLE + "<div class='container'><h1>📂 Résultats du Scan</h1><p class='subtext'>Seuls les dossiers à risque permettent une action.</p>"
        
        for msg in messages:
            full = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            snippet = full.get('snippet', '')
            analysis = analyze_with_ai(snippet, subject, sender)
            
            # --- LOGIQUE PROPRE (Pas de triche) ---
            action_html = ""
            if analysis['color'] == "red":
                # Le bouton n'apparaît QUE si l'IA détecte un danger
                action_html = f"""
                <div style='margin-top: 20px;'>
                    <a href='/auto_send/{msg['id']}'>
                        <button class='btn btn-danger'>⚡ ACTIVER PROTECTION JURIDIQUE</button>
                    </a>
                </div>
                """
            else:
                action_html = "<div style='margin-top: 15px; color: var(--success); font-size: 0.9rem; font-weight: 500;'>✅ Commande conforme</div>"
            
            card_class = "card-danger" if analysis['color'] == "red" else "card-safe"
            
            html += f"""
            <div class='card {card_class}'>
                <h3>{subject}</h3>
                <div class='meta'>Vendeur : {sender}</div>
                <div style='margin-bottom: 10px;'>
                    <span class='status-badge badge-money'>💰 {analysis['amount']}</span>
                    <span class='status-badge badge-info'>📝 {analysis['status']}</span>
                </div>
                {action_html}
            </div>
            """
        html += "<div style='height: 50px;'></div><a href='/'><button class='btn' style='background:#e2e8f0; color:#475569;'>Retour</button></a></div>"
        return html
    except Exception as e: return f"Erreur: {e} <a href='/logout'>Reset</a>"

@app.route("/auto_send/<msg_id>")
def auto_send(msg_id):
    if "credentials" not in session: return redirect("/login")
    credentials = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=credentials)
    
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = msg['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
    sender_email = next((h['value'] for h in headers if h['name'] == 'From'), '')
    if "<" in sender_email: sender_email = sender_email.split("<")[1].replace(">", "")
    snippet = msg.get('snippet', '')
    
    legal_body = generate_agency_email(snippet, subject, sender_email, session.get('name', 'Client'))
    
    try:
        send_email_directly(service, "me", sender_email, subject, legal_body)
        return STYLE + f"""
        <div class='container' style='text-align: center; margin-top: 80px;'>
            <div style='font-size: 4rem; margin-bottom: 20px;'>⚖️</div>
            <h1 style='color: var(--primary);'>Dossier Transmis</h1>
            <div class='card'>
                <p><strong>Le Service Contentieux Justicio a pris le relais.</strong></p>
                <p>La mise en demeure officielle a été envoyée à <br><strong>{sender_email}</strong>.</p>
                <p class='meta'>Vous recevrez leur réponse directement
