import cv2
import numpy as np


def _charger(chemin: str):
    try:
        img = cv2.imread(chemin)
        return img
    except Exception:
        return None


def _detecter_tampon(img) -> dict:
    """Détecte un tampon (région colorée : rouge/bleu/mauve, souvent ronde).

    Heuristique prudente : un vrai cachet est une région compacte et ronde
    (ratio proche de 1) de surface MODÉRÉE. On rejette les grandes surfaces
    colorées uniformes (logo, image, montants rouges, etc.).
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    masques = []
    for bas, haut in [
        (np.array([0, 80, 60]), np.array([10, 255, 255])),    # rouge 1
        (np.array([165, 80, 60]), np.array([179, 255, 255])), # rouge 2
        (np.array([95, 60, 60]), np.array([135, 255, 255])),  # bleu
        (np.array([130, 60, 60]), np.array([165, 255, 255])), # mauve/violet
    ]:
        masques.append(cv2.inRange(hsv, bas, haut))

    mask = masques[0]
    for m in masques[1:]:
        mask = cv2.bitwise_or(mask, m)

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    total = img.shape[0] * img.shape[1]
    pixels_colores = int((mask > 0).sum())
    frac_colore = pixels_colores / total

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # On garde les candidats compacts et ronds, de surface raisonnable
    # (>= ~0.05 % et <= ~8 % de l'image : un cachet, pas un logo plein).
    meilleurs = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.0004 * total or area > 0.08 * total:
            continue
        x, y, w, h = cv2.boundingRect(c)
        ratio = min(w, h) / max(w, h) if max(w, h) > 0 else 0
        if ratio >= 0.6:  # nettement rond/ovale
            meilleurs.append((area, ratio))

    if not meilleurs:
        return {
            "detected": False,
            "confidence": 0.7,
            "detail": "aucun cachet rond/compact de surface plausible détecté",
        }

    meilleure_area, meilleur_ratio = max(meilleurs, key=lambda t: t[0])
    confiance = round(min(1.0, 0.55 + min(0.25, meilleure_area / (0.02 * total)) + (0.15 if meilleur_ratio >= 0.8 else 0)), 2)
    # Prudence : sans un vrai rondneté, on ne déclare pas.
    if confiance < 0.6:
        return {
            "detected": False,
            "confidence": 0.7,
            "detail": "région colorée mais pas assez ronde/compacte pour être un tampon",
        }

    return {
        "detected": True,
        "confidence": confiance,
        "detail": f"cachet rond d'environ {int(meilleure_area)} px colorés ({(meilleure_area / total * 100):.2f}% de l'image)",
    }


def _detecter_signature(img) -> dict:
    """Détecte une signature manuscrite (trait continu sinonueux).

    Heuristique prudente : une vraie signature est constituée d'UN à TROIS
    traits longs, allongés et continus, concentrés dans une petite zone.
    Un texte imprimé dense ou un tableau (beaucoup de petits composants)
    est exclu pour éviter les faux positifs.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    h, w = bw.shape
    zone = bw[int(h * 0.60):, :]  # moitié inférieure : zone de signature

    n, _, stats, _ = cv2.connectedComponentsWithStats(zone, connectivity=8)

    traits = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < 60 or area > 8000:
            continue
        ratio = min(cw, ch) / max(cw, ch) if max(cw, ch) > 0 else 0
        # Trait de signature : nettement allongé (forme sinueuse continue)
        if ratio <= 0.35 and area >= 200:
            traits.append((x, y, cw, ch, area))

    if not traits:
        return {"detected": False, "confidence": 0.6,
                "detail": "aucun trait manuscrit long et continu détecté dans la zone de signature"}

    # Une signature est CONCENTRÉE : tous les traits doivent tenir dans une
    # petite fenêtre (~10 % de la largeur), pas répartis sur toute la page.
    xs = [t[0] for t in traits]
    xe = [t[0] + t[2] for t in traits]
    xmins = [t[0] + t[2] * 0.5 for t in traits]
    largeur_totale = max(xe) - min(xs)
    if largeur_totale <= 0:
        return {"detected": False, "confidence": 0.6, "detail": "zone de signature trop réduite"}

    surface_zone = zone.shape[0] * zone.shape[1]
    # Au plus quelques traits (2-3), regroupés, occupant une surface modérée.
    if len(traits) > 4:
        return {"detected": False, "confidence": 0.75,
                "detail": "trop de composants dans la zone basse : texte imprimé plutôt que signature"}

    xcentre = sum(xmins) / len(xmins)
    portee = (max(xmins) - min(xmins)) / w
    aire = sum(t[4] for t in traits)
    confiance = min(1.0, 0.5 + min(0.3, aire / (surface_zone * 0.001)) + (0.1 if portee <= 0.35 else 0))
    if confiance < 0.6:
        return {"detected": False, "confidence": 0.7,
                "detail": "la zone basse semble imprimée, pas une signature manuscrite"}

    return {
        "detected": True,
        "confidence": round(confiance, 2),
        "detail": f"{len(traits)} trait(s) manuscrit(s) continu(s) concentré(s) dans la zone de signature",
    }


def analyser_image(chemin: str) -> dict:
    """Analyse visuelle d'une facture : signature et tampon.

    Détection fiable via vision IA (un tampon médical contient « DR » / « دكتور »,
    la signature est un trait manuscrit). Le repli OpenCV n'est utilisé QUE lorsque
    Gemini n'est pas configuré : ces heuristiques sont approximatives et produisent
    des faux positifs (trait imprimé pris pour une signature, formule colorée prise
    pour un tampon). Si Gemini est configuré mais échoue (ex. quota dépassé) ou
    renvoie un résultat partiel, on préfère ne rien déclarer plutôt que de renvoyer
    une détection non fiable.
    """
    from concurrent import futures

    gemini_configure = False
    try:
        from app.gemini_service import analyser_visuel, est_configure

        gemini_configure = est_configure()  # type: ignore[attr-defined]

        def _via_gemini():
            return analyser_visuel(chemin)

        pool = futures.ThreadPoolExecutor(max_workers=1)
        try:
            futur = pool.submit(_via_gemini)
            resultat = futur.result(timeout=60)
        except Exception:
            resultat = None
        finally:
            pool.shutdown(wait=False)

        if resultat and ("signature" in resultat or "tampon" in resultat):
            return {
                "erreur": None,
                "signature": resultat.get("signature"),
                "tampon": resultat.get("tampon"),
            }
    except Exception:
        resultat = None

    # Gemini est configuré mais a échoué ou renvoyé un résultat inexploitable :
    # on évite le repli OpenCV non fiable, on ne déclare rien.
    if gemini_configure:
        return {
            "erreur": "analyse IA indisponible (quota ou erreur). Assez prudent, aucune détection renvoyée.",
            "signature": {"detected": False, "confidence": 0.0, "detail": "analyse non concluante"},
            "tampon": {"detected": False, "confidence": 0.0, "detail": "analyse non concluante"},
        }

    # Gemini non configuré : repli sur les heuristiques OpenCV approximatives.
    img = _charger(chemin)
    if img is None:
        return {
            "erreur": "image illisible",
            "signature": None,
            "tampon": None,
        }

    return {
        "erreur": None,
        "signature": _detecter_signature(img),
        "tampon": _detecter_tampon(img),
    }
