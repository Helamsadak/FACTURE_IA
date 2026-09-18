# -*- coding: utf-8 -*-
"""EPREUVE_AUTH.py — parle le VRAI protocole de facture_IA
(register/login -> COOKIE de session HttpOnly -> endpoints protégés).

Verifie, en HTTP réel, sur le serveur VIVANT de facture_IA (venv, 8010) :
  - la page d'accueil servie, et le verrou 401 sans session
  - inscription réelle (compte de sonde unique) -> session
  - GET /api/auth/me (identité confirmée)
  - GET /api/stats      (panneau « Répartition (TND) »)
  - GET /api/historique (panneau « Historique / traçabilité »)
  - login réel -> nouvelle session
  - création réelle d'une facture : upload -> OCR -> fraude -> analyser
  - DELETE /api/factures/{id}  <== LE TEST DU BOUTON
  - traçabilité : « creation » puis « suppression » dans le journal

Important : le serveur ne renvoie aucun jeton dans le corps JSON. La session
est transportée par COOKIE HttpOnly (signé + chiffré côté serveur). C'est ce
cookie que l'épreuve capte et réutilise, comme le navigateur.

Sortie : statuts HTTP exacts ; verdict final VERT si tout == 200.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import EPREUVE_COMMON as C


def _verdict(resultats):
    reussies = sum(1 for ok, _ in resultats if ok)
    ok = bool(resultats) and all(ok for ok, _ in resultats)
    print("=" * 66)
    if ok:
        print(f"  VERDICT : TOUT OK ({reussies}/{len(resultats)}) - serveur + auth + panneaux + pipeline + delete")
    else:
        print(f"  VERDICT : {reussies}/{len(resultats)} étapes OK - voir les '!! ' ci-dessus :")
        for ok, libelle in resultats:
            if not ok:
                print("      " + libelle)
    print("=" * 66)


if __name__ == "__main__":
    print("=" * 66)
    print("  EPREUVE_AUTH - serveur VIVANT facture_IA (venv, 8010)")
    print("=" * 66)
    resultats = C.suite()
    _verdict(resultats)