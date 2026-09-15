from datetime import date, timedelta
from belgium_public.config import ROOT
from belgium_public.jao_discovery import audit_core_domain

payload = audit_core_domain(date.today() - timedelta(days=2), ROOT / "state" / "JAO_ENDPOINT_AUDIT.json")
print(payload)
