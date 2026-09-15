from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

BRUSSELS = ZoneInfo("Europe/Brussels")
CORE_TAPE_SOURCES = ["ods001", "ods031", "ods032", "ods016", "ods026", "ods127", "ods132", "ods134", "ods166"]


def belgian_delivery_day_utc(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Exact UTC window of one Belgian local delivery day, including DST days."""
    start_local = datetime.combine(day, time.min, tzinfo=BRUSSELS)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=BRUSSELS)
    return pd.Timestamp(start_local.astimezone(timezone.utc)), pd.Timestamp(end_local.astimezone(timezone.utc))


def build_tape(day: date, canonical_root: Path, out_dir: Path) -> Path:
    start, end = belgian_delivery_day_utc(day)
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
        x["delivery_start_brussels"] = pd.to_datetime(x["delivery_start_utc"], utc=True).dt.tz_convert("Europe/Brussels")
        frames.append(x)
    if not frames:
        raise RuntimeError(f"No canonical data for Belgian delivery day {day}")
    tape = pd.concat(frames, ignore_index=True, sort=False).sort_values(["delivery_start_utc", "source_id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"MARKET_TAPE_{day.isoformat()}.parquet"
    tape.to_parquet(out, index=False)
    return out
