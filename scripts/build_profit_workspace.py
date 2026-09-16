#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date

from belgium_public.config import ROOT
from belgium_public.profit_workspace import DEFAULT_DA_START, build_workspace


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=DEFAULT_DA_START.isoformat())
    p.add_argument("--end", default="")
    args = p.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else None
    payload = build_workspace(ROOT, start=start, end=end)
    print(
        "profit_workspace_status="
        f"{payload.get('status')} entry_cache_rows={payload.get('entry_cache_rows', 0)} "
        f"certified_features={payload.get('certified_feature_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
