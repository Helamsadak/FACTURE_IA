from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from app.database import engine

_NOUVELLES_COLONNES_FACTURE = [
    ("identifiant", "VARCHAR"),
    ("informations", "TEXT"),
    ("texte_ocr", "TEXT"),
    ("classification", "VARCHAR"),
    ("risk_score", "INTEGER"),
    ("confidence", "INTEGER"),
    ("fraud_indicators", "TEXT"),
    ("missing_information", "TEXT"),
    ("recommendations", "TEXT"),
    ("signature_detectee", "INTEGER"),
    ("tampon_detecte", "INTEGER"),
    # Nouveautés dashboard/statistiques : devise + date d'enregistrement.
    ("devise", "VARCHAR"),
    ("created_at", "DATETIME"),
]

_NOUVELLES_COLONNES_USER = [
    ("created_at", "DATETIME"),
    ("updated_at", "DATETIME"),
    ("recovery_code_hash", "VARCHAR"),
    ("last_login", "DATETIME"),
    ("role", "VARCHAR"),
    ("full_name", "VARCHAR"),
    ("phone", "VARCHAR"),
]


def migrer():
    """Ajoute les colonnes manquantes aux tables existantes (SQLite)."""
    inspecteur = inspect(engine)
    tables = inspecteur.get_table_names()

    if "factures" in tables:
        colonnes = {c["name"] for c in inspecteur.get_columns("factures")}
        with engine.begin() as conn:
            for nom, type_sql in _NOUVELLES_COLONNES_FACTURE:
                if nom in colonnes:
                    continue
                try:
                    conn.execute(
                        text(f"ALTER TABLE factures ADD COLUMN {nom} {type_sql}")
                    )
                except OperationalError:
                    pass

    if "users" in tables:
        colonnes = {c["name"] for c in inspecteur.get_columns("users")}
        with engine.begin() as conn:
            for nom, type_sql in _NOUVELLES_COLONNES_USER:
                if nom in colonnes:
                    continue
                try:
                    conn.execute(
                        text(f"ALTER TABLE users ADD COLUMN {nom} {type_sql}")
                    )
                except OperationalError:
                    pass
            # Renseigne created_at/updated_at pour les comptes existants
            try:
                conn.execute(
                    text(
                        "UPDATE users SET created_at = datetime('now'), "
                        "updated_at = datetime('now') "
                        "WHERE created_at IS NULL OR updated_at IS NULL"
                    )
                )
            except OperationalError:
                pass
            # Rôle par défaut pour les comptes existants
            try:
                conn.execute(
                    text("UPDATE users SET role = 'USER' WHERE role IS NULL OR role = ''")
                )
            except OperationalError:
                pass
