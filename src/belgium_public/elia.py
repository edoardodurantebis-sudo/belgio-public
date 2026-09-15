from __future__ import annotations
import io
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


def _export_url(source_id: str, where: str | None = None) -> str:
    params = {"timezone": "UTC", "use_labels_for_header": "false"}
    if where:
        params["where"] = where
    return f"{BASE}/{source_id}/exports/csv?{urlencode(params)}"


def _session() -> requests.Session:
    # Elia's public portal can transiently return 429/5xx under load. Retry only
    # safe GETs and still fail closed once the bounded retry budget is exhausted.
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


def collect_elia(spec: SourceSpec, raw_root: Path, *, since: str | None = None, timeout: tuple[int, int] = (20, 180)) -> dict:
    if spec.provider != "Elia":
        raise ValueError(spec.id)
    retrieved = utc_now()
    where = None
    if since and spec.mode == "historical":
        # Opendatasoft Explore v2.1 accepts ODSQL date literals. Provider
        # rejection is a hard collector failure; never silently fall back to a
        # full-history rewrite on an incremental run.
        where = f"datetime > date'{since}'"
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
    }
