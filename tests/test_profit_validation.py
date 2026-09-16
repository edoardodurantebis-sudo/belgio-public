import json

import pandas as pd

import belgium_public.profit_validation as pv


def _panel(tmp_path, final_negative=False):
    ts = pd.date_range("2025-10-01", "2026-08-31 23:45", freq="15min", tz="UTC")
    pnl = pd.Series(1.0, index=range(len(ts)))
    if final_negative:
        pnl.loc[ts >= pd.Timestamp("2026-07-01", tz="UTC")] = -1.0
    df = pd.DataFrame(
        {
            "delivery_start_utc": ts,
            "entry_price": 100.0,
            "imbalance_price": 104.0,
            "system_view": "SHORT_SYSTEM",
            "gross_pnl_1mw_eur": pnl.to_numpy(),
            "signal": 1.0,
        }
    )
    panel = tmp_path / "panel.parquet"
    df.to_parquet(panel, index=False)
    registry = tmp_path / "registry.csv"
    pd.DataFrame(
        [{"feature_id": "S", "column_name": "signal", "family": "S", "pit_status": "CERTIFIED"}]
    ).to_csv(registry, index=False)
    return panel, registry


def _fake_discover(train, reg):
    definition = json.dumps(
        [{"feature": "signal", "op": ">=", "threshold": 0.5, "label": "ON", "family": "S"}],
        sort_keys=True,
    )
    return [{"definition_json": definition, "n_factors": 1, "train_score": 1.0}]


def test_two_stage_requires_validation_and_final_pass(monkeypatch, tmp_path):
    panel, registry = _panel(tmp_path, final_negative=False)
    monkeypatch.setattr(pv, "discover", _fake_discover)
    out = pv.run_two_stage_profit_lab(
        panel,
        registry,
        tmp_path / "out.json",
        "2026-04-01",
        "2026-07-01",
    )
    assert out["train_rows"] > 0
    assert out["validation_rows"] > 0
    assert out["final_rows"] > 0
    assert out["validation_gate_pass_count"] == 1
    assert out["review_ready_count"] == 1
    assert out["candidates"][0]["machine_status"] == "REVIEW_READY"
    assert out["candidates"][0]["val_fdr_pass"] is True
    assert out["candidates"][0]["final_fdr_pass"] is True


def test_final_holdout_can_kill_validation_winner(monkeypatch, tmp_path):
    panel, registry = _panel(tmp_path, final_negative=True)
    monkeypatch.setattr(pv, "discover", _fake_discover)
    out = pv.run_two_stage_profit_lab(
        panel,
        registry,
        tmp_path / "out.json",
        "2026-04-01",
        "2026-07-01",
    )
    row = out["candidates"][0]
    assert row["validation_gate_pass"] is True
    assert row["final_gate_pass"] is False
    assert row["machine_status"] == "FINAL_HOLDOUT_FAIL"
    assert out["review_ready_count"] == 0


def test_two_stage_fails_closed_when_final_slice_missing(tmp_path):
    ts = pd.date_range("2025-10-01", "2026-06-30 23:45", freq="15min", tz="UTC")
    panel = tmp_path / "panel.parquet"
    pd.DataFrame(
        {
            "delivery_start_utc": ts,
            "entry_price": 100.0,
            "imbalance_price": 101.0,
            "system_view": "SHORT_SYSTEM",
            "gross_pnl_1mw_eur": 0.25,
            "signal": 1.0,
        }
    ).to_parquet(panel, index=False)
    registry = tmp_path / "registry.csv"
    pd.DataFrame(
        [{"feature_id": "S", "column_name": "signal", "family": "S", "pit_status": "CERTIFIED"}]
    ).to_csv(registry, index=False)
    out = pv.run_two_stage_profit_lab(panel, registry, tmp_path / "out.json", "2026-04-01", "2026-07-01")
    assert out["status"] == "BLOCKED_INSUFFICIENT_TEMPORAL_SPLITS"
    assert out["final_rows"] == 0
