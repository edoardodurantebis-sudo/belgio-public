from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from .canonical import resolve_col


def build_extreme_case_registry(canonical_root: Path, out_dir: Path, n_each: int = 8) -> dict:
    """Select reproducible ex-post cases from ODS134 without making trading claims."""
    source = canonical_root / "ods134.parquet"
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "CASE_STUDY_REGISTRY.json"
    if not source.exists():
        payload = {"status": "BLOCKED", "reason": "ods134 canonical missing", "cases": []}
        status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    df = pd.read_parquet(source)
    dt_col = "delivery_start_utc" if "delivery_start_utc" in df.columns else resolve_col(df, "datetime")
    si_col = resolve_col(df, "systemimbalance")
    price_col = resolve_col(df, "imbalanceprice")
    if not dt_col or not si_col or not price_col:
        payload = {
            "status": "BLOCKED",
            "reason": "required ODS134 fields unresolved",
            "resolved": {"datetime": dt_col, "systemimbalance": si_col, "imbalanceprice": price_col},
            "cases": [],
        }
        status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    x = pd.DataFrame({
        "delivery_start_utc": pd.to_datetime(df[dt_col], utc=True, errors="coerce"),
        "system_imbalance_mw": pd.to_numeric(df[si_col], errors="coerce"),
        "imbalance_price_eur_mwh": pd.to_numeric(df[price_col], errors="coerce"),
    }).dropna()
    x = x.drop_duplicates(subset=["delivery_start_utc"], keep="last")
    if x.empty:
        payload = {"status": "BLOCKED", "reason": "ODS134 has no numeric complete observations", "cases": []}
        status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    buckets = {
        "most_positive_system_imbalance": x.nlargest(n_each, "system_imbalance_mw"),
        "most_negative_system_imbalance": x.nsmallest(n_each, "system_imbalance_mw"),
        "highest_imbalance_price": x.nlargest(n_each, "imbalance_price_eur_mwh"),
        "lowest_imbalance_price": x.nsmallest(n_each, "imbalance_price_eur_mwh"),
    }
    rows = []
    for category, part in buckets.items():
        y = part.copy()
        y["case_category"] = category
        rows.append(y)
    cases = pd.concat(rows, ignore_index=True).drop_duplicates(subset=["case_category", "delivery_start_utc"])
    cases["delivery_start_brussels"] = cases["delivery_start_utc"].dt.tz_convert("Europe/Brussels")
    csv_path = out_dir / "CASE_STUDY_CANDIDATES.csv"
    cases.to_csv(csv_path, index=False)

    payload = {
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_source": "ods134",
        "selection_policy": "Ex-post extremes only; these are mechanism case studies, not trading rules.",
        "observations_available": int(len(x)),
        "first_delivery_utc": x["delivery_start_utc"].min().isoformat(),
        "last_delivery_utc": x["delivery_start_utc"].max().isoformat(),
        "cases_csv": str(csv_path),
        "case_count": int(len(cases)),
        "cases": [
            {
                "category": r.case_category,
                "delivery_start_utc": r.delivery_start_utc.isoformat(),
                "system_imbalance_mw": float(r.system_imbalance_mw),
                "imbalance_price_eur_mwh": float(r.imbalance_price_eur_mwh),
            }
            for r in cases.itertuples(index=False)
        ],
    }
    status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
