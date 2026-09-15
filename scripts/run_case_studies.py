from belgium_public.case_studies import build_extreme_case_registry
from belgium_public.config import ROOT

payload = build_extreme_case_registry(ROOT / "data" / "canonical", ROOT / "research" / "case_studies")
print(payload)
