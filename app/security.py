import base64
import hashlib
import hmac
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet


def _charger_env():
    """Charge le fichier .env local (sans dépendance externe, 100 % hors-ligne)."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_charger_env()

# ===== Secret key (signature + chiffrement des cookies de session) =====
# La clé doit impérativement être définie dans .env (variable SECRET_KEY).
# Un fallback aléatoire est généré uniquement pour éviter un secret par défaut connu,
# mais les sessions existantes seront invalidées à chaque redémarrage dans ce cas.
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print(
        "⚠️  SECRET_KEY non définie dans .env : clé aléatoire générée. "
        "Définissez SECRET_KEY pour des sessions stables et sûres.",
        file=sys.stderr,
    )

SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "2"))
SESSION_COOKIE_NAME = "session_token"
# HttpOnly + SameSite=Lax par défaut. Secure uniquement en HTTPS (COOKIE_SECURE=true).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").strip().lower() == "true"


def _fernet() -> Fernet:
    """Dérive une clé Fernet (32 octets) depuis SECRET_KEY pour signer+chiffrer les cookies."""
    digeste = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digeste))


def chiffrer_cookie(token: str) -> str:
    """Chiffre et signe le jeton de session pour le stocker dans un cookie."""
    return _fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def dechiffrer_cookie(valeur: str) -> str | None:
    """Retourne le jeton de session, ou None si la signature/l'intégrité est invalide."""
    try:
        return _fernet().decrypt(valeur.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


# ===== Configuration bcrypt =====
# Cost factor configurable via l'environnement (défaut : 12).
def _charger_cost_factor() -> int:
    try:
        return int(os.environ.get("BCRYPT_COST_FACTOR", "12"))
    except ValueError:
        return 12


BCRYPT_COST_FACTOR = _charger_cost_factor()
COST_FACTOR_MIN = 10             # plage minimale de l'outil
COST_FACTOR_MAX = 14             # plage maximale de l'outil
MIN_PASSWORD_LENGTH = 8          # politique de mot de passe


def hash_password(password: str, rounds: int = BCRYPT_COST_FACTOR) -> str:
    """Hash un mot de passe avec bcrypt (salt aléatoire généré automatiquement).

    Le résultat a la forme $2b$<rounds>$... et n'est jamais réversible.
    """
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=rounds),
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Vérifie qu'un mot de passe correspond à un hash bcrypt (bcrypt.checkpw).

    Ne JAMAIS hacher plain puis comparer les deux hash avec == :
    bcrypt utilise un sel aléatoire, deux hachages du même mot de passe diffèrent.
    Le hash stocké contient le sel et le cost factor nécessaires à la vérification.
    """
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Hash invalide ou mal formé
        return False


_HASH_FACTICE = bcrypt.hashpw(b"mot-de-passe-factice", bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR))


def verification_factice() -> None:
    """Effectue une vérification bcrypt sur un hash factice.

    Permet de conserver un temps de réponse quasi identique lorsque le username
    n'existe pas (anti-énumération d'utilisateurs par mesure de temps).
    Le mot de passe fourni n'est jamais loggé ni stocké.
    """
    bcrypt.checkpw(b"mot-de-passe-factice", _HASH_FACTICE)


def valider_mot_de_passe(password: str) -> tuple[bool, str]:
    """Politique de mot de passe : longueur minimale, pas de mot de passe trivial."""
    if not password:
        return False, "Le mot de passe est requis"
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, (
            f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères"
        )
    if password.lower() in {"password", "12345678", "motdepasse", "azertyui"}:
        return False, "Ce mot de passe est trop faible"
    return True, ""


def generer_token_session() -> str:
    return secrets.token_urlsafe(48)


def expiration_session() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)


# ===== Code de récupération (local, sans email) =====
# Format : XXXX-XXXX-XXXX-XXXX (16 caractères hexadécimaux = 64 bits d'aléa
# cryptographique). Généré avec `secrets`, jamais avec Math.random() ou
# une source prévisible. Jamais affiché dans les logs.
def generer_code_recuperation() -> str:
    blocs = [secrets.token_hex(2).upper() for _ in range(4)]
    return "-".join(blocs)


def hash_recovery_code(code: str) -> str:
    """Hash le code de récupération (HMAC-SHA256, sel implicite : SECRET_KEY).

    Le code n'est JAMAIS stocké en clair en base : seule la signature HMAC
    est enregistrée. La comparaison est faite en temps constant.
    La casse et les espaces sont normalisés avant le hash.
    """
    normalise = code.strip().upper()
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        normalise.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_recovery_code(code: str, code_hash: str) -> bool:
    """Compare un code fourni au hash stocké, en temps constant."""
    if not code or not code_hash:
        return False
    return hmac.compare_digest(
        hash_recovery_code(code.strip().upper()),
        code_hash,
    )
