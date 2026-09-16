#!/usr/bin/env python3
from __future__ import annotations

import json
import os

from belgium_public.config import ROOT
from belgium_public.profit_discovery import run_profit_lab


def main() -> int:
    holdout_from = os.getenv("BELGIUM_PROFIT_HOLDOUT_FROM", "2026-04-01")
    profit_root = ROOT / "research" / "profit"
    registry = profit_root / "FEATURE_REGISTRY.csv"
    readiness_path = profit_root / "READINESS.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8")) if readiness_path.exists() else {}
    entry_state = str(readiness.get("entry_state", "UNKNOWN"))
    entry_promotable = entry_state == "CROSS_SOURCE_PUBLIC_MATCH"

    routes = {
        "DA_SHORT": profit_root / "PROFIT_PANEL_DA_SHORT.parquet",
        "DA_LONG": profit_root / "PROFIT_PANEL_DA_LONG.parquet",
    }
    detailed = {}
    combined = []
    for route, panel in routes.items():
        out = profit_root / f"PROFIT_LAB_{route}.json"
        payload = run_profit_lab(panel, registry, out, holdout_from)
        detailed[route] = {
            "status": payload.get("status"),
            "candidate_count": payload.get("candidate_count", 0),
            "economic_review_ready_count": payload.get("review_ready_count", 0),
            "train_rows": payload.get("train_rows", 0),
            "holdout_rows": payload.get("holdout_rows", 0),
        }
        for row in payload.get("candidates", []):
            r = dict(row)
            r["route"] = route
            r["economic_machine_status"] = r.get("machine_status")
            if r.get("machine_status") == "REVIEW_READY" and not entry_promotable:
                r["machine_status"] = "RESEARCH_READY_ENTRY_PROVISIONAL"
                r["promotion_blocker"] = f"entry_state={entry_state}"
            combined.append(r)

    def pnl_key(r: dict) -> float:
        try:
            return float(r.get("oos_total_pnl_1mw_eur", -1e18))
        except Exception:
            return -1e18

    order = {"REVIEW_READY": 0, "RESEARCH_READY_ENTRY_PROVISIONAL": 1, "VALIDATING": 2}
    combined.sort(key=lambda r: (order.get(str(r.get("machine_status")), 9), -pnl_key(r)))
    ready = sum(r.get("machine_status") == "REVIEW_READY" for r in combined)
    provisional = sum(r.get("machine_status") == "RESEARCH_READY_ENTRY_PROVISIONAL" for r in combined)
    status = "PASS" if combined else "PASS_NO_CANDIDATES"
    if all(str(v.get("status", "")).startswith("BLOCKED") for v in detailed.values()):
        status = "BLOCKED"
    summary = {
        "status": status,
        "objective": "DIRECT_GROSS_PNL_DA_ENTRY_TO_IMBALANCE_PT15_BOTH_DIRECTIONS",
        "holdout_from": holdout_from,
        "entry_state": entry_state,
        "entry_promotable": entry_promotable,
        "routes": detailed,
        "candidate_count": len(combined),
        "review_ready_count": ready,
        "research_ready_entry_provisional_count": provisional,
        "candidates": combined[:200],
        "policy": "Economic candidates may be discovered from public provisional entry series, but REVIEW_READY promotion requires CROSS_SOURCE_PUBLIC_MATCH plus CERTIFIED pre-gate features.",
    }
    out = ROOT / "research" / "PROFIT_LAB_STATUS.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(
        f"profit_lab_status={status} candidates={len(combined)} review_ready={ready} "
        f"provisional_ready={provisional} entry_state={entry_state} holdout_from={holdout_from}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
