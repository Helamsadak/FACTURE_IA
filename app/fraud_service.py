import json
import re

from app.groq_client import completion as _groq_completion

_SYSTEME_PROMPT = """Tu es un système expert en analyse et détection d'anomalies sur les factures de soins médicaux destinées à une assurance maladie.

Analyse la facture médicale ci-dessous et vérifie sa cohérence administrative, médicale et financière.

IMPORTANT :
- Ne considère jamais automatiquement une anomalie comme une fraude.
- Une erreur OCR, une donnée manquante ou une erreur de calcul doit être classée comme une ANOMALIE et non comme une fraude.
- Signale une FRAUDE_POTENTIELLE uniquement lorsqu'il existe plusieurs indices cohérents ou un indice particulièrement important.
- Ne crée aucune information absente de la facture.
- Si une information n'est pas disponible, retourne null.
- Le score de risque représente un niveau de suspicion et non une preuve de fraude.

==============================
1. INFORMATIONS À EXTRAIRE
==============================

Extrais les informations suivantes :

FACTURE :
- numéro de facture
- date de facture
- devise
- montant total
- sous-total
- taxes
- remise
- statut de paiement
- mode de paiement

ASSURÉ / PATIENT :
- nom
- prénom
- date de naissance
- numéro d'assuré
- organisme d'assurance

PROFESSIONNEL :
- nom
- spécialité
- identifiant professionnel
- matricule fiscal
- adresse
- téléphone

ACTES :
- date
- code
- description
- quantité
- prix unitaire
- montant

REMBOURSEMENT :
- montant total
- montant pris en charge
- part patient
- reste à payer
- taux de remboursement

==============================
2. INDICATEURS DE FRAUDE
==============================

- doublon_detecte
- double_facturation
- incoherence_montants
- erreur_calcul
- incoherence_dates
- prix_inhabituels
- quantites_inhabituelles
- donnees_manquantes
- modifications_document
- donnees_patient_incoherentes
- donnees_professionnel_incoherentes
- donnees_assurance_incoherentes

==============================
3. SORTIE JSON STRICT
==============================

Retourne UNIQUEMENT un JSON valide, sans texte autour, avec cette structure :

{
  "classification": "NORMAL" | "ANOMALIE" | "FRAUDE_POTENTIELLE" | "INFORMATION_INSUFFISANTE",
  "risk_score": 0 à 100,
  "confidence": 0 à 100,
  "indicateurs": ["doublon_detecte", "erreur_calcul", ...],
  "recommandations": ["..."],
  "informations": { ...FACTURE..., ...ASSURÉ/PATIENT..., ...PROFESSIONNEL..., ...ACTES..., ...REMBOURSEMENT... },
  "missing_information": "texte libre",
}

Réponds uniquement avec le JSON.
"""


def _normaliser_liste(valeur):
    """Force une valeur (liste, chaîne ou None) en liste propre."""
    if isinstance(valeur, list):
        return [str(i).strip() for i in valeur if str(i).strip()]
    if isinstance(valeur, str):
        morceaux = re.split(r"[,;\n•\-–]+", valeur)
        return [m.strip() for m in morceaux if m.strip()]
    return []


def _vide() -> dict:
    """Structure de réponse par défaut (aucune analyse disponible)."""
    return {
        "classification": "INFORMATION_INSUFFISANTE",
        "risk_score": 0,
        "confidence": 0,
        "indicateurs": [],
        "recommandations": [],
        "informations": {},
        "missing_information": "",
    }


def analyser_facture(texte: str, visuel: dict | None = None) -> dict:
    """Analyse une facture (texte OCR + analyse visuelle optionnelle).

    `visuel` est le résultat de `visual_service.analyser_image` :
    il ajoute des indicateurs visuels (signature / tampon absents) au
    contexte transmis au modèle et aux indicateurs retournés.
    """
    return _analyser_avec_groq(texte, visuel)


def _contexte_visuel(visuel: dict | None) -> str:
    """Convertit l'analyse visuelle en contexte texte pour le modèle."""
    if not visuel:
        return ""
    signature = visuel.get("signature") or {}
    tampon = visuel.get("tampon") or {}
    sig_det = signature.get("detected") is True
    tam_det = tampon.get("detected") is True
    lignes = [
        "ANALYSE VISUELLE DU DOCUMENT (signature / tampon) :",
        f"- Signature détectée : {sig_det} (confiance {signature.get('confidence', 'n/a')})",
        f"- Tampon du professionnel détecté : {tam_det} (confiance {tampon.get('confidence', 'n/a')})",
    ]
    if not sig_det:
        lignes.append("- Note : l'absence de signature peut être normale pour certains documents.")
    return "\n".join(lignes)


def _analyser_avec_groq(texte: str, visuel: dict | None = None) -> dict:
    """Appelle Groq et parse le JSON retourné."""
    contenu_visuel = _contexte_visuel(visuel)
    message = texte
    if contenu_visuel:
        message = f"{contenu_visuel}\n\nTEXTE OCR DE LA FACTURE :\n{texte}"

    try:
        contenu = _groq_completion(
            [
                {"role": "system", "content": _SYSTEME_PROMPT},
                {"role": "user", "content": message},
            ],
            max_tokens=2200,
        )
    except Exception:
        return _vide()

    # Nettoyage : garde uniquement le premier objet JSON
    match = re.search(r"\{.*\}", contenu, re.DOTALL)
    json_str = match.group(0) if match else "{}"

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        data = {}

    resultat = _vide()
    resultat["classification"] = data.get("classification") or "INFORMATION_INSUFFISANTE"
    resultat["risk_score"] = data.get("risk_score") or 0
    resultat["confidence"] = data.get("confidence") or 0
    resultat["indicateurs"] = _normaliser_liste(data.get("indicateurs") or data.get("fraud_indicators"))
    resultat["recommandations"] = _normaliser_liste(data.get("recommandations") or data.get("recommendations"))
    resultat["informations"] = data.get("informations") or {}
    resultat["missing_information"] = data.get("missing_information") or ""

    # Alias compatibles avec l'API (JS et base de données)
    resultat["fraud_indicators"] = resultat["indicateurs"]
    resultat["recommendations"] = resultat["recommandations"]
    return resultat


def analyser_facture_parallele(texte: str) -> list:
    """Placeholder : analyse en parallèle de plusieurs factures."""
    return []
