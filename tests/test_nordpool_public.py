from datetime import date

import pandas as pd

import belgium_public.nordpool_public as np_public
from belgium_public.nordpool_public import (
    _validate_day,
    backfill_nordpool_public,
    missing_local_days,
    parse_nordpool_public_payload,
)


def _payload(start_utc: str, periods: int, area: str = "BE") -> dict:
    ts = pd.date_range(start_utc, periods=periods, freq="15min", tz="UTC")
    entries = []
    for i, t in enumerate(ts):
        entries.append(
            {
                "deliveryStart": t.isoformat().replace("+00:00", "Z"),
                "deliveryEnd": (t + pd.Timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
                "entryPerArea": {area: 80.0 + i / 10.0},
                "status": "Final",
            }
        )
    return {"market": "DayAhead", "multiAreaEntries": entries}


def test_parse_nordpool_public_standard_pt15_belgium_day():
    # 2025-10-01 local Brussels delivery day starts at 2025-09-30 22:00Z.
    x = parse_nordpool_public_payload(_payload("2025-09-30T22:00:00Z", 96))
    assert len(x) == 96
    assert x["entry_price"].notna().all()
    assert (x["delivery_start_utc"].diff().dropna().dt.total_seconds() == 900).all()
    meta = _validate_day(date(2025, 10, 1), x)
    assert meta["rows"] == 96
    assert meta["common_step_seconds"] == 900.0


def test_validate_nordpool_public_dst_spring_and_autumn_days():
    # Brussels DST spring day has 23h = 92 quarters.
    spring = parse_nordpool_public_payload(_payload("2026-03-28T23:00:00Z", 92))
    assert _validate_day(date(2026, 3, 29), spring)["rows"] == 92

    # Brussels DST autumn day has 25h = 100 quarters.
    autumn = parse_nordpool_public_payload(_payload("2025-10-25T22:00:00Z", 100))
    assert _validate_day(date(2025, 10, 26), autumn)["rows"] == 100


def test_missing_local_days_uses_brussels_delivery_date():
    x = parse_nordpool_public_payload(_payload("2025-09-30T22:00:00Z", 96))
    missing = missing_local_days(x, date(2025, 10, 1), date(2025, 10, 3))
    assert missing == [date(2025, 10, 2), date(2025, 10, 3)]


def test_backfill_parallel_output_is_deterministic(monkeypatch, tmp_path):
    calls = []

    def fake_fetch(day, raw_root, **kwargs):
        calls.append(day)
        local_start = pd.Timestamp(day).tz_localize("Europe/Brussels").tz_convert("UTC")
        x = pd.DataFrame(
            {
                "delivery_start_utc": [local_start],
                "delivery_end_utc": [local_start + pd.Timedelta(minutes=15)],
                "entry_price": [float(day.day)],
                "status": ["Final"],
            }
        )
        return x, {"date": day.isoformat(), "rows": 1, "status": "PASS"}

    monkeypatch.setattr(np_public, "fetch_nordpool_public_day", fake_fetch)
    fresh, meta = backfill_nordpool_public(
        pd.DataFrame(),
        date(2026, 1, 1),
        date(2026, 1, 3),
        tmp_path,
        max_days=3,
        max_workers=3,
    )
    assert set(calls) == {date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)}
    assert list(pd.to_datetime(fresh["delivery_start_utc"], utc=True)) == sorted(
        pd.to_datetime(fresh["delivery_start_utc"], utc=True)
    )
    assert meta["attempted_days"] == 3
    assert meta["successful_days"] == 3
    assert meta["workers_used"] == 3


def test_custom_session_forces_serial_backfill(monkeypatch, tmp_path):
    class DummySession:
        pass

    def fake_fetch(day, raw_root, **kwargs):
        local_start = pd.Timestamp(day).tz_localize("Europe/Brussels").tz_convert("UTC")
        return (
            pd.DataFrame({"delivery_start_utc": [local_start], "entry_price": [1.0]}),
            {"date": day.isoformat(), "rows": 1, "status": "PASS"},
        )

    monkeypatch.setattr(np_public, "fetch_nordpool_public_day", fake_fetch)
    _, meta = backfill_nordpool_public(
        pd.DataFrame(),
        date(2026, 1, 1),
        date(2026, 1, 2),
        tmp_path,
        max_days=2,
        max_workers=8,
        session=DummySession(),
    )
    assert meta["workers_used"] == 1
