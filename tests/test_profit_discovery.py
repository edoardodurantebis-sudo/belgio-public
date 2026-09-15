import pandas as pd

from belgium_public.profit_discovery import pnl_from_prices, summarize_pnl, run_profit_lab


def test_pt15_pnl_matches_uk_economic_convention_scaled_to_quarter_hour():
    entry = pd.Series([50.0, 50.0])
    imbalance = pd.Series([90.0, 10.0])
    short_system = pnl_from_prices(entry, imbalance, "SHORT_SYSTEM")
    long_system = pnl_from_prices(entry, imbalance, "LONG_SYSTEM")
    assert short_system.tolist() == [10.0, -10.0]
    assert long_system.tolist() == [-10.0, 10.0]


def test_summary_penalises_small_sample():
    rows = pd.DataFrame({
        "delivery_start_utc": pd.date_range("2026-01-01", periods=8, freq="15min", tz="UTC"),
        "gross_pnl_1mw_eur": [1.0] * 8,
    })
    out = summarize_pnl(rows)
    assert out["verdict"] == "INSUFFICIENT_ECON_SAMPLE"


def test_profit_lab_fails_closed_without_certified_panel(tmp_path):
    out = run_profit_lab(
        tmp_path / "missing.parquet",
        tmp_path / "registry.csv",
        tmp_path / "PROFIT_LAB_STATUS.json",
        "2026-01-01",
    )
    assert out["status"] == "BLOCKED_MISSING_PROFIT_PANEL"
    assert out["candidates"] == []
