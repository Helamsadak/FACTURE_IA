import json
import re

from app.groq_client import completion as _groq_completion

_PROMPT = """Tu es un expert en extraction de données sur des factures de soins médicaux (médecins, cliniques, laboratoires, pharmacies).
Le texte OCR ci-dessous provient d'une facture et peut contenir des erreurs de reconnaissance.
Corrige les erreurs probables grâce au contexte (ex: 0 lu comme 8, 1 lu comme 7).

Extrais ces champs et réponds UNIQUEMENT avec un objet JSON valide, sans texte autour.
LES NOMS DES CHAMPS DOIVENT ÊTRE EXACTEMENT CEUX CI-DESSOUS (aucune variation) :

{
  "fournisseur": "nom de l'entreprise/professionnel de santé",
  "numero_facture": "numero de la facture",
  "date_facture": "date au format AAAA-MM-JJ",
  "devise": "ex: DT, EUR, MAD, ...",
  "montant_ht": montant hors taxes (nombre, pas de texte),
  "tva": montant de la TVA (nombre),
  "remise": remise éventuelle (nombre ou null),
  "sous_total": sous-total (nombre),
  "montant_ttc": montant toutes taxes comprises (nombre),
  "statut_paiement": "paye / non paye / partiel / inconnu",
  "mode_paiement": "especes / carte / cheque / virement / inconnu",
  "patient": {
    "nom": "...", "prenom": "...", "date_naissance": "AAAA-MM-JJ ou null",
    "numero_assure": "... ou null", "organisme_assurance": "... ou null", "adresse": "..."
  },
  "professionnel": {
    "nom": "...", "specialite": "...", "identifiant_professionnel": "... ou null",
    "matricule_fiscal": "... ou null", "adresse": "...", "telephone": "8 chiffres ou null"
  },
  "actes": [
    {"date": "AAAA-MM-JJ ou null", "description": "...", "quantite": nombre, "prix_unitaire": nombre, "montant": nombre}
  ],
  "remboursement": {
    "montant_total": nombre, "montant_pris_en_charge": nombre,
    "part_patient": nombre, "reste_a_payer": nombre, "taux_remboursement": "... ou nombre"
  }
}

IMPORTANT : Les champs montant_ht, tva, montant_ttc sont OBLIGATOIRES. Si la facture ne distingue pas HT/TVA/TTC, mets le montant total dans montant_ttc et mets montant_ht et tva à 0.

IMPORTANT (relecture exhaustive) : Ne mets ABSOLUMENT PAS patient, actes ou remboursement à vide sans avoir cherché dans TOUT le texte OCR. Ces factures de soins comportent quasi systématiquement une section patient (« assuré » : nom, prénom, n° d'assuré, organisme cnam/cnan), des lignes de soins (actes : date, désignation, quantité, PU, montant) et des montants de remboursement (« part patient », « reste à payer », « pris en charge », « total remboursé », « taux »). Si une donnée est partiellement lisible, remplis la partie lisible (un nom seul, un chiffre seul) plutôt que de mettre vide. Extrais TOUTES les lignes d'actes visibles (jusqu'à 20).

Règles :
- Si un champ est réellement absent ou illisible APRÈS une relecture complète du texte, mets une chaine vide pour le texte, null pour les objets, et 0 pour les montants.
- `actes` doit être un tableau : vide uniquement si aucune ligne de soins n'est identifiable.
- Les montants doivent être des nombres (décimale avec un point).
- Ne confonds JAMAIS l'adresse du patient et l'adresse du professionnel : mets l'adresse du patient dans `patient.adresse` et celle du médecin/cabinet dans `professionnel.adresse`. Si une seule adresse est visible, ne l'attribue pas à l'autre.

Texte OCR :
{texte}
"""


def _to_float(value) -> float:
    """Convertit une valeur en float, tolérant devise et décimales françaises.

    « 47,00 DT », « 188.00 DT » ou « 25 % » → nombre. On prend le premier
    nombre détecté, sinon 0.
    """
    if value is None or value == "":
        return 0.0
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value))
    if not m:
        return 0.0
    return float(m.group(0).replace(",", "."))


def _to_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_list(value) -> list:
    return value if isinstance(value, list) else []


def _extraire_json(structure: str) -> dict | None:
    """Tente d'extraire un objet JSON valide d'une réponse du modèle.

    Le modèle répond parfois avec du texte autour, des accents cassés ou un
    JSON tronqué : on essaie plusieurs stratégies avant d'abandonner.
    """
    candidats = []
    match = re.search(r"\{.*\}", structure, re.DOTALL)
    if match:
        candidats.append(match.group(0))
    candidats.append(structure.strip())

    for candidat in candidats:
        # On neutralise les virgules parasites/espaces insécables.
        for variant in (candidat, candidat.replace("\u202f", "").replace("\u00a0", " ")):
            try:
                return json.loads(variant)
            except Exception:
                continue
    return None


def extract_invoice_data(ocr_text: str, tentatives: int = 3) -> dict:
    """Extrait les champs d'une facture de soins (fournisseur, patient,
    professionnel, actes, remboursement, montants).

    Le modèle Groq est parfois instable (JSON invalide ou champs vides) :
    on relance l'appel jusqu'à `tentatives` fois tant qu'aucun champ utile
    n'est extrait, puis on réutilise la meilleure réponse obtenue.
    """
    prompt = _PROMPT.replace("{texte}", ocr_text)

    def _valeur_utile(obj: dict) -> bool:
        return bool(
            obj.get("numero_facture")
            or obj.get("date_facture")
            or obj.get("montant_ttc")
            or obj.get("montant_ht")
            or obj.get("tva")
            or obj.get("fournisseur")
        )

    # Champs de remboursement OBLIGATOIRES en fonction des libellés réellement
    # présents dans l'OCR : on ne relance que si le texte contenait bien la
    # ligne attendue mais que l'extraction ne l'a pas remplie.
    def _exigences_ocr() -> list[str]:
        t = ocr_text.lower()
        blocs = []
        if any(m in t for m in ("part patient", "part du patient", "part a charge")):
            blocs.append("part_patient")
        if any(m in t for m in ("pris en charge", "prise en charge", "montant rembours", "remboursement :")):
            blocs.append("montant_pris_en_charge")
        if any(m in t for m in ("reste a payer", "reste à payer", "reste a charge")):
            blocs.append("reste_a_payer")
        if "taux de remboursement" in t or "taux remboursement" in t:
            blocs.append("taux_remboursement")
        return blocs

    def _sections_remplies(obj: dict) -> bool:
        patient = obj.get("patient") if isinstance(obj.get("patient"), dict) else {}
        remb = obj.get("remboursement") if isinstance(obj.get("remboursement"), dict) else {}
        remb_fondamentaux = bool(
            remb.get("montant_total")
            or remb.get("montant_pris_en_charge")
            or remb.get("part_patient")
            or remb.get("reste_a_payer")
        )
        sections_principales = bool(
            patient.get("nom")
            or patient.get("prenom")
            or patient.get("numero_assure")
            or (isinstance(obj.get("actes"), list) and len(obj["actes"]) > 0)
            or remb_fondamentaux
        )
        # Relance tant qu'un libellé de remboursement présent dans l'OCR
        # n'a pas été rempli dans l'extraction.
        for cle in _exigences_ocr():
            if not remb.get(cle):
                return False
        return sections_principales

    def _plus_riche(a: dict, b: dict) -> bool:
        return len(json.dumps(a, ensure_ascii=False)) > len(json.dumps(b, ensure_ascii=False))

    meilleur = None
    for _ in range(tentatives):
        try:
            # Pas de str.format() ici : le prompt contient des accolades JSON.
            contenu = _groq_completion(
                [{"role": "system", "content": prompt}],
                max_tokens=5000,
            )
            donnees = _extraire_json(contenu) or {}
        except Exception:
            donnees = {}
        if meilleur is None or _plus_riche(donnees, meilleur):
            meilleur = donnees
        if _valeur_utile(donnees) and _sections_remplies(donnees):
            break

    donnees = meilleur or {}

    # Normalisation : corriger les noms de champs variants retournés par Groq
    if not donnees.get("montant_ttc") and donnees.get("montant_total"):
        donnees["montant_ttc"] = donnees.pop("montant_total")
    if not donnees.get("tva") and donnees.get("taxes"):
        donnees["tva"] = donnees.pop("taxes")
    if not donnees.get("montant_ht") and donnees.get("montant_net"):
        donnees["montant_ht"] = donnees.pop("montant_net")

    def _obj(nom: str) -> dict:
        val = donnees.get(nom)
        return val if isinstance(val, dict) else {}

    patient = (
        _obj("patient")
        or _obj("assure")
        or _obj("assure_patient")
        or _obj("patient_info")
        or {}
    )
    professionnel = (
        _obj("professionnel")
        or _obj("professional")
        or _obj("medecin")
        or _obj("fournisseur_info")
        or {}
    )
    remboursement = _obj("remboursement") or _obj("reimbursement") or _obj("remb") or {}

    # Reconstruit patient/professionnel si les champs sont à plat au niveau racine
    if not patient.get("nom"):
        patient["nom"] = donnees.get("nom_patient") or donnees.get("nom_assure") or donnees.get("patient_nom") or ""
    if not patient.get("prenom"):
        patient["prenom"] = donnees.get("prenom_patient") or donnees.get("prenom_assure") or donnees.get("patient_prenom") or ""
    if not patient.get("adresse"):
        patient["adresse"] = donnees.get("adresse_patient") or donnees.get("patient_adresse") or donnees.get("adresse_assure") or ""
    if not professionnel.get("nom"):
        professionnel["nom"] = donnees.get("nom_professionnel") or donnees.get("nom_medecin") or donnees.get("professional_nom") or donnees.get("fournisseur") or ""
    if not professionnel.get("specialite"):
        professionnel["specialite"] = donnees.get("specialite") or donnees.get("specialty") or ""

    def _float_obj(d, cle):
        try:
            v = d.get(cle)
            if v in (None, ""):
                return None
            return _to_float(v)
        except Exception:
            return None

    # Groq varie parfois les noms de clés du bloc remboursement : on recherche
    # la première clé connue non vide.
    def _float_remb(obj, cles):
        for cle in cles:
            v = _float_obj(obj, cle)
            if v is not None:
                return v
        return None

    actes = []
    for acte in _to_list(donnees.get("actes")):
        if not isinstance(acte, dict):
            continue
        actes.append(
            {
                "date": _to_str(acte.get("date")),
                "description": _to_str(acte.get("description")),
                "quantite": _float_obj(acte, "quantite"),
                "prix_unitaire": _float_obj(acte, "prix_unitaire"),
                "montant": _float_obj(acte, "montant"),
            }
        )

    return {
        "fournisseur": _to_str(donnees.get("fournisseur")),
        "numero_facture": _to_str(donnees.get("numero_facture")),
        "date_facture": _to_str(donnees.get("date_facture")),
        "devise": _to_str(donnees.get("devise")),
        "montant_ht": _to_float(donnees.get("montant_ht")),
        "tva": _to_float(donnees.get("tva")),
        "remise": _to_float(donnees.get("remise")),
        "sous_total": _to_float(donnees.get("sous_total")),
        "montant_ttc": _to_float(donnees.get("montant_ttc")),
        "statut_paiement": _to_str(donnees.get("statut_paiement")),
        "mode_paiement": _to_str(donnees.get("mode_paiement")),
        "patient": {
            "nom": _to_str(patient.get("nom")),
            "prenom": _to_str(patient.get("prenom")),
            "date_naissance": _to_str(patient.get("date_naissance")),
            "numero_assure": _to_str(patient.get("numero_assure")),
            "organisme_assurance": _to_str(patient.get("organisme_assurance")),
            "adresse": _to_str(patient.get("adresse")),
        },
        "professionnel": {
            "nom": _to_str(professionnel.get("nom")),
            "specialite": _to_str(professionnel.get("specialite")),
            "identifiant_professionnel": _to_str(professionnel.get("identifiant_professionnel")),
            "matricule_fiscal": _to_str(professionnel.get("matricule_fiscal")),
            "adresse": _to_str(professionnel.get("adresse")),
            "telephone": _to_str(professionnel.get("telephone")),
        },
        "actes": actes,
        "remboursement": {
            "montant_total": _float_remb(remboursement, ("montant_total", "total_rembourse", "total", "montant")),
            "montant_pris_en_charge": _float_remb(remboursement, ("montant_pris_en_charge", "pris_en_charge", "prise_en_charge", "montant_rembourse", "rembourse", "pris_en_chargee")),
            "part_patient": _float_remb(remboursement, ("part_patient", "part_du_patient", "part", "part_a_charge")),
            "reste_a_payer": _float_remb(remboursement, ("reste_a_payer", "reste", "reste_a_charge")),
            "taux_remboursement": _float_remb(remboursement, ("taux_remboursement", "taux", "taux_remboursement_pourcent", "pourcentage_remboursement")),
        },
    }
