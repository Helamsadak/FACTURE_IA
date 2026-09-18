import sys, os, uuid
sys.path.insert(0, r"C:\Users\delllatitude\invoice-ai-system-v2")
os.chdir(r"C:\Users\delllatitude\invoice-ai-system-v2")
from app.database import SessionLocal
from app.models import User, Facture
import sqlalchemy as sa
from datetime import datetime, date

uname = "tc_" + uuid.uuid4().hex[:8]
db = SessionLocal()
# creation user si absent
u = User(username=uname, password_hash="x", role="USER")
db.add(u); db.commit(); db.refresh(u)

f = Facture(user_id=u.id, identifiant="TC-DEL-" + uuid.uuid4().hex[:8],
            fournisseur="TC Del", numero_facture="TC-N", date_facture=date(2026,9,1).isoformat(),
            montant_ht=100.0, tva=19.0, montant_ttc=119.0, devise="TND",
            created_at=datetime.utcnow())
db.add(f); db.commit(); db.refresh(f)
fid, uid = f.id, u.id
db.close()
del u, f

from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
# vrai login
r = c.post("/api/auth/login", json={"username": uname, "password": "Passw0rd!"})
print("login", r.status_code)
if r.status_code == 401:
    # le user vient d'être créé, mot de passe inconnu -> re-register + login
    r = c.post("/api/auth/register", json={"username": uname, "password": "Passw0rd!"})
    print("register", r.status_code)
    r = c.post("/api/auth/login", json={"username": uname, "password": "Passw0rd!"})
    print("login2", r.status_code)
# DELETE
try:
    d = c.delete(f"/api/factures/{fid}")
    print("DELETE", d.status_code, d.text[:300])
except Exception:
    import traceback; traceback.print_exc()
