#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import hashlib
import sys

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
    # Operational hold: the current source contract has no verified record-level
    # publication/vintage provenance. Preserve prior diagnostics; keep collectors
    # and NRT capture running so new evidence continues accumulating.
    from datetime import datetime, timezone
    health_path = ROOT / "state" / "DATA_HEALTH.json"
    health = json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else {}
    hold = {
        "status": "PIT_UNCERTIFIED", "operational_status": "BLOCKED_CLEANLY",
        "source_health": health.get("overall_status", "UNKNOWN"),
        "reason": "Record-level publication/vintage provenance is not admitted; source health alone cannot certify PIT.",
        "research_executed": False, "promotion_eligible": False,
        "prior_diagnostics_preserved": True,
        "collectors_and_nrt_continue": True,
        "resumption_requirement": "Verified source record provenance and an admitted ingestion contract; no boolean override.",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_identity": {k: os.environ.get(k, "") for k in
                             ["GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA"]},
    }
    out = ROOT / "research" / "PROFIT_COMPUTE_STATUS.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from governance.operation_status import record, source_identity, publish
    previous = json.loads(out.read_text()) if out.exists() else None
    if previous and 'schema' not in previous:
        if previous.get('status') != 'PIT_UNCERTIFIED' or previous.get('research_executed') is not False:
            raise ValueError('UNRECOGNIZED_LEGACY_PROFIT_STATUS')
        previous = None  # First observation under the versioned status contract.
    inputs = {}
    for path in sorted((ROOT / 'research' / 'profit').glob('PROFIT_PANEL_*.parquet')):
        with path.open('rb') as stream:
            inputs[path.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    registry = ROOT / 'research/profit/FEATURE_REGISTRY.csv'
    inputs['feature_registry'] = hashlib.sha256(registry.read_bytes()).hexdigest() if registry.exists() else 'MISSING'
    status = record('PIT_UNCERTIFIED/BLOCKED', 'RECORD_PUBLICATION_VINTAGE_NOT_ADMITTED',
                    inputs, source_identity(ROOT), previous, scope='Belgium profit discovery')
    hold.update(status)
    out.write_text(json.dumps(hold, indent=2) + "\n", encoding="utf-8")
    publish(status, ROOT / 'research' / 'operation')
    print("PROFIT_COMPUTE_BLOCKED " + json.dumps(hold, sort_keys=True))
    return 0


def run_unadmitted_diagnostic_only() -> int:
    """Retained for historical recovery; not called by the production entrypoint."""
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
        "objective": "DIRECT_GROSS_PNL_DA_ENTRY_TO_IMBALANCE_PT15_CHRONOLOGICAL_DIAGNOSTIC_BOTH_DIRECTIONS",
        "validation_from": validation_from,
        "final_holdout_from": final_holdout_from,
        "entry_state": entry_state,
        "entry_promotable": entry_promotable,
        "promotion_eligible": False,
        "evidence_classification": "DIAGNOSTIC/PSEUDO_OOS",
        "pit_certification": "NOT_CERTIFIED",
        "independent_holdout": False,
        "routes": detailed,
        "candidate_count": len(combined),
        "review_ready_count": ready,
        "research_ready_entry_provisional_count": provisional,
        "candidates": combined[:200],
        "policy": (
            "Definitions/thresholds frozen on train. Validation Apr-Jun selects under FDR; only selected candidates "
            "are evaluated on the Jul+ chronological diagnostic slice, repeatedly exposed across runs. No independent OOS or record-level PIT certification is established; entry-source assurance alone cannot grant admission."
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

