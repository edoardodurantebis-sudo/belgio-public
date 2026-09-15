from __future__ import annotations
import gzip, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def save_raw_vintage(base: Path, provider: str, source_id: str, payload: bytes, *, url: str, retrieved_at: datetime, headers: Mapping[str, str] | None = None, suffix: str = "csv") -> tuple[Path, Path]:
    folder = base / provider.lower() / source_id
    folder.mkdir(parents=True, exist_ok=True)
    stem = stamp(retrieved_at)
    raw_path = folder / f"{stem}.{suffix}.gz"
    meta_path = folder / f"{stem}.meta.json"
    with gzip.open(raw_path, "wb") as fh:
        fh.write(payload)
    meta = {
        "provider": provider,
        "source_id": source_id,
        "request_url": url,
        "retrieved_at_utc": retrieved_at.isoformat(),
        "sha256": sha256_bytes(payload),
        "bytes": len(payload),
        "response_headers": dict(headers or {}),
        "raw_file": str(raw_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return raw_path, meta_path
