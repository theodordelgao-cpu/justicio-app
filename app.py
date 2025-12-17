import os
import requests
from flask import Flask, session, abort, redirect, request, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from pip._vendor import cachecontrol
import google.auth.transport.requests

app = Flask(__name__)

# --- CONFIGURATION DE SÉCURITÉ ---
app.secret_key = os.environ.get("SECRET_KEY", "azerty_super_secret_key")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

# Cette ligne permet au HTTPS de fonctionner correctement sur Render
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Configuration de la connexion Google
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

# Les permissions qu'on demande au client (Profil + Lire les mails)
SCOPES = [
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid"
]

# Petite fonction utilitaire pour gérer les clés
def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

# --- PAGE D'ACCUEIL ---
@app.route("/")
def index():
    if "credentials" in session:
        # Si connecté : Affiche le Tableau de bord
        return f"""
        <div style='font-family: 'Segoe UI', sans-serif; text-align: center; margin-top: 50px; color: #333;'>
            <h1 style='font-size: 3em;'>⚖️ JUSTICIO</h1>
            <p style='color: green; font-weight: bold; font-size: 1.2em;'>✅ Connecté en tant que : {session.get('name', 'Spy One')}</p>
            <br>
            <div style='background: #f0fdf4; padding: 30px; display: inline-block; border-radius: 15px; border: 1px solid #bbf7d0;'>
                <p>Le robot est prêt à scanner : <strong>Uber, Amazon, SNCF, Temu...</strong></p>
                <a href='/scan'>
                    <button style='padding: 20px 40px; font-size: 20px; background-color: #16a34a; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; box-shadow: 0 4px 6px rgba(0,0,0,0.1); transition: transform 0.1s;'>
                        🕵️‍♂️ LANCER LE SCAN INTELLIGENT
                    </button>
                </a>
            </div>
            <br><br>
            <a href='/logout' style='color: #666; text-decoration: none;'>Se déconnecter</a>
        </div>
        """
    else:
        # Si pas connecté : Affiche la page de vente
        return """
        <div style='font-family: 'Segoe UI', sans-serif; text-align: center; margin-top: 50px; color: #333;'>
            <h1 style='font-size: 3em;'>⚖️ JUSTICIO</h1>
            <h2 style='color: #555;'>Récupérez votre argent caché.</h2>
            <p style='font-size: 1.1em;'>Connectez votre boîte mail. Nous trouvons vos remboursements oubliés.</p>
            <br>
            <a href='/login'>
                <button style='padding: 15px 30px; font-size: 18px; background-color: #4285F4; color: white; border: none; border-radius: 5px; cursor: pointer; box-shadow: 0 2px 4px rgba(0,0,0,0.2);'>
                    👉 Se connecter avec Google
                </button>
            </a>
        </div>
        """

# --- LE SCANNEUR (C'est ici que la magie opère) ---
@app.route("/scan")
def scan_emails():
    # Vérification de sécurité
    if "credentials" not in session:
        return redirect("/login")

    # On récupère les clés de la session
    credentials = Credentials(**session["credentials"])
    
    try:
        # On lance le service Gmail
        service = build('gmail', 'v1', credentials=credentials)
        
        # REQUÊTE DE RECHERCHE (C'est ici qu'on filtre)
        query = "subject:(Uber OR Amazon OR SNCF OR Facture OR Commande OR Temu)"
        
        # On demande les 15 derniers résultats
        results = service.users().messages().list(userId='me', q=query, maxResults=15).execute()
        messages = results.get('messages', [])

        if not messages:
            return "<h1>Aucun email trouvé !</h1><a href='/'>Retour</a>"

        # On construit l'affichage des résultats (HTML)
        html_content = "<div style='font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #fafafa; min-height: 100vh;'>"
        html_content += "<h1 style='text-align: center; color: #333;'>💰 Trésors Potentiels Trouvés</h1>"
        html_content += "<ul style='list-style: none; padding: 0;'>"

        for msg in messages:
            # Pour chaque mail, on va chercher les détails (Snippet + Sujet)
            msg_detail = service.users().messages().get(userId='me', id=msg['id'], format='metadata').execute()
            
            # On récupère l'aperçu du texte (le début du mail)
            snippet = msg_detail.get('snippet', 'Pas d\'aperçu disponible')
            
            # On cherche le Sujet et l'Expéditeur dans les en-têtes
            headers = msg_detail['payload']['headers']
            subject = next((header['value'] for header in headers if header['name'] == 'Subject'), 'Sans objet')
            sender = next((header['value'] for header in headers if header['name'] == 'From'), 'Inconnu')
            
            # On crée une jolie carte pour chaque mail
            html_content += f"""
            <li style='background: #fff; margin-bottom: 20px; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border-left: 6px solid #28a745;'>
                <div style='font-weight: bold; color: #2c3e50; font-size: 1.1em; margin-bottom: 5px;'>{subject}</div>
                <div style='color: #7f8c8d; font-size: 0.9em; margin-bottom: 12px;'>De : {sender}</div>
                <div style='background: #f1f8e9; padding: 12px; border-radius: 8px; font-style: italic; color: #558b2f; border: 1px dashed #c5e1a5;'>
                    " {snippet}... "
                </div>
            </li>
            """

        html_content += "</ul>"
        html_content += "<div style='text-align: center; margin-top: 30px;'><a href='/'><button style='padding:12px 24px; background: #333; color: white; border: none; border-radius: 50px; cursor: pointer;'>Retour au menu</button></a></div></div>"
        
        return html_content

    except Exception as e:
        return f"<h1>Erreur technique lors du scan :</h1><p>{str(e)}</p><a href='/logout'>Se reconnecter</a>"

# --- ROUTES DE CONNEXION (Ne pas toucher) ---
@app.route("/login")
def login():
    # On gère l'URL de retour (Callback) pour Render
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

    # On enregistre les infos de connexion
    credentials = flow.credentials
    session["credentials"] = credentials_to_dict(credentials)
    
    # On essaie de récupérer le vrai nom de l'utilisateur (Bonus)
    try:
        session["name"] = "Spy One" # Valeur par défaut
        # Ici on pourrait appeler l'API userinfo pour avoir le vrai nom
    except:
        pass
    
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
