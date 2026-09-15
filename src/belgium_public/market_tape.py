from __future__ import annotations
from datetime import date
from pathlib import Path
import pandas as pd

CORE_TAPE_SOURCES = ["ods001","ods031","ods032","ods026","ods127","ods132","ods134","ods166"]


def build_tape(day: date, canonical_root: Path, out_dir: Path) -> Path:
    start = pd.Timestamp(day, tz="UTC")
    end = start + pd.Timedelta(days=1)
    frames = []
    for sid in CORE_TAPE_SOURCES:
        p = canonical_root / f"{sid}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if "delivery_start_utc" not in df.columns:
            continue
        dt = pd.to_datetime(df["delivery_start_utc"], utc=True, errors="coerce")
        x = df.loc[(dt >= start) & (dt < end)].copy()
        if x.empty:
            continue
        x["source_id"] = sid
        frames.append(x)
    if not frames:
        raise RuntimeError(f"No canonical data for {day}")
    tape = pd.concat(frames, ignore_index=True, sort=False).sort_values(["delivery_start_utc","source_id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"MARKET_TAPE_{day.isoformat()}.parquet"
    tape.to_parquet(out, index=False)
    return out
