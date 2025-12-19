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

# --- DESIGN V2 (Moderne & Propre) ---
STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;700&display=swap');
:root { --primary: #4f46e5; --danger: #e11d48; --success: #059669; --bg: #f8fafc; }
body { font-family: 'Outfit', sans-serif; background: linear-gradient(135deg, #f0f9ff 0%, #e0e7ff 100%); margin: 0; padding: 20px; color: #1e293b; min-height: 100vh; display: flex; flex-direction: column; align-items: center; }
.container { width: 100%; max-width: 600px; animation: slideUp 0.8s; }
h1 { font-weight: 800; font-size: 2.5rem; text-align: center; background: linear-gradient(to right, #4f46e5, #ec4899); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 5px; }
.card { background: rgba(255,255,255,0.95); border-radius: 20px; padding: 25px; margin-bottom: 20px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #fff; }
.card-danger { border-left: 6px solid var(--danger); }
.card-safe { border-left: 6px solid var(--success); }
.btn { display: block; width: 100%; padding: 15px; border-radius: 12px; font-weight: 700; text-align: center; text-decoration: none; border: none; cursor: pointer; color: white; margin-top: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
.btn-primary { background: linear-gradient(135deg, #4f46e5 0%, #4338ca 100%); }
.btn-danger { background: linear-gradient(135deg, #e11d48 0%, #be123c 100%); }
.badge { display: inline-block; padding: 5px 10px; border-radius: 10px; font-size: 0.8rem; margin-right: 5px; background: #f1f5f9; font-weight: 600; }
@keyframes slideUp { from { opacity: 0; transform: translateY(40px); } to { opacity: 1; transform: translateY(0); } }
</style>
"""

def credentials_to_dict(credentials):
    return {'token': credentials.token, 'refresh_token': credentials.refresh_token, 'token_uri': credentials.token_uri, 'client_id': credentials.client_id, 'client_secret': credentials.client_secret, 'scopes': credentials.scopes}

# --- CERVEAU IA (STRICT) ---
def analyze_with_ai(text, subject, sender):
    if not OPENAI_API_KEY: return {"amount": "?", "status": "Pas de clé", "color": "gray"}
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # PROMPT CORRIGÉ : Il force le ROUGE sur le mot "Problème"
    prompt = f"""
    Analyse ce mail : "{subject}" de "{sender}".
    Contenu : "{text[:300]}..."
    
    RÈGLES ABSOLUES :
    1. Si le sujet ou le texte contient "Problème", "Réclamation", "Jamais reçu", "Vol" -> C'est DANGER (Rouge).
    2. Si le sujet dit "Bientôt", "Arrive", "En cours", "Livré" -> C'est SAFE (Vert).
    
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
    case_num = random.randint(10000, 99999)
    prompt = f"Tu es le SERVICE JURIDIQUE JUSTICIO. Rédige MISE EN DEMEURE pour '{user_name}' contre '{sender}'. Sujet: '{subject}'. Contexte: '{text[:500]}'. Ton autoritaire. Cite Art L.216-1. Exige remboursement. Signe: 'SERVICE CONTENTIEUX JUSTICIO, Dossier #{case_num}, Mandataire de {user_name}'. Pas de Markdown."
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
            <p style='text-align:center; color:#64748b;'>Bonjour <strong>{session.get('name', 'Utilisateur')}</strong></p>
            <div class='card' style='text-align:center; border: 2px solid #4f46e5;'>
                <h3>Scan de Recouvrement</h3>
                <p>Détecter les litiges dans la boîte de réception.</p>
                <a href='/scan'><button class='btn btn-primary'>🚀 LANCER LE SCAN</button></a>
            </div>
            <div style='text-align:center;'><a href='/logout' style='color:#94a3b8;'>Déconnexion</a></div>
        </div>
        """
    return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    try:
        credentials = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=credentials)
        
        # ICI : "label:INBOX" force à ne regarder que les messages REÇUS
        # On exclut "label:SENT" implicitement
        query = "label:INBOX subject:(Uber OR Amazon OR SNCF OR Temu OR Facture OR Commande OR Problème)"
        
        results = service.users().messages().list(userId='me', q=query, maxResults=12).execute()
        messages = results.get('messages', [])
        
        if not messages: return STYLE + "<div class='container'><h1>Rien trouvé</h1><a href='/'><button class='btn btn-primary'>Retour</button></a></div>"

        html = STYLE + "<div class='container'><h1>📂 Résultats</h1>"
        for msg in messages:
            full = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            # Nettoyage du nom sender
            if "<" in sender: sender = sender.split("<")[0].replace('"', '').strip()

            snippet = full.get('snippet', '')
            analysis = analyze_with_ai(snippet, subject, sender)
            
            action_html = ""
            # LOGIQUE STRICTE : Bouton seulement si ROUGE
            if analysis['color'] == "red":
                action_html = f"<a href='/auto_send/{msg['id']}'><button class='btn btn-danger'>⚡ RÉCLAMER {analysis['amount']}</button></a>"
                status_icon = "⚠️"
            else:
                action_html = "<div style='text-align:center; color:#059669; margin-top:10px;'>✅ Commande conforme</div>"
                status_icon = "✅"
            
            card_class = "card-danger" if analysis['color'] == "red" else "card-safe"
            html += f"""
            <div class='card {card_class}'>
                <h3>{status_icon} {subject}</h3>
                <div style='color:#64748b; font-size:0.9rem; margin-bottom:10px;'>{sender}</div>
                <div><span class='badge'>💰 {analysis['amount']}</span><span class='badge'>📝 {analysis['status']}</span></div>
                {action_html}
            </div>
            """
        html += "<a href='/'><button class='btn' style='background:#cbd5e1; color:#475569;'>Retour</button></a></div>"
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
    sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
    if "<" in sender: sender = sender.split("<")[1].replace(">", "")
    snippet = msg.get('snippet', '')
    
    legal_body = generate_agency_email(snippet, subject, sender, session.get('name', 'Client'))
    
    try:
        send_email_directly(service, "me", sender, subject, legal_body)
        return STYLE + f"""
        <div class='container' style='text-align:center; margin-top:50px;'>
            <h1 style='color:#4f46e5;'>Action Effectuée</h1>
            <div class='card'>
                <p><strong>Justicio a pris le relais.</strong></p>
                <p>Mise en demeure envoyée à :<br><strong>{sender}</strong></p>
            </div>
            <a href='/scan'><button class='btn btn-primary'>Retour aux dossiers</button></a>
        </div>
        """
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
    session["credentials"] = credentials_to_dict(flow.credentials)
    session["name"] = "Spy One" 
    return redirect("/")

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

if __name__ == "__main__": app.run(debug=True)
