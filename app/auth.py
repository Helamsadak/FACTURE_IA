from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import Session, User
from app.rate_limit import (
    autoriser_login,
    enregistrer_echec,
    reinitialiser_tentatives,
)
from app.security import (
    COOKIE_SECURE,
    SESSION_COOKIE_NAME,
    SESSION_HOURS,
    chiffrer_cookie,
    dechiffrer_cookie,
    expiration_session,
    generer_code_recuperation,
    generer_token_session,
    hash_password,
    hash_recovery_code,
    valider_mot_de_passe,
    verification_factice,
    verify_password,
    verify_recovery_code,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

bearer = HTTPBearer(auto_error=False)

RESET_TOKEN_MINUTES = 15

# Message générique : ne révèle jamais si le username existe ou si le mot de passe
# est mauvais (évite l'énumération d'utilisateurs et le brute force ciblé).
MESSAGE_IDENTIFIANTS_INCORRECTS = "Nom d'utilisateur ou mot de passe incorrect"


def login(username: str, password: str) -> tuple[bool, User | None]:
    """Vérifie les identifiants (username + password) à l'aide de bcrypt.

    Flux :
      1. Recherche de l'utilisateur par username.
      2. Récupération du password_hash (colonne hashed_password).
      3. Vérification via bcrypt.checkpw(password, password_hash).
      4. Retourne (True, user) si la vérification réussit, sinon (False, None).

    Le mot de passe n'est jamais stocké, loggé ni renvoyé. Aucun nouveau hash
    n'est généré pour comparer : on utilise directement la fonction de
    comparaison de bcrypt (le hash stocké contient sel et cost factor).
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            # Temps de réponse constant pour ne pas révéler l'existence du compte
            verification_factice()
            return False, None
        if not verify_password(password, user.hashed_password):
            return False, None
        return True, user
    finally:
        db.close()


ROLES_AUTORISES = {"USER", "ADMIN"}


class Inscription(BaseModel):
    username: str
    email: str | None = None
    password: str
    password_confirmation: str | None = None
    full_name: str | None = None
    phone: str | None = None
    role: str | None = None


class Connexion(BaseModel):
    username: str
    password: str


class ReinitialisationLocale(BaseModel):
    username: str
    recovery_code: str
    new_password: str
    new_password_confirmation: str | None = None


class ChangementMotDePasse(BaseModel):
    current_password: str
    new_password: str
    new_password_confirmation: str | None = None


def _extraire_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    # 1) Cookie de session (signé + chiffré côté serveur)
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        jeton = dechiffrer_cookie(cookie)
        if jeton:
            return jeton

    # 2) En-tête Authorization: Bearer ... (rétro-compatibilité)
    token = credentials.credentials if credentials is not None else None
    if token is None:
        token = request.query_params.get("token")
    return token


def _definir_cookie_session(response: Response, jeton: str) -> None:
    """Cookie de session : HttpOnly, SameSite=Lax, Secure selon la config, signé + chiffré."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=chiffrer_cookie(jeton),
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def _supprimer_cookie_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
    token = _extraire_token(request, credentials)
    if token is None:
        raise HTTPException(status_code=401, detail="Authentification requise")

    db = SessionLocal()
    try:
        session = db.query(Session).filter(Session.token == token).first()
        if session is None:
            raise HTTPException(status_code=401, detail="Session invalide")

        if session.revoked_at is not None:
            raise HTTPException(status_code=401, detail="Session révoquée")

        if session.expires_at is None or session.expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Session expirée, reconnectez-vous")

        user = db.query(User).filter(User.id == session.user_id).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Utilisateur introuvable")
        return user
    finally:
        db.close()


def _creer_session(db, user: User) -> str:
    session = Session(
        user_id=user.id,
        token=generer_token_session(),
        expires_at=expiration_session(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session.token


@router.post("/register")
def register(body: Inscription):
    db = SessionLocal()
    try:
        username = body.username.strip()
        if len(username) < 3:
            raise HTTPException(
                status_code=400,
                detail="Le nom d'utilisateur doit contenir au moins 3 caractères",
            )
        if db.query(User).filter(User.username == username).first():
            raise HTTPException(status_code=400, detail="Nom d'utilisateur déjà pris")

        if body.email:
            email = body.email.strip().lower()
            if db.query(User).filter(User.email == email).first():
                raise HTTPException(status_code=400, detail="Email déjà utilisé")
        else:
            email = None

        role = (body.role or "USER").strip().upper()
        if role not in ROLES_AUTORISES:
            role = "USER"

        # Confirmation du mot de passe (contrôle côté serveur)
        if body.password_confirmation is not None and body.password != body.password_confirmation:
            raise HTTPException(status_code=400, detail="Les mots de passe ne correspondent pas")

        # Politique de mot de passe (jamais de mot de passe en clair)
        valide, raison = valider_mot_de_passe(body.password)
        if not valide:
            raise HTTPException(status_code=400, detail=raison)

        # Validation du téléphone : exactement 8 chiffres si fourni
        telephone = (body.phone or "").strip() or None
        if telephone and (not telephone.isdigit() or len(telephone) != 8):
            raise HTTPException(
                status_code=400,
                detail="Le numéro de téléphone doit contenir exactement 8 chiffres",
            )

        # Code de récupération : généré une seule fois, affiché une seule fois,
        # stocké UNIQUEMENT sous forme de hash (jamais en clair).
        code_recuperation = generer_code_recuperation()

        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(body.password),
            recovery_code_hash=hash_recovery_code(code_recuperation),
            full_name=(body.full_name or "").strip() or None,
            phone=telephone,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        jeton = _creer_session(db, user)
        reponse = JSONResponse({
            "id": user.id,
            "username": user.username,
            "role": user.role,
            # Affiché UNE SEULE FOIS après l'inscription :
            "recovery_code": code_recuperation,
        })
        _definir_cookie_session(reponse, jeton)
        return reponse
    finally:
        db.close()


@router.post("/login")
def login_endpoint(body: Connexion, request: Request):
    # Clé de rate limiting : IP + username (anti brute force)
    ip = request.client.host if request.client else "inconnu"
    cle = f"{ip}:{body.username.strip().lower()}"

    if not autoriser_login(cle):
        raise HTTPException(
            status_code=429,
            detail="Trop de tentatives. Réessayez dans quelques minutes.",
        )

    valide, user = login(body.username, body.password)
    if not valide:
        enregistrer_echec(cle)
        raise HTTPException(status_code=401, detail=MESSAGE_IDENTIFIANTS_INCORRECTS)

    # Succès : création de la session (cookie HttpOnly chiffré) + réinitialisation
    reinitialiser_tentatives(cle)
    db = SessionLocal()
    try:
        user.last_login = datetime.utcnow()
        jeton = _creer_session(db, user)
    finally:
        db.close()
    reponse = JSONResponse({
        "id": user.id,
        "username": user.username,
        "role": user.role,
    })
    _definir_cookie_session(reponse, jeton)
    return reponse


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Vérifie la session courante (utilisée au chargement de la page)."""
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "full_name": user.full_name,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
):
    token = _extraire_token(request, credentials)
    if token is None:
        raise HTTPException(status_code=401, detail="Authentification requise")

    db = SessionLocal()
    try:
        session = db.query(Session).filter(Session.token == token).first()
        if session is not None:
            session.revoked_at = datetime.utcnow()
            db.commit()
        _supprimer_cookie_session(response)
        return {"detail": "Déconnecté"}
    finally:
        db.close()


@router.post("/forgot-password")
def forgot_password(body: ReinitialisationLocale, request: Request):
    """Récupération 100 % locale : username + code de récupération.

    Aucun email, aucune connexion Internet. Le code a été remis à
    l'utilisateur UNE SEULE FOIS lors de l'inscription (stocké en base
    uniquement sous forme de hash). Le nombre de tentatives est limité.
    """
    # Rate limiting : ne révèle jamais si le username existe ou non.
    ip = request.client.host if request.client else "inconnu"
    cle = f"rec:{ip}:{body.username.strip().lower()}"

    if not autoriser_login(cle):
        raise HTTPException(
            status_code=429,
            detail="Trop de tentatives. Réessayez dans quelques minutes.",
        )

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == body.username.strip()).first()

        # Message générique : aucune information sur l'existence du compte.
        if user is None or not user.recovery_code_hash:
            enregistrer_echec(cle)
            raise HTTPException(status_code=400, detail="Identifiants de récupération invalides")

        if not verify_recovery_code(body.recovery_code, user.recovery_code_hash):
            enregistrer_echec(cle)
            raise HTTPException(status_code=400, detail="Identifiants de récupération invalides")

        if body.new_password_confirmation is not None and body.new_password != body.new_password_confirmation:
            raise HTTPException(status_code=400, detail="Les mots de passe ne correspondent pas")

        valide, raison = valider_mot_de_passe(body.new_password)
        if not valide:
            raise HTTPException(status_code=400, detail=raison)

        user.hashed_password = hash_password(body.new_password)
        db.commit()
        reinitialiser_tentatives(cle)
        return {"detail": "Votre mot de passe a été réinitialisé avec succès."}
    finally:
        db.close()


@router.post("/reset-password")
def reset_password(body: ReinitialisationLocale, request: Request):
    """Alias de /forgot-password (même flux de récupération locale)."""
    return forgot_password(body, request)


@router.post("/change-password")
def change_password(body: ChangementMotDePasse, user: User = Depends(get_current_user)):
    """Modification du mot de passe une fois connecté (ancien mot de passe requis)."""
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect")

    if body.new_password_confirmation is not None and body.new_password != body.new_password_confirmation:
        raise HTTPException(status_code=400, detail="Les mots de passe ne correspondent pas")

    valide, raison = valider_mot_de_passe(body.new_password)
    if not valide:
        raise HTTPException(status_code=400, detail=raison)

    db = SessionLocal()
    try:
        # L'objet `user` vient de la session de get_current_user (détaché) :
        # on le re-charge dans SA session pour que la modification soit persistée.
        utilisateur = db.query(User).filter(User.id == user.id).first()
        if utilisateur is None:
            raise HTTPException(status_code=401, detail="Utilisateur introuvable")
        utilisateur.hashed_password = hash_password(body.new_password)
        db.commit()
    finally:
        db.close()
    return {"detail": "Mot de passe modifié avec succès."}
