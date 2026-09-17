from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from .canonical import resolve_col

ENERGY_CHARTS_URL = "https://api.energy-charts.info/price"
DEFAULT_DA_START = date(2025, 10, 1)
BRUSSELS = "Europe/Brussels"


def _utc_ts(df: pd.DataFrame) -> pd.Series:
    c = "delivery_start_utc" if "delivery_start_utc" in df.columns else resolve_col(df, "datetime")
    if not c:
        raise RuntimeError("DELIVERY_TIMESTAMP_COLUMN_MISSING")
    return pd.to_datetime(df[c], utc=True, errors="coerce")


def _num(df: pd.DataFrame, wanted: str) -> pd.Series:
    c = resolve_col(df, wanted)
    if not c:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[c], errors="coerce")


def _read_canonical(root: Path, source_id: str) -> pd.DataFrame:
    p = root / f"{source_id}.parquet"
    if not p.exists():
        return pd.DataFrame()
    x = pd.read_parquet(p)
    x = x.copy()
    x["delivery_start_utc"] = _utc_ts(x)
    x = x[x["delivery_start_utc"].notna()].copy()
    return x


def _month_chunks(start: date, end: date):
    cur = start
    while cur <= end:
        nxt = (pd.Timestamp(cur) + pd.offsets.MonthBegin(1)).date()
        chunk_end = min(end, nxt - timedelta(days=1))
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def parse_energy_charts_payload(payload: dict[str, Any]) -> pd.DataFrame:
    seconds = payload.get("unix_seconds")
    prices = payload.get("price")
    if not isinstance(seconds, list) or not isinstance(prices, list) or len(seconds) != len(prices):
        raise RuntimeError("ENERGY_CHARTS_BAD_PARALLEL_ARRAYS")
    x = pd.DataFrame({"unix_seconds": seconds, "entry_price": prices})
    x["delivery_start_utc"] = pd.to_datetime(x["unix_seconds"], unit="s", utc=True, errors="coerce")
    x["entry_price"] = pd.to_numeric(x["entry_price"], errors="coerce")
    x = x.dropna(subset=["delivery_start_utc", "entry_price"]).copy()
    x = x[["delivery_start_utc", "entry_price"]].drop_duplicates("delivery_start_utc", keep="last")
    return x.sort_values("delivery_start_utc").reset_index(drop=True)


def fetch_energy_charts_da(start: date, end: date, raw_root: Path, session: requests.Session | None = None) -> tuple[pd.DataFrame, dict]:
    if end < start:
        raise ValueError("END_BEFORE_START")
    session = session or requests.Session()
    retrieved = datetime.now(timezone.utc)
    rows = []
    meta_chunks = []
    raw_root.mkdir(parents=True, exist_ok=True)

    for a, b in _month_chunks(start, end):
        params = {"bzn": "BE", "start": a.isoformat(), "end": b.isoformat()}
        r = session.get(ENERGY_CHARTS_URL, params=params, timeout=45)
        r.raise_for_status()
        payload = r.json()
        parsed = parse_energy_charts_payload(payload)
        rows.append(parsed)
        meta_chunks.append({
            "start": a.isoformat(),
            "end": b.isoformat(),
            "rows": int(len(parsed)),
            "license_info": payload.get("license_info"),
            "unit": payload.get("unit"),
            "deprecated": payload.get("deprecated"),
        })
        stamp = retrieved.strftime("%Y%m%dT%H%M%SZ")
        (raw_root / f"{a}_{b}_{stamp}.json").write_text(json.dumps({"request": params, "retrieved_at_utc": retrieved.isoformat(), "payload": payload}, default=str), encoding="utf-8")

    if not rows:
        raise RuntimeError("ENERGY_CHARTS_NO_CHUNKS")
    out = pd.concat(rows, ignore_index=True).drop_duplicates("delivery_start_utc", keep="last").sort_values("delivery_start_utc")
    lo = pd.Timestamp(start, tz="UTC")
    hi = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    out = out[(out["delivery_start_utc"] >= lo) & (out["delivery_start_utc"] < hi)].copy()
    if out.empty:
        raise RuntimeError("ENERGY_CHARTS_EMPTY_RANGE")

    diffs = out["delivery_start_utc"].drop_duplicates().sort_values().diff().dropna().dt.total_seconds()
    common_step = float(diffs.mode().iloc[0]) if len(diffs) else np.nan
    qh_share = float((diffs == 900).mean()) if len(diffs) else 0.0
    if start >= DEFAULT_DA_START and len(diffs) >= 20 and common_step != 900.0:
        raise RuntimeError(f"ENERGY_CHARTS_NOT_PT15:common_step={common_step}")

    out["entry_source"] = "Fraunhofer Energy-Charts"
    out["entry_source_upstream"] = "ENTSO-E / EPEX SPOT as reported by Energy-Charts"
    out["entry_source_url"] = ENERGY_CHARTS_URL
    out["entry_retrieved_at_utc"] = pd.Timestamp(retrieved)
    meta = {
        "status": "PASS",
        "source": "Fraunhofer Energy-Charts public API",
        "upstream": "ENTSO-E / EPEX SPOT as reported by Energy-Charts",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": int(len(out)),
        "first_utc": out["delivery_start_utc"].min().isoformat(),
        "last_utc": out["delivery_start_utc"].max().isoformat(),
        "common_step_seconds": common_step,
        "pt15_diff_share": qh_share,
        "chunks": meta_chunks,
        "classification": "PUBLIC_SECONDARY_ENTRY_SERIES",
    }
    return out.reset_index(drop=True), meta


def merge_price_cache(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    frames = [x for x in (existing, fresh) if x is not None and not x.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["delivery_start_utc"] = pd.to_datetime(out["delivery_start_utc"], utc=True, errors="coerce")
    out = out.dropna(subset=["delivery_start_utc", "entry_price"])
    return out.sort_values("delivery_start_utc").drop_duplicates("delivery_start_utc", keep="last").reset_index(drop=True)


def _aggregate_wind(x: pd.DataFrame) -> pd.DataFrame:
    if x.empty:
        return pd.DataFrame(columns=["delivery_start_utc", "wind_actual_mw", "wind_da11h_mw"])
    y = pd.DataFrame({
        "delivery_start_utc": x["delivery_start_utc"],
        "wind_actual_mw": _num(x, "measured"),
        "wind_da11h_mw": _num(x, "dayahead11hforecast"),
    })
    return y.groupby("delivery_start_utc", as_index=False).sum(min_count=1)


def _aggregate_solar(x: pd.DataFrame) -> pd.DataFrame:
    if x.empty:
        return pd.DataFrame(columns=["delivery_start_utc", "solar_actual_mw", "solar_da11h_mw"])
    region = resolve_col(x, "region")
    y = x.copy()
    if region:
        be = y[region].astype(str).str.casefold().eq("belgium")
        if be.any():
            y = y[be].copy()
    out = pd.DataFrame({
        "delivery_start_utc": y["delivery_start_utc"],
        "solar_actual_mw": _num(y, "measured"),
        "solar_da11h_mw": _num(y, "dayahead11hforecast"),
    })
    return out.groupby("delivery_start_utc", as_index=False).last()


def _aggregate_load(x: pd.DataFrame) -> pd.DataFrame:
    if x.empty:
        return pd.DataFrame(columns=["delivery_start_utc", "load_actual_mw", "load_da18h_mw"])
    out = pd.DataFrame({
        "delivery_start_utc": x["delivery_start_utc"],
        "load_actual_mw": _num(x, "totalload"),
        "load_da18h_mw": _num(x, "dayaheadforecast"),
    })
    return out.groupby("delivery_start_utc", as_index=False).last()


def _lookup_lag(series: pd.Series, index: pd.DatetimeIndex, lag: str = "48h") -> pd.Series:
    s = pd.Series(pd.to_numeric(series, errors="coerce").to_numpy(), index=index).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    wanted = index - pd.Timedelta(lag)
    return pd.Series(s.reindex(wanted).to_numpy(), index=range(len(index)), dtype=float)


def _safe_rolling_lookup(series: pd.Series, index: pd.DatetimeIndex, window: str, lag: str = "48h", kind: str = "mean") -> pd.Series:
    s = pd.Series(pd.to_numeric(series, errors="coerce").to_numpy(), index=index).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    if kind == "mean":
        r = s.rolling(window, min_periods=24).mean()
    elif kind == "sum":
        r = s.rolling(window, min_periods=24).sum()
    elif kind == "abs_sum":
        r = s.abs().rolling(window, min_periods=24).sum()
    else:
        raise ValueError(kind)
    wanted = index - pd.Timedelta(lag)
    return pd.Series(r.reindex(wanted).to_numpy(), index=range(len(index)), dtype=float)


def _gate_timestamp(delivery_utc: pd.Series) -> pd.Series:
    local = delivery_utc.dt.tz_convert(BRUSSELS)
    dates = local.dt.date
    gates = [pd.Timestamp(d - timedelta(days=1)).tz_localize(BRUSSELS) + pd.Timedelta(hours=12) for d in dates]
    return pd.Series(gates, index=delivery_utc.index).dt.tz_convert("UTC")


def feature_registry() -> pd.DataFrame:
    rows = [
        ("CTX_QH", "ctx_qh_of_day", "TIME", "CERTIFIED"),
        ("CTX_HOUR", "ctx_local_hour", "TIME_HOUR", "CERTIFIED"),
        ("CTX_WEEKDAY", "ctx_weekday", "CALENDAR", "CERTIFIED"),
        ("CTX_MONTH", "ctx_month", "MONTH", "CERTIFIED"),
        ("CTX_WEEKEND", "ctx_weekend", "WORKCLASS", "CERTIFIED"),
        ("SI_LAG48", "si_lag48h_mw", "IMBALANCE_MEMORY", "PIT_UNCERTIFIED"),
        ("SI_ROLL7", "si_roll7d_safe_mean_mw", "IMBALANCE_MEMORY_LEVEL", "PIT_UNCERTIFIED"),
        ("SI_DIR14", "si_roll14d_safe_directionality", "IMBALANCE_MEMORY_DIRECTION", "PIT_UNCERTIFIED"),
        ("IP_LAG48", "imb_price_lag48h_eur_mwh", "PRICE_MEMORY", "PIT_UNCERTIFIED"),
        ("IP_ROLL7", "imb_price_roll7d_safe_mean_eur_mwh", "PRICE_MEMORY_LEVEL", "PIT_UNCERTIFIED"),
        ("DA_LAG48", "da_price_lag48h_eur_mwh", "ENTRY_MEMORY", "PIT_UNCERTIFIED"),
        ("DA_ROLL7", "da_price_roll7d_safe_mean_eur_mwh", "ENTRY_MEMORY_LEVEL", "PIT_UNCERTIFIED"),
        ("LOAD_LAG48", "load_actual_lag48h_mw", "LOAD_MEMORY", "PIT_UNCERTIFIED"),
        ("WIND_LAG48", "wind_actual_lag48h_mw", "WIND_MEMORY", "PIT_UNCERTIFIED"),
        ("SOLAR_LAG48", "solar_actual_lag48h_mw", "SOLAR_MEMORY", "PIT_UNCERTIFIED"),
        ("RESIDUAL_LAG48", "residual_actual_lag48h_mw", "RESIDUAL_MEMORY", "PIT_UNCERTIFIED"),
        ("WIND_DA11", "wind_da11h_mw", "WIND_DA", "CANDIDATE_FIELD_TIME"),
        ("SOLAR_DA11", "solar_da11h_mw", "SOLAR_DA", "CANDIDATE_FIELD_TIME"),
        ("RENEW_DA11", "renewable_da11h_mw", "RENEWABLE_DA", "CANDIDATE_FIELD_TIME"),
        ("RENEW_QH_SHAPE", "renewable_da11h_qh_spread_mw", "RENEWABLE_SHAPE", "CANDIDATE_FIELD_TIME"),
        ("LOAD_DA18", "load_da18h_mw", "LOAD_DA", "UNSAFE_AFTER_DA_GATE"),
        ("RESIDUAL_MIXED", "residual_load_mixed_timing_mw", "RESIDUAL_DA", "UNSAFE_MIXED_TIMING"),
    ]
    registry = pd.DataFrame(rows, columns=["feature_id", "column_name", "family", "pit_status"])
    registry["certification_scope"] = registry.feature_id.map(
        lambda x: "DETERMINISTIC_CALENDAR_ONLY" if x.startswith("CTX_") else "NO_RECORD_LEVEL_PUBLICATION_VINTAGE_PROOF"
    )
    return registry


def build_profit_base(canonical_root: Path, da_prices: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    outcome = _read_canonical(canonical_root, "ods134")
    if outcome.empty:
        raise RuntimeError("ODS134_MISSING")
    base = pd.DataFrame({
        "delivery_start_utc": outcome["delivery_start_utc"],
        "system_imbalance": _num(outcome, "systemimbalance"),
        "imbalance_price": _num(outcome, "imbalanceprice"),
    }).dropna(subset=["delivery_start_utc", "imbalance_price"])
    base = base.sort_values("delivery_start_utc").drop_duplicates("delivery_start_utc", keep="last")

    prices = da_prices[["delivery_start_utc", "entry_price"]].copy()
    prices["delivery_start_utc"] = pd.to_datetime(prices["delivery_start_utc"], utc=True, errors="coerce")
    base = base.merge(prices, on="delivery_start_utc", how="inner", validate="one_to_one")
    if base.empty:
        raise RuntimeError("NO_ENTRY_OUTCOME_INTERSECTION")

    wind = _aggregate_wind(_read_canonical(canonical_root, "ods031"))
    solar = _aggregate_solar(_read_canonical(canonical_root, "ods032"))
    load = _aggregate_load(_read_canonical(canonical_root, "ods001"))
    for x in (wind, solar, load):
        if not x.empty:
            base = base.merge(x, on="delivery_start_utc", how="left", validate="one_to_one")

    base = base.sort_values("delivery_start_utc").reset_index(drop=True)
    local = base["delivery_start_utc"].dt.tz_convert(BRUSSELS)
    base["delivery_date"] = local.dt.tz_localize(None).dt.normalize()
    base["ctx_local_hour"] = local.dt.hour.astype(float)
    base["ctx_qh_of_day"] = (local.dt.hour * 4 + (local.dt.minute // 15)).astype(float)
    base["ctx_weekday"] = local.dt.weekday.astype(float)
    base["ctx_month"] = local.dt.month.astype(float)
    base["ctx_weekend"] = (local.dt.weekday >= 5).astype(float)
    base["da_gate_utc"] = _gate_timestamp(base["delivery_start_utc"])
    base["safe_history_cutoff_utc"] = base["delivery_start_utc"] - pd.Timedelta(hours=48)
    if (base["safe_history_cutoff_utc"] > base["da_gate_utc"]).any():
        raise RuntimeError("SAFE_HISTORY_CUTOFF_AFTER_DA_GATE")

    idx = pd.DatetimeIndex(base["delivery_start_utc"])
    base["si_lag48h_mw"] = _lookup_lag(base["system_imbalance"], idx)
    base["si_roll7d_safe_mean_mw"] = _safe_rolling_lookup(base["system_imbalance"], idx, "7D")
    si14 = _safe_rolling_lookup(base["system_imbalance"], idx, "14D", kind="sum")
    sia14 = _safe_rolling_lookup(base["system_imbalance"], idx, "14D", kind="abs_sum")
    base["si_roll14d_safe_directionality"] = np.where(sia14 > 0, np.abs(si14) / sia14, np.nan)
    base["imb_price_lag48h_eur_mwh"] = _lookup_lag(base["imbalance_price"], idx)
    base["imb_price_roll7d_safe_mean_eur_mwh"] = _safe_rolling_lookup(base["imbalance_price"], idx, "7D")
    base["da_price_lag48h_eur_mwh"] = _lookup_lag(base["entry_price"], idx)
    base["da_price_roll7d_safe_mean_eur_mwh"] = _safe_rolling_lookup(base["entry_price"], idx, "7D")

    for src, dst in [
        ("load_actual_mw", "load_actual_lag48h_mw"),
        ("wind_actual_mw", "wind_actual_lag48h_mw"),
        ("solar_actual_mw", "solar_actual_lag48h_mw"),
    ]:
        if src in base:
            base[dst] = _lookup_lag(base[src], idx)
        else:
            base[dst] = np.nan
    base["residual_actual_lag48h_mw"] = base["load_actual_lag48h_mw"] - base["wind_actual_lag48h_mw"] - base["solar_actual_lag48h_mw"]

    if "wind_da11h_mw" not in base:
        base["wind_da11h_mw"] = np.nan
    if "solar_da11h_mw" not in base:
        base["solar_da11h_mw"] = np.nan
    base["renewable_da11h_mw"] = base["wind_da11h_mw"] + base["solar_da11h_mw"]
    hour_key = local.dt.floor("h")
    base["renewable_da11h_qh_spread_mw"] = base["renewable_da11h_mw"] - base.groupby(hour_key)["renewable_da11h_mw"].transform("mean")
    if "load_da18h_mw" not in base:
        base["load_da18h_mw"] = np.nan
    base["residual_load_mixed_timing_mw"] = base["load_da18h_mw"] - base["renewable_da11h_mw"]

    meta = {
        "rows": int(len(base)),
        "first_utc": base["delivery_start_utc"].min().isoformat(),
        "last_utc": base["delivery_start_utc"].max().isoformat(),
        "entry_outcome_match_rows": int(len(base)),
        "entry_non_null": int(base["entry_price"].notna().sum()),
        "imbalance_non_null": int(base["imbalance_price"].notna().sum()),
    }
    return base, meta


def write_profit_panels(base: pd.DataFrame, research_root: Path) -> dict:
    outdir = research_root / "profit"
    outdir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, view, sign in [
        ("DA_SHORT", "SHORT_SYSTEM", 1.0),
        ("DA_LONG", "LONG_SYSTEM", -1.0),
    ]:
        x = base.copy()
        x["system_view"] = view
        edge = (x["imbalance_price"] - x["entry_price"]) * sign
        x["gross_pnl_1mw_eur"] = edge * 0.25
        p = outdir / f"PROFIT_PANEL_{name}.parquet"
        x.to_parquet(p, index=False)
        paths[name] = str(p)
    return paths


def build_workspace(repo_root: Path, start: date = DEFAULT_DA_START, end: date | None = None, session: requests.Session | None = None) -> dict:
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    canonical_root = repo_root / "data" / "canonical"
    raw_root = repo_root / "data" / "raw" / "energy_charts" / "be_day_ahead"
    research_root = repo_root / "research"
    outdir = research_root / "profit"
    outdir.mkdir(parents=True, exist_ok=True)
    price_cache = canonical_root / "be_da_price_energy_charts.parquet"

    existing = pd.read_parquet(price_cache) if price_cache.exists() else pd.DataFrame()
    fetch_from = start
    if not existing.empty and "delivery_start_utc" in existing:
        mx = pd.to_datetime(existing["delivery_start_utc"], utc=True, errors="coerce").max()
        if pd.notna(mx):
            fetch_from = max(start, (mx.date() - timedelta(days=2)))

    entry_meta: dict[str, Any]
    fresh = pd.DataFrame()
    try:
        fresh, entry_meta = fetch_energy_charts_da(fetch_from, end, raw_root, session=session)
    except requests.RequestException as exc:
        entry_meta = {"status": "FETCH_FAILED_USING_CACHE", "error": f"{type(exc).__name__}: {exc}"}
    except RuntimeError as exc:
        entry_meta = {"status": "FETCH_VALIDATION_FAILED_USING_CACHE", "error": str(exc)}

    prices = merge_price_cache(existing, fresh)
    if not prices.empty:
        canonical_root.mkdir(parents=True, exist_ok=True)
        prices.to_parquet(price_cache, index=False)

    registry = feature_registry()
    registry_path = outdir / "FEATURE_REGISTRY.csv"
    registry.to_csv(registry_path, index=False)

    if prices.empty:
        payload = {
            "status": "BLOCKED_ENTRY_PRICE",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "entry": entry_meta,
            "entry_cache_rows": 0,
            "certified_feature_count": int(registry.pit_status.eq("CERTIFIED").sum()),
            "blockers": ["No public Belgian day-ahead entry-price rows available"],
        }
        (outdir / "READINESS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    base, base_meta = build_profit_base(canonical_root, prices)
    panel_paths = write_profit_panels(base, research_root)
    certified = registry[registry.pit_status.eq("CERTIFIED")]
    candidate = registry[registry.pit_status.str.startswith("CANDIDATE")]
    payload = {
        "status": "PASS_PROVISIONAL_PUBLIC_ENTRY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "route": "BELGIUM_SDAC_DA_TO_IMBALANCE_PT15",
        "da_gate": "12:00 CET/CEST D-1",
        "entry_source": entry_meta,
        "entry_cache_rows": int(len(prices)),
        "base": base_meta,
        "panels": panel_paths,
        "feature_registry": str(registry_path),
        "certified_feature_count": int(len(certified)),
        "candidate_field_time_feature_count": int(len(candidate)),
        "strict_features": certified.column_name.tolist(),
        "candidate_features_not_used_by_strict_discovery": candidate.column_name.tolist(),
        "promotion_eligible": False,
        "evidence_classification": "DIAGNOSTIC/PSEUDO_OOS",
        "known_blockers": [
            "A 48-hour lag does not establish immutable as-of publication or revision provenance; all measured-memory features remain PIT_UNCERTIFIED",
            "Repeated chronological splits are not an independently sealed holdout; outcome publication provenance and full adaptive trial history are unavailable",
            "Energy-Charts is a public secondary entry source; independent official EPEX/ENTSO-E cross-check still required before promotion",
            "Wind/solar Day Ahead 11AM fields are source-labelled but remain outside strict discovery until publication timing is fully certified",
            "ODS001 day-ahead load is the 6PM forecast and is after the 12:00 DA gate, so it is excluded from strict DA discovery",
            "Historical Belgian intraday entry-price series is still missing for IDA/continuous routes",
        ],
        "policy": "Use only CERTIFIED features for strict profit discovery. Candidate/unsafe fields remain in the panel for audit but cannot drive promotion.",
    }
    (outdir / "READINESS.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload

