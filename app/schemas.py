from datetime import date
from typing import Optional

from pydantic import BaseModel


class FactureCreate(BaseModel):
    fournisseur: str
    date_facture: Optional[date] = None
    montant_ht: Optional[float] = None
    tva: Optional[float] = None
    montant_ttc: Optional[float] = None
    devise_origine: str = "EUR"      # EUR (le PDF importe), jamais cote en dur
    note: str = ""


class FactureOut(BaseModel):
    id: int
    fournisseur: str
    date_facture: Optional[date]
    montant_ht: float
    tva: float
    montant_ttc: float
    devise_origine: str
    montant_ttc_tnd: float           # converti EUR -> TND, cote serveur
    devise_affichage: str = "TND"
    note: str

    class Config:
        from_attributes = True
