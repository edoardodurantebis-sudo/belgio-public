#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

from belgium_public.config import ROOT
from belgium_public.profit_discovery import run_profit_lab


def main() -> int:
    holdout_from = os.getenv("BELGIUM_PROFIT_HOLDOUT_FROM", "2026-04-01")
    panel = ROOT / "research" / "PROFIT_PANEL.parquet"
    registry = ROOT / "config" / "profit_feature_registry.csv"
    out = ROOT / "research" / "PROFIT_LAB_STATUS.json"
    payload = run_profit_lab(panel, registry, out, holdout_from)
    print(f"profit_lab_status={payload.get('status')} holdout_from={holdout_from}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
