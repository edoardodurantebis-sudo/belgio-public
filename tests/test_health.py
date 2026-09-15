from pathlib import Path
from belgium_public.config import SourceSpec
from belgium_public.health import inspect_source


def _core():
    return SourceSpec(
        id="x", provider="Elia", name="x", family="x", endpoint="https://example.invalid",
        documentation="https://example.invalid", coverage="x", granularity="PT15M",
        timezone="Europe/Brussels", primary_key=["datetime"], publication_timing="x",
        pit_status="outcome_only", required_fields=["datetime"], tier="core"
    )


def test_missing_core_keeps_tier_and_fails(tmp_path: Path):
    result = inspect_source(_core(), tmp_path / "missing.parquet")
    assert result["tier"] == "core"
    assert result["status"] == "FAIL"
