from __future__ import annotations
import io
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

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


def collect_elia(spec: SourceSpec, raw_root: Path, *, since: str | None = None, timeout: int = 120) -> dict:
    if spec.provider != "Elia":
        raise ValueError(spec.id)
    retrieved = utc_now()
    where = None
    if since and spec.mode == "historical":
        # Opendatasoft Explore v2.1 date literal; provider rejection is a hard collector failure.
        where = f"datetime > date'{since}'"
    url = _export_url(spec.id, where=where)
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "belgio-public/0.1 (+public research)"})
    except requests.RequestException as e:
        raise CollectorError(f"{spec.id}: network error: {e}") from e
    if r.status_code != 200:
        raise CollectorError(f"{spec.id}: HTTP {r.status_code}: {r.text[:300]}")
    body = r.content
    if not body.strip():
        raise CollectorError(f"{spec.id}: empty response")
    if body[:1] in (b"{", b"[") and b"error" in body[:500].lower():
        raise CollectorError(f"{spec.id}: provider returned error payload")
    raw_path, meta_path = save_raw_vintage(raw_root, "Elia", spec.id, body, url=r.url, retrieved_at=retrieved, headers=r.headers, suffix="csv")
    try:
        df = pd.read_csv(io.BytesIO(body), sep=";", low_memory=False)
        if len(df.columns) == 1:
            df = pd.read_csv(io.BytesIO(body), low_memory=False)
    except Exception as e:
        raise CollectorError(f"{spec.id}: CSV parse failed: {e}") from e
    return {"source": spec, "df": df, "retrieved_at": retrieved, "url": r.url, "raw_path": raw_path, "meta_path": meta_path}
