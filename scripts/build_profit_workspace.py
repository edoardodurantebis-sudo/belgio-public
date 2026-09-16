#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from belgium_public.config import ROOT
from belgium_public.nordpool_public import backfill_nordpool_public
from belgium_public.profit_workspace import (
    DEFAULT_DA_START,
    build_profit_base,
    feature_registry,
    fetch_energy_charts_da,
    merge_price_cache,
    write_profit_panels,
)


def _cross_check(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    if a.empty or b.empty:
        return {"status": "NOT_AVAILABLE", "overlap_rows": 0}
    x = a[["delivery_start_utc", "entry_price"]].rename(columns={"entry_price": "a"})
    y = b[["delivery_start_utc", "entry_price"]].rename(columns={"entry_price": "b"})
    z = x.merge(y, on="delivery_start_utc", how="inner")
    if z.empty:
        return {"status": "NO_OVERLAP", "overlap_rows": 0}
    err = (pd.to_numeric(z["a"], errors="coerce") - pd.to_numeric(z["b"], errors="coerce")).abs().dropna()
    if err.empty:
        return {"status": "NO_NUMERIC_OVERLAP", "overlap_rows": 0}
    p99 = float(err.quantile(0.99))
    return {
        "status": "PASS" if len(err) >= 96 and p99 <= 0.01 else "CHECK",
        "overlap_rows": int(len(err)),
        "mean_abs_diff_eur_mwh": float(err.mean()),
        "max_abs_diff_eur_mwh": float(err.max()),
        "p99_abs_diff_eur_mwh": p99,
        "tolerance_eur_mwh": 0.01,
    }


def _local_day_coverage(prices: pd.DataFrame, start: date, end: date) -> dict:
    if prices.empty:
        return {"days_present": 0, "days_expected": (end - start).days + 1, "coverage": 0.0}
    ts = pd.to_datetime(prices["delivery_start_utc"], utc=True, errors="coerce").dropna()
    local = ts.dt.tz_convert("Europe/Brussels")
    have = {d for d in local.dt.date if start <= d <= end}
    expected = (end - start).days + 1
    return {"days_present": len(have), "days_expected": expected, "coverage": len(have) / expected if expected else 1.0}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=DEFAULT_DA_START.isoformat())
    p.add_argument("--end", default="")
    p.add_argument("--nordpool-max-days", type=int, default=220)
    args = p.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else (datetime.now(timezone.utc).date() - timedelta(days=1))

    canonical_root = ROOT / "data" / "canonical"
    research_root = ROOT / "research"
    outdir = research_root / "profit"
    outdir.mkdir(parents=True, exist_ok=True)
    generic_cache = canonical_root / "be_da_price_public.parquet"
    legacy_cache = canonical_root / "be_da_price_energy_charts.parquet"

    if generic_cache.exists():
        existing = pd.read_parquet(generic_cache)
    elif legacy_cache.exists():
        existing = pd.read_parquet(legacy_cache)
    else:
        existing = pd.DataFrame()

    ec_fresh = pd.DataFrame()
    try:
        ec_fresh, ec_meta = fetch_energy_charts_da(
            start,
            end,
            ROOT / "data" / "raw" / "energy_charts" / "be_day_ahead",
        )
    except Exception as exc:
        ec_meta = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    prices_after_ec = merge_price_cache(existing, ec_fresh)
    try:
        np_fresh, np_meta = backfill_nordpool_public(
            prices_after_ec,
            start,
            end,
            ROOT / "data" / "raw" / "nordpool_public" / "be_day_ahead",
            max_days=max(1, args.nordpool_max_days),
        )
    except Exception as exc:
        np_fresh = pd.DataFrame()
        np_meta = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    cross = _cross_check(ec_fresh, np_fresh)
    prices = merge_price_cache(prices_after_ec, np_fresh)
    registry = feature_registry()
    registry_path = outdir / "FEATURE_REGISTRY.csv"
    registry.to_csv(registry_path, index=False)

    if prices.empty:
        payload = {
            "status": "BLOCKED_ENTRY_PRICE",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "energy_charts": ec_meta,
            "nordpool_public": np_meta,
            "cross_check": cross,
            "entry_cache_rows": 0,
            "certified_feature_count": int(registry.pit_status.eq("CERTIFIED").sum()),
            "blockers": ["No public Belgian day-ahead entry-price rows available from either public route"],
        }
        (outdir / "READINESS.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(json.dumps(payload, default=str))
        return 0

    canonical_root.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(generic_cache, index=False)
    # Keep the old cache path readable for backwards compatibility while all
    # new code uses the generic public-entry cache.
    prices.to_parquet(legacy_cache, index=False)

    base, base_meta = build_profit_base(canonical_root, prices)
    panel_paths = write_profit_panels(base, research_root)
    coverage = _local_day_coverage(prices, start, end)
    certified = registry[registry.pit_status.eq("CERTIFIED")]
    candidate = registry[registry.pit_status.str.startswith("CANDIDATE")]

    if cross.get("status") == "PASS":
        entry_state = "CROSS_SOURCE_PUBLIC_MATCH"
    elif not np_fresh.empty:
        entry_state = "PROVISIONAL_NORDPOOL_PUBLIC_PORTAL"
    else:
        entry_state = "PROVISIONAL_ENERGY_CHARTS"

    payload = {
        "status": "PASS_PROVISIONAL_PUBLIC_ENTRY" if entry_state != "CROSS_SOURCE_PUBLIC_MATCH" else "PASS_CROSS_SOURCE_ENTRY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "route": "BELGIUM_SDAC_DA_TO_IMBALANCE_PT15",
        "sdac_pt15_delivery_go_live": "2025-10-01",
        "da_gate": "12:00 CET/CEST D-1",
        "entry_state": entry_state,
        "energy_charts": ec_meta,
        "nordpool_public": np_meta,
        "cross_check": cross,
        "coverage": coverage,
        "entry_cache_rows": int(len(prices)),
        "base": base_meta,
        "panels": panel_paths,
        "feature_registry": str(registry_path),
        "certified_feature_count": int(len(certified)),
        "candidate_field_time_feature_count": int(len(candidate)),
        "strict_features": certified.column_name.tolist(),
        "candidate_features_not_used_by_strict_discovery": candidate.column_name.tolist(),
        "known_blockers": [
            "An otherwise REVIEW_READY rule remains research-only while entry_state is provisional rather than CROSS_SOURCE_PUBLIC_MATCH",
            "Wind/solar Day Ahead 11AM fields stay outside strict discovery until publication timing is fully certified",
            "ODS001 day-ahead load is the 6PM forecast and is after the 12:00 DA gate, so it is excluded from strict DA discovery",
            "Historical Belgian intraday entry-price series is still missing for IDA/continuous routes",
        ],
        "policy": "Public-only. Use only CERTIFIED pre-gate features for strict discovery; provisional entry sources may generate research candidates but cannot alone promote a live rule.",
    }
    (outdir / "READINESS.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(
        "profit_workspace_status="
        f"{payload['status']} entry_state={entry_state} entry_cache_rows={len(prices)} "
        f"base_rows={base_meta.get('rows', 0)} certified_features={len(certified)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
