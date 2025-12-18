import os
import re
import base64
from flask import Flask, session, redirect, request, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from openai import OpenAI  # Le cerveau est là !

app = Flask(__name__)

# --- CONFIGURATION ---
app.secret_key = os.environ.get("SECRET_KEY", "azerty_super_secret_key")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
# On récupère la clé OpenAI qu'on a mise sur Render
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

# --- FONCTION INTELLIGENTE : L'IA GPT ---
def analyze_with_ai(text, subject, sender):
    # Si pas de clé, on renvoie une erreur silencieuse
    if not OPENAI_API_KEY:
        return {"amount": "Erreur Clé", "status": "Clé OpenAI manquante", "color": "gray"}

    client = OpenAI(api_key=OPENAI_API_KEY)

    # Le Prompt (L'ordre qu'on donne au robot)
    prompt = f"""
    Agis comme un expert comptable. Analyse cet email de {sender} avec le sujet "{subject}".
    Le contenu est : "{text[:500]}"...

    Tu dois extraire 3 informations précises :
    1. Le montant exact (ex: "25.50€") ou "0€" si aucun prix.
    2. Le statut en 3 mots max (ex: "Livré", "Remboursement Validé", "Retard de livraison", "Commande confirmée").
    3. Le niveau de risque : "DANGER" (si retard, problème, annulation) ou "SAFE" (si tout va bien).

    Réponds UNIQUEMENT sous ce format strict :
    MONTANT | STATUT | RISQUE
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", # Modèle rapide et pas cher
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        result = response.choices[0].message.content.strip()
        
        # On découpe la réponse (Montant | Statut | Risque)
        parts = result.split("|")
        if len(parts) == 3:
            return {
                "amount": parts[0].strip(),
                "status": parts[1].strip(),
                "color": "red" if "DANGER" in parts[2] else "green"
            }
        else:
            return {"amount": "?", "status": "Analyse incertaine", "color": "gray"}
            
    except Exception as e:
        print(f"Erreur IA: {e}")
        return {"amount": "Erreur", "status": "IA indisponible", "color": "gray"}

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" in session:
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px; color: #333;'>
            <h1>⚖️ JUSTICIO</h1>
            <p style='color: green; font-weight: bold;'>✅ Connecté : {session.get('name', 'Spy One')}</p>
            <br>
            <div style='background: #e3f2fd; padding: 30px; border-radius: 15px; display: inline-block; border: 2px solid #2196f3;'>
                <h3>🧠 IA CONNECTÉE</h3>
                <p>Analyse par Intelligence Artificielle (GPT-4o)</p>
                <a href='/scan'>
                    <button style='padding: 15px 30px; font-size: 20px; background-color: #1976d2; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold;'>
                        LANCER L'ANALYSE IA 🤖
                    </button>
                </a>
            </div>
            <br><br>
            <a href='/logout' style='color: #888;'>Déconnexion</a>
        </div>
        """
    else:
        return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    credentials = Credentials(**session["credentials"])
    
    try:
        service = build('gmail', 'v1', credentials=credentials)
        
        # On cherche un peu de tout pour tester l'IA
        query = "subject:(Uber OR Amazon OR SNCF OR Temu OR Facture)"
        results = service.users().messages().list(userId='me', q=query, maxResults=8).execute() # On limite à 8 pour économiser l'IA au début
        messages = results.get('messages', [])

        if not messages: return "<h1>Rien trouvé !</h1><a href='/'>Retour</a>"

        html_content = "<div style='font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f4f6f8;'>"
        html_content += "<h1 style='text-align: center;'>🤖 Rapport de l'Intelligence Artificielle</h1>"
        html_content += "<ul style='list-style: none; padding: 0;'>"

        for msg in messages:
            full_msg = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full_msg['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sans objet')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Inconnu')

            # Décodage
            body_text = ""
            if 'parts' in full_msg['payload']:
                for part in full_msg['payload']['parts']:
                    if part['mimeType'] == 'text/plain' or part['mimeType'] == 'text/html':
                        data = part['body'].get('data')
                        if data:
                            body_text += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            elif 'body' in full_msg['payload']:
                data = full_msg['payload']['body'].get('data')
                if data:
                    body_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

            # Nettoyage rapide
            soup = BeautifulSoup(body_text, "html.parser")
            for script in soup(["script", "style"]): script.extract()
            clean_text = " ".join(soup.get_text(separator=' ').split())
            
            # --- APPEL À L'IA ---
            # C'est ici qu'on paie l'IA pour réfléchir
            analysis = analyze_with_ai(clean_text, subject, sender)
            
            # Couleurs dynamiques
            border_color = "#d32f2f" if analysis['color'] == "red" else "#388e3c" # Rouge ou Vert
            bg_color = "#ffebee" if analysis['color'] == "red" else "#e8f5e9"
            badge_bg = "#ffcdd2" if analysis['color'] == "red" else "#c8e6c9"
            badge_text = "#b71c1c" if analysis['color'] == "red" else "#1b5e20"

            html_content += f"""
            <li style='background: {bg_color}; margin-bottom: 20px; padding: 20px; border-radius: 12px; border-left: 8px solid {border_color}; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>
                <div style='font-weight: bold; font-size: 1.2em; color: #333; margin-bottom: 5px;'>{subject}</div>
                <div style='color: #666; font-size: 0.9em; margin-bottom: 15px;'>De : {sender}</div>
                
                <div style='display: flex; gap: 15px; align-items: center;'>
                    <span style="background: {badge_bg}; color: {badge_text}; padding: 8px 15px; border-radius: 25px; font-weight: bold; font-size: 1.1em;">
                        💰 {analysis['amount']}
                    </span>
                    <span style="background: #fff; border: 1px solid {border_color}; color: {border_color}; padding: 8px 15px; border-radius: 25px; font-weight: bold;">
                        📝 {analysis['status']}
                    </span>
                </div>
            </li>
            """

        html_content += "</ul><div style='text-align:center'><a href='/'><button style='padding:12px 25px; background: #333; color: white; border: none; border-radius: 50px; cursor: pointer;'>Retour</button></a></div></div>"
        return html_content
        
    except Exception as e:
        return f"Erreur : {e} <a href='/logout'>Reconnecter</a>"

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
