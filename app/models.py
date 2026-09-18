from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from app.database import Base


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), index=True)

    token = Column(String, unique=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
    revoked_at = Column(DateTime, nullable=True)


class Facture(Base):
    __tablename__ = "factures"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), index=True)

    # Identifiant unique lisible (ex: FAC-8F3A-2C91)
    identifiant = Column(String, unique=True, index=True)

    fournisseur = Column(String)
    numero_facture = Column(String)
    date_facture = Column(String)

    montant_ht = Column(Float)
    tva = Column(Float)
    montant_ttc = Column(Float)

    # Devise extraite (ex: DT, EUR, MAD…). Le système est pensé pour la
    # Tunisie (TND), la valeur est donc généralement « DT » ou « TND ».
    devise = Column(String, nullable=True)

    # Date d'enregistrement en base (permet l'historique récent et les stats).
    created_at = Column(DateTime, nullable=True, index=True)

    chemin_image = Column(String)
    chemin_pdf = Column(String)
    texte_ocr = Column(Text, nullable=True)

    # Extraction étendue (JSON) : patient, professionnel, actes, remboursement…
    informations = Column(Text, nullable=True)

    classification = Column(String, nullable=True)
    risk_score = Column(Integer, nullable=True)
    confidence = Column(Integer, nullable=True)
    fraud_indicators = Column(Text, nullable=True)
    missing_information = Column(Text, nullable=True)
    recommendations = Column(Text, nullable=True)
    signature_detectee = Column(Integer, nullable=True)
    tampon_detecte = Column(Integer, nullable=True)


class Historique(Base):
    """Journal d'audit / traçabilité des actions de l'utilisateur.

    Enregistre les actions effectuées sur les factures (téléchargement, OCR,
    analyse de fraude, génération PDF, relecture, suppression…) pour permettre
    un suivi et un tableau de bord.
    """

    __tablename__ = "historique"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    facture_id = Column(Integer, nullable=True, index=True)

    # Type d'action : upload, ocr, analyse, fraude, pdf, suppression, relecture…
    action = Column(String, index=True)

    # Description lisible de l'action (ex: « Analyse de la facture FAC-XXXX »)
    description = Column(String)

    # Devise de la facture concernée (pour la cohérence TND/EUR).
    devise = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    username = Column(String, unique=True, index=True)
    # Email OPTIONNEL : l'application fonctionne 100 % en local, sans Internet.
    # Aucun email n'est requis pour s'inscrire.
    email = Column(String, unique=True, index=True, nullable=True)

    # Contient UNIQUEMENT le hash bcrypt (jamais le mot de passe en clair)
    hashed_password = Column(String)

    # Code de récupération : stocké UNIQUEMENT sous forme de hash
    # (jamais en clair), permet de réinitialiser le mot de passe sans email.
    recovery_code_hash = Column(String, nullable=True)

    # Champs optionnels du compte
    full_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    role = Column(String, default="USER")

    last_login = Column(DateTime, nullable=True)

    reset_token = Column(String, nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
