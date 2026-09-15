from __future__ import annotations
import argparse, json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from belgium_public.canonical import canonicalize
from belgium_public.config import ROOT, SourceSpec, load_registry
from belgium_public.elia import collect_elia, discover_datetime_bounds
from belgium_public.health import build_health
from belgium_public.jao import collect_jao_maxexchanges
from belgium_public.planning import select_sources, year_windows


def _last_delivery(spec: SourceSpec, canonical_root: Path) -> pd.Timestamp | None:
    path = canonical_root / f"{spec.id}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["delivery_start_utc"])
    except Exception:
        return None
    dt = pd.to_datetime(df["delivery_start_utc"], utc=True, errors="coerce").dropna()
    return pd.Timestamp(dt.max()) if not dt.empty else None


def _collect_historical_elia(spec: SourceSpec, raw_root: Path, canonical_root: Path, mode: str, since_override: str | None) -> dict:
    last_local = _last_delivery(spec, canonical_root)
    if since_override:
        result = collect_elia(spec, raw_root, after=since_override)
        can = canonicalize(result, canonical_root)
        return {"chunks": 1, "rows_canonical": can["rows"], "strategy": "explicit_incremental", "after": since_override}

    if last_local is not None and mode in {"incremental", "all"}:
        result = collect_elia(spec, raw_root, after=last_local)
        can = canonicalize(result, canonical_root)
        return {"chunks": 1, "rows_canonical": can["rows"], "strategy": "incremental", "after": last_local.isoformat()}

    # Missing canonical history is always bootstrapped in bounded yearly windows,
    # even if an incremental workflow reached the source for the first time.
    first, last = discover_datetime_bounds(spec)
    chunks = 0
    rows = 0
    for start, end in year_windows(first, last):
        result = collect_elia(spec, raw_root, start=start, end=end)
        if result["df"].empty:
            continue
        can = canonicalize(result, canonical_root)
        chunks += 1
        rows = can["rows"]
    if chunks == 0:
        raise RuntimeError(f"{spec.id}: bootstrap produced no data")
    return {
        "chunks": chunks,
        "rows_canonical": rows,
        "strategy": "yearly_bootstrap",
        "provider_first_utc": first.isoformat(),
        "provider_last_utc": last.isoformat(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["bootstrap", "incremental", "nrt", "all"], default="incremental")
    ap.add_argument("--source", action="append", help="limit to source id; repeatable")
    ap.add_argument("--tier", action="append", help="limit to source tier; repeatable")
    ap.add_argument("--since", help="UTC ISO date/time override for historical incremental filter")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any selected collector fails")
    ap.add_argument("--strict-core", action="store_true", help="exit non-zero if a selected core collector fails")
    args = ap.parse_args()

    sources, registry = load_registry()
    explicit = set(args.source or [])
    tiers = set(args.tier or [])
    selected = select_sources(sources, mode=args.mode, explicit=explicit, tiers=tiers)

    raw_root = ROOT / "data" / "raw"
    canonical_root = ROOT / "data" / "canonical"
    events: list[dict] = []
    failures: list[dict] = []

    for spec in selected:
        try:
            if spec.provider == "Elia" and spec.mode == "historical":
                detail = _collect_historical_elia(spec, raw_root, canonical_root, args.mode, args.since)
                events.append({"source_id": spec.id, "status": "PASS", **detail})
            elif spec.provider == "Elia" and spec.mode == "snapshot":
                result = collect_elia(spec, raw_root)
                can = canonicalize(result, canonical_root)
                events.append({"source_id": spec.id, "status": "PASS", "rows_canonical": can["rows"], "strategy": "snapshot"})
            elif spec.provider == "JAO" and spec.id == "jao_core_maxexchanges":
                result = collect_jao_maxexchanges(spec, date.today() - timedelta(days=2), raw_root)
                can = canonicalize(result, canonical_root)
                events.append({"source_id": spec.id, "status": "PASS", "rows_canonical": can["rows"], "strategy": "canary_day"})
        except Exception as e:
            failures.append({"source_id": spec.id, "tier": spec.tier, "status": "FAIL", "error": repr(e)})

    health = build_health(sources, canonical_root, ROOT / "state" / "DATA_HEALTH.json")
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "selected_sources": [s.id for s in selected],
        "events": events,
        "failures": failures,
        "health": health["overall_status"],
        "health_gates": health.get("gates", {}),
        "structural_breaks": registry.get("structural_breaks", []),
    }
    (ROOT / "state").mkdir(exist_ok=True)
    (ROOT / "state" / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))

    if args.strict and failures:
        raise SystemExit(2)
    if args.strict_core and any(f["tier"] in {"core", "core_pre_mari"} for f in failures):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
