"""Normalisation et similarité des noms de fournisseurs.

Permet de regrouper les factures d'un même professionnel malgré les
variantes d'orthographe, de casse ou de ponctuation introduites par l'OCR
ou l'extraction IA (ex. « Dr Soumaya » vs « Dr. Soumaya Maali »).
"""

import re
import unicodedata
from difflib import SequenceMatcher

from app.models import Facture

_TITRES = {"dr", "docteur", "med", "service", "cabinet", "clinique", "hopital", "centrale"}


def normaliser_nom(nom) -> str:
    """Clé de comparaison : minuscules, sans accents, sans ponctuation.

    Exemple : « Dr. Soumaya Maali » -> « soumaya maali ».
    """
    if not nom:
        return ""
    valeur = unicodedata.normalize("NFKD", str(nom))
    valeur = "".join(c for c in valeur if not unicodedata.combining(c))
    valeur = valeur.lower()
    valeur = re.sub(r"[^a-z0-9]+", " ", valeur)
    valeur = " ".join(mot for mot in valeur.split() if mot not in _TITRES)
    return re.sub(r"\s+", " ", valeur).strip()


def _similaires(cle_a: str, cle_b: str) -> bool:
    """True si deux clés normalisées désignent vraisemblablement le même nom."""
    if not cle_a or not cle_b:
        return False
    if cle_a == cle_b:
        return True
    if cle_a in cle_b or cle_b in cle_a:
        return True
    return SequenceMatcher(None, cle_a, cle_b).ratio() >= 0.8


def fournisseur_similaire(db, user_id: int, nom: str) -> str | None:
    """Nom actuel équivalent déjà présent en base pour ce fournisseur.

    Retourne le nom le plus long (le plus complet) parmi les variantes
    existantes, ou None si aucun fournisseur similaire n'est connu.
    """
    cle = normaliser_nom(nom)
    if not cle:
        return None

    noms = db.query(Facture.fournisseur).filter(
        Facture.user_id == user_id,
        Facture.fournisseur.isnot(None),
    ).distinct().all()

    meilleur = None
    cle_meilleur = ""
    for (nom_existant,) in noms:
        cle_existant = normaliser_nom(nom_existant)
        if cle_existant and _similaires(cle, cle_existant):
            if cle_existant == "inconnu":
                continue
            if meilleur is None or len(cle_existant) > len(cle_meilleur):
                meilleur = nom_existant
                cle_meilleur = cle_existant

    return meilleur