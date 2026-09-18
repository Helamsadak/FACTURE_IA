# -*- coding: utf-8 -*-
"""EPREUVE_COMMON — le VRAI protocole de facture_IA.

Le serveur doit ecouter sur 8010. Cette suite verifie, en HTTP reel, le
protocole tel qu'il existe vraiment dans le code (app/main.py, app/auth.py) :

  - la page d'accueil est servie (GET / -> 200)
  - SANS session, /api/factures repond 401 (les panneaux sont verrouilles)
  - POST /api/auth/register cree un compte de sonde + pose le COOKIE de
    session HttpOnly (le serveur NE renvoie AUCUN jeton dans le corps JSON)
  - GET /api/auth/me confirme l'identite (cookie)
  - GET /api/stats      -> panneau « Repartition (TND) »
  - GET /api/historique -> panneau « Historique / tracabilite »
  - POST /api/auth/login refait une vraie connexion (nouveau cookie)
  - pipeline de creation : multipart upload -> OCR -> fraude -> analyser
    (le seul vrai chemin qui cree une facture en base)
  - GET /api/historique contient la trace « creation »
  - DELETE /api/factures/{id}  -> <== LE TEST DU BOUTON
  - GET /api/factures ne contient plus l'id (suppression reelle)
  - GET /api/historique contient la trace « suppression »

La session se transporte par COOKIE HttpOnly (signe + chiffre serveur) :
on l'echange tel quel, sans jamais besoin de le decrypter.
"""
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8010"
PORT = 8010


def _set_cookie(msg) -> str | None:
    """Extrait « session_token=<valeur> » de l'en-tete Set-Cookie."""
    try:
        valeurs = msg.get_all("Set-Cookie")
    except Exception:
        return None
    if not valeurs:
        return None
    return valeurs[-1].split(";", 1)[0].strip()


def _requete(methode, chemin, corps=None, cookie=None, multipart=None, timeout=30):
    """Sonde HTTP. Renvoie (code_http, texte, set_cookie brut). Ne plante jamais."""
    req = urllib.request.Request(URL + chemin, method=methode)
    if multipart:
        frontiere, data = multipart
        req.data = data
        req.add_header("Content-Type", "multipart/form-data; boundary=" + frontiere)
    elif corps is not None:
        req.data = json.dumps(corps).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rep:
            dat = rep.read().decode("utf-8", "replace")
            return rep.status, dat, _set_cookie(rep.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _set_cookie(e.headers)
    except Exception as e:
        return 0, "EXCEPTION " + repr(e)[:120], None


def serveur_en_ligne() -> bool:
    """Vrai si le serveur repond (meme une 401/404)."""
    try:
        req = urllib.request.Request(URL + "/", method="GET")
        with urllib.request.urlopen(req, timeout=5) as rep:
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def generer_image_facture(chemin: str) -> str:
    """Fabrique une facture-image de test lisible (PIL) ; renvoie son numero."""
    from PIL import Image, ImageDraw, ImageFont

    numero = "SONDE-" + uuid.uuid4().hex[:8].upper()
    largeur, hauteur = 1600, 1400
    img = Image.new("RGB", (largeur, hauteur), "white")
    d = ImageDraw.Draw(img)

    def police(taille):
        for nom in ("arial.ttf", "segoeui.ttf", "tahoma.ttf", "cour.ttf"):
            try:
                return ImageFont.truetype(nom, taille)
            except Exception:
                continue
        return ImageFont.load_default()

    titre = police(52)
    corps = police(38)
    lignes = [
        ("CLINIQUE SONDE IMAGINAIRE", titre),
        ("12 Rue de la Kasbah, Tunis 1006", corps),
        ("Dr. Sonde Analyse", corps),
        ("Facture N: " + numero, corps),
        ("Date: 17/09/2026", corps),
        ("Consultation cardiologie", corps),
        ("Consultation - 80.00 DT", corps),
        ("Radiographie - 20.00 DT", corps),
        ("Montant HT: 100.00 DT", corps),
        ("TVA 19 pour cent: 19.00 DT", corps),
        ("Total TTC: 119.00 DT", corps),
        ("Paye par especes", corps),
    ]
    y = 45
    for txt, fonte in lignes:
        try:
            w = d.textlength(txt, font=fonte)
        except AttributeError:
            w = d.textbbox((0, 0), txt, font=fonte)[2]
        d.text(((largeur - int(w)) // 2, y), txt, fill="black", font=fonte)
        y += 95
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    img.save(chemin)
    return numero


def _multipart(chemin):
    """Construit le corps multipart/form-data pour un upload de fichier."""
    frontiere = "----epreuve" + uuid.uuid4().hex
    nom = os.path.basename(chemin)
    with open(chemin, "rb") as f:
        contenu = f.read()
    debut = (
        "--" + frontiere + "\r\n"
        'Content-Disposition: form-data; name="file"; filename="' + nom + '"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("utf-8")
    fin = ("\r\n--" + frontiere + "--\r\n").encode("utf-8")
    return frontiere, debut + contenu + fin


def _ids_du_json(texte) -> list:
    """Ids (entiers) d'une reponse JSON liste de factures."""
    try:
        data = json.loads(texte)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [d.get("id") for d in data if isinstance(d, dict)]


def _actions_journal(texte) -> list:
    """Actions (creation/suppression/upload/...) du journal /api/historique."""
    try:
        data = json.loads(texte)
    except Exception:
        return []
    return [str(e.get("action")) for e in data if isinstance(e, dict)]


def suite():
    """Déroule le protocole réel complet. Imprime chaque étape.

    Retourne la liste [(ok: bool, libellé: str), ...] pour le verdict final.
    """
    resultats = []

    def etape(ok, libelle):
        resultats.append((ok, libelle))
        print(("  [OK ] " if ok else "  [!! ] ") + libelle)

    print("=" * 66)
    print("  Suite réelle facture_IA : register -> session -> panneaux -> pipeline -> delete")
    print("=" * 66)

    if not serveur_en_ligne():
        etape(False, "Serveur injoignable sur " + URL)
        return resultats

    # 1) page d'accueil servie
    c, t, _ = _requete("GET", "/")
    etape(c == 200, "GET /                        -> " + str(c) + "  (page d'accueil)")

    # 2) verrou : sans session, tout endpoint facture est 401
    c, t, _ = _requete("GET", "/api/factures")
    etape(c == 401, "GET /api/factures sans session -> " + str(c) + "  (doit être 401)")

    # 3) register : compte de sonde unique + COOKIE de session
    uname = "sonde_" + uuid.uuid4().hex[:8].upper()
    mdp = "Sonde2026!"
    c, t, cookie = _requete("POST", "/api/auth/register", {
        "username": uname,
        "password": mdp,
        "password_confirmation": mdp,
        "full_name": "Sonde Epreuve",
    })
    etape(c == 200 and cookie is not None,
          "POST /api/auth/register      -> " + str(c) +
          "  (session " + ("COOKIE capté" if cookie else "ABSENT !!" + " " + t[:120]) + ")")

    # 4) identite confirmee
    c, t, _ = _requete("GET", "/api/auth/me", cookie=cookie)
    etape(c == 200 and '"' + uname + '"' in t,
          "GET /api/auth/me             -> " + str(c) + "  (identité confirmée)")

    # 5) liste des factures (l'espace de l'utilisateur)
    c, t, _ = _requete("GET", "/api/factures", cookie=cookie)
    etape(c == 200, "GET /api/factures            -> " + str(c))

    # 6) panneau « Repartition (TND) »
    c, t, _ = _requete("GET", "/api/stats", cookie=cookie)
    etape(c == 200, "GET /api/stats               -> " + str(c) + "  (panneau Répartition TND)")

    # 7) panneau « Historique / tracabilite »
    c, t, _ = _requete("GET", "/api/historique", cookie=cookie)
    etape(c == 200, "GET /api/historique          -> " + str(c) + "  (panneau Historique)")

    # 8) vrai login (nouvelle session)
    c, t, cookie2 = _requete("POST", "/api/auth/login", {"username": uname, "password": mdp})
    etape(c == 200 and cookie2 is not None,
          "POST /api/auth/login         -> " + str(c) +
          "  (session " + ("COOKIE capté" if cookie2 else "ABSENT !!") + ")")

    # 9..12) pipeline réel de creation d'une facture
    img = BASE / "epreuve_test.png"
    try:
        numero = generer_image_facture(str(img))
        print("      (facture-image de test générée : " + numero + ")")

        c, t, _ = _requete("POST", "/api/factures/upload",
                           multipart=_multipart(str(img)), cookie=cookie2, timeout=30)
        m = re.search(r'"upload_id"\s*:\s*"([^"]+)"', t)
        uid = m.group(1) if m else None
        etape(c == 200 and uid,
              "POST /api/factures/upload    -> " + str(c) + "  (upload_id " +
              ("capté" if uid else "ABSENT !! " + t[:120]) + ")")

        fid = None
        if uid:
            c, t, _ = _requete("POST", "/api/factures/" + uid + "/ocr",
                               cookie=cookie2, timeout=150)
            etape(c == 200, "POST /api/factures/" + uid + "/ocr -> " + str(c))

            c, t, _ = _requete("POST", "/api/factures/" + uid + "/fraude",
                               cookie=cookie2, timeout=90)
            etape(c == 200, "POST /api/factures/" + uid + "/fraude -> " + str(c))

            c, t, _ = _requete("POST", "/api/factures/" + uid + "/analyser",
                               cookie=cookie2, timeout=150)
            m = re.search(r'"id"\s*:\s*(\d+)', t)
            fid = int(m.group(1)) if m else None
            etape(c == 200 and fid,
                  "POST /api/factures/" + uid + "/analyser -> " + str(c) +
                  "  (facture n° " + (str(fid) if fid else "inconnue !! " + t[:120]) + " créée)")

        # 13) le journal garde la trace « creation »
        c, t, _ = _requete("GET", "/api/historique", cookie=cookie2)
        actions = " ".join(_actions_journal(t))
        etape(c == 200 and "creation" in actions,
              "GET /api/historique          -> " + str(c) + "  (trace 'creation' au journal)")

        # 14) DELETE : le test du bouton
        if fid:
            c, t, _ = _requete("DELETE", "/api/factures/" + str(fid), cookie=cookie2)
            etape(c == 200, "DELETE /api/factures/" + str(fid) +
                  "    -> " + str(c) + "  <== LE TEST DU BOUTON")
        else:
            etape(False, "Aucune facture créée : pas de test DELETE")

        # 15) la liste ne contient plus l'id supprimé
        if fid:
            c, t, _ = _requete("GET", "/api/factures", cookie=cookie2)
            etape(c == 200 and fid not in _ids_du_json(t),
                  "GET /api/factures            -> " + str(c) +
                  "  (id " + str(fid) + " disparu de la liste)")

        # 16) le journal garde la trace « suppression »
        c, t, _ = _requete("GET", "/api/historique", cookie=cookie2)
        actions = " ".join(_actions_journal(t))
        etape(c == 200 and "suppression" in actions,
              "GET /api/historique          -> " + str(c) +
              "  (trace 'suppression' au journal)")
    finally:
        try:
            if img.exists():
                img.unlink()
        except OSError:
            pass

    print("=" * 66)
    return resultats