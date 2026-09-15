from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from .canonical import resolve_col
from .config import SourceSpec


def _iso(v):
    if v is None or pd.isna(v):
        return None
    return pd.Timestamp(v).isoformat()


def _base(spec: SourceSpec) -> dict:
    return {
        "source_id": spec.id,
        "provider": spec.provider,
        "family": spec.family,
        "tier": spec.tier,
        "pit_status": spec.pit_status,
        "granularity_expected": spec.granularity,
    }


def inspect_source(spec: SourceSpec, path: Path) -> dict:
    if not path.exists():
        return {
            **_base(spec),
            "status": "FAIL" if spec.tier in {"core", "core_pre_mari"} else "MISSING",
            "reason": "canonical_missing",
            "rows": 0,
            "reasons": ["canonical_missing"],
        }
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        return {**_base(spec), "status": "FAIL", "rows": 0, "reasons": [f"parquet_read_error:{type(e).__name__}"]}

    missing_fields = [f for f in spec.required_fields if resolve_col(df, f) is None]
    dt_col = "delivery_start_utc" if "delivery_start_utc" in df.columns else resolve_col(df, "datetime")
    min_dt = max_dt = None
    bad_time = 0
    if dt_col:
        dt = pd.to_datetime(df[dt_col], utc=True, errors="coerce")
        bad_time = int(dt.isna().sum())
        if dt.notna().any():
            min_dt, max_dt = dt.min(), dt.max()

    keys = [resolve_col(df, k) for k in spec.primary_key]
    unresolved_keys = [k for k, resolved in zip(spec.primary_key, keys) if resolved is None]
    keys = [k for k in keys if k]
    dup = int(df.duplicated(subset=keys).sum()) if keys and not spec.preserve_vintages else 0

    status = "PASS"
    reasons: list[str] = []
    if len(df) == 0:
        status, reasons = "FAIL", ["empty_canonical"]
    if missing_fields:
        status, reasons = "FAIL", reasons + [f"missing_fields:{missing_fields}"]
    if unresolved_keys and spec.tier in {"core", "core_pre_mari"}:
        status, reasons = "FAIL", reasons + [f"unresolved_primary_key:{unresolved_keys}"]
    if bad_time and dt_col:
        status, reasons = "FAIL", reasons + [f"bad_timestamps:{bad_time}"]
    if dup:
        status, reasons = "FAIL", reasons + [f"duplicate_keys:{dup}"]

    return {
        **_base(spec),
        "status": status,
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "first_delivery_utc": _iso(min_dt),
        "last_delivery_utc": _iso(max_dt),
        "duplicate_keys": dup,
        "bad_timestamps": bad_time,
        "missing_required_fields": missing_fields,
        "unresolved_primary_key_fields": unresolved_keys,
        "reasons": reasons,
    }


def build_health(sources: list[SourceSpec], canonical_root: Path, out_path: Path) -> dict:
    items = [inspect_source(s, canonical_root / f"{s.id}.parquet") for s in sources]
    post_mari_core = [x for x in items if x.get("tier") == "core"]
    pre_mari_core = [x for x in items if x.get("tier") == "core_pre_mari"]
    post_status = "PASS" if post_mari_core and all(x["status"] == "PASS" for x in post_mari_core) else "FAIL"
    pre_status = "PASS" if pre_mari_core and all(x["status"] == "PASS" for x in pre_mari_core) else "FAIL"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_status": post_status,
        "gates": {
            "post_mari_core": post_status,
            "pre_mari_price_regime": pre_status,
        },
        "policy": "FAIL means scientific outputs must not be promoted. Missing non-core sources are reported but do not masquerade as PASS.",
        "sources": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload
