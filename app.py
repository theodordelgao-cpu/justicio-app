from flask import Flask, render_template_string, jsonify
import time
import random
import os

app = Flask(__name__)

# --- LE CERVEAU ---
def robot_scan_intelligence():
    time.sleep(1.5) 
    montant_trouve = random.choice([125.50, 340.00, 45.00, 600.00])
    sources = [
        {"nom": "SNCF (Retard)", "montant": montant_trouve * 0.4},
        {"nom": "Air France (Annulation)", "montant": montant_trouve * 0.5},
        {"nom": "Uber (Frais cachés)", "montant": montant_trouve * 0.1}
    ]
    commission = montant_trouve * 0.30
    client_net = montant_trouve - commission
    return {"total": montant_trouve, "commission": commission, "client": client_net, "details": sources}

# --- LE SITE WEB ---
@app.route("/")
def home():
    html_code = """
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>JUSTICIO</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background-color: #050505; color: white; text-align: center; padding: 20px; }
            h1 { font-size: 3rem; margin: 0; color: #4CAF50; letter-spacing: -2px; }
            .tagline { color: #888; text-transform: uppercase; letter-spacing: 3px; font-size: 0.8rem; margin-bottom: 40px; }
            .btn-main { background-color: white; color: black; padding: 15px 40px; font-size: 18px; border-radius: 50px; border: none; cursor: pointer; font-weight: bold; transition: 0.3s; width: 100%; max-width: 300px; box-shadow: 0 0 20px rgba(255,255,255,0.1); }
            .btn-main:hover { transform: scale(1.05); background-color: #eee; }
            .card { background: #111; padding: 30px; margin: 0 auto; border-radius: 20px; max-width: 400px; border: 1px solid #333; }
            .hidden { display: none; }
            .amount { float: right; color: #4CAF50; font-weight: bold; }
            .big-number { font-size: 3.5rem; color: #4CAF50; font-weight: bold; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div id="screen1">
            <h1>JUSTICIO</h1>
            <div class="tagline">Service de Récupération</div>
            <div class="card">
                <p>Scanner votre historique maintenant.</p>
                <br>
                <button class="btn-main" onclick="lancerScan()">LANCER LE SCAN</button>
            </div>
        </div>

        <div id="screen2" class="hidden">
            <h1>RÉSULTAT</h1>
            <div class="card">
                <div id="loading" style="color:#888">Connexion aux serveurs...</div>
                <div id="results" class="hidden">
                    <div class="big-number" id="totalDisplay">0 €</div>
                    <div id="detailsList" style="text-align:left; font-size:0.9rem; color:#ccc;"></div>
                    <hr style="border-color: #333; margin: 20px 0;">
                    <p>Votre part : <span id="clientDisplay" style="font-weight:bold; color:white;"></span></p>
                    <button class="btn-main" style="background:#4CAF50; color:white;">RÉCUPÉRER L'ARGENT</button>
                </div>
            </div>
        </div>

        <script>
            function lancerScan() {
                document.getElementById('screen1').classList.add('hidden');
                document.getElementById('screen2').classList.remove('hidden');
                fetch('/api/scan').then(r => r.json()).then(data => {
                    setTimeout(() => {
                        document.getElementById('loading').classList.add('hidden');
                        document.getElementById('results').classList.remove('hidden');
                        document.getElementById('totalDisplay').innerText = data.total.toFixed(2) + " €";
                        document.getElementById('clientDisplay').innerText = data.client.toFixed(2) + " €";
                        let html = "";
                        data.details.forEach(i => html += `<p>${i.nom} <span class="amount">${i.montant.toFixed(2)}€</span></p>`);
                        document.getElementById('detailsList').innerHTML = html;
                    }, 1500);
                });
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html_code)

@app.route("/api/scan")
def api_scan():
    return jsonify(robot_scan_intelligence())

if __name__ == '__main__':
    # Configuration automatique pour le Cloud
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
