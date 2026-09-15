from __future__ import annotations
import io
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import SourceSpec
from .provenance import save_raw_vintage, utc_now

BASE = "https://opendata.elia.be/api/explore/v2.1/catalog/datasets"


class CollectorError(RuntimeError):
    pass


def _session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
    s = requests.Session()
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": "belgio-public/0.2 (+public reproducible research)"})
    return s


def _fmt_ods(dt: str | pd.Timestamp | datetime) -> str:
    t = pd.Timestamp(dt)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _export_url(source_id: str, where: str | None = None) -> str:
    params = {"timezone": "UTC", "use_labels_for_header": "false"}
    if where:
        params["where"] = where
    return f"{BASE}/{source_id}/exports/csv?{urlencode(params)}"


def discover_datetime_bounds(spec: SourceSpec, timeout: tuple[int, int] = (20, 60)) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Discover provider-native first/last datetime without downloading history."""
    url = f"{BASE}/{spec.id}/records"
    values: list[pd.Timestamp] = []
    try:
        with _session() as session:
            for order_by in ("datetime", "datetime desc"):
                r = session.get(
                    url,
                    params={"select": "datetime", "order_by": order_by, "limit": 1, "timezone": "UTC"},
                    timeout=timeout,
                )
                if r.status_code != 200:
                    raise CollectorError(f"{spec.id}: bounds HTTP {r.status_code}: {r.text[:300]}")
                payload = r.json()
                rows = payload.get("results", []) if isinstance(payload, dict) else []
                if not rows or "datetime" not in rows[0]:
                    raise CollectorError(f"{spec.id}: cannot discover datetime bounds")
                value = pd.to_datetime(rows[0]["datetime"], utc=True, errors="raise")
                values.append(pd.Timestamp(value))
    except requests.RequestException as e:
        raise CollectorError(f"{spec.id}: bounds network error: {e}") from e
    if values[0] > values[1]:
        values.reverse()
    return values[0], values[1]


def collect_elia(
    spec: SourceSpec,
    raw_root: Path,
    *,
    after: str | pd.Timestamp | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    timeout: tuple[int, int] = (20, 180),
) -> dict:
    if spec.provider != "Elia":
        raise ValueError(spec.id)
    if after is not None and start is not None:
        raise ValueError("Use after or start, not both")

    clauses: list[str] = []
    if after is not None:
        clauses.append(f"datetime > date'{_fmt_ods(after)}'")
    if start is not None:
        clauses.append(f"datetime >= date'{_fmt_ods(start)}'")
    if end is not None:
        clauses.append(f"datetime < date'{_fmt_ods(end)}'")
    where = " and ".join(clauses) or None

    retrieved = utc_now()
    url = _export_url(spec.id, where=where)
    try:
        with _session() as session:
            r = session.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise CollectorError(f"{spec.id}: network error after bounded retries: {e}") from e
    if r.status_code != 200:
        raise CollectorError(f"{spec.id}: HTTP {r.status_code}: {r.text[:300]}")
    body = r.content
    if not body.strip():
        raise CollectorError(f"{spec.id}: empty response")
    if body[:1] in (b"{", b"[") and b"error" in body[:500].lower():
        raise CollectorError(f"{spec.id}: provider returned error payload")

    raw_path, meta_path = save_raw_vintage(
        raw_root,
        "Elia",
        spec.id,
        body,
        url=r.url,
        retrieved_at=retrieved,
        headers=r.headers,
        suffix="csv",
    )
    try:
        df = pd.read_csv(io.BytesIO(body), sep=";", low_memory=False)
        if len(df.columns) == 1:
            df = pd.read_csv(io.BytesIO(body), low_memory=False)
    except Exception as e:
        raise CollectorError(f"{spec.id}: CSV parse failed: {e}") from e
    return {
        "source": spec,
        "df": df,
        "retrieved_at": retrieved,
        "url": r.url,
        "raw_path": raw_path,
        "meta_path": meta_path,
        "window": {"after": str(after) if after is not None else None, "start": str(start) if start is not None else None, "end": str(end) if end is not None else None},
    }
