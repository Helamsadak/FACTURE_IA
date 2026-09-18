"""Client Groq partagé (extraction des champs + analyse anti-fraude).

Groq retire régulièrement d'anciens modèles : si le modèle principal n'existe
plus (404 model_not_found), on bascule automatiquement sur un modèle de
secours au lieu d'échouer silencieusement.
"""

import os

from groq import Groq


def _load_api_key() -> str | None:
    """Lit GROQ_API_KEY depuis l'environnement ou le fichier .env."""
    key = os.environ.get("GROQ_API_KEY")
    if key:
        return key
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GROQ_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


client = Groq(api_key=_load_api_key())

# Modèle principal (configurable via GROQ_MODEL dans .env) + secours.
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
_MODELS_SECOURS = ["groq/compound-mini"]


def completion(messages: list, max_tokens: int) -> str:
    """Appelle Groq et renvoie le texte ; bascule de modèle en cas de 404."""
    derniere_erreur: Exception | None = None
    for modele in [MODEL, *_MODELS_SECOURS]:
        try:
            reponse = client.chat.completions.create(
                model=modele,
                messages=messages,
                max_tokens=max_tokens,
            )
            return reponse.choices[0].message.content or ""
        except Exception as exc:
            message = str(exc)
            if "model_not_found" in message or "does not exist" in message.lower():
                derniere_erreur = exc
                continue
            raise
    assert derniere_erreur is not None
    raise derniere_erreur
