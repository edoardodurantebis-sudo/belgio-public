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


def _merge_default_registry(base: dict[str, Any], supplement: dict[str, Any]) -> dict[str, Any]:
    remove_ids = set(supplement.get("remove_ids", []))
    by_id = {x["id"]: x for x in base.get("sources", []) if x.get("id") not in remove_ids}
    for item in supplement.get("sources", []):
        by_id[item["id"]] = item

    breaks: dict[tuple[str, str], dict[str, Any]] = {}
    for item in [*base.get("structural_breaks", []), *supplement.get("structural_breaks", [])]:
        breaks[(str(item.get("date")), str(item.get("name")))] = item

    merged = dict(base)
    merged["sources"] = list(by_id.values())
    merged["structural_breaks"] = list(breaks.values())
    merged["registry_files"] = ["config/sources.json", "config/sources_verified_additions.json"]
    return merged


def load_registry(path: Path | None = None) -> tuple[list[SourceSpec], dict[str, Any]]:
    """Load the central logical source registry.

    The default registry is the deterministic merge of the original registry
    and live/documentation-verified additions. A caller-supplied path remains a
    standalone registry, which keeps tests and external reuse predictable.
    """
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        base_path = ROOT / "config" / "sources.json"
        payload = json.loads(base_path.read_text(encoding="utf-8"))
        supplement_path = ROOT / "config" / "sources_verified_additions.json"
        if supplement_path.exists():
            supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
            payload = _merge_default_registry(payload, supplement)
    sources = [SourceSpec.from_dict(x) for x in payload["sources"]]
    return sources, payload
