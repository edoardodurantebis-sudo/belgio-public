from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd


def run_lab(canonical_root: Path, health_path: Path, out_path: Path) -> dict:
    health = json.loads(health_path.read_text(encoding="utf-8"))
    if health.get("overall_status") != "PASS":
        payload = {
            "status": "BLOCKED",
            "reason": "DATA_HEALTH core gate is not PASS",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
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

    payload = {
        "status": "PASS_NO_EDGE_PROMOTED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mechanisms": mechanisms,
        "robust": [],
        "watchlist": [],
        "rejected": [],
        "insufficient_evidence": [
            {
                "name": "historical_most_recent_forecast_revision_rules",
                "reason": "historical publication/knowledge timestamp is not yet certified; prospective NRT vintages are being accumulated",
            },
            {
                "name": "JAO_CNEC_RAM_PTDF_rules",
                "reason": "exact Core domain endpoint/schema and historical publication timestamp mapping are not yet certified",
            },
        ],
        "policy": "No rule promotion until feature availability is certified and temporal holdout/walk-forward tests exist.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
