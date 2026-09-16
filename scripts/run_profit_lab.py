#!/usr/bin/env python3
from __future__ import annotations

import json
import os

from belgium_public.config import ROOT
from belgium_public.profit_validation import run_two_stage_profit_lab


def _float_or_none(value):
    try:
        return float(value)
    except Exception:
        return None


def _compact_candidate(row: dict) -> dict:
    try:
        definition = json.loads(str(row.get("definition_json", "[]")))
        factors = [f"{x.get('feature')} {x.get('op')} {x.get('threshold')}" for x in definition if isinstance(x, dict)]
    except Exception:
        factors = [str(row.get("definition_json", ""))]
    return {
        "route": row.get("route"),
        "status": row.get("machine_status"),
        "factors": factors,
        "val_mtu": row.get("val_n_mtu"),
        "val_days": row.get("val_n_days"),
        "val_total_pnl_1mw_eur": _float_or_none(row.get("val_total_pnl_1mw_eur")),
        "val_profit_factor": _float_or_none(row.get("val_profit_factor")),
        "val_qvalue": _float_or_none(row.get("val_qvalue")),
        "final_mtu": row.get("final_n_mtu"),
        "final_days": row.get("final_n_days"),
        "final_total_pnl_1mw_eur": _float_or_none(row.get("final_total_pnl_1mw_eur")),
        "final_mean_pnl_1mw_eur_per_mtu": _float_or_none(row.get("final_mean_pnl_1mw_eur_per_mtu")),
        "final_profit_factor": _float_or_none(row.get("final_profit_factor")),
        "final_max_drawdown_1mw_eur": _float_or_none(row.get("final_max_drawdown_1mw_eur")),
        "final_no_best5_total_pnl_1mw_eur": _float_or_none(row.get("final_no_best5_total_pnl_1mw_eur")),
        "final_positive_month_fraction": _float_or_none(row.get("final_positive_month_fraction")),
        "final_qvalue": _float_or_none(row.get("final_qvalue")),
        "promotion_blocker": row.get("promotion_blocker"),
    }


def main() -> int:
    validation_from = os.getenv("BELGIUM_PROFIT_VALIDATION_FROM", "2026-04-01")
    final_holdout_from = os.getenv("BELGIUM_PROFIT_FINAL_HOLDOUT_FROM", "2026-07-01")
    profit_root = ROOT / "research" / "profit"
    registry = profit_root / "FEATURE_REGISTRY.csv"
    readiness_path = profit_root / "READINESS.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8")) if readiness_path.exists() else {}
    entry_state = str(readiness.get("entry_state", "UNKNOWN"))
    entry_promotable = bool(readiness.get("entry_promotable", False))

    routes = {
        "DA_SHORT": profit_root / "PROFIT_PANEL_DA_SHORT.parquet",
        "DA_LONG": profit_root / "PROFIT_PANEL_DA_LONG.parquet",
    }
    detailed = {}
    combined = []
    for route, panel in routes.items():
        out = profit_root / f"PROFIT_LAB_{route}.json"
        payload = run_two_stage_profit_lab(panel, registry, out, validation_from, final_holdout_from)
        detailed[route] = {
            "status": payload.get("status"),
            "candidate_count": payload.get("candidate_count", 0),
            "validation_gate_pass_count": payload.get("validation_gate_pass_count", 0),
            "economic_review_ready_count": payload.get("review_ready_count", 0),
            "train_rows": payload.get("train_rows", 0),
            "validation_rows": payload.get("validation_rows", 0),
            "final_rows": payload.get("final_rows", 0),
        }
        for row in payload.get("candidates", []):
            r = dict(row)
            r["route"] = route
            r["economic_machine_status"] = r.get("machine_status")
            if r.get("machine_status") == "REVIEW_READY" and not entry_promotable:
                r["machine_status"] = "RESEARCH_READY_ENTRY_PROVISIONAL"
                r["promotion_blocker"] = f"entry_state={entry_state};entry_promotable=false"
            combined.append(r)

    def pnl_key(r: dict) -> float:
        try:
            return float(r.get("final_total_pnl_1mw_eur", -1e18))
        except Exception:
            return -1e18

    order = {
        "REVIEW_READY": 0,
        "RESEARCH_READY_ENTRY_PROVISIONAL": 1,
        "FINAL_HOLDOUT_FAIL": 2,
        "VALIDATION_FAIL": 3,
    }
    combined.sort(key=lambda r: (order.get(str(r.get("machine_status")), 9), -pnl_key(r)))
    ready = sum(r.get("machine_status") == "REVIEW_READY" for r in combined)
    provisional = sum(r.get("machine_status") == "RESEARCH_READY_ENTRY_PROVISIONAL" for r in combined)
    status = "PASS" if combined else "PASS_NO_CANDIDATES"
    if all(str(v.get("status", "")).startswith("BLOCKED") for v in detailed.values()):
        status = "BLOCKED"
    summary = {
        "status": status,
        "objective": "DIRECT_GROSS_PNL_DA_ENTRY_TO_IMBALANCE_PT15_TWO_STAGE_OOS_BOTH_DIRECTIONS",
        "validation_from": validation_from,
        "final_holdout_from": final_holdout_from,
        "entry_state": entry_state,
        "entry_promotable": entry_promotable,
        "routes": detailed,
        "candidate_count": len(combined),
        "review_ready_count": ready,
        "research_ready_entry_provisional_count": provisional,
        "candidates": combined[:200],
        "policy": (
            "Definitions/thresholds frozen on train. Validation Apr-Jun selects under FDR; only selected candidates "
            "are opened on the Jul+ final holdout. Entry-source promotion is a separate independent gate."
        ),
    }
    out = ROOT / "research" / "PROFIT_LAB_STATUS.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(
        f"profit_lab_status={status} candidates={len(combined)} review_ready={ready} "
        f"provisional_ready={provisional} entry_state={entry_state} "
        f"validation_from={validation_from} final_holdout_from={final_holdout_from}"
    )
    for rank, row in enumerate(combined[:5], start=1):
        print(f"TOP_PROFIT_CANDIDATE_{rank}=" + json.dumps(_compact_candidate(row), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
