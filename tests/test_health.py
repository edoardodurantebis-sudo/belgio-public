from pathlib import Path

import pandas as pd

from belgium_public.config import SourceSpec
from belgium_public.health import inspect_source


def _core():
    return SourceSpec(
        id="x", provider="Elia", name="x", family="x", endpoint="https://example.invalid",
        documentation="https://example.invalid", coverage="x", granularity="PT15M",
        timezone="Europe/Brussels", primary_key=["datetime"], publication_timing="x",
        pit_status="outcome_only", required_fields=["datetime"], tier="core"
    )


def _write(path: Path, timestamps: list[str]):
    dt = pd.to_datetime(timestamps, utc=True)
    pd.DataFrame({"datetime": dt, "delivery_start_utc": dt, "value": range(len(dt))}).to_parquet(path, index=False)


def test_missing_core_keeps_tier_and_fails(tmp_path: Path):
    result = inspect_source(_core(), tmp_path / "missing.parquet")
    assert result["tier"] == "core"
    assert result["status"] == "FAIL"


def test_regular_quarter_hour_series_passes_and_reports_coverage(tmp_path: Path):
    path = tmp_path / "x.parquet"
    stamps = pd.date_range("2026-01-01T00:00:00Z", periods=96, freq="15min").astype(str).tolist()
    _write(path, stamps)
    result = inspect_source(_core(), path)
    assert result["status"] == "PASS"
    assert result["coverage_ratio_in_span"] == 1.0
    assert result["missing_intervals_in_span"] == 0
    assert result["median_unique_timestep_minutes"] == 15.0
    assert result["max_gap_minutes"] == 15.0


def test_material_gap_fails_closed(tmp_path: Path):
    path = tmp_path / "x.parquet"
    before = pd.date_range("2026-01-01T00:00:00Z", periods=8, freq="15min")
    after = pd.date_range("2026-01-01T05:00:00Z", periods=8, freq="15min")
    _write(path, pd.Index(before.append(after)).astype(str).tolist())
    result = inspect_source(_core(), path)
    assert result["status"] == "FAIL"
    assert result["missing_intervals_in_span"] > 0
    assert result["max_gap_minutes"] > 60
    assert any("max_gap_exceeds_4x_grain" in reason for reason in result["reasons"])


def test_schema_drift_from_previous_run_fails_core(tmp_path: Path):
    path = tmp_path / "x.parquet"
    stamps = pd.date_range("2026-01-01T00:00:00Z", periods=8, freq="15min").astype(str).tolist()
    _write(path, stamps)
    result = inspect_source(_core(), path, previous={"schema_fingerprint": "not-the-current-schema"})
    assert result["status"] == "FAIL"
    assert result["schema_drift_from_previous_run"] is True
    assert "schema_drift" in result["reasons"]
