from datetime import date

import pandas as pd

from belgium_public.nordpool_public import (
    _validate_day,
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
