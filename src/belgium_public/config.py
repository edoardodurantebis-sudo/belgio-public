from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class SourceSpec:
    id: str
    provider: str
    name: str
    family: str
    endpoint: str
    documentation: str
    coverage: str
    granularity: str
    timezone: str
    primary_key: list[str]
    publication_timing: str
    pit_status: str
    required_fields: list[str] = field(default_factory=list)
    tier: str = "extended"
    mode: str = "historical"
    validated: bool = False
    preserve_vintages: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceSpec":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in allowed})


def load_registry(path: Path | None = None) -> tuple[list[SourceSpec], dict[str, Any]]:
    path = path or ROOT / "config" / "sources.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = [SourceSpec.from_dict(x) for x in payload["sources"]]
    return sources, payload
