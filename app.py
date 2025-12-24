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

# --- CONFIGURATION (Render Environment Variables) ---
app.secret_key = os.environ.get("SECRET_KEY", "justicio_ultra_secret")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AERODATABOX_KEY = os.environ.get("AERODATABOX_API_KEY") 
SNCF_TOKEN = os.environ.get("NAVITIA_API_TOKEN") 
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

# --- RADARS TECHNIQUES ---
def get_flight_status(flight_no, date_str):
    if not AERODATABOX_KEY: return "Radar Vol désactivé"
    url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_no.replace(' ', '')}/{date_str}"
    headers = {"X-RapidAPI-Key": AERODATABOX_KEY, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 204: return "Aucun vol trouvé"
        data = r.json()
        delay = data[0].get('arrival', {}).get('delayMinutes', 0)
        return f"RETARD : {delay} min"
    except: return "Radar Vol indisponible"

def get_train_status(train_no):
    if not SNCF_TOKEN: return "Radar SNCF désactivé"
    url = f"https://api.sncf.com/v1/coverage/sncf/disruptions/?q={train_no}"
    try:
        r = requests.get(url, auth=(SNCF_TOKEN, '')).json()
        return "Perturbation détectée" if r.get('disruptions') else "Circulation normale"
    except: return "Radar SNCF indisponible"

# --- CERVEAU IA SÉCURISÉ ---
def analyze_litigation(text, subject):
    if not OPENAI_API_KEY: return ["À vérifier", "Loi inconnue", "Entreprise"]
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role":"user", "content": f"Analyse ce mail: {subject}. Snippet: {text[:400]}. Réponds uniquement: MONTANT | LOI | ENTREPRISE"}]
        )
        data = res.choices[0].message.content.split("|")
        while len(data) < 3: data.append("Détail manquant")
        return data
    except: return ["Litige détecté", "Code Transports", "Entreprise"]

# --- ROUTES ---
STYLE = """<style>@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');body{font-family:'Outfit',sans-serif;background:#f8fafc;padding:20px;display:flex;flex-direction:column;align-items:center}.card{background:white;border-radius:15px;padding:20px;margin:10px;width:100%;max-width:500px;box-shadow:0 4px 6px rgba(0,0,0,0.1);border-left:5px solid #ef4444}.btn{display:block;background:#4f46e5;color:white;padding:12px;text-align:center;border-radius:8px;text-decoration:none;font-weight:bold;margin-top:10px}.badge{background:#fee2e2;color:#b91c1c;padding:5px 10px;border-radius:5px;font-size:0.8rem;font-weight:bold}</style>"""

@app.route("/")
def index():
    if "credentials" not in session: return redirect("/login")
    msg = request.args.get("payment")
    banner = "<p style='color:green;'>✅ Protection activée !</p>" if msg == "success" else ""
    return STYLE + f"{banner}<h1>⚖️ JUSTICIO</h1><p>Bonjour {session.get('name')}</p><a href='/scan' class='btn'>🔍 SCANNER MES EMAILS</a>"

@app.route("/scan")
def scan():
    if "credentials" not in session: return redirect("/login")
    try:
        creds = Credentials(**session["credentials"])
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().messages().list(userId='me', q="retard OR remboursement OR SNCF OR Amazon OR Flight", maxResults=5).execute()
        msgs = results.get('messages', [])
        
        html = "<h1>Résultats du Scan</h1>"
        if not msgs:
            html += "<p>Aucun mail de litige trouvé dans vos derniers messages.</p>"
        
        for m in msgs:
            f = service.users().messages().get(userId='me', id=m['id']).execute()
            headers = f['payload'].get('headers', [])
            
            # --- FIX STOPITERATION : On donne une valeur par défaut ---
            subj = next((h['value'] for h in headers if h['name'] == 'Subject'), "Sans objet")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Inconnu")
            
            ana = analyze_litigation(f.get('snippet', ''), subj)
            html += f"<div class='card'><h3>{subj}</h3><p>Expéditeur : {sender}</p><span class='badge'>💰 Indemnité : {ana[0]}</span><a href='/setup-payment' class='btn'>🚀 RÉCUPÉRER MES {ana[0]}</a></div>"
        
        return STYLE + html + "<br><a href='/'>Retour</a>"
    except Exception as e:
        return f"Erreur critique : {str(e)}. Vérifiez vos clés API sur Render."

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
        return f"Erreur Stripe : {str(e)}. Votre clé Secrète est-elle bien sur Render ?"

@app.route("/test-api")
def test_api():
    f = get_flight_status("AF123", "2025-12-22")
    t = get_train_status("8001")
    return f"Radars : Vol ({f}) | SNCF ({t})"

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

if __name__ == "__main__":
    app.run(debug=True)
