from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

NORDPOOL_PUBLIC_URL = "https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices"
BRUSSELS = "Europe/Brussels"


class NordPoolPublicError(RuntimeError):
    pass


def parse_nordpool_public_payload(payload: dict[str, Any], area: str = "BE") -> pd.DataFrame:
    entries = payload.get("multiAreaEntries")
    if not isinstance(entries, list):
        raise NordPoolPublicError("NORDPOOL_BAD_MULTI_AREA_ENTRIES")
    rows: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        per_area = row.get("entryPerArea")
        if not isinstance(per_area, dict) or area not in per_area:
            continue
        rows.append(
            {
                "delivery_start_utc": row.get("deliveryStart"),
                "delivery_end_utc": row.get("deliveryEnd"),
                "entry_price": per_area.get(area),
                "status": row.get("status"),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["delivery_start_utc", "delivery_end_utc", "entry_price", "status"])
    out["delivery_start_utc"] = pd.to_datetime(out["delivery_start_utc"], utc=True, errors="coerce")
    out["delivery_end_utc"] = pd.to_datetime(out["delivery_end_utc"], utc=True, errors="coerce")
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    out = out.dropna(subset=["delivery_start_utc", "entry_price"])
    return out.sort_values("delivery_start_utc").drop_duplicates("delivery_start_utc", keep="last").reset_index(drop=True)


def _validate_day(day: date, rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        raise NordPoolPublicError(f"NORDPOOL_EMPTY_DAY:{day}")
    ts = rows["delivery_start_utc"].drop_duplicates().sort_values()
    diffs = ts.diff().dropna().dt.total_seconds()
    common = float(diffs.mode().iloc[0]) if len(diffs) else np.nan
    local_dates = ts.dt.tz_convert(BRUSSELS).dt.date
    in_day = int((local_dates == day).sum())
    if len(ts) not in {92, 96, 100} or in_day != len(ts):
        raise NordPoolPublicError(f"NORDPOOL_BAD_DAY_CARDINALITY:{day}:rows={len(ts)}:local_rows={in_day}")
    if len(diffs) and common != 900.0:
        raise NordPoolPublicError(f"NORDPOOL_NOT_PT15:{day}:common_step={common}")
    return {"date": day.isoformat(), "rows": int(len(ts)), "common_step_seconds": common}


def fetch_nordpool_public_day(
    day: date,
    raw_root: Path,
    *,
    area: str = "BE",
    currency: str = "EUR",
    session: requests.Session | None = None,
    timeout: int = 30,
    retries: int = 3,
    persist_raw: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch one recent delivery day for validation/cross-check purposes.

    The public Data Portal backend is deliberately *not* used as the historical
    source of record. Nord Pool sells historical access and redistribution
    rights separately, so public-repo collection stays transient by default:
    values may be compared in-memory, while raw market data are not written to
    the public artifact unless a future explicitly licensed use enables it.
    """
    session = session or requests.Session()
    params = {"date": day.isoformat(), "market": "DayAhead", "deliveryArea": area, "currency": currency}
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        retrieved = datetime.now(timezone.utc)
        try:
            r = session.get(
                NORDPOOL_PUBLIC_URL,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "belgio-public/0.8"},
            )
            if r.status_code == 204:
                raise NordPoolPublicError(f"NORDPOOL_NO_CONTENT:{day}")
            r.raise_for_status()
            payload = r.json()
            parsed = parse_nordpool_public_payload(payload, area=area)
            quality = _validate_day(day, parsed)
            parsed["entry_source"] = "Nord Pool public Data Portal"
            parsed["entry_source_upstream"] = "Nord Pool Day-Ahead / SDAC"
            parsed["entry_source_url"] = NORDPOOL_PUBLIC_URL
            parsed["entry_retrieved_at_utc"] = pd.Timestamp(retrieved)
            if persist_raw:
                raw_root.mkdir(parents=True, exist_ok=True)
                stamp = retrieved.strftime("%Y%m%dT%H%M%SZ")
                (raw_root / f"{day}_{stamp}.json").write_text(
                    json.dumps(
                        {"request": params, "retrieved_at_utc": retrieved.isoformat(), "http_status": r.status_code, "payload": payload},
                        default=str,
                    ),
                    encoding="utf-8",
                )
            meta = {
                "status": "PASS",
                "source": "Nord Pool public Data Portal backend",
                "classification": "RECENT_TRANSIENT_CROSSCHECK_ONLY",
                "raw_persisted": bool(persist_raw),
                "url": NORDPOOL_PUBLIC_URL,
                "area": area,
                "currency": currency,
                **quality,
            }
            return parsed, meta
        except (requests.RequestException, ValueError, NordPoolPublicError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2.0 * attempt, 5.0))
    raise NordPoolPublicError(f"NORDPOOL_FETCH_FAILED:{day}:{type(last_error).__name__}:{last_error}")


def missing_local_days(cache: pd.DataFrame, start: date, end: date) -> list[date]:
    wanted = list(pd.date_range(start, end, freq="D").date)
    if cache is None or cache.empty or "delivery_start_utc" not in cache:
        return wanted
    ts = pd.to_datetime(cache["delivery_start_utc"], utc=True, errors="coerce").dropna()
    have = set(ts.dt.tz_convert(BRUSSELS).dt.date)
    return [d for d in wanted if d not in have]


def backfill_nordpool_public(
    existing: pd.DataFrame,
    start: date,
    end: date,
    raw_root: Path,
    *,
    max_days: int = 8,
    max_workers: int = 4,
    session: requests.Session | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch only a small recent window for cross-source validation.

    This function keeps the historical name for API compatibility, but it no
    longer attempts to backfill old Nord Pool prices into the public project.
    It chooses the most recent missing days, plus the latest three completed
    days for refresh, and never persists raw responses by default.
    """
    miss = missing_local_days(existing, start, end)
    selected = miss[-max_days:] if max_days > 0 else []
    recent = [end - timedelta(days=i) for i in range(0, 3) if end - timedelta(days=i) >= start]
    for d in recent:
        if d not in selected:
            selected.append(d)
    selected = sorted(set(selected))

    started = time.monotonic()
    results: dict[date, tuple[pd.DataFrame, dict[str, Any]]] = {}
    failures_by_day: dict[date, str] = {}
    requested_workers = max(1, int(max_workers))
    workers_used = 1 if session is not None else min(requested_workers, max(1, len(selected)))

    def _fetch_one(day: date) -> tuple[pd.DataFrame, dict[str, Any]]:
        if session is not None:
            return fetch_nordpool_public_day(day, raw_root, session=session, persist_raw=False)
        with requests.Session() as local_session:
            return fetch_nordpool_public_day(day, raw_root, session=local_session, persist_raw=False)

    if workers_used <= 1:
        for day in selected:
            try:
                results[day] = _fetch_one(day)
            except Exception as exc:
                failures_by_day[day] = f"{type(exc).__name__}: {exc}"
    else:
        with ThreadPoolExecutor(max_workers=workers_used, thread_name_prefix="nordpool-be-da") as pool:
            futures = {pool.submit(_fetch_one, day): day for day in selected}
            for future in as_completed(futures):
                day = futures[future]
                try:
                    results[day] = future.result()
                except Exception as exc:
                    failures_by_day[day] = f"{type(exc).__name__}: {exc}"

    frames: list[pd.DataFrame] = []
    day_meta: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for day in selected:
        if day in results:
            x, meta = results[day]
            frames.append(x)
            day_meta.append(meta)
        elif day in failures_by_day:
            failures.append({"date": day.isoformat(), "error": failures_by_day[day]})

    fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    meta = {
        "status": "PASS" if frames else "FAIL",
        "source": "Nord Pool public Data Portal backend",
        "classification": "RECENT_TRANSIENT_CROSSCHECK_ONLY",
        "historical_source_of_record": False,
        "requested_missing_days": int(len(miss)),
        "attempted_days": int(len(selected)),
        "successful_days": int(len(day_meta)),
        "failed_days": int(len(failures)),
        "rows": int(len(fresh)),
        "max_workers_requested": requested_workers,
        "workers_used": workers_used,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "days": day_meta,
        "failures": failures[:50],
    }
    return fresh, meta
