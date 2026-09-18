import re
from concurrent import futures

import cv2
import easyocr
import numpy as np

print("Chargement du modèle EasyOCR...")

reader = easyocr.Reader(
    ['fr', 'en'],
    gpu=False,
    verbose=False,
)

print("EasyOCR prêt !")

# Caractères que l'on s'attend à trouver sur une facture
_ALLOWLIST = (
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ"
    "€%().,-:;' /"
)

# Taille cible du plus grand côté : les photos de manuscrits sont souvent
# petites / floues, un agrandissement améliore nettement la reconnaissance.
_TAILLE_CIBLE = 2200


def _charger_gris(image_path: str) -> np.ndarray:
    """Charge l'image et la convertit en niveaux de gris."""
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Impossible de lire l'image : {image_path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _agrandir(gris: np.ndarray) -> np.ndarray:
    """Agrandit les petites images (photos de manuscrits) vers la taille cible."""
    h, w = gris.shape
    plus_grand = max(h, w)
    if plus_grand >= _TAILLE_CIBLE:
        return gris
    echelle = min(3.0, _TAILLE_CIBLE / plus_grand)
    return cv2.resize(
        gris,
        None,
        fx=echelle,
        fy=echelle,
        interpolation=cv2.INTER_CUBIC,
    )


def _variantes(image_path: str) -> list[np.ndarray]:
    """Prépare plusieurs variantes de l'image pour l'OCR.

    Le manuscrit tolère mal les filtres agressifs : on garde des variantes
    douces (gris, contraste local CLAHE, seuillage adaptatif) et l'OCR tente
    d'abord la variante simple avant de recourir aux autres.
    """
    gris = _agrandir(_charger_gris(image_path))

    # Contraste local adaptatif : fait ressortir une écriture pâle
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gris)

    # Seuillage adaptatif : encre sombre sur fond irrégulier (photo, ombres)
    seuil = cv2.adaptiveThreshold(
        gris, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35, 12,
    )

    return [gris, clahe, seuil]


def _fix_slashes(text: str) -> str:
    """Corrige le '1' manuscrit incliné confondu avec '/'.

    Motif 'chiffre/chiffre' (ex: '3/3') -> 'chiffre1chiffre' ('313').
    Ne touche pas aux dates (groupes de 2 chiffres : '05/07/2026').
    """
    return re.sub(r"(?<!\d)(\d)/(\d)(?!\d)", r"\g<1>1\g<2>", text)


def _group_lines(detections):
    """Regroupe les mots en lignes selon leur position verticale."""
    sorted_det = sorted(
        detections,
        key=lambda d: (round(d[0][0][1] / 20), d[0][0][0])
    )

    lines = []
    current_line = []

    for bbox, text, conf in sorted_det:
        y_center = (bbox[0][1] + bbox[2][1]) / 2

        if not current_line:
            current_line = [(y_center, text)]
            continue

        line_y = sum(y for y, _ in current_line) / len(current_line)

        if abs(y_center - line_y) < 25:
            current_line.append((y_center, text))
        else:
            current_line.sort(key=lambda item: item[0])
            lines.append(" ".join(t for _, t in current_line))
            current_line = [(y_center, text)]

    if current_line:
        current_line.sort(key=lambda item: item[0])
        lines.append(" ".join(t for _, t in current_line))

    return lines


def _iou(bbox_a, bbox_b) -> float:
    """IoU approximative entre deux boîtes (rectangles englobants)."""
    xa = [p[0] for p in bbox_a]
    ya = [p[1] for p in bbox_a]
    xb = [p[0] for p in bbox_b]
    yb = [p[1] for p in bbox_b]

    ax1, ay1, ax2, ay2 = min(xa), min(ya), max(xa), max(ya)
    bx1, by1, bx2, by2 = min(xb), min(yb), max(xb), max(yb)

    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy

    aire_a = (ax2 - ax1) * (ay2 - ay1)
    aire_b = (bx2 - bx1) * (by2 - by1)
    union = aire_a + aire_b - inter
    return inter / union if union > 0 else 0.0


def _fusionner(passages: list[list]) -> list:
    """Fusionne les détections de plusieurs passages OCR.

    Chaque mot détecté est gardé une seule fois : en cas de chevauchement
    (même zone repérée dans plusieurs variantes), on conserve la version
    ayant obtenu la meilleure confiance.
    """
    toutes = []
    for dets in passages:
        toutes.extend(dets)

    toutes.sort(key=lambda d: -d[2])

    gardees = []
    for det in toutes:
        if all(_iou(det[0], autre[0]) < 0.45 for autre in gardees):
            gardees.append(det)
    return gardees


def _qualite(texte: str) -> int:
    """Nombre de caractères exploitables (lettres/chiffres) du texte extrait."""
    return len(re.findall(r"[A-Za-z0-9]", texte))


def _transcription_gemini(image_path: str, delai_s: int = 90) -> str | None:
    """Transcription via Gemini Vision, plafonnée par un délai maximum.

    Un appel réseau peut rester bloqué indéfiniment : on l'encadre avec un
    timeout strict et on repasse la main à l'OCR local en cas d'échec.
    """
    try:
        from app.gemini_service import transcrire_image
    except Exception:
        return None

    pool = futures.ThreadPoolExecutor(max_workers=1)
    try:
        futur = pool.submit(transcrire_image, image_path)
        return futur.result(timeout=delai_s)
    except Exception:
        return None
    finally:
        pool.shutdown(wait=False)


def extract_text(image_path: str, min_confidence: float = 0.2, source: bool = False):
    """Extraction du texte d'une facture (manuscrite ou imprimée).

    Gemini Vision est interrogé EN PRIORITÉ dès qu'il est configuré : il lit
    l'écriture manuscrite bien mieux que l'OCR local. Si Gemini est absent,
    échoue ou dépasse le délai, on retombe sur EasyOCR multi-variantes.

    Si `source` vaut True, renvoie (texte, "gemini"|"easyocr") pour qu'un
    avertissement clair soit possible en cas de dégradation (quota atteint).
    """
    def _sortie(texte: str, origine: str):
        return (texte, origine) if source else texte

    transcription = _transcription_gemini(image_path)
    if transcription and _qualite(transcription) >= 30:
        return _sortie(transcription, "gemini")

    variantes = _variantes(image_path)

    def lire(img):
        results = reader.readtext(
            img,
            allowlist=_ALLOWLIST,
            paragraph=False,
            contrast_ths=0.1,
            adjust_contrast=0.7,
            width_ths=0.7,
            add_margin=0.1,
        )
        return [
            (bbox, text.strip(), float(confidence))
            for bbox, text, confidence in results
            if text.strip() and confidence >= min_confidence
        ]

    premier = lire(variantes[0])
    texte_premier = "\n".join(_fix_slashes(ligne) for ligne in _group_lines(premier))

    # Scan propre reconnu dès le premier passage : inutile de continuer.
    if _qualite(texte_premier) >= 60:
        return _sortie(texte_premier, "easyocr")

    # Manuscrit mal lu : tentatives supplémentaires + fusion des détections.
    passages = [premier]
    for variante in variantes[1:]:
        passages.append(lire(variante))

    detections = _fusionner(passages)
    lignes = _group_lines(detections)
    return _sortie("\n".join(_fix_slashes(ligne) for ligne in lignes), "easyocr")
