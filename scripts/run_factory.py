from __future__ import annotations
import argparse, json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from belgium_public.canonical import canonicalize
from belgium_public.config import ROOT, SourceSpec, load_registry
from belgium_public.elia import collect_elia
from belgium_public.health import build_health
from belgium_public.jao import collect_jao_maxexchanges


def _last_delivery(spec: SourceSpec, canonical_root: Path) -> str | None:
    path = canonical_root / f"{spec.id}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["delivery_start_utc"])
    except Exception:
        return None
    dt = pd.to_datetime(df["delivery_start_utc"], utc=True, errors="coerce").dropna()
    if dt.empty:
        return None
    return dt.max().isoformat().replace("+00:00", "Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["bootstrap", "incremental", "nrt", "all"], default="incremental")
    ap.add_argument("--source", action="append", help="limit to source id; repeatable")
    ap.add_argument("--since", help="UTC ISO date/time override for historical incremental filter")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any selected collector fails")
    ap.add_argument("--strict-core", action="store_true", help="exit non-zero if a selected core collector fails")
    args = ap.parse_args()

    sources, registry = load_registry()
    selected = [s for s in sources if not args.source or s.id in set(args.source)]
    if args.mode == "bootstrap":
        selected = [s for s in selected if s.mode == "historical"]
    elif args.mode == "nrt":
        selected = [s for s in selected if s.mode == "snapshot"]
    elif args.mode == "incremental":
        selected = [s for s in selected if s.mode == "historical"]

    raw_root = ROOT / "data" / "raw"
    canonical_root = ROOT / "data" / "canonical"
    events: list[dict] = []
    failures: list[dict] = []

    for spec in selected:
        try:
            since = None
            if spec.provider == "Elia":
                if args.mode in {"incremental", "all"} and spec.mode == "historical":
                    since = args.since or _last_delivery(spec, canonical_root)
                result = collect_elia(spec, raw_root, since=since)
            elif spec.provider == "JAO" and spec.id == "jao_core_maxexchanges":
                result = collect_jao_maxexchanges(spec, date.today() - timedelta(days=2), raw_root)
            else:
                continue
            can = canonicalize(result, canonical_root)
            events.append({
                "source_id": spec.id,
                "status": "PASS",
                "rows_canonical": can["rows"],
                "raw": str(result["raw_path"].relative_to(ROOT)),
                "incremental_since": since,
            })
        except Exception as e:
            failures.append({"source_id": spec.id, "tier": spec.tier, "status": "FAIL", "error": repr(e)})

    health = build_health(sources, canonical_root, ROOT / "state" / "DATA_HEALTH.json")
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
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
