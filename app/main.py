import json
import os
import secrets
import shutil
import time

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Body, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_

from app.auth import get_current_user, router as auth_router
from app.models import Historique as JournalDb
from datetime import datetime, timedelta
from app.bcrypt_routes import router as bcrypt_router
from app.chatbot_routes import router as chatbot_router
from app.database import Base, engine, SessionLocal
from app.models import Facture, User
from app.llm_service import extract_invoice_data
from app.ocr_service import extract_text
from app.pdf_service import donnees_to_pdf, sanitize_folder
from app.fraud_service import analyser_facture
from app.visual_service import analyser_image
from app.migration import migrer
from app.fournisseur_utils import fournisseur_similaire

migrer()
Base.metadata.create_all(bind=engine)

app = FastAPI()

os.makedirs("uploads", exist_ok=True)
os.makedirs("factures", exist_ok=True)

@app.middleware("http")
async def _statiques_sans_cache(request: Request, call_next):
    response = await call_next(request)
    chemin = request.url.path
    if chemin == "/" or chemin.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth_router)
app.include_router(bcrypt_router)
app.include_router(chatbot_router)


# ---------------------------------------------------------------------------
# Identifiant unique de facture (style ASSUROCR : FAC-XXXX-XXXX)
# ---------------------------------------------------------------------------
def _generer_identifiant() -> str:
    return f"FAC-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"


def _nouvel_identifiant(db) -> str:
    """Génère un identifiant unique (non présent en base)."""
    while True:
        identifiant = _generer_identifiant()
        existe = db.query(Facture).filter(Facture.identifiant == identifiant).first()
        if existe is None:
            return identifiant


# ---------------------------------------------------------------------------
# Staging : une facture en cours d'analyse (étapes OCR / fraude / analyse)
# Persisté sur disque pour survivre à un redémarrage du serveur.
# ---------------------------------------------------------------------------
_STAGING = {}  # upload_id -> {"user_id", "file_path", "texte_ocr", "donnees", "analyse_fraude", "created"}
_STAGING_FILE = os.path.join("uploads", "_staging.json")


def _staging_save():
    try:
        with open(_STAGING_FILE, "w", encoding="utf-8") as fichier:
            json.dump(_STAGING, fichier, ensure_ascii=False)
    except OSError:
        pass


def _staging_load():
    try:
        with open(_STAGING_FILE, "r", encoding="utf-8") as fichier:
            contenu = json.load(fichier)
    except (OSError, ValueError):
        return
    _STAGING.clear()
    for uid, item in contenu.items():
        if item.get("file_path") and os.path.exists(item["file_path"]):
            _STAGING[uid] = item


def _staging_get(upload_id: str, user_id: int) -> dict:
    item = _STAGING.get(upload_id)
    if not item or item["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Fichier introuvable ou expiré")
    return item


def _staging_cleanup():
    """Purge les fichiers temporaires de plus de 24 heures."""
    now = time.time()
    modifie = False
    for uid, item in list(_STAGING.items()):
        if now - item["created"] > 86400:
            _STAGING.pop(uid, None)
            modifie = True
            try:
                if item.get("file_path") and os.path.exists(item["file_path"]):
                    os.remove(item["file_path"])
            except OSError:
                pass
    if modifie:
        _staging_save()


_staging_load()


# ---------------------------------------------------------------------------
# Utilitaires de sérialisation
# ---------------------------------------------------------------------------
def _liste(colonne):
    if not colonne:
        return []
    try:
        val = json.loads(colonne)
    except (ValueError, TypeError):
        return []
    if isinstance(val, list):
        return [str(i).strip() for i in val if str(i).strip()]
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    return []


def _dict_informations(colonne: str | None) -> dict:
    """Parse la colonne `informations` qui contient un objet JSON.

    Ne doit PAS être confondu avec `_liste` (réservé aux colonnes-tableaux) :
    ici on reconstruit le dictionnaire complet des données extraites.
    """
    if not colonne:
        return {}
    try:
        val = json.loads(colonne)
    except (ValueError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}


def _to_dict(f: Facture) -> dict:
    return {
        "id": f.id,
        "identifiant": f.identifiant,
        "fournisseur": f.fournisseur,
        "numero_facture": f.numero_facture,
        "date_facture": f.date_facture,
        "montant_ht": f.montant_ht,
        "tva": f.tva,
        "montant_ttc": f.montant_ttc,
        "donnees": _dict_informations(f.informations) or {
            "fournisseur": f.fournisseur,
            "numero_facture": f.numero_facture,
            "date_facture": f.date_facture,
            "montant_ht": f.montant_ht,
            "tva": f.tva,
            "montant_ttc": f.montant_ttc,
        },
        "fraude": {
            "classification": f.classification,
            "risk_score": f.risk_score,
            "confidence": f.confidence,
            "fraud_indicators": _liste(f.fraud_indicators),
            "missing_information": _liste(f.missing_information),
            "recommendations": _liste(f.recommendations),
            "signature_detectee": f.signature_detectee,
            "tampon_detecte": f.tampon_detecte,
        },
    }


def _trouver_facture(db, facture_id: int, user_id: int) -> Facture:
    facture = (
        db.query(Facture)
        .filter(Facture.id == facture_id, Facture.user_id == user_id)
        .first()
    )
    if facture is None:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    return facture


def _supprimer_silencieusement(chemin):
    if chemin and os.path.exists(chemin):
        try:
            os.remove(chemin)
        except OSError:
            pass


def _journaliser(
    db,
    user: User,
    action: str,
    description: str,
    facture_id: int | None = None,
    devise: str | None = None,
):
    """Écrit une entrée dans le journal de traçabilité (table « historique »).

    Utilisé à chaque étape importante du pipeline (téléchargement, OCR,
    analyse de fraude, génération PDF/PDF, suppression…) pour garantir une
    traçabilité complète du travail effectué par l'utilisateur.
    """
    entree = JournalDb(
        user_id=user.id,
        facture_id=facture_id,
        action=action,
        description=description,
        devise=devise,
    )
    db.add(entree)
    db.commit()


# ---------------------------------------------------------------------------
# Page d'accueil
# ---------------------------------------------------------------------------
@app.get("/")
def accueil():
    return FileResponse("app/static/index.html")


# ---------------------------------------------------------------------------
# Étape 1 — Télécharger la facture (image stockée, aucune analyse)
# ---------------------------------------------------------------------------
@app.post("/api/factures/upload")
async def telecharger_facture(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    _staging_cleanup()

    upload_id = secrets.token_hex(8)
    image_dir = os.path.join("uploads", str(user.id), "tmp")
    os.makedirs(image_dir, exist_ok=True)
    extension = os.path.splitext(file.filename or "")[1] or ".jpg"
    file_path = os.path.join(image_dir, f"{upload_id}{extension}")

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    _STAGING[upload_id] = {
        "user_id": user.id,
        "file_path": file_path,
        "filename": file.filename or "facture",
        "texte_ocr": None,
        "donnees": None,
        "analyse_fraude": None,
        "created": time.time(),
    }
    _staging_save()

    return {"upload_id": upload_id, "filename": file.filename, "statut": "telecharge"}


# ---------------------------------------------------------------------------
# Analyse en lot : plusieurs fichiers traités dans la même requête
# ---------------------------------------------------------------------------
@app.post("/api/factures/batch")
async def analyser_batch(files: list[UploadFile] = File(...), user: User = Depends(get_current_user)):
    _staging_cleanup()

    resultats = []
    erreurs = []
    for fichier in files:
        try:
            up = await telecharger_facture(fichier, user)
            upload_id = up["upload_id"]
            lancer_ocr(upload_id, user)
            detecter_fraude(upload_id, user)
            res = analyser_facture_finale(upload_id, user)
            db = SessionLocal()
            try:
                facture = db.query(Facture).filter(Facture.id == res["id"], Facture.user_id == user.id).first()
                resultats.append(_to_dict(facture) if facture else res)
            finally:
                db.close()
        except HTTPException as exc:
            erreurs.append({"fichier": fichier.filename or "?", "erreur": exc.detail})
        except Exception as exc:
            erreurs.append({"fichier": fichier.filename or "?", "erreur": str(exc)})

    groupes = {}
    for f in resultats:
        nom = (f.get("donnees") or {}).get("fournisseur") or f.get("fournisseur") or "INCONNU"
        groupes.setdefault(nom, []).append(f)

    regroupes = [
        {"fournisseur": nom, "nombre": len(els), "factures": sorted(els, key=lambda x: x.get("id"), reverse=True)}
        for nom, els in groupes.items()
    ]

    return {"groupes": regroupes, "traitees": len(resultats), "erreurs": erreurs}


# ---------------------------------------------------------------------------
# Étape 2 — Lancer l'OCR (lecture du texte + extraction des champs)
# ---------------------------------------------------------------------------
@app.post("/api/factures/{upload_id}/ocr")
def lancer_ocr(upload_id: str, user: User = Depends(get_current_user)):
    item = _staging_get(upload_id, user.id)

    try:
        texte, source_ocr = extract_text(item["file_path"], source=True)
        donnees = extract_invoice_data(texte)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR impossible : {exc}")

    item["texte_ocr"] = texte
    item["donnees"] = donnees
    _staging_save()

    avertissement = None
    if source_ocr != "gemini":
        avertissement = (
            "Quota Gemini (20 requêtes/jour) atteint ou vision indisponible : "
            "l'OCR local a été utilisé, sa qualité est réduite sur les scans "
            "manuscrits et photos. Les champs peuvent être incomplets. "
            "Réessayez ce fichier après la réinitialisation du quota."
        )

    return {
        "upload_id": upload_id,
        "texte_ocr": texte,
        "donnees": donnees,
        "statut": "ocr",
        "source_ocr": source_ocr,
        "avertissement": avertissement,
    }


# ---------------------------------------------------------------------------
# Étape 3 — Détection de fraude (analyse IA sur le texte OCR)
# ---------------------------------------------------------------------------
@app.post("/api/factures/{upload_id}/fraude")
def detecter_fraude(upload_id: str, user: User = Depends(get_current_user)):
    item = _staging_get(upload_id, user.id)
    if not item["texte_ocr"]:
        raise HTTPException(status_code=400, detail="Lancez d'abord l'OCR")

    analyse = analyser_facture(item["texte_ocr"])
    item["analyse_fraude"] = analyse
    _staging_save()

    return {"upload_id": upload_id, "analyse_fraude": analyse, "statut": "fraude"}


# ---------------------------------------------------------------------------
# Étape 4 — Analyser (analyse visuelle + sauvegarde complète en base)
# ---------------------------------------------------------------------------
@app.post("/api/factures/{upload_id}/analyser")
def analyser_facture_finale(upload_id: str, user: User = Depends(get_current_user)):
    item = _staging_get(upload_id, user.id)
    if not item["texte_ocr"] or not item["donnees"]:
        raise HTTPException(status_code=400, detail="Lancez d'abord l'OCR")
    if not item["analyse_fraude"]:
        item["analyse_fraude"] = analyser_facture(item["texte_ocr"])

    donnees = item["donnees"]
    texte = item["texte_ocr"]
    analyse = item["analyse_fraude"]
    visuel = analyser_image(item["file_path"])

    # Vérification d'un doublon (même numéro de facture pour cet utilisateur)
    # et rapprochement avec un fournisseur déjà connu (mêmes dossiers/groupes).
    nom_fournisseur = donnees.get("fournisseur") or "INCONNU"
    if donnees.get("numero_facture"):
        db = SessionLocal()
        try:
            doublon = (
                db.query(Facture)
                .filter(
                    Facture.user_id == user.id,
                    Facture.numero_facture == donnees["numero_facture"],
                )
                .first()
            )
        finally:
            db.close()
        if doublon:
            _STAGING.pop(upload_id, None)
            _staging_save()
            _supprimer_silencieusement(item["file_path"])
            raise HTTPException(status_code=409, detail="Facture existe déjà")

    conn_u = SessionLocal()
    try:
        canonique = fournisseur_similaire(conn_u, user.id, nom_fournisseur)
    finally:
        conn_u.close()
    if canonique:
        nom_fournisseur = canonique

    # Génération du PDF, rangé par fournisseur
    fournisseur = sanitize_folder(nom_fournisseur)
    numero = donnees.get("numero_facture") or "sans_numero"
    date = donnees.get("date_facture") or "sans_date"

    pdf_dir = os.path.join("factures", str(user.id), fournisseur)
    pdf_path = os.path.join(pdf_dir, f"{numero}_{date}.pdf")

    visuel_sign = None
    visuel_tam = None
    if visuel:
        vsig = visuel.get("signature")
        vtam = visuel.get("tampon")
        visuel_sign = 1 if (isinstance(vsig, dict) and vsig.get("detected")) else 0
        visuel_tam = 1 if (isinstance(vtam, dict) and vtam.get("detected")) else 0

    db_ident = SessionLocal()
    try:
        identifiant = _nouvel_identifiant(db_ident)
    finally:
        db_ident.close()

    donnees_to_pdf(
        donnees,
        pdf_path,
        extra={
            "analyse": analyse,
            "texte_ocr": texte,
            "identifiant": identifiant,
            "signature_detectee": visuel_sign,
            "tampon_detecte": visuel_tam,
        },
    )

    db = SessionLocal()
    try:
        facture = Facture(
            user_id=user.id,
            identifiant=identifiant,
            fournisseur=nom_fournisseur,
            numero_facture=donnees.get("numero_facture") or None,
            date_facture=donnees.get("date_facture") or None,
            montant_ht=donnees.get("montant_ht"),
            tva=donnees.get("tva"),
            montant_ttc=donnees.get("montant_ttc"),
            devise=donnees.get("devise"),
            chemin_image=item["file_path"],
            chemin_pdf=pdf_path,
            texte_ocr=texte,
            informations=json.dumps(donnees, ensure_ascii=False),
            classification=analyse.get("classification"),
            risk_score=analyse.get("risk_score"),
            confidence=analyse.get("confidence"),
            fraud_indicators=json.dumps(analyse.get("fraud_indicators", []), ensure_ascii=False),
            missing_information=json.dumps(analyse.get("missing_information", []), ensure_ascii=False),
            recommendations=json.dumps(analyse.get("recommendations", []), ensure_ascii=False),
            signature_detectee=_bool_to_int(visuel.get("signature")),
            tampon_detecte=_bool_to_int(visuel.get("tampon")),
        )
        db.add(facture)
        db.commit()
        db.refresh(facture)

        # Traçabilité : l'ajout de la facture en base est journalisé.
        try:
            entree_journal = JournalDb(
                user_id=user.id,
                facture_id=facture.id,
                action="creation",
                description=f"Enregistrement de la facture {identifiant} "
                f"({donnees.get('fournisseur') or 'fournisseur inconnu'}, "
                f"{donnees.get('montant_ttc') or 0:.3f} TND)",
                devise=donnees.get("devise") or "TND",
            )
            db.add(entree_journal)
            db.commit()
        except Exception:
            db.rollback()
        resultat = _to_dict(facture)
    finally:
        db.close()

    _STAGING.pop(upload_id, None)
    _staging_save()

    return {
        "id": resultat["id"],
        "identifiant": resultat["identifiant"],
        "donnees": donnees,
        "texte_ocr": texte,
        "analyse_fraude": analyse,
        "analyse_visuelle": visuel,
        "statut": "termine",
    }


def _bool_to_int(visuel_cle) -> int | None:
    if not visuel_cle:
        return None
    return 1 if visuel_cle.get("detected") else 0


# ---------------------------------------------------------------------------
# Endpoint « tout en un » (rétrocompatibilité) — même pipeline complet
# ---------------------------------------------------------------------------
@app.post("/upload")
async def upload(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    upload_id = secrets.token_hex(8)
    image_dir = os.path.join("uploads", str(user.id), "tmp")
    os.makedirs(image_dir, exist_ok=True)
    extension = os.path.splitext(file.filename or "")[1] or ".jpg"
    file_path = os.path.join(image_dir, f"{upload_id}{extension}")

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    _STAGING[upload_id] = {
        "user_id": user.id,
        "file_path": file_path,
        "filename": file.filename or "facture",
        "texte_ocr": None,
        "donnees": None,
        "analyse_fraude": None,
        "created": time.time(),
    }
    _staging_save()

    try:
        item = _staging_get(upload_id, user.id)
        texte = extract_text(file_path)
        donnees = extract_invoice_data(texte)
        item["texte_ocr"] = texte
        item["donnees"] = donnees
        item["analyse_fraude"] = analyser_facture(texte)
        _staging_save()
        final = analyser_facture_finale(upload_id, user)
    except HTTPException:
        raise
    except Exception as exc:
        _STAGING.pop(upload_id, None)
        _staging_save()
        _supprimer_silencieusement(file_path)
        raise HTTPException(status_code=500, detail=f"Traitement impossible : {exc}")

    return final


# ---------------------------------------------------------------------------
# Fichiers (image / PDF)
# ---------------------------------------------------------------------------
@app.get("/file/image/{facture_id}")
def fichier_image(facture_id: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        facture = _trouver_facture(db, facture_id, user.id)
        if not facture.chemin_image or not os.path.exists(facture.chemin_image):
            raise HTTPException(status_code=404, detail="Image introuvable")
        return FileResponse(facture.chemin_image)
    finally:
        db.close()


def _regenerer_pdf(facture, analyse: dict | None = None) -> str:
    """Régénère le PDF d'une facture à partir des données stockées.

    Format lisible garanti : les valeurs structurées (fraude, remboursement)
    sont présentées en « libellé : valeur » et jamais en JSON/repr brut.
    """
    def _champ(v):
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return v
        return v

    if analyse is None:
        analyse = {
            "classification": facture.classification,
            "risk_score": facture.risk_score,
            "confidence": facture.confidence,
            "fraud_indicators": _champ(facture.fraud_indicators),
            "recommendations": _champ(facture.recommendations),
            "missing_information": _champ(facture.missing_information),
        }

    try:
        donnees = json.loads(facture.informations) if facture.informations else {}
    except (ValueError, TypeError):
        donnees = {}
    if not isinstance(donnees, dict):
        donnees = {}

    chemin = facture.chemin_pdf
    if not chemin:
        dossier = sanitize_folder(facture.fournisseur or "INCONNU")
        numero = facture.numero_facture or "sans_numero"
        date = facture.date_facture or "sans_date"
        chemin = os.path.join("factures", str(facture.user_id), dossier, f"{numero}_{date}.pdf")

    donnees_to_pdf(
        donnees,
        chemin,
        extra={"analyse": analyse, "identifiant": facture.identifiant},
    )
    return chemin


@app.get("/file/pdf/{facture_id}")
def fichier_pdf(facture_id: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        facture = _trouver_facture(db, facture_id, user.id)
        # Le PDF est TOUJOURS régénéré à partir des données stockées : un ancien
        # fichier (format de fraude brut) ne peut plus être servi tel quel.
        try:
            chemin = _regenerer_pdf(facture)
        except Exception:
            chemin = facture.chemin_pdf
        if chemin and chemin != facture.chemin_pdf:
            facture.chemin_pdf = chemin
            db.commit()
        if not chemin or not os.path.exists(chemin):
            raise HTTPException(status_code=404, detail="PDF introuvable")
        return FileResponse(chemin, media_type="application/pdf")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Liste / recherche / détail
# ---------------------------------------------------------------------------
@app.get("/api/factures")
def lister_factures(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        factures = (
            db.query(Facture)
            .filter(Facture.user_id == user.id)
            .order_by(Facture.id.desc())
            .all()
        )
        return [_to_dict(f) for f in factures]
    finally:
        db.close()


@app.get("/api/factures/recherche")
def rechercher_factures(fournisseur: str, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        motif = f"%{fournisseur.strip()}%"
        factures = (
            db.query(Facture)
            .filter(
                Facture.user_id == user.id,
                or_(
                    Facture.fournisseur.ilike(motif),
                    Facture.numero_facture.ilike(motif),
                    Facture.date_facture.ilike(motif),
                    Facture.identifiant.ilike(motif),
                ),
            )
            .order_by(Facture.id.desc())
            .all()
        )
        return [_to_dict(f) for f in factures]
    finally:
        db.close()


@app.get("/api/factures/par-fournisseur")
def factures_par_fournisseur(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        factures = (
            db.query(Facture)
            .filter(Facture.user_id == user.id)
            .order_by(Facture.fournisseur.asc(), Facture.id.desc())
            .all()
        )
    finally:
        db.close()

    groupes = {}
    for f in factures:
        nom = f.fournisseur or "INCONNU"
        groupes.setdefault(nom, []).append(_to_dict(f))

    return [
        {"fournisseur": nom, "nombre": len(els), "factures": els}
        for nom, els in groupes.items()
    ]


@app.post("/api/factures/selection")
def factures_selection(ids: list[int] = Body(..., embed=True), user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        factures = (
            db.query(Facture)
            .filter(Facture.user_id == user.id, Facture.id.in_(ids))
            .order_by(Facture.fournisseur.asc(), Facture.id.desc())
            .all()
        )
    finally:
        db.close()

    groupes = {}
    for f in factures:
        nom = f.fournisseur or "INCONNU"
        groupes.setdefault(nom, []).append(_to_dict(f))

    return [
        {"fournisseur": nom, "nombre": len(els), "factures": els}
        for nom, els in groupes.items()
    ]


@app.get("/api/factures/{facture_id}")
def detail_facture(facture_id: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        facture = _trouver_facture(db, facture_id, user.id)
        return _to_dict(facture)
    finally:
        db.close()


@app.post("/api/factures/{facture_id}/reanalyser-fraude")
def reanalyser_fraude(facture_id: int, user: User = Depends(get_current_user)):
    """Relance l'analyse anti-fraude sur une facture déjà stockée."""
    db = SessionLocal()
    try:
        facture = _trouver_facture(db, facture_id, user.id)
        texte = facture.texte_ocr
        chemin = facture.chemin_image
    finally:
        db.close()

    if not texte:
        if not chemin or not os.path.exists(chemin):
            raise HTTPException(status_code=404, detail="Image introuvable")
        texte = extract_text(chemin)

    visuel = analyser_image(chemin) if chemin and os.path.exists(chemin) else None
    analyse = analyser_facture(texte, visuel) or {}

    db = SessionLocal()
    try:
        facture = db.query(Facture).filter(Facture.id == facture_id).first()
        if facture is None:
            raise HTTPException(status_code=404, detail="Facture introuvable")
        facture.classification = analyse.get("classification")
        facture.risk_score = analyse.get("risk_score")
        facture.confidence = analyse.get("confidence")
        facture.fraud_indicators = json.dumps(analyse.get("fraud_indicators", []), ensure_ascii=False)
        facture.missing_information = json.dumps(analyse.get("missing_information", []), ensure_ascii=False)
        facture.recommendations = json.dumps(analyse.get("recommendations", []), ensure_ascii=False)
        if visuel and visuel.get("signature"):
            facture.signature_detectee = 1 if visuel["signature"].get("detected") else 0
        if visuel and visuel.get("tampon"):
            facture.tampon_detecte = 1 if visuel["tampon"].get("detected") else 0

        # Régénère le PDF du rapport (format lisible) pour la nouvelle analyse.
        try:
            chemin_pdf = _regenerer_pdf(facture, analyse)
            facture.chemin_pdf = chemin_pdf
        except Exception:
            pass  # un échec d'impression ne doit pas faire échouer la ré-analyse

        db.commit()
    finally:
        db.close()

    return {"detail": "Analyse mise à jour", "analyse_fraude": analyse, "analyse_visuelle": visuel}


@app.delete("/api/factures/{facture_id}")
def supprimer_facture(facture_id: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        facture = _trouver_facture(db, facture_id, user.id)

        for chemin in (facture.chemin_image, facture.chemin_pdf):
            _supprimer_silencieusement(chemin)

        _journaliser(
            db,
            user,
            facture_id=facture_id,
            action="suppression",
            description=f"Suppression de la facture {facture.identifiant} "
            f"({facture.fournisseur or 'fournisseur inconnu'}, "
            f"{facture.montant_ttc or 0:.3f} TND)",
            devise="TND",
        )

        db.delete(facture)
        db.commit()
        return {"detail": f"Facture {facture_id} supprimée"}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Statistiques agrégées (tableau de bord) — aucune donnée sensible, uniquement
# des agrégats par fournisseur, en TND. Utilisé par la page d'accueil.
# ---------------------------------------------------------------------------
@app.get("/api/stats")
def stats_globales(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        factures = (
            db.query(Facture)
            .filter(Facture.user_id == user.id)
            .order_by(Facture.created_at.desc(), Facture.id.desc())
            .all()
        )

        total_factures = len(factures)
        total_ttc = sum((f.montant_ttc or 0) for f in factures)
        total_ht = sum((f.montant_ht or 0) for f in factures)
        total_tva = sum((f.tva or 0) for f in factures)
        nombre_a_risque = sum(
            1
            for f in factures
            if f.risk_score is not None and f.risk_score >= 50
        )

        # Devise d'affichage : toujours le dinar tunisien (TND).
        devise_totaux = "TND"

        # Regroupement par fournisseur avec nombre + total TTC.
        par_fournisseur = {}
        for f in factures:
            nom = (f.fournisseur or "INCONNU").strip()
            cle = nom.lower()
            agg = par_fournisseur.setdefault(
                cle, {"fournisseur": nom or "INCONNU", "nombre": 0, "total_ttc": 0}
            )
            agg["nombre"] += 1
            agg["total_ttc"] += f.montant_ttc or 0

        # Depuis 7 derniers jours (activité récente).
        seuil = datetime.utcnow() - timedelta(days=7)
        recentes_7j = sum(
            1
            for f in factures
            if f.created_at is not None and f.created_at >= seuil
        )

        return {
            "total_factures": total_factures,
            "total_ttc": round(total_ttc, 3),
            "total_ht": round(total_ht, 3),
            "total_tva": round(total_tva, 3),
            "nombre_a_risque": nombre_a_risque,
            "recentes_7j": recentes_7j,
            "devise_totaux": devise_totaux,
            "par_fournisseur": sorted(
                par_fournisseur.values(),
                key=lambda p: (-p["nombre"], p["fournisseur"].lower()),
            ),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Historique / traçabilité : le journal d'audit des actions de l'utilisateur
# (ajout, OCR, analyse fraude, génération PDF, suppression…). Ordre anti‑chrono.
# ---------------------------------------------------------------------------
@app.get("/api/historique")
def historique_actions(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        entrées = (
            db.query(JournalDb)
            .filter(JournalDb.user_id == user.id)
            .order_by(JournalDb.created_at.desc(), JournalDb.id.desc())
            .all()
        )
        return [
            {
                "id": e.id,
                "facture_id": e.facture_id,
                "action": e.action or "—",
                "description": e.description or "—",
                "devise": e.devise or "TND",
                "created_at": (
                    e.created_at.isoformat()
                    if e.created_at is not None
                    else None
                ),
            }
            for e in entrées
        ]
    finally:
        db.close()
