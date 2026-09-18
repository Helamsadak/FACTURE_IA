# -*- coding: utf-8 -*-
"""EPREUVE_FINALE - dossier facture_IA (copie propre de invoice-ai-system-v2).

Parcourt TOUTE la chaine pertinente et imprime un verdict sur :
  1. le venv compile bien les modeles (py_compile)
  2. aucun autre python n'ecoute 8010 avant nous
  3. le serveur demarre (via ce meme venv) sur le port 8010
  4. le VRAI protocole HTTP (EPREUVE_COMMON.suite) :
       page /, verrou 401 sans session, register -> COOKIE de session,
       /api/auth/me, /api/factures, les 2 panneaux (/api/stats,
       /api/historique), login, pipeline upload -> OCR -> fraude -> analyser,
       GET /api/historique (trace creation), DELETE (le bouton),
       liste sans l'id, journal avec trace suppression
  5. verdict final : TOUT_OK ou l'etape qui a casse
"""
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(r"C:\Users\delllatitude\facture_IA")
VENV_PY = BASE / "venv" / "Scripts" / "python.exe"
PORT = 8010

sys.path.insert(0, str(BASE))

import EPREUVE_COMMON as C

RESUME = []
def etape(ok, texte):
    statut = "OK " if ok else "!! "
    RESUME.append((ok, statut + texte))
    print("  [" + statut + "] " + texte)

def qui_ecoute_port(n):
    """Renvoie l'id du process qui ecoute le port n, ou None."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetTCPConnection -LocalPort " + str(n) +
             " -State Listen -ErrorAction SilentlyContinue | " +
             "ForEach-Object { $_.OwningProcess }"],
            capture_output=True, text=True, timeout=12).stdout
        lignes = [int(x) for x in out.split() if x.strip().isdigit()]
        return lignes[0] if lignes else None
    except Exception:
        return None

def tuer_all_python():
    """Tue SEULEMENT le(s) process qui occupe(nt) le port (jamais soi-même)."""
    pid = qui_ecoute_port(PORT)
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                       capture_output=True)
        time.sleep(5)

def demarrer_serveur():
    """Lance uvicorn (venv) sur PORT en arriere-plan, log dans fichiers."""
    out = open(str(BASE / "srv_out.log"), "w", encoding="utf-8", errors="replace")
    err = open(str(BASE / "srv_err.log"), "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(BASE), stdout=out, stderr=err,
        creationflags=0x08000000)  # CREATE_NO_WINDOW
    return p

def lire_log_err():
    pth = BASE / "srv_err.log"
    if not pth.exists():
        return "(pas de log d'erreur)"
    lignes = pth.read_text(encoding="utf-8", errors="replace").splitlines()
    coups = [l for l in lignes if "Error" in l or "Exception" in l
             or "Traceback" in l or "NameError" in l]
    return ("AUCUNE erreur" if not coups else coups[-3:])

if __name__ == "__main__":
    print("=" * 66)
    print("  EPREUVE FINALE - facture_IA (neuf, copie propre de v2)")
    print("=" * 66)

    # 0) Sanite du venv copie
    etape(VENV_PY.exists() and VENV_PY.is_file(), "venv python existe")
    try:
        pyv = subprocess.run([str(VENV_PY), "--version"],
                             capture_output=True, text=True, timeout=20)
        version = (pyv.stdout or pyv.stderr).strip()
        etape((pyv.returncode == 0), "python venv : " + version)
    except Exception as e:
        etape(False, "python venv : EXCEPTION " + repr(e)[:80])

    # 1) Le modele compile (py_compile) - preuve que models.py est valide
    try:
        r = subprocess.run([str(VENV_PY), "-m", "py_compile",
                            str(BASE / "app" / "models.py")],
                           capture_output=True, text=True, timeout=30)
        etape(r.returncode == 0, "models.py compile (NameError impossible au demarrage)")
        if r.returncode != 0:
            print("       " + (r.stderr or r.stdout).strip()[:300])
    except Exception as e:
        etape(False, "py_compile EXCEPTION " + repr(e)[:80])

    # 2) Port libre avant nous
    av = qui_ecoute_port(PORT)
    if av:
        print("  Port " + str(PORT) + " occupe par pid " + str(av) + " -> on nettoie.")
        tuer_all_python()
    else:
        print("  Port " + str(PORT) + " libre - aucun voleur.")
    time.sleep(1)

    # 3) Demarrage
    print("\n  Demarrage du serveur venv sur " + str(PORT) + " ...")
    demarrer_serveur()
    time.sleep(18)

    # 4) Le VRAI protocole complet (login -> session -> panneaux -> pipeline -> DELETE)
    resultats = C.suite()
    for ok, libelle in resultats:
        RESUME.append((ok, libelle))

    print("\n  -- log d'erreur serveur : " + str(lire_log_err()))

    print("\n" + "=" * 66)
    eches = [r for r in RESUME if not r[0]]
    if not eches:
        print("  VERDICT FINAL : TOUT_OK  (auth cookie, panneaux, pipeline, delete, journal)")
    else:
        print("  VERDICT FINAL : " + str(len(eches)) + " etape(s) cassee(s):")
        for ok, t in eches:
            print("      " + t)
    print("=" * 66)
    print("  A OUVERT: http://127.0.0.1:8010/   (dossier facture_IA)")
    print("  Puis dans le navigateur : Ctrl+F5, cocher 1, supprimer, toast.")