from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from .config import ROOT


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _hypothesis_summary() -> dict:
    registry = _load_json(ROOT / "config" / "research_hypotheses_public.json")
    hypotheses = registry.get("hypotheses", []) if isinstance(registry, dict) else []
    counts = Counter(str(h.get("status", "unknown")) for h in hypotheses if isinstance(h, dict))
    return {
        "registry": "config/research_hypotheses_public.json",
        "count": len(hypotheses),
        "status_counts": dict(sorted(counts.items())),
        "policy": registry.get("policy"),
        "items": [
            {
                "id": h.get("id"),
                "title": h.get("title"),
                "status": h.get("status"),
                "promotion_rule": h.get("promotion_rule"),
            }
            for h in hypotheses
            if isinstance(h, dict)
        ],
    }


def run_lab(canonical_root: Path, health_path: Path, out_path: Path) -> dict:
    # Missing/malformed state is a technical fault, not a known scientific gap.
    health = json.loads(health_path.read_text(encoding="utf-8"))
    if health.get("overall_status") not in {"PASS", "FAIL"}:
        raise ValueError("INVALID_DATA_HEALTH_STATE")
    hypotheses = _hypothesis_summary()
    if health.get("overall_status") != "PASS":
        payload = {
            "status": "BLOCKED",
            "operation_class": "SCIENTIFIC_BLOCK",
            "scientific_state": "BLOCKED_DATA_HEALTH",
            "promotion_eligible": False,
            "reason": "DATA_HEALTH core gate is not PASS",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "research_hypotheses": hypotheses,
            "robust": [],
            "watchlist": [],
            "rejected": [],
            "insufficient_evidence": [{"name": "all_ex_ante_discovery", "reason": "core data gate not PASS"}],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    available = {p.stem for p in canonical_root.glob("*.parquet")}
    required_expost = {"ods134", "ods166", "ods127"}
    mechanisms = []
    if required_expost.issubset(available):
        df = pd.read_parquet(canonical_root / "ods166.parquet")
        mechanisms.append({
            "name": "post_MARI_price_stack_available",
            "evidence_rows": int(len(df)),
            "classification": "mechanism_ready",
            "note": "System imbalance / balancing-price components can be aligned with ODS134 and activation volumes for ex-post case studies.",
        })

    jao_audit = _load_json(health_path.parent / "JAO_ENDPOINT_AUDIT.json")
    verified = jao_audit.get("verified", []) if jao_audit.get("status") == "VERIFIED" else []
    if verified:
        mechanisms.append({
            "name": "JAO_Core_Final_Computation_endpoint_schema_verified",
            "classification": "mechanism_ready",
            "endpoint": verified[0].get("url"),
            "schema_proof": "live HTTP 200 with RAM + PTDF + CNEC identity",
            "note": "Endpoint/schema uncertainty is closed. Historical PIT publication-time certification remains separate.",
        })

    if "jao_core_final_computation" in available:
        jao_df = pd.read_parquet(canonical_root / "jao_core_final_computation.parquet")
        mechanisms.append({
            "name": "JAO_Core_Final_Computation_history_available",
            "classification": "mechanism_ready",
            "evidence_rows": int(len(jao_df)),
            "note": "CNEC/RAM/PTDF history is available for ex-post congestion mechanism analysis; it is not automatically PIT-safe.",
        })

    insufficient = [
        {
            "name": "historical_most_recent_forecast_revision_rules",
            "reason": "historical publication/knowledge timestamp is not yet certified; prospective NRT vintages are being accumulated",
        }
    ]
    if verified:
        insufficient.append({
            "name": "JAO_CNEC_RAM_PTDF_ex_ante_rules",
            "reason": "endpoint and schema are verified, but scheduled publication time is not proof of the actual historical knowledge timestamp for every vintage",
        })
    else:
        insufficient.append({
            "name": "JAO_CNEC_RAM_PTDF_rules",
            "reason": "live Core domain endpoint/schema is not yet certified",
        })

    payload = {
        "status": "PASS_NO_EDGE_PROMOTED",
        "operation_class": "SCIENTIFIC_BLOCK",
        "scientific_state": "NO_CERTIFIED_INPUT_NO_VIEW",
        "promotion_eligible": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_hypotheses": hypotheses,
        "mechanisms": mechanisms,
        "robust": [],
        "watchlist": [],
        "rejected": [],
        "insufficient_evidence": insufficient,
        "policy": "No rule promotion until feature availability is certified and temporal holdout/walk-forward tests exist.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
