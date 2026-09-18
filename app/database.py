from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

# Création du dossier database s'il n'existe pas
os.makedirs("database", exist_ok=True)

# Surchargeable via l'environnement (utilisé par les tests pour une base isolée)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database/factures.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()