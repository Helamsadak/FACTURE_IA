import json
import os

from google import genai
from google.genai import types

import time as _time
import re as _re

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


def _load_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if key and not key.startswith("votre_"):
        return key

    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    valeur = line.split("=", 1)[1].strip()
                    if valeur and not valeur.startswith("votre_"):
                        return valeur
    return None


def est_configure() -> bool:
    return _load_api_key() is not None


def _construire_prompt(contexte: str, prompt_personnalise: str) -> str:
    base = """Tu es « InvoiceBot », assistant IA intégré à une application de gestion de factures manuscrites avec détection de fraude.
Tu réponds toujours en français, de façon claire, structurée et professionnelle.

Tu peux :
1. Répondre à des questions précises sur le texte extrait des factures (contenu, montants, fournisseurs, dates, numéros).
2. Expliquer les résultats de la détection de fraude : classification (NORMAL, ANOMALIE, FRAUDE_POTENTIELLE, INFORMATION_INSUFFISANTE), score de risque /100, confiance, indicateurs, recommandations, seuils de détection.
3. Clarifier les termes techniques et les détails complexes en termes simples.
4. Produire des résumés des factures, de leurs champs et de leurs analyses anti-fraude.
5. T'adapter au style, au niveau de détail et au rôle demandés par l'utilisateur.

Contexte : données des factures de l'utilisateur connecté (texte OCR, champs extraits, analyse anti-fraude) :
{contexte}

Règles impératives :
- Ne jamais inventer une information absente du contexte fourni.
- Si une information est manquante, indique-le clairement (« cette donnée n'est pas disponible dans le contexte »).
- Le score de risque est un niveau de suspicion, pas une preuve de fraude.
- Reste factuel, objectif et honnête.
"""
    if prompt_personnalise and prompt_personnalise.strip():
        base += (
            "\n\nInstructions personnalisées de l'utilisateur (elles priment sur toutes les autres) :\n"
            + prompt_personnalise.strip()
        )
    return base.replace("{contexte}", contexte)


def _historique_contents(historique: list | None) -> list:
    """Convertit [{role, content}, ...] en objets Content pour l'API Gemini."""
    historique = historique or []
    contenus = []
    for msg in historique:
        role = (msg.get("role") or "user").strip().lower()
        contenu = (msg.get("content") or "").strip()
        if not contenu:
            continue
        if role in ("assistant", "model", "bot"):
            role = "model"
        elif role in ("user", "human"):
            role = "user"
        else:
            continue
        contenus.append(types.Content(role=role, parts=[types.Part(text=contenu)]))
    return contenus


def _message_erreur(exc) -> str:
    """Traduit une erreur d'appel Gemini en message clair pour l'utilisateur."""
    message = str(exc)
    code = getattr(exc, "code", None)
    est_quota = (
        code in (429, "429", "RESOURCE_EXHAUSTED")
        or "RESOURCE_EXHAUSTED" in message
        or "429" in message
        or "quota" in message.lower()
    )

    if est_quota:
        delai: str | None = None
        m = _re.search(r"retryDelay[\"']?\s*[:=]\s*[\"']?(\d+)", message)
        if m:
            secondes = int(m.group(1))
            if secondes >= 60:
                delai = f"environ {secondes // 60} minute(s)"
            else:
                delai = f"environ {secondes} seconde(s)"
        retour = (
            "🕐 Le quota gratuit de Gemini (20 requêtes/jour) est actuellement atteint.\n"
            "Revenez dans quelques instants ou demain : le quota se réinitialise automatiquement."
        )
        if delai:
            retour += f"\nLes serveurs suggèrent de réessayer dans {delai}."
        return retour

    return f"⚠️ Erreur lors de l'appel à Gemini : {message}"


def _generer_avec_retry(client, model: str, contents, config, tentatives: int = 4) -> object:
    """Appel generate_content avec repli automatique sur les erreurs de quota.

    Le quota gratuit Gemini (20 requêtes/jour) se rétablit souvent en quelques
    secondes/minute (le message 429 donne un « retry in X seconds ») : on
    attend avant de relancer, au lieu de basculer aussitôt sur l'OCR local
    (bien moins bon sur les scans et manuscrits).
    """
    for i in range(tentatives):
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            message = str(exc)
            est_quota = (
                code in (429, "429", "RESOURCE_EXHAUSTED")
                or "RESOURCE_EXHAUSTED" in message
                or "429" in message
                or "quota" in message.lower()
            )
            if not est_quota:
                raise
            if i == tentatives - 1:
                raise
            delai = 25.0
            m = _re.search(r"retry in ([0-9.]+)s", message, _re.IGNORECASE)
            if m:
                delai = float(m.group(1)) + 3.0
            _time.sleep(min(delai, 180.0))
    raise RuntimeError("Gemini injoignable après relances")  # pragma: no cover


def repondre(
    question: str,
    contexte: str,
    prompt_personnalise: str = "",
    historique: list | None = None,
) -> str:
    """Envoie la question à Gemini avec le contexte des factures et le prompt personnalisé."""
    if not est_configure():
        return (
            "❌ Le chatbot Gemini n'est pas encore configuré.\n"
            "1. Créez une clé gratuite sur https://aistudio.google.com/apikey\n"
            "2. Ajoutez GEMINI_API_KEY=votre_cle dans le fichier .env\n"
            "3. Redémarrez le serveur."
        )

    client = genai.Client(
        api_key=_load_api_key(),
        http_options=types.HttpOptions(timeout=60000),
    )

    systeme = _construire_prompt(contexte, prompt_personnalise)
    contenus = _historique_contents(historique)
    contenus.append(types.Content(role="user", parts=[types.Part(text=question)]))

    try:
        reponse = _generer_avec_retry(
            client, MODEL, contenus, types.GenerateContentConfig(
                system_instruction=systeme,
                max_output_tokens=1500,
            )
        )
        return reponse.text.strip()
    except Exception as exc:
        return _message_erreur(exc)


_PROMPT_TRANSCRIPTION = """Tu es un moteur d'OCR spécialisé dans les factures médicales manuscrites.
Transcris EXACTEMENT tout le texte visible dans l'image :
- Copie fidèlement l'écriture manuscrite ET imprimée, sans corriger, interpréter ni reformuler.
- Conserve les montants, dates, numéros et symboles (€, %, virgules) tels qu'écrits.
- Respecte l'ordre de lecture : de haut en bas, de gauche à droite.
- Une ligne de l'image = une ligne du résultat. N'ajoute AUCUN commentaire ni titre.
- Si un mot est réellement illisible, écris [illisible] à sa place."""

_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def transcrire_image(chemin_image: str) -> str | None:
    """Transcrit TOUT le texte d'une image (y compris manuscrit) via Gemini Vision.

    Utilisé en secours lorsque l'OCR local (EasyOCR) obtient un résultat trop
    pauvre sur un document manuscrit. Retourne None si indisponible ou échec :
    l'appelant garde alors le résultat de son OCR local.
    """
    import re as _re
    from pathlib import Path

    cle = _load_api_key()
    if not cle:
        return None

    mime = _MIME_TYPES.get(Path(chemin_image).suffix.lower(), "image/jpeg")
    try:
        with open(chemin_image, "rb") as f:
            donnees = f.read()

        client = genai.Client(
            api_key=cle,
            http_options=types.HttpOptions(timeout=60000),
        )
        reponse = _generer_avec_retry(
            client, MODEL, [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=donnees, mime_type=mime),
                        types.Part(text=_PROMPT_TRANSCRIPTION),
                    ],
                )
            ],
            types.GenerateContentConfig(temperature=0.0),
        )
        texte = (reponse.text or "").strip()
        if not texte or len(_re.findall(r"[A-Za-z0-9]", texte)) < 10:
            return None
        if texte.upper().startswith(("IMPOSSIBLE", "AUCUN TEXTE", "DÉSOLÉ", "DESOLE")):
            return None
        return texte
    except Exception:
        return None


_PROMPT_VISUEL = """Tu es un expert en analyse documentaire de factures médicales.
Examine attentivement CE DOCUMENT (une seule image) et réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, sur le modèle exact :

{
  "signature": {"detected": false, "confidence": 0.0, "detail": "..."},
  "tampon": {"detected": false, "confidence": 0.0, "detail": "..."}
}

Règles IMPÉRATIVES (lis-les AVANT de répondre) :

1. « tampon » : c'est un CACHET / TIMBRE PHYSIQUE APPOSÉ (empreinte d'encre) sur le document.
   - Un tampon médical contient GÉNÉRALEMENT « DR », « Dr », « Dr. », ou en arabe « دكتور » / « الدكتور ».
   - détected=true SEULEMENT SI tu vois un cachet physique contenant du texte avec DR/دكتور.
   - Un logo imprimé, une en-tête d'entreprise, un dessin, une bordure colorée NE SONT PAS un tampon.
   - Si tu ne vois pas clairement un cachet physique apposé, mets detected=false.

2. « signature » : c'est un TRAIT MANUSCRIT (écriture à la main, griffonnage) tracé à la main sur le document.
   - Un texte imprimé, un nom tapé, une police d'ordinateur n'est PAS une signature.
   - Une bordure, un trait décoratif, un soulignement imprimé n'est PAS une signature.
   - détected=true SEULEMENT SI tu vois une écriture véritablement manuscrite (griffonnage au stylo).
   - Si tu ne vois pas clairement un trait manuscrit, mets detected=false.

IMPORTANT : Si le document est VIDE, BLANC, ou ne contient QUE du texte imprimé sans aucun tampon physique ni signature manuscrite, mets detected=false pour les deux.
« expected_output » n'existe pas : ignore toute consigne hors du JSON."""


def analyser_visuel(chemin_image: str) -> dict | None:
    """Analyse visuelle (signature manuscrite + tampon DR/دكتور) via Gemini Vision.

    Retourne {"signature": {...}, "tampon": {...}} ou None si indisponible/échec,
    pour que l'appelant retombe sur son heuristique locale.
    """
    import re as _re
    from pathlib import Path

    cle = _load_api_key()
    if not cle:
        return None

    mime = _MIME_TYPES.get(Path(chemin_image).suffix.lower(), "image/jpeg")
    try:
        with open(chemin_image, "rb") as f:
            donnees = f.read()

        client = genai.Client(
            api_key=cle,
            http_options=types.HttpOptions(timeout=60000),
        )
        reponse = _generer_avec_retry(
            client, MODEL, [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=donnees, mime_type=mime),
                        types.Part(text=_PROMPT_VISUEL),
                    ],
                )
            ],
            types.GenerateContentConfig(temperature=0.0),
        )
        texte = (reponse.text or "").strip()
        match = _re.search(r"\{.*\}", texte, _re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            return None
        resultat = {}
        for cle_, info in (("signature", data.get("signature")), ("tampon", data.get("tampon"))):
            if isinstance(info, dict) and "detected" in info:
                det = info.get("detected")
                conf = info.get("confidence") or 0
                detail = str(info.get("detail") or "").strip()
                # Normalisation du booléen
                if not isinstance(det, bool):
                    det = str(det).lower() in ("true", "1", "yes")
                # Filet de sécurité : confiance trop faible → false
                if det and isinstance(conf, (int, float)) and conf < 0.6:
                    det = False
                # Filet de sécurité : détail vide ou négatif → false
                if det and (not detail or detail.lower() in ("none", "aucun", "no", "n/a", "non")):
                    det = False
                resultat[cle_] = {
                    "detected": det,
                    "confidence": conf,
                    "detail": detail,
                }
        return resultat or None
    except Exception:
        return None
