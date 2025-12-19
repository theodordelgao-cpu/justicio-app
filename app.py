import os
import re
import base64
from flask import Flask, session, redirect, request, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from openai import OpenAI

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
    "openid"
]

def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

# --- FONCTION 1 : L'ANALYSTE (Scan le mail) ---
def analyze_with_ai(text, subject, sender):
    if not OPENAI_API_KEY: return {"amount": "?", "status": "Pas de clé", "color": "gray"}
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""
    Analyse ce mail de {sender} : "{subject}".
    Contenu : "{text[:300]}..."
    
    Réponds UNIQUEMENT au format : MONTANT | STATUT | RISQUE
    Exemple: 25.00€ | Livré | SAFE
    Si tu ne trouves pas le prix, mets "Inconnu".
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        parts = response.choices[0].message.content.strip().split("|")
        if len(parts) == 3:
            return {"amount": parts[0].strip(), "status": parts[1].strip(), "color": "red" if "DANGER" in parts[2] else "green"}
        return {"amount": "?", "status": "Info manquante", "color": "gray"}
    except:
        return {"amount": "Err", "status": "Erreur IA", "color": "gray"}

# --- FONCTION 2 : L'AVOCAT (Rédige la lettre) ---
def generate_legal_letter(text, subject, sender, user_name):
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""
    Agis comme un avocat français spécialiste du droit de la consommation.
    Rédige une lettre de réclamation formelle (Mise en demeure) pour le client "{user_name}" à l'encontre de "{sender}".
    
    Contexte du mail reçu : "{subject}"
    Contenu du mail : "{text[:500]}..."
    
    La lettre doit :
    1. Être menaçante mais polie et professionnelle.
    2. Citer le Code de la Consommation (ex: Article L.216-1 pour retard de livraison, ou garantie de conformité).
    3. Exiger un remboursement ou une action immédiate sous 7 jours.
    4. Être au format HTML propre (avec des <p> et <br>).
    """
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600
    )
    return response.choices[0].message.content.strip()

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" in session:
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
            <h1>⚖️ JUSTICIO</h1>
            <p style='color: green;'>✅ Connecté : {session.get('name', 'Spy One')}</p>
            <br>
            <div style='background: #e3f2fd; padding: 30px; border-radius: 15px; display: inline-block; border: 2px solid #2196f3;'>
                <h3>MODE AVOCAT ACTIVÉ 👨‍⚖️</h3>
                <p>Scanner les mails et générer des plaintes.</p>
                <a href='/scan'><button style='padding: 15px 30px; font-size: 20px; background-color: #1565c0; color: white; border: none; border-radius: 8px; cursor: pointer;'>🔍 LANCER LE SCAN</button></a>
            </div>
            <br><br><a href='/logout'>Déconnexion</a>
        </div>
        """
    return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    
    # C'est ici que ça plantait avant si le refresh_token manquait
    try:
        credentials = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=credentials)
        
        results = service.users().messages().list(userId='me', q="subject:(Uber OR Amazon OR SNCF OR Temu OR Facture)", maxResults=8).execute()
        messages = results.get('messages', [])
        
        if not messages: return "<h1>Rien trouvé !</h1><a href='/'>Retour</a>"

        html = "<div style='font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f4f6f8;'>"
        html += "<h1 style='text-align:center'>📋 Dossiers Détectés</h1>"
        
        for msg in messages:
            full = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            snippet = full.get('snippet', '')
            
            analysis = analyze_with_ai(snippet, subject, sender)
            
            html += f"""
            <div style='background: white; margin-bottom: 20px; padding: 20px; border-radius: 10px; border-left: 5px solid {analysis['color']}; box-shadow: 0 2px 5px rgba(0,0,0,0.1);'>
                <h3>{subject}</h3>
                <p style='color: #666;'>De : {sender}</p>
                <div style='margin: 10px 0;'>
                    <span style='background: #eee; padding: 5px 10px; border-radius: 5px;'>💰 {analysis['amount']}</span>
                    <span style='background: #eee; padding: 5px 10px; border-radius: 5px;'>📝 {analysis['status']}</span>
                </div>
                <div style='margin-top: 15px;'>
                    <a href='/complain/{msg['id']}' style='text-decoration: none;'>
                        <button style='background: #d32f2f; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: bold;'>
                            ⚖️ GÉNÉRER PLAINTE
                        </button>
                    </a>
                </div>
            </div>
            """
        html += "<center><a href='/'>Retour</a></center></div>"
        return html
    except Exception as e:
        # En cas d'erreur de token, on force la déconnexion
        return f"<h1>Erreur de connexion</h1><p>Google demande de se reconnecter pour valider la sécurité.</p><a href='/logout'><button>Se reconnecter</button></a><br><br><small>Erreur technique : {e}</small>"

@app.route("/complain/<msg_id>")
def complain(msg_id):
    if "credentials" not in session: return redirect("/login")
    credentials = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=credentials)
    
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = msg['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
    snippet = msg.get('snippet', '')
    
    letter = generate_legal_letter(snippet, subject, sender, session.get('name', 'Client'))
    
    return f"""
    <div style='font-family: serif; max-width: 700px; margin: 0 auto; padding: 40px; background: white; border: 1px solid #ccc; margin-top: 30px; box-shadow: 0 0 20px rgba(0,0,0,0.1);'>
        <h2 style='text-align: center; text-decoration: underline;'>MISE EN DEMEURE</h2>
        <div style='white-space: pre-wrap; line-height: 1.6;'>{letter}</div>
        <br><br>
        <hr>
        <div style='text-align: center;'>
            <button onclick="navigator.clipboard.writeText(document.querySelector('div').innerText); alert('Copié !')" style='padding: 15px 30px; background: #28a745; color: white; border: none; cursor: pointer; font-size: 16px; border-radius: 5px;'>
                📋 COPIER LA LETTRE
            </button>
            <br><br>
            <a href='/scan' style='color: #666;'>Retour aux dossiers</a>
        </div>
    </div>
    """

# --- AUTH & SYSTEM ---
@app.route("/login")
def login():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    
    # C'EST ICI LA CORRECTION MAGIQUE :
    # On force 'offline' pour avoir le refresh_token
    # On force 'consent' pour que Google nous le donne à coup sûr
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    session["state"] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(authorization_response=request.url)
    session["credentials"] = credentials_to_dict(flow.credentials)
    session["name"] = "Spy One"
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
