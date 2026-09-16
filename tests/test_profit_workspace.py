from pathlib import Path

import numpy as np
import pandas as pd

from belgium_public.profit_workspace import (
    build_profit_base,
    feature_registry,
    parse_energy_charts_payload,
    write_profit_panels,
)


def test_parse_energy_charts_parallel_arrays():
    payload = {"unix_seconds": [1760000000, 1760000900], "price": [80.0, 81.5]}
    x = parse_energy_charts_payload(payload)
    assert len(x) == 2
    assert list(x["entry_price"]) == [80.0, 81.5]
    assert (x["delivery_start_utc"].diff().dropna().dt.total_seconds() == 900).all()


def test_feature_registry_separates_strict_and_unsafe():
    r = feature_registry()
    assert (r["pit_status"] == "CERTIFIED").any()
    assert (r["pit_status"] == "UNSAFE_AFTER_DA_GATE").any()
    assert "wind_da11h_mw" in set(r["column_name"])


def test_build_profit_base_and_both_direction_panels(tmp_path: Path):
    canonical = tmp_path / "data" / "canonical"
    canonical.mkdir(parents=True)
    ts = pd.date_range("2026-01-01", periods=96 * 6, freq="15min", tz="UTC")

    pd.DataFrame({
        "delivery_start_utc": ts,
        "systemimbalance": np.sin(np.arange(len(ts)) / 10) * 200,
        "imbalanceprice": 100 + np.cos(np.arange(len(ts)) / 8) * 40,
    }).to_parquet(canonical / "ods134.parquet", index=False)

    wind_rows = []
    for t in ts:
        wind_rows.extend([
            {"delivery_start_utc": t, "measured": 100.0, "dayahead11hforecast": 90.0, "offshoreonshore": "Offshore", "region": "Federal", "gridconnectiontype": "Elia"},
            {"delivery_start_utc": t, "measured": 50.0, "dayahead11hforecast": 45.0, "offshoreonshore": "Onshore", "region": "Flanders", "gridconnectiontype": "Dso"},
        ])
    pd.DataFrame(wind_rows).to_parquet(canonical / "ods031.parquet", index=False)

    pd.DataFrame({
        "delivery_start_utc": list(ts) + list(ts),
        "region": ["Belgium"] * len(ts) + ["Flanders"] * len(ts),
        "measured": [300.0] * len(ts) + [200.0] * len(ts),
        "dayahead11hforecast": [280.0] * len(ts) + [190.0] * len(ts),
    }).to_parquet(canonical / "ods032.parquet", index=False)

    pd.DataFrame({
        "delivery_start_utc": ts,
        "totalload": 10000.0,
        "dayaheadforecast": 10100.0,
    }).to_parquet(canonical / "ods001.parquet", index=False)

    prices = pd.DataFrame({"delivery_start_utc": ts, "entry_price": 95.0})
    base, meta = build_profit_base(canonical, prices)
    assert meta["rows"] == len(ts)
    assert np.isclose(base["wind_actual_mw"].dropna().iloc[0], 150.0)
    assert np.isclose(base["solar_actual_mw"].dropna().iloc[0], 300.0)
    assert (base["safe_history_cutoff_utc"] <= base["da_gate_utc"]).all()

    paths = write_profit_panels(base, tmp_path / "research")
    short = pd.read_parquet(paths["DA_SHORT"])
    long = pd.read_parquet(paths["DA_LONG"])
    assert np.allclose(short["gross_pnl_1mw_eur"], (short["imbalance_price"] - short["entry_price"]) * 0.25)
    assert np.allclose(long["gross_pnl_1mw_eur"], (long["entry_price"] - long["imbalance_price"]) * 0.25)
