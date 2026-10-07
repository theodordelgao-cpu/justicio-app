"""Métiers, grilles de prix d'exemple et demandes types.

Les prix sont des EXEMPLES pour la démo : l'artisan les remplace par ses propres
tarifs dans la page (ils sont gardés dans son navigateur).
Chaque ligne : (libellé, unité, prix HT, mots-clés qui la déclenchent).
"""

METIERS = {
    "plomberie": {
        "label": "Plomberie / sanitaire",
        "grille": [
            ("Déplacement et diagnostic", "forfait", 49.0, ["deplacement", "diagnostic"]),
            ("Main d'œuvre", "heure", 55.0, ["main d'oeuvre", "heure"]),
            ("Recherche et réparation de fuite", "forfait", 120.0, ["fuite", "goutte", "coule"]),
            ("Remplacement siphon PVC", "unité", 45.0, ["siphon"]),
            ("Remplacement flexible d'alimentation", "unité", 28.0, ["flexible"]),
            ("Débouchage canalisation", "forfait", 110.0, ["debouch", "bouche", "evacuation"]),
            ("Remplacement mitigeur (fourni-posé)", "unité", 165.0, ["mitigeur", "robinet"]),
            ("Remplacement mécanisme WC", "unité", 95.0, ["wc", "chasse", "toilette"]),
            ("Chauffe-eau électrique 200 L (fourni-posé)", "unité", 890.0, ["chauffe-eau", "ballon", "cumulus"]),
            ("Pose receveur de douche extra-plat", "unité", 420.0, ["douche", "receveur"]),
        ],
    },
    "electricite": {
        "label": "Électricité",
        "grille": [
            ("Déplacement et diagnostic", "forfait", 49.0, ["deplacement", "diagnostic"]),
            ("Main d'œuvre", "heure", 52.0, ["main d'oeuvre", "heure"]),
            ("Recherche de panne", "forfait", 95.0, ["panne", "disjonct", "saute", "coupure"]),
            ("Remplacement tableau électrique (fourni-posé)", "unité", 1450.0, ["tableau"]),
            ("Création prise électrique", "unité", 85.0, ["prise"]),
            ("Création point lumineux", "unité", 95.0, ["point lumineux", "luminaire", "eclairage", "plafonnier"]),
            ("Interrupteur différentiel 30 mA", "unité", 120.0, ["differentiel"]),
            ("Mise en sécurité installation", "forfait", 380.0, ["mise en securite", "vetuste", "aux normes", "norme"]),
            ("Borne de recharge véhicule 7 kW (fourni-posé)", "unité", 1290.0, ["borne", "recharge", "voiture"]),
        ],
    },
    "serrurerie": {
        "label": "Serrurerie",
        "grille": [
            ("Déplacement", "forfait", 39.0, ["deplacement"]),
            ("Ouverture de porte claquée", "forfait", 89.0, ["claque", "ouverture", "bloque", "enferme"]),
            ("Ouverture de porte fermée à clé", "forfait", 149.0, ["ferme a cle", "cle perdue", "perdu"]),
            ("Remplacement cylindre de sécurité", "unité", 135.0, ["cylindre", "barillet", "serrure"]),
            ("Serrure 3 points (fourni-posé)", "unité", 420.0, ["3 points", "trois points", "multipoint"]),
            ("Majoration urgence soir / week-end", "forfait", 60.0, ["urgence", "nuit", "soir", "dimanche", "week-end"]),
        ],
    },
    "peinture": {
        "label": "Peinture / revêtements",
        "grille": [
            ("Protection et préparation des supports", "m²", 6.0, ["preparation", "protection", "rebouch", "ponc"]),
            ("Peinture murs 2 couches", "m²", 22.0, ["mur", "peinture", "peindre", "repeindre"]),
            ("Peinture plafond 2 couches", "m²", 26.0, ["plafond"]),
            ("Pose papier peint", "m²", 24.0, ["papier peint", "tapisserie"]),
            ("Pose parquet stratifié", "m²", 32.0, ["parquet", "stratifie"]),
            ("Fournitures (peinture, sous-couche)", "forfait", 180.0, ["fourniture", "peinture"]),
        ],
    },
    "chauffage": {
        "label": "Chauffage / climatisation",
        "grille": [
            ("Déplacement et diagnostic", "forfait", 59.0, ["deplacement", "diagnostic"]),
            ("Entretien annuel chaudière gaz", "forfait", 130.0, ["entretien", "chaudiere", "revision"]),
            ("Dépannage chaudière", "forfait", 150.0, ["panne", "depann", "plus de chauffage", "eau chaude"]),
            ("Désembouage circuit de chauffage", "forfait", 590.0, ["desembou", "radiateur froid", "boue"]),
            ("Remplacement radiateur", "unité", 380.0, ["radiateur"]),
            ("Climatisation monosplit 3,5 kW (fourni-posé)", "unité", 1890.0, ["clim", "climatisation"]),
            ("Pompe à chaleur air/eau 8 kW (fourni-posé)", "unité", 9800.0, ["pompe a chaleur", "pac"]),
        ],
    },
}

# demandes d'exemple pour essayer en un clic
EXEMPLES = [
    {"metier": "plomberie", "client": "Mme Laurent", "adresse": "14 rue des Lilas, 31700 Blagnac",
     "demande": "Fuite sous l'évier de la cuisine, ça coule en continu. Il faudra sûrement changer le siphon et le flexible. Appartement au 2e étage sans ascenseur."},
    {"metier": "electricite", "client": "M. Garcia", "adresse": "8 chemin du Lac, 31200 Toulouse",
     "demande": "Le disjoncteur saute dès qu'on allume le four. Maison des années 80, je voudrais aussi ajouter 3 prises dans le salon et un point lumineux dans l'entrée."},
    {"metier": "peinture", "client": "SCI Les Tilleuls", "adresse": "22 avenue de Muret, 31300 Toulouse",
     "demande": "Repeindre un T2 avant location : murs environ 95 m2 et plafonds 38 m2, rebouchage des trous à prévoir."},
]

TVA_OPTIONS = [
    ("20", "20 % (taux normal)"),
    ("10", "10 % (rénovation d'un logement de plus de 2 ans)"),
    ("5.5", "5,5 % (rénovation énergétique)"),
    ("0", "TVA non applicable, art. 293 B du CGI (micro-entreprise)"),
]


def grille_json(metier: str) -> list[dict]:
    m = METIERS.get(metier) or METIERS["plomberie"]
    return [{"libelle": l, "unite": u, "prix": p, "mots": mots} for (l, u, p, mots) in m["grille"]]
