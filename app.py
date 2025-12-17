import os
import requests
from flask import Flask, session, abort, redirect, request, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from pip._vendor import cachecontrol
import google.auth.transport.requests

app = Flask(__name__)

# CONFIGURATION
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
    "https://www.googleapis.com/auth/gmail.readonly",  # On ajoute le droit de LIRE les mails
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

@app.route("/")
def index():
    if "credentials" in session:
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
            <h1>⚖️ JUSTICIO</h1>
            <p style='color: green;'>✅ Connecté en tant que : {session.get('name', 'Utilisateur')}</p>
            <br>
            <a href='/scan'>
                <button style='padding: 20px 40px; font-size: 20px; background-color: #28a745; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold;'>
                    🕵️‍♂️ LANCER LE SCAN (Uber, Amazon, SNCF)
                </button>
            </a>
            <br><br>
            <a href='/logout' style='color: #666;'>Se déconnecter</a>
        </div>
        """
    else:
        return """
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
            <h1>⚖️ JUSTICIO</h1>
            <h2>Récupérez votre argent caché.</h2>
            <p>Connectez votre boîte mail pour scanner vos factures.</p>
            <br>
            <a href='/login'>
                <button style='padding: 15px 30px; font-size: 18px; background-color: #4285F4; color: white; border: none; border-radius: 5px; cursor: pointer;'>
                    👉 Se connecter avec Google
                </button>
            </a>
        </div>
        """

@app.route("/scan")
def scan_emails():
    if "credentials" not in session:
        return redirect("/login")

    # 1. On reconstruit les clés à partir de la session
    credentials = Credentials(**session["credentials"])
    
    # 2. On se connecte à Gmail
    try:
        service = build('gmail', 'v1', credentials=credentials)
        
        # 3. On cherche les emails avec les mots clés
        # "q" est la requête de recherche comme dans la barre Gmail
        query = "subject:(Uber OR Amazon OR SNCF OR Facture OR Commande)"
        results = service.users().messages().list(userId='me', q=query, maxResults=10).execute()
        messages = results.get('messages', [])

        if not messages:
            return "<h1>Aucun email trouvé avec ces mots-clés (Uber, Amazon, SNCF...) !</h1><a href='/'>Retour</a>"

        # 4. On prépare l'affichage des résultats
        html_content = "<div style='font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;'>"
        html_content += "<h1>💰 Trésors Potentiels Trouvés :</h1>"
        html_content += "<ul style='list-style: none; padding: 0;'>"

        for msg in messages:
            # On récupère les détails de chaque mail
            msg_detail = service.users().messages().get(userId='me', id=msg['id'], format='metadata').execute()
            headers = msg_detail['payload']['headers']
            subject = next((header['value'] for header in headers if header['name'] == 'Subject'), 'Sans objet')
            sender = next((header['value'] for header in headers if header['name'] == 'From'), 'Inconnu')
            
            html_content += f"""
            <li style='background: #f4f4f4; margin-bottom: 10px; padding: 15px; border-radius: 5px; border-left: 5px solid #28a745;'>
                <strong>De :</strong> {sender}<br>
                <strong>Sujet :</strong> {subject}
            </li>
            """

        html_content += "</ul>"
        html_content += "<br><a href='/'><button>Retour au menu</button></a></div>"
        
        return html_content

    except Exception as e:
        return f"<h1>Erreur lors du scan :</h1><p>{str(e)}</p><a href='/logout'>Se reconnecter</a>"

@app.route("/login")
def login():
    redirect_uri = url_for('callback', _external=True)
    if "http://" in redirect_uri:
        redirect_uri = redirect_uri.replace("http://", "https://")

    flow = Flow.from_client_config(
        client_secrets_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    authorization_url, state = flow.authorization_url()
    session["state"] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    redirect_uri = url_for('callback', _external=True)
    if "http://" in redirect_uri:
        redirect_uri = redirect_uri.replace("http://", "https://")

    flow = Flow.from_client_config(
        client_secrets_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    flow.fetch_token(authorization_response=request.url)

    # On sauvegarde les credentials complets pour pouvoir scanner plus tard
    credentials = flow.credentials
    session["credentials"] = credentials_to_dict(credentials)
    
    # On récupère juste le nom pour l'accueil
    session["name"] = "Spy One" # On peut améliorer ça plus tard
    
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
