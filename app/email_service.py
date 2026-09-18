import os
import smtplib
from email.message import EmailMessage


def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_load_env()

HOST = os.environ.get("SMTP_HOST", "")
PORT = int(os.environ.get("SMTP_PORT", "587"))
USER = os.environ.get("SMTP_USER", "")
PASSWORD = os.environ.get("SMTP_PASSWORD", "")


def is_configured() -> bool:
    return all([HOST, USER, PASSWORD])


def send_reset_email(destinataire: str, reset_token: str) -> None:
    """Envoie un email contenant le lien de réinitialisation du mot de passe."""
    if not is_configured():
        raise RuntimeError(
            "SMTP non configuré. Renseignez SMTP_HOST, SMTP_USER, SMTP_PASSWORD dans .env"
        )

    lien = f"http://127.0.0.1:8000/?token={reset_token}"

    message = EmailMessage()
    message["Subject"] = "Réinitialisation de votre mot de passe - OCR Factures"
    message["From"] = USER
    message["To"] = destinataire
    message.set_content(
        f"Bonjour,\n\n"
        f"Vous avez demandé la réinitialisation de votre mot de passe.\n\n"
        f"Cliquez sur ce lien pour choisir un nouveau mot de passe :\n{lien}\n\n"
        f"Ce lien est valable 15 minutes.\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez cet email."
    )

    with smtplib.SMTP(HOST, PORT, timeout=20) as serveur:
        serveur.starttls()
        serveur.login(USER, PASSWORD)
        serveur.send_message(message)
