import os
import time
from app import app, db, User, credentials_to_dict # On importe la config de ton app.py
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

def scan_all_users():
    with app.app_context():
        # 1. On récupère tous les utilisateurs qui ont une clé (Refresh Token)
        users = User.query.filter(User.refresh_token != None).all()
        print(f"--- Lancement du scan pour {len(users)} utilisateurs ---")

        for user in users:
            print(f"Vérification pour : {user.email}")
            try:
                # 2. On reconstruit les accès Google pour cet utilisateur
                creds = Credentials(
                    None, # On n'a pas besoin de l'access_token, il va être régénéré
                    refresh_token=user.refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
                    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET")
                )

                # 3. On rafraîchit la clé si besoin
                creds.refresh(Request())
                
                # 4. On se connecte à Gmail
                service = build('gmail', 'v1', credentials=creds)

                # 5. RECHERCHE DE REMBOURSEMENT (Le coeur du robot)
                # On cherche les mails de succès reçus récemment
                query = "subject:(Remboursement OR Refund OR 'virement effectué') (Uber OR Amazon OR SNCF OR Air France)"
                results = service.users().messages().list(userId='me', q=query, maxResults=5).execute()
                messages = results.get('messages', [])

                if messages:
                    print(f"💰 POTENTIEL REMBOURSEMENT TROUVÉ pour {user.email} !")
                    # Ici on lancera l'IA pour extraire le montant et Stripe pour prendre les 30%
                    # Pour l'instant, on se contente de le noter dans les logs
                else:
                    print(f"Rien de neuf pour {user.email}")

            except Exception as e:
                print(f"Erreur pour {user.email}: {e}")

if __name__ == "__main__":
    # Ce script tourne en boucle toutes les 12 heures (43200 secondes)
    while True:
        scan_all_users()
        print("Scan terminé. Prochain passage dans 12 heures...")
        time.sleep(43200)
