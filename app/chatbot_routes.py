import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import SessionLocal
from app.gemini_service import est_configure, repondre
from app.models import Facture, User

router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


class Message(BaseModel):
    message: str
    prompt_personnalise: str = ""
    historique: list[dict] = []


def _liste(colonne: str | None) -> list:
    if not colonne:
        return []
    try:
        return json.loads(colonne)
    except (ValueError, TypeError):
        return []


def _contexte_utilisateur(user_id: int) -> str:
    db = SessionLocal()
    try:
        factures = (
            db.query(Facture)
            .filter(Facture.user_id == user_id)
            .order_by(Facture.id.desc())
            .limit(20)
            .all()
        )
    finally:
        db.close()

    if not factures:
        return "Aucune facture stockée pour cet utilisateur pour le moment."

    blocs = []
    for f in factures:
        blocs.append({
            "facture_id": f.id,
            "fournisseur": f.fournisseur,
            "numero_facture": f.numero_facture,
            "date_facture": f.date_facture,
            "montant_ht": f.montant_ht,
            "tva": f.tva,
            "montant_ttc": f.montant_ttc,
            "texte_ocr": (f.texte_ocr or "")[:1200],
            "analyse_fraude": {
                "classification": f.classification,
                "risk_score": f.risk_score,
                "confidence": f.confidence,
                "indicateurs": _liste(f.fraud_indicators),
                "informations_manquantes": _liste(f.missing_information),
                "recommandations": _liste(f.recommendations),
                "signature_detectee": f.signature_detectee,
                "tampon_detecte": f.tampon_detecte,
            },
        })

    return json.dumps(blocs, ensure_ascii=False, default=str)


def _resume_factures(user_id: int) -> str:
    """Réponse locale hors-ligne (quota Gemini épuisé): synthèse des données réelles."""
    db = SessionLocal()
    try:
        factures = (
            db.query(Facture)
            .filter(Facture.user_id == user_id)
            .order_by(Facture.id.desc())
            .all()
        )
    finally:
        db.close()

    if not factures:
        return "Aucune facture stockée pour votre compte pour le moment."

    total_ttc = sum((f.montant_ttc or 0) for f in factures)
    fournisseurs: dict[str, int] = {}
    devises: dict[str, int] = {}
    for f in factures:
        nom = f.fournisseur or "INCONNU"
        fournisseurs[nom] = fournisseurs.get(nom, 0) + 1
        d = (f.devise or "TND").upper()
        devises[d] = devises.get(d, 0) + 1
    devise = max(devises, key=devises.get) if devises else "TND"

    from app.pdf_service import _mon

    lignes = [
        f"📊 Vous avez {len(factures)} facture(s) enregistrée(s).",
        f"💰 Montant total TTC : {_mon(total_ttc, devise)}",
        "",
        "Fournisseurs :",
    ]
    for nom, nb in sorted(fournisseurs.items(), key=lambda x: -x[1]):
        lignes.append(f"   • {nom} — {nb} facture(s)")
    lignes.append("")
    lignes.append(
        "🔁 Le quota Gemini (20 requêtes/jour) sera de nouveau disponible "
        "dans un moment ou demain : je pourrai alors traiter vos questions "
        "sur le contenu et l'analyse anti-fraude."
    )
    return "\n".join(lignes)


@router.post("/message")
def message(body: Message, user: User = Depends(get_current_user)):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Le message est vide")

    if not est_configure():
        return {
            "reponse": (
                "❌ Le chatbot Gemini n'est pas encore configuré.\n"
                "1. Créez une clé gratuite sur https://aistudio.google.com/apikey\n"
                "2. Ajoutez GEMINI_API_KEY=votre_cle dans le fichier .env\n"
                "3. Redémarrez le serveur."
            )
        }

    contexte = _contexte_utilisateur(user.id)
    reponse = repondre(
        body.message.strip(),
        contexte,
        body.prompt_personnalise or "",
        body.historique or [],
    )

    if reponse.startswith("🕐 Le quota gratuit"):
        reponse = _resume_factures(user.id)

    return {"reponse": reponse}
