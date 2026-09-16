from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

REVONERGI_DAY_URL = "https://revonergi.be/stroomprijzen/data.json"
REVONERGI_RIGHTS_URL = "https://revonergi.be/stroomprijzen"
BRUSSELS = ZoneInfo("Europe/Brussels")
QH_GO_LIVE = date(2025, 10, 1)


class RevonergiPublicError(RuntimeError):
    pass


def _candidate_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    preferred = (
        "prices", "prijzen", "data", "values", "waarden", "items", "records",
        "quarters", "kwartieren", "hours", "uren", "today", "day",
    )
    for key in preferred:
        v = payload.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict) and v:
            # Common compact form: {"00:00": 82.1, ...}
            if all(isinstance(k, str) for k in v):
                return [{"time": k, "price": val} for k, val in v.items()]
    # Last-resort: first list of row-like values.
    for v in payload.values():
        if isinstance(v, list) and v:
            return v
    # Or the payload itself may be a time->price map.
    if payload and all(isinstance(k, str) for k in payload):
        hhmm = sum(bool(re.fullmatch(r"\d{1,2}:\d{2}", k)) for k in payload)
        if hhmm >= max(2, len(payload) // 2):
            return [{"time": k, "price": val} for k, val in payload.items()]
    return []


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    folded = {str(k).casefold().replace("_", "").replace("-", ""): v for k, v in row.items()}
    for k in keys:
        kk = k.casefold().replace("_", "").replace("-", "")
        if kk in folded:
            return folded[kk]
    return None


def _localize_hhmm(day: date, value: str, occurrence: int = 0) -> pd.Timestamp:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", str(value))
    if not m:
        raise ValueError(value)
    h, minute, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h == 24:
        base = datetime.combine(day + timedelta(days=1), dtime(0, minute, sec))
    else:
        base = datetime.combine(day, dtime(h, minute, sec))
    # Ambiguous autumn hour is uncommon in compact APIs. Pandas needs an
    # explicit choice; occurrence=0 picks first, occurrence=1 second.
    ts = pd.Timestamp(base)
    try:
        local = ts.tz_localize(BRUSSELS, ambiguous=bool(occurrence), nonexistent="shift_forward")
    except TypeError:
        local = ts.tz_localize(BRUSSELS)
    return local.tz_convert("UTC")


def _price_eur_mwh(row: dict[str, Any]) -> float | None:
    direct_mwh = _first(row, (
        "price_eur_mwh", "eur_mwh", "eurpermwh", "europermwh", "prijs_eur_mwh",
        "mwh", "priceMWh", "prijsMWh",
    ))
    if direct_mwh is not None:
        try:
            return float(str(direct_mwh).replace(",", "."))
        except Exception:
            return None
    direct_kwh = _first(row, ("price_eur_kwh", "eur_kwh", "eurperkwh", "prijs_eur_kwh", "kwh"))
    if direct_kwh is not None:
        try:
            return float(str(direct_kwh).replace(",", ".")) * 1000.0
        except Exception:
            return None
    cents = _first(row, ("centperkwh", "centsperkwh", "ctperkwh", "cent_kwh"))
    if cents is not None:
        try:
            return float(str(cents).replace(",", ".")) * 10.0
        except Exception:
            return None
    generic = _first(row, ("price", "prijs", "value", "waarde"))
    if generic is None:
        return None
    try:
        v = float(str(generic).replace(",", "."))
    except Exception:
        return None
    # Generic field: the page/API presents both EUR/kWh and EUR/MWh. A value
    # whose absolute magnitude is < 5 is overwhelmingly EUR/kWh for wholesale
    # electricity and is converted; otherwise treat as EUR/MWh.
    return v * 1000.0 if abs(v) < 5.0 else v


def parse_revonergi_payload(payload: Any, day: date) -> pd.DataFrame:
    rows = _candidate_rows(payload)
    out: list[dict[str, Any]] = []
    compact_times: list[str] = []
    for item in rows:
        if isinstance(item, (int, float)):
            # Pure numeric arrays are handled after row loop.
            continue
        if not isinstance(item, dict):
            continue
        price = _price_eur_mwh(item)
        if price is None or not np.isfinite(price):
            continue
        stamp = _first(item, (
            "delivery_start_utc", "deliveryStart", "datetime", "timestamp", "dateTime",
            "start", "startTime", "tijdstip", "time", "tijd", "uur",
        ))
        if stamp is None:
            continue
        s = str(stamp)
        if re.fullmatch(r"\s*\d{1,2}:\d{2}(?::\d{2})?\s*", s):
            compact_times.append(s.strip())
            # Count duplicate local labels on the autumn DST day.
            occurrence = sum(t == s.strip() for t in compact_times[:-1])
            try:
                ts = _localize_hhmm(day, s, occurrence=occurrence)
            except Exception:
                continue
        else:
            ts = pd.to_datetime(stamp, utc=True, errors="coerce")
            if pd.isna(ts):
                continue
        out.append({"delivery_start_utc": ts, "entry_price": price})

    if not out and isinstance(rows, list) and rows and all(isinstance(v, (int, float)) for v in rows):
        expected = 96 if day >= QH_GO_LIVE else 24
        if len(rows) in ({23, 24, 25} if day < QH_GO_LIVE else {92, 96, 100}):
            freq = "15min" if day >= QH_GO_LIVE else "1h"
            local_start = pd.Timestamp(day).tz_localize(BRUSSELS)
            local_end = pd.Timestamp(day + timedelta(days=1)).tz_localize(BRUSSELS)
            idx = pd.date_range(local_start, local_end, inclusive="left", freq=freq).tz_convert("UTC")
            if len(idx) == len(rows):
                out = [
                    {"delivery_start_utc": ts, "entry_price": float(v) * (1000.0 if abs(float(v)) < 5 else 1.0)}
                    for ts, v in zip(idx, rows)
                ]

    df = pd.DataFrame(out)
    if df.empty:
        return pd.DataFrame(columns=["delivery_start_utc", "entry_price"])
    df["delivery_start_utc"] = pd.to_datetime(df["delivery_start_utc"], utc=True, errors="coerce")
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    return (
        df.dropna(subset=["delivery_start_utc", "entry_price"])
        .sort_values("delivery_start_utc")
        .drop_duplicates("delivery_start_utc", keep="last")
        .reset_index(drop=True)
    )


def validate_revonergi_day(day: date, rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        raise RevonergiPublicError(f"REVONERGI_EMPTY_DAY:{day}")
    ts = pd.to_datetime(rows["delivery_start_utc"], utc=True, errors="coerce").dropna().drop_duplicates().sort_values()
    local_dates = ts.dt.tz_convert(BRUSSELS).dt.date
    if int((local_dates == day).sum()) != len(ts):
        raise RevonergiPublicError(f"REVONERGI_WRONG_LOCAL_DAY:{day}")
    diffs = ts.diff().dropna().dt.total_seconds()
    common = float(diffs.mode().iloc[0]) if len(diffs) else np.nan
    expected_counts = {92, 96, 100} if day >= QH_GO_LIVE else {23, 24, 25}
    expected_step = 900.0 if day >= QH_GO_LIVE else 3600.0
    if len(ts) not in expected_counts:
        raise RevonergiPublicError(f"REVONERGI_BAD_CARDINALITY:{day}:rows={len(ts)}")
    if len(diffs) and common != expected_step:
        raise RevonergiPublicError(f"REVONERGI_BAD_STEP:{day}:step={common}")
    return {"date": day.isoformat(), "rows": int(len(ts)), "common_step_seconds": common}


def fetch_revonergi_day(
    day: date,
    raw_root: Path,
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
    retries: int = 3,
    persist_raw: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session = session or requests.Session()
    params = {"dag": day.isoformat()}
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        retrieved = datetime.now(timezone.utc)
        try:
            r = session.get(
                REVONERGI_DAY_URL,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "belgio-public/0.8 (+public-research; attribution-preserved)"},
            )
            r.raise_for_status()
            payload = r.json()
            parsed = parse_revonergi_payload(payload, day)
            quality = validate_revonergi_day(day, parsed)
            parsed["entry_source"] = "Revonergi Belgian day-ahead archive"
            parsed["entry_source_upstream"] = "Energy-Charts / coupled Day-Ahead market as stated by Revonergi"
            parsed["entry_source_url"] = REVONERGI_DAY_URL
            parsed["entry_rights_url"] = REVONERGI_RIGHTS_URL
            parsed["entry_retrieved_at_utc"] = pd.Timestamp(retrieved)
            if persist_raw:
                raw_root.mkdir(parents=True, exist_ok=True)
                stamp = retrieved.strftime("%Y%m%dT%H%M%SZ")
                (raw_root / f"{day}_{stamp}.json").write_text(
                    json.dumps(
                        {
                            "request": params,
                            "retrieved_at_utc": retrieved.isoformat(),
                            "source": REVONERGI_DAY_URL,
                            "rights_page": REVONERGI_RIGHTS_URL,
                            "rights_classification": "SOURCE_STATES_FREE_USE_INCLUDING_COMMERCIAL_WITH_ATTRIBUTION_REQUESTED",
                            "payload": payload,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    encoding="utf-8",
                )
            meta = {
                "status": "PASS",
                "source": "Revonergi Belgian day-ahead archive",
                "classification": "PUBLIC_SOURCE_STATED_FREE_COMMERCIAL_REUSE",
                "rights_page": REVONERGI_RIGHTS_URL,
                "rights_assurance": "DOWNSTREAM_SOURCE_STATEMENT;UPSTREAM_RIGHTS_NOT_INDEPENDENTLY_VERIFIED",
                **quality,
            }
            return parsed, meta
        except (requests.RequestException, ValueError, RevonergiPublicError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(float(attempt), 3.0))
    raise RevonergiPublicError(f"REVONERGI_FETCH_FAILED:{day}:{type(last_error).__name__}:{last_error}")


def _present_days(cache: pd.DataFrame) -> set[date]:
    if cache is None or cache.empty or "delivery_start_utc" not in cache:
        return set()
    ts = pd.to_datetime(cache["delivery_start_utc"], utc=True, errors="coerce").dropna()
    return set(ts.dt.tz_convert(BRUSSELS).dt.date)


def backfill_revonergi_history(
    existing: pd.DataFrame,
    start: date,
    end: date,
    raw_root: Path,
    *,
    max_days: int = 400,
    max_workers: int = 4,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    have = _present_days(existing)
    missing = [d for d in pd.date_range(start, end, freq="D").date if d not in have]
    selected = missing[: max(0, int(max_days))]
    # Refresh latest two completed days to catch corrections.
    for d in (end, end - timedelta(days=1)):
        if d >= start and d not in selected:
            selected.append(d)
    selected = sorted(set(selected))

    started = time.monotonic()
    results: dict[date, tuple[pd.DataFrame, dict[str, Any]]] = {}
    failures: dict[date, str] = {}
    workers = min(max(1, int(max_workers)), max(1, len(selected)))

    def one(day: date):
        with requests.Session() as s:
            return fetch_revonergi_day(day, raw_root, session=s, persist_raw=True)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="revonergi-be-da") as pool:
        futures = {pool.submit(one, d): d for d in selected}
        for fut in as_completed(futures):
            d = futures[fut]
            try:
                results[d] = fut.result()
            except Exception as exc:
                failures[d] = f"{type(exc).__name__}: {exc}"

    frames: list[pd.DataFrame] = []
    day_meta: list[dict[str, Any]] = []
    failure_rows: list[dict[str, str]] = []
    for d in selected:
        if d in results:
            x, meta = results[d]
            frames.append(x)
            day_meta.append(meta)
        elif d in failures:
            failure_rows.append({"date": d.isoformat(), "error": failures[d]})
    fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return fresh, {
        "status": "PASS" if frames else "FAIL",
        "source": "Revonergi Belgian day-ahead archive",
        "classification": "PUBLIC_SOURCE_STATED_FREE_COMMERCIAL_REUSE",
        "rights_page": REVONERGI_RIGHTS_URL,
        "requested_missing_days": len(missing),
        "attempted_days": len(selected),
        "successful_days": len(day_meta),
        "failed_days": len(failure_rows),
        "rows": int(len(fresh)),
        "workers_used": workers,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "days": day_meta,
        "failures": failure_rows[:50],
    }
