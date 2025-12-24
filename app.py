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

# --- CONFIGURATION (Render) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_ultra_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY") # Ta clé sk_live
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY") # Ta clé pk_live
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
DATABASE_URL = os.environ.get("DATABASE_URL")

stripe.api_key = STRIPE_SK

# --- BASE DE DONNÉES ---
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

# --- AUTH GOOGLE ---
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

# --- IA : ANALYSE STRICTE (0 BAVARDAGE) ---
def analyze_litigation(text, subject):
    if not OPENAI_API_KEY: return ["35€", "Loi consommation"]
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        # Prompt ultra-direct pour éviter les phrases inutiles
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"system", "content": "Tu es un expert juridique. Réponds uniquement au format: MONTANT | LOI. Exemple: 250€ | Reg 261/2004. Si inconnu, écris: À calculer | Code Civil."},
                      {"role":"user", "content": f"Mail: {subject}. Texte: {text[:400]}"}]
        )
        data = res.choices[0].message.content.split("|")
        return [d.strip() for d in data]
    except:
        return ["À calculer", "Code Civil"]

# --- ROUTES ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');
body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:20px;display:flex;flex-direction:column;align-items:center;color:#1e293b}
.card{background:white;border-radius:15px;padding:20px;margin:15px;width:100%;max-width:500px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);border-left:6px solid #ef4444}
.btn{display:block;background:#4f46e5;color:white;padding:15px;text-align:center;border-radius:10px;text-decoration:none;font-weight:bold;margin-top:15px}
.badge{background:#fee2e2;color:#b91c1c;padding:5px 10px;border-radius:6px;font-size:0.85rem;font-weight:bold;margin-bottom:10px;display:inline-block}</style>"""

@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    payment_status = request.args.get("payment")
    banner = "<div style='background:#dcfce7;color:#166534;padding:15px;border-radius:10px;margin-bottom:20px;'>✅ Protection activée : Votre carte est enregistrée !</div>" if payment_status == "success" else ""
    return STYLE + f"{banner}<h1>⚖️ JUSTICIO</h1><p>Compte : <b>{session.get('name')}</b></p><a href='/scan' class='btn'>🔍 ANALYSER MES LITIGES</a>"

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    try:
        creds = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().messages().list(userId='me', q="SNCF OR Amazon OR Temu OR remboursement OR retard", maxResults=5).execute()
        msgs = results.get('messages', [])
        
        html = "<h1>Litiges Identifiés</h1>"
        if not msgs: html += "<p>Aucun litige détecté.</p>"
        
        for m in msgs:
            f = service.users().messages().get(userId='me', id=m['id']).execute()
            headers = f['payload'].get('headers', [])
            subj = next((h['value'] for h in headers if h['name'] == 'Subject'), "Sans objet")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Inconnu")
            
            ana = analyze_litigation(f.get('snippet', ''), subj)
            html += f"""<div class='card'>
                <span class='badge'>💰 Gain estimé : {ana[0]}</span>
                <h3>{subj}</h3>
                <p style='color:#64748b;font-size:0.9rem;'>Expéditeur : {sender}</p>
                <p style='font-size:0.85rem;'><b>Base légale :</b> {ana[1]}</p>
                <a href='/setup-payment' class='btn'>🚀 RÉCUPÉRER MES {ana[0]} (0€)</a>
            </div>"""
        
        return STYLE + html + "<br><a href='/' style='text-decoration:none;color:#64748b;'>⬅️ Retour</a>"
    except Exception as e:
        return f"Erreur de scan : {str(e)}"

@app.route("/setup-payment")
def setup_payment():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='setup',
            success_url=url_for('index', _external=True) + "?payment=success",
            cancel_url=url_for('index', _external=True)
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return f"Erreur Stripe : {str(e)}. Vérifiez votre clé sk_live sur Render."

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

if __name__ == "__main__":
    app.run(debug=True)
