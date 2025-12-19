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

# --- DESIGN "GLASSMORPHISM" (MODERNE) ---
STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;700;800&display=swap');

:root {
    --primary: #6366f1;
    --danger: #f43f5e;
    --success: #10b981;
    --bg-gradient: linear-gradient(135deg, #fdfbfb 0%, #ebedee 100%);
}

body {
    font-family: 'Outfit', sans-serif;
    background: #f3f4f6; /* Fond gris clair propre */
    background-image: radial-gradient(at 0% 0%, hsla(253,16%,7%,1) 0, transparent 50%), radial-gradient(at 50% 0%, hsla(225,39%,30%,1) 0, transparent 50%), radial-gradient(at 100% 0%, hsla(339,49%,30%,1) 0, transparent 50%);
    background-size: cover;
    background-attachment: fixed;
    margin: 0;
    padding: 20px;
    color: #1e293b;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
}

.container {
    width: 100%;
    max-width: 600px;
    margin-top: 40px;
    animation: slideUp 0.6s cubic-bezier(0.2, 0.8, 0.2, 1);
}

h1 {
    font-weight: 800;
    font-size: 3rem;
    text-align: center;
    margin-bottom: 10px;
    color: white;
    text-shadow: 0 4px 10px rgba(0,0,0,0.3);
}

p.subtext {
    text-align: center;
    color: #cbd5e1;
    margin-bottom: 30px;
    font-size: 1.1rem;
}

.card {
    background: rgba(255, 255, 255, 0.95);
    border-radius: 24px;
    padding: 25px;
    margin-bottom: 25px;
    box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
    transition: transform 0.2s ease;
}

.card:hover { transform: translateY(-5px); }

.card-danger { border-left: 8px solid var(--danger); }
.card-safe { border-left: 8px solid var(--success); }

h3 { margin: 0 0 5px 0; font-size: 1.3rem; font-weight: 700; color: #1e293b; }

.meta { font-size: 0.95rem; color: #64748b; margin-bottom: 15px; font-weight: 500; }

.badge {
    display: inline-flex;
    padding: 8px 14px;
    border-radius: 12px;
    font-size: 0.85rem;
    font-weight: 700;
    margin-right: 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.badge-money { background: #ecfdf5; color: #047857; }
.badge-status { background: #f1f5f9; color: #475569; }

.btn {
    display: block;
    width: 100%;
    padding: 18px;
    border-radius: 16px;
    font-weight: 700;
    text-align: center;
    text-decoration: none;
    border: none;
    cursor: pointer;
    font-size: 1.1rem;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
    transition: transform 0.1s;
}
.btn:active { transform: scale(0.98); }

.btn-primary { background: #6366f1; color: white; }
.btn-danger { 
    background: #e11d48; 
    color: white; 
    margin-top: 15px;
    animation: pulse 2s infinite;
}

@keyframes slideUp { from { opacity: 0; transform: translateY(50px); } to { opacity: 1; transform: translateY(0); } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0.7); } 70% { box-shadow: 0 0 0 10px rgba(225, 29, 72, 0); } 100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0); } }
</style>
"""

def credentials_to_dict(credentials):
    return {'token': credentials.token, 'refresh_token': credentials.refresh_token, 'token_uri': credentials.token_uri, 'client_id': credentials.client_id, 'client_secret': credentials.client_secret, 'scopes': credentials.scopes}

# --- IA : MODE "PITBULL" (TRÈS AGRESSIF SUR LES PROBLÈMES) ---
def analyze_with_ai(text, subject, sender):
    if not OPENAI_API_KEY: return {"amount": "?", "status": "Pas de clé", "color": "gray"}
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # PROMPT CORRIGÉ POUR FORCER LE ROUGE SUR AMAZON
    prompt = f"""
    Analyse ce mail.
    Sujet : "{subject}"
    Expéditeur : "{sender}"
    Début du texte : "{text[:300]}..."
    
    RÈGLES IMPÉRATIVES :
    1. Regarde le SUJET en premier. Si le sujet contient les mots : "Problème", "Réclamation", "Erreur", "Action requise" -> C'est DANGER (Rouge).
    2. Si le texte parle de "non reçu", "volé", "remboursement", "retard" -> C'est DANGER (Rouge).
    3. Si c'est juste "Livré", "Expédié", "Confirmé" -> C'est SAFE (Vert).
    
    Réponds UNIQUEMENT sous la forme : MONTANT | STATUT | RISQUE
    Exemple : 25€ | Colis perdu | DANGER
    Exemple : 0€ | Livré | SAFE
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
    prompt = f"Tu es le SERVICE JURIDIQUE JUSTICIO. Rédige MISE EN DEMEURE pour '{user_name}' contre '{sender}'. Sujet: '{subject}'. Contexte: '{text[:500]}'. Ton menaçant et juridique. Cite Art L.216-1 Code Conso. Exige remboursement immédiat. Signe: 'SERVICE CONTENTIEUX JUSTICIO, Dossier #{case_num}, Mandataire de {user_name}'. Pas de Markdown."
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
            <p class='subtext'>Bonjour <strong>{session.get('name', 'Utilisateur')}</strong>.</p>
            
            <div class='card' style='text-align:center;'>
                <div style='font-size: 4rem; margin-bottom: 10px;'>🛡️</div>
                <h3>Scan de Recouvrement</h3>
                <p style='color:#64748b; margin-bottom:25px;'>Détectez les anomalies et récupérez votre argent.</p>
                <a href='/scan'><button class='btn btn-primary'>🚀 LANCER L'ANALYSE</button></a>
            </div>
            
            <div style='text-align:center; margin-top:20px;'><a href='/logout' style='color:#cbd5e1; text-decoration:none;'>Se déconnecter</a></div>
        </div>
        """
    return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    try:
        credentials = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=credentials)
        
        # --- CORRECTIF MAJEUR ICI : "label:INBOX" ---
        # On ne cherche que dans la boîte de réception (pas les envoyés)
        query = "label:INBOX subject:(Uber OR Amazon OR SNCF OR Temu OR Facture OR Commande OR Problème)"
        
        results = service.users().messages().list(userId='me', q=query, maxResults=12).execute()
        messages = results.get('messages', [])
        
        if not messages: return STYLE + "<div class='container'><h1>Rien trouvé</h1><p class='subtext'>Aucun email correspondant dans la boîte de réception.</p><a href='/'><button class='btn btn-primary'>Retour</button></a></div>"

        html = STYLE + "<div class='container'><h1>📂 Dossiers Détectés</h1><p class='subtext'>Analyse IA terminée.</p>"
        for msg in messages:
            full = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            
            # Nettoyage affichage expéditeur
            if "<" in sender: sender_clean = sender.split("<")[0].replace('"', '')
            else: sender_clean = sender

            snippet = full.get('snippet', '')
            analysis = analyze_with_ai(snippet, subject, sender)
            
            action_html = ""
            # Si Rouge -> Bouton d'attaque
            if analysis['color'] == "red":
                action_html = f"<a href='/auto_send/{msg['id']}'><button class='btn btn-danger'>⚡ ACTIVER LA PROTECTION</button></a>"
            else:
                action_html = "<div style='text-align:center; color:#10b981; margin-top:15px; font-weight:700;'>✅ AUCUN LITIGE</div>"
            
            card_class = "card-danger" if analysis['color'] == "red" else "card-safe"
            html += f"""
            <div class='card {card_class}'>
                <h3>{subject}</h3>
                <div class='meta'>Vendeur : {sender_clean}</div>
                <div>
                    <span class='badge badge-money'>💰 {analysis['amount']}</span>
                    <span class='badge badge-status'>📝 {analysis['status']}</span>
                </div>
                {action_html}
            </div>
            """
        html += "<a href='/'><button class='btn' style='background:#e2e8f0; color:#475569; margin-top:20px;'>Retour</button></a></div>"
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
        <div class='container' style='text-align:center;'>
            <div style='font-size: 5rem; margin-bottom: 20px;'>⚖️</div>
            <h1 style='color:white;'>Procédure Lancée</h1>
            <div class='card'>
                <p><strong>Le Service Contentieux a pris le relais.</strong></p>
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
