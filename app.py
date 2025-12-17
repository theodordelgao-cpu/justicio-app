import os
import re
import base64
from flask import Flask, session, redirect, request, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup  # Notre nouvel outil de nettoyage

app = Flask(__name__)

# --- CONFIGURATION ---
app.secret_key = os.environ.get("SECRET_KEY", "azerty_super_secret_key")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
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

# --- FONCTION INTELLIGENTE : EXTRACTEUR DE PRIX ---
def extract_money(text):
    # On cherche des motifs comme "25.00€", "25 €", "25,00 EUR"
    pattern = r"(\d+[.,]\d{2})\s?(€|EUR)"
    match = re.search(pattern, text)
    if match:
        return match.group(0) # On retourne "25.00€"
    return None

# --- ACCUEIL ---
@app.route("/")
def index():
    if "credentials" in session:
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px; color: #333;'>
            <h1>⚖️ JUSTICIO</h1>
            <p style='color: green; font-weight: bold;'>✅ Connecté : {session.get('name', 'Spy One')}</p>
            <br>
            <div style='background: #e8f5e9; padding: 30px; border-radius: 15px; display: inline-block; border: 2px solid #4caf50;'>
                <h3>🕵️‍♂️ Prêt à trouver l'argent ?</h3>
                <p>Le robot va analyser les montants et les retards.</p>
                <a href='/scan'>
                    <button style='padding: 15px 30px; font-size: 20px; background-color: #2e7d32; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold;'>
                        LANCER L'ANALYSE FINANCIÈRE 💰
                    </button>
                </a>
            </div>
            <br><br>
            <a href='/logout' style='color: #888;'>Déconnexion</a>
        </div>
        """
    else:
        return redirect("/login")

# --- LE SCANNEUR (VERSION COMPTABLE) ---
@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    credentials = Credentials(**session["credentials"])
    
    try:
        service = build('gmail', 'v1', credentials=credentials)
        
        # On cherche Temu, Uber, Amazon...
        query = "subject:(Uber OR Amazon OR SNCF OR Temu OR Facture)"
        results = service.users().messages().list(userId='me', q=query, maxResults=10).execute()
        messages = results.get('messages', [])

        if not messages: return "<h1>Rien trouvé !</h1><a href='/'>Retour</a>"

        html_content = "<div style='font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f9f9f9;'>"
        html_content += "<h1 style='text-align: center;'>💸 Analyse des Dépenses</h1>"
        html_content += "<ul style='list-style: none; padding: 0;'>"

        for msg in messages:
            # On récupère le mail COMPLET (pas juste l'aperçu)
            full_msg = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            
            headers = full_msg['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sans objet')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Inconnu')

            # DÉCODAGE DU CORPS DU MAIL (C'est technique !)
            body_text = ""
            if 'parts' in full_msg['payload']:
                for part in full_msg['payload']['parts']:
                    if part['mimeType'] == 'text/plain' or part['mimeType'] == 'text/html':
                        data = part['body'].get('data')
                        if data:
                            decoded_bytes = base64.urlsafe_b64decode(data)
                            body_text += decoded_bytes.decode('utf-8', errors='ignore')
            elif 'body' in full_msg['payload']:
                data = full_msg['payload']['body'].get('data')
                if data:
                    decoded_bytes = base64.urlsafe_b64decode(data)
                    body_text = decoded_bytes.decode('utf-8', errors='ignore')

            # NETTOYAGE (On enlève le HTML moche)
            clean_text = BeautifulSoup(body_text, "html.parser").get_text()
            
            # ANALYSE (Le cerveau cherche l'argent)
            price = extract_money(clean_text)
            
            # DETECTION DE PROBLÈMES (Mots clés)
            is_problem = "retard" in clean_text.lower() or "remboursement" in clean_text.lower()
            
            # --- AFFICHAGE DE LA CARTE ---
            border_color = "#e53935" if is_problem else "#43a047" # Rouge si problème, Vert sinon
            bg_color = "#ffebee" if is_problem else "#ffffff"
            
            html_content += f"""
            <li style='background: {bg_color}; margin-bottom: 15px; padding: 20px; border-radius: 10px; border-left: 8px solid {border_color}; box-shadow: 0 2px 5px rgba(0,0,0,0.1);'>
                <div style='font-weight: bold; font-size: 1.1em; color: #333;'>{subject}</div>
                <div style='color: #666; font-size: 0.9em; margin-bottom: 10px;'>De : {sender}</div>
                
                <div style='display: flex; gap: 10px; margin-top: 10px;'>
                    {'<span style="background: #c8e6c9; color: #2e7d32; padding: 5px 10px; border-radius: 20px; font-weight: bold;">💰 ' + price + '</span>' if price else ''}
                    {'<span style="background: #ffcdd2; color: #c62828; padding: 5px 10px; border-radius: 20px; font-weight: bold;">⚠️ Retard détecté</span>' if is_problem else ''}
                </div>
                
                <p style='color: #555; font-size: 0.85em; margin-top: 10px; font-style: italic;'>
                    "{clean_text[:150]}..."
                </p>
            </li>
            """

        html_content += "</ul><div style='text-align:center'><a href='/'><button style='padding:10px 20px;'>Retour</button></a></div></div>"
        return html_content
        
    except Exception as e:
        return f"Erreur : {e} <a href='/logout'>Reconnecter</a>"

# --- LOGIN & CALLBACK ---
@app.route("/login")
def login():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url()
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
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
