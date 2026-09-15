from __future__ import annotations
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .config import SourceSpec
from .provenance import save_raw_vintage, utc_now

BRUSSELS = ZoneInfo("Europe/Brussels")


class JAOCollectorError(RuntimeError):
    pass


def business_day_param(day: date) -> str:
    """Return UTC instant corresponding to Brussels midnight for a Core business day."""
    local_midnight = datetime.combine(day, time.min, tzinfo=BRUSSELS)
    return local_midnight.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def collect_jao_maxexchanges(spec: SourceSpec, day: date, raw_root: Path, timeout: int = 60) -> dict:
    retrieved = utc_now()
    params = {"date": business_day_param(day)}
    try:
        r = requests.get(spec.endpoint, params=params, timeout=timeout, headers={"User-Agent": "belgio-public/0.1"})
    except requests.RequestException as e:
        raise JAOCollectorError(f"network error: {e}") from e
    if r.status_code != 200:
        raise JAOCollectorError(f"HTTP {r.status_code}: {r.text[:300]}")
    try:
        payload = r.json()
    except json.JSONDecodeError as e:
        raise JAOCollectorError("JAO response is not JSON") from e
    rows = payload.get("maxExchanges") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise JAOCollectorError(f"unexpected schema keys={list(payload)[:20] if isinstance(payload, dict) else type(payload)}")
    raw, meta = save_raw_vintage(raw_root, "JAO", spec.id, r.content, url=r.url, retrieved_at=retrieved, headers=r.headers, suffix="json")
    return {"source": spec, "df": pd.DataFrame(rows), "retrieved_at": retrieved, "url": r.url, "raw_path": raw, "meta_path": meta}
