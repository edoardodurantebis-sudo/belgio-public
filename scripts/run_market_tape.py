from __future__ import annotations
import argparse
from datetime import date
from belgium_public.config import ROOT
from belgium_public.market_tape import build_tape

ap = argparse.ArgumentParser()
ap.add_argument("--date", required=True)
args = ap.parse_args()
out = build_tape(date.fromisoformat(args.date), ROOT / "data" / "canonical", ROOT / "research" / "case_studies")
print(out)
