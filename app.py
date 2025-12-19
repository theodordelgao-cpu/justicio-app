import os
import base64
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

# On garde les droits d'envoi
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

# --- 1. LE CERVEAU (ANALYSE) ---
def analyze_with_ai(text, subject, sender):
    if not OPENAI_API_KEY: return {"amount": "?", "status": "Pas de clé", "color": "gray"}
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Prompt strict pour éviter les erreurs
    prompt = f"""
    Analyse ce mail de {sender} : "{subject}".
    Contenu : "{text[:300]}..."
    
    Règles :
    1. Si livré/expédié/en route -> SAFE (Vert).
    2. Si Retard/Annulé/Remboursement/Problème -> DANGER (Rouge).
    
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

# --- 2. LA PLUME INVISIBLE (Génération du texte) ---
def generate_legal_text_hidden(text, subject, sender, user_name):
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""
    Agis comme un avocat pour le client "{user_name}".
    Écris un email de réclamation formelle à "{sender}".
    Sujet original : "{subject}"
    Contexte : "{text[:500]}..."
    
    Règles :
    - Sois poli mais ferme.
    - Cite l'article L.216-1 du Code de la consommation.
    - Demande un remboursement immédiat.
    - Signe "L'assistant juridique de {user_name} via Justicio".
    - Ne mets AUCUNE balise Markdown. Texte brut seulement.
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500
    )
    return response.choices[0].message.content.replace("```", "").strip()

# --- 3. L'ACTION (ENVOI DIRECT) ---
def send_email_directly(service, user_email, to_email, subject, body):
    message = MIMEText(body)
    message['to'] = to_email
    message['from'] = user_email
    message['subject'] = f"MISE EN DEMEURE - Réclamation ({subject})"
    
    # Encodage pour l'API Gmail
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {'raw': raw}
    
    # C'est ici que ça change : messages().send() au lieu de drafts().create()
    sent = service.users().messages().send(userId="me", body=body).execute()
    return sent

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
                <h3>MODE AUTOMATIQUE (INVISIBLE) 👻</h3>
                <p>On scanne, on détecte, on envoie.</p>
                <a href='/scan'><button style='padding: 15px 30px; font-size: 20px; background-color: #1565c0; color: white; border: none; border-radius: 8px; cursor: pointer;'>🔍 LANCER LE SCAN</button></a>
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
        html += "<h1 style='text-align:center'>⚡ Tableau de Bord</h1>"
        
        for msg in messages:
            full = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = full['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            snippet = full.get('snippet', '')
            
            analysis = analyze_with_ai(snippet, subject, sender)
            
            action_button = ""
            if analysis['color'] == "red":
                # Le bouton dit "RÉCLAMER", mais l'utilisateur ne verra pas le texte
                action_button = f"""
                <div style='margin-top: 15px;'>
                    <a href='/auto_send/{msg['id']}' style='text-decoration: none;'>
                        <button style='background: #d32f2f; color: white; border: none; padding: 15px 30px; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 16px;'>
                            💸 RÉCLAMER {analysis['amount']} (AUTO)
                        </button>
                    </a>
                </div>
                """
            else:
                action_button = "<div style='margin-top: 10px; color: green; font-style: italic;'>✅ Pas de litige</div>"

            html += f"""
            <div style='background: white; margin-bottom: 20px; padding: 20px; border-radius: 10px; border-left: 5px solid {analysis['color']}; box-shadow: 0 2px 5px rgba(0,0,0,0.1);'>
                <h3>{subject}</h3>
                <p style='color: #666;'>De : {sender}</p>
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
    
    # 1. Infos
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = msg['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
    sender_email = next((h['value'] for h in headers if h['name'] == 'From'), '')
    if "<" in sender_email: sender_email = sender_email.split("<")[1].replace(">", "")
    snippet = msg.get('snippet', '')
    
    # 2. IA génère le texte (INVISIBLE POUR LE CLIENT)
    legal_body = generate_legal_text_hidden(snippet, subject, sender_email, session.get('name', 'Client'))
    
    # 3. ENVOI DIRECT (Sans montrer)
    try:
        send_email_directly(service, "me", sender_email, subject, legal_body)
        
        # 4. Feedback Client (On lui dit juste que c'est fait)
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 100px;'>
            <h1 style='color: #2e7d32; font-size: 3em;'>✅ SUCCÈS !</h1>
            <div style='background: #e8f5e9; padding: 40px; max-width: 600px; margin: 0 auto; border-radius: 20px;'>
                <h2>La réclamation a été envoyée.</h2>
                <p style='font-size: 1.2em;'>Nous avons contacté <strong>{sender_email}</strong> en votre nom.</p>
                <p>Vous n'avez plus rien à faire. La réponse arrivera directement dans votre boîte mail.</p>
                <br><br>
                <a href='/scan'>
                    <button style='padding: 15px 30px; background-color: #333; color: white; border: none; font-size: 18px; cursor: pointer; border-radius: 50px;'>
                        🔍 Scanner une autre commande
                    </button>
                </a>
            </div>
        </div>
        """
    except Exception as e:
        return f"Erreur lors de l'envoi : {e}"

# --- AUTH SYSTEM ---
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
