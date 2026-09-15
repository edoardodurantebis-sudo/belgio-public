from __future__ import annotations
import re
from pathlib import Path
import pandas as pd
from .config import SourceSpec


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def resolve_col(df: pd.DataFrame, wanted: str) -> str | None:
    target = _norm(wanted)
    for c in df.columns:
        if _norm(str(c)) == target:
            return c
    aliases = {
        "datetime": ["date_time", "dateTimeUtc", "timestamp"],
        "offshoreonshore": ["offshore_onshore"],
        "fueltypepublication": ["fuel_type_publication"],
    }
    for a in aliases.get(wanted, []):
        for c in df.columns:
            if _norm(str(c)) == _norm(a):
                return c
    return None


def canonicalize(result: dict, canonical_root: Path) -> dict:
    spec: SourceSpec = result["source"]
    df = result["df"].copy()
    df.columns = [str(c).strip() for c in df.columns]
    dt_col = resolve_col(df, "datetime") or resolve_col(df, "dateTimeUtc")
    if dt_col:
        parsed = pd.to_datetime(df[dt_col], utc=True, errors="coerce")
        df["delivery_start_utc"] = parsed
    df["_source_id"] = spec.id
    df["_provider"] = spec.provider
    df["_retrieved_at_utc"] = pd.Timestamp(result["retrieved_at"])
    df["_source_url"] = result["url"]
    df["_pit_status"] = spec.pit_status

    out = canonical_root / f"{spec.id}.parquet"
    canonical_root.mkdir(parents=True, exist_ok=True)
    if out.exists():
        old = pd.read_parquet(out)
        df = pd.concat([old, df], ignore_index=True, sort=False)

    keys = []
    for k in spec.primary_key:
        c = resolve_col(df, k)
        if c:
            keys.append(c)
    if spec.preserve_vintages:
        if keys:
            keys = keys + ["_retrieved_at_utc"]
            df = df.drop_duplicates(subset=keys, keep="last")
        else:
            df = df.drop_duplicates()
    else:
        if keys:
            df = df.sort_values("_retrieved_at_utc").drop_duplicates(subset=keys, keep="last")
        else:
            df = df.drop_duplicates()
    df.to_parquet(out, index=False)
    return {"source_id": spec.id, "path": out, "rows": len(df), "columns": list(df.columns)}
