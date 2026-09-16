from __future__ import annotations

import json
import random
import time as sleep_time
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .config import SourceSpec
from .provenance import save_raw_vintage, utc_now

BRUSSELS = ZoneInfo("Europe/Brussels")
# The live endpoint reports a nominal two-day cap, but a request spanning two
# Brussels business days can still be rejected at boundary/DST semantics.
# One business day per HTTP range is cheap, deterministic and fail-safe.
JAO_MAX_RANGE_DAYS = 1
JAO_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


class JAOCollectorError(RuntimeError):
    pass


def business_day_param(day: date) -> str:
    """Return UTC instant corresponding to Brussels midnight for a Core business day."""
    local_midnight = datetime.combine(day, time.min, tzinfo=BRUSSELS)
    return local_midnight.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _rows_from_payload(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "finalComputation", "maxExchanges"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        for value in payload.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return [row for row in value if isinstance(row, dict)]
    raise JAOCollectorError(f"unexpected JSON schema type={type(payload).__name__}")


def _validate_final_computation_rows(rows: list[dict]) -> None:
    if not rows:
        raise JAOCollectorError("Final Computation response contains no rows")
    keys = {str(k).lower().replace("_", "").replace("-", "") for row in rows[:100] for k in row}
    has_ram = "ram" in keys or any(k.endswith("ram") for k in keys)
    has_ptdf = any("ptdf" in k for k in keys)
    has_cnec = any("cnec" in k or "criticalnetworkelement" in k for k in keys)
    if not (has_ram and has_ptdf and has_cnec):
        raise JAOCollectorError(
            f"Final Computation schema proof failed: ram={has_ram}, ptdf={has_ptdf}, cnec={has_cnec}"
        )


def _range_windows(start_day: date, end_day: date):
    """Yield conservative API-safe [start, end) one-business-day windows."""
    cur = start_day
    while cur < end_day:
        nxt = min(end_day, cur + timedelta(days=JAO_MAX_RANGE_DAYS))
        yield cur, nxt
        cur = nxt


def _request_with_retry(
    url: str,
    *,
    params: dict,
    timeout: int,
    retries: int = 4,
    base_sleep_seconds: float = 1.0,
    request_get=None,
):
    """GET JAO with bounded retry on transient transport/server failures only.

    Hard 4xx responses are returned immediately so schema/range/auth defects do
    not get hidden behind retries. Transport exceptions and explicitly
    retryable HTTP statuses use exponential backoff with small jitter.
    """
    getter = request_get or requests.get
    last_exc: Exception | None = None
    last_response = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            response = getter(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "belgio-public/0.9"},
            )
            last_response = response
            if response.status_code not in JAO_RETRYABLE_HTTP:
                return response, attempt
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < max(1, retries):
            delay = base_sleep_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, min(0.25, base_sleep_seconds))
            sleep_time.sleep(delay)
    if last_response is not None:
        return last_response, max(1, retries)
    raise JAOCollectorError(f"network error after retries={max(1, retries)}: {last_exc}") from last_exc


def collect_jao_final_computation(
    spec: SourceSpec,
    start_day: date,
    end_day: date,
    raw_root: Path,
    *,
    timeout: int = 60,
    take: int = 40000,
    max_pages: int = 200,
    retries: int = 4,
) -> dict:
    """Collect Core Final Computation for Brussels business days [start_day, end_day).

    Requests are split into one-business-day ranges. This is deliberately more
    conservative than the nominal API cap and avoids boundary/DST range errors.
    Each page is preserved with its exact response URL and retrieval time.
    Transient transport and server failures are retried without weakening any
    content/schema validation.
    """
    if end_day <= start_day:
        raise ValueError("end_day must be after start_day")

    all_rows: list[dict] = []
    raw_paths: list[str] = []
    meta_paths: list[str] = []
    request_urls: list[str] = []
    retry_attempts: list[int] = []
    last_retrieved = None
    window_count = 0

    for window_start, window_end in _range_windows(start_day, end_day):
        window_count += 1
        base_params = {
            "FromUtc": business_day_param(window_start),
            "ToUtc": business_day_param(window_end),
        }
        window_had_rows = False

        for page in range(max_pages):
            params = {**base_params, "skip": page * take, "take": take}
            try:
                response, attempts = _request_with_retry(
                    spec.endpoint,
                    params=params,
                    timeout=timeout,
                    retries=retries,
                )
            except JAOCollectorError as exc:
                raise JAOCollectorError(f"network error window={window_start}:{window_end}: {exc}") from exc
            retry_attempts.append(attempts)
            if response.status_code != 200:
                raise JAOCollectorError(
                    f"HTTP {response.status_code} window={window_start}:{window_end}: {response.text[:500]}"
                )
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise JAOCollectorError(f"JAO response is not JSON window={window_start}:{window_end}") from exc

            rows = _rows_from_payload(payload)
            if page == 0 and rows:
                _validate_final_computation_rows(rows)
            if not rows:
                break
            window_had_rows = True

            retrieved = utc_now()
            raw, meta = save_raw_vintage(
                raw_root,
                "JAO",
                spec.id,
                response.content,
                url=response.url,
                retrieved_at=retrieved,
                headers=response.headers,
                suffix=f"{window_start.isoformat()}_{window_end.isoformat()}_page{page:04d}.json",
            )
            raw_paths.append(str(raw))
            meta_paths.append(str(meta))
            request_urls.append(response.url)
            last_retrieved = retrieved
            all_rows.extend(rows)

            if len(rows) < take:
                break
        else:
            raise JAOCollectorError(
                f"pagination exceeded max_pages={max_pages} window={window_start}:{window_end}"
            )

        if not window_had_rows:
            continue

    if not all_rows or last_retrieved is None:
        raise JAOCollectorError("Final Computation collection produced no data")

    df = pd.DataFrame(all_rows)
    if "id" in df.columns:
        df = df.drop_duplicates(subset=["id"], keep="last")
    else:
        df = df.drop_duplicates(keep="last")

    return {
        "source": spec,
        "df": df,
        "retrieved_at": last_retrieved,
        "url": request_urls[-1],
        "raw_path": raw_paths[-1],
        "meta_path": meta_paths[-1],
        "raw_paths": raw_paths,
        "meta_paths": meta_paths,
        "request_urls": request_urls,
        "api_safe_windows": window_count,
        "http_request_count": len(retry_attempts),
        "http_retry_extra_attempts": int(sum(max(0, n - 1) for n in retry_attempts)),
        "max_attempts_for_one_request": int(max(retry_attempts) if retry_attempts else 0),
    }


def collect_jao_maxexchanges(
    spec: SourceSpec,
    day: date,
    raw_root: Path,
    timeout: int = 60,
    retries: int = 4,
) -> dict:
    """Legacy compatibility collector using the modern FromUtc/ToUtc contract."""
    retrieved = utc_now()
    params = {
        "FromUtc": business_day_param(day),
        "ToUtc": business_day_param(date.fromordinal(day.toordinal() + 1)),
        "skip": 0,
        "take": 40000,
    }
    response, attempts = _request_with_retry(
        spec.endpoint,
        params=params,
        timeout=timeout,
        retries=retries,
    )
    if response.status_code != 200:
        raise JAOCollectorError(f"HTTP {response.status_code}: {response.text[:300]}")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise JAOCollectorError("JAO response is not JSON") from exc
    rows = _rows_from_payload(payload)
    if not rows:
        raise JAOCollectorError("Max Exchanges response contains no rows")
    raw, meta = save_raw_vintage(
        raw_root,
        "JAO",
        spec.id,
        response.content,
        url=response.url,
        retrieved_at=retrieved,
        headers=response.headers,
        suffix="json",
    )
    return {
        "source": spec,
        "df": pd.DataFrame(rows),
        "retrieved_at": retrieved,
        "url": response.url,
        "raw_path": raw,
        "meta_path": meta,
        "http_attempts": attempts,
    }
