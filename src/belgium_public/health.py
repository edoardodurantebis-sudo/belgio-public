from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .canonical import resolve_col
from .config import SourceSpec

_FIXED_GRAIN_RE = re.compile(r"^PT(?P<minutes>\d+)M$")
_CORE_TIERS = {"core", "core_pre_mari"}


def _iso(v):
    if v is None or pd.isna(v):
        return None
    return pd.Timestamp(v).isoformat()


def _expected_minutes(granularity: str) -> int | None:
    match = _FIXED_GRAIN_RE.fullmatch(granularity or "")
    return int(match.group("minutes")) if match else None


def _schema_fingerprint(df: pd.DataFrame) -> str:
    payload = "\n".join(f"{name}:{dtype}" for name, dtype in sorted((c, str(df[c].dtype)) for c in df.columns))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _base(spec: SourceSpec) -> dict:
    return {
        "source_id": spec.id,
        "provider": spec.provider,
        "family": spec.family,
        "tier": spec.tier,
        "pit_status": spec.pit_status,
        "publication_timing": spec.publication_timing,
        "granularity_expected": spec.granularity,
    }


def inspect_source(spec: SourceSpec, path: Path, previous: dict | None = None) -> dict:
    if not path.exists():
        return {
            **_base(spec),
            "status": "FAIL" if spec.tier in _CORE_TIERS else "MISSING",
            "reason": "canonical_missing",
            "rows": 0,
            "reasons": ["canonical_missing"],
        }
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        return {
            **_base(spec),
            "status": "FAIL",
            "rows": 0,
            "reasons": [f"parquet_read_error:{type(e).__name__}"],
        }

    schema_fp = _schema_fingerprint(df)
    prior_fp = (previous or {}).get("schema_fingerprint")
    schema_drift = bool(prior_fp and prior_fp != schema_fp)

    missing_fields = [f for f in spec.required_fields if resolve_col(df, f) is None]
    dt_col = "delivery_start_utc" if "delivery_start_utc" in df.columns else resolve_col(df, "datetime")
    min_dt = max_dt = None
    bad_time = 0
    unique_delivery = 0
    median_step_minutes = None
    coverage_days = None
    expected_intervals = None
    missing_intervals = None
    coverage_ratio = None
    max_gap_minutes = None
    off_grid_intervals = None
    expected_minutes = _expected_minutes(spec.granularity)

    if dt_col:
        dt = pd.to_datetime(df[dt_col], utc=True, errors="coerce")
        bad_time = int(dt.isna().sum())
        good = dt.dropna().drop_duplicates().sort_values()
        unique_delivery = int(len(good))
        if len(good):
            min_dt, max_dt = good.iloc[0], good.iloc[-1]
            coverage_days = float((max_dt - min_dt).total_seconds() / 86400.0)
        if len(good) > 1:
            deltas = good.diff().dropna().dt.total_seconds() / 60.0
            median_step_minutes = float(deltas.median())
            max_gap_minutes = float(deltas.max())
            if expected_minutes:
                off_grid_intervals = int((deltas % expected_minutes != 0).sum())
                span_minutes = int((max_dt - min_dt).total_seconds() // 60)
                expected_intervals = span_minutes // expected_minutes + 1
                missing_intervals = max(0, expected_intervals - unique_delivery)
                coverage_ratio = float(unique_delivery / expected_intervals) if expected_intervals else None

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
    if unresolved_keys and spec.tier in _CORE_TIERS:
        status, reasons = "FAIL", reasons + [f"unresolved_primary_key:{unresolved_keys}"]
    if bad_time and dt_col:
        status, reasons = "FAIL", reasons + [f"bad_timestamps:{bad_time}"]
    if dup:
        status, reasons = "FAIL", reasons + [f"duplicate_keys:{dup}"]
    if schema_drift and spec.tier in _CORE_TIERS:
        status, reasons = "FAIL", reasons + ["schema_drift"]

    # Fixed-granularity core series are expected to be close to continuous on
    # the physical UTC timeline. A few missing points are reported; material
    # holes or off-grid timestamps fail closed.
    if expected_minutes and spec.tier in _CORE_TIERS and unique_delivery > 1:
        if off_grid_intervals:
            status, reasons = "FAIL", reasons + [f"off_grid_intervals:{off_grid_intervals}"]
        if coverage_ratio is not None and coverage_ratio < 0.995:
            status, reasons = "FAIL", reasons + [f"coverage_ratio_below_0.995:{coverage_ratio:.6f}"]
        if max_gap_minutes is not None and max_gap_minutes > expected_minutes * 4:
            status, reasons = "FAIL", reasons + [f"max_gap_exceeds_4x_grain:{max_gap_minutes:.1f}m"]
        if median_step_minutes is not None and abs(median_step_minutes - expected_minutes) > 1e-9:
            status, reasons = "FAIL", reasons + [
                f"median_granularity_mismatch:expected={expected_minutes},observed={median_step_minutes:.3f}"
            ]

    return {
        **_base(spec),
        "status": status,
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "schema_fingerprint": schema_fp,
        "schema_drift_from_previous_run": schema_drift,
        "first_delivery_utc": _iso(min_dt),
        "last_delivery_utc": _iso(max_dt),
        "coverage_days_span": coverage_days,
        "unique_delivery_timestamps": unique_delivery,
        "expected_interval_minutes": expected_minutes,
        "median_unique_timestep_minutes": median_step_minutes,
        "max_gap_minutes": max_gap_minutes,
        "expected_intervals_in_span": expected_intervals,
        "missing_intervals_in_span": missing_intervals,
        "coverage_ratio_in_span": coverage_ratio,
        "off_grid_intervals": off_grid_intervals,
        "duplicate_keys": dup,
        "bad_timestamps": bad_time,
        "missing_required_fields": missing_fields,
        "unresolved_primary_key_fields": unresolved_keys,
        "reasons": reasons,
    }


def _timestamp_set(path: Path) -> set[pd.Timestamp]:
    if not path.exists():
        return set()
    try:
        df = pd.read_parquet(path)
    except Exception:
        return set()
    dt_col = "delivery_start_utc" if "delivery_start_utc" in df.columns else resolve_col(df, "datetime")
    if not dt_col:
        return set()
    dt = pd.to_datetime(df[dt_col], utc=True, errors="coerce").dropna().drop_duplicates()
    return set(pd.Timestamp(x) for x in dt)


def _joinability_gate(canonical_root: Path) -> dict:
    required = ["ods134", "ods127", "ods132", "ods166"]
    sets = {source_id: _timestamp_set(canonical_root / f"{source_id}.parquet") for source_id in required}
    missing = [source_id for source_id, values in sets.items() if not values]
    if missing:
        return {
            "status": "FAIL",
            "required_sources": required,
            "missing_or_unreadable": missing,
            "intersection_timestamps": 0,
            "reference_timestamps": len(sets["ods134"]),
            "intersection_ratio_vs_ods134": 0.0,
        }
    common = set.intersection(*(sets[source_id] for source_id in required))
    reference = sets["ods134"]
    ratio = len(common) / len(reference) if reference else 0.0
    return {
        "status": "PASS" if ratio >= 0.98 else "FAIL",
        "required_sources": required,
        "intersection_timestamps": len(common),
        "reference_timestamps": len(reference),
        "intersection_ratio_vs_ods134": ratio,
        "threshold": 0.98,
        "per_source_timestamps": {source_id: len(values) for source_id, values in sets.items()},
    }


def build_health(sources: list[SourceSpec], canonical_root: Path, out_path: Path) -> dict:
    previous_sources: dict[str, dict] = {}
    if out_path.exists():
        try:
            previous_payload = json.loads(out_path.read_text(encoding="utf-8"))
            previous_sources = {x["source_id"]: x for x in previous_payload.get("sources", [])}
        except Exception:
            previous_sources = {}

    items = [
        inspect_source(
            s,
            canonical_root / f"{s.id}.parquet",
            previous=previous_sources.get(s.id),
        )
        for s in sources
    ]
    post_mari_core = [x for x in items if x.get("tier") == "core"]
    pre_mari_core = [x for x in items if x.get("tier") == "core_pre_mari"]
    post_status = "PASS" if post_mari_core and all(x["status"] == "PASS" for x in post_mari_core) else "FAIL"
    pre_status = "PASS" if pre_mari_core and all(x["status"] == "PASS" for x in pre_mari_core) else "FAIL"
    joinability = _joinability_gate(canonical_root)
    overall = "PASS" if post_status == "PASS" and joinability["status"] == "PASS" else "FAIL"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "gates": {
            "post_mari_core": post_status,
            "pre_mari_price_regime": pre_status,
            "post_mari_tape_joinability": joinability["status"],
        },
        "joinability": joinability,
        "policy": (
            "FAIL means scientific outputs must not be promoted. Core fixed-granularity series fail on material "
            "holes, off-grid timestamps, duplicate keys, timestamp/schema defects or schema drift. Missing non-core "
            "sources are reported but do not masquerade as PASS."
        ),
        "sources": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload
