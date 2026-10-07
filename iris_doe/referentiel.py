"""Référentiel DOE : catégories du dossier, lots de travaux et pièces attendues.

C'est ici qu'on ajuste Iris au métier : ajouter un mot-clé, un lot ou une pièce
obligatoire ne demande aucune autre modification du code.
"""

# Ordre = ordre des sections dans le DOE final.
CATEGORIES = [
    {
        "key": "admin",
        "label": "Pièces administratives",
        "keywords": [
            "kbis", "extrait k", "decennale", "attestation d'assurance", "responsabilite civile",
            "urssaf", "proces-verbal de reception", "pv de reception", "reception des travaux",
            "levee des reserves", "siret",
        ],
    },
    {
        "key": "plans",
        "label": "Plans d'exécution et de récolement",
        "keywords": [
            "plan", "recolement", "plan d'execution", "coupe", "elevation", "schema",
            "synoptique", "echelle 1/", "cartouche", "implantation", "carnet de detail",
        ],
    },
    {
        "key": "fiches",
        "label": "Fiches techniques des matériaux et équipements",
        "keywords": [
            "fiche technique", "fiche produit", "caracteristiques techniques", "avis technique",
            "declaration de performance", "dop", "marquage ce", "acermi", "donnees techniques",
            "documentation technique", "dimensions", "performances",
        ],
    },
    {
        "key": "notices",
        "label": "Notices de fonctionnement et d'entretien",
        "keywords": [
            "notice", "mode d'emploi", "entretien", "maintenance", "manuel d'utilisation",
            "instructions d'installation", "guide d'utilisation", "consignes d'utilisation",
        ],
    },
    {
        "key": "essais",
        "label": "Procès-verbaux d'essais et autocontrôles",
        "keywords": [
            "essai", "autocontrole", "mise en pression", "etancheite", "rapport de mesure",
            "resistance d'isolement", "mesure de terre", "test", "mise en service", "debit mesure",
            "fiche d'autocontrole",
        ],
    },
    {
        "key": "certificats",
        "label": "Certificats et attestations de conformité",
        "keywords": [
            "consuel", "certificat de conformite", "attestation de conformite", "qualigaz",
            "pv de classement", "reaction au feu", "re2020", "rt2012", "attestation thermique",
            "permeabilite a l'air", "attestation de fin de travaux",
        ],
    },
    {
        "key": "garanties",
        "label": "Garanties",
        "keywords": ["garantie", "bon de garantie", "certificat de garantie", "extension de garantie"],
    },
    {
        "key": "photos",
        "label": "Photos de chantier",
        "keywords": ["photo", "img_", "dsc_", "whatsapp image"],
    },
    {
        "key": "autres",
        "label": "Autres documents",
        "keywords": [],
    },
]

CATEGORY_LABELS = {c["key"]: c["label"] for c in CATEGORIES}
CATEGORY_ORDER = [c["key"] for c in CATEGORIES]

# Pièces exigées pour tout chantier, quel que soit le lot.
PIECES_COMMUNES = [
    {"label": "Attestation d'assurance décennale", "categorie": "admin", "keywords": ["decennale"]},
    {"label": "Plans de récolement / d'exécution", "categorie": "plans", "keywords": []},
    {"label": "Fiches techniques des matériaux posés", "categorie": "fiches", "keywords": []},
    {"label": "Notices d'entretien", "categorie": "notices", "keywords": []},
]

# Pièces spécifiques à chaque lot.
LOTS = {
    "electricite": {
        "label": "Électricité",
        "pieces": [
            {"label": "Attestation Consuel", "categorie": "certificats", "keywords": ["consuel"]},
            {"label": "Schéma électrique / synoptique", "categorie": "plans", "keywords": ["schema", "synoptique", "unifilaire"]},
            {"label": "Mesures (terre, isolement)", "categorie": "essais", "keywords": ["terre", "isolement", "mesure"]},
        ],
    },
    "plomberie": {
        "label": "Plomberie / sanitaire",
        "pieces": [
            {"label": "PV d'essai d'étanchéité / mise en pression", "categorie": "essais", "keywords": ["etancheite", "pression"]},
        ],
    },
    "gaz": {
        "label": "Gaz",
        "pieces": [
            {"label": "Certificat de conformité gaz (Qualigaz)", "categorie": "certificats", "keywords": ["gaz", "qualigaz"]},
        ],
    },
    "cvc": {
        "label": "Chauffage / ventilation / climatisation",
        "pieces": [
            {"label": "PV de mise en service des équipements", "categorie": "essais", "keywords": ["mise en service"]},
            {"label": "Fiches techniques des équipements (chaudière, PAC, VMC...)", "categorie": "fiches", "keywords": []},
            {"label": "Contrat ou notice de maintenance", "categorie": "notices", "keywords": ["maintenance", "entretien"]},
        ],
    },
    "menuiserie": {
        "label": "Menuiseries",
        "pieces": [
            {"label": "PV de classement AEV / performances", "categorie": "certificats", "keywords": ["aev", "classement", "uw"]},
        ],
    },
    "isolation": {
        "label": "Isolation / plâtrerie",
        "pieces": [
            {"label": "Certificat ACERMI des isolants", "categorie": "fiches", "keywords": ["acermi"]},
        ],
    },
    "couverture": {
        "label": "Couverture / étanchéité",
        "pieces": [
            {"label": "Avis technique des produits d'étanchéité", "categorie": "fiches", "keywords": ["avis technique"]},
            {"label": "Garantie du système d'étanchéité", "categorie": "garanties", "keywords": []},
        ],
    },
    "gros_oeuvre": {
        "label": "Gros œuvre / maçonnerie",
        "pieces": [
            {"label": "Bons de livraison / fiches béton", "categorie": "fiches", "keywords": ["beton", "livraison"]},
        ],
    },
}
