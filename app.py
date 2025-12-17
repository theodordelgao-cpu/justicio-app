import os
import pathlib
import requests
from flask import Flask, session, abort, redirect, request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from pip._vendor import cachecontrol
import google.auth.transport.requests

app = Flask(__name__)

# 1. On récupère les clés secrètes qu'on a mises sur Render
app.secret_key = os.environ.get("SECRET_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

# Cette astuce permet de faire fonctionner le HTTPS sur Render
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1" 

# Configuration pour Google
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

@app.route("/")
def index():
    # Si l'utilisateur est déjà connecté, on lui montre ses infos
    if "google_id" in session:
        return f"""
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
            <h1>⚖️ JUSTICIO</h1>
            <p style='color: green;'>Connecté en tant que : {session['name']}</p>
            <p>Email : {session.get('email')}</p>
            <br>
            <button style='padding: 15px 30px; font-size: 18px; background-color: #28a745; color: white; border: none; border-radius: 5px;'>
                🔍 LANCER L'ANALYSE DES EMAILS
            </button>
            <br><br>
            <a href='/logout'>Se déconnecter</a>
        </div>
        """
    # Sinon, on affiche le bouton de connexion
    else:
        return """
        <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
            <h1>⚖️ JUSTICIO</h1>
            <h2>Récupérez votre argent caché.</h2>
            <p>Connectez votre boîte mail pour scanner vos factures (Train, Avion, Uber, Amazon).</p>
            <br>
            <a href='/login'>
                <button style='padding: 15px 30px; font-size: 18px; background-color: #4285F4; color: white; border: none; border-radius: 5px; cursor: pointer;'>
                    👉 Se connecter avec Google
                </button>
            </a>
        </div>
        """

@app.route("/login")
def login():
    # On prépare la connexion avec Google
    # IMPORTANT : On doit dire à Google où nous renvoyer (le callback)
    # Sur Render, l'URL change, donc on la construit dynamiquement ou on la force
    # Pour l'instant on tente de construire l'URL de callback
    
    redirect_uri = url_for('callback', _external=True)
    # Petite correction pour s'assurer qu'on est en HTTPS (Render est en HTTPS)
    if "http://" in redirect_uri:
        redirect_uri = redirect_uri.replace("http://", "https://")

    flow = Flow.from_client_config(
        client_secrets_config,
        scopes=["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "openid"],
        redirect_uri=redirect_uri
    )
    
    authorization_url, state = flow.authorization_url()
    session["state"] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    # C'est ici que Google nous renvoie après la connexion
    
    redirect_uri = url_for('callback', _external=True)
    if "http://" in redirect_uri:
        redirect_uri = redirect_uri.replace("http://", "https://")

    flow = Flow.from_client_config(
        client_secrets_config,
        scopes=["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "openid"],
        redirect_uri=redirect_uri
    )
    
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    request_session = requests.session()
    cached_session = cachecontrol.CacheControl(request_session)
    token_request = google.auth.transport.requests.Request(session=cached_session)

    id_info = id_token.verify_oauth2_token(
        id_token=credentials._id_token,
        request=token_request,
        audience=GOOGLE_CLIENT_ID
    )

    # On sauvegarde les infos de l'utilisateur dans le "Cookie" (Session)
    session["google_id"] = id_info.get("sub")
    session["name"] = id_info.get("name")
    session["email"] = id_info.get("email")
    
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# Cette fonction sert juste à obtenir l'URL complète dans les autres fonctions
from flask import url_for

if __name__ == "__main__":
    app.run(debug=True)
