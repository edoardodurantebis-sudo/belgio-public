from belgium_public.config import ROOT
from belgium_public.lab import run_lab

p = run_lab(ROOT / "data" / "canonical", ROOT / "state" / "DATA_HEALTH.json", ROOT / "research" / "LAB_STATUS.json")
print(p)
