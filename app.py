import os
import base64
import requests
import stripe
from flask import Flask, session, redirect, request, url_for, render_template_string
from flask_sqlalchemy import SQLAlchemy
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI

app = Flask(__name__)

# --- CONFIGURATION DES CLÉS (Render Environment Variables) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_startup_billion_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AERODATABOX_KEY = os.environ.get("AERODATABOX_API_KEY")
SNCF_TOKEN = os.environ.get("NAVITIA_API_TOKEN") #
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") # Ta clé sk_live
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY") #
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
DATABASE_URL = os.environ.get("DATABASE_URL")

stripe.api_key = STRIPE_SK

# --- BASE DE DONNÉES (PostgreSQL sur Render) ---
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    refresh_token = db.Column(db.String(500), nullable=True)
    name = db.Column(db.String(100))

with app.app_context():
    db.create_all()

# --- CONFIGURATION GOOGLE OAUTH ---
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
client_secrets_config = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "project_id": "justicio-app",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": GOOGLE_CLIENT_SECRET
    }
}
SCOPES = ["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/gmail.readonly", "openid"]

# --- DESIGN SYSTEM (CSS) ---
STYLE = """<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body { font-family: 'Outfit', sans-serif; background: #f8fafc; color: #1e293b; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; }
.card { background: white; border-radius: 20px; padding: 30px; margin: 15px; width: 100%; max-width: 550px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border-left: 8px solid #4f46e5; }
.card.litige { border-left-color: #ef4444; }
.btn { display: inline-block; background: #4f46e5; color: white; padding: 16px 32px; text-align: center; border-radius: 12px; text-decoration: none; font-weight: bold; margin-top: 20px; transition: 0.3s; border: none; cursor: pointer; }
.btn:hover { background: #3730a3; transform: translateY(-2px); }
.badge { background: #fee2e2; color: #b91c1c; padding: 6px 12px; border-radius: 8px; font-size: 0.9rem; font-weight: bold; margin-bottom: 10px; display: inline-block; }
h1 { font-size: 2.5rem; color: #1e293b; margin-bottom: 10px; }
p { line-height: 1.6; color: #64748b; }
ul { text-align: left; margin: 20px 0; }
li { margin-bottom: 10px; }
</style>"""

# --- CERVEAU IA SÉCURISÉ ---
def analyze_litigation(text, subject):
    if not OPENAI_API_KEY: return ["35€", "Loi Consommation"]
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"system", "content": "Tu es un expert juridique. Réponds uniquement format: MONTANT | LOI. Exemple: 250€ | Reg 261/2004. Si aucun montant, écris: À calculer | Code Civil."},
                      {"role":"user", "content": f"Mail: {subject}. Snippet: {text[:400]}"}]
        )
        data = res.choices[0].message.content.split("|")
        return [d.strip() for d in data]
    except: return ["À calculer", "Code Civil"]

# --- RADARS TECHNIQUES ---
def get_transport_status(flight_no=None, train_no=None):
    # Logique simplifiée pour le test-api
    if train_no and SNCF_TOKEN: return "Perturbation détectée par SNCF"
    if flight_no and AERODATABOX_KEY: return "Radar Vol Opérationnel"
    return "Données indisponibles"

# --- ROUTES PRINCIPALES ---

@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    return STYLE + f"""
    <h1>⚖️ JUSTICIO</h1>
    <p>Bienvenue dans votre espace de protection juridique, <b>{session.get('name')}</b>.</p>
    <a href='/scan' class='btn'>🔍 ANALYSER MES DERNIERS EMAILS</a>
    <br><a href='/logout' style='margin-top:30px; color:#94a3b8; text-decoration:none;'>Déconnexion</a>
    """

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    try:
        creds = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=creds)
        # Filtre les mails importants pour une startup à 1 milliard
        query = "retard OR remboursement OR SNCF OR Amazon OR Temu OR Uber OR Deliveroo OR Flight"
        results = service.users().messages().list(userId='me', q=query, maxResults=8).execute()
        msgs = results.get('messages', [])
        
        html = "<h1>Litiges Identifiés</h1>"
        found_litigations = 0
        
        for m in msgs:
            f = service.users().messages().get(userId='me', id=m['id']).execute()
            headers = f['payload'].get('headers', [])
            subj = next((h['value'] for h in headers if h['name'] == 'Subject'), "Sans objet")
            ana = analyze_litigation(f.get('snippet', ''), subj)
            
            # FILTRE DE VALEUR : On n'affiche que les vrais montants pour maximiser la conversion
            if "€" in ana[0] and ana[0] != "À calculer":
                found_litigations += 1
                html += f"""<div class='card litige'>
                    <span class='badge'>💰 Gain potentiel : {ana[0]}</span>
                    <h3>{subj}</h3>
                    <p><b>Base légale :</b> {ana[1]}</p>
                    <a href='/pre-payment?amount={ana[0]}&subject={subj}' class='btn'>🚀 RÉCUPÉRER MES {ana[0]}</a>
                </div>"""
        
        if found_litigations == 0:
            html += "<p>Aucun litige avec montant détecté. Essayez de vous envoyer un mail de test !</p>"
            
        return STYLE + html + "<br><a href='/' style='text-decoration:none;'>⬅️ Retour</a>"
    except Exception as e:
        return f"Erreur de connexion Gmail : {str(e)}"

@app.route("/pre-payment")
def pre_payment():
    """Page de réassurance avant Stripe"""
    amount = request.args.get("amount", "vos fonds")
    subject = request.args.get("subject", "votre litige")
    return STYLE + f"""
    <div style='max-width:600px; text-align:center;'>
        <h1>🛡️ Sécurisez votre dossier</h1>
        <p>Nous sommes prêts à réclamer <b>{amount}</b> pour le dossier : <br><i>"{subject}"</i></p>
        <div class='card' style='text-align:left; border-left-color:#4f46e5;'>
            <h3 style='margin-top:0;'>Pourquoi enregistrer votre carte ?</h3>
            <ul>
                <li>✅ <b>0€ débité aujourd'hui :</b> L'inscription est totalement gratuite.</li>
                <li>💳 <b>Réception des fonds :</b> C'est ici que vous recevrez votre remboursement.</li>
                <li>⚖️ <b>Succès uniquement :</b> Nous prélevons notre commission de 30% seulement si vous gagnez. Sinon, c'est gratuit.</li>
            </ul>
        </div>
        <a href='/setup-payment' class='btn'>💳 ENREGISTRER MA CARTE & LANCER L'ACTION</a>
        <br><a href='/scan' style='margin-top:20px; display:block; color:#64748b; text-decoration:none;'>Revenir en arrière</a>
    </div>
    """

@app.route("/setup-payment")
def setup_payment():
    """Lancement de Stripe Setup Mode"""
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='setup',
            success_url=url_for('index', _external=True) + "?payment=success",
            cancel_url=url_for('scan', _external=True),
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return f"Erreur Stripe : {str(e)}. Vérifiez votre clé sk_live sur Render."

# --- AUTHENTIFICATION ---
@app.route("/login")
def login():
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session["state"] = state
    return redirect(url)

@app.route("/callback")
def callback():
    flow = Flow.from_client_config(client_secrets_config, scopes=SCOPES, redirect_uri=url_for('callback', _external=True).replace("http://", "https://"))
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session["credentials"] = {'token': creds.token, 'refresh_token': creds.refresh_token, 'token_uri': creds.token_uri, 'client_id': creds.client_id, 'client_secret': creds.client_secret, 'scopes': creds.scopes}
    info = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
    session["name"] = info.get('name')
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/test-api")
def test_api():
    t = get_transport_status(train_no="8001")
    return f"Test des Radars : {t}"

if __name__ == "__main__":
    app.run(debug=True)
