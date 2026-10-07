# Iris — agent IA de montage de DOE (Standia)

Module Flask autonome, à brancher dans Justicio. Il est servi sur
`https://justicio.fr/standia/AgentIA_DEO/`.

## Ce que fait le MVP

1. L'artisan crée un chantier (nom, maître d'ouvrage, entreprise, lots réalisés).
2. Il dépose ses documents en vrac (PDF, JPG, PNG, plusieurs à la fois, glisser-déposer).
3. Iris lit chaque document (texte des PDF, OCR des scans et des photos) et le classe
   dans la bonne section du DOE. L'artisan peut corriger d'un clic.
4. Iris coche les pièces attendues (communes + spécifiques à chaque lot : Consuel,
   PV d'étanchéité, Qualigaz, ACERMI…) et signale celles qui manquent.
5. Un clic génère le DOE en un seul PDF : page de garde, sommaire paginé,
   intercalaires par section, documents, pied de page « Page x / y ».

## Intégration dans Justicio

1. Copier le dossier `iris_doe/` à côté de l'app Flask de Justicio.
2. Installer les dépendances :
   ```bash
   pip install -r iris_doe/requirements.txt
   sudo apt install tesseract-ocr tesseract-ocr-fra   # OCR en français
   ```
3. Enregistrer le blueprint là où l'app est créée :
   ```python
   from iris_doe import bp as iris_bp
   app.register_blueprint(iris_bp)
   app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # envois jusqu'à 200 Mo
   ```
   `app.secret_key` doit être défini (c'est déjà le cas dans Justicio pour les sessions).
4. Optionnel : `IRIS_STORAGE=/chemin/vers/stockage` dans `app.config`
   (par défaut : `instance/iris_chantiers/`).
5. Si Nginx est devant : `client_max_body_size 200M;` sur le server block,
   sinon les gros envois seront coupés.

### Lecture et classement par IA

Avec `OPENAI_API_KEY`, Iris lit aussi les scans et les photos de documents
(GPT vision, 3 pages max par fichier) et classe chaque pièce avec un titre propre.
Appels en HTTP direct (`iris_doe/ai.py`), indépendants de la version du SDK.

### Envoi du DOE

- WhatsApp : bouton qui ouvre WhatsApp avec un message et le lien de téléchargement.
- Email : envoyé par le serveur via le SMTP Brevo de Justicio (`BREVO_SMTP_KEY`,
  `BREVO_SMTP_LOGIN`, `IRIS_MAIL_FROM`). PDF joint s'il fait moins de 8 Mo.
- Lien de partage en lecture seule : `/standia/AgentIA_DEO/partage/<jeton>`.

### Ancien classement par IA

Sans clé, Iris classe par mots-clés (référentiel métier) : ça suffit pour une démo.
Avec `OPENAI_API_KEY` dans l'environnement, Iris utilise GPT (modèle réglable via
`IRIS_LLM_MODEL`, défaut `gpt-4o-mini`) : classement plus fin et titres propres.
En cas d'erreur de l'API, repli automatique sur les mots-clés.

## Tester seul en local

```bash
pip install -r iris_doe/requirements.txt
python run_demo.py
# ouvrir http://127.0.0.1:5000/standia/AgentIA_DEO/
```

## Adapter au métier

Tout le savoir DOE est dans `referentiel.py` : catégories, mots-clés, lots et pièces
obligatoires. Ajouter un lot ou une pièce ne demande pas de toucher au reste.

## Limites connues (avant la mise en production)

- **Pas d'authentification** : chaque chantier est protégé par son lien
  (identifiant aléatoire non devinable). Pour de vrais clients, placer les routes
  derrière le login de Justicio.
- Stockage sur disque (JSON + fichiers) : passer sur PostgreSQL au-delà de la démo.
- Le traitement est synchrone : au-delà d'une trentaine de scans d'un coup,
  prévoir une file de tâches (RQ ou Celery).
- Formats acceptés : PDF, JPG, PNG, WEBP (pas encore DWG, DOCX ni HEIC des iPhone).
