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

def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

# --- 1. LE CERVEAU ---
def analyze_with_ai(text, subject, sender):
    if not OPENAI_API_KEY: return {"amount": "?", "status": "Pas de clé", "color": "gray"}
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""
    Tu es expert en litiges e-commerce. Analyse ce mail de {sender} : "{subject}".
    Contenu : "{text[:300]}..."
    
    Règles strictes :
    1. Si tout va bien (Livré, Expédié) -> SAFE (Vert).
    2. Si Problème (Retard > 3 jours, Annulé, Perdu, Remboursement refusé) -> DANGER (Rouge).
    
    Réponds UNIQUEMENT : MONTANT | STATUT | RISQUE
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

# --- 2. LA PLUME "AGENCE JURIDIQUE" ---
def generate_agency_email(text, subject, sender, user_name):
    client = OpenAI(api_key=OPENAI_API_KEY)
    case_number = random.randint(10000, 99999) # Numéro de dossier fictif
    
    prompt = f"""
    Tu es le "SERVICE JURIDIQUE DE JUSTICIO".
    Tu interviens pour protéger ton client "{user_name}" face au vendeur "{sender}".
    
    Sujet original du litige : "{subject}"
    Contexte : "{text[:500]}..."
    
    Ta mission : Rédiger une MISE EN DEMEURE formelle.
    
    Consignes de rédaction :
    - Ton : Froid, Autoritaire, Juridique.
    - Ne dis pas "Je suis mécontent". Dis "Nous constatons un manquement contractuel".
    - Cite l'Article L.216-1 du Code de la consommation.
    - Exige un remboursement immédiat.
    - SIGNATURE OBLIGATOIRE :
      "SERVICE CONTENTIEUX JUSTICIO
      Dossier Réf : #{case_number}
      Intervenant pour le compte de M./Mme {user_name}"
    
    Pas de Markdown. Texte brut uniquement.
    """
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600
    )
    return response.choices[0].message.content.replace("```", "").strip()

# --- 3. L'ENVOI ---
def send_email_directly(service, user_email, to_email, subject, body):
    official_subject = f"MISE EN DEMEURE - DOSSIER JUSTICIO (Réf: {subject})"
    
    message = MIMEText(body)
    message['to'] = to_email
    message['from'] = user_email
    message['subject'] = official_subject
    
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {'raw': raw}
    
    sent = service.users().messages().send(userId="me", body=body).execute()
    return sent

# --- ROUTES ---
@app.route("/")
def index():
    if "credentials" in session:
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
            <h1>⚖️ JUSTICIO</h1>
            <p style='color: green;'>✅ Agent connecté : {session.get('name', 'Spy One')}</p>
            <br>
            <div style='background: #e3f2fd; padding: 30px; border-radius: 15px; display: inline-block; border: 2px solid #2196f3;'>
                <h3>AGENCE DE RECOUVREMENT IA 🤖</h3>
                <p>Mode Test Activé : Boutons forcés.</p>
                <a href='/scan'><button style='padding: 15px 30px; font-size: 20px; background-color: #0d47a1; color: white; border: none; border-radius: 8px; cursor: pointer;'>🚔 LANCER LA PROCÉDURE</button></a>
            </div>
            <br><br><a href='/logout'>Déconnexion</a>
        </div>
        """
    return redirect("/login")

@app.route("/scan")
def scan_emails():
    if "credentials" not in session: return redirect("/login")
    try:
        credentials = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=credentials)
        
        results = service.users().messages().list(userId='me', q="subject:(Uber OR Amazon OR SNCF OR Temu OR Facture)", maxResults=8).execute()
        messages = results.get('messages', [])
        
        if not messages: return "<h1>Rien trouvé !</h1><a href='/'>Retour</a>"

        html = "<div style='font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f4f6f8;'>"
        html += "<h1 style='text-align:center'>📂 Dossiers en cours</h1>"
        
        for msg in messages:
            full = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            snippet = full.get('snippet', '')
            
            analysis = analyze_with_ai(snippet, subject, sender)
            
            # --- CHEAT CODE : ON FORCE LE BOUTON ROUGE ---
            if True: # On force l'affichage du bouton pour tester
                action_button = f"""
                <div style='margin-top: 15px;'>
                    <a href='/auto_send/{msg['id']}' style='text-decoration: none;'>
                        <button style='background: #b71c1c; color: white; border: none; padding: 15px 30px; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 16px; box-shadow: 0 4px 0 #7f0000;'>
                            ⚡ ACTIVER PROTECTION JURIDIQUE
                        </button>
                    </a>
                </div>
                """
            else:
                action_button = ""

            html += f"""
            <div style='background: white; margin-bottom: 20px; padding: 20px; border-radius: 10px; border-left: 5px solid {analysis['color']}; box-shadow: 0 2px 5px rgba(0,0,0,0.1);'>
                <h3>{subject}</h3>
                <p style='color: #666;'>Contre : {sender}</p>
                <div style='margin: 10px 0;'>
                    <span style='background: #eee; padding: 5px 10px; border-radius: 5px;'>💰 {analysis['amount']}</span>
                    <span style='background: #eee; padding: 5px 10px; border-radius: 5px;'>📝 {analysis['status']}</span>
                </div>
                {action_button}
            </div>
            """
        html += "<center><a href='/'>Retour</a></center></div>"
        return html
    except Exception as e:
        return f"<h1>Erreur</h1><p>{e}</p><a href='/logout'>Se reconnecter</a>"

@app.route("/auto_send/<msg_id>")
def auto_send(msg_id):
    if "credentials" not in session: return redirect("/login")
    credentials = Credentials(**session["credentials"])
    service = build('gmail', 'v1', credentials=credentials)
    
    # Infos
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = msg['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
    sender_email = next((h['value'] for h in headers if h['name'] == 'From'), '')
    if "<" in sender_email: sender_email = sender_email.split("<")[1].replace(">", "")
    snippet = msg.get('snippet', '')
    
    # Génération Agence
    legal_body = generate_agency_email(snippet, subject, sender_email, session.get('name', 'Client'))
    
    try:
        send_email_directly(service, "me", sender_email, subject, legal_body)
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 100px;'>
            <h1 style='color: #0d47a1; font-size: 3em;'>⚖️ PROCÉDURE LANCÉE</h1>
            <div style='background: #e3f2fd; padding: 40px; max-width: 600px; margin: 0 auto; border-radius: 20px; border: 2px solid #0d47a1;'>
                <h2>Justicio a pris le relais.</h2>
                <p style='font-size: 1.2em;'>Une mise en demeure officielle a été envoyée à <strong>{sender_email}</strong>.</p>
                <p>Signature : <em>Service Contentieux Justicio</em></p>
                <br>
                <a href='/scan'>
                    <button style='padding: 15px 30px; background-color: #333; color: white; border: none; font-size: 18px; cursor: pointer; border-radius: 50px;'>
                        📂 Retour aux dossiers
                    </button>
                </a>
            </div>
        </div>
        """
    except Exception as e:
        return f"Erreur envoi : {e}"

# --- AUTH ---
@app.route("/login")
def login():
    redirect_uri = url_for('callback', _external=True).replace("http://", "https://")
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type='offline', include_granted_scopes='true', prompt='consent'
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
