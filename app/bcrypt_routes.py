from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.models import User
from app.security import (
    BCRYPT_COST_FACTOR,
    COST_FACTOR_MAX,
    COST_FACTOR_MIN,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/bcrypt", tags=["bcrypt"])


class Generation(BaseModel):
    password: str
    rounds: int = BCRYPT_COST_FACTOR


class Verification(BaseModel):
    password: str
    hash: str


@router.post("/generate")
def generer(body: Generation, user: User = Depends(get_current_user)):
    """Génère un hash bcrypt réel depuis un mot de passe (salt aléatoire)."""
    if not body.password:
        raise HTTPException(status_code=400, detail="Le mot de passe est requis")

    rounds = body.rounds
    if not COST_FACTOR_MIN <= rounds <= COST_FACTOR_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"Cost factor (rounds) doit être entre {COST_FACTOR_MIN} et {COST_FACTOR_MAX}",
        )

    hash_bcrypt = hash_password(body.password, rounds=rounds)

    return {
        "hash": hash_bcrypt,
        "rounds": rounds,
        "format": "bcrypt",
    }


@router.post("/verify")
def verifier(body: Verification, user: User = Depends(get_current_user)):
    """Vérifie qu'un mot de passe correspond à un hash bcrypt.

    Utilise bcrypt.checkpw (comparaison native, jamais de comparaison de chaînes).
    """
    if not body.password or not body.hash:
        raise HTTPException(
            status_code=400,
            detail="Le mot de passe et le hash sont requis",
        )

    matches = verify_password(body.password, body.hash)

    return {"matches": matches}
