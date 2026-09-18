"""Protection contre le brute force sur l'endpoint de connexion.

Comptage des échecs par clé (adresse IP + username) sur une fenêtre glissante.
En mémoire, adapté à une instance unique. Au-delà du seuil, la connexion est
refusée temporairement (HTTP 429) avec un message générique.
"""

import os
import threading
import time

LOGIN_MAX_TENTATIVES = int(os.environ.get("LOGIN_MAX_TENTATIVES", "5"))
LOGIN_FENETRE_SECONDES = int(os.environ.get("LOGIN_FENETRE_SECONDES", "300"))

_verrou = threading.Lock()
_echecs: dict[str, list[float]] = {}


def _nettoyer(cle: str, maintenant: float) -> list[float]:
    return [t for t in _echecs.get(cle, []) if maintenant - t < LOGIN_FENETRE_SECONDES]


def autoriser_login(cle: str) -> bool:
    """True si la clé n'a pas dépassé le nombre maximal d'échecs récents."""
    with _verrou:
        return len(_nettoyer(cle, time.monotonic())) < LOGIN_MAX_TENTATIVES


def enregistrer_echec(cle: str) -> None:
    """Enregistre un échec de connexion pour la clé."""
    with _verrou:
        maintenant = time.monotonic()
        liste = _nettoyer(cle, maintenant)
        liste.append(maintenant)
        _echecs[cle] = liste


def reinitialiser_tentatives(cle: str) -> None:
    """Réinitialise le compteur d'échecs après une connexion réussie."""
    with _verrou:
        _echecs.pop(cle, None)
